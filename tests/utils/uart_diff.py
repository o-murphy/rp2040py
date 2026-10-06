"""A lockstep differential oracle for the UART: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the UART design note). The method is the PIO's, the DMA's and the SSI's (tests/utils/pio_diff.py, dma_diff.py, ssi_diff.py): one
chip whose UART0 is the *pure-Python* reference (``peripherals/_uart.py``, built explicitly because the facade would hand out the native one) and one whose UART0 is whatever
the facade gives (today the same class, later the C++ one), fed one stream of operations:

* writes of **every register offset**, through the bus and through its four aliases (normal, XOR, SET, CLR - ``UARTICR`` clears what the bus *passed*, not the alias-decoded
  value), and reads of every offset including the unimplemented ones and the atomic-region addresses (so the warnings are compared);
* ``feed_byte()`` in bursts long enough to overflow the 32-entry RX FIFO, DR reads that drain it (and read it empty), ``check_interrupts()``, ``reset()``;
* the callbacks attached and detached (``on_byte``, ``on_baud_rate_change``), and ``clk_peri`` changed under the baud-rate computation - including a zero divider, whose
  ``ZeroDivisionError`` has to reach the writer on both sides.

After **each** step: the register file read through the bus, the private state (control, line control, both divisors, interrupt mask and status, ``raw_write_value``), the RX
FIFO's level and contents, the NVIC's pending bits, and the ordered log of everything that left the block (IRQ line changes, DREQ set/clear, bytes transmitted, baud rates
announced, warnings); an exception on one side and not the other is a difference like any other.

The rig *replaces* the chip's DMA with a recorder (the real one would act on the DREQs, which is a different block's behaviour) and wraps ``set_interrupt``.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.peripherals import _uart as U
from rp2040py.peripherals.dma import DREQChannel
from rp2040py.rp2040 import RP2040

UART0_BASE = 0x40034000
NVIC_ISPR = 0xE000E200
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTERS = (
    U.UARTRSR, U.UARTILPR, U.UARTIFLS, U.UARTDMACR, U.UARTFR, U.UARTIBRD, U.UARTFBRD, U.UARTLCR_H, U.UARTCR, U.UARTIMSC, U.UARTIRIS, U.UARTIMIS, U.UARTPERIPHID0, U.UARTPERIPHID1, U.UARTPERIPHID2, U.UARTPERIPHID3,
    U.UARTPCELLID0, U.UARTPCELLID1, U.UARTPCELLID2, U.UARTPCELLID3,
)  # fmt: skip
WRITABLE = (
    U.UARTDR,
    U.UARTRSR,
    U.UARTILPR,
    U.UARTIBRD,
    U.UARTFBRD,
    U.UARTLCR_H,
    U.UARTCR,
    U.UARTIFLS,
    U.UARTIMSC,
    U.UARTICR,
    U.UARTDMACR,
)
UNIMPLEMENTED = (0x08, 0x14, 0x1C, 0x4C, 0x80, 0x100, 0xF00, 0xFDC, 0xFFC + 4)
CLOCKS = (125_000_000, 48_000_000, 12_000_000, 133_000_000, 1, 0)
INTERRUPT_BITS = (0x10, 0x20, 0x30, 0x7FF, 0x400, 0x1)


class Rig:
    """One chip plus the log of everything that left its UART0."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        if kind == "pure":
            self._replace_the_uart(U.RPUART)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_uart(factory)
        self._tap()

    def _replace_the_uart(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(
            chip, "UART0", IRQ.UART0, U.IUARTDMAChannels(rx=DREQChannel.DREQ_UART0_RX, tx=DREQChannel.DREQ_UART0_TX)
        )
        chip.uart[0] = new
        chip.peripherals[UART0_BASE >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log

        class _Dma:
            def set_dreq(self, channel: int) -> None:
                log.append(("dreq", int(channel), 1))

            def clear_dreq(self, channel: int) -> None:
                log.append(("dreq", int(channel), 0))

        chip.dma = _Dma()  # type: ignore[assignment]  # the UART looks `rp2040.dma` up on every CR write
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger, method, lambda name, message, _m=method: log.append(("log", _m, str(name), str(message)))
            )
        self.attach_callbacks(True, True)

    def attach_callbacks(self, on_byte: bool, on_baud: bool) -> None:
        uart, log = self.chip.uart[0], self.log
        uart.on_byte = (lambda value: log.append(("byte", int(value)))) if on_byte else None
        uart.on_baud_rate_change = (lambda rate: log.append(("baud", int(rate)))) if on_baud else None

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        uart = chip.uart[0]
        if kind == "write":
            chip.write_uint32(UART0_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(UART0_BASE + op[2] + op[1]))
        elif kind == "feed":
            uart.feed_byte(op[1])
        elif kind == "check":
            uart.check_interrupts()
        elif kind == "reset":
            uart.reset()
        elif kind == "callbacks":
            self.attach_callbacks(op[1], op[2])
        elif kind == "clk":
            chip.clk_peri = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, uart = self.chip, self.chip.uart[0]
        fifo = uart.rx_fifo
        return {
            "regs": tuple(int(chip.read_uint32(UART0_BASE + offset)) for offset in REGISTERS),
            "state": (
                int(uart._ctrl_register),
                int(uart._line_ctrl_register),
                int(uart._int_divisor),
                int(uart._frac_divisor),
                int(uart._interrupt_mask),
                int(uart._interrupt_status),
                int(uart._ifls),
                int(uart._ilpr),
                int(uart._dmacr),
                int(uart._rsr),
                int(uart.raw_write_value),
            ),
            "rx": (bool(fifo.empty), bool(fifo.full), int(fifo.item_count), tuple(int(v) for v in fifo.items)),
            "nvic": int(chip.read_uint32(NVIC_ISPR)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose UART is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""

    class Mutant(U.RPUART):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if name == "fifo_16":
                self.rx_fifo = U.FIFO(16)

        @property
        def flags(self) -> int:
            if name == "flags_no_txfe":
                return super().flags & ~U.TXFE
            if name == "flags_rxff_wrong":
                return (super().flags & ~U.RXFF) | (U.RXFF if self.rx_fifo.item_count > 16 else 0)
            return super().flags

        def _update_dreq(self) -> None:
            if name == "dreq_tx_ignores_dmacr":
                tx = self.enabled and self.tx_enabled
                rx = self.enabled and self.rx_enabled and bool(self._dmacr & U.DMACR_RXDMAE) and not self.rx_fifo.empty
            elif name == "dreq_tx_ignores_txe":
                tx = self.enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = self.enabled and self.rx_enabled and bool(self._dmacr & U.DMACR_RXDMAE) and not self.rx_fifo.empty
            elif name == "dreq_rx_ignores_dmacr":
                tx = self.enabled and self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = self.enabled and self.rx_enabled and not self.rx_fifo.empty
            elif name == "dreq_rx_when_empty":
                tx = self.enabled and self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = self.enabled and self.rx_enabled and bool(self._dmacr & U.DMACR_RXDMAE)
            elif name == "dreq_rx_ignores_rxe":
                tx = self.enabled and self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = self.enabled and bool(self._dmacr & U.DMACR_RXDMAE) and not self.rx_fifo.empty
            elif name == "dreq_ignores_dmaonerr":
                tx = self.enabled and self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = self.enabled and self.rx_enabled and bool(self._dmacr & U.DMACR_RXDMAE) and not self.rx_fifo.empty
            elif name == "dreq_swapped_channels":
                tx = self.enabled and self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = (
                    self.enabled
                    and self.rx_enabled
                    and bool(self._dmacr & U.DMACR_RXDMAE)
                    and not self.rx_fifo.empty
                    and not (self._dmacr & U.DMACR_DMAONERR and self._interrupt_status & U.UART_ERROR_INTERRUPTS)
                )
            elif name == "dreq_ignores_uarten":
                tx = self.tx_enabled and bool(self._dmacr & U.DMACR_TXDMAE)
                rx = (
                    self.rx_enabled
                    and bool(self._dmacr & U.DMACR_RXDMAE)
                    and not self.rx_fifo.empty
                    and not (self._dmacr & U.DMACR_DMAONERR and self._interrupt_status & U.UART_ERROR_INTERRUPTS)
                )
            else:
                super()._update_dreq()
                return
            dma = self.rp2040.dma
            dreq = self.dreq
            if name == "dreq_swapped_channels":
                dreq = U.IUARTDMAChannels(rx=dreq.tx, tx=dreq.rx)
            (dma.set_dreq if tx else dma.clear_dreq)(dreq.tx)
            (dma.set_dreq if rx else dma.clear_dreq)(dreq.rx)

        def feed_byte(self, value: int) -> None:
            if name == "feed_ignores_enable":
                self.rx_fifo.push(value)
                self._interrupt_status |= U.UARTRXINTR
                self.check_interrupts()
                self._update_dreq()
                return
            if name == "feed_ignores_rxe" and self.enabled:
                self.rx_fifo.push(value)
                self._interrupt_status |= U.UARTRXINTR
                self.check_interrupts()
                self._update_dreq()
                return
            if name == "overrun_no_status" and self.enabled and self.rx_enabled and self.rx_fifo.full:
                return
            if name == "overrun_no_interrupt" and self.enabled and self.rx_enabled and self.rx_fifo.full:
                self._rsr |= U.RSR_OE
                return
            if name == "overrun_pushes" and self.enabled and self.rx_enabled and self.rx_fifo.full:
                self._rsr |= U.RSR_OE
                self._interrupt_status |= U.UARTOEINTR
                self.rx_fifo.pull()
                self.rx_fifo.push(value)
                self.check_interrupts()
                return
            if name == "rx_full_overwrites" and self.rx_fifo.full:
                self.rx_fifo.pull()
            if name == "feed_no_irq_update":
                self.rx_fifo.push(value)
                self._interrupt_status |= U.UARTRXINTR
                return
            super().feed_byte(value)

        def read_uint32(self, offset: int) -> int:
            if offset == U.UARTDR and name == "dr_read_keeps_rxintr":
                value = self.rx_fifo.pull()
                self.check_interrupts()
                self._update_dreq()
                return value
            if offset == U.UARTDR and name == "dr_read_no_dreq_update":
                value = self.rx_fifo.pull()
                if not self.rx_fifo.empty:
                    self._interrupt_status |= U.UARTRXINTR
                else:
                    self._interrupt_status &= ~U.UARTRXINTR
                self.check_interrupts()
                return value
            if offset == U.UARTIFLS and name == "ifls_reads_ilpr":
                return self._ilpr
            if offset == U.UARTRSR and name == "rsr_reads_zero":
                return 0
            if offset == U.UARTDR and name == "dr_read_peeks":
                return self.rx_fifo.peek()
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if offset == U.UARTDR and name == "dr_write_no_txintr":
                if self.on_byte:
                    self.on_byte(value & 0xFF)
                return
            if offset == U.UARTDR and name == "dr_write_unmasked_byte":
                if self.on_byte:
                    self.on_byte(value & 0xFFF)
                self._interrupt_status |= U.UARTTXINTR
                self.check_interrupts()
                return
            if offset == U.UARTICR and name == "icr_decoded_value":
                self._interrupt_status &= ~value
                self.check_interrupts()
                return
            if offset == U.UARTICR and name == "icr_clears_all":
                self._interrupt_status = 0
                self.check_interrupts()
                return
            if offset == U.UARTIMSC and name == "imsc_unmasked":
                self._interrupt_mask = value
                self.check_interrupts()
                return
            if offset == U.UARTIBRD and name == "ibrd_unmasked":
                self._int_divisor = value & 0xFFFFF
                if self.on_baud_rate_change:
                    self.on_baud_rate_change(self.baud_rate)
                return
            if offset == U.UARTFBRD and name == "fbrd_unmasked":
                self._frac_divisor = value & 0xFF
                if self.on_baud_rate_change:
                    self.on_baud_rate_change(self.baud_rate)
                return
            if offset == U.UARTIBRD and name == "ibrd_no_callback":
                self._int_divisor = value & 0xFFFF
                return
            if offset == U.UARTCR and name == "cr_unmasked":
                self._ctrl_register = value
                self._update_dreq()
                return
            if offset == U.UARTLCR_H and name == "lcr_unmasked":
                self._line_ctrl_register = value
                return
            if offset == U.UARTIFLS and name == "ifls_unmasked":
                self._ifls = value
                return
            if offset == U.UARTDMACR and name == "dmacr_unmasked":
                self._dmacr = value
                self._update_dreq()
                return
            if offset == U.UARTDMACR and name == "dmacr_no_dreq_update":
                self._dmacr = value & 0x7
                return
            if offset == U.UARTILPR and name == "ilpr_unmasked":
                self._ilpr = value
                return
            if offset == U.UARTRSR and name == "ecr_does_not_clear":
                return
            if offset == U.UARTDR and name == "dr_write_ignores_enable":
                if self.on_byte:
                    self.on_byte(value & 0xFF)
                self._interrupt_status |= U.UARTTXINTR
                self.check_interrupts()
                return
            if offset == U.UARTDR and name == "dr_write_ignores_txe":
                if self.enabled:
                    if self.on_byte:
                        self.on_byte(value & 0xFF)
                    self._interrupt_status |= U.UARTTXINTR
                    self.check_interrupts()
                return
            if offset == U.UARTDR and name == "loopback_ignored":
                if self.enabled and self.tx_enabled:
                    if self.on_byte:
                        self.on_byte(value & 0xFF)
                    self._interrupt_status |= U.UARTTXINTR
                    self.check_interrupts()
                return
            if offset == U.UARTICR and name == "icr_no_dreq_update":
                self._interrupt_status &= ~self.raw_write_value
                self.check_interrupts()
                return
            super().write_uint32(offset, value)

        @property
        def baud_rate(self) -> int:
            if name == "baud_floor":
                import math

                return math.floor(self.rp2040.clk_peri / (self.baud_divider * 16))
            return super().baud_rate

        def reset(self) -> None:
            on_byte, on_baud = self.on_byte, self.on_baud_rate_change
            if name == "reset_keeps_ifls":
                kept = self._ifls
                super().reset()
                self._ifls = kept
                return
            if name == "reset_keeps_dmacr":
                kept = self._dmacr
                super().reset()
                self._dmacr = kept
                return
            if name == "reset_keeps_rsr":
                kept = self._rsr
                super().reset()
                self._rsr = kept
                return
            if name == "reset_keeps_rx":
                kept = list(self.rx_fifo.items)
                super().reset()
                for item in kept:
                    self.rx_fifo.push(item)
                return
            super().reset()
            if name == "reset_clears_callbacks":
                self.on_byte = self.on_baud_rate_change = None
            elif name == "reset_keeps_mask":
                pass
            else:
                self.on_byte, self.on_baud_rate_change = on_byte, on_baud

    return Rig("mutant", Mutant)


MUTANTS = (
    "fifo_16", "flags_no_txfe", "flags_rxff_wrong", "rx_full_overwrites", "feed_no_irq_update", "dr_read_keeps_rxintr", "dr_read_peeks", "dr_write_no_txintr",
    "dr_write_unmasked_byte", "icr_decoded_value", "icr_clears_all", "imsc_unmasked", "ibrd_unmasked", "fbrd_unmasked", "ibrd_no_callback", "baud_floor",
    "reset_keeps_rx", "reset_clears_callbacks", "reset_keeps_ifls", "reset_keeps_dmacr", "reset_keeps_rsr", "cr_unmasked", "lcr_unmasked", "ifls_unmasked",
    "dmacr_unmasked", "dmacr_no_dreq_update", "ilpr_unmasked", "ecr_does_not_clear", "dr_write_ignores_enable", "dr_write_ignores_txe", "icr_no_dreq_update",
    "dreq_tx_ignores_dmacr", "dreq_tx_ignores_txe", "dreq_rx_ignores_dmacr", "dreq_rx_when_empty", "dreq_rx_ignores_rxe", "dreq_ignores_dmaonerr",
    "dreq_ignores_uarten", "dreq_swapped_channels", "feed_ignores_enable", "feed_ignores_rxe", "overrun_no_status", "overrun_no_interrupt", "overrun_pushes",
    "dr_read_no_dreq_update", "ifls_reads_ilpr", "rsr_reads_zero", "loopback_ignored",
)  # fmt: skip


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == U.UARTCR:
        return r.choice((0, 0x301, 0x300, 0x1, 0x201, 0x101, r.getrandbits(16), r.getrandbits(32)))
    if offset in (U.UARTIMSC, U.UARTICR):
        return r.choice(INTERRUPT_BITS) if roll < 0.7 else r.getrandbits(32)
    if offset in (U.UARTIBRD, U.UARTFBRD):
        return (
            r.choice((0, 1, 67, 52, 6, 0x3F, 0xFFFF, 0x10000, r.randrange(0, 0x100)))
            if roll < 0.8
            else r.getrandbits(32)
        )
    if offset == U.UARTLCR_H:
        return r.choice((0, 0x70, 0x60, 0x10, 0x30, 0x7F, 0x1FF, r.getrandbits(8), r.getrandbits(32)))
    if offset == U.UARTDMACR:
        return r.choice((0, 1, 2, 3, 5, 7, r.getrandbits(3), r.getrandbits(32)))
    if offset == U.UARTIFLS:
        return r.choice((0, 0x12, 0x3F, r.getrandbits(6), r.getrandbits(32)))
    if offset == U.UARTRSR:
        return r.choice((0, 0xFF, r.getrandbits(32)))
    if offset == U.UARTDR:
        return r.getrandbits(8) if roll < 0.8 else r.getrandbits(32)
    return r.getrandbits(32)


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = [
        ("write", U.UARTCR, 0x301, ALIASES[0]),
        ("write", U.UARTDMACR, 3, ALIASES[0]),
    ]  # an enabled UART, as a firmware leaves it
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.04:  # firmware (re)enabling the UART and its DMA requests
            ops.append(("write", U.UARTCR, r.choice((0x301, 0x301, 0x381, 0x201, 0x101, 0x1)), ALIASES[0]))
            ops.append(("write", U.UARTDMACR, r.choice((3, 3, 1, 2, 5, 7)), ALIASES[0]))
            ops.append(("write", U.UARTIMSC, r.choice((0x10, 0x30, 0x7FF, 0x400)), ALIASES[0]))
        elif roll < 0.30:
            offset = r.choice(WRITABLE + (U.UARTIMSC, U.UARTICR, U.UARTCR, U.UARTDR))
            alias = ALIASES[0] if r.random() < 0.7 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.34:
            ops.append(
                ("write", r.choice(UNIMPLEMENTED + (U.UARTFR, U.UARTIRIS, U.UARTIMIS)), r.getrandbits(32), ALIASES[0])
            )
        elif roll < 0.55:  # a burst of received bytes - some bursts overflow the 32-entry FIFO
            count = r.choice((1, 1, 2, 3, 8, 20, 33, 40))
            ops.extend(("feed", r.getrandbits(r.choice((8, 8, 8, 32)))) for _ in range(count))
        elif roll < 0.70:  # drain: DR reads, sometimes more of them than there are bytes
            ops.extend(("read", U.UARTDR, ALIASES[0]) for _ in range(r.choice((1, 1, 2, 5, 12, 33))))
        elif roll < 0.82:
            ops.append(
                (
                    "read",
                    r.choice(REGISTERS + UNIMPLEMENTED + (U.UARTIMSC, U.UARTICR)),
                    r.choice(ALIASES) if r.random() < 0.15 else ALIASES[0],
                )
            )
        elif roll < 0.87:
            ops.append(("reset",))
            if r.random() < 0.8:
                ops.append(("write", U.UARTCR, 0x301, ALIASES[0]))
        elif roll < 0.92:
            ops.append(("callbacks", r.random() < 0.7, r.random() < 0.7))
        elif roll < 0.96:
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

    uart = rig.chip.uart[0]
    for op in ops:
        before = len(rig.log)
        queued = not uart.rx_fifo.empty
        if op[0] == "feed" and uart.rx_fifo.full:
            count("rx.full_drop")
        if op[0] == "read" and op[1] == U.UARTDR and op[2] == 0:
            count("dr.read.empty" if uart.rx_fifo.empty else "dr.read.data")
        if op[0] == "write" and op[1] == U.UARTICR:
            count("icr.alias" if op[3] else "icr.plain")
        if op[0] == "write" and op[1] == U.UARTIMSC:
            count("imsc")
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(f"log.{event[0]}" + (f".{event[2]}" if event[0] in ("dreq", "irq") else ""))
        if op[0] == "write" and op[1] == U.UARTDR and any(e[0] == "byte" for e in rig.log[before:]):
            count("dr.write.byte")
        if op[0] == "reset":
            count("reset.fifo_nonempty" if queued else "reset")
    return counts
