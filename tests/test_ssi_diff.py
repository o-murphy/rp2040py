"""The SSI differential oracle (tests/utils/ssi_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, the flash-path design note).

The same three things as for every oracle of that record: (1) the reference (the pure-Python SSI) and the implementation the facade hands out agree on long random runs -
today that is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and
so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (every opcode, erase and program with and without the write-enable latch, reads
past the end of the flash, chip-select toggles, a reset in the middle of a command) - a green differential that never erased anything would prove nothing.
"""

import pytest
from utils.ssi_diff import MUTANTS, SSI_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 5000
DIFF_STEPS = 3500
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_ssi_and_the_default_ssi_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _flip_a_flash_byte(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.flash[0x100] ^= 0x01


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(SSI_BASE + 0x14, 0x1234)  # BAUDR


def _push_a_byte_onto_the_rx_fifo(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.ssi._rx_queue.append(0x5A)


def _latch_write_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.ssi._write_enabled = not candidate.chip.ssi._write_enabled


def _extend_the_command_in_flight(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.ssi._tx_buffer.append(0x77)


@pytest.mark.parametrize(
    "perturb",
    [
        _flip_a_flash_byte,
        _write_a_register,
        _push_a_byte_onto_the_rx_fifo,
        _latch_write_enable,
        _extend_the_command_in_flight,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    caught = [run_pair(generate(seed, 3000), candidate=lambda: mutant_rig(mutant)) for seed in (1, 2, 3)]
    assert any(caught), f"{mutant}: three seeds of 3000 steps did not see it"


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4):
        for name, count in coverage(generate(seed, STEPS)).items():
            total[name] = total.get(name, 0) + count
    for name in ("opcode.wren", "opcode.wrdi", "opcode.unknown", "opcode.first"):
        assert total.get(name, 0) >= 100, (name, total)
    for name in (
        "erase.wel",
        "erase.no_wel",
        "erase.wel.short",
        "program.wel",
        "program.no_wel",
        "program.wel.long",
        "program.no_wel.long",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    for name in ("cs.assert", "cs.deassert", "dr0.cs_asserted", "dr0.cs_deasserted", "dr0.ssienr_clear"):
        assert total.get(name, 0) >= 50, (name, total)
    assert total.get("read.in_range", 0) >= 1000 and total.get("read.past_the_end", 0) >= 100, total
    assert total.get("shift.past_260", 0) >= 500, total  # a command longer than the 256 data bytes a program keeps
    assert total.get("reset", 0) >= 50 and total.get("reset.mid_command", 0) >= 10, total
    assert total.get("warning", 0) >= 100, total  # the unimplemented registers
