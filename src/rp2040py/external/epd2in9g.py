"""`Epd2in9G` - wire-protocol emulation of the Waveshare 2.9inch e-Paper (G), 128x296 BWYR
(2 bits/pixel). A concrete `ExternalDevice` (`external/device.py`'s Protocol), same reasoning
`external/led_mock.py`/`external/cyw43/` follow - flat, sibling to them rather than its own
subpackage, since (unlike `cyw43/`'s bus/chip/nat split) this is genuinely one file.

Decodes the real SPI/GPIO wire protocol used by Waveshare's official epd2in9g.py driver:
https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/2.9inch_e-Paper_G/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd2in9g.py
so a real display-driver script running inside the emulated firmware can drive this instead of
real hardware.

No image-library dependency on purpose: `on_frame` is handed the raw packed 2bpp frame buffer
(`bytes`, `PALETTE`-indexed, row-major, 4 pixels/byte) rather than a decoded picture - turning
that into an actual image is a presentation concern for whichever caller wants one (e.g.
`demo/eink_run.py`'s Pillow-based renderers), not something this protocol model should need a
dependency for.

Not part of any `boards.py` entry: unlike a Pico W's onboard CYW43439, a panel like this is
arbitrary hardware the user wires up themselves, not fixed on any real board - a caller wires it
in with `attach_external_devices(mcu, Epd2in9G(...))` directly, same as `boards.py`'s own
docstring describes for any custom `ExternalDevice` combination outside the registry.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.peripherals.spi import RPSPI
    from rp2040py.rp2040 import RP2040

__all__ = ("EPD_HEIGHT", "EPD_WIDTH", "PALETTE", "Epd2in9G")

EPD_WIDTH = 128
EPD_HEIGHT = 296

# Opcodes from the vendor driver's init()/display()/Clear()/sleep() that this emulation reacts
# to; every other opcode is accepted (recorded as the "current command") but otherwise ignored.
_CMD_POWER_ON = 0x04
_CMD_DATA_START_TRANSMISSION = 0x10
_CMD_DISPLAY_REFRESH = 0x12

# getbuffer()'s quantization palette, in the vendor driver's 2-bit index order - part of the wire
# protocol (which 2-bit code means which real-world color), not a rendering concern, so it stays
# here rather than in whatever decodes `on_frame`'s raw bytes into a picture.
PALETTE = (
    (0, 0, 0),  # 00 BLACK
    (255, 255, 255),  # 01 WHITE
    (255, 255, 0),  # 10 YELLOW
    (255, 0, 0),  # 11 RED
)

_BUSY_NANOS_POWER = 100_000_000  # 100ms - power on/off settle time, order-of-magnitude only
_BUSY_NANOS_REFRESH = 15_000_000_000  # 15s - a real full 4-color refresh is slow


class Epd2in9G:
    """Implements `ExternalDevice` (`external/device.py`): `attach(rp2040)` wires this onto one
    RPSPI's `on_transmit` plus the CS/DC/RST/BUSY GPIOs, then `on_frame(buf)` fires with the raw
    packed frame buffer every time firmware issues a DISPLAY_REFRESH (0x12). Only safe to
    `attach()` before `Simulator.start_execution()`, same contract as every other `ExternalDevice`.

    NOTE on BUSY polarity: the vendor driver's own `ReadBusy()` loops `while digital_read(busy)
    == 0`, which contradicts its adjacent comment ("0: idle, 1: busy") - the polarity real
    firmware actually depends on is 0=busy/wait, 1=idle. Reproduced here as-is rather than
    "fixed", matching this project's convention of preserving faithfully-ported upstream quirks.
    """

    def __init__(
        self,
        *,
        spi_index: int = 1,
        cs_pin: int = 9,
        dc_pin: int = 8,
        rst_pin: int = 12,
        busy_pin: int = 13,
        on_frame: "Callable[[bytes], None] | None" = None,
        busy_nanos_power: float = _BUSY_NANOS_POWER,
        busy_nanos_refresh: float = _BUSY_NANOS_REFRESH,
    ) -> None:
        self.spi_index = spi_index
        self.cs_pin = cs_pin
        self.dc_pin = dc_pin
        self.rst_pin = rst_pin
        self.busy_pin = busy_pin
        self.on_frame = on_frame
        # Real hardware's ~15s full-refresh BUSY hold is faithfully slow to actually emulate too
        # (the emulated core has to execute every instruction of firmware's busy-poll loop for
        # that whole simulated duration, not just fast-forward a clock) - overridable so a demo
        # can trade hardware realism for a runtime that finishes in a reasonable time.
        self._busy_nanos_power = busy_nanos_power
        self._busy_nanos_refresh = busy_nanos_refresh

        self._command: int | None = None
        self._frame_buf = bytearray()
        self._frame_size = (EPD_WIDTH // 4) * EPD_HEIGHT

    def attach(self, rp2040: "RP2040") -> None:
        self._spi: RPSPI = rp2040.spi[self.spi_index]
        self._cs = rp2040.gpio[self.cs_pin]
        self._dc = rp2040.gpio[self.dc_pin]
        self._busy = rp2040.gpio[self.busy_pin]

        self._busy_alarm = rp2040.clock.create_alarm(self._release_busy)
        self._tx_alarm = rp2040.clock.create_alarm(self._complete_transmit)

        self._busy.set_input_value(True)  # idle
        rp2040.gpio[self.rst_pin].add_listener(self._on_rst_changed)
        self._spi.on_transmit = self._on_transmit

    def _on_rst_changed(self, state: GPIOPinState, old_state: GPIOPinState) -> None:
        if state == GPIOPinState.LOW:
            self._command = None
            self._frame_buf.clear()

    def _on_transmit(self, byte: int) -> None:
        # Every send_command()/send_data() in the vendor driver is its own 1-byte CS transaction;
        # only the bulk framebuffer write (send_data2()) holds CS low across many bytes. Either
        # way, checking DC's *current* level per byte is enough - no need to track CS edges.
        if self._cs.value == GPIOPinState.LOW:
            if self._dc.value == GPIOPinState.LOW:
                self._on_command(byte)
            else:
                self._on_data(byte)
        # Deferred by one real SPI byte-time (via the SPI peripheral's own configured clock, not
        # completed synchronously): a bulk framebuffer write is driven by a *pair* of DMA channels
        # (TX feeding SSPDR, RX draining it), each pacing itself independently through the shared
        # simulated clock. Completing instantly here let the TX side blast through all ~9KB in a
        # single burst of zero-delay alarms, overflowing the 8-entry RX FIFO (silently discarding
        # everything past the first 8 bytes - see complete_transmit()'s overrun branch) long
        # before the RX DMA channel's own alarm chain got a turn to drain any of it - leaving RX
        # permanently blocked waiting for FIFO data that would never arrive again, which firmware
        # sees as an indefinite hang (confirmed via a stuck PC polling DMA CTRL.BUSY, and the RX
        # channel's trans_count frozen exactly 8 short of the total - the FIFO's capacity). Record
        # 0044 later fixed this hang's actual root cause at the simulator level too (a DREQ-cache
        # reset bug plus a same-tick alarm-ordering bug in the DMA-driven transfer path) - this
        # pacing is kept anyway since it's also a more realistic per-byte SPI clock model, not just
        # a workaround.
        frequency = self._spi.clock_frequency
        nanos_per_byte = int(self._spi.data_bits / frequency * 1_000_000_000) if frequency else 1_000
        self._tx_alarm.schedule(nanos_per_byte)

    def _complete_transmit(self) -> None:
        self._spi.complete_transmit(0)

    def _on_command(self, opcode: int) -> None:
        self._command = opcode
        if opcode == _CMD_DATA_START_TRANSMISSION:
            self._frame_buf.clear()

    def _on_data(self, byte: int) -> None:
        if self._command == _CMD_DATA_START_TRANSMISSION:
            self._frame_buf.append(byte)
        elif self._command == _CMD_DISPLAY_REFRESH:
            self._refresh()
        elif self._command == _CMD_POWER_ON:
            self._start_busy(self._busy_nanos_power)

    def _refresh(self) -> None:
        if self.on_frame is not None and len(self._frame_buf) >= self._frame_size:
            self.on_frame(bytes(self._frame_buf))
        self._start_busy(self._busy_nanos_refresh)

    def _start_busy(self, nanos: float) -> None:
        self._busy.set_input_value(False)
        self._busy_alarm.schedule(nanos)

    def _release_busy(self) -> None:
        self._busy.set_input_value(True)
