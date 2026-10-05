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
