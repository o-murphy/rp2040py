"""The SYSINFO / TBMAN differential oracle (tests/utils/ident_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, "The small blocks").

(1) the reference (the pure-Python block) and the implementation the facade hands out agree on long random runs - the C++ block in a native build; (2) a deliberately damaged candidate is caught
at the step the damage is done, and so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (every bootrom version, aliases, unimplemented offsets, the
warnings).
"""

import pytest
from utils.ident_diff import BLOCKS, SYSINFO_MUTANTS, TBMAN_MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

SEEDS = range(1, 9)
DIFF_STEPS = 3000
MUTANTS = [("sysinfo", name) for name in SYSINFO_MUTANTS] + [("tbman", name) for name in TBMAN_MUTANTS]


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_block_and_the_default_block_agree(block, seed):
    divergence = run_pair(block, generate(block, seed, DIFF_STEPS))
    assert divergence is None, f"{block} seed {seed}: {divergence}"


def _change_the_rom_version(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.set_rom_version(3 if candidate.chip.bootrom[4] >> 24 != 3 else 2)  # on one side only


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
    assert divergence.step == 700 + 1 or divergence.step == 700, divergence


def test_a_changed_rom_version_on_one_side_is_caught():
    divergence = run_pair("sysinfo", generate("sysinfo", 3, 1500), perturb=_change_the_rom_version)
    assert divergence is not None and divergence.step >= 700, divergence


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
    for name in (
        "read.reg",
        "read.unimplemented",
        "read.alias",
        "write.reg",
        "write.unimplemented",
        "write.alias",
        "reset",
        "log.entries",
    ):
        assert total.get(name, 0) >= 100, (name, total)
    if block == "sysinfo":
        for version in (0, 1, 2, 3, 4, 0xFF):
            assert total.get(f"rom.{version}", 0) >= 20, (version, total)
