"""The pin bank as an interface both builds share (docs/records/0096-cpp-mcu-core.md, step 0 (b)): a chip's pins are slots of two banks, `gpio` / `qspi` are the banks' own pin lists, and
the chip answers `gpio_values` and the IO_BANK0 interrupt line by asking its bank - in the pure-Python build and in the native one, with the pure-Python bank the reference of the C++ one
(tests/test_pin_diff.py holds the two equal under mutation). Runs on whichever build is in use, so CI covers both.
"""

import pytest

from rp2040py._gpio_pin import GPIOPin as PureGPIOPin
from rp2040py._pin_bank import GPIOPinBank as PureGPIOPinBank
from rp2040py.gpio_pin import GPIOPin, GPIOPinBank
from rp2040py.irq import IRQ
from rp2040py.rp2040 import RP2040

IO = 0x40014000


def test_the_chips_pins_are_slots_of_two_banks_and_the_lists_are_the_banks_own():
    chip = RP2040()
    assert isinstance(chip._gpio_bank, GPIOPinBank) and isinstance(chip._qspi_bank, GPIOPinBank)
    assert len(chip._gpio_bank) == 30 and len(chip._qspi_bank) == 6
    assert chip.gpio is chip._gpio_bank.shells and chip.qspi is chip._qspi_bank.shells
    assert all(isinstance(pin, GPIOPin) for pin in chip.gpio + chip.qspi)
    assert [pin.index for pin in chip.gpio] == list(range(30)) and [pin.index for pin in chip.qspi] == list(range(6))
    assert [pin.name for pin in chip.qspi] == ["SCLK", "SS", "SD0", "SD1", "SD2", "SD3"]
    assert all(not pin.snapshot()["always_output_enabled"] for pin in chip.gpio) and all(
        pin.snapshot()["always_output_enabled"] for pin in chip.qspi
    )


def test_the_bank_answers_for_all_its_pins_at_once():
    chip = RP2040()
    chip.gpio[3].set_input_value(True)
    chip.gpio[7].set_input_value(True)
    chip.gpio[29].set_input_value(True)
    expected = (1 << 3) | (1 << 7) | (1 << 29)
    assert chip._gpio_bank.input_values() == chip.gpio_values == expected
    assert chip._qspi_bank.input_values() == 1 << 1  # GPIO_QSPI_SS has the pull-up, nothing else reads high
    assert not chip._gpio_bank.any_irq_value()
    chip.write_uint32(IO + 0x100, 0x2 << 12)  # pin 3: level high enabled for proc 0
    assert chip._gpio_bank.any_irq_value()


def test_the_io_bank0_interrupt_line_follows_any_pin():
    chip = RP2040()
    raised = []
    original = chip.set_interrupt

    def spy(irq, value):
        raised.append((irq, bool(value)))
        original(irq, value)

    chip.set_interrupt = spy  # type: ignore[method-assign]
    chip.gpio[5].set_input_value(True)
    chip.write_uint32(IO + 0x100, 0x2 << (5 * 4))  # PROC0_INTE0: pin 5, level high (bit 1 of its nibble)
    assert raised[-1] == (IRQ.IO_BANK0, True)
    chip.gpio[5].set_input_value(False)
    assert raised[-1] == (IRQ.IO_BANK0, False)
    chip.gpio[5].set_input_value(True)
    assert raised[-1] == (IRQ.IO_BANK0, True)
    # a second pin raising it too does not lower the line when the first goes away
    chip.write_uint32(IO + 0x100, (0x2 << (5 * 4)) | (0x2 << (6 * 4)))
    chip.gpio[6].set_input_value(True)
    chip.gpio[5].set_input_value(False)
    assert raised[-1] == (IRQ.IO_BANK0, True)


@pytest.mark.parametrize("pin_class, bank_class", [(PureGPIOPin, PureGPIOPinBank), (GPIOPin, GPIOPinBank)])
def test_a_bank_and_its_pins_obey_one_contract(pin_class, bank_class):
    chip = RP2040()
    bank = bank_class(chip, 4, False, 10)  # pins 10..13
    assert len(bank) == 4 and bank.first == 10 and list(bank.shells) == [None] * 4
    pins = [pin_class(chip, 10 + i, bank=bank) for i in range(4)]
    assert list(bank.shells) == pins
    with pytest.raises(ValueError):
        pin_class(chip, 11, bank=bank)  # the slot already has its pin
    with pytest.raises(ValueError):
        pin_class(chip, 9, bank=bank)  # before the bank
    with pytest.raises(ValueError):
        pin_class(chip, 14, bank=bank)  # after the bank
    with pytest.raises(ValueError):
        bank_class(chip, 0)
    with pytest.raises(ValueError):
        bank_class(chip, 33)
    assert bank.input_values() == 0  # nothing drives them, the pulls (pull-down at reset) decide
    pins[2].set_input_value(True)
    assert bank.input_values() == 1 << 2  # bit = the slot, not the pin number
    assert not bank.any_irq_value()
    pins[3].irq_force_mask = 0x1
    assert bank.any_irq_value()


@pytest.mark.parametrize("pin_class", [PureGPIOPin, GPIOPin])
def test_a_pin_without_a_bank_gets_one_of_its_own(pin_class):
    chip = RP2040()
    lone = pin_class(chip, 5, always_output_enabled=True)
    assert lone.index == 5 and lone.snapshot()["always_output_enabled"]
    assert lone._slot == 0
