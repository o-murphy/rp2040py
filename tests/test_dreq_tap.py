"""`chip.dreq_tap`: an observer of the DMA requests that reach the chip's DMA, the same on the pure-Python and the C++ chip (docs/tasks/twin-backend-testing.md).

It sees every request (the redundant ones too) in order, before the DMA gets it, and the DMA still gets it: that is what separates it from standing in for the DMA with a recorder.
"""

import pytest
from utils.twin import new_chip

UART0 = 0x40034000
UARTDMACR, UARTCR, UARTLCR_H = 0x48, 0x30, 0x2C
DREQ_UART0_TX = 20


@pytest.mark.parametrize("kind", ["pure", "default"])
def test_the_tap_sees_the_requests_and_the_dma_still_gets_them(kind):
    chip = new_chip(kind)
    seen: list[tuple[int, bool]] = []
    chip.dreq_tap = lambda number, asserted: seen.append((number, asserted))
    chip.write_uint32(UART0 + UARTLCR_H, 0x70)  # 8 bits, FIFOs enabled
    chip.write_uint32(UART0 + UARTCR, 0x301)  # UARTEN, TXE, RXE
    chip.write_uint32(UART0 + UARTDMACR, 2)  # TXDMAE (bit 1; RXDMAE is bit 0): the TX FIFO has room, so the UART asks
    assert (DREQ_UART0_TX, True) in seen
    assert DREQ_UART0_TX in chip.dma.snapshot()["dreq"]  # the DMA got it as well


@pytest.mark.parametrize("kind", ["pure", "default"])
def test_a_removed_tap_sees_nothing_more(kind):
    chip = new_chip(kind)
    seen: list[tuple[int, bool]] = []
    chip.dreq_tap = lambda number, asserted: seen.append((number, asserted))
    chip.dreq_tap = None
    chip.write_uint32(UART0 + UARTLCR_H, 0x70)
    chip.write_uint32(UART0 + UARTCR, 0x301)
    chip.write_uint32(UART0 + UARTDMACR, 2)
    assert seen == []


def test_both_chips_report_the_same_requests_in_the_same_order():
    logs = []
    for kind in ("pure", "default"):
        chip = new_chip(kind)
        log: list[tuple[int, bool]] = []
        chip.dreq_tap = lambda number, asserted, log=log: log.append((number, asserted))
        chip.write_uint32(UART0 + UARTLCR_H, 0x70)
        chip.write_uint32(UART0 + UARTCR, 0x301)
        chip.write_uint32(UART0 + UARTDMACR, 3)
        chip.write_uint32(UART0 + UARTDMACR, 0)
        logs.append(log)
    assert logs[0] == logs[1] and logs[0]
