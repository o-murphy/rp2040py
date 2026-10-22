"""Deferred time (`defer_ticks`) against ticking after every step, on the pure-Python clock and on the native one (the Python twin of the check in `tests/cpp/test_clock.cpp`).

The batch loop of a CPU running alone defers the time and the clock settles it when somebody looks or an alarm is due. The contract is that this is invisible: the same operations on an eager
clock (`tick_ticks` after every step) and on a lazy one (`defer_ticks`) must give the same alarms, fired in the same order each at the same tick, the same time at every read, and the same
exception at the same step (a callback that raises stops the clock at its own time on both) - including callbacks that read the time, re-arm themselves and cancel or arm others while the clock
is settling.
"""

import pytest

from rp2040py.clock._simulation_clock import SimulationClock as PureClock

try:
    from rp2040py.native._simulation_clock import SimulationClock as NativeClock
except ImportError:  # the extension is not built
    NativeClock = None

CLOCKS = [pytest.param(PureClock, id="pure")]
if NativeClock is not None:
    CLOCKS.append(pytest.param(NativeClock, id="native"))

ALARMS = 6


class Boom(Exception):
    pass


class Sim:
    def __init__(self, clock_cls, fail_at):
        self.clock = clock_cls()
        self.fail_at = fail_at  # the n-th firing of alarm 3 raises (None: never)
        self.log = []
        self.fired = [0] * ALARMS
        self.alarms = [self.clock.create_alarm(self._callback(i)) for i in range(ALARMS)]

    def _callback(self, i):
        def fire():
            self.fired[i] += 1
            self.log.append((i, self.clock.ticks))  # reads the time from inside a callback, while the clock is settling
            h = ((i + 1) * 2654435761 + self.fired[i] * 40503) & 0xFFFFFFFF
            if (h >> 4) % 3 == 0:
                self.alarms[i].schedule_ticks(1 + (h >> 8) % 3000)
            if (h >> 6) % 7 == 0:
                self.alarms[(h >> 10) % ALARMS].cancel()
            if (h >> 9) % 11 == 0:
                self.alarms[(h >> 13) % ALARMS].schedule_ticks(1 + (h >> 16) % 2000)
            if self.fail_at is not None and i == 3 and self.fired[i] == self.fail_at:
                raise Boom(i)

        return fire


def _step(call, *args):
    try:
        call(*args)
    except Boom as boom:
        return boom.args
    return None


@pytest.mark.parametrize("clock_cls", CLOCKS)
def test_deferred_time_is_invisible(clock_cls):
    import random

    for seed in range(1, 121):
        rng = random.Random(seed)
        fail_at = 2 + seed % 9 if seed % 5 == 0 else None
        eager, lazy = Sim(clock_cls, fail_at), Sim(clock_cls, fail_at)
        for _ in range(2500):
            op = rng.randrange(10)
            if op <= 5:  # let some time pass: an instruction is 128 ticks at 125 MHz; sometimes a longer jump
                delta = 1 + rng.randrange(20000 if op == 5 else 400)
                a = _step(eager.clock.tick_ticks, delta)
                b = _step(lazy.clock.defer_ticks, delta)
                assert a == b, f"seed {seed}"
                # what the callbacks did is already visible to the rest of the chip (an interrupt raised, a flag set), without anyone asking the clock: an alarm that came due at the end
                # of this step has fired by the time the step returns, as it has on the eager clock
                assert len(eager.log) == len(lazy.log), (
                    f"seed {seed}: an alarm due at the end of the step has not fired"
                )
                if a is not None:
                    break
            elif (
                op == 6
            ):  # look at the time; each reader first, in turn (a reader that did not settle would otherwise be settled by the one before it)
                if rng.randrange(2):
                    assert eager.clock.nanos == lazy.clock.nanos
                    assert eager.clock.ticks == lazy.clock.ticks
                else:
                    assert eager.clock.ticks == lazy.clock.ticks
                    assert eager.clock.nanos == lazy.clock.nanos
            elif op == 7:  # arm an alarm
                i, delta = rng.randrange(ALARMS), 1 + rng.randrange(5000)
                eager.alarms[i].schedule_ticks(delta)
                lazy.alarms[i].schedule_ticks(delta)
            elif op == 8:  # cancel one
                i = rng.randrange(ALARMS)
                eager.alarms[i].cancel()
                lazy.alarms[i].cancel()
            else:  # ask about the next alarm
                assert eager.clock.has_scheduled_alarm == lazy.clock.has_scheduled_alarm
                assert eager.clock.ticks_to_next_alarm == lazy.clock.ticks_to_next_alarm
        assert eager.clock.ticks == lazy.clock.ticks, f"seed {seed}"
        assert eager.log == lazy.log, f"seed {seed}"
