"""The USB controller differential oracle (tests/utils/usb_diff.py) proves itself before it is allowed to judge anything (docs/tasks/usb-port.md).

The same three things as for every oracle of that record: (1) the reference (the pure-Python chip and its controller) and the implementation the default chip uses agree on long random runs - today that is
the same class, later the C++ block, and this file is what stays green; (2) a deliberately damaged candidate is caught at the step the damage is done, and so is a change of the reference's *logic* (every
entry of ``MUTATIONS`` is the reference's own source with one expression changed); (3) the runs really exercise what they claim to (transfers in both directions, double buffering, every buffer-status path,
setup packets, host-mode transactions and interrupt endpoints, resets) - a green differential that never moved a buffer would prove nothing.
"""

import pytest
from utils.usb_diff import MUTANTS, USB_BASE, Rig, coverage, generate, mutant_rig, run_pair

STEPS = 4000
DIFF_STEPS = 3000
SEEDS = range(1, 9)
MUTANT_SEEDS = range(1, 9)
MUTANT_STEPS = 2500


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pure_python_usb_and_the_default_usb_agree(seed):
    divergence = run_pair(generate(seed, DIFF_STEPS))
    assert divergence is None, f"seed {seed}: {divergence}"


def _flip_a_dpram_byte(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.usb_dpram[0x180] ^= 0x01


def _set_a_status_bit(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.usb_ctrl.raise_sie_status(1 << 17)


def _complete_a_buffer(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.usb_ctrl.raise_stall_nak(1 << 3)


def _change_the_enable(candidate: Rig, step: int) -> None:
    if step == 700:
        usb = candidate.chip.usb_ctrl
        usb.write_uint32(0x90, usb.read_uint32(0x90) ^ 0x10)  # INTE


def _flip_a_buffer_select(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.usb_ctrl.raise_interrupt(1 << 4)


def _write_a_register(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.write_uint32(USB_BASE + 0x6C, 0x1234)  # NAK_POLL


def _advance_the_clock(candidate: Rig, step: int) -> None:
    if step == 700:
        candidate.chip.clock.tick(1_000_000)


@pytest.mark.parametrize(
    "perturb",
    [
        _flip_a_dpram_byte,
        _set_a_status_bit,
        _complete_a_buffer,
        _change_the_enable,
        _flip_a_buffer_select,
        _write_a_register,
        _advance_the_clock,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_damaged_candidate_is_caught_where_it_was_damaged(perturb):
    divergence = run_pair(generate(3, 1500), perturb=perturb)
    assert divergence is not None
    assert divergence.step == 700, divergence


@pytest.mark.parametrize("mutant", MUTANTS)
def test_a_change_of_the_reference_logic_is_caught(mutant):
    caught = [run_pair(generate(seed, MUTANT_STEPS), candidate=lambda: mutant_rig(mutant)) for seed in MUTANT_SEEDS]
    assert any(caught), f"{mutant}: {len(MUTANT_SEEDS)} seeds of {MUTANT_STEPS} steps did not see it"


def test_the_runs_exercise_what_they_claim_to():
    total: dict[str, int] = {}
    for seed in (1, 2, 3, 4):
        for name, count in coverage(generate(seed, STEPS)).items():
            total[name] = total.get(name, 0) + count
    for name in (
        "device.in_delivered",
        "device.in_data",
        "device.out_asked",
        "bufctrl.in",
        "bufctrl.out",
        "bufctrl.double",
        "bufctrl.stall",
        "bufctrl.reset_select",
    ):
        assert total.get(name, 0) >= 50, (name, total)
    for name in (
        "bufctrl.width1",
        "bufctrl.width2",
        "bufctrl.width4",
        "bufctrl.in_without_full",
        "bufctrl.out_already_full",
        "epctrl.iso",
        "epctrl.per_two",
    ):
        assert total.get(name, 0) >= 50, (name, total)
    for name in (
        "buff_status.raised",
        "buff_status.buffer1",
        "buff_status.cleared",
        "buff_status.two_waiting",
        "sie.trans_complete",
        "sie.setup_rec",
        "sof_rd.read",
    ):
        assert total.get(name, 0) >= 50, (name, total)
    for name in (
        "host.mode_entered",
        "host.transaction",
        "host.interrupt_endpoints_armed",
        "log.dev.setup",
        "log.dev.in",
        "log.dev.out",
        "log.dev.reset",
        "log.dev.address",
    ):
        assert total.get(name, 0) >= 20, (name, total)
    assert total.get("device.in_zero_length", 0) >= 20, total  # the status stage of a control transfer
    for name in (
        "alias",
        "past_the_block",
        "log.enabled",
        "log.irq",
        "log.log",
        "reset",
        "usb_pwr.write",
        "sie.bus_reset",
        "poke.poke_status",
        "poke.poke_stall_nak",
    ):
        assert total.get(name, 0) >= 20, (name, total)
