"""`SM_CLKDIV` and `[delay]` pacing - docs/records/0063.

Before this, `RPPIO` parsed both and paced itself by neither: every enabled state machine advanced
exactly one instruction per CPU instruction, whatever divider it had configured and whatever delays
its program carried. These tests hold the two halves of the fix to arithmetic that can be worked out
by hand, one system clock at a time, and pin down the ceiling that makes it safe
(docs/records/0043): a machine may never execute more than one instruction per `advance()` call,
however far behind its own due time it has fallen.

Driven through `RPPIO.advance()` directly rather than through a CPU program: `advance()` *is* the
unit under test, and a real `Simulator` (rather than `tests/test_pio.py`'s no-owning-`Simulator`
fixture) is what stops the `CTRL` write from running its own synchronous 1000-cycle batch first.
"""

import pytest

from rp2040py.simulator import Simulator
from rp2040py.utils.pio_assembler import (
    PIO_COND_ALWAYS,
    PIO_COND_XDEC,
    PIO_MOV_DEST_X,
    PIO_OP_INVERT,
    PIO_SRC_NULL,
    pio_jmp,
    pio_mov,
    pio_pull,
)

CTRL = 0x50200000
INSTR_MEM0 = 0x50200048
SM0_CLKDIV = 0x502000C8
SM0_INSTR = 0x502000D8

CLKDIV_RESTART_SM0 = 1 << 8


def _counting_pio(*, clkdiv_int: int = 1, clkdiv_frac: int = 0, delay: int = 0):
    """A PIO0 SM0 running a single `jmp x--, 0 [delay]` - one instruction, always taken, that
    decrements X every time it executes. X starts at 0xFFFFFFFF, so `executed()` below is an exact
    count of instructions retired, with nothing else in the program to confuse it.

    Returns `(simulator, pio, executed)`; the machine is enabled and due immediately.
    """
    simulator = Simulator()
    rp2040 = simulator.rp2040
    pio = rp2040.pio[0]

    rp2040.write_uint32(INSTR_MEM0, pio_jmp(0, PIO_COND_XDEC, delay))
    rp2040.write_uint32(SM0_CLKDIV, (clkdiv_int << 16) | (clkdiv_frac << 8))
    # X = ~0 via a directly-executed MOV (SM_INSTR), before the machine is enabled - so it costs
    # no scheduled cycles of its own.
    rp2040.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_X, PIO_SRC_NULL, PIO_OP_INVERT))
    rp2040.write_uint32(CTRL, 1)

    machine = pio.machines[0]

    def executed() -> int:
        return 0xFFFFFFFF - machine.x

    return simulator, pio, executed


def _advance_single_cycles(pio, count: int) -> None:
    """`count` system clocks, one at a time - the shape `_execute_batch()` produces for a run of
    single-cycle CPU instructions."""
    for _ in range(count):
        pio.advance(1)


def test_clkdiv_1_still_runs_one_instruction_per_cycle():
    """The reset divider has to keep behaving exactly as everything did before docs/records/0063:
    one instruction per system clock, no arithmetic of its own visible anywhere."""
    _simulator, pio, executed = _counting_pio()

    _advance_single_cycles(pio, 1000)

    assert executed() == 1000


@pytest.mark.parametrize(
    ("clkdiv_int", "expected"),
    [
        (2, 1 + 500),  # CYW43's own divider (docs/records/0063's addendum measured it live)
        (4, 1 + 250),
        (10, 1 + 100),
    ],
)
def test_integer_clkdiv_divides_the_instruction_rate(clkdiv_int, expected):
    """The `1 +` in each expectation is the instruction due the instant the machine is enabled;
    the rest are one per divider period."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=clkdiv_int)

    _advance_single_cycles(pio, 1000)

    assert executed() == expected


def test_fractional_clkdiv_is_honoured_exactly():
    """CLKDIV is 16.8 fixed point: INT=2/FRAC=128 is /2.5, so 1000 system clocks are 400
    instructions - not the 500 an int-only implementation would give."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=2, clkdiv_frac=128)

    _advance_single_cycles(pio, 1000)

    assert executed() == 1 + 400


def test_clkdiv_int_zero_means_divide_by_65536_not_by_zero():
    """RP2040 datasheet 3.5.5. Only the very first instruction (due the moment the machine is
    enabled) fits into 1000 system clocks at /65536."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=0)

    _advance_single_cycles(pio, 1000)

    assert executed() == 1


@pytest.mark.parametrize(("delay", "expected"), [(0, 1000), (1, 1 + 500), (3, 1 + 250), (9, 1 + 100)])
def test_delay_cycles_are_honoured(delay, expected):
    """`[delay]` costs delay *PIO* cycles on top of the instruction's own one - the half of
    docs/records/0063 that carries a WS2812 waveform's bit widths."""
    _simulator, pio, executed = _counting_pio(delay=delay)

    _advance_single_cycles(pio, 1000)

    assert executed() == expected


def test_clkdiv_and_delay_compose():
    """CircuitPython's own `neopixel_write` program shape: a divider *and* delays. 12 system clocks
    per instruction (4 PIO cycles at /3) - 1000 of them is 84 instructions (the first is due
    immediately, then one every 12)."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=3, delay=3)

    _advance_single_cycles(pio, 1000)

    assert executed() == 1 + 1000 // 12


def test_at_most_one_instruction_per_advance_call():
    """The ceiling docs/records/0043 depends on: `clock.tick()` only runs between `advance()`
    calls, so a machine that took several steps inside one could drain a DMA-fed TX FIFO the DMA
    channel has had no chance to refill. Ten system clocks handed over at once must still be one
    instruction, not ten - letting them all run turns MicroPython v1.23.0's `nic.scan()` into
    `OSError: EPERM` (measured, see docs/records/0063)."""
    _simulator, pio, executed = _counting_pio()

    pio.advance(10)

    assert executed() == 1


def test_a_backlog_too_big_to_walk_through_is_written_off():
    """A CPU idle jump goes straight to the next scheduled alarm, which can be a millisecond -
    125,000 system clocks - away. One instruction per `advance()` can never pay that off, so the
    machine would otherwise run flat out for the next 125,000 CPU instructions working through a
    debt it accrued while the CPU was asleep. It is re-armed from the current cycle instead, and
    keeps running at the rate it asked for."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=10)

    pio.advance(125_000)  # the idle jump
    assert executed() == 1  # one instruction, not the 12,500 hardware would have run
    assert pio.backlog_drops == 1  # ...and the other 12,499 written off, once

    _advance_single_cycles(pio, 1000)

    assert pio.backlog_drops == 1  # no further write-offs - the machine is back on schedule
    assert executed() == 1 + 100  # ...and /10 paced from there, not catching up flat out


def test_ordinary_running_never_writes_off_a_backlog():
    """The write-off above must not fire for the ordinary case of a multi-cycle CPU instruction
    overshooting a due time by a cycle or two - that would quantize every state machine to the
    CPU's own instruction boundaries and lose the divider's precision."""
    _simulator, pio, executed = _counting_pio(clkdiv_int=4)

    for _ in range(300):
        pio.advance(3)  # a 3-cycle CPU instruction, repeatedly - never lands exactly on /4

    assert pio.backlog_drops == 0
    # Due at 0, 4, 8, ... 900 - each served by the first multiple of 3 at or after it.
    assert executed() == 1 + 900 // 4


def test_clkdiv_restart_rearms_the_divider_phase():
    """`CTRL.CLKDIV_RESTART` was a "not implemented" warning for as long as nothing was paced by
    the divider at all. It restarts the count now: the machine's next instruction is one full
    divider period from the restart, not from wherever the accumulator had got to."""
    simulator, pio, executed = _counting_pio(clkdiv_int=10)

    _advance_single_cycles(pio, 25)
    assert executed() == 3  # due at cycles 0, 10, 20

    simulator.rp2040.write_uint32(CTRL, 1 | CLKDIV_RESTART_SM0)
    _advance_single_cycles(pio, 9)
    assert executed() == 3  # ...restarted at 25, so nothing is due before 35

    _advance_single_cycles(pio, 1)
    assert executed() == 4


def test_a_disabled_machine_is_rearmed_when_it_is_enabled_again():
    """Re-enabling must not hand the machine a due time from before it was disabled - that is a
    backlog it never earned, and it would run flat out until it had paid it off."""
    simulator, pio, executed = _counting_pio(clkdiv_int=10)
    rp2040 = simulator.rp2040

    _advance_single_cycles(pio, 5)
    assert executed() == 1

    rp2040.write_uint32(CTRL, 0)  # disabled
    _advance_single_cycles(pio, 1000)
    assert executed() == 1  # nothing runs while disabled

    rp2040.write_uint32(CTRL, 1)  # enabled again - due immediately, then every 10th cycle
    _advance_single_cycles(pio, 20)

    assert executed() == 1 + 3
    assert pio.backlog_drops == 0


def test_a_stalled_machine_costs_nothing_until_something_unstalls_it():
    """`next_due_fp` excludes a stalled machine entirely (there is no answer to "when is it next
    due" while it is waiting), so a block whose only machine is stalled short-circuits on a single
    integer compare - the property that let `_execute_batch()`'s `_has_runnable_machine()` scan go
    away. `check_wait()` lowers the block's due time again the instant the stall ends."""
    simulator, pio, _executed = _counting_pio()
    rp2040 = simulator.rp2040
    machine = pio.machines[0]

    # A blocking pull with an empty TX FIFO: the machine stalls on it immediately.
    # (`pio_pull()`'s second argument lands in the ISA's own BLOCK bit, so True is "block" here -
    # the parameter's name reads the other way round, see utils/pio_assembler.py.)
    rp2040.write_uint32(INSTR_MEM0, pio_jmp(0, PIO_COND_ALWAYS))
    rp2040.write_uint32(SM0_INSTR, pio_pull(False, True))
    assert machine.waiting

    pio.advance(1000)
    assert pio.next_due_fp > pio.cycle_fp  # nothing is due: the only machine is stalled

    rp2040.write_uint32(0x50200010, 0x1234)  # TXF0 - the stall's condition, from the CPU side

    assert not machine.waiting
    assert pio.next_due_fp <= pio.cycle_fp + (1 << 8)  # ...and it is due again immediately
