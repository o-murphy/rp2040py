"""`Simulator.pump()`: the thread-free way to drive the engine for a synchronous caller (docs/records/0096-cpp-mcu-core.md, the e-paper demo's GIL finding)."""

import asyncio
import threading

import pytest

from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator


def _simulator_with_a_loop_of_its_own():
    simulator = Simulator(rp2040=RP2040())
    loop = asyncio.new_event_loop()
    simulator.bind_loop(loop)
    return simulator, loop


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
