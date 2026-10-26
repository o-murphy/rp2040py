"""What the native chip's pin wiring promises beyond the register behaviour (that is tests/test_pin_diff.py): the chip's pins are slots of two shared banks, a pin on its own still works,
and the sources of a pin's level are wired straight to the native SIO / PIO / PWM registers - and wired back to the host when a block is replaced (docs/records/0096-cpp-mcu-core.md #29).
"""

import pytest

pytest.importorskip("rp2040py.native._gpio_pin", reason="the native pins are not built")

from rp2040py.native._gpio_pin import GPIOPin, GPIOPinBank
from rp2040py.native._io import RPIO
from rp2040py.native._pads import RPPADS
from rp2040py.rp2040 import RP2040

SIO = 0xD0000000
GPIO_OUT, GPIO_OE, GPIO_HI_OUT, GPIO_HI_OE = 0x10, 0x20, 0x30, 0x40
IO = 0x40014000


def _native_chip():
    chip = RP2040()
    if not isinstance(chip.gpio[0], GPIOPin):
        pytest.skip("this build runs the pure-Python pins")
    return chip


def test_the_chips_pins_are_slots_of_two_shared_banks():
    chip = _native_chip()
    assert isinstance(chip._gpio_bank, GPIOPinBank) and isinstance(chip._qspi_bank, GPIOPinBank)
    assert len(chip._gpio_bank) == 30 and len(chip._qspi_bank) == 6
    assert all(chip._gpio_bank.shells[i] is chip.gpio[i] for i in range(30))
    assert all(chip._qspi_bank.shells[i] is chip.qspi[i] for i in range(6))
    assert isinstance(chip.io_bank0, RPIO) and isinstance(chip.io_qspi, RPIO)
    assert isinstance(chip.pads_bank0, RPPADS) and isinstance(chip.pads_qspi, RPPADS)


def test_the_bank_answers_for_all_its_pins_at_once():
    chip = _native_chip()
    chip.gpio[3].set_input_value(True)
    chip.gpio[7].set_input_value(True)
    assert chip._gpio_bank.input_values() == (1 << 3) | (1 << 7) == chip.gpio_values
    assert not chip._gpio_bank.any_irq_value()
    chip.write_uint32(IO + 0x100, 0x2 << 12)  # pin 3: level high enabled
    assert chip._gpio_bank.any_irq_value()


def test_a_pin_without_a_bank_gets_one_of_its_own():
    chip = _native_chip()
    lone = GPIOPin(chip, 5)
    assert lone.index == 5 and lone.value.name == "INPUT_PULL_DOWN"  # PADS_BANK0 resets to 0x56
    lone.pad_value = 0x4C  # IE | PUE | PDE: pulled both ways
    assert lone.value.name == "INPUT_BUS_KEEPER"
    with pytest.raises(ValueError):
        GPIOPin(chip, 4, bank=chip._gpio_bank)  # the slot already has its pin
    with pytest.raises(ValueError):
        GPIOPin(chip, 30, bank=chip._gpio_bank)  # no such slot


def test_the_sio_registers_reach_the_pins_with_no_python_in_between():
    chip = _native_chip()
    chip.write_uint32(IO + 8 * 2 + 4, 5)  # GPIO2: SIO
    chip.write_uint32(SIO + GPIO_OE, 1 << 2)
    chip.write_uint32(SIO + GPIO_OUT, 1 << 2)
    assert chip.gpio[2].value.name == "HIGH" and chip.gpio[2].output_value
    # the QSPI bank is wired to GPIO_HI_*
    chip.write_uint32(0x40018000 + 8 * 1 + 4, 5)  # QSPI SS: SIO
    chip.write_uint32(SIO + GPIO_HI_OE, 1 << 1)
    chip.write_uint32(SIO + GPIO_HI_OUT, 1 << 1)
    assert chip.qspi[1].value.name == "HIGH"
