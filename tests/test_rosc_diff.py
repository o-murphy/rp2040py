"""The ROSC differential oracle (tests/utils/rosc_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md).

The same three things as for every oracle of that record: (1) the reference (the pure-Python ROSC) and the implementation the facade hands out agree on long random runs; (2) a deliberately
damaged candidate is caught at the step the damage is done, and so is a change of the reference's *logic* (every entry of ``MUTATIONS`` is the reference's own source with one expression
changed); (3) the runs really exercise what they claim to (valid and invalid writes of every register, the counter running down, a retuned frequency, a disabled oscillator, resets).
"""

import pytest
from utils.rosc_diff import MUTANTS, ROSC_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 3000
DIFF_STEPS = 2000
SEEDS = range(1, 5)
MUTANT_SEEDS = range(1, 13)
MUTANT_STEPS = 1500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_rosc_and_the_default_rosc_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(ROSC_BASE + 0x10, 0xAA0 + 5)  # DIV


def _change_the_count(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(ROSC_BASE + 0x20, 77)  # COUNT


def _flip_the_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        enabled = candidate.chip.rosc.snapshot()["enabled"]
        candidate.chip.write_uint32(
            ROSC_BASE + 0x00, (0xD1E if enabled else 0xFAB) << 12 | 0xAA0
        )  # CTRL: ENABLE <-> DISABLE


def _set_badwrite(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(ROSC_BASE + 0x04, 0x1234)  # FREQA without its password: BADWRITE


def _retune_the_frequency(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(ROSC_BASE + 0x00, (0xFAB << 12) | 0xFA7)  # CTRL: FREQ_RANGE = HIGH


def _advance_one_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(10)


@pytest.mark.parametrize(
    "perturb",
    [_write_a_register, _change_the_count, _flip_the_enable, _set_badwrite, _retune_the_frequency, _advance_one_clock],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    for seed in MUTANT_SEEDS:
        if run_pair(generate(seed, MUTANT_STEPS), candidate=lambda: mutant_rig(mutant)) is not None:
            return
    pytest.fail(f"{mutant}: {len(MUTANT_SEEDS)} seeds of {MUTANT_STEPS} steps did not see it")


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3):
        for name, count in coverage(generate(seed, STEPS)).items():
            total[name] = total.get(name, 0) + count
    for name in (
        "ctrl.write",
        "ctrl.disable",
        "div.valid",
        "count.start",
        "count.counting",
        "count.reached_zero",
        "state.disabled",
        "state.badwrite",
        "state.retuned",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    for name in ("log.log", "log.bit", "log.clocks"):
        assert total.get(name, 0) >= 20, (name, total)
