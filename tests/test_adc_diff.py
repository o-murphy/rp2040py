"""The ADC differential oracle (tests/utils/adc_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python ADC) and the implementation the facade hands out agree on long random runs - today that
is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of the
reference's *logic*; (3) the runs really exercise what they claim to (conversions through both alarms, a FIFO overflow, an error flag, an underflow, round-robin, DREQ traffic, a reset
with an alarm pending, the failure out of the sample alarm for a channel above 4) - a green differential that never finished a conversion would prove nothing.
"""

import pytest
from utils.adc_diff import ADC_BASE, MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 6000
DIFF_STEPS = 4000
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_adc_and_the_default_adc_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _complete_a_read_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.adc.complete_adc_read(0x123, False)


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(ADC_BASE + 0x10, 0x1234)  # DIV


def _change_the_interrupt_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.adc.int_enable ^= 0x1


def _change_the_channel(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.adc.current_channel ^= 0x1


def _pull_a_result(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.read_uint32(ADC_BASE + 0x0C)  # a FIFO read on one side only


def _advance_one_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(10)


@pytest.mark.parametrize(
    "perturb",
    [
        _complete_a_read_on_one_side,
        _write_a_register,
        _change_the_interrupt_enable,
        _change_the_channel,
        _pull_a_result,
        _advance_one_clock,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    caught = [run_pair(generate(seed, 3000), candidate=lambda: mutant_rig(mutant)) for seed in (1, 2, 3, 4)]
    assert any(caught), f"{mutant}: four seeds of 3000 steps did not see it"


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4):
        for name, count in coverage(generate(seed, STEPS)).items():
            total[name] = total.get(name, 0) + count
    for name in (
        "fifo.read.empty",
        "fifo.read.data",
        "fifo.overflow",
        "complete.error",
        "complete.spurious",
        "complete.busy",
        "tick",
    ):
        assert total.get(name, 0) >= 30, (name, total)
    for name in ("alarm.scheduled", "round_robin.tick", "reset.alarm"):
        assert total.get(name, 0) >= 20, (name, total)
    for name in ("device.immediate", "device.deferred", "device.silent", "device.default"):
        assert total.get(name, 0) >= 10, (name, total)
    for name in ("log.irq.1", "log.irq.0", "log.dreq.1", "log.dreq.0", "log.read"):
        assert total.get(name, 0) >= 20, (name, total)
    assert total.get("raised.IndexError", 0) >= 1, total
