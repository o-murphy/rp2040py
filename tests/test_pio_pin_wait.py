"""A state machine parked on ``wait gpio`` / ``wait pin`` is woken by an input change on exactly that pin.

docs/records/0096-cpp-mcu-core.md, Phase 3: the pin layer walks every machine of both PIOs on each input change and wakes the ones parked
on that pin. These tests pin that behaviour on both builds (a wake-up on the right pin only, polarity, ``wait pin`` relative to
``IN_BASE``, both PIOs, several waiters, a second wait after a wake-up) so any change to how input changes reach the machines - the walk
is the obvious thing to optimise, see the record's 2026-10-04 "pin_wait_mask" entry for why it was tried and dropped - is checked against
something that fails if a waiting machine is ever missed.
"""

from rp2040py.rp2040 import RP2040
from rp2040py.utils.pio_assembler import PIO_WAIT_SRC_GPIO, PIO_WAIT_SRC_PIN, pio_wait

PAD_INPUT_ENABLE = 0x40
PIO_BASES = (0x50200000, 0x50300000)
CTRL, SM0_INSTR, SM0_PINCTRL, SM_STRIDE, ATOMIC_SET = 0x00, 0xD8, 0xDC, 0x18, 0x2000


def _chip_with_inputs(*pins: int) -> RP2040:
    chip = RP2040()
    chip.simulator = (
        object()
    )  # an owner, so enabling a machine does not start the no-Simulator fallback loop: nothing here steps a PIO
    for pin in pins:
        chip.gpio[pin].pad_value |= PAD_INPUT_ENABLE  # an input is only seen through an enabled pad
    return chip


def _enable(chip: RP2040, pio: int, machine: int) -> None:
    chip.write_uint32(PIO_BASES[pio] + ATOMIC_SET + CTRL, 1 << machine)  # (set: the other machines stay as they are)


def _exec(chip: RP2040, pio: int, machine: int, opcode: int) -> None:
    chip.write_uint32(PIO_BASES[pio] + SM0_INSTR + SM_STRIDE * machine, opcode)


def _waiting(chip: RP2040, pio: int, machine: int) -> bool:
    return bool(chip.pio[pio].snapshot()["machines"][machine]["waiting"])


def _wait(chip: RP2040, pio: int, machine: int, source: int, index: int, polarity: bool = True):
    _enable(chip, pio, machine)
    _exec(chip, pio, machine, pio_wait(polarity, source, index))
    assert _waiting(chip, pio, machine)
    return pio, machine


def test_wait_gpio_wakes_when_its_pin_goes_high():
    chip = _chip_with_inputs(3)
    sm = _wait(chip, 0, 0, PIO_WAIT_SRC_GPIO, 3)
    chip.gpio[3].set_input_value(True)
    assert not _waiting(chip, *sm)


def test_a_change_on_another_pin_does_not_wake_it():
    chip = _chip_with_inputs(3, 4)
    sm = _wait(chip, 0, 0, PIO_WAIT_SRC_GPIO, 3)
    chip.gpio[4].set_input_value(True)
    assert _waiting(chip, *sm)
    chip.gpio[3].set_input_value(True)
    assert not _waiting(chip, *sm)


def test_wait_for_low_wakes_on_the_falling_level():
    chip = _chip_with_inputs(6)
    chip.gpio[6].set_input_value(True)
    sm = _wait(chip, 0, 0, PIO_WAIT_SRC_GPIO, 6, polarity=False)
    chip.gpio[6].set_input_value(True)  # still high: stays parked
    assert _waiting(chip, *sm)
    chip.gpio[6].set_input_value(False)
    assert not _waiting(chip, *sm)


def test_a_second_wait_after_a_wake_up_is_still_seen():
    """The mask is recomputed after a delivery; a wait that starts afterwards must set its bit again."""
    chip = _chip_with_inputs(3, 5)
    sm = _wait(chip, 0, 0, PIO_WAIT_SRC_GPIO, 3)
    chip.gpio[3].set_input_value(True)
    assert not _waiting(chip, *sm)
    _exec(chip, *sm, pio_wait(True, PIO_WAIT_SRC_GPIO, 5))
    assert _waiting(chip, *sm)
    chip.gpio[5].set_input_value(True)
    assert not _waiting(chip, *sm)


def test_the_second_pio_is_walked_too():
    chip = _chip_with_inputs(7)
    sm = _wait(chip, 1, 2, PIO_WAIT_SRC_GPIO, 7)
    chip.gpio[7].set_input_value(True)
    assert not _waiting(chip, *sm)


def test_two_machines_waiting_on_the_same_pin_both_wake():
    chip = _chip_with_inputs(9)
    first = _wait(chip, 0, 0, PIO_WAIT_SRC_GPIO, 9)
    second = _wait(chip, 0, 3, PIO_WAIT_SRC_GPIO, 9)
    chip.gpio[9].set_input_value(True)
    assert not _waiting(chip, *first) and not _waiting(chip, *second)


def test_wait_pin_is_relative_to_in_base():
    chip = _chip_with_inputs(5)
    _enable(chip, 0, 1)
    chip.write_uint32(PIO_BASES[0] + SM0_PINCTRL + SM_STRIDE, 3 << 15)  # SM1_PINCTRL.IN_BASE = 3
    _exec(chip, 0, 1, pio_wait(True, PIO_WAIT_SRC_PIN, 2))  # pin 2 + in_base 3 = GPIO 5
    assert _waiting(chip, 0, 1)
    chip.gpio[5].set_input_value(True)
    assert not _waiting(chip, 0, 1)
