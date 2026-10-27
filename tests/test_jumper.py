"""Unit tests for `rp2040py.external.jumper.Jumper` - a wire between two GPIO pads (`tests/target_wiring/rp2.py` of MicroPython: "Connect GPIO0 to GPIO1")."""

import pytest

from rp2040py.external.jumper import Jumper
from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.rp2040 import RP2040


def _drive(rp2040: RP2040, gpio: int, high: bool) -> None:
    """machine.Pin(gpio, Pin.OUT).value(x): the SIO function, the output enabled, the level set."""
    pin = rp2040.gpio[gpio]
    pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
    rp2040.sio.gpio_output_enable |= 1 << gpio
    if high:
        rp2040.sio.gpio_value |= 1 << gpio
    else:
        rp2040.sio.gpio_value &= ~(1 << gpio)
    pin.check_for_updates()


def _let_go(rp2040: RP2040, gpio: int) -> None:
    """machine.Pin(gpio, Pin.IN): the output disabled."""
    rp2040.sio.gpio_output_enable &= ~(1 << gpio)
    rp2040.gpio[gpio].check_for_updates()


def test_a_level_driven_on_one_end_is_read_on_the_other(rp2040_factory):
    rp2040 = rp2040_factory()
    Jumper(0, 1).attach(rp2040)

    _drive(rp2040, 0, True)
    assert rp2040.gpio[1].input_value is True
    _drive(rp2040, 0, False)
    assert rp2040.gpio[1].input_value is False


def test_it_carries_the_level_either_way(rp2040_factory):
    rp2040 = rp2040_factory()
    Jumper(0, 1).attach(rp2040)

    _drive(rp2040, 1, True)
    assert rp2040.gpio[0].input_value is True


def test_the_wire_floats_when_neither_end_drives_it_and_the_pull_decides(rp2040_factory):
    rp2040 = rp2040_factory()
    Jumper(0, 1).attach(rp2040)
    _drive(rp2040, 0, True)
    assert rp2040.gpio[1].input_value is True

    _let_go(rp2040, 0)

    pin = rp2040.gpio[1]
    assert pin.driven is False  # nothing holds it any more
    assert pin.input_value is False  # the pad's reset state: no pull, reads low


def test_a_pin_that_listens_sees_the_edge_on_the_far_end(rp2040_factory):
    rp2040 = rp2040_factory()
    Jumper(0, 1).attach(rp2040)
    pin = rp2040.gpio[1]
    before = pin.irq_status

    _drive(rp2040, 0, True)

    assert pin.irq_status != before  # an edge latched: IRQ_EDGE_HIGH


def test_a_jumper_joins_two_different_pins():
    with pytest.raises(ValueError):
        Jumper(3, 3)
