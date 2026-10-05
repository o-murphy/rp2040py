"""The native batch loop (C++ `run_batch`, docs/records/0096-cpp-mcu-core.md Phase 2 step 4c) against the pure-Python one.

Both loops run on *native* chips (the pure loop works on any chip - it only talks to the clock, the core and the PIO blocks through
their Python API), from identical machine states, until the program stops the simulator with a BKPT. What must match: the registers,
flags, cycle count and simulated time at the end; the simulated time at which every clock alarm fired (a callback logs it); and,
when something fails inside the loop - an alarm callback or a peripheral raising - the exception that comes out and the machine state
it leaves behind.
"""

import pytest
from utils.chip_pair import Native, make_chip

pytest.importorskip("rp2040py.native._simulator", reason="the native extension is not built")

from rp2040py._native_gate import native_disabled

# With RP2040PY_SKIP_CYTHON=1 a native chip is still constructible directly, but its PIO blocks come from the facade as the pure-Python
# class, which the native loop refuses (a combination nothing builds outside this kind of test).
pytestmark = pytest.mark.skipif(
    native_disabled(), reason="RP2040PY_SKIP_CYTHON=1: the facade gives the chip pure-Python PIO blocks"
)

from rp2040py._execute_batch import execute_batch as pure_execute_batch
from rp2040py.native._simulator import execute_batch as native_execute_batch
from rp2040py.simulator import Simulator
from rp2040py.utils.assembler import (
    opcode_adds2,
    opcode_cmp_imm,
    opcode_ldr_imm,
    opcode_movs,
    opcode_nop,
    opcode_wfi,
)

CODE = 0x20000000
BKPT = 0xBE00


class Boom(Exception):
    pass


def _rig(loop, program, *, alarms=(), idle=False, raise_in_alarm=None):
    """A native chip with `program` (16-bit opcodes) in SRAM and a Simulator around it; returns (simulator, log, run)."""
    chip = make_chip(Native)
    simulator = Simulator(rp2040=chip)
    for i, opcode in enumerate(program):
        chip.write_uint16(CODE + 2 * i, opcode)
    chip.core.pc = CODE
    chip.core.registers[13] = 0x20001000
    chip.core.waiting = idle
    log = []

    def on_alarm(name):
        def fire():
            log.append((name, chip.clock.nanos))
            if idle:
                chip.core.waiting = False  # a device interrupt waking the core
            if raise_in_alarm == name:
                raise Boom(name)

        return fire

    for name, delay in alarms:
        chip.clock.create_alarm(on_alarm(name)).schedule(delay)

    def run(tick_batch):
        simulator.stopped = False
        try:
            for _ in range(200):  # a batch also ends on the real-time budget: resume until the program has stopped it
                loop(simulator, tick_batch)
                if simulator.stopped:
                    break
            return "ok"
        except Boom as error:
            return ("raised", str(error))

    return simulator, log, run


def _state(simulator):
    core = simulator.rp2040.core
    return (
        [core.registers[i] for i in range(16)],
        (core.n, core.c, core.z, core.v),
        core.cycles,
        core.waiting,
        simulator.rp2040.clock.nanos,
        simulator.stopped,
    )


def _count_loop():
    """r0 = 0; do { r0 += 1; nop; cmp r0, #200 } while (r0 != 200); bkpt"""
    return [
        opcode_movs(0, 0),
        opcode_adds2(0, 1),
        opcode_nop(),
        opcode_cmp_imm(0, 200),
        0xD1FB,  # bne .-6: back to the `adds`
        BKPT,
    ]


@pytest.mark.parametrize("tick_batch", [1, 16])
@pytest.mark.parametrize("alarms", [(), (("a", 300.0),), (("a", 300.0), ("b", 300.0), ("c", 4000.5), ("d", 1e9))])
def test_a_busy_program_ends_in_the_same_state(tick_batch, alarms):
    states = []
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, log, run = _rig(loop, _count_loop(), alarms=alarms)
        assert run(tick_batch) == "ok"
        states.append((_state(simulator), log))

    assert states[0] == states[1]
    assert states[0][0][0][0] == 200  # the program really ran


@pytest.mark.parametrize("tick_batch", [1, 16])
def test_an_idle_core_jumps_to_each_alarm_and_resumes_in_the_same_state(tick_batch):
    program = [opcode_wfi(), opcode_movs(2, 7), BKPT]
    states = []
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, log, run = _rig(loop, program, alarms=(("wake", 12345.0),), idle=True)
        assert run(tick_batch) == "ok"
        states.append((_state(simulator), log))

    assert states[0] == states[1]
    assert states[0][1] == [("wake", 12345.0)]


@pytest.mark.parametrize("tick_batch", [1, 16])
def test_a_failing_alarm_callback_surfaces_and_leaves_the_same_machine_state(tick_batch):
    results = []
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, log, run = _rig(loop, _count_loop(), alarms=(("a", 100.0), ("late", 5000.0)), raise_in_alarm="a")
        outcome = run(tick_batch)
        results.append((outcome, _state(simulator), log))

    assert results[0] == results[1]
    assert results[0][0] == ("raised", "a")


@pytest.mark.parametrize("tick_batch", [1, 16])
def test_a_peripheral_that_fails_during_a_load_surfaces_from_the_batch(tick_batch):
    results = []
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, _log, run = _rig(loop, [opcode_ldr_imm(2, 1, 0), opcode_nop(), BKPT])
        chip = simulator.rp2040
        chip.core.registers[1] = 0x40054000  # the address of a window whose block raises

        class Angry:
            def read_uint32(self, offset):
                raise Boom("read")

            def write_uint32_atomic(self, offset, value, atomic_type):
                raise Boom("write")

        chip.peripherals[0x40054] = Angry()
        outcome = run(tick_batch)
        results.append((outcome, _state(simulator)))

    assert results[0] == results[1]
    assert results[0][0] == ("raised", "read")
    assert results[0][1][0][2] == 0  # the destination register of the failed load was not written


def test_the_native_loop_refuses_a_clock_it_would_bypass():
    from rp2040py.clock.mock_clock import MockClock

    class SlowClock(MockClock):
        def tick(self, delta_nanos):
            return super().tick(delta_nanos)

    chip = make_chip(Native)
    simulator = Simulator(rp2040=chip)
    simulator.clock = SlowClock()
    simulator.stopped = False

    with pytest.raises(TypeError, match="overrides tick"):
        native_execute_batch(simulator, 1)


@pytest.mark.parametrize("tick_batch", [1, 16])
def test_a_paced_idle_core_advances_at_most_a_millisecond_per_batch_in_both_loops(tick_batch):
    """While a device waits on the real world (`Simulator.real_io_begin()`) an idle jump is capped at 1 ms of simulated time and ends the batch - the same in
    the pure-Python loop and the C++ one - so `Simulator.execute()` can sleep for it; an unpaced idle core jumps straight to its alarm."""
    program = [opcode_wfi(), opcode_movs(2, 7), BKPT]
    states = []
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, log, _run = _rig(loop, program, alarms=(("wake", 50_000_000.0),), idle=True)
        simulator._real_io_flag[0] = 1
        simulator.stopped = False
        loop(simulator, tick_batch)  # one batch
        after_one = _state(simulator)
        assert after_one[4] == 1_000_000.0 and log == [], after_one  # 1 ms, and the alarm 50 ms away has not fired
        for _ in range(
            60
        ):  # 49 more capped jumps reach the alarm exactly, which wakes the core and lets the program stop
            loop(simulator, tick_batch)
            if simulator.stopped:
                break
        states.append((after_one, _state(simulator), log))

    assert states[0] == states[1]
    assert states[0][2] == [("wake", 50_000_000.0)]


def test_clearing_the_real_io_flag_restores_the_unpaced_jump_in_both_loops():
    program = [opcode_wfi(), BKPT]
    for loop in (pure_execute_batch, native_execute_batch):
        simulator, log, run = _rig(loop, program, alarms=(("wake", 50_000_000.0),), idle=True)
        simulator._real_io_flag[0] = 1
        simulator.stopped = False
        loop(simulator, 1)
        assert simulator.rp2040.clock.nanos == 1_000_000.0
        simulator._real_io_flag[0] = 0
        assert run(1) == "ok"
        assert log == [("wake", 50_000_000.0)]
