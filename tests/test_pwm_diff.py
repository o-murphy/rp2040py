"""The PWM differential oracle (tests/utils/pwm_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python PWM) and the implementation the facade hands out agree on long random runs - today that
is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of the
reference's *logic* (every entry of ``MUTATIONS`` is the reference's own source with one expression changed); (3) the runs really exercise what they claim to (wraps with interrupts and
DREQ, pin transitions on both pin pairs, phase-correct and inverted channels, the gated and the edge-counting divider modes with a partial tick count, double-buffered writes still
pending, a reset with the timers running) - a green differential that never wrapped a counter would prove nothing.
"""

import pytest
from utils.pwm_diff import MUTANTS, PWM_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 4000
DIFF_STEPS = 3000
SEEDS = range(1, 5)
MUTANT_SEEDS = range(1, 9)
MUTANT_STEPS = 1500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_pwm_and_the_default_pwm_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(PWM_BASE + 0x10, 0x1234)  # channel 0 TOP


def _write_the_compare_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(PWM_BASE + 0x14 * 3 + 0x0C, 0x00050003)  # channel 3 CC


def _flip_the_b_input_latch(candidate: Rig, step: int) -> None:
    if (
        step == 700
    ):  # (a pin's level is not a reliable probe: an input override a random FUNCSEL write left on the pin can pin it)
        candidate.chip.pwm.channels[3].last_b_value = not candidate.chip.pwm.channels[3].last_b_value


def _advance_one_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(10)


def _change_the_interrupt_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pwm._int_enable ^= 0x1


def _change_the_pin_word(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pwm.gpio_value ^= 0x1


def _change_a_channel(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.pwm.channels[2].tick_counter += 1


def _pull_a_pin(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(0x40014000 + 8 * 20 + 4, 4)  # GPIO20 to the PWM function


@pytest.mark.parametrize(
    "perturb",
    [
        _write_a_register,
        _write_the_compare_register,
        _flip_the_b_input_latch,
        _advance_one_clock,
        _change_the_interrupt_enable,
        _change_the_pin_word,
        _change_a_channel,
        _pull_a_pin,
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
        "csr",
        "csr.phase_correct",
        "csr.strobe",
        "input.counted_mode",
        "reset.running",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    for name in (
        "alarm.scheduled",
        "double_buffer.cc_pending",
        "double_buffer.top_pending",
        "running.gated",
        "running.inverted",
        "tick_counter.nonzero",
    ):
        assert total.get(name, 0) >= 100, (name, total)
    for name in ("log.irq.1", "log.irq.0", "log.dreq.1", "log.pin", "pin.first", "pin.second"):
        assert total.get(name, 0) >= 100, (name, total)
    assert total.get("log.log", 0) >= 10, total
