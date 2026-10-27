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
    assert spi.snapshot()["master_mode"]  # reset: MS clear is master
    chip.write_uint32(SPI0_BASE + SSPCR0, 1 << 2)  # bit 2 of SSPCR0 is part of the data size, not the mode
    assert spi.snapshot()["master_mode"]
    chip.write_uint32(SPI0_BASE + SSPCR1, 1 << 2)  # SSPCR1.MS: slave
    assert not spi.snapshot()["master_mode"]


def test_icr_clears_the_bits_the_bus_passed_whatever_the_alias(rp2040_factory):
    """The same rule as the UART's and the DMA's write-1-to-clear registers: what a write clears is the raw value of the write, not the alias-decoded value (an
    alias decodes against a read of ICR, which is write-only and reads as all ones here)."""
    chip = rp2040_factory()
    spi = chip.spi[0]
    spi.on_transmit = lambda value: None
    for value in range(9):
        spi.complete_transmit(value)  # eight fill the RX FIFO, the ninth is an overrun: ROR is raised
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & SSPRORINTR
    chip.write_uint32(
        SPI0_BASE + 0x3000 + SSPICR, SSPRTINTR
    )  # the CLR alias, raw value RT: ROR is not among the bits written, so it stays
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & SSPRORINTR
    chip.write_uint32(SPI0_BASE + 0x3000 + SSPICR, SSPRORINTR)  # the CLR alias, raw value ROR: cleared
    assert chip.read_uint32(SPI0_BASE + SSPRIS) & (SSPRTINTR | SSPRORINTR) == 0


SSPDR, SSPDMACR = 0x008, 0x024
SSE, LBM = 1 << 1, 1 << 0
DREQ_SPI0_TX, DREQ_SPI0_RX = 16, 17


def test_reserved_bits_read_zero_and_ris_resets_to_the_tx_interrupt(rp2040_factory):
    chip = rp2040_factory()
    assert chip.read_uint32(SPI0_BASE + SSPRIS) == 1 << 3  # datasheet: SSPRIS resets to 0x8 (the TX FIFO is empty)
    for offset, kept in ((SSPCR0, 0xFFFF), (SSPCR1, 0xF), (SSPIMSC, 0xF), (SSPDMACR, 0x3)):
        chip.write_uint32(SPI0_BASE + offset, 0xFFFFFFFF)
        assert chip.read_uint32(SPI0_BASE + offset) == kept, hex(offset)


def test_a_disabled_ssp_sends_nothing_until_enabled(rp2040_factory):
    chip = rp2040_factory()
    spi = chip.spi[0]
    sent = []
    spi.on_transmit = sent.append  # a device that holds the word
    chip.write_uint32(SPI0_BASE + SSPCR0, 0x7)  # 8-bit words
    chip.write_uint32(SPI0_BASE + SSPDR, 0x12)
    chip.write_uint32(SPI0_BASE + SSPDR, 0x34)
    assert sent == []  # the FIFO can be primed while disabled
    chip.write_uint32(SPI0_BASE + SSPCR1, SSE)
    assert sent == [0x12]


def test_loop_back_returns_the_transmitted_word(rp2040_factory):
    chip = rp2040_factory()
    spi = chip.spi[0]
    sent = []
    spi.on_transmit = sent.append
    chip.write_uint32(SPI0_BASE + SSPCR0, 0x7)
    chip.write_uint32(SPI0_BASE + SSPCR1, SSE | LBM)
    chip.write_uint32(SPI0_BASE + SSPDR, 0x1A5)
    assert sent == []
    assert chip.read_uint32(SPI0_BASE + SSPDR) == 0xA5


def test_dma_requests_need_the_ssp_enabled_and_the_dma_enable(rp2040_factory):
    chip = rp2040_factory()

    def asked() -> tuple[bool, bool]:
        asserted = chip.dma.snapshot()["dreq"]
        return DREQ_SPI0_TX in asserted, DREQ_SPI0_RX in asserted

    chip.write_uint32(SPI0_BASE + SSPDMACR, 3)
    assert asked() == (False, False)  # DMA enabled, SSP not: nothing asked for
    chip.write_uint32(SPI0_BASE + SSPCR1, SSE)
    assert asked() == (True, False)  # TX has room, RX has nothing
    chip.write_uint32(SPI0_BASE + SSPDMACR, 0)
    assert asked() == (False, False)
    chip.write_uint32(SPI0_BASE + SSPDMACR, 1)
    chip.spi[0].complete_transmit(7)  # a word arrives
    assert asked() == (False, True)
    chip.write_uint32(SPI0_BASE + SSPCR1, 0)
    assert asked() == (False, False)


SSPDR, SSPSR, SSPCPSR = 0x008, 0x00C, 0x010
BSY, RNE = 1 << 4, 1 << 2


def _configured(rp2040_factory, *, clk_peri=125e6, cpsdvsr=2, scr=0, dss=7):
    chip = rp2040_factory()
    chip.clk_peri = clk_peri
    chip.write_uint32(SPI0_BASE + SSPCPSR, cpsdvsr)
    chip.write_uint32(SPI0_BASE + SSPCR0, (scr << 8) | dss)
    chip.write_uint32(SPI0_BASE + SSPCR1, 1 << 1)  # SSE
    return chip


def test_a_word_is_busy_for_data_bits_times_the_bit_clock_period(rp2040_factory):
    # "SSPCLKOUT = SSPCLK / (CPSDVSR * (1 + SCR))" (datasheet 4.4.3.6): 125 MHz / (2 * (1 + 1)) = 31.25 MHz, 32 ns a bit, 8 bits = 256 ns
    chip = _configured(rp2040_factory, scr=1)
    chip.write_uint32(SPI0_BASE + SSPDR, 0xA5)
    assert chip.read_uint32(SPI0_BASE + SSPSR) & BSY
    assert not chip.read_uint32(SPI0_BASE + SSPSR) & RNE  # the answer is not there before the last bit
    chip.clock.tick(255)
    assert chip.read_uint32(SPI0_BASE + SSPSR) & BSY
    chip.clock.tick(1)
    status = chip.read_uint32(SPI0_BASE + SSPSR)
    assert not status & BSY and status & RNE
    assert chip.read_uint32(SPI0_BASE + SSPDR) == 0  # the default device answers 0


def test_a_burst_takes_the_sum_of_its_words(rp2040_factory):
    chip = _configured(rp2040_factory, cpsdvsr=4, dss=15)  # 125 MHz / 4: 32 ns a bit, 16 bits = 512 ns a word
    for value in range(4):
        chip.write_uint32(SPI0_BASE + SSPDR, value)
    chip.clock.tick(4 * 512 - 1)
    assert chip.read_uint32(SPI0_BASE + SSPSR) & BSY
    chip.clock.tick(1)
    assert not chip.read_uint32(SPI0_BASE + SSPSR) & BSY


def test_a_slow_device_holds_the_transfer_after_the_frame(rp2040_factory):
    chip = _configured(rp2040_factory)
    spi = chip.spi[0]
    spi.on_transmit = lambda value: None  # answers whenever the test says
    chip.write_uint32(SPI0_BASE + SSPDR, 1)
    chip.clock.tick(10_000)  # far past the frame
    assert chip.read_uint32(SPI0_BASE + SSPSR) & BSY
    spi.complete_transmit(0x42)
    assert not chip.read_uint32(SPI0_BASE + SSPSR) & BSY
    assert chip.read_uint32(SPI0_BASE + SSPDR) == 0x42


def test_a_fast_device_is_held_until_the_frame_ends(rp2040_factory):
    chip = _configured(rp2040_factory)
    spi = chip.spi[0]
    spi.on_transmit = lambda value: spi.complete_transmit(0x99)  # answers from inside the callback
    chip.write_uint32(SPI0_BASE + SSPDR, 1)
    assert not chip.read_uint32(SPI0_BASE + SSPSR) & RNE
    chip.clock.tick(10_000)
    assert chip.read_uint32(SPI0_BASE + SSPDR) == 0x99
