"""The BUSCTRL / SYSCFG / VREG_AND_CHIP_RESET differential oracle (tests/utils/small_blocks_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, "The small blocks").

(1) the reference (the pure-Python block) and the implementation the facade hands out agree on long random runs - the C++ block in a native build; (2) a deliberately damaged candidate is caught
at the step the damage is done, and so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (every register, aliases, unimplemented offsets, resets, causes, the warnings).
"""

import pytest
from utils.small_blocks_diff import (
    BLOCKS,
    BUSCTRL_MUTANTS,
    SYSCFG_MUTANTS,
    VREG_MUTANTS,
    Rig,
    coverage,
    generate,
    mutant_rig,
    run_pair,
)

SEEDS = range(1, 9)
DIFF_STEPS = 3000
MUTANTS = (
    [("busctrl", name) for name in BUSCTRL_MUTANTS]
    + [("syscfg", name) for name in SYSCFG_MUTANTS]
    + [("vreg", name) for name in VREG_MUTANTS]
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


def _change_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        name = candidate.spec["attrs"][0]
        current = getattr(candidate.peripheral, name)
        setattr(candidate.peripheral, name, (current ^ 0x10) if isinstance(current, int) else current)


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize(
    "perturb", [_warn_on_one_side, _write_with_an_alias, _change_a_register], ids=lambda f: f.__name__.lstrip("_")
)
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
    wanted += {"busctrl": ["reset", "count"], "syscfg": ["reset", "nmi"], "vreg": ["cause", "psm_flag"]}[block]
    for name in wanted:
        assert total.get(name, 0) >= 100, (name, total)
