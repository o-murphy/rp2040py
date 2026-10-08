"""The RTC differential oracle (tests/utils/rtc_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, "The small blocks").

(1) the reference (the pure-Python block) and the implementation the facade hands out agree on long random runs - the C++ block in a native build; (2) a deliberately damaged candidate is caught
at the step the damage is done, and so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (every carry, the leap day, the alarm, the interrupt line failing).
"""

import pytest
from utils.rtc_diff import MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

SEEDS = range(1, 11)
DIFF_STEPS = 3000


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_block_and_the_default_block_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _warn_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.peripheral.warn("only here")


def _write_with_an_alias(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(0x4005C000 + 0x2000 + 0x04, 0xA5)  # SET alias of SETUP_0: raw_write_value moves


def _change_the_counter(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.peripheral.minute ^= 0x10


def _move_the_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(1)


@pytest.mark.parametrize(
    "perturb",
    [_warn_on_one_side, _write_with_an_alias, _change_the_counter, _move_the_clock],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step in (700, 701), divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    caught = [run_pair(generate(seed, 3000), candidate=lambda: mutant_rig(mutant)) for seed in (1, 2, 3, 4)]
    assert any(caught), f"{mutant}: four seeds of 3000 steps did not see it"


def test_the_parametrised_advance_with_its_defaults_is_the_reference():
    """The control for the mutants that go through ``_advance``: with nothing changed it must agree (else a "caught" mutant could be an artefact of the harness)."""
    assert run_pair(generate(2, 3000), candidate=lambda: mutant_rig("control")) is None


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4, 5, 6):
        for name, count in coverage(generate(seed, 3000)).items():
            total[name] = total.get(name, 0) + count
    for name in (
        "read.reg",
        "read.unimplemented",
        "read.alias",
        "write.reg",
        "write.unimplemented",
        "write.alias",
        "tick",
        "reset",
        "clock",
        "load",
        "log.entries",
        "irq.high",
        "irq.low",
        "state.running",
        "state.enabled_without_clock",
        "state.match",
        "carry.minute",
        "carry.hour",
        "carry.day",
        "carry.month",
        "carry.year",
        "carry.dotw",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    for name in ("carry.february", "carry.leap_day", "irq.raising", "exception"):
        assert total.get(name, 0) >= 3, (name, total)
