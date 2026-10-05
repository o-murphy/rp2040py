"""Behaviours of the SPI (PL022) that the reference used to get wrong and that the differential (tests/test_spi_diff.py) now holds both implementations to.

Each test fails on the code before its fix. They run on whichever build is selected - the pure-Python block or the C++ one - through the public API only.
"""

from rp2040py.irq import IRQ

SPI0_BASE = 0x4003C000
SSPCR0, SSPCR1, SSPIMSC, SSPRIS, SSPICR = 0x000, 0x004, 0x014, 0x018, 0x020
SSPRTINTR, SSPRORINTR = 1 << 1, 1 << 0
NVIC_ISPR, NVIC_ICPR = 0xE000E200, 0xE000E280


def _line_is_pending(chip) -> bool:
    return bool(chip.read_uint32(NVIC_ISPR) & (1 << int(IRQ.SPI0)))


def test_an_rx_overrun_raises_the_interrupt_line_at_once(rp2040_factory):
    chip = rp2040_factory()
    spi = chip.spi[0]
    spi.on_transmit = lambda value: None  # a device that never answers: the completions below are the test's own
    chip.write_uint32(SPI0_BASE + SSPIMSC, SSPRORINTR)
    for value in range(8):
        spi.complete_transmit(value)  # fills the 8-entry RX FIFO
    assert not _line_is_pending(chip)
    spi.complete_transmit(99)  # one too many: an overrun
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & SSPRORINTR
    assert _line_is_pending(chip)  # it used to wait for some other change of the status


def test_master_mode_is_sspcr1s_ms_bit_not_sspcr0s(rp2040_factory):
    chip = rp2040_factory()
    spi = chip.spi[0]
    assert spi.master_mode  # reset: MS clear is master
    chip.write_uint32(SPI0_BASE + SSPCR0, 1 << 2)  # bit 2 of SSPCR0 is part of the data size, not the mode
    assert spi.master_mode
    chip.write_uint32(SPI0_BASE + SSPCR1, 1 << 2)  # SSPCR1.MS: slave
    assert not spi.master_mode


def test_icr_clears_the_bits_the_bus_passed_whatever_the_alias(rp2040_factory):
    """The same rule as the UART's and the DMA's write-1-to-clear registers: what a write clears is the raw value of the write, not the alias-decoded value (an
    alias decodes against a read of ICR, which is write-only and reads as all ones here)."""
    chip = rp2040_factory()
    spi = chip.spi[0]
    spi._int_raw = SSPRTINTR | SSPRORINTR
    chip.write_uint32(SPI0_BASE + 0x3000 + SSPICR, SSPRORINTR)  # the CLR alias: clear ROR
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & (SSPRTINTR | SSPRORINTR) == SSPRTINTR
    chip.write_uint32(SPI0_BASE + SSPICR, SSPRTINTR)
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & (SSPRTINTR | SSPRORINTR) == 0
