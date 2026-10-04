"""Record IO_BANK0 + PADS_BANK0 traffic of a real boot, replay it against a fresh chip, diff the answers.

Phase 3 of docs/records/0096-cpp-mcu-core.md: the pin layer (``GPIOPin``, ``RPIO``, ``RPPADS``) is rewritten in C++ and this is the
oracle it is judged by, the same way ``mmio_trace.py`` judges TIMER and SIO. Read that module's docstring first; this one only says what a
*pin* trace adds, because IO_BANK0 and PADS_BANK0 are thin windows over the state of the 30 ``GPIOPin`` objects and that state is also
written by other things:

====== ====================================================== ========= ===== =====
kind   meaning                                                a         b     c
====== ====================================================== ========= ===== =====
io.r   bus read of IO_BANK0 (compared)                        offset    value 0
io.a   bus write of IO_BANK0 (``write_uint32_atomic``)        offset    value atomic_type
io.x   ``reset()`` of IO_BANK0                                0         0     0
pads.* the same three for PADS_BANK0                          ...
sio.w  bus write of SIO (a *driver*, replayed, not compared)  offset    value 0
sio.x  ``reset()`` of SIO                                     0         0     0
q      who drives the pins from outside, 30 GPIO pins         raw mask  driven mask 0
i      the IO_BANK0 interrupt line changed                    irq       value 0
====== ====================================================== ========= ===== =====

* **SIO is a driver.** A pad's output level and direction (``SIO.GPIO_OUT``/``GPIO_OE``, with the pin's FUNCSEL = SIO) are what the
  ``STATUS`` register reports, so the SIO *writes* are replayed into the fresh chip. Its reads are not recorded or compared: SIO has its
  own oracle.
* **Pins driven from outside** (a button, the CYW43, a test) change ``_raw_input_value`` / ``_driven`` without touching any register. They
  are sampled just before every IO_BANK0 / PADS_BANK0 access and a ``q`` event is logged when they differ from the last sample; replay
  drives the pins to that state before the access. So an external change is reproduced as of the next register access, which is when it
  can first be observed; a pulse that comes and goes between two accesses is not seen (the same limitation as the SIO ``p`` event).
* **Interrupts** are compared by ``(irq, value)`` in order, *not* by time: an edge caused by an external drive is raised when the drive
  happened in the original run but when its ``q`` sample is applied in the replay.
* **PIO, PWM and the CYW43 as drivers are not modelled**: a trace of a boot that uses them replays as if they never drove a pin.
  Their pin traffic is covered by ``scripts/bench/pin_events.py`` (the order of the events listeners see).
"""

from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.rp2040 import RP2040
from utils.mmio_trace import Mismatch

IO_KEY = 0x40014  # the bus looks blocks up by ``address >> 12`` with the low two bits clear
PADS_KEY = 0x4001C
GPIO_PINS = 30
IO_IRQS = (IRQ.IO_BANK0,)

TARGETS = {"io": IO_KEY, "pads": PADS_KEY}


def drive_state(mcu: RP2040) -> "tuple[int, int]":
    """``(raw, driven)``: bit n of each is GPIO n's externally driven level and whether anything drives it at all."""
    raw = sum(1 << i for i in range(GPIO_PINS) if mcu.gpio[i]._raw_input_value)
    driven = sum(1 << i for i in range(GPIO_PINS) if mcu.gpio[i]._driven)
    return raw, driven


def apply_drive_state(mcu: RP2040, raw: int, driven: int) -> None:
    """Puts every pin into the recorded ``(raw, driven)`` state through the pin's own API, as a device would."""
    for i in range(GPIO_PINS):
        pin = mcu.gpio[i]
        want_driven = bool((driven >> i) & 1)
        want_raw = bool((raw >> i) & 1)
        if want_driven:
            if not pin._driven or pin._raw_input_value != want_raw:
                pin.set_input_value(want_raw)
        elif pin._driven:
            pin.release_input()


class _Tap:
    """Stands in for one block in the bus's dispatch table; logs the bus calls, forwards them."""

    def __init__(self, mcu: RP2040, name: str, target: Any, events: "list[Any]", state: "dict[str, Any]") -> None:
        self._mcu = mcu
        self._name = name
        self._target = target
        self._events = events
        self._state = state

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._target, attribute)

    def _sample(self) -> None:
        levels = drive_state(self._mcu)
        if levels != self._state["last"]:
            self._state["last"] = levels
            self._events.append((self._mcu.clock.nanos, "q", levels[0], levels[1], 0))

    def read_uint32(self, offset: int) -> int:
        self._sample()
        value = self._target.read_uint32(offset)
        self._events.append((self._mcu.clock.nanos, f"{self._name}.r", offset, int(value) & 0xFFFFFFFF, 0))
        return value

    def write_uint32(self, offset: int, value: int) -> None:
        self._sample()
        self._events.append((self._mcu.clock.nanos, f"{self._name}.w", offset, int(value) & 0xFFFFFFFF, 0))
        self._target.write_uint32(offset, value)

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        self._sample()
        self._events.append((self._mcu.clock.nanos, f"{self._name}.a", offset, int(value) & 0xFFFFFFFF, atomic_type))
        self._target.write_uint32_atomic(offset, value, atomic_type)

    def reset(self) -> None:
        self._events.append((self._mcu.clock.nanos, f"{self._name}.x", 0, 0, 0))
        self._target.reset()


class _SioDriver:
    """SIO as a *driver*: only its writes (and reset) are logged; reads are forwarded unrecorded. SIO has no atomic aliases, so the bus
    calls plain ``write_uint32`` on it."""

    def __init__(self, mcu: RP2040, target: Any, events: "list[Any]") -> None:
        self._mcu = mcu
        self._target = target
        self._events = events

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._target, attribute)

    def read_uint32(self, offset: int) -> int:
        return self._target.read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        self._events.append((self._mcu.clock.nanos, "sio.w", offset, int(value) & 0xFFFFFFFF, 0))
        self._target.write_uint32(offset, value)

    def reset(self) -> None:
        self._events.append((self._mcu.clock.nanos, "sio.x", 0, 0, 0))
        self._target.reset()


def _tap_interrupts(mcu: RP2040, events: "list[Any]") -> None:
    original = mcu.set_interrupt
    lines = {int(irq) for irq in IO_IRQS}

    def set_interrupt(irq: int, value: bool) -> None:
        if int(irq) in lines:
            events.append((mcu.clock.nanos, "i", int(irq), int(bool(value)), 0))
        original(irq, value)

    mcu.set_interrupt = set_interrupt  # type: ignore[method-assign]


def record_pins(mcu: RP2040) -> "list[Any]":
    """Starts recording IO_BANK0 + PADS_BANK0 (and the SIO writes that drive pins) of `mcu`; returns the live event list.

    Install this right after the chip is constructed, before anything runs: replay starts a *fresh* chip at time zero."""
    events: list[Any] = []
    state: dict[str, Any] = {"last": (0, 0)}
    for name, key in TARGETS.items():
        mcu.peripherals[key] = _Tap(mcu, name, mcu.peripherals[key], events, state)  # type: ignore[index, assignment]
    mcu.sio = _SioDriver(mcu, mcu.sio, events)  # type: ignore[assignment]
    _tap_interrupts(mcu, events)
    return events


def replay_pins(events: "list[Any]", *, factory: "Callable[[], RP2040]" = RP2040, limit: int = 20) -> "list[Mismatch]":
    """Replays `events` against a fresh chip; returns up to `limit` mismatches (empty = parity).

    `factory` builds the chip the pin blocks live on - the hook for comparing another implementation (e.g. a chip whose pins are
    the C++ ones) against the recording."""
    mcu = factory()
    targets = {name: mcu.peripherals[key] for name, key in TARGETS.items()}
    seen: list[Any] = []
    _tap_interrupts(mcu, seen)
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
        if kind == "q":
            apply_drive_state(mcu, a, b)
            continue
        block, operation = kind.split(".")
        target = mcu.sio if block == "sio" else targets[block]
        if operation == "r":
            got = int(target.read_uint32(a)) & 0xFFFFFFFF
            if got != b and add(Mismatch(index, f"{block} read @{a:#x} (t={nanos})", b, got)):
                return mismatches
        elif operation == "w":
            target.write_uint32(a, b)
        elif operation == "a":
            target.write_uint32_atomic(a, b, c)
        elif operation == "x":
            target.reset()
        else:
            raise ValueError(f"unknown event kind {kind!r} at {index}")

    expected = [(e[2], e[3]) for e in events if e[1] == "i"]
    got_irqs = [(e[2], e[3]) for e in seen]
    for position, (want, got) in enumerate(zip(expected, got_irqs, strict=False)):
        if want != got and add(Mismatch(-1, f"interrupt #{position}", want, got)):
            return mismatches
    if len(expected) != len(got_irqs):
        add(Mismatch(-1, "number of interrupt events", len(expected), len(got_irqs)))
    return mismatches
