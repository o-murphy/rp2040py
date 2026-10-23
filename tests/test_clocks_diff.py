"""The CLOCKS / PLL differential oracle (tests/utils/clocks_diff.py) proves itself before it is allowed to judge anything (docs/tasks/clocks-port.md).

(1) the reference (the pure-Python blocks) and the implementation the facade hands out agree on long random runs - the C++ tree in a native build; (2) a deliberately damaged candidate is caught at the
step the damage is done, and so is a change of the reference's *logic*; (3) the runs really exercise what they claim to (the sinks, the retune with the order of its calls, the counter, the reset).
"""

import pytest
from utils.clocks_diff import CLOCKS_BASE, MUTANTS, PLL_BASES, Rig, coverage, generate, mutant_rig, run_pair

SEEDS = range(1, 9)
DIFF_STEPS = 2500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_tree_and_the_default_tree_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _warn_on_one_side(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clocks.warn("only here")


def _write_with_an_alias(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(
            CLOCKS_BASE + 0x2000 + 0x78, 0xA5
        )  # SET alias of CLK_SYS_RESUS_CTRL: raw_write_value moves


def _change_a_divider(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clocks.sys_div ^= 0x300


def _change_a_pll(candidate: Rig, step: int) -> None:
    if step == 700:
        pll = candidate.chip.pll_usb
        pll.write_uint32(0x08, pll.read_uint32(0x08) ^ 0x10)  # FBDIV_INT


def _move_the_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(1)


@pytest.mark.parametrize(
    "perturb",
    [_warn_on_one_side, _write_with_an_alias, _change_a_divider, _change_a_pll, _move_the_clock],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step in (700, 701), divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    caught = [run_pair(generate(seed, 2500), candidate=lambda: mutant_rig(mutant)) for seed in (1, 2, 3, 4)]
    assert any(caught), f"{mutant}: four seeds of 2500 steps did not see it"


def test_a_mutant_that_changes_nothing_agrees():
    """The control for the harness: a mutant name that no override reacts to must agree (else a "caught" mutant could be an artefact of the harness)."""
    assert run_pair(generate(2, 2500), candidate=lambda: mutant_rig("control")) is None


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4):
        for name, count in coverage(generate(seed, 2500)).items():
            total[name] = total.get(name, 0) + count
    for name in (
        "read",
        "write",
        "seq",
        "tick",
        "xosc",
        "rosc_div",
        "reset",
        "raising",
        "alias",
        "log",
        "listener",
        "exception",
        "clock changed",
        "sink.ppb",
        "sink.watchdog",
        "sink.rtc",
        "sink.uart0",
        "sink.uart1",
        "both plls running",
        "rtc clock running",
        "fc done",
        "fc died",
    ):
        assert total.get(name, 0) > 0, f"the runs never exercised {name}: {total}"
    assert PLL_BASES  # (imported for the readers of the generator)
