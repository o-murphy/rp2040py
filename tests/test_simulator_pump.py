"""`Simulator.pump()`: the thread-free way to drive the engine for a synchronous caller (docs/records/0096-cpp-mcu-core.md, the e-paper demo's GIL finding)."""

import asyncio
import threading

import pytest
from utils.emscripten import needs_blocking_loop, needs_threads

from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator


def _simulator_with_a_loop_of_its_own():
    simulator = Simulator(rp2040=RP2040())
    loop = asyncio.new_event_loop()
    simulator.bind_loop(loop)
    return simulator, loop


@needs_blocking_loop
def test_pump_advances_the_simulation_without_a_thread():
    simulator, loop = _simulator_with_a_loop_of_its_own()
    threads_before = threading.active_count()
    try:
        simulator.rp2040.core.pc = 0x20000000
        simulator.start_execution()
        t0 = simulator.clock.nanos
        simulator.pump(0.05)
        assert simulator.clock.nanos > t0  # time passed
        assert threading.active_count() == threads_before  # and nothing was started to make it
        assert simulator._loop_thread is None
        stopped_at = simulator.clock.nanos
        # paused between pumps: nothing runs while the caller does its own work
        assert simulator.clock.nanos == stopped_at
        simulator.pump(0.05)
        assert simulator.clock.nanos > stopped_at
    finally:
        simulator.stop()
        simulator.pump(0.01)
        loop.close()


def test_pump_needs_a_registered_loop():
    simulator = Simulator(rp2040=RP2040())
    with pytest.raises(RuntimeError, match="bind_loop"):
        simulator.pump(0.0)


def test_pump_refuses_a_running_loop():
    async def scenario():
        simulator = Simulator(rp2040=RP2040())
        simulator.bind_loop()
        with pytest.raises(RuntimeError, match="not running"):
            simulator.pump(0.0)

    asyncio.run(scenario())


# --- the blocking half of the API without a thread: `submit()`/`call()` on a loop nobody runs pump it -------------------------------------


async def _answer(value, delay=0.0):
    await asyncio.sleep(delay)
    return value


async def _fail():
    raise ValueError("boom")


@needs_blocking_loop
def test_submit_returns_a_future_whose_result_pumps_the_loop():
    simulator, loop = _simulator_with_a_loop_of_its_own()
    threads_before = threading.active_count()
    try:
        future = simulator.submit(_answer(42, 0.01))
        assert not future.done()  # nothing runs it until somebody pumps
        assert future.result(timeout=5) == 42
        assert threading.active_count() == threads_before
        assert simulator.submit(_fail()).exception(timeout=5).args == ("boom",)
        with pytest.raises(ValueError, match="boom"):
            simulator.submit(_fail()).result(timeout=5)
    finally:
        loop.close()


def test_a_submitted_future_times_out_like_a_concurrent_one():
    import concurrent.futures

    simulator, loop = _simulator_with_a_loop_of_its_own()
    try:
        future = simulator.submit(_answer(1, 10))
        with pytest.raises(concurrent.futures.TimeoutError):
            future.result(timeout=0.05)
        assert future.cancel()
    finally:
        loop.close()


@needs_blocking_loop
def test_call_runs_the_coroutine_to_completion_on_the_callers_thread():
    import concurrent.futures

    simulator, loop = _simulator_with_a_loop_of_its_own()
    try:
        assert simulator.call(_answer("x", 0.01), timeout=5) == "x"
        with pytest.raises(concurrent.futures.TimeoutError):
            simulator.call(_answer("never", 10), timeout=0.05)
    finally:
        loop.close()


@needs_blocking_loop
def test_a_threadless_simulator_makes_its_own_loop_without_a_thread():
    threads_before = threading.active_count()
    simulator = Simulator(rp2040=RP2040(), threadless=True)
    try:
        assert simulator.submit(_answer(7)).result(timeout=5) == 7
        assert simulator._loop_thread is None and threading.active_count() == threads_before
        simulator.rp2040.core.pc = 0x20000000
        simulator.start_execution()
        t0 = simulator.clock.nanos
        simulator.pump(0.05)
        assert simulator.clock.nanos > t0
    finally:
        simulator.stop()
        simulator.pump(0.01)
        simulator._loop.close()


def test_threadless_is_the_default_and_the_environment_or_the_argument_opt_into_the_thread(monkeypatch):
    monkeypatch.delenv("RP2040PY_THREADLESS", raising=False)
    assert Simulator(rp2040=RP2040())._threadless is True
    monkeypatch.setenv("RP2040PY_THREADLESS", "1")
    assert Simulator(rp2040=RP2040())._threadless is True
    monkeypatch.setenv("RP2040PY_THREADLESS", "0")
    assert Simulator(rp2040=RP2040())._threadless is False
    monkeypatch.delenv("RP2040PY_THREADLESS")
    assert Simulator(rp2040=RP2040(), threadless=False)._threadless is False


@needs_threads
def test_the_threaded_mode_is_still_there_when_asked_for():
    simulator = Simulator(rp2040=RP2040(), threadless=False)
    try:
        assert simulator._ensure_loop().is_running() or simulator._loop_thread is not None
        assert simulator.submit(_answer(5)).result(timeout=5) == 5  # a real engine-room thread serves it
    finally:
        simulator.stop()
        if simulator._loop is not None and simulator._loop_thread is not None:
            simulator._loop.call_soon_threadsafe(simulator._loop.stop)
            simulator._loop_thread.join(timeout=2.0)


@needs_threads
def test_two_threads_blocking_on_the_thread_free_loop_do_not_run_it_at_once():
    """`call()` from a second thread while the first is pumping the loop for its own `.result()` used to run `run_until_complete` twice at once - an AssertionError inside the
    proactor loop on Windows. One thread pumps at a time; the other waits for it and takes over."""
    import threading

    simulator = Simulator(threadless=True)
    loop = asyncio.new_event_loop()
    simulator.bind_loop(loop)
    errors: list[BaseException] = []

    async def _work(value: int) -> int:
        await asyncio.sleep(0.01)
        return value

    def _from_thread() -> None:
        try:
            for i in range(20):
                assert simulator.call(_work(i), timeout=5) == i
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            errors.append(exc)

    thread = threading.Thread(target=_from_thread)
    thread.start()
    try:
        for i in range(20):
            assert simulator.submit(_work(i)).result(timeout=5) == i
    finally:
        thread.join(10)
        loop.close()
    assert not errors and not thread.is_alive()
