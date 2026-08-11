"""Unit tests for `rp2040py.external.led_mock.LEDMock` - the generic `ExternalDevice` validation
vehicle from CYW43_WIFI_BACKLOG.md's step 1 ("prove the ExternalDevice pattern on something
already understood")."""

from rp2040py.external.led_mock import LEDMock
from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.rp2040 import RP2040


def _drive_gpio_high(rp2040: RP2040, gpio: int, high: bool) -> None:
    """Mirrors what real firmware does via machine.Pin(gpio, Pin.OUT).value(x): select the SIO
    function, enable output, then flip the SIO GPIO_OUT bit - the same path sio.py's own
    GPIO_OUT/GPIO_OUT_SET/GPIO_OUT_CLR register writes drive, triggering check_for_updates()."""
    pin = rp2040.gpio[gpio]
    pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
    rp2040.sio.gpio_output_enable |= 1 << gpio
    if high:
        rp2040.sio.gpio_value |= 1 << gpio
    else:
        rp2040.sio.gpio_value &= ~(1 << gpio)
    pin.check_for_updates()


def test_attach_does_not_report_on_before_any_write():
    rp2040 = RP2040()
    led = LEDMock(gpio=25)
    led.attach(rp2040)
    assert led.on is False
    assert led.toggle_count == 0


def test_driving_the_pin_high_sets_on_and_counts_a_toggle():
    rp2040 = RP2040()
    led = LEDMock(gpio=25)
    led.attach(rp2040)

    _drive_gpio_high(rp2040, 25, True)

    assert led.on is True
    assert led.toggle_count == 1


def test_toggling_on_then_off_counts_two_toggles():
    rp2040 = RP2040()
    led = LEDMock(gpio=25)
    led.attach(rp2040)

    _drive_gpio_high(rp2040, 25, True)
    _drive_gpio_high(rp2040, 25, False)

    assert led.on is False
    assert led.toggle_count == 2


def test_driving_the_same_value_twice_does_not_double_count():
    rp2040 = RP2040()
    led = LEDMock(gpio=25)
    led.attach(rp2040)

    _drive_gpio_high(rp2040, 25, True)
    _drive_gpio_high(rp2040, 25, True)

    assert led.on is True
    assert led.toggle_count == 1


def test_only_watches_its_own_gpio():
    rp2040 = RP2040()
    led = LEDMock(gpio=25)
    led.attach(rp2040)

    _drive_gpio_high(rp2040, 2, True)  # a different pin entirely

    assert led.on is False
    assert led.toggle_count == 0
