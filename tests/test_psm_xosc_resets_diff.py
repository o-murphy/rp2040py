"""The PSM / XOSC / RESETS differential oracle (tests/utils/psm_xosc_resets_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, "The small blocks").

(1) the reference (the pure-Python block) and the implementation the facade hands out agree on long random runs - the C++ block in a native build; (2) a deliberately damaged candidate is caught
at the step the damage is done, and so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (every register, aliases, unimplemented offsets, resets, the XOSC's states).
"""

import pytest
from utils.psm_xosc_resets_diff import (
    BLOCKS,
    PSM_MUTANTS,
    RESETS_MUTANTS,
    XOSC_MUTANTS,
    Rig,
    coverage,
    generate,
    mutant_rig,
    run_pair,
)

SEEDS = range(1, 9)
DIFF_STEPS = 3000
MUTANTS = (
    [("psm", name) for name in PSM_MUTANTS]
    + [("resets", name) for name in RESETS_MUTANTS]
    + [("xosc", name) for name in XOSC_MUTANTS]
)


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_block_and_the_default_block_agree(block, seed):
    divergence = run_pair(block, generate(block, seed, DIFF_STEPS))
    assert divergence is None, f"{block} seed {seed}: {divergence}"


def _warn_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.peripheral.warn("only here")


def _write_with_an_alias(candidate: Rig, step: int) -> None:
    if step == 700:
        base = candidate.spec["base"]
        candidate.chip.write_uint32(
            base + 0x2000 + candidate.spec["registers"][0], 0xA5
        )  # SET alias: raw_write_value moves


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("perturb", [_warn_on_one_side, _write_with_an_alias], ids=lambda f: f.__name__.lstrip("_"))
def test_a_damaged_candidate_is_caught_where_it_was_damaged(block, perturb):
    divergence = run_pair(block, generate(block, 3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step in (700, 701), divergence


@pytest.mark.parametrize(("block", "mutant"), MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(block, mutant):
    caught = [
        run_pair(block, generate(block, seed, 3000), candidate=lambda: mutant_rig(block, mutant)) for seed in (1, 2, 3)
    ]
    assert any(caught), f"{block}/{mutant}: three seeds of 3000 steps did not see it"


@pytest.mark.parametrize("block", BLOCKS)
def test_the_runs_exercise_what_they_claim_to(block):
    total: dict[str, int] = {}
    for seed in (1, 2, 3):
        for name, count in coverage(block, generate(block, seed, 3000)).items():
            total[name] = total.get(name, 0) + count
    wanted = [
        "read.reg",
        "read.unimplemented",
        "read.alias",
        "write.reg",
        "write.unimplemented",
        "write.alias",
        "log.entries",
    ]
    if block == "xosc":
        wanted += ["reset", "state.enabled", "state.dormant", "state.badwrite"]
    for name in wanted:
        assert total.get(name, 0) >= 100, (name, total)
