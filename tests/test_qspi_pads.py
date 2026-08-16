"""PADS_QSPI reset values - see docs/records/0050-qspi-pad-reset-values.md.

The pull-up on `GPIO_QSPI_SS` is not decoration: firmware reads that pad to decide whether the
BOOTSEL button is held, and CircuitPython 10.x polls it in a RAM-resident loop during boot. With
the bank0 pad default (pull-down, input disabled) an undriven SS reads low forever and that loop
never exits.
"""

from rp2040py.qspi_pads import QSPI_PAD_RESET_VALUES
from rp2040py.sio import GPIO_HI_IN

SS = 1
SCLK = 0
SD0 = 2


def test_qspi_pads_use_their_own_reset_values_not_bank0s(rp2040_factory):
    rp2040 = rp2040_factory()
    assert tuple(pin.pad_value for pin in rp2040.qspi) == QSPI_PAD_RESET_VALUES
    # ...and they are genuinely different from the ordinary GPIO default, which is the bug this
    # guards against.
    assert rp2040.qspi[SS].pad_value != rp2040.gpio[0].pad_value


def test_only_qspi_ss_has_a_pull_up_at_reset(rp2040_factory):
    rp2040 = rp2040_factory()
    assert rp2040.qspi[SS].pullup_enabled is True
    assert rp2040.qspi[SS].pulldown_enabled is False
    assert rp2040.qspi[SCLK].pulldown_enabled is True
    assert rp2040.qspi[SCLK].pullup_enabled is False
    assert rp2040.qspi[SD0].pullup_enabled is False
    assert rp2040.qspi[SD0].pulldown_enabled is False


def test_undriven_qspi_ss_reads_high_through_gpio_hi_in(rp2040_factory):
    """The actual thing firmware sees: SIO's GPIO_HI_IN, with nothing driving the pad."""
    rp2040 = rp2040_factory()
    assert rp2040.qspi[SS].input_enable is True, "IE must be set or the pad reads 0 regardless"
    hi_in = rp2040.sio.read_uint32(GPIO_HI_IN)
    assert hi_in & (1 << SS), "BOOTSEL not pressed means SS floats up to 1"
    assert not hi_in & (1 << SCLK)
    assert not hi_in & (1 << SD0)


def test_driving_qspi_ss_low_still_wins_over_the_pull_up(rp2040_factory):
    """What pressing BOOTSEL would look like - the pull-up must not mask an actively driven line."""
    rp2040 = rp2040_factory()
    rp2040.qspi[SS].set_input_value(False)
    assert not rp2040.sio.read_uint32(GPIO_HI_IN) & (1 << SS)
