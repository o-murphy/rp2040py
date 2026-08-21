"""RESET button / the RUN pin - docs/records/0089-one-reset-for-every-trigger.md's Phase 4, which
is docs/records/0057-run-pin-reset-hook.md in full.

Three layers, tested separately because they fail separately:

1. `RP2040.set_run_pin()`'s level/edge semantics - RUN is a level (a held button holds the chip in
   reset), and only the *release* is an edge that resets anything.
2. `execute_batch()`'s third execution state - held in reset executes nothing and advances no
   simulated time, unlike a WFI'd `core.waiting` core, which still services alarms.
3. `ResetButton` itself - a button pressed *while the simulation runs*, so every level change goes
   through `schedule_threadsafe()` rather than being applied on the caller's own thread (0030).

Runs under both builds (`RP2040PY_SKIP_CYTHON=1` and `=0`) like the rest of the suite, which is
what covers 0057's own parity requirement: the RUN level exists in both `_rp2040.py` and
`native/_rp2040.pyx`, and both `execute_batch()` twins have to honour it.
"""

import pytest

from rp2040py.external.reset_button import ResetButton
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.simulator import Simulator


class _RecordingSimulator:
    """Just enough `Simulator` for `RP2040.schedule_threadsafe()`: records instead of running, so a
    test can assert that a level change was *handed off* and then run it deliberately. A real
    Simulator would need a real engine-room loop (and a thread to run it on) to prove the same
    thing far less directly."""

    def __init__(self) -> None:
        self.scheduled: list = []

    def schedule_threadsafe(self, fn_or_coro) -> None:
        self.scheduled.append(fn_or_coro)

    def run_all(self) -> None:
        pending, self.scheduled = self.scheduled, []
        for fn in pending:
            fn()


def _attached(rp2040) -> ResetButton:
    rp2040.simulator = _RecordingSimulator()
    button = ResetButton()
    button.attach(rp2040)
    return button


# -- 1. the RUN level on RP2040 -----------------------------------------------------------------


def test_a_fresh_chip_has_run_high(rp2040_factory):
    assert rp2040_factory().run_pin_low is False


def test_holding_run_low_does_not_reset_anything_by_itself(rp2040_factory):
    """The falling edge holds the chip; it is the release that boots it. A hook fired on press
    would model a pulse - which is exactly what the schematic in 0057's addendum rules out."""
    rp2040 = rp2040_factory()
    fired = []
    rp2040.on_run_pin_reset = lambda: fired.append(1)

    rp2040.set_run_pin(low=True)

    assert rp2040.run_pin_low is True
    assert fired == []


def test_releasing_run_fires_the_reset_hook_exactly_once(rp2040_factory):
    rp2040 = rp2040_factory()
    fired = []
    rp2040.on_run_pin_reset = lambda: fired.append(1)

    rp2040.set_run_pin(low=True)
    rp2040.set_run_pin(low=False)

    assert rp2040.run_pin_low is False
    assert fired == [1]


def test_re_driving_the_same_level_is_not_an_edge(rp2040_factory):
    """A switch that is simply still held is not a second press, and a button released twice is not
    two resets."""
    rp2040 = rp2040_factory()
    fired = []
    rp2040.on_run_pin_reset = lambda: fired.append(1)

    rp2040.set_run_pin(low=True)
    rp2040.set_run_pin(low=True)
    rp2040.set_run_pin(low=False)
    rp2040.set_run_pin(low=False)

    assert fired == [1]


def test_pressing_run_with_no_hook_installed_resets_the_chip(rp2040_factory):
    """A bare `RP2040` outside a `BaseDevice` resets itself.

    It used to log "no reset handler provided" and keep running, because the USB half of a reset
    was only reachable from the device layer. `RP2040.enter_reset()`/`leave_reset()` own the whole
    sequence now (0057's option B), so declining would be the wrong default: a RESET button on a
    board file, or `rp2040py run`'s bare chip, would silently do nothing."""
    rp2040 = rp2040_factory()
    rp2040.core.pc = 0x10001234
    rp2040.pwm.write_uint32(0x00, 0x1F)  # any block that a reset must clear

    rp2040.set_run_pin(low=True)
    assert rp2040.pwm.read_uint32(0x00) == 0, "pressing RUN must reset the chip's blocks"

    rp2040.set_run_pin(low=False)
    assert rp2040.core.pc == FLASH_START_ADDRESS, "releasing RUN must boot it"


# -- 2. held in reset, in the batch loop --------------------------------------------------------


def _batch_with_a_recurring_alarm(simulator: Simulator, period_nanos: int = 1_000_000) -> int:
    """One batch of a WFI'd core with a rescheduling alarm, returning how many times it fired.
    Non-zero for a running chip (the idle-jump branch services alarms); zero for one held in
    reset, which is the whole distinction."""
    fire_count = 0

    def _on_alarm() -> None:
        nonlocal fire_count
        fire_count += 1
        alarm.schedule(period_nanos)

    alarm = simulator.clock.create_alarm(_on_alarm)
    alarm.schedule(period_nanos)
    simulator.rp2040.core.waiting = True
    simulator.stopped = False
    simulator._execute_batch()
    simulator.stop()
    return fire_count


def test_a_chip_held_in_reset_executes_nothing_and_advances_no_simulated_time(rp2040_factory):
    simulator = Simulator(rp2040=rp2040_factory())
    simulator.rp2040.set_run_pin(low=True)
    nanos_before = simulator.clock.nanos

    assert _batch_with_a_recurring_alarm(simulator) == 0
    assert simulator.clock.nanos == nanos_before


def test_the_same_batch_runs_normally_once_run_is_released(rp2040_factory):
    """The control for the test above - without it, a batch that did nothing for an unrelated
    reason would look identical."""
    simulator = Simulator(rp2040=rp2040_factory())
    simulator.rp2040.on_run_pin_reset = lambda: None  # no BaseDevice here to install a real one
    simulator.rp2040.set_run_pin(low=True)
    simulator.rp2040.set_run_pin(low=False)

    assert _batch_with_a_recurring_alarm(simulator) > 0
    assert simulator.clock.nanos > 0


# -- 3. the device -------------------------------------------------------------------------------


def test_using_the_button_before_attach_is_an_error():
    button = ResetButton()
    with pytest.raises(RuntimeError, match="not attached"):
        button.press()


def test_press_hands_the_level_change_to_the_engine_room_instead_of_applying_it(rp2040_factory):
    """0030's rule: a RESET button is pressed while the simulation is running, from whatever thread
    the caller is on, so it may not touch engine-room state directly. Asserted as "scheduled but
    not yet applied", which is the difference a direct call would erase."""
    rp2040 = rp2040_factory()
    button = _attached(rp2040)

    button.press()

    assert rp2040.run_pin_low is False, "applied inline instead of on the engine-room loop"
    assert len(rp2040.simulator.scheduled) == 1
    rp2040.simulator.run_all()
    assert rp2040.run_pin_low is True


def test_release_reaches_the_reset_hook_once_the_engine_room_runs_it(rp2040_factory):
    rp2040 = rp2040_factory()
    fired = []
    rp2040.on_run_pin_reset = lambda: fired.append(1)
    button = _attached(rp2040)

    button.press()
    button.release()
    assert fired == [], "nothing may happen before the engine room runs the handoff"

    rp2040.simulator.run_all()

    assert fired == [1]
    assert rp2040.run_pin_low is False


def test_is_pressed_tracks_the_hosts_view_immediately(rp2040_factory):
    """Deliberately not the chip's view - `run_pin_low` is that, and it lags by one engine-room
    turn. Same split as `BootselButton.is_pressed`."""
    rp2040 = rp2040_factory()
    button = _attached(rp2040)

    assert button.is_pressed is False
    button.press()
    assert button.is_pressed is True
    button.release()
    assert button.is_pressed is False


def test_click_leaves_the_chip_released_and_reset_once(rp2040_factory):
    rp2040 = rp2040_factory()
    fired = []
    rp2040.on_run_pin_reset = lambda: fired.append(1)
    button = _attached(rp2040)

    button.click()
    rp2040.simulator.run_all()

    assert button.is_pressed is False
    assert rp2040.run_pin_low is False
    assert fired == [1]
