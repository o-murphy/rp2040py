"""Self-test of tests/utils/mmio_trace.py (docs/records/0096-cpp-mcu-core.md, Phase 0): the
record -> replay -> diff harness that every later C++ block is judged by must first prove it can
tell a faithful replay from a broken one - here with the Python TIMER against itself."""

import pytest
from utils.mmio_trace import Mismatch, load, record, replay, save

from rp2040py.irq import IRQ
from rp2040py.rp2040 import RP2040

TIMER_BASE = 0x40054000
TIMER_KEY = 0x40054  # the bus looks blocks up by `address >> 12` with the low two bits clear
TIMELR, TIMEHR, ALARM0, INTR, INTE = 0x0C, 0x08, 0x10, 0x34, 0x38
SET_ALIAS, CLEAR_ALIAS = 0x2000, 0x3000
TIMER_IRQS = (IRQ.TIMER_0, IRQ.TIMER_1, IRQ.TIMER_2, IRQ.TIMER_3)


def _session() -> "list[tuple]":
    """A scripted session over the real bus: arm ALARM0, let it fire, acknowledge it, poll TIMELR."""
    mcu = RP2040()
    events = record(mcu, TIMER_KEY, TIMER_IRQS)
    clock = mcu.clock
    mcu.write_uint32(TIMER_BASE + INTE + SET_ALIAS, 1)  # an atomic-alias write
    clock.tick(5_000)
    now = mcu.read_uint32(TIMER_BASE + TIMELR)
    mcu.write_uint32(TIMER_BASE + ALARM0, (now + 100) & 0xFFFFFFFF)  # 100 us from now
    for _ in range(6):
        clock.tick(30_000)  # the alarm fires inside one of these
        mcu.read_uint32(TIMER_BASE + TIMELR)
        mcu.read_uint32(TIMER_BASE + TIMEHR)
    mcu.write_uint32(TIMER_BASE + INTR, 1)  # acknowledge
    mcu.write_uint32(TIMER_BASE + INTE + CLEAR_ALIAS, 1)
    clock.tick(1_000)
    mcu.read_uint32(TIMER_BASE + TIMELR)
    return events


def test_the_session_exercises_every_kind_of_event():
    events = _session()
    assert {"r", "a", "i"} <= {event[1] for event in events}
    # The bus sends *every* write of a peripheral through write_uint32_atomic(): a plain write is
    # atomic_type 0, an alias write 1/2/3. (No "w" events appear unless something calls the block's
    # write_uint32() directly - the contract a C++ block has to honour is the atomic one.)
    assert {e[4] for e in events if e[1] == "a"} >= {0, 2, 3}


def test_replaying_a_block_against_itself_is_exact():
    events = _session()
    assert any(e[1] == "i" and e[3] == 1 for e in events), "the alarm should have raised its interrupt"
    assert replay(events, TIMER_KEY, TIMER_IRQS) == []


def test_replay_reports_a_wrong_read_value():
    events = _session()
    index = next(i for i, e in enumerate(events) if e[1] == "r" and e[2] == TIMELR)
    nanos, kind, offset, value, extra = events[index]
    events[index] = (nanos, kind, offset, value ^ 0x40, extra)

    mismatches = replay(events, TIMER_KEY, TIMER_IRQS)

    assert len(mismatches) == 1
    assert mismatches[0].index == index
    assert (mismatches[0].expected, mismatches[0].got) == (value ^ 0x40, value)


def test_replay_reports_a_missing_interrupt():
    events = _session()
    without_irq = [e for e in events if e[1] != "i"]

    mismatches = replay(without_irq, TIMER_KEY, TIMER_IRQS)

    # The replay still raises the interrupt the (doctored) trace no longer lists.
    assert mismatches == [Mismatch(-1, "number of interrupt events", 0, mismatches[0].got)]
    assert mismatches[0].got >= 1


def test_replay_reports_an_interrupt_at_the_wrong_time():
    events = _session()
    index = next(i for i, e in enumerate(events) if e[1] == "i")
    nanos, *rest = events[index]
    events[index] = (nanos + 8, *rest)

    mismatches = replay(events, TIMER_KEY, TIMER_IRQS)

    assert [m.what for m in mismatches] == ["interrupt #0"]


def test_replay_reports_a_trace_that_runs_backwards():
    events = _session()
    events.append((0.0, "r", TIMELR, 0, 0))

    first = next(iter(replay(events, TIMER_KEY, TIMER_IRQS)))
    assert first.what.startswith("trace goes back in time")


def test_a_trace_survives_a_round_trip_through_a_file(tmp_path):
    events = _session()
    path = tmp_path / "timer.jsonl.gz"

    save(events, path)

    assert load(path) == events
    assert replay(load(path), TIMER_KEY, TIMER_IRQS) == []


def test_an_unknown_event_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown event kind"):
        replay([(0.0, "?", 0, 0, 0)], TIMER_KEY)
