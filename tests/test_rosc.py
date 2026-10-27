"""The ring oscillator (RP2040 datasheet 2.17) through a whole chip, on whichever ROSC the build selects.

The register-level behaviour is pinned by tests/test_rosc_diff.py (reference against the facade, every mutant of the reference caught) and tests/cpp/test_rosc.cpp; this file is what needs
a chip around the block: the clock tree following DIV and FREQ_RANGE ("The ROSC ... can directly provide a high speed clock to the cores"; at boot the chip runs from it), the counter
against the chip's clock, RANDOMBIT's default source, and the power-on reset against the watchdog reset (`PSM.WDSEL`'s ROSC bit, which `watchdog_reboot()` clears).
"""

ROSC = 0x40060000
CTRL, FREQA, DORMANT, DIV, PHASE, STATUS, RANDOMBIT, COUNT = 0x00, 0x04, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
BADWRITE = 1 << 24


def _r(chip, offset):
    return chip.read_uint32(ROSC + offset)


def _w(chip, offset, value):
    chip.write_uint32(ROSC + offset, value)


def test_the_registers_at_the_time_firmware_runs(rp2040_factory):
    chip = rp2040_factory()
    assert _r(chip, CTRL) == 0x00FABAA0  # ENABLE is ENABLE, FREQ_RANGE 0xAA0
    assert _r(chip, DIV) == 0xAB0  # "this register resets to div=16"
    assert _r(chip, DORMANT) == 0x77616B65  # WAKE
    assert _r(chip, PHASE) == 0x8
    assert (
        _r(chip, STATUS) == 0x80011000
    )  # STABLE | DIV_RUNNING | ENABLED: it "is started automatically during RP2040 power up"
    assert chip.rosc_freq == 6.5e6  # "During boot the ROSC runs at a nominal 6.5MHz"


def test_div_and_freq_range_retune_the_chip_clocks(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, DIV, 0xAA0 + 8)  # half the divisor: twice the frequency
    assert chip.rosc_freq == 13e6
    assert chip.clocks.snapshot()["ref_freq"] == 13e6  # clk_ref still runs from the ROSC at reset
    assert chip.clk_sys == 13e6  # and so does clk_sys
    _w(chip, DIV, 0xAA0)  # "div = 0 divides by 32"
    assert chip.rosc_freq == 3.25e6
    _w(chip, DIV, 0xAA0 + 16)
    _w(chip, CTRL, (0xFAB << 12) | 0xFA7)  # HIGH: "2 times the LOW range"
    assert chip.rosc_freq == 13e6


def test_invalid_writes_set_badwrite_which_is_write_one_to_clear(rp2040_factory):
    chip = rp2040_factory()
    for offset, value in ((CTRL, 0xAA0), (DIV, 0x123), (FREQA, 0x1234), (DORMANT, 0x1), (PHASE, 0x0)):
        _w(chip, STATUS, BADWRITE)
        assert not _r(chip, STATUS) & BADWRITE
        _w(chip, offset, value)
        assert _r(chip, STATUS) & BADWRITE, hex(offset)
    _w(chip, STATUS, BADWRITE)
    assert _r(chip, STATUS) == 0x80011000


def test_the_counter_counts_down_at_the_rosc_frequency_and_stops_at_zero(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, COUNT, 200)
    chip.clock.tick(10_000)  # 10 us at 6.5 MHz: 65 cycles
    assert _r(chip, COUNT) == 135
    chip.clock.tick(1_000_000)
    assert _r(chip, COUNT) == 0
    chip.clock.tick(1_000_000)
    assert _r(chip, COUNT) == 0


def test_randombit_is_a_bit_that_varies(rp2040_factory):
    chip = rp2040_factory()
    bits = [_r(chip, RANDOMBIT) for _ in range(64)]
    assert set(bits) == {0, 1}
    assert _r(chip, RANDOMBIT) in (0, 1)


def test_a_power_on_reset_restores_the_rosc_and_a_watchdog_reset_does_not(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, DIV, 0xAA0 + 8)
    chip.enter_reset(from_watchdog=True)  # watchdog_reboot(): "everything apart from ROSC and XOSC"
    assert _r(chip, DIV) == 0xAA8 and chip.rosc_freq == 13e6
    chip.enter_reset()  # the RUN pin / power on
    assert _r(chip, DIV) == 0xAB0 and chip.rosc_freq == 6.5e6
