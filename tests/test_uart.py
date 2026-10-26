from rp2040py.peripherals.uart import RPUART, IUARTDMAChannels

UARTIBRD = 0x24
UARTFBRD = 0x28
OFFSET_UARTLCR_H = 0x2C


def test_word_length_based_on_uartlcr_h(rp2040_factory):
    rp2040 = rp2040_factory()
    uart = RPUART(rp2040, "UART", 0, IUARTDMAChannels(rx=0, tx=0))
    uart.write_uint32(OFFSET_UARTLCR_H, 0x70)
    assert (uart.snapshot()["line_ctrl"] >> 5) & 0x3 == 0x3  # WLEN = 0b11: 8 bits per character


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

    rp2040.dreq_tap = lambda number, asserted: dreqs.append(
        (number, asserted)
    )  # (the real DMA gets the requests as well)
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


def test_loopback_feeds_a_written_byte_to_the_receiver(rp2040_factory):
    uart, _dreqs = _fresh(rp2040_factory)
    sent = []
    uart.on_byte = sent.append
    uart.write_uint32(UARTCR, 1 | (1 << 7) | (1 << 8) | (1 << 9))  # UARTEN | LBE | TXE | RXE
    uart.write_uint32(UARTDR, 0x5A)
    assert sent == [] and uart.read_uint32(UARTDR) == 0x5A
    uart.write_uint32(UARTCR, 1 | (1 << 8) | (1 << 9))
    uart.write_uint32(UARTDR, 0x5B)
    assert sent == [0x5B]


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


# --- the transmitter's time (RP2040 datasheet 4.2.3.2.2 "Data transmission or reception", 4.2.6.3 UARTTXINTR, UARTFR) ----------------------------------------------------------

UARTFR = 0x18
UARTLCR_H = 0x2C
FR_TXFF, FR_TXFE, FR_BUSY = 1 << 5, 1 << 7, 1 << 3
LCR_8N1_FEN = 0x70


def _wired_uart(rp2040_factory, *, divisor=1):
    """A UART at a known bit time: clk_peri / (16 x divisor) baud."""
    rp2040 = rp2040_factory()
    uart = rp2040.uart[0]
    sent = []
    uart.on_byte = sent.append
    uart.write_uint32(UARTIBRD, divisor)
    uart.write_uint32(UARTLCR_H, LCR_8N1_FEN)
    uart.write_uint32(UARTCR, UART_ENABLED)
    bit_ns = 16 * divisor * 1e9 / rp2040.clk_peri
    return rp2040, uart, sent, bit_ns


def test_a_character_reaches_the_line_after_ten_bit_times_and_busy_covers_the_frame(rp2040_factory):
    rp2040, uart, sent, bit_ns = _wired_uart(rp2040_factory)
    uart.write_uint32(UARTDR, 0x41)
    assert (
        sent == [] and uart.read_uint32(UARTFR) & FR_BUSY
    )  # "BUSY ... set as soon as the transmit FIFO becomes non-empty"
    rp2040.clock.tick(bit_ns * 9.5)
    assert sent == []
    rp2040.clock.tick(bit_ns * 0.5 + 1)
    assert sent == [0x41] and not uart.read_uint32(UARTFR) & FR_BUSY  # start + 8 data + 1 stop


def test_the_tx_fifo_is_32_deep_and_txff_marks_it_full(rp2040_factory):
    rp2040, uart, sent, bit_ns = _wired_uart(rp2040_factory)
    for value in range(40):
        uart.write_uint32(UARTDR, value)
    assert uart.read_uint32(UARTFR) & FR_TXFF and not uart.read_uint32(UARTFR) & FR_TXFE
    rp2040.clock.tick(bit_ns * 10 * 40)
    assert sent == list(range(33))  # one in the shift register + 32 queued; the rest were lost, as on the chip
    assert uart.read_uint32(UARTFR) & FR_TXFE and not uart.read_uint32(UARTFR) & (FR_TXFF | FR_BUSY)


def test_without_fen_the_tx_fifo_is_a_single_holding_register(rp2040_factory):
    rp2040, uart, sent, bit_ns = _wired_uart(rp2040_factory)
    uart.write_uint32(UARTLCR_H, 0x60)  # WLEN 8, FIFOs off
    for value in (1, 2, 3):
        uart.write_uint32(UARTDR, value)
    assert uart.read_uint32(UARTFR) & FR_TXFF
    rp2040.clock.tick(bit_ns * 30)
    assert sent == [1, 2]


def test_the_tx_interrupt_asserts_as_the_fifo_falls_to_the_trigger_level(rp2040_factory):
    rp2040, uart, _sent, bit_ns = _wired_uart(rp2040_factory)
    for value in range(20):  # one on the wire, 19 queued; TXIFLSEL resets to b010: at or below 16
        uart.write_uint32(UARTDR, value)
    assert not uart.read_uint32(0x3C) & UARTTXINTR
    rp2040.clock.tick(bit_ns * 10 * 3 + 1)  # three characters later: 16 queued
    assert uart.read_uint32(0x3C) & UARTTXINTR
    uart.write_uint32(
        UARTDR, 99
    )  # back above the level: "cleared by writing data ... until it becomes greater than the trigger level"
    uart.write_uint32(UARTDR, 98)
    assert not uart.read_uint32(0x3C) & UARTTXINTR


def test_a_loopback_byte_arrives_in_the_receiver_after_its_frame_time(rp2040_factory):
    rp2040, uart, sent, bit_ns = _wired_uart(rp2040_factory)
    uart.write_uint32(UARTCR, UART_ENABLED | (1 << 7))  # LBE
    uart.write_uint32(UARTDR, 0x5A)
    assert uart.read_uint32(UARTFR) & (1 << 4)  # RXFE: still on the wire
    rp2040.clock.tick(bit_ns * 10 + 1)
    assert not uart.read_uint32(UARTFR) & (1 << 4) and uart.read_uint32(UARTDR) == 0x5A and sent == []
