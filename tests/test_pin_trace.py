"""Self-test of tests/utils/pin_trace.py (docs/records/0096-cpp-mcu-core.md, Phase 3): the pin oracle must prove it can tell a faithful
replay from a broken one - here with the current IO_BANK0/PADS_BANK0 against themselves, on a bare chip with no firmware."""

from utils.pin_trace import record_pins, replay_pins

from rp2040py.rp2040 import RP2040

IO_BASE, PADS_BASE, SIO_BASE = 0x40014000, 0x4001C000, 0xD0000000
GPIO_STATUS = lambda n: IO_BASE + 8 * n
GPIO_CTRL = lambda n: IO_BASE + 8 * n + 4
PAD = lambda n: PADS_BASE + 4 + 4 * n
PROC0_INTE0, PROC0_INTF0, PROC0_INTS0, INTR0 = IO_BASE + 0x100, IO_BASE + 0x110, IO_BASE + 0x120, IO_BASE + 0xF0
SIO_GPIO_OUT_SET, SIO_GPIO_OE_SET, SIO_GPIO_OUT_CLR = SIO_BASE + 0x14, SIO_BASE + 0x24, SIO_BASE + 0x18
FUNCSEL_SIO = 5
EDGE_HIGH, EDGE_LOW = 1 << 3, 1 << 2  # per-pin nibble of the IRQ registers


def _session() -> "list[tuple]":
    """A scripted session over the real bus: SIO drives pin 2, pin 3 is an input with an edge interrupt that the test drives from outside."""
    mcu = RP2040()
    events = record_pins(mcu)
    clock = mcu.clock
    mcu.write_uint32(GPIO_CTRL(2), FUNCSEL_SIO)
    mcu.write_uint32(SIO_GPIO_OE_SET, 1 << 2)
    mcu.write_uint32(SIO_GPIO_OUT_SET, 1 << 2)
    mcu.read_uint32(GPIO_STATUS(2))  # output level shows in STATUS
    clock.tick(1_000)
    mcu.write_uint32(SIO_GPIO_OUT_CLR, 1 << 2)
    mcu.read_uint32(GPIO_STATUS(2))
    mcu.write_uint32(PAD(2), mcu.read_uint32(PAD(2)) | 0x40)  # input enable
    mcu.write_uint32(GPIO_CTRL(3), FUNCSEL_SIO)
    mcu.write_uint32(PAD(3), mcu.read_uint32(PAD(3)) | 0x40)
    mcu.write_uint32(PROC0_INTE0, (EDGE_HIGH | EDGE_LOW) << 12)  # pin 3 is nibble 3 of INTE0
    clock.tick(2_000)
    mcu.gpio[3].set_input_value(True)  # an external device pulls pin 3 high
    mcu.read_uint32(GPIO_STATUS(3))
    mcu.read_uint32(PROC0_INTS0)
    mcu.read_uint32(INTR0)
    mcu.write_uint32(INTR0, EDGE_HIGH << 12)  # acknowledge
    mcu.read_uint32(PROC0_INTS0)
    clock.tick(500)
    mcu.gpio[3].set_input_value(False)
    mcu.read_uint32(PROC0_INTS0)
    mcu.gpio[3].release_input()
    mcu.write_uint32(PROC0_INTF0, 1 << 12)  # force a level interrupt
    mcu.read_uint32(PROC0_INTS0)
    return events


def test_the_session_exercises_every_kind_of_event():
    events = _session()
    kinds = {event[1] for event in events}
    assert {"io.r", "io.a", "pads.r", "pads.a", "sio.w", "q", "i"} <= kinds


def test_replaying_the_pins_against_themselves_is_exact():
    events = _session()
    assert any(e[1] == "i" and e[3] == 1 for e in events), "the edge should have raised the IO interrupt"
    assert replay_pins(events) == []


def test_replay_reports_a_wrong_status_read():
    events = _session()
    index = next(i for i, e in enumerate(events) if e[1] == "io.r" and e[2] == 8 * 2)
    nanos, kind, offset, value, extra = events[index]
    events[index] = (nanos, kind, offset, value ^ 0x100, extra)

    mismatches = replay_pins(events)

    assert len(mismatches) == 1
    assert mismatches[0].index == index


def test_replay_notices_a_missing_sio_driver_write():
    events = _session()
    without_driver = [e for e in events if not (e[1] == "sio.w" and e[2] == 0x14)]  # drop GPIO_OUT_SET

    assert replay_pins(without_driver) != []


def test_replay_notices_a_missing_external_drive():
    events = _session()
    without_drive = [e for e in events if e[1] != "q"]

    assert replay_pins(without_drive) != []


def test_replay_reports_a_missing_interrupt():
    events = _session()
    without_irq = [e for e in events if e[1] != "i"]

    mismatches = replay_pins(without_irq)

    assert mismatches
    assert mismatches[-1].what == "number of interrupt events"
