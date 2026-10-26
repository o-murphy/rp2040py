"""The DMA requests of a chip reach its DMA block, and `chip.dreq_tap` sees every one of them on the way (docs/records/0096-cpp-mcu-core.md, step 2c; docs/tasks/twin-backend-testing.md).

Written once for both builds: on the pure-Python chip a request is a call of `RPDMA.set_dreq` / `clear_dreq`; on the native one it is a C++ call of `Mcu::set_dreq` into the chip's own DMA block with no
Python between them. The observer is called first, for every request, and does not take it away from the DMA; an observer that raises surfaces from the access that raised the request. Same observable
behaviour either way.
"""

import pytest

from rp2040py.peripherals._dma import DREQChannel
from rp2040py.rp2040 import RP2040

UARTCR, UARTDMACR = 0x30, 0x48
UART_ENABLED_TX, TXDMAE = 0x101, 1 << 1
SSPCR1, SSPDMACR = 0x04, 0x24
SSE, SPI_TXDMAE = 1 << 1, 1 << 1


def _asserted(chip: RP2040) -> set[int]:
    return set(chip.dma.snapshot()["dreq"])


def _request_uart_tx(chip: RP2040) -> None:
    uart = chip.uart[0]
    uart.write_uint32(UARTCR, UART_ENABLED_TX)
    uart.write_uint32(UARTDMACR, TXDMAE)


def _request_spi_tx(chip: RP2040) -> None:
    spi = chip.spi[0]
    spi.write_uint32(SSPCR1, SSE)
    spi.write_uint32(SSPDMACR, SPI_TXDMAE)


def _tapped(chip: RP2040) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    chip.dreq_tap = lambda number, asserted: calls.append((("set" if asserted else "clear"), number))
    return calls


def test_the_observer_sees_the_request_and_the_dma_still_gets_it():
    chip = RP2040()
    calls = _tapped(chip)
    _request_uart_tx(chip)
    assert ("set", int(DREQChannel.DREQ_UART0_TX)) in calls
    assert DREQChannel.DREQ_UART0_TX in _asserted(chip)


def test_the_observer_is_told_to_clear_as_well_as_to_set():
    chip = RP2040()
    _request_spi_tx(chip)
    calls = _tapped(chip)
    chip.spi[0].write_uint32(SSPDMACR, 0)
    assert ("clear", int(DREQChannel.DREQ_SPI0_TX)) in calls
    assert DREQChannel.DREQ_SPI0_TX not in _asserted(chip)


def test_taking_the_observer_away_stops_the_calls_and_not_the_requests():
    chip = RP2040()
    calls = _tapped(chip)
    chip.dreq_tap = None
    _request_uart_tx(chip)
    assert calls == []
    assert DREQChannel.DREQ_UART0_TX in _asserted(chip)


def test_a_failing_observer_surfaces_from_the_access_that_raised_the_request():
    chip = RP2040()

    class Boom(Exception):
        pass

    def tap(number: int, asserted: bool) -> None:
        raise Boom(number)

    chip.dreq_tap = tap
    with pytest.raises(Boom):
        _request_uart_tx(chip)
