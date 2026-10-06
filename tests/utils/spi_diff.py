"""A lockstep differential oracle for the SPI (PL022): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the SPI design note). The method is the PIO's, the DMA's, the SSI's and the UART's (tests/utils/*_diff.py): one chip whose SPI0 is the
*pure-Python* reference (``peripherals/_spi.py``, built explicitly because the facade would hand out the native one) and one whose SPI0 is whatever the facade gives (today the
same class, later the C++ one), fed one stream of operations:

* writes and reads of **every register offset**, through the bus and through its four aliases (normal, XOR, SET, CLR - an alias write decodes against a *read*, and a read of
  SSPDR pulls a byte from the RX FIFO), unimplemented offsets included (so the warnings are compared);
* the device on the bus, in the three shapes a real one has: the **default** (the reference's own: every byte comes straight back as 0), an **immediate** device whose
  ``on_transmit`` calls ``complete_transmit()`` from inside the callback (re-entrancy: the block is in the middle of ``_do_tx`` when it is entered again), a **deferred**
  one that completes on a later operation (the ST7735S and the e-paper do, from a clock alarm), and a **silent** one that never does (the block stays busy);
* ``complete_transmit()`` called when nothing was sent, FIFOs driven past full (a DR write on a full TX FIFO is dropped, a completion into a full RX FIFO is an overrun),
  ``check_interrupts()``, ``reset()``, and ``clk_peri`` changed under ``clock_frequency``.

After **each** step: the register file read through the bus (SSPDR excluded - it has a side effect - and read as an operation of its own), the private state (both control
registers, DMA control, the prescale divisor, raw and enabled interrupts, the busy flag, ``raw_write_value``), both FIFOs' level and contents, the derived properties
(``enabled``, ``data_bits``, ``master_mode``, ``spi_mode``, ``clock_frequency``, ``int_status``), the NVIC's pending bits, and the ordered log of everything that left the block (IRQ
line changes, DREQ set/clear, transmitted bytes, warnings); an exception on one side and not the other is a difference like any other.

The rig *replaces* the chip's DMA with a recorder and wraps ``set_interrupt``.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.peripherals import _spi as P
from rp2040py.peripherals.dma import DREQChannel
from rp2040py.rp2040 import RP2040

SPI0_BASE = 0x4003C000
NVIC_ISPR = 0xE000E200
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTERS = (
    P.SSPCR0, P.SSPCR1, P.SSPSR, P.SSPCPSR, P.SSPIMSC, P.SSPRIS, P.SSPMIS, P.SSPDMACR, P.SSPPERIPHID0, P.SSPPERIPHID1, P.SSPPERIPHID2, P.SSPPERIPHID3, P.SSPPCELLID0,
    P.SSPPCELLID1, P.SSPPCELLID2, P.SSPPCELLID3,
)  # fmt: skip
WRITABLE = (P.SSPCR0, P.SSPCR1, P.SSPDR, P.SSPCPSR, P.SSPIMSC, P.SSPDMACR, P.SSPICR)
UNIMPLEMENTED = (
    0x28,
    0x2C,
    0x40,
    0x80,
    0x100,
    0xF00,
    0xFDC,
    P.SSPSR,
    P.SSPRIS,
    P.SSPMIS,
)  # the last three are read-only: a write is unimplemented
CLOCKS = (125_000_000, 48_000_000, 12_000_000, 1, 0)
INTERRUPT_BITS = (0x1, 0x2, 0x4, 0x8, 0xF, 0x3)


class Rig:
    """One chip plus the log of everything that left its SPI0."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        self.pending: list[int] = []  # bytes a deferred device has been handed and not yet completed
        self.next_rx = 0
        if kind == "pure":
            self._replace_the_spi(P.RPSPI)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_spi(factory)
        self._tap()

    def _replace_the_spi(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(
            chip, "SPI0", IRQ.SPI0, P.ISPIDMAChannels(rx=DREQChannel.DREQ_SPI0_RX, tx=DREQChannel.DREQ_SPI0_TX)
        )
        chip.spi[0] = new
        chip.peripherals[SPI0_BASE >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log

        class _Dma:
            def set_dreq(self, channel: int) -> None:
                log.append(("dreq", int(channel), 1))

            def clear_dreq(self, channel: int) -> None:
                log.append(("dreq", int(channel), 0))

        chip.dma = _Dma()  # type: ignore[assignment]  # the SPI looks `rp2040.dma` up whenever a FIFO level changes
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger, method, lambda name, message, _m=method: log.append(("log", _m, str(name), str(message)))
            )

    def attach_device(self, mode: str) -> None:
        spi, log = self.chip.spi[0], self.log

        if mode == "immediate":

            def on_transmit(value: int) -> None:
                log.append(("tx", int(value)))
                self.next_rx = (self.next_rx + 37) & 0xFFFF
                spi.complete_transmit(self.next_rx)

        elif mode == "deferred":

            def on_transmit(value: int) -> None:
                log.append(("tx", int(value)))
                self.pending.append(int(value))

        else:  # silent

            def on_transmit(value: int) -> None:
                log.append(("tx", int(value)))

        spi.on_transmit = on_transmit

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        spi = chip.spi[0]
        if kind == "write":
            chip.write_uint32(SPI0_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(SPI0_BASE + op[2] + op[1]))
        elif kind == "device":
            self.attach_device(op[1])
        elif kind == "complete":  # a deferred device finishing (or a spurious completion when nothing is pending)
            if self.pending:
                self.pending.pop(0)
            spi.complete_transmit(op[1])
        elif kind == "check":
            spi.check_interrupts()
        elif kind == "reset":
            spi.reset()
        elif kind == "clk":
            chip.clk_peri = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, spi = self.chip, self.chip.spi[0]
        rx, tx = spi.rx_fifo, spi.tx_fifo
        return {
            "regs": tuple(int(chip.read_uint32(SPI0_BASE + offset)) for offset in REGISTERS),
            "state": (
                int(spi._control0),
                int(spi._control1),
                int(spi._dma_control),
                int(spi._clock_divisor),
                int(spi._int_raw),
                int(spi._int_enable),
                bool(spi._busy),
                int(spi.raw_write_value),
            ),
            "rx": (bool(rx.empty), bool(rx.full), int(rx.item_count), tuple(int(v) for v in rx.items)),
            "tx": (bool(tx.empty), bool(tx.full), int(tx.item_count), tuple(int(v) for v in tx.items)),
            "props": (
                bool(spi.enabled),
                int(spi.data_bits),
                bool(spi.master_mode),
                int(spi.spi_mode),
                float(spi.clock_frequency),
                int(spi.int_status),
            ),
            "nvic": int(chip.read_uint32(NVIC_ISPR)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose SPI is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""

    class Mutant(P.RPSPI):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if name == "fifo_depth_16":
                self.rx_fifo = P.FIFO(16)
                self.tx_fifo = P.FIFO(16)
            if name == "init_raw_zero":
                self._int_raw = 0

        @property
        def spi_mode(self) -> int:
            if name == "spi_mode_swapped":
                cpol, cpha = self._control0 & P.SPO, self._control0 & P.SPH
                return (2 if cpol else 0) + (1 if cpha else 0)
            return super().spi_mode

        @property
        def master_mode(self) -> bool:
            if name == "master_mode_inverted":
                return bool(self._control0 & P.MS)
            return super().master_mode

        @property
        def clock_frequency(self) -> float:
            if name == "clock_freq_no_scr":
                return self.rp2040.clk_peri / self._clock_divisor if self._clock_divisor else 0
            return super().clock_frequency

        def _update_dma_tx(self) -> None:
            if name == "dma_tx_inverted":
                (self.rp2040.dma.set_dreq if self.tx_fifo.full else self.rp2040.dma.clear_dreq)(self.dreq.tx)
                return
            if name in ("dma_tx_ignores_sse", "dma_tx_ignores_txdmae"):
                gate = (self._control1 & P.SSE if name == "dma_tx_ignores_txdmae" else True) and (
                    self._dma_control & P.TXDMAE if name == "dma_tx_ignores_sse" else True
                )
                (self.rp2040.dma.clear_dreq if self.tx_fifo.full or not gate else self.rp2040.dma.set_dreq)(
                    self.dreq.tx
                )
                return
            super()._update_dma_tx()

        def _update_dma_rx(self) -> None:
            if name == "dma_rx_inverted":
                (self.rp2040.dma.set_dreq if self.rx_fifo.empty else self.rp2040.dma.clear_dreq)(self.dreq.rx)
                return
            if name in ("dma_rx_ignores_sse", "dma_rx_ignores_rxdmae"):
                gate = (self._control1 & P.SSE if name == "dma_rx_ignores_rxdmae" else True) and (
                    self._dma_control & P.RXDMAE if name == "dma_rx_ignores_sse" else True
                )
                (self.rp2040.dma.clear_dreq if self.rx_fifo.empty or not gate else self.rp2040.dma.set_dreq)(
                    self.dreq.rx
                )
                return
            super()._update_dma_rx()

        def _do_tx(self) -> None:
            if name == "busy_not_set":
                if not self._busy and not self.tx_fifo.empty:
                    value = self.tx_fifo.pull()
                    self.on_transmit(value)
                    self._fifos_updated()
                return
            if name == "do_tx_no_fifos_updated":
                if not self._busy and not self.tx_fifo.empty:
                    value = self.tx_fifo.pull()
                    self._busy = True
                    self.on_transmit(value)
                return
            if name == "tx_ignores_sse":
                if not self._busy and not self.tx_fifo.empty:
                    value = self.tx_fifo.pull()
                    self._busy = True
                    if self._control1 & P.LBM:
                        self.complete_transmit(value)
                    else:
                        self.on_transmit(value)
                    self._fifos_updated()
                return
            if name == "loopback_ignored":
                if not self._busy and not self.tx_fifo.empty and self._control1 & P.SSE:
                    value = self.tx_fifo.pull()
                    self._busy = True
                    self.on_transmit(value)
                    self._fifos_updated()
                return
            super()._do_tx()

        def complete_transmit(self, rx_value: int) -> None:
            if name == "overrun_not_flagged":
                self._busy = False
                if not self.rx_fifo.full:
                    self.rx_fifo.push(rx_value)
                self._fifos_updated()
                self._do_tx()
                return
            if name == "complete_no_do_tx":
                self._busy = False
                if not self.rx_fifo.full:
                    self.rx_fifo.push(rx_value)
                else:
                    self._int_raw |= P.SSPRORINTR
                self._fifos_updated()
                return
            super().complete_transmit(rx_value)

        def _fifos_updated(self) -> None:
            if name in ("tx_threshold_lt", "rx_threshold_gt"):
                prev_status = self.int_status
                tx_low = (
                    self.tx_fifo.item_count < self.tx_fifo.size / 2
                    if name == "tx_threshold_lt"
                    else self.tx_fifo.item_count <= self.tx_fifo.size / 2
                )
                rx_high = (
                    self.rx_fifo.item_count > self.rx_fifo.size / 2
                    if name == "rx_threshold_gt"
                    else self.rx_fifo.item_count >= self.rx_fifo.size / 2
                )
                self._int_raw = (self._int_raw | P.SSPTXINTR) if tx_low else (self._int_raw & ~P.SSPTXINTR)
                self._int_raw = (self._int_raw | P.SSPRXINTR) if rx_high else (self._int_raw & ~P.SSPRXINTR)
                if self.int_status != prev_status:
                    self.check_interrupts()
                self._update_dma_tx()
                self._update_dma_rx()
                return
            super()._fifos_updated()

        def read_uint32(self, offset: int) -> int:
            if offset == P.SSPDR and name == "dr_read_empty_ff" and self.rx_fifo.empty:
                return 0xFF
            if offset == P.SSPSR and name == "sr_bsy_wrong":
                return super().read_uint32(offset) & ~P.BSY
            if offset == P.SSPMIS and name == "mis_unmasked":
                return self._int_raw
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if offset == P.SSPDR and name == "dss_mask_ignored":
                if not self.tx_fifo.full:
                    self.tx_fifo.push(value)
                    self._do_tx()
                    self._fifos_updated()
                return
            if offset == P.SSPDR and name == "dr_write_when_full_pushes":
                self.tx_fifo.push(value & ((1 << self.data_bits) - 1))
                self._do_tx()
                self._fifos_updated()
                return
            if offset == P.SSPCPSR and name == "cpsr_mask_ff":
                self._clock_divisor = value & 0xFF
                return
            if offset == P.SSPICR and name == "icr_decoded_value":
                self._int_raw &= ~(value & (P.SSPRTINTR | P.SSPRORINTR))
                self.check_interrupts()
                return
            if offset == P.SSPICR and name == "icr_clears_all":
                self._int_raw = 0
                self.check_interrupts()
                return
            if offset == P.SSPICR and name == "icr_clears_tx":
                self._int_raw &= ~(value & (P.SSPRTINTR | P.SSPRORINTR | P.SSPTXINTR))
                self.check_interrupts()
                return
            if offset == P.SSPIMSC and name == "imsc_unmasked":
                self._int_enable = value
                self.check_interrupts()
                return
            if offset == P.SSPCR0 and name == "cr0_unmasked":
                self._control0 = value
                return
            if offset == P.SSPCR1 and name in ("cr1_unmasked", "cr1_write_no_start"):
                self._control1 = value if name == "cr1_unmasked" else value & P.CR1_MASK
                if name == "cr1_unmasked":
                    self._do_tx()
                    self._fifos_updated()
                return
            if offset == P.SSPDMACR and name in ("dmacr_unmasked", "dmacr_write_no_publish"):
                self._dma_control = value if name == "dmacr_unmasked" else value & P.DMACR_MASK
                if name == "dmacr_unmasked":
                    self._update_dma_tx()
                    self._update_dma_rx()
                return
            super().write_uint32(offset, value)

        def reset(self) -> None:
            on_transmit = self.on_transmit
            if name == "reset_no_dma_publish":
                self.rx_fifo.reset()
                self.tx_fifo.reset()
                self._busy = False
                self._control0 = self._control1 = self._dma_control = self._clock_divisor = self._int_raw = (
                    self._int_enable
                ) = 0
                self.rp2040.set_interrupt(self.irq, False)
                return
            super().reset()
            if name == "reset_raw_zero":
                self._int_raw = 0
            if name == "reset_clears_callback":
                self.on_transmit = lambda value: self.complete_transmit(0)
            else:
                self.on_transmit = on_transmit

    return Rig("mutant", Mutant)


MUTANTS = (
    "fifo_depth_16", "spi_mode_swapped", "master_mode_inverted", "clock_freq_no_scr", "dma_tx_inverted", "dma_rx_inverted", "busy_not_set", "do_tx_no_fifos_updated",
    "overrun_not_flagged", "complete_no_do_tx", "tx_threshold_lt", "rx_threshold_gt", "dr_read_empty_ff", "sr_bsy_wrong", "mis_unmasked", "dss_mask_ignored",
    "dr_write_when_full_pushes", "cpsr_mask_ff", "icr_decoded_value", "icr_clears_all", "icr_clears_tx", "imsc_unmasked", "reset_no_dma_publish", "reset_clears_callback",
    "init_raw_zero", "reset_raw_zero", "cr0_unmasked", "cr1_unmasked", "dmacr_unmasked", "cr1_write_no_start", "dmacr_write_no_publish", "tx_ignores_sse", "loopback_ignored",
    "dma_tx_ignores_sse", "dma_tx_ignores_txdmae", "dma_rx_ignores_sse", "dma_rx_ignores_rxdmae",
)  # fmt: skip


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == P.SSPCR0:
        return r.choice((0x7, 0xF, 0x0, 0x4C7, 0x1F7, 0xFF07, r.getrandbits(16), r.getrandbits(32)))
    if offset == P.SSPCR1:
        return r.choice((0, P.SSE, P.SSE, P.SSE | P.LBM, P.MS | P.SSE, r.getrandbits(4), r.getrandbits(32)))
    if offset == P.SSPDR:
        return r.getrandbits(r.choice((4, 8, 8, 16))) if roll < 0.9 else r.getrandbits(32)
    if offset == P.SSPCPSR:
        return r.choice((0, 2, 4, 10, 254, 255, 0x100, r.getrandbits(32)))
    if offset == P.SSPDMACR:
        return r.choice((0, 1, 2, 3, 3, r.getrandbits(32)))
    if offset in (P.SSPIMSC, P.SSPICR):
        return r.choice(INTERRUPT_BITS) if roll < 0.7 else r.getrandbits(32)
    return r.getrandbits(32)


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.12 and ops:  # attach a device only after a while: the default behaviour gets a prefix of its own
            ops.append(("device", r.choice(("immediate", "deferred", "silent", "deferred"))))
        elif roll < 0.40:
            offset = r.choice(WRITABLE + (P.SSPDR, P.SSPDR, P.SSPDR))
            alias = ALIASES[0] if r.random() < 0.75 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.44:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.62:  # a burst of DR writes - overflows the 8-entry TX FIFO when the device is silent or slow
            ops.extend(("write", P.SSPDR, r.getrandbits(8), ALIASES[0]) for _ in range(r.choice((1, 2, 4, 9, 12))))
        elif roll < 0.76:
            ops.extend(("read", P.SSPDR, ALIASES[0]) for _ in range(r.choice((1, 1, 3, 9))))
        elif roll < 0.84:
            ops.append(
                ("read", r.choice(REGISTERS + UNIMPLEMENTED), r.choice(ALIASES) if r.random() < 0.15 else ALIASES[0])
            )
        elif roll < 0.93:
            ops.append(("complete", r.getrandbits(16)))
        elif roll < 0.95:
            ops.append(("reset",))
        elif roll < 0.97:
            ops.append(("clk", r.choice(CLOCKS)))
        else:
            ops.append(("check",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b[key]
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:60] for i in where[:6]]} vs {[str(value_b[i])[:60] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    spi = rig.chip.spi[0]
    for op in ops:
        before = len(rig.log)
        was_busy, tx_full, rx_full = spi._busy, spi.tx_fifo.full, spi.rx_fifo.full
        if op[0] == "write" and op[1] == P.SSPDR and op[3] == 0 and tx_full:
            count("tx.full_drop")
        if op[0] == "complete":
            count("complete.spurious" if not was_busy else "complete.busy")
            if rx_full:
                count("rx.overrun")
        if op[0] == "read" and op[1] == P.SSPDR and op[2] == 0:
            count("dr.read.empty" if spi.rx_fifo.empty else "dr.read.data")
        if op[0] == "device":
            count(f"device.{op[1]}")
        if op[0] == "write" and op[1] == P.SSPICR:
            count("icr")
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(f"log.{event[0]}" + (f".{event[2]}" if event[0] in ("dreq", "irq") else ""))
        if op[0] == "reset":
            count("reset.busy" if was_busy else "reset")
    return counts
