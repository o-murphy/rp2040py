import pytest

from rp2040py.clock.mock_clock import MockClock
from rp2040py.utils.timer32 import Timer32, TimerMode

BASE_FREQ = 1_000_000  # 1 MHz: one tick per microsecond


def _us(clock, microseconds):
    clock.tick(microseconds * 1000)  # tick() takes a delta in nanoseconds


@pytest.fixture
def clock():
    return MockClock()


def _timer(clock, mode=TimerMode.INCREMENT, top=0xFFFFFFFF):
    timer = Timer32(clock, BASE_FREQ)
    timer.mode = mode
    timer.top = top
    return timer


def test_an_incrementing_counter_wraps_at_top_plus_one(clock):
    timer = _timer(clock, top=9)

    _us(clock, 13)

    assert timer.counter == 3


def test_a_zigzag_counter_goes_up_to_top_and_back_down(clock):
    timer = _timer(clock, TimerMode.ZIGZAG, top=4)

    seen = []
    for _ in range(10):
        seen.append(timer.counter)
        _us(clock, 1)

    assert seen == [0, 1, 2, 3, 4, 3, 2, 1, 0, 1]


@pytest.mark.parametrize("mode", [TimerMode.INCREMENT, TimerMode.DECREMENT, TimerMode.ZIGZAG])
def test_a_counter_with_top_zero_never_divides_by_zero(clock, mode):
    """TOP == 0 in ZIGZAG makes the counter's period 2*TOP == 0. That used to raise ZeroDivisionError (found by
    writing random values into the PWM registers); the counter has one state, so it reads 0."""
    timer = _timer(clock, mode, top=0)

    for _ in range(5):
        assert timer.counter == 0
        assert timer.raw_counter == 0
        _us(clock, 7)


def test_setting_top_to_zero_on_a_running_zigzag_counter_is_safe(clock):
    timer = _timer(clock, TimerMode.ZIGZAG, top=50)
    _us(clock, 20)

    timer.top = 0

    assert timer.counter == 0


def test_advance_keeps_the_counter_in_range_and_wraps_past_zero_to_top(clock):
    timer = _timer(clock, top=9)

    timer.advance(-1)

    assert timer.counter == 9
    timer.advance(25)
    assert timer.counter == 4


def test_advance_wraps_a_zigzag_counter_over_its_whole_period(clock):
    timer = _timer(clock, TimerMode.ZIGZAG, top=4)

    timer.advance(-1)  # one before 0 is the last point of the period: 7 of 0..7, which reads as 1 on the way down

    assert (timer.raw_counter, timer.counter) == (7, 1)


def test_advance_on_a_zigzag_counter_with_top_zero_has_no_period_to_wrap():
    timer = _timer(MockClock(), TimerMode.ZIGZAG, top=0)

    timer.advance(3)

    assert timer.counter == 0


def test_advance_tells_the_alarms():
    from rp2040py.clock._simulation_clock import SimulationClock
    from rp2040py.utils.timer32 import Timer32PeriodicAlarm

    clock = SimulationClock()
    timer = _timer(clock, top=9)
    alarm = Timer32PeriodicAlarm(timer, lambda: None)
    alarm.target = 5
    alarm.enable = True
    _us(clock, 2)
    assert clock.nanos_to_next_alarm == 3000

    timer.advance(1)

    assert clock.nanos_to_next_alarm == 2000
