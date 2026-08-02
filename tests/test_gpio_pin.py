from rp2040py.rp2040 import RP2040

_INPUT_ENABLE = 0x40
_PULLDOWN = 0x4
_PULLUP = 0x8


def _gpio(index: int = 0):
    rp2040 = RP2040()
    return rp2040.gpio[index]


def test_undriven_pin_with_no_pulls_reads_low():
    # Unchanged from before pull resolution existed - no pull configured, nothing driving it.
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE
    assert gpio.input_value is False


def test_undriven_pin_with_pullup_resolves_high():
    # Regression test: firmware reading an undriven, pulled-up-only pin (e.g. Kaluma's GP22
    # "skip auto-run" check via gpio_set_pulls()+gpio_get()) used to always read it as low,
    # indistinguishable from a pin actively driven low - matching real hardware requires
    # resolving the pull instead.
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE | _PULLUP
    assert gpio.input_value is True


def test_undriven_pin_with_pulldown_resolves_low():
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE | _PULLDOWN
    assert gpio.input_value is False


def test_actively_driven_pin_ignores_pullup():
    # An external driver (button harness, another simulated peripheral) pulling the pin low
    # takes priority over a configured pull-up, same as real hardware.
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE | _PULLUP
    gpio.set_input_value(False)
    assert gpio.input_value is False


def test_actively_driven_pin_survives_input_enable_toggling():
    # Regression test: refresh_input() (called whenever input_enable changes, e.g. from a pad
    # register write) used to go through set_input_value() and would incorrectly mark the pin as
    # freshly "driven" using its last raw value - which would have permanently pinned an
    # undriven, pulled-up pin to whatever _raw_input_value happened to default to (False) the
    # moment input_enable was toggled, defeating pull resolution entirely.
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE | _PULLUP
    gpio.refresh_input()
    assert gpio.input_value is True

    gpio.set_input_value(True)
    gpio.pad_value = 0  # disable input_enable
    gpio.pad_value = _INPUT_ENABLE | _PULLUP  # re-enable - refresh_input() fires via RPPADS normally;
    gpio.refresh_input()  # call directly here since we're bypassing the RPPADS register write path
    assert gpio.input_value is True  # still reflects the earlier explicit drive, not a fresh pull-only read


def test_bus_keeper_mode_falls_back_to_raw_value_when_undriven():
    # Both pulls enabled (bus-keeper mode) isn't covered by the pull-resolution fix - falls back
    # to the plain default, same as before.
    gpio = _gpio()
    gpio.pad_value = _INPUT_ENABLE | _PULLUP | _PULLDOWN
    assert gpio.input_value is False
