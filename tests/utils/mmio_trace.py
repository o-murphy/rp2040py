"""Record one MCU block's bus traffic, replay it against a fresh instance, diff the answers.

The per-block parity oracle of docs/records/0096-cpp-mcu-core.md ("The per-block recipe"): when a
block is rewritten (Python -> C++ behind a Cython facade), the *same* recorded session is replayed
against the old and the new implementation, and every read value and every interrupt the block
raised must match.

A trace is a list of `Event` tuples ``(nanos, kind, a, b, c)`` in simulated time:

====  ===========================================  =====  =====  =====
kind  meaning                                      a      b      c
====  ===========================================  =====  =====  =====
r     bus read of the block                        offset result 0
w     a direct write_uint32() call on the block    offset value  0
a     a bus write: write_uint32_atomic()           offset value  atomic_type (0 plain, 1 xor, 2 set, 3 clear)
x     reset() through the bus's dispatch table     0      0      0
i     interrupt line change the block raised       irq    value  0
p     pin input levels the block depends on        gpio   qspi   0
====  ===========================================  =====  =====  =====

The bus sends every peripheral write through `write_uint32_atomic()` (a plain write is atomic_type 0), so
recorded writes are normally kind ``a``; kind ``w`` only appears for a caller that bypasses the bus.

What this does and does not capture: it records what crosses the bus's dispatch table
(``RP2040.peripherals`` / ``sio`` / ``ppb``) plus the interrupts the block raises through
``RP2040.set_interrupt()``, plus - for SIO only, the one block that *reads pins* - the pin input levels
(kind ``p``: the 30 GPIO ``input_value`` bits and the 6 QSPI ones, sampled just before an SIO read of
``GPIO_IN``/``GPIO_HI_IN`` whenever they differ from the last sample). A block driven by anything else
(another block's direct method call) replays as if that input never happened, so replay is exact only for
blocks whose inputs are bus accesses, time and those pin levels - which is true of TIMER and SIO, and is the
thing to check for each new block (a replay that diverges on a *Python-vs-same-Python* run is the sign of a
missed input; that is how SIO's pin inputs were found).

Replaying a ``p`` event drives each pin from outside (``GPIOPin.set_input_value``) to the recorded level. That
reproduces what the block read as long as the replay chip's pad input-enable is its reset value (enabled) - the
levels recorded are the *effective* ones (after the original run's pad configuration), so the replay does not
need the PADS or IO blocks. It is an input channel, not a model of who drove the pin.

Replay advances the fresh instance's own `SimulationClock` to each event's time with `tick()`, which
fires any due alarm at its own scheduled time - so alarm-driven interrupts reproduce at the same
nanosecond without having to be recorded as inputs.
"""

import gzip
import json
import os
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from rp2040py.rp2040 import RP2040

Event = "tuple[float, str, int, int, int]"

# The block a trace is about is named the way a caller thinks of it: a peripheral-table key
# (``0x40054`` is TIMER: the bus looks blocks up by ``address >> 12`` with the low two bits clear), or
# the two blocks the bus reaches by other means.
SIO = "sio"
PPB = "ppb"


def find_block(mcu: RP2040, block: "int | str") -> Any:
    """The live block object for `block` (a peripherals-table key, or ``"sio"``/``"ppb"``)."""
    if block == SIO:
        return mcu.sio
    if block == PPB:
        return mcu.ppb
    assert isinstance(block, int)
    return mcu.peripherals[block]


SIO_GPIO_IN, SIO_GPIO_HI_IN = 0x04, 0x08  # the two SIO registers whose value is a pin level, not SIO state
GPIO_PINS, QSPI_PINS = 30, 6


def pin_levels(mcu: RP2040) -> "tuple[int, int]":
    """The effective input level of every GPIO pin and every QSPI pin, one bit each: what SIO's
    ``GPIO_IN`` / ``GPIO_HI_IN`` registers return."""
    gpio = sum(1 << i for i in range(GPIO_PINS) if mcu.gpio[i].input_value)
    qspi = sum(1 << i for i in range(QSPI_PINS) if mcu.qspi[i].input_value)
    return gpio, qspi


PAD_INPUT_ENABLE = 0x40
FIRST_PAD_REGISTER = 0x04  # GPIO0 / QSPI SCLK; one 32-bit register per pin from here


def apply_pin_levels(mcu: RP2040, gpio: int, qspi: int) -> None:
    """Drives every pin from outside to the level recorded in a ``p`` event.

    The recorded bits are *effective* input levels (raw level AND the pad's input-enable), so each pad's
    input-enable is switched on first - through the PADS block's own register, as firmware would, because a
    fresh chip's GPIO pads reset with it off and would otherwise read 0 whatever is driven."""
    for pads, pins, count, bits in (
        (mcu.pads_bank0, mcu.gpio, GPIO_PINS, gpio),
        (mcu.pads_qspi, mcu.qspi, QSPI_PINS, qspi),
    ):
        for i in range(count):
            pad = FIRST_PAD_REGISTER + 4 * i
            pads.write_uint32(pad, pins[i].pad_value | PAD_INPUT_ENABLE)
            pins[i].set_input_value(bool((bits >> i) & 1))


class _Recorder:
    """Stands in for a block in the bus's dispatch table; logs the bus calls, forwards them."""

    def __init__(self, mcu: RP2040, target: Any, events: "list[Any]", *, samples_pins: bool = False) -> None:
        self._mcu = mcu
        self._target = target
        self._events = events
        self._samples_pins = samples_pins
        self._last_levels: tuple[int, int] | None = None

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._target, attribute)

    def read_uint32(self, offset: int) -> int:
        if self._samples_pins and offset in (SIO_GPIO_IN, SIO_GPIO_HI_IN):
            levels = pin_levels(self._mcu)
            if levels != self._last_levels:
                self._last_levels = levels
                self._events.append((self._mcu.clock.nanos, "p", levels[0], levels[1], 0))
        value = self._target.read_uint32(offset)
        self._events.append((self._mcu.clock.nanos, "r", offset, int(value) & 0xFFFFFFFF, 0))
        return value

    def write_uint32(self, offset: int, value: int) -> None:
        self._events.append((self._mcu.clock.nanos, "w", offset, int(value) & 0xFFFFFFFF, 0))
        self._target.write_uint32(offset, value)

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        self._events.append((self._mcu.clock.nanos, "a", offset, int(value) & 0xFFFFFFFF, atomic_type))
        self._target.write_uint32_atomic(offset, value, atomic_type)

    def reset(self) -> None:
        self._events.append((self._mcu.clock.nanos, "x", 0, 0, 0))
        self._target.reset()


def record(mcu: RP2040, block: "int | str", irqs: "Collection[int]" = ()) -> "list[Any]":
    """Starts recording `block` of `mcu`; returns the live event list.

    Install this right after the chip is constructed, before anything runs: replay starts a *fresh*
    block at time zero, so the trace must too. `irqs` are the interrupt lines this block owns - only
    those are logged (other blocks raise lines through the same `set_interrupt()`)."""
    events: list[Any] = []
    recorder = _Recorder(mcu, find_block(mcu, block), events, samples_pins=(block == SIO))
    if block == SIO:
        mcu.sio = recorder  # type: ignore[assignment]
    elif block == PPB:
        mcu.ppb = recorder  # type: ignore[assignment]
    else:
        mcu.peripherals[block] = recorder  # type: ignore[index, assignment]
    if irqs:
        _tap_interrupts(mcu, set(irqs), events)
    return events


def _tap_interrupts(mcu: RP2040, irqs: "set[int]", events: "list[Any]") -> None:
    original = mcu.set_interrupt

    def set_interrupt(irq: int, value: bool) -> None:
        if irq in irqs:
            events.append((mcu.clock.nanos, "i", int(irq), int(bool(value)), 0))
        original(irq, value)

    mcu.set_interrupt = set_interrupt  # type: ignore[method-assign]


@dataclass(frozen=True)
class Mismatch:
    index: int  # position in the recorded trace (-1: after its end)
    what: str
    expected: Any
    got: Any

    def __str__(self) -> str:
        return f"[{self.index}] {self.what}: expected {self.expected!r}, got {self.got!r}"


def replay(
    events: "list[Any]",
    block: "int | str",
    irqs: "Collection[int]" = (),
    *,
    factory: "Callable[[], RP2040]" = RP2040,
    limit: int = 20,
) -> "list[Mismatch]":
    """Replays `events` against a fresh chip's `block`; returns up to `limit` mismatches (empty = parity).

    `factory` builds the chip the block lives on - the hook for comparing another implementation
    (e.g. a chip whose `block` entry is a Cython facade over a C++ block) against the recording."""
    mcu = factory()
    target = find_block(mcu, block)
    seen: list[Any] = []
    if irqs:
        _tap_interrupts(mcu, set(irqs), seen)
    mismatches: list[Mismatch] = []

    def add(mismatch: Mismatch) -> bool:
        mismatches.append(mismatch)
        return len(mismatches) >= limit

    for index, (nanos, kind, a, b, c) in enumerate(events):
        if kind == "i":
            continue  # an *output*: compared against what this replay raises, below
        delta = nanos - mcu.clock.nanos
        if delta < 0:
            if add(Mismatch(index, "trace goes back in time", mcu.clock.nanos, nanos)):
                return mismatches
        elif delta > 0:
            mcu.clock.tick(delta)
        if kind == "r":
            got = int(target.read_uint32(a)) & 0xFFFFFFFF
            if got != b and add(Mismatch(index, f"read @{a:#x} (t={nanos})", b, got)):
                return mismatches
        elif kind == "w":
            target.write_uint32(a, b)
        elif kind == "a":
            target.write_uint32_atomic(a, b, c)
        elif kind == "x":
            target.reset()
        elif kind == "p":
            apply_pin_levels(mcu, a, b)
        else:
            raise ValueError(f"unknown event kind {kind!r} at {index}")

    expected_irqs = [e for e in events if e[1] == "i"]
    for position, (want, got) in enumerate(zip(expected_irqs, seen, strict=False)):
        if tuple(want) != tuple(got) and add(Mismatch(-1, f"interrupt #{position}", tuple(want), tuple(got))):
            return mismatches
    if len(expected_irqs) != len(seen):
        add(Mismatch(-1, "number of interrupt events", len(expected_irqs), len(seen)))
    return mismatches


def save(events: "list[Any]", path: "str | os.PathLike[str]") -> None:
    """One JSON array per line, gzip-compressed (a boot's TIMER trace is ~10^6 events)."""
    with gzip.open(path, "wt") as f:
        for event in events:
            f.write(json.dumps(list(event)) + "\n")


def load(path: "str | os.PathLike[str]") -> "list[Any]":
    with gzip.open(path, "rt") as f:
        return [tuple(json.loads(line)) for line in f]
