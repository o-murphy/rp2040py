"""The I2C differential oracle (tests/utils/i2c_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python I2C) and the implementation the facade hands out agree on long random runs - today that
is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of the
reference's *logic*; (3) the runs really exercise what they claim to (a whole transaction through every state of the bus state machine, a NACKed address, a NACKed data byte, an abort
with commands still queued, a stop, RX under- and overflow, TX overflow, re-entrant completion from inside a callback, a reset in the middle of a transfer) - a green differential
that never got past the first NACK would prove nothing.
"""

import pytest
from utils.i2c_diff import I2C0_BASE, MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 8000
DIFF_STEPS = 4000
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_i2c_and_the_default_i2c_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _complete_a_write_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.i2c[0].complete_write(True)


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(I2C0_BASE + 0x14, 0x1234)  # IC_SS_SCL_HCNT


def _change_the_interrupt_mask(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.i2c[0].int_enable ^= 0x10


def _change_the_state_machine(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.i2c[0]._busy = not candidate.chip.i2c[0]._busy


def _pull_a_byte_from_the_rx_fifo(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.read_uint32(I2C0_BASE + 0x10)  # an IC_DATA_CMD read on one side only


@pytest.mark.parametrize(
    "perturb",
    [
        _complete_a_write_on_one_side,
        _write_a_register,
        _change_the_interrupt_mask,
        _change_the_state_machine,
        _pull_a_byte_from_the_rx_fifo,
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
    for name in ("tx.overflow", "rx.under", "rx.data", "log.start", "log.connect", "log.write", "log.read", "log.stop"):
        assert total.get(name, 0) >= 30, (name, total)
    for state in range(5):  # IDLE START CONNECT CONNECTED STOP: a transition into each
        assert any(k.startswith("transition.") and k.endswith(f"->{state}") and v >= 5 for k, v in total.items()), (
            state,
            total,
        )
    assert total.get("abort.nonzero", 0) >= 50, total
    assert total.get("log.irq.1", 0) >= 30 and total.get("log.irq.0", 0) >= 30, total
    assert total.get("reset", 0) >= 20 and total.get("reset.busy", 0) >= 5, total
    for name in (
        "device.connect.auto",
        "device.connect.defer",
        "device.read.auto",
        "device.write.silent",
        "device.stop.default",
    ):
        assert total.get(name, 0) >= 5, (name, total)
