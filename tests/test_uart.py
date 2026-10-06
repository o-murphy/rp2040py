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
UARTCR = 0x30
UART_ENABLED = 0x301  # UARTEN | TXE | RXE: a disabled UART neither sends nor receives (RP2040 datasheet, UARTCR)


def test_tx_interrupt_is_not_raised_by_an_empty_fifo_alone(rp2040_factory):
    uart = RPUART(rp2040_factory(), "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    uart.write_uint32(UARTIMSC, UARTTXINTR)
    assert uart.read_uint32(UARTMIS) == 0


def test_a_written_byte_raises_the_tx_interrupt_and_icr_clears_it_for_good(rp2040_factory):
    uart = RPUART(rp2040_factory(), "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    sent = []
    uart.on_byte = sent.append
    uart.write_uint32(UARTCR, UART_ENABLED)
    uart.write_uint32(UARTIMSC, UARTTXINTR)
    uart.write_uint32(UARTDR, 0x41)
    assert sent == [0x41]
    assert uart.read_uint32(UARTMIS) == UARTTXINTR
    uart.write_uint32_atomic(UARTICR, UARTTXINTR, 0)  # the bus entry, as the guest writes it
    assert uart.read_uint32(UARTMIS) == 0  # the old code re-asserted it immediately: an IRQ storm


UARTRSR = 0x04
UARTILPR = 0x20
UARTIFLS = 0x34
UARTDMACR = 0x48
UARTRIS = 0x3C
UARTOEINTR = 1 << 10


def _fresh(rp2040_factory):
    rp2040 = rp2040_factory()
    dreqs: list[tuple[int, bool]] = []

    class _Dma:
        def set_dreq(self, channel):
            dreqs.append((channel, True))

        def clear_dreq(self, channel):
            dreqs.append((channel, False))

    rp2040.dma = _Dma()
    return RPUART(rp2040, "UART", 0, IUARTDMAChannels(rx=7, tx=8)), dreqs


def test_a_disabled_uart_sends_and_receives_nothing(rp2040_factory):
    uart, _dreqs = _fresh(rp2040_factory)
    sent = []
    uart.on_byte = sent.append
    uart.write_uint32(UARTDR, 0x41)  # UARTEN is 0 after reset
    uart.feed_byte(0x42)
    assert sent == [] and uart.read_uint32(0x18) & (1 << 4)  # nothing sent; RXFE still set
    uart.write_uint32(UARTCR, 1 | (1 << 9))  # UARTEN | RXE, but the transmitter is off
    uart.write_uint32(UARTDR, 0x41)
    uart.feed_byte(0x42)
    assert sent == [] and uart.read_uint32(UARTDR) == 0x42


def test_cr_and_lcr_h_keep_only_the_bits_that_exist(rp2040_factory):
    uart, _dreqs = _fresh(rp2040_factory)
    uart.write_uint32(UARTCR, 0xFFFFFFFF)
    assert uart.read_uint32(UARTCR) == 0xFF87  # 15:7 and 2:0; 6:3 are reserved
    uart.write_uint32(OFFSET_UARTLCR_H, 0xFFFFFFFF)
    assert uart.read_uint32(OFFSET_UARTLCR_H) == 0xFF


def test_ifls_ilpr_and_dmacr_read_back_what_the_datasheet_says_they_hold(rp2040_factory):
    uart, _dreqs = _fresh(rp2040_factory)
    assert uart.read_uint32(UARTIFLS) == 0x12 and uart.read_uint32(UARTILPR) == 0 and uart.read_uint32(UARTDMACR) == 0
    uart.write_uint32(UARTIFLS, 0xFFFFFFFF)
    uart.write_uint32(UARTILPR, 0xFFFFFFFF)
    uart.write_uint32(UARTDMACR, 0xFFFFFFFF)
    assert (uart.read_uint32(UARTIFLS), uart.read_uint32(UARTILPR), uart.read_uint32(UARTDMACR)) == (0x3F, 0xFF, 0x7)


def test_the_dma_requests_follow_dmacr_and_the_enables(rp2040_factory):
    uart, dreqs = _fresh(rp2040_factory)
    uart.write_uint32(UARTCR, UART_ENABLED)
    assert dreqs[-2:] == [(8, False), (7, False)]  # TXDMAE and RXDMAE are 0: nothing asks
    uart.write_uint32(UARTDMACR, 0x3)  # TXDMAE | RXDMAE
    assert dreqs[-2:] == [(8, True), (7, False)]  # the transmit FIFO never fills; the receive FIFO is empty
    uart.feed_byte(0x42)
    assert dreqs[-2:] == [(8, True), (7, True)]
    uart.read_uint32(UARTDR)
    assert dreqs[-2:] == [(8, True), (7, False)]
    uart.write_uint32(UARTCR, 0x201)  # the transmitter is off
    assert dreqs[-2:] == [(8, False), (7, False)]


def test_dmaonerr_holds_the_receive_request_back_while_an_error_interrupt_is_up(rp2040_factory):
    uart, dreqs = _fresh(rp2040_factory)
    uart.write_uint32(UARTCR, UART_ENABLED)
    uart.write_uint32(UARTDMACR, 0x1 | 0x4)  # RXDMAE | DMAONERR
    for byte in range(33):  # the 33rd arrives with the 32-entry FIFO full: an overrun
        uart.feed_byte(byte)
    assert uart.read_uint32(UARTRSR) == 0x8 and uart.read_uint32(UARTRIS) & UARTOEINTR
    assert dreqs[-1] == (7, False)  # data is waiting, but the error interrupt holds the request back
    uart.write_uint32_atomic(UARTICR, UARTOEINTR, 0)
    assert dreqs[-1] == (7, True)
    uart.write_uint32(UARTRSR, 0)  # UARTECR
    assert uart.read_uint32(UARTRSR) == 0


def test_an_overrun_drops_the_byte_and_sets_oe_and_its_interrupt(rp2040_factory):
    uart, _dreqs = _fresh(rp2040_factory)
    uart.write_uint32(UARTCR, UART_ENABLED)
    for byte in range(32):
        uart.feed_byte(byte)
    assert uart.read_uint32(UARTRSR) == 0 and not uart.read_uint32(UARTRIS) & UARTOEINTR
    uart.feed_byte(0xEE)
    assert uart.read_uint32(UARTRSR) == 0x8 and uart.read_uint32(UARTRIS) & UARTOEINTR
    assert [uart.read_uint32(UARTDR) for _ in range(32)] == list(range(32))  # the FIFO kept its contents
