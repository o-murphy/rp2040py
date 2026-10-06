"""The UART differential oracle (tests/utils/uart_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python UART) and the implementation the facade hands out agree on long random runs -
today that is the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so
is a change of the reference's *logic*; (3) the runs really exercise what they claim to (a FIFO overflow, a DR read of an empty FIFO, ICR through every alias, a zero baud
divider, a reset with bytes queued) - a green differential that never overflowed anything would prove nothing.
"""

import pytest
from utils.uart_diff import MUTANTS, UART0_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 6000
DIFF_STEPS = 4000
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_uart_and_the_default_uart_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _feed_an_extra_byte(candidate: Rig, step: int) -> None:
    if step == 700:
        uart = candidate.chip.uart[0]
        uart._ctrl_register |= 0x301  # a disabled UART takes nothing from the line; this one is damaged while enabled
        uart.feed_byte(0x5A)


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(UART0_BASE + 0x24, 0x1234)  # IBRD


def _change_the_interrupt_mask(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.uart[0]._interrupt_mask ^= 0x10


def _change_the_line_control(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.uart[0]._line_ctrl_register ^= 0x10


def _drain_a_byte(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.read_uint32(UART0_BASE)  # a DR read on one side only


@pytest.mark.parametrize(
    "perturb",
    [_feed_an_extra_byte, _write_a_register, _change_the_interrupt_mask, _change_the_line_control, _drain_a_byte],
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
    for name in ("rx.full_drop", "dr.read.empty", "dr.read.data", "icr.alias", "icr.plain", "imsc", "dr.write.byte"):
        assert total.get(name, 0) >= 50, (name, total)
    for name in ("log.irq.1", "log.irq.0", "log.dreq.1", "log.dreq.0", "log.byte", "log.baud", "log.log"):
        assert total.get(name, 0) >= 20, (name, total)
    assert total.get("raised.ZeroDivisionError", 0) >= 5, total  # a zero divider reaches the writer
    assert total.get("reset", 0) >= 20 and total.get("reset.fifo_nonempty", 0) >= 5, total
