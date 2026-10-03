from rp2040py.clock.mock_clock import MockClock


def test_advance_moves_the_clock_by_exactly_the_requested_microseconds():
    clock = MockClock()

    clock.advance(1)
    assert clock.nanos == 1_000

    clock.advance(1)  # used to add the whole current reading again: 3_000
    assert clock.nanos == 2_000

    clock.advance(2.5)
    assert clock.nanos == 4_500


def test_advance_fires_an_alarm_due_inside_the_step_at_its_own_time():
    clock = MockClock()
    fired = []
    alarm = clock.create_alarm(lambda: fired.append(clock.nanos))
    alarm.schedule(1_500)

    clock.advance(1)
    assert fired == []

    clock.advance(1)
    assert fired == [1_500]
    assert clock.nanos == 2_000
