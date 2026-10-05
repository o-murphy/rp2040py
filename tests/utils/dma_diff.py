"""A lockstep differential oracle for the DMA controller: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the design note of the DMA). The method is the PIO's (tests/utils/pio_diff.py): one chip whose DMA is the
*pure-Python* reference (``peripherals/_dma.py``, built explicitly because the facade would hand out the native one) and one whose DMA is whatever the facade
gives (today the same class, later the C++ one), fed one stream of operations:

* writes through the **bus** to every DMA register - the per-channel blocks with their alias layouts (so the trigger registers, the write-clear bits of CTRL and
  the set/xor/clear alias decode are part of what is compared), the interrupt registers (INTR is write-1-to-clear through ``raw_write_value``), the four pacing
  timers, MULTI_CHAN_TRIGGER and CHAN_ABORT; reads of all of them;
* "programs" for a channel: source, destination, count and a control word drawn over every data size (with and without BSWAP), increments, rings on either side,
  chains (including to itself and past the last channel), IRQ_QUIET and every TREQ kind (permanent, the four timers, DREQs);
* clock ticks of every size - the DMA is clock-driven, one alarm per channel - and ``clk_sys`` changes (the timers' period depends on it);
* DREQ edges raised directly (``set_dreq``/``clear_dreq``, as UART/SPI/ADC/PWM do) and *from inside a transfer*: the **probe** is a peripheral in an unused window
  that a DMA channel can read from and write to, and whose registers raise and clear DREQs - so the re-entrancy of a transfer that writes a PIO FIFO (which
  answers by changing a DREQ while the DMA is still inside ``transfer()``) is exercised without a PIO in the harness;
* memory pokes, a ``reset()``, and some addresses that are not mapped.

After **each** step the whole readable register file (so every channel's addresses, count, control, DBG registers), the next alarm of the chip's clock (a transfer
scheduled at a different time is a difference at once), the asserted DREQs, the data in the memory the channels work on, the probe's state, and the ordered logs
of what left the block (interrupt-line changes, probe accesses) are compared; an exception on one side and not the other is a difference like any other.

Kept out of the domain on purpose: a TRANS_COUNT of 0 at a trigger. The reference starts such a channel (BUSY set, nothing scheduled) and leaves it BUSY for good; the
next CTRL rewrite or DREQ edge then runs a transfer, which takes the count to -1. What hardware does is a decision for the port (record 0096's progress log), not
something the oracle should freeze by accident - the generator keeps every count at 1 or more. The same -1 is reachable *without* it, by a stale alarm (a CTRL
rewrite while a channel is running re-arms its alarm; if that was its last transfer, the alarm then runs one more on the finished channel), and that is part of the
reference's behaviour the C++ reproduces; only the representation differs (a Python -1 vs the word 0xFFFFFFFF), which ``BusView`` below normalises.

Same precondition as every oracle of this record: the pure DMA against the implementation under test must show 0 differences on long runs
(tests/test_dma_diff.py), and a deliberately damaged run must be caught.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.rp2040 import RP2040

DMA_BASE = 0x50000000
PROBE_BASE = 0x50400000  # an unused window (XIP_AUX on the chip), handed to the probe
CHANNELS = 12

SRC = 0x20000100
DST = 0x20001000
SPAN = 0x400
FLASH = 0x10000000
UNMAPPED = (0x30000000, 0x60000000, 0xE0200000)

# register offsets of the controller (not of a channel)
INTR, INTE0, INTF0, INTS0, INTE1, INTF1, INTS1 = 0x400, 0x404, 0x408, 0x40C, 0x414, 0x418, 0x41C
TIMERS = (0x420, 0x424, 0x428, 0x42C)
MULTI_CHAN_TRIGGER, SNIFF_CTRL, SNIFF_DATA, FIFO_LEVELS, CHAN_ABORT, N_CHANNELS = (
    0x430,
    0x434,
    0x438,
    0x440,
    0x444,
    0x448,
)
CONTROLLER_REGISTERS = (INTR, INTE0, INTF0, INTS0, INTE1, INTF1, INTS1, *TIMERS, MULTI_CHAN_TRIGGER, SNIFF_CTRL, SNIFF_DATA, FIFO_LEVELS, CHAN_ABORT, N_CHANNELS)  # fmt: skip
ALIASES = (0x0000, 0x0000, 0x0000, 0x1000, 0x2000, 0x3000)

# the DREQs the stimulus raises: the pacing-timer and permanent TREQs, the real peripherals' range, and a few that no channel listens to
DREQS = (0, 1, 2, 7, 8, 9, 10, 11, 20, 21, 22, 23, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F)
TREQS = (0x3F, 0x3F, 0x3B, 0x3C, 0x3D, 0x3E, 0, 1, 2, 7, 8, 9, 10, 11, 20, 21)

# SPAN-wide windows a channel works in
POOL = (SRC, DST, SRC + 0x200, DST + 0x200)


def channel_register(channel: int, offset: int) -> int:
    return channel * 0x40 + offset


class BusView:
    """What the bus sees of the pure-Python DMA: its registers as 32-bit words. The reference keeps a count that went below zero as a Python ``-1`` (see the
    module docstring: a stale alarm can run a transfer on a channel that already finished) which a 32-bit bus cannot carry - the Cython bus raises
    ``OverflowError`` - where the C++ block reads it back as 0xFFFFFFFF; the word is what the comparison is about."""

    def __init__(self, dma: Any) -> None:
        self._dma = dma

    def read_uint32(self, offset: int) -> int:
        return int(self._dma.read_uint32(offset)) & 0xFFFFFFFF

    def write_uint32(self, offset: int, value: int) -> None:
        self._dma.write_uint32(offset, value)

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        self._dma.write_uint32_atomic(offset, value, atomic_type)

    def reset(self) -> None:
        self._dma.reset()


class Probe:
    """A peripheral channels can read and write. A write raises or clears a DREQ (``offset 0``: value bits 0-5 the DREQ, bit 8 set/clear), which is what a
    PIO TX FIFO does in answer to a DMA write; everything else just goes to the log. A read returns a number that changes with every read, so a copy
    from the probe is distinguishable from one of constants."""

    def __init__(self, chip: RP2040, log: list) -> None:
        self.chip, self.log = chip, log
        self.reads = 0
        self.last_write = 0

    def read_uint32(self, offset: int) -> int:
        self.reads += 1
        return (self.reads * 0x9E3779B1 + offset) & 0xFFFFFFFF

    def write_uint32(self, offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        self.last_write = value
        self.log.append(("probe", offset, value))
        if offset == 0:
            number = value & 0x3F
            if value & 0x100:
                self.chip.dma.set_dreq(number)
            else:
                self.chip.dma.clear_dreq(number)

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        self.write_uint32(offset, value)

    def reset(self) -> None:
        pass


class Rig:
    """One chip plus the logs of everything that left its DMA."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.chip = RP2040()
        if kind == "pure":
            from rp2040py.peripherals import _dma

            # The DREQs the chip's peripherals asserted at construction (a UART/SPI TX FIFO is ready from the start) went to the DMA built with the chip;
            # the replacement starts from the same levels, as a DMA reset leaves them (_dma.RPDMA.reset).
            levels = dict(self.chip.dma.dreq)
            self.chip.dma = _dma.RPDMA(self.chip, "DMA")
            self.chip.dma.dreq.update(levels)
            self.chip.peripherals[DMA_BASE >> 12] = BusView(self.chip.dma)
        self.log: list[tuple] = []
        self.probe = Probe(self.chip, self.log)
        self.chip.peripherals[PROBE_BASE >> 12] = self.probe
        original = self.chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            if int(irq) in (int(IRQ.DMA_IRQ0), int(IRQ.DMA_IRQ1)):
                self.log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        self.chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                self.chip.logger,
                method,
                lambda name, message, _m=method: self.log.append(("log", _m, str(name), str(message))),
            )

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            _, offset, value, alias = op
            chip.write_uint32(DMA_BASE + alias + offset, value & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(DMA_BASE + op[1]))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "dreq":
            _, number, level = op
            if level:
                chip.dma.set_dreq(number)
            else:
                chip.dma.clear_dreq(number)
        elif kind == "poke":
            chip.write_uint32(op[1], op[2])
        elif kind == "peek":
            return int(chip.read_uint32(op[1]))
        elif kind == "reset":
            chip.dma.reset()
        elif kind == "clk":
            chip.clk_sys = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip = self.chip
        regs = []
        for channel in range(CHANNELS):
            for offset in (0x0, 0x4, 0x8, 0xC, 0x800, 0x804):
                regs.append(int(chip.read_uint32(DMA_BASE + channel_register(channel, offset))))
        for offset in CONTROLLER_REGISTERS:
            regs.append(int(chip.read_uint32(DMA_BASE + offset)))
        out: dict[str, Any] = {"regs": tuple(regs)}
        out["int_raw"] = int(chip.dma.int_raw)
        out["dreq"] = tuple(sorted(int(k) for k, v in chip.dma.dreq.items() if v))
        out["now"] = float(chip.clock.nanos)
        out["next_alarm"] = (bool(chip.clock.has_scheduled_alarm), float(chip.clock.nanos_to_next_alarm))
        out["probe"] = (self.probe.reads, self.probe.last_write)
        out["ram"] = tuple(
            int(chip.read_uint32(base + 4 * i)) for base in (SRC, DST) for i in range(0, SPAN // 4 * 2, 3)
        )
        return out


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose DMA is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic* (and not only of state poked from outside)."""
    from rp2040py.peripherals import _dma

    class Channel(_dma.RPDMAChannel):
        def transfer(self) -> None:
            saved = self._ctrl
            if name == "no_chain":
                self._chain_to = self.index
            elif name == "ignores_quiet":
                self._ctrl &= ~_dma.IRQ_QUIET
            elif name == "ring_ignored":
                self._ring_mask = 0
            try:
                super().transfer()
            finally:
                if name in ("ignores_quiet",):
                    self._ctrl = (self._ctrl & ~_dma.IRQ_QUIET) | (saved & _dma.IRQ_QUIET)

        def write_uint32(self, offset: int, value: int) -> None:
            super().write_uint32(offset, value)
            if name == "bswap_ignored" and offset in _dma.CTRL_REGS:
                self._transfer_fn = {1: self.transfer16, 2: self.transfer32}.get(self._data_size, self._transfer_fn)

    class Controller(_dma.RPDMA):
        def set_dreq(self, dreq_channel: int) -> None:
            if name == "dreq_does_not_wake":
                self.dreq[dreq_channel] = True
                return
            super().set_dreq(dreq_channel)

        def get_timer(self, treq: int) -> float:
            if name == "timer3_shifts_16" and treq == _dma.TREQ.TIMER3:
                dividend, divisor = self._timer3 >> 16, self._timer3 & 0xFFFF
                return 0 if divisor == 0 else ((dividend / divisor) * 1e6) / self.rp2040.clk_sys
            return super().get_timer(treq)

        def reset(self) -> None:
            super().reset()
            if name == "reset_clears_dreq":
                self.dreq.clear()

    rig = Rig("pure")
    levels = dict(rig.chip.dma.dreq)
    dma = Controller(rig.chip, "DMA")
    dma.channels = [Channel(dma, rig.chip, index) for index in range(CHANNELS)]
    dma.dreq.update(levels)
    rig.chip.dma = dma
    rig.chip.peripherals[DMA_BASE >> 12] = BusView(dma)
    return rig


MUTANTS = (
    "no_chain",
    "ignores_quiet",
    "ring_ignored",
    "bswap_ignored",
    "dreq_does_not_wake",
    "timer3_shifts_16",
    "reset_clears_dreq",
)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _address(r: random.Random, size: int) -> int:
    roll = r.random()
    if roll < 0.80:
        address = r.choice(POOL) + r.randrange(0, SPAN)
    elif roll < 0.90:
        address = PROBE_BASE + 4 * r.randrange(4)
    elif roll < 0.95:
        address = FLASH + r.randrange(0, 0x400)
    else:
        address = r.choice(UNMAPPED)
    if r.random() < 0.9:
        address &= ~(size - 1)
    return address


COUNT_OFFSETS = (0x8, 0x1C, 0x24, 0x38)  # the TRANS_COUNT register of each alias layout (a trigger one among them)
TRIGGER_OFFSETS = (0xC, 0x1C, 0x2C, 0x3C)


CTRL_OFFSETS = (0xC, 0x10, 0x20, 0x30)


def _chain_to(r: random.Random, channel: int) -> int:
    """Itself (no chain) or a *higher* channel, 12..15 (which do not exist) included: a chain back to a lower one can form a cycle of zero-delay transfers
    (A chains to B chains to A on a PERMANENT TREQ) that never lets a clock tick return - a loop the hardware has too, not a difference to look for."""
    return channel if r.random() < 0.6 else r.randrange(channel, 16)


def _register_value(r: random.Random, offset: int, channel: int) -> int:
    """A random register value - except that a TRANS_COUNT is kept small (a 2**32 count on a PERMANENT channel would be a transfer loop the run never leaves)
    and a trigger is non-zero most of the time (a zero trigger is the "null trigger", exercised on its own)."""
    value = r.getrandbits(32)
    if offset in COUNT_OFFSETS:
        value = (value & 0x3F) or 1  # never 0: see the module docstring's last paragraph
    if offset in CTRL_OFFSETS:
        value = (value & ~(0xF << 11)) | (_chain_to(r, channel) << 11)
    if offset in TRIGGER_OFFSETS and r.random() < 0.9:
        value |= 1
    return value


def _program(r: random.Random, channel: int) -> list[tuple]:
    size_code = r.choice((0, 1, 1, 2, 2, 2, 3))  # 3 is the reserved encoding, taken as bytes
    size = (1, 2, 4, 1)[size_code]
    ctrl = 1  # EN
    ctrl |= size_code << 2
    ctrl |= (r.random() < 0.8) << 4  # INCR_READ
    ctrl |= (r.random() < 0.8) << 5  # INCR_WRITE
    ring_size = r.choice((0, 0, 0, 0, 1, 2, 3, 4, 5, 8, 15))
    ctrl |= ring_size << 6
    ctrl |= (r.getrandbits(1)) << 10  # RING_SEL
    chain = _chain_to(r, channel)
    ctrl |= chain << 11
    ctrl |= r.choice(TREQS) << 15
    ctrl |= (r.random() < 0.15) << 21  # IRQ_QUIET
    ctrl |= (r.random() < 0.2) << 22  # BSWAP
    ctrl |= (r.random() < 0.1) << 1  # HIGH_PRIORITY
    ctrl |= (r.random() < 0.05) << 29 | (r.random() < 0.05) << 30  # sticky errors, written back
    count = r.choice((1, 1, 2, 3, 4, 8, 17, 40))
    ops: list[tuple] = [("write", channel_register(channel, 0x0), _address(r, size), 0)]  # READ_ADDR
    ops.append(("write", channel_register(channel, 0x4), _address(r, size), 0))  # WRITE_ADDR
    ops.append(("write", channel_register(channel, 0x8), count, 0))  # TRANS_COUNT
    trigger = r.choice((0xC, 0xC, 0xC, 0x1C, 0x2C, 0x3C))  # CTRL_TRIG itself or an alias that does not trigger
    if trigger == 0xC:
        ops.append(("write", channel_register(channel, 0xC), ctrl, 0))
    else:  # the alias that does not trigger, then a trigger through one of the others
        ops.append(("write", channel_register(channel, 0x10), ctrl, 0))  # AL1_CTRL
        alias = r.choice((0x1C, 0x2C, 0x3C, 0x1C))
        ops.append(("write", channel_register(channel, alias), _register_value(r, alias, channel), 0))
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    for base in (SRC, DST):
        for word in range(0, SPAN * 2, 4):
            ops.append(("poke", base + word, r.getrandbits(32)))
    for channel in range(
        CHANNELS
    ):  # a channel that was never given a count would have 0 at its first trigger (see the docstring)
        ops.append(("write", channel_register(channel, 0x8), 1, 0))
    for index, timer in enumerate(TIMERS):
        ops.append(("write", timer, (r.choice((1, 1, 2, 3)) << 16) | r.choice((1, 2, 3, 7, 40, 0)), 0))
    ops.append(("write", INTE0, r.getrandbits(12), 0))
    ops.append(("write", INTE1, r.getrandbits(12), 0))
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.26:
            ops.extend(_program(r, r.randrange(CHANNELS)))
        elif roll < 0.48:
            ops.append(("tick", r.choice((0, 1, 7, 8, 9, 16, 100, 1000, 5000, 100000, 2000000))))
        elif roll < 0.58:
            ops.append(("dreq", r.choice(DREQS), r.getrandbits(1)))
        elif roll < 0.62:
            ops.append(("dreq", r.randrange(64), r.getrandbits(1)))
        elif roll < 0.66:
            ops.append(("write", MULTI_CHAN_TRIGGER, r.getrandbits(12) if r.random() < 0.7 else r.getrandbits(16), 0))
        elif roll < 0.70:
            ops.append(("write", CHAN_ABORT, r.getrandbits(12) if r.random() < 0.8 else 1 << r.randrange(12), 0))
        elif roll < 0.74:
            ops.append(("write", r.choice((INTR, INTS0, INTS1)), r.getrandbits(16), r.choice(ALIASES)))
        elif roll < 0.78:
            ops.append(("write", r.choice((INTE0, INTE1, INTF0, INTF1)), r.getrandbits(32), r.choice(ALIASES)))
        elif roll < 0.81:
            ops.append(("write", r.choice(TIMERS), (r.choice((1, 1, 2, 3, 100)) << 16) | r.choice((1, 2, 3, 7, 40, 0, 0xFFFF)), r.choice(ALIASES)))  # fmt: skip
        elif roll < 0.84:
            channel = r.randrange(CHANNELS)
            offset = r.choice(
                (
                    0x0,
                    0x4,
                    0x8,
                    0xC,
                    0x10,
                    0x14,
                    0x18,
                    0x1C,
                    0x20,
                    0x24,
                    0x28,
                    0x2C,
                    0x30,
                    0x34,
                    0x38,
                    0x3C,
                    0x800,
                    0x804,
                )
            )
            alias = r.choice(ALIASES)
            if offset in COUNT_OFFSETS:
                alias = 0  # an xor / clear alias could take the reload value to 0 (see the docstring)
            elif offset in CTRL_OFFSETS and alias in (0x1000, 0x3000):
                alias = 0x2000  # xor / clear could lower the CHAIN_TO field (below the channel: a cycle); set can only raise it
            ops.append(("write", channel_register(channel, offset), _register_value(r, offset, channel), alias))
        elif roll < 0.92:
            channel = r.randrange(CHANNELS)
            offset = r.choice((0x0, 0x4, 0x8, 0xC, 0x1C, 0x2C, 0x3C, 0x800, 0x804))
            ops.append(("read", channel_register(channel, offset)))
        elif roll < 0.94:
            ops.append(("read", r.choice(CONTROLLER_REGISTERS)))
        elif roll < 0.96:
            ops.append(("poke", r.choice(POOL) + 4 * r.randrange(SPAN // 4), r.getrandbits(32)))
        elif roll < 0.97:
            ops.append(
                ("write", r.choice((SNIFF_CTRL, SNIFF_DATA, 0x44C, 0x500)), r.getrandbits(32), 0)
            )  # not implemented
        elif roll < 0.98:
            ops.append(("clk", r.choice((125e6, 125e6, 48e6, 133e6, 12e6))))
        elif roll < 0.985:
            ops.append(("reset",))
        else:
            ops.append(("peek", r.choice(POOL) + 4 * r.randrange(SPAN // 4)))
    return ops[:steps]


# --- the comparison --------------------------------------------------------------------------------------------------------


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
            parts.append(f"{key}[{where[:6]}] {[value_a[i] for i in where[:6]]} vs {[value_b[i] for i in where[:6]]}")
        else:
            parts.append(f"{key}: {value_a} vs {value_b}")
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
            return Divergence(step, op, f"what left the block: {new_a[:6]} vs {new_b[:6]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    from rp2040py.peripherals import _dma

    counts: dict[str, int] = {}

    def count(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    channel_cls = _dma.RPDMAChannel
    original_transfer, original_start, original_abort = channel_cls.transfer, channel_cls.start, channel_cls.abort
    original_schedule = channel_cls.schedule_transfer

    def transfer(self: Any) -> None:
        ctrl = self._ctrl
        count(f"transfer.{self._transfer_fn.__name__}")
        if self._ring_mask:
            count("transfer.ring_read" if not ctrl & _dma.RING_SEL else "transfer.ring_write")
        if not ctrl & _dma.INCR_READ:
            count("transfer.fixed_read")
        if not ctrl & _dma.INCR_WRITE:
            count("transfer.fixed_write")
        last = self._trans_count == 1
        original_transfer(self)
        if last:
            count("finished.quiet" if ctrl & _dma.IRQ_QUIET else "finished.irq")
            if self._chain_to != self.index:
                count("chain.valid" if self._chain_to < len(self.dma.channels) else "chain.invalid")

    def start(self: Any) -> None:
        count(
            "start.running" if self._ctrl & _dma.BUSY else ("start.disabled" if not self._ctrl & _dma.EN else "start")
        )
        original_start(self)

    def abort(self: Any) -> None:
        count("abort.busy" if self._ctrl & _dma.BUSY else "abort.idle")
        original_abort(self)

    def schedule(self: Any) -> None:
        dma = self.dma
        if self._treq_value == _dma.TREQ.PERMANENT:
            count("schedule.permanent")
        elif dma.dreq.get(self._treq_value, False):
            count("schedule.dreq_asserted")
        elif dma.get_timer(self._treq_value):
            count("schedule.paced")
        else:
            count("schedule.stalled")
        original_schedule(self)

    channel_cls.transfer, channel_cls.start, channel_cls.abort, channel_cls.schedule_transfer = (
        transfer,
        start,
        abort,
        schedule,
    )  # type: ignore[method-assign]
    try:
        rig = Rig("pure")
        for op in ops:
            _, error = _step(rig, op)
            if error:
                count("exception")
            for event in rig.log:
                count(event[0])
            rig.log.clear()
    finally:
        channel_cls.transfer, channel_cls.start, channel_cls.abort, channel_cls.schedule_transfer = (
            original_transfer, original_start, original_abort, original_schedule,
        )  # type: ignore[method-assign]  # fmt: skip
    return counts
