"""The DMA differential oracle (tests/utils/dma_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, Phase 4).

The same three things as for every oracle of that record: (1) the reference (the pure-Python DMA) and the implementation the facade hands out agree on long
random runs - today that is the same class, later the C++ controller, and this file is what stays green; (2) a deliberately damaged candidate is caught at the
step the damage is done; (3) the runs really exercise what they claim to (every transfer function, rings on both sides, chains, quiet and interrupting
completion, aborts, paced / DREQ-driven / permanent scheduling, probe writes that raise DREQs from inside a transfer) - a green differential that never ran a
ring or a chain would prove nothing.
"""

import pytest
from utils.dma_diff import DMA_BASE, MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 5000
DIFF_STEPS = 3000
SEEDS = range(1, 9)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_dma_and_the_default_dma_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _move_a_read_pointer(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(DMA_BASE + 3 * 0x40, 0x20000444)


def _raise_a_spurious_dreq(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.dma.set_dreq(5)


def _set_a_raw_interrupt_bit(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.dma.int_raw ^= 1 << 4


def _let_the_clock_run_on(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(300000)


def _change_an_interrupt_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(DMA_BASE + 0x404, 0xFFF)


def _poke_the_memory_a_channel_works_in(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(0x20001000, 0x12345678)


@pytest.mark.parametrize(
    "perturb",
    [
        _move_a_read_pointer,
        _raise_a_spurious_dreq,
        _set_a_raw_interrupt_bit,
        _let_the_clock_run_on,
        _change_an_interrupt_enable,
        _poke_the_memory_a_channel_works_in,
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
    for function in ("transfer8", "transfer16", "transfer32", "transfer_swap16", "transfer_swap32"):
        assert total.get(f"transfer.{function}", 0) >= 300, (function, total)
    for name in ("ring_read", "ring_write", "fixed_read", "fixed_write"):
        assert total.get(f"transfer.{name}", 0) >= 300, (name, total)
    for name in (
        "finished.irq",
        "finished.quiet",
        "chain.valid",
        "chain.invalid",
        "abort.busy",
        "abort.idle",
        "start.running",
        "start.disabled",
    ):
        assert total.get(name, 0) >= 30, (name, total)
    assert total.get("start", 0) >= 300, total
    for name in ("schedule.permanent", "schedule.dreq_asserted", "schedule.paced", "schedule.stalled"):
        assert total.get(name, 0) >= 100, (name, total)
    assert total.get("irq", 0) >= 500, total
    assert total.get("probe", 0) >= 300, total
    assert total.get("log", 0) >= 500, total  # the bus's warnings for the unmapped addresses
