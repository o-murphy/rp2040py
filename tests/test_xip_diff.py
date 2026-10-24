"""The XIP_CTRL differential oracle (tests/utils/xip_diff.py) proves itself before it is allowed to judge anything (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md).

The same three things as for every oracle of that record: (1) the reference (the pure-Python chip and its XIP block) and the implementation the default chip uses agree on long random runs; (2) a deliberately
damaged candidate is caught at the step the damage is done, and so is a change of the reference's *logic* (every entry of ``MUTATIONS`` is the reference's own source with one expression changed); (3) the runs
really exercise what they claim to (hits and misses on every alias, evictions, write-invalidates, flushes, the cache as SRAM available and refused, a stream filling its FIFO, a DMA channel draining it, saturation).
"""

import pytest
from utils.xip_diff import MUTANTS, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 3000
DIFF_STEPS = 2500
SEEDS = range(1, 5)
MUTANT_SEEDS = range(1, 13)
MUTANT_STEPS = 1500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_xip_and_the_default_xip_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _count_an_access(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.xip_ctrl.set_counter("ctr_acc", 12345)


def _change_the_ctrl(candidate: Rig, step: int) -> None:
    if step == 700:
        xip = candidate.chip.xip_ctrl
        xip.write_uint32(0x00, xip.read_uint32(0x00) ^ 0x2)  # CTRL


def _move_the_stream(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.xip_ctrl.write_uint32(0x14, 0x1230)  # STREAM_ADDR


def _change_the_flash(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.flash[0] ^= 0xFF


def _advance_one_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(10)


def _flush_the_cache_unseen(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(0x14000004, 1)


@pytest.mark.parametrize(
    "perturb",
    [
        _count_an_access,
        _change_the_ctrl,
        _move_the_stream,
        _change_the_flash,
        _advance_one_clock,
        _flush_the_cache_unseen,
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
        "read.alias0",
        "read.alias1",
        "read.alias2",
        "read.alias3",
        "read.hit",
        "read.miss",
        "read.width2",
        "read.width1",
        "write.alias0",
        "write.alias2",
        "sram.read.ok",
        "sram.read.refused",
        "sram.write.ok",
        "sram.write.refused",
        "flush",
        "flush.ready_changed",
        "stream.word",
        "stream.full",
        "dma.word_landed",
        "state.cache_off",
        "state.power_down",
        "log.sram_error",
        "log.bad_write",
        "reset",
    ):
        assert total.get(name, 0) >= 15, (name, total)
    assert total.get("counter.saturated", 0) >= 3, total
    assert total.get("counter.hit_saturated", 0) >= 1, total
