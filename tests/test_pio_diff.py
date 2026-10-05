"""The PIO differential oracle (tests/utils/pio_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 3).

Three things, the same three as for every oracle of that record: (1) the reference (the pure-Python PIO) and the implementation the facade hands out agree on
long random runs - today that is the Cython PIO, later the C++ one, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step
the damage is done; (3) the runs really exercise what they claim to (every instruction kind, every JMP condition, all three WAIT sources, PUSH and PULL, DREQ, interrupt
and pin traffic) - a green differential that never executed a WAIT would prove nothing.
"""

import pytest
from utils.pio_diff import Rig, coverage, generate, run_pair

STEPS = 5000  # the coverage run (cheap: one chip, no snapshots)
DIFF_STEPS = 3500  # the lockstep runs snapshot everything after every step, which is what costs
SEEDS = range(1, 7)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_pio_and_the_default_pio_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _flip_x(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pio[0].machines[1].x ^= 1


def _bump_pin_image(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pio[1].pin_values ^= 1 << 5


def _change_a_fifo(candidate: Rig, step: int) -> None:
    if step == 700:
        fifo = candidate.chip.pio[0].machines[2].tx_fifo
        if fifo.full:  # a push into a full FIFO would change nothing
            fifo.pull()
        else:
            fifo.push(0xDEADBEEF)


def _advance_an_extra_cycle(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pio[0].advance(1)


def _raise_a_flag(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pio[1].irq ^= 1 << 2


@pytest.mark.parametrize(
    "perturb",
    [_flip_x, _bump_pin_image, _change_a_fifo, _advance_an_extra_cycle, _raise_a_flag],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4):
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
