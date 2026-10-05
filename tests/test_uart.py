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


UARTDR = 0x00
UARTIMSC = 0x38
UARTMIS = 0x40
UARTICR = 0x44
UARTTXINTR = 1 << 5


def test_tx_interrupt_is_not_raised_by_an_empty_fifo_alone(rp2040_factory):
    uart = RPUART(rp2040_factory(), "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    uart.write_uint32(UARTIMSC, UARTTXINTR)
    assert uart.read_uint32(UARTMIS) == 0


def test_a_written_byte_raises_the_tx_interrupt_and_icr_clears_it_for_good(rp2040_factory):
    uart = RPUART(rp2040_factory(), "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    sent = []
    uart.on_byte = sent.append
    uart.write_uint32(UARTIMSC, UARTTXINTR)
    uart.write_uint32(UARTDR, 0x41)
    assert sent == [0x41]
    assert uart.read_uint32(UARTMIS) == UARTTXINTR
    uart.write_uint32_atomic(UARTICR, UARTTXINTR, 0)  # the bus entry, as the guest writes it
    assert uart.read_uint32(UARTMIS) == 0  # the old code re-asserted it immediately: an IRQ storm
