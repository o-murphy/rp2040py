"""The SPI differential oracle (tests/utils/spi_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python SPI) and the implementation the facade hands out agree on long random runs - today
that is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of
the reference's *logic*; (3) the runs really exercise what they claim to (a TX FIFO overflow, an RX overrun, a completion with nothing sent, the three kinds of device, re-entrant
completion, a reset in the middle of a transfer) - a green differential that never overran anything would prove nothing.
"""

import pytest
from utils.spi_diff import MUTANTS, SPI0_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 6000
DIFF_STEPS = 4000
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_spi_and_the_default_spi_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _complete_a_transfer_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.spi[0].complete_transmit(0x55)


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(SPI0_BASE + 0x10, 0x12)  # SSPCPSR


def _change_the_interrupt_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.spi[0]._int_enable ^= 0x4


def _change_the_control_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.spi[0]._control0 ^= 0x40


def _drain_a_byte(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.read_uint32(SPI0_BASE + 0x08)  # an SSPDR read on one side only


@pytest.mark.parametrize(
    "perturb",
    [
        _complete_a_transfer_on_one_side,
        _write_a_register,
        _change_the_interrupt_enable,
        _change_the_control_register,
        _drain_a_byte,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(4, 1500), perturb=perturb)
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
    for name in (
        "tx.full_drop",
        "rx.overrun",
        "complete.spurious",
        "complete.busy",
        "dr.read.empty",
        "dr.read.data",
        "icr",
    ):
        assert total.get(name, 0) >= 30, (name, total)
    for name in ("device.immediate", "device.deferred", "device.silent"):
        assert total.get(name, 0) >= 20, (name, total)
    for name in ("log.irq.1", "log.irq.0", "log.dreq.1", "log.dreq.0", "log.tx", "log.log"):
        assert total.get(name, 0) >= 20, (name, total)
    assert total.get("reset", 0) >= 20 and total.get("reset.busy", 0) >= 5, total
