"""CLOCKS and PLL register behaviour the datasheet pins down (docs/records/0098-datasheet-conformance-audit.md): which bits of the dividers exist, the reset values, the
widths of the PLL registers. Pure Python blocks: the same on both builds."""

CLOCKS = 0x40008000
PLL_SYS = 0x40028000


def test_clock_dividers_reset_to_one_and_ref_usb_adc_have_only_a_two_bit_integer_part(rp2040_factory):
    chip = rp2040_factory()
    for offset in (0x04, 0x10, 0x1C, 0x28, 0x34, 0x40, 0x58, 0x64, 0x70):  # GPOUT0-3, REF, SYS, USB, ADC, RTC: INT = 1
        assert chip.read_uint32(CLOCKS + offset) == 0x100, hex(offset)
    for offset in (0x34, 0x58, 0x64):  # CLK_REF_DIV, CLK_USB_DIV, CLK_ADC_DIV: INT is bits 9:8, the rest is reserved
        chip.write_uint32(CLOCKS + offset, 0xFFFFFFFF)
        assert chip.read_uint32(CLOCKS + offset) == 0x300, hex(offset)
    for offset in (0x04, 0x40, 0x70):  # the others are 24.8 fixed point
        chip.write_uint32(CLOCKS + offset, 0xFFFFFFFF)
        assert chip.read_uint32(CLOCKS + offset) == 0xFFFFFFFF, hex(offset)


def test_the_clocks_registers_that_are_stored_have_their_reset_values_and_widths(rp2040_factory):
    chip = rp2040_factory()
    assert chip.read_uint32(CLOCKS + 0x78) == 0xFF  # CLK_SYS_RESUS_CTRL: TIMEOUT resets to 0xFF
    assert chip.read_uint32(CLOCKS + 0xA0) == 0xFFFFFFFF and chip.read_uint32(CLOCKS + 0xA4) == 0x7FFF  # WAKE_EN0/1
    assert chip.read_uint32(CLOCKS + 0xA8) == 0xFFFFFFFF and chip.read_uint32(CLOCKS + 0xAC) == 0x7FFF  # SLEEP_EN0/1
    for offset, kept in ((0x78, 0x111FF), (0xA4, 0x7FFF), (0xAC, 0x7FFF), (0xBC, 0x1), (0xC0, 0x1)):
        chip.write_uint32(CLOCKS + offset, 0xFFFFFFFF)
        assert chip.read_uint32(CLOCKS + offset) == kept, hex(offset)
    assert chip.read_uint32(CLOCKS + 0xC4) == 1  # INTS = (INTR & INTE) | INTF


def test_pll_registers_keep_only_their_datasheet_bits(rp2040_factory):
    chip = rp2040_factory()
    assert chip.read_uint32(PLL_SYS + 0x0C) == 0x77000  # PRIM: POSTDIV1 = POSTDIV2 = 7
    for offset, kept in ((0x04, 0x2D), (0x08, 0xFFF), (0x0C, 0x77000)):
        chip.write_uint32(PLL_SYS + offset, 0xFFFFFFFF)
        assert chip.read_uint32(PLL_SYS + offset) == kept, hex(offset)
    chip.write_uint32(PLL_SYS + 0x00, 0xFFFFFFFF)  # CS: BYPASS 8 and REFDIV 5:0 (LOCK 31 is read only)
    assert chip.read_uint32(PLL_SYS + 0x00) == 0x80000000 | 0x13F
