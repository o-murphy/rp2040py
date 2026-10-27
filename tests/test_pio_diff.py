"""The PIO differential oracle (tests/utils/pio_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 3).

Three things, the same three as for every oracle of that record: (1) the reference (the pure-Python PIO) and the implementation the facade hands out agree on
long random runs - today that is the Cython PIO, later the C++ one, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step
the damage is done; (3) the runs really exercise what they claim to (every instruction kind, every JMP condition, all three WAIT sources, PUSH and PULL, DREQ, interrupt
and pin traffic) - a green differential that never executed a WAIT would prove nothing.
"""

import pytest
from utils.pio_diff import MUTANTS, SM_STRIDE, Rig, coverage, generate, mutant_rig, run_pair

from rp2040py.peripherals import pio_registers as R
from rp2040py.utils.pio_assembler import PIO_MOV_DEST_X, PIO_OP_INVERT, PIO_SRC_X, pio_mov

STEPS = 12000  # the coverage run (cheap: one chip, no snapshots)
DIFF_STEPS = 3500  # the lockstep runs snapshot everything after every step, which is what costs
SEEDS = range(1, 7)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_pio_and_the_default_pio_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _flip_x(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.write(
            0, R.SM0_INSTR + SM_STRIDE, pio_mov(PIO_MOV_DEST_X, PIO_SRC_X, PIO_OP_INVERT)
        )  # MOV X, ~X on machine 1


def _bump_pin_image(candidate: Rig, step: int) -> None:
    if step == 700:
        pio = candidate.chip.pio[1]
        pio.pin_values_changed(((pio.snapshot()["pin_values"] >> 5) & 1) ^ 1, 5, 1)


def _change_a_fifo(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.write(
            0, R.TXF0 + 8, 0xDEADBEEF
        )  # a word into machine 2's TX FIFO (or, with the FIFO full / joined away, TXOVER)


def _advance_an_extra_cycle(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pio[0].advance(1)


def _raise_a_flag(candidate: Rig, step: int) -> None:
    if step == 700:
        flags = candidate.chip.pio[1].snapshot()["irq"]
        candidate.write(1, R.IRQ if flags & 4 else R.IRQ_FORCE, 4)  # flip IRQ flag 2: write-1-to-clear, or force it


@pytest.mark.parametrize(
    "perturb",
    [_flip_x, _bump_pin_image, _change_a_fifo, _advance_an_extra_cycle, _raise_a_flag],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


MUTANT_SEEDS = range(1, 9)


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_pin_arbitration_logic_is_caught(mutant):
    """Every entry of ``MUTATIONS`` is the reference's own source with one expression of the pin-write arbitration changed (datasheet 3.5.6.1)."""
    for seed in MUTANT_SEEDS:
        if run_pair(generate(seed, DIFF_STEPS), candidate=lambda: mutant_rig(mutant)) is not None:
            return
    pytest.fail(f"{mutant}: {len(MUTANT_SEEDS)} seeds of {DIFF_STEPS} steps did not see it")


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4, 5, 6):
        for name, count in coverage(generate(seed, STEPS)).items():
            total[name] = total.get(name, 0) + count
    for kind in range(8):
        assert total.get(f"instr{kind}", 0) >= 150, (kind, total)
    for condition in range(8):
        assert total.get(f"jmp.cond{condition}", 0) >= 15, (condition, total)
    for source in range(3):  # GPIO, PIN, IRQ (3 is the reserved encoding, executed too but not asserted)
        assert total.get(f"wait.src{source}", 0) >= 30, (source, total)
    assert total.get("push", 0) >= 50 and total.get("pull", 0) >= 50, total
    assert total.get("dreq", 0) >= 500, total
    assert total.get("irq", 0) >= 1000, total
    assert total.get("pin", 0) >= 200, total
    assert total.get("log", 0) >= 50, total  # the warnings of unimplemented registers
    for wait in ("wait_pin", "wait_irq", "wait_fifo"):
        assert total.get(wait, 0) >= 50, (wait, total)
    for name in ("fjoin.tx", "fjoin.rx", "fjoin.both", "fjoin.tx_deep", "fjoin.rx_deep"):
        assert total.get(name, 0) >= 50, (name, total)
    for name in (
        "sticky.on",
        "sticky.held",
        "inline.enabled",
        "inline.gated",
        "arbitration.cycles",
        "arbitration.overwritten",
    ):  # the pin-write arbitration (datasheet 3.5.6.1)
        assert total.get(name, 0) >= 30, (name, total)
