"""SHIFTCTRL.FJOIN_TX / FJOIN_RX (RP2040 datasheet 3.5.3, SHIFTCTRL), through a whole chip on whichever PIO the build selects.

"When one FIFO is increased in size (from 4 to 8), the other FIFO on that state machine is reduced to zero. For example, if joining to TX, the RX FIFO is unavailable, and any PUSH
instruction will stall. The RX FIFO will appear both RXFULL and RXEMPTY in the FSTAT register. The converse is true if joining to RX [...]. Setting both FJOIN_RX and FJOIN_TX makes both
FIFOs unavailable." and, for the bits themselves, "FIFOs are flushed when this bit is changed".
"""

from rp2040py.peripherals.pio_registers import DREQ_RX0, DREQ_TX0

PIO0 = 0x50200000
FSTAT, FDEBUG, FLEVEL, TXF0, RXF0 = 0x04, 0x08, 0x0C, 0x10, 0x20
SM0_SHIFTCTRL, SM0_INSTR = 0xD0, 0xD8
FJOIN_RX, FJOIN_TX = 1 << 31, 1 << 30
RXFULL, RXEMPTY, TXFULL, TXEMPTY = 1 << 0, 1 << 8, 1 << 16, 1 << 24
TXOVER, RXUNDER = 1 << 16, 1 << 8
PUSH_NOBLOCK, PUSH_BLOCK, PULL_BLOCK = 0x8000, 0x8020, 0x80A0


def _w(chip, offset, value):
    chip.write_uint32(PIO0 + offset, value)


def _r(chip, offset):
    return chip.read_uint32(PIO0 + offset)


def test_a_default_state_machine_has_four_entries_each_way(rp2040_factory):
    chip = rp2040_factory()
    for value in range(6):
        _w(chip, TXF0, value)
    assert (_r(chip, FLEVEL) & 0xF) == 4
    assert _r(chip, FSTAT) & TXFULL
    assert _r(chip, FDEBUG) & TXOVER  # the fifth and sixth were dropped


def test_fjoin_tx_gives_eight_tx_entries_and_no_rx_fifo(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, SM0_SHIFTCTRL, FJOIN_TX)
    status = _r(chip, FSTAT)
    assert status & RXFULL and status & RXEMPTY and status & TXEMPTY and not status & TXFULL
    for value in range(8):
        _w(chip, TXF0, value)
    assert (_r(chip, FLEVEL) & 0xF) == 8 and _r(chip, FSTAT) & TXFULL
    assert not _r(chip, FDEBUG) & TXOVER
    _w(chip, TXF0, 99)
    assert _r(chip, FDEBUG) & TXOVER
    assert _r(chip, RXF0) == 0 and _r(chip, FDEBUG) & RXUNDER  # nothing is ever there to read
    _w(chip, SM0_INSTR, PUSH_NOBLOCK)  # nowhere to push to: dropped
    _w(chip, SM0_INSTR, PUSH_BLOCK)
    assert chip.pio[0].snapshot()["machines"][0]["waiting"]  # "any PUSH instruction will stall"


def test_fjoin_rx_gives_eight_rx_entries_and_no_tx_fifo(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, SM0_SHIFTCTRL, FJOIN_RX)
    status = _r(chip, FSTAT)
    assert status & TXFULL and status & TXEMPTY and status & RXEMPTY and not status & RXFULL
    _w(chip, TXF0, 1)
    assert _r(chip, FDEBUG) & TXOVER  # the TX FIFO reads as full: the word is refused
    for _ in range(8):
        _w(chip, SM0_INSTR, PUSH_NOBLOCK)
    assert ((_r(chip, FLEVEL) >> 4) & 0xF) == 8 and _r(chip, FSTAT) & RXFULL
    _w(chip, SM0_INSTR, PULL_BLOCK)
    assert chip.pio[0].snapshot()["machines"][0]["waiting"]  # nothing to pull, ever


def test_both_joins_leave_neither_fifo(rp2040_factory):
    chip = rp2040_factory()
    _w(chip, SM0_SHIFTCTRL, FJOIN_RX | FJOIN_TX)
    status = _r(chip, FSTAT)
    assert status & (TXFULL | TXEMPTY | RXFULL | RXEMPTY) == (TXFULL | TXEMPTY | RXFULL | RXEMPTY)


def test_changing_fjoin_flushes_both_fifos_and_an_unchanged_write_does_not(rp2040_factory):
    chip = rp2040_factory()
    for value in range(3):
        _w(chip, TXF0, value)
    _w(chip, SM0_INSTR, PUSH_NOBLOCK)
    _w(chip, SM0_SHIFTCTRL, FJOIN_TX)
    assert _r(chip, FSTAT) & TXEMPTY and (_r(chip, FLEVEL) & 0xF) == 0
    _w(chip, TXF0, 5)
    _w(chip, SM0_SHIFTCTRL, FJOIN_TX | (1 << 16))  # AUTOPUSH: the FJOIN bits did not change
    assert (_r(chip, FLEVEL) & 0xF) == 1
    _w(chip, SM0_SHIFTCTRL, 0)
    assert (_r(chip, FLEVEL) & 0xF) == 0 and not _r(chip, FSTAT) & (RXFULL | TXFULL)


def test_the_dma_requests_follow_the_joined_fifos(rp2040_factory):
    chip = rp2040_factory()
    events = []

    class _Dma:
        def set_dreq(self, channel):
            events.append((int(channel), 1))

        def clear_dreq(self, channel):
            events.append((int(channel), 0))

    chip.dma = _Dma()
    tx, rx = int(DREQ_TX0[0]), int(DREQ_RX0[0])
    _w(chip, SM0_SHIFTCTRL, FJOIN_TX)
    assert (tx, 1) in events and (rx, 0) in events  # TX has room, RX is "empty": no request
    events.clear()
    for value in range(8):
        _w(chip, TXF0, value)
    assert events[-1] == (tx, 0)  # eight words fill it: the request drops only now, not at four
    assert (tx, 0) not in events[:-1]
    events.clear()
    _w(chip, SM0_SHIFTCTRL, FJOIN_RX)
    assert (tx, 0) in events  # the TX FIFO is gone, "full": no request, whatever the DMA does
