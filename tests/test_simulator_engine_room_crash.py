"""Regression coverage for docs/tasks/cyw43-post-data-header-freeze.md's real root cause: an
uncaught exception from `_execute_batch()` used to just kill `execute()`'s task silently - nobody
ever called `.exception()` on it, so nothing ever surfaced, and every caller waiting on
device-produced state (an `asyncio.Event` only `execute()`'s own call chain could ever `.set()`)
hung forever instead of failing. `execute()` now records/re-raises via `engine_room_error`,
`Simulator.wait_for()` races a wait against that, and `wait_for_shutdown()` surfaces it too - see
each one's own docstring for the mechanism.
"""

import asyncio

import pytest

from rp2040py import simulator as simulator_module
from rp2040py.simulator import Simulator


def test_execute_crash_sets_engine_room_error_and_wait_for_surfaces_it(monkeypatch):
    def _boom(sim: Simulator, tick_batch: int) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(simulator_module, "execute_batch", _boom)

    async def scenario() -> None:
        sim = Simulator()
        sim.bind_loop()
        sim._execute_task = asyncio.ensure_future(sim.execute())
        event = asyncio.Event()  # never set by anything - only execute() ending unblocks this

        with pytest.raises(RuntimeError, match="engine room crashed") as exc_info:
            await sim.wait_for(event, timeout=5)
        assert str(exc_info.value.__cause__) == "boom"

        assert sim.stopped is True
        assert sim.engine_room_error is not None
        assert str(sim.engine_room_error) == "boom"

        with pytest.raises(RuntimeError, match="boom"):
            await sim._execute_task  # drain - avoid an "exception was never retrieved" warning

    asyncio.run(scenario())


def test_execute_deliberate_stop_unblocks_wait_for_without_engine_room_error(monkeypatch):
    def _stop_cleanly(sim: Simulator, tick_batch: int) -> None:
        sim.stop()

    monkeypatch.setattr(simulator_module, "execute_batch", _stop_cleanly)

    async def scenario() -> None:
        sim = Simulator()
        sim.bind_loop()
        sim._execute_task = asyncio.ensure_future(sim.execute())
        event = asyncio.Event()

        with pytest.raises(RuntimeError, match="simulator stopped while waiting"):
            await sim.wait_for(event, timeout=5)

        assert sim.engine_room_error is None
        await sim._execute_task  # ended cleanly - nothing to drain/suppress

    asyncio.run(scenario())


def test_wait_for_returns_normally_once_event_is_set(monkeypatch):
    calls = 0

    def _once_then_idle(sim: Simulator, tick_batch: int) -> None:
        nonlocal calls
        calls += 1
        if calls > 3:
            sim.stop()  # bounds the test - never actually reached if wait_for() works

    monkeypatch.setattr(simulator_module, "execute_batch", _once_then_idle)

    async def scenario() -> None:
        sim = Simulator()
        sim.bind_loop()
        sim._execute_task = asyncio.ensure_future(sim.execute())
        event = asyncio.Event()
        event.set()

        await sim.wait_for(event, timeout=5)  # must return normally, not raise

        sim.stop()
        await sim._execute_task

    asyncio.run(scenario())


def test_wait_for_shutdown_surfaces_engine_room_error_and_runs_cleanup():
    simulator = Simulator()  # freshly constructed, never executed - .executing is False already
    simulator.engine_room_error = RuntimeError("boom")
    cleanup_calls = []

    with pytest.raises(RuntimeError, match="boom"):
        simulator.wait_for_shutdown(cleanup=lambda: cleanup_calls.append(True))

    assert cleanup_calls == [True]
