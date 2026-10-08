from utils.tick import start_tick

from rp2040py.clock.mock_clock import MockClock

ALARM1 = 0x40054014
ALARM2 = 0x40054018
ALARM3 = 0x4005401C
ARMED = 0x40054020
INTR = 0x40054034
INTR_CLEAR = INTR | 0x3000
INTE = 0x40054038
INTF = 0x4005403C
INTS = 0x40054040


def test_alarm1_armed_on_write(rp2040_factory):
    rp2040 = rp2040_factory(MockClock())
    rp2040.write_uint32(ALARM1, 0x1000)
    assert rp2040.read_uint32(ARMED) == 0x2


def test_disarm_alarm2_via_armed_register(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.write_uint32(ALARM2, 0x1000)
    assert rp2040.read_uint32(ARMED) == 0x4
    rp2040.write_uint32(ARMED, 0xFF)
    assert rp2040.read_uint32(ARMED) == 0


def test_alarm3_fires_irq3(rp2040_factory):
    clock = MockClock()
    rp2040 = rp2040_factory(clock)
    start_tick(rp2040)  # the TIMER counts once pico-sdk's clocks_init has started the watchdog tick
    # Arm the alarm
    rp2040.write_uint32(ALARM3, 1000)
    assert rp2040.read_uint32(ARMED) == 0x8
    assert rp2040.read_uint32(INTR) == 0
    # Advance time so that the alarm will fire
    clock.advance(2000)
    assert rp2040.read_uint32(ARMED) == 0
    assert rp2040.read_uint32(INTR) == 0x8
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.core.pending_interrupts == 0
    # Enable the interrupts for all alarms
    rp2040.write_uint32(INTE, 0xFF)
    assert rp2040.read_uint32(INTS) == 0x8
    assert rp2040.core.pending_interrupts == 0x8
    assert rp2040.core.interrupts_updated is True
    # Clear the alarm's interrupt
    rp2040.write_uint32(INTR_CLEAR, 0x8)
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.core.pending_interrupts == 0


def test_intf_forces_interrupt_even_when_inte_is_zero(rp2040_factory):
    clock = MockClock()
    rp2040 = rp2040_factory(clock)
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.read_uint32(INTE) == 0
    rp2040.write_uint32(INTF, 0x4)
    # The corresponding interrupt bit should be 1
    assert rp2040.read_uint32(INTS) == 0x4


ALARM0 = 0x40054010


def test_alarm_registers_hold_32_bits(rp2040_factory):
    """ALARMn is a 32-bit register: a write wider than that (or negative) reads back masked. The stored value used to be the
    raw Python int, so the bus's 32-bit read-back of it raised OverflowError."""
    rp2040 = rp2040_factory(MockClock())

    rp2040.write_uint32(ALARM0, -1)
    assert rp2040.read_uint32(ALARM0) == 0xFFFFFFFF

    rp2040.write_uint32(ALARM0, 0x1_0000_0005)
    assert rp2040.read_uint32(ALARM0) == 5

    rp2040.write_uint32(ALARM0, 0x1234)
    assert rp2040.read_uint32(ALARM0) == 0x1234


TIMEHW = 0x40054000
TIMELW = 0x40054004
TIMERAWH = 0x40054024
TIMERAWL = 0x40054028
DBGPAUSE = 0x4005402C
PAUSE = 0x40054030


def test_pause_freezes_the_count_and_holds_back_the_alarms(rp2040_factory):
    """RP2040 datasheet, TIMER PAUSE: "Set high to pause the timer". The count stops, no alarm comes due, and clearing it resumes from the frozen count."""
    clock = MockClock()
    rp2040 = rp2040_factory(clock)
    start_tick(rp2040)  # the TIMER counts once pico-sdk's clocks_init has started the watchdog tick
    rp2040.write_uint32(ALARM3, 500)
    clock.advance(100)  # 100 us
    rp2040.write_uint32(PAUSE, 1)
    assert rp2040.read_uint32(PAUSE) == 1
    clock.advance(5_000)  # 5 ms: nothing moves
    assert rp2040.read_uint32(TIMERAWL) == 100
    assert rp2040.read_uint32(INTR) == 0 and rp2040.read_uint32(ARMED) == 0x8
    rp2040.write_uint32(PAUSE, 0)
    clock.advance(
        399
    )  # 399 us after the resume: one microsecond short of the alarm (500 - 100 = 400 us of count to go)
    assert rp2040.read_uint32(INTR) == 0
    clock.advance(1)
    assert rp2040.read_uint32(INTR) == 0x8


def test_timelw_is_a_latch_and_timehw_sets_the_time(rp2040_factory):
    """RP2040 datasheet, TIMER TIMEHW/TIMELW: "always write timelw before timehw"; the writes "do not get copied to time until timehw is written"."""
    clock = MockClock()
    rp2040 = rp2040_factory(clock)
    start_tick(rp2040)  # the TIMER counts once pico-sdk's clocks_init has started the watchdog tick
    rp2040.write_uint32(TIMELW, 0x80000000)
    assert rp2040.read_uint32(TIMERAWL) == 0
    rp2040.write_uint32(TIMEHW, 7)
    assert rp2040.read_uint32(TIMERAWL) == 0x80000000 and rp2040.read_uint32(TIMERAWH) == 7
    clock.advance(10)
    assert rp2040.read_uint32(TIMERAWL) == 0x80000000 + 10


def test_an_armed_alarm_follows_the_time_when_it_is_set(rp2040_factory):
    clock = MockClock()
    rp2040 = rp2040_factory(clock)
    start_tick(rp2040)  # the TIMER counts once pico-sdk's clocks_init has started the watchdog tick
    rp2040.write_uint32(ALARM1, 1000)
    rp2040.write_uint32(TIMELW, 900)
    rp2040.write_uint32(TIMEHW, 0)
    clock.advance(99)
    assert rp2040.read_uint32(INTR) == 0
    clock.advance(1)
    assert rp2040.read_uint32(INTR) == 0x2


def test_dbgpause_is_a_two_bit_register_that_resets_to_both_bits_set(rp2040_factory):
    rp2040 = rp2040_factory(MockClock())
    assert rp2040.read_uint32(DBGPAUSE) == 0x6
    rp2040.write_uint32(DBGPAUSE, 0xFFFFFFFF)
    assert rp2040.read_uint32(DBGPAUSE) == 0x6
    rp2040.write_uint32(DBGPAUSE, 0x2)
    assert rp2040.read_uint32(DBGPAUSE) == 0x2
