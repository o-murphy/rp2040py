"""The DMA requests of the native blocks go straight to the DMA block, and a replaced `dma` still sees them (docs/records/0096-cpp-mcu-core.md, step 2c).

Written once for both builds: on the pure-Python chip a request is a call of `chip.dma.set_dreq` / `clear_dreq`; on the native one it is a C++ call of `Mcu::set_dreq` into the chip's own DMA block with no
Python between them, *unless* the chip is given another DMA (a recorder, a test double) - then that object gets the requests instead, as it always did. Same observable behaviour either way.
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


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def set_dreq(self, channel: int) -> None:
        self.calls.append(("set", int(channel)))

    def clear_dreq(self, channel: int) -> None:
        self.calls.append(("clear", int(channel)))


def test_a_block_asserts_its_request_on_the_dma():
    chip = RP2040()
    assert DREQChannel.DREQ_UART0_TX not in _asserted(chip)
    _request_uart_tx(chip)
    assert DREQChannel.DREQ_UART0_TX in _asserted(chip)
    assert DREQChannel.DREQ_UART0_RX not in _asserted(chip)  # (the pair: TX is not RX)
    assert DREQChannel.DREQ_UART1_TX not in _asserted(chip)


def test_a_pair_that_passes_tx_first_reaches_the_right_numbers():
    chip = RP2040()
    _request_spi_tx(chip)
    assert DREQChannel.DREQ_SPI0_TX in _asserted(chip)
    assert DREQChannel.DREQ_SPI0_RX not in _asserted(chip)


def test_the_pio_requests_are_on_the_dma_from_the_start():
    assert {0, 1, 2, 3, 8, 9, 10, 11} <= _asserted(
        RP2040()
    )  # the TX FIFOs of both blocks are empty, the RX ones too: not full / not empty


def test_a_replaced_dma_gets_the_requests_instead_of_the_native_one():
    chip = RP2040()
    native = chip.dma
    recorder = _Recorder()
    chip.dma = recorder  # type: ignore[assignment]
    _request_uart_tx(chip)
    assert ("set", int(DREQChannel.DREQ_UART0_TX)) in recorder.calls
    assert DREQChannel.DREQ_UART0_TX not in native.snapshot()["dreq"]


def test_giving_the_native_dma_back_sends_the_requests_to_it_again():
    chip = RP2040()
    native = chip.dma
    chip.dma = _Recorder()  # type: ignore[assignment]
    chip.dma = native
    _request_uart_tx(chip)
    assert DREQChannel.DREQ_UART0_TX in _asserted(chip)


def test_a_replacement_is_told_to_clear_as_well_as_to_set():
    chip = RP2040()
    _request_spi_tx(chip)
    recorder = _Recorder()
    chip.dma = recorder  # type: ignore[assignment]
    chip.spi[0].write_uint32(SSPDMACR, 0)
    assert ("clear", int(DREQChannel.DREQ_SPI0_TX)) in recorder.calls


def test_a_failing_replacement_surfaces_from_the_access_that_raised_the_request():
    chip = RP2040()

    class Boom(Exception):
        pass

    class Failing(_Recorder):
        def set_dreq(self, channel: int) -> None:
            raise Boom(channel)

    chip.dma = Failing()  # type: ignore[assignment]
    with pytest.raises(Boom):
        _request_uart_tx(chip)
