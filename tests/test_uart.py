from rp2040py.peripherals.uart import RPUART, IUARTDMAChannels

UARTIBRD = 0x24
UARTFBRD = 0x28
OFFSET_UARTLCR_H = 0x2C


def test_word_length_based_on_uartlcr_h(rp2040_factory):
    rp2040 = rp2040_factory()
    uart = RPUART(rp2040, "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    uart.write_uint32(OFFSET_UARTLCR_H, 0x70)
    assert uart.word_length == 8


def test_baud_rate_based_on_uartibrd_uartfbrd(rp2040_factory):
    rp2040 = rp2040_factory()
    uart = RPUART(rp2040, "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    uart.write_uint32(UARTIBRD, 67)  # Values taken from example in section 4.2.7.1. of the datasheet
    uart.write_uint32(UARTFBRD, 52)
    assert uart.baud_rate == 115207
