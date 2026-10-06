"""The PPB differential oracle (tests/utils/ppb_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, the PPB design note).

The same three things as for every oracle of that record: (1) the reference (the pure-Python PPB) and the implementation the facade hands out agree on long random runs - today that
is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of the
reference's *logic* (every entry of ``MUTATIONS`` is the reference's own source with one expression changed); (3) the runs really exercise what they claim to (SysTick firing with and
without its interrupt enabled, COUNTFLAG read back, NVIC lines pending and enabled, priorities set, the core really taking exceptions, a reset with SysTick running) - a green
differential that never fired SysTick would prove nothing.
"""

import pytest
from utils.ppb_diff import MUTANTS, PPB_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 3000
DIFF_STEPS = 2000
SEEDS = range(1, 5)
MUTANT_SEEDS = range(1, 9)
MUTANT_STEPS = 1500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_ppb_and_the_default_ppb_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(PPB_BASE + 0xD08, 0x20000100)  # VTOR


def _change_the_reload(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(PPB_BASE + 0x014, 0x1234)  # SYST_RVR


def _advance_one_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(10)


def _flip_the_count_flag(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.ppb.systick_count_flag = not candidate.chip.ppb.systick_count_flag


def _change_the_pending_set(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.core.pending_interrupts ^= 0x4


def _change_an_enable_bit(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.core.enabled_interrupts ^= 0x8


def _change_the_systick_frequency(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.ppb.systick_timer.frequency = 3e6


def _change_a_priority_word(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.core.interrupt_priorities[1] ^= 0x10


@pytest.mark.parametrize(
    "perturb",
    [
        _write_a_register,
        _change_the_reload,
        _advance_one_clock,
        _flip_the_count_flag,
        _change_the_pending_set,
        _change_an_enable_bit,
        _change_the_systick_frequency,
        _change_a_priority_word,
    ],
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
        "tick",
        "tick.running",
        "systick.fired",
        "systick.pended",
        "csr.read_clears_flag",
        "service.taken",
        "reset.running",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    for name in (
        "alarm.scheduled",
        "nvic.pending",
        "nvic.enabled",
        "nvic.deliverable",
        "nvic.priorities_set",
        "scb.pending",
    ):
        assert total.get(name, 0) >= 100, (name, total)
    assert total.get("log.log", 0) >= 10, total
