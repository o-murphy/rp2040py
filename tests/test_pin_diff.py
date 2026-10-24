"""The pin oracle (tests/utils/pin_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md #29, docs/records/0098-datasheet-conformance-audit.md).

The same three things as for every oracle of that record: (1) the reference (the pure-Python pins) and the pins the chip builds agree on long random runs; (2) a deliberately damaged candidate is caught at the
step the damage is done, and so is a change of the reference's *logic* (every entry of ``MUTATIONS`` is the reference's own source with one expression changed); (3) the runs really exercise what they claim to
(interrupts raised, edges latched, outputs enabled by each function, overrides, driven and released inputs, resets) - a green differential that never raised an interrupt would prove nothing.
"""

import pytest
from utils.pin_diff import IO_BANK0, MUTANTS, PADS_BANK0, PADS_QSPI, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 4000
DIFF_STEPS = 2500
SEEDS = range(1, 5)
MUTANT_SEEDS = range(1, 13)
MUTANT_STEPS = 2000


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_pins_and_the_chips_pins_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _write_a_ctrl(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(IO_BANK0 + 8 * 7 + 4, 0x5)  # GPIO7_CTRL


def _write_a_pad(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(PADS_BANK0 + 4 + 4 * 3, 0xC0)


def _drive_a_pin(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.gpio[9].set_input_value(True)


def _change_a_mask(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(
            IO_BANK0 + 0x100, candidate.chip.read_uint32(IO_BANK0 + 0x100) ^ (0x8 << 16)
        )  # PROC0_INTE0: GPIO4's nibble, the high-level bit


def _change_the_status(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(
            IO_BANK0 + 0x114, candidate.chip.read_uint32(IO_BANK0 + 0x114) ^ (0x4 << 12)
        )  # PROC0_INTF1: force GPIO11's edge-low bit


def _change_a_qspi_pad(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(
            PADS_QSPI + 0x10, candidate.chip.read_uint32(PADS_QSPI + 0x10) ^ 0x8
        )  # PADS_QSPI: SD0's pull-up bit


@pytest.mark.parametrize(
    "perturb",
    [_write_a_ctrl, _write_a_pad, _drive_a_pin, _change_a_mask, _change_the_status, _change_a_qspi_pad],
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
        "pin.edge_latched",
        "pin.interrupt_raw",
        "pin.irq_value",
        "pin.output_enabled",
        "pin.func4",
        "pin.func5",
        "pin.func6",
        "pin.func7",
        "pin.driven",
        "pin.overridden",
    ):
        assert total.get(name, 0) >= 100, (name, total)
    for name in ("log.io_irq", "log.pin", "log.pwm.input", "log.log"):
        assert total.get(name, 0) >= 20, (name, total)
