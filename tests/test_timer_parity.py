"""The native TIMER (C++ `TimerBlock`, docs/records/0096-cpp-mcu-core.md Phase 2) against the pure-Python reference.

Two chips with the same native bus and clock, one TIMER each: the pure-Python `peripherals/_timer.RPTimer` on one, the
chip's own native TIMER on the other. Randomized sessions of bus accesses (8/16/32-bit reads and writes, every alias,
implemented and unimplemented registers, awkward values), direct method calls, resets and clock ticks run on both; then
everything observable must be identical: every value read, every interrupt-line call (their number, order, line and
level - the Python block re-announces all four lines on every change), every message logged, `int_status`,
`raw_write_value`, and what happens when an interrupt-line call raises.
"""

import random

import pytest
from utils.chip_pair import Native, make_chip

from rp2040py.peripherals._timer import RPTimer as PureTimer

pytest.importorskip("rp2040py.native._timer", reason="the native extension is not built")

from rp2040py.native._timer import RPTimer as NativeTimer

TIMER_KEY, TIMER_BASE = 0x40054, 0x40054000
REGISTERS = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x40]
ALARMS = [0x10, 0x14, 0x18, 0x1C]
ALIASES = [0x0000, 0x1000, 0x2000, 0x3000]


class Boom(Exception):
    pass


class Probe:
    """One chip + one TIMER implementation, with everything the TIMER can affect recorded."""

    def __init__(self, pure: bool):
        self.chip = make_chip(Native)
        self.irqs, self.logged = [], []
        self.fail_at = None  # the Nth interrupt-line call from now raises
        self.chip.logger = self  # warning()/error()/... below
        real_set_interrupt = self.chip.set_interrupt

        def set_interrupt(irq, value):
            self.irqs.append((int(irq), bool(value)))
            if self.fail_at is not None:
                self.fail_at -= 1
                if self.fail_at < 0:
                    self.fail_at = None
                    raise Boom("injected")
            real_set_interrupt(irq, value)

        self.chip.set_interrupt = set_interrupt
        # Both sides are built explicitly, not taken from the facade: with RP2040PY_SKIP_CYTHON=1 the facade hands out the
        # pure class for both, and the comparison would be the reference against itself.
        self.timer = (PureTimer if pure else NativeTimer)(self.chip, "TIMER_BASE")
        self.chip.peripherals[TIMER_KEY] = self.timer

    def __getattr__(self, name):  # the logger protocol: debug/info/warning/error(component, message)
        if name in ("debug", "info", "warning", "error"):
            return lambda component, message: self.logged.append((name, component, message))
        raise AttributeError(name)

    def apply(self, op):
        kind = op[0]
        try:
            if kind == "tick":
                self.chip.clock.tick(op[1])
            elif kind == "read32":
                return ("r", self.chip.read_uint32(TIMER_BASE + op[1]))
            elif kind == "write32":
                self.chip.write_uint32(TIMER_BASE + op[1], op[2])
            elif kind == "write16":
                self.chip.write_uint16(TIMER_BASE + op[1], op[2] & 0xFFFF)
            elif kind == "write8":
                self.chip.write_uint8(TIMER_BASE + op[1], op[2] & 0xFF)
            elif kind == "direct_read":
                return ("r", self.timer.read_uint32(op[1]))
            elif kind == "direct_write":
                self.timer.write_uint32(op[1], op[2])
            elif kind == "direct_atomic":
                self.timer.write_uint32_atomic(op[1], op[2], op[3])
            elif kind == "reset":
                self.timer.reset()
            elif kind == "tick_hz":
                self.timer.tick_changed(op[1])  # the watchdog's tick, as the chip announces it
            elif kind == "fail_next_irq_call":
                self.fail_at = op[1]
        except Boom:
            return ("boom",)
        return None

    def observe(self):
        return (self.timer.int_status, self.timer.raw_write_value, self.chip.clock.nanos, self.timer.tick_hz)


def _value(rng, now_us):
    return rng.choice(
        [
            0, 1, 2, 3, 0xF, 0xFF, 0xFFFFFFFF, -1, -2, 0x80000000, 0x12345678,
            rng.getrandbits(32),
            int(now_us + rng.randrange(0, 400)) & 0xFFFFFFFF,  # an alarm that is about to be due
            int(now_us + rng.randrange(0, 20)) & 0xFFFFFFFF,
        ]
    )  # fmt: skip


TICKS = [
    0.0,
    1e6,
    1e6,
    1e6,
    2e6,
    5e5,
    6.5e6 / 12,
    12e6,
    1.5e6,
    3e5,
]  # Hz: stopped, the nominal tick, and the rates clk_ref / CYCLES can give


def _script(seed, length=400):
    rng = random.Random(seed)
    now_us = 0.0
    ops = [("tick_hz", 1e6)]  # the SDK starts the watchdog tick before anything uses the TIMER
    for _ in range(length):
        offset = rng.choice(REGISTERS) if rng.random() < 0.8 else rng.randrange(0, 0x1000) & ~3
        offset |= rng.choice(ALIASES) if rng.random() < 0.4 else 0
        value = _value(rng, now_us)
        roll = rng.random()
        if rng.random() < 0.04:
            ops.append(("tick_hz", rng.choice(TICKS)))
        if roll < 0.22:
            step = rng.choice(
                [0, 500, 1000, 5000, 50_000, 400_000, 3_000_000, 1e9, 4294967296.0 * 1000 * rng.randrange(1, 3)]
            )
            now_us += step / 1000
            ops.append(("tick", step))
        elif roll < 0.5:
            ops.append(("read32", offset))
        elif roll < 0.72:
            ops.append(("write32", offset, value))
        elif roll < 0.77:
            ops.append(("write16", offset, value))
        elif roll < 0.82:
            ops.append(("write8", offset + rng.randrange(4), value))
        elif roll < 0.87:
            ops.append(("direct_read", offset & 0x3FFF))
        elif roll < 0.91:
            ops.append(("direct_write", offset & 0xFFF, value))
        elif roll < 0.95:
            ops.append(("direct_atomic", offset & 0xFFF, value, rng.randrange(4)))
        elif roll < 0.975:
            ops.append(("reset",))
        else:
            ops.append(("fail_next_irq_call", rng.randrange(4)))
    return ops


@pytest.mark.parametrize("seed", range(60))
def test_the_native_timer_matches_the_pure_python_timer(seed):
    pure, native = Probe(pure=True), Probe(pure=False)

    for index, op in enumerate(_script(seed)):
        expected, got = pure.apply(op), native.apply(op)
        assert got == expected, f"op {index} {op}: native {got!r}, pure {expected!r}"
        assert native.observe() == pure.observe(), f"state after op {index} {op}"
        assert native.irqs == pure.irqs, f"interrupt-line calls after op {index} {op}"
        assert native.logged == pure.logged, f"log after op {index} {op}"

    assert pure.irqs, "the session should have announced some interrupts"


def test_the_two_timers_are_the_two_implementations():
    pure, native = Probe(pure=True), Probe(pure=False)

    assert type(pure.timer).__module__ == "rp2040py.peripherals._timer"
    assert type(native.timer).__module__ == "rp2040py.native._timer"


def test_an_interrupt_line_that_raises_surfaces_from_the_register_write_that_caused_it():
    for pure in (True, False):
        probe = Probe(pure=pure)
        probe.chip.write_uint32(TIMER_BASE + 0x38, 0xF)  # INTE
        probe.fail_at = 1

        with pytest.raises(Boom):
            probe.chip.write_uint32(TIMER_BASE + 0x3C, 0x1)  # INTF: forces a line, the second announcement raises

        assert probe.timer.int_status == 0x1  # the register was already updated: the exception stopped the rest


def test_an_interrupt_line_that_raises_surfaces_from_a_clock_tick_and_the_clock_stays_usable():
    for pure in (True, False):
        probe = Probe(pure=pure)
        probe.timer.tick_changed(1e6)
        clock = probe.chip.clock
        probe.chip.write_uint32(TIMER_BASE + 0x38, 0xF)
        probe.chip.write_uint32(TIMER_BASE + 0x10, 100)  # ALARM0 at t = 100 us
        probe.fail_at = 0

        with pytest.raises(Boom):
            clock.tick(1_000_000)

        assert clock.nanos == 100_000  # stopped at the alarm's own time
        clock.tick(1_000)  # and ticking again works
        assert clock.nanos == 101_000


def test_a_dropped_chip_with_an_armed_alarm_is_collected_and_the_clock_survives():
    """The native TIMER's alarms are nodes inside the block; its __dealloc__ has to unlink them from a clock that may
    outlive it, or the clock's list would point into freed memory."""
    import gc

    chip = make_chip(Native)
    chip.peripherals[TIMER_KEY] = NativeTimer(chip, "TIMER_BASE")
    chip.peripherals[TIMER_KEY].tick_changed(1e6)
    clock = chip.clock
    chip.write_uint32(TIMER_BASE + 0x10, 50)  # armed, due at 50 us
    assert clock.has_scheduled_alarm

    del chip
    gc.collect()

    assert not clock.has_scheduled_alarm  # unlinked by the dying TIMER
    clock.tick(10_000_000)  # would crash if a node were left behind


def test_a_wrapper_around_the_native_timer_still_sees_every_access():
    """The native fast path is looked up on the block's *type*, so a recorder/profiler that forwards attributes is
    not bypassed by lending out its target's C++ handler."""
    probe = Probe(pure=False)
    seen = []

    class Wrapper:
        def __init__(self, target):
            self.target = target

        def __getattr__(self, name):
            return getattr(self.target, name)

        def read_uint32(self, offset):
            seen.append(("r", offset))
            return self.target.read_uint32(offset)

        def write_uint32_atomic(self, offset, value, atomic_type):
            seen.append(("a", offset))
            self.target.write_uint32_atomic(offset, value, atomic_type)

    probe.chip.peripherals[TIMER_KEY] = Wrapper(probe.timer)

    probe.chip.read_uint32(TIMER_BASE + 0x0C)
    probe.chip.write_uint32(TIMER_BASE + 0x38, 1)

    assert seen == [("r", 0x0C), ("a", 0x38)]
