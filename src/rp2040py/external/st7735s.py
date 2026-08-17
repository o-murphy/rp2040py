"""`St7735s` - wire-protocol emulation of a Sitronix ST7735S TFT controller driving the 0.96inch
160x80 65K IPS panel found on Waveshare's RP2040-LCD-0.96 board (and on the pin-compatible
Pico-LCD-0.96 HAT). A concrete `ExternalDevice` (`external/device.py`'s Protocol), flat sibling to
`external/epd2in9g.py`/`external/led_mock.py` rather than its own subpackage, same reasoning: this
is one class in one file (see CLAUDE.md's module-layout default, and 0028 for the one case -
`cyw43/` - that genuinely earned a package).

Decodes the SPI/DC/CS/RST wire protocol the vendor's own MicroPython driver speaks:
https://github.com/waveshare/Pico_code/blob/main/Python/Pico-LCD-0.96/pico-lcd-0.96.py
(`LCD_0inch96`) - `Init()`'s command stream, `SetWindows()`'s CASET/RASET + the panel's
`+1`/`+26` GRAM offsets, and `display()`'s bulk RAMWR of a 160x80 RGB565 framebuffer.

Addressing model: pixels are stored exactly where CASET/RASET put them, in *controller address*
space, and the visible picture is the `width` x `height` window at (`col_offset`, `row_offset`)
inside the ST7735S's 132x162 GRAM. `MADCTL` (0x36) is recorded (`madctl`) but deliberately not
applied as a memory->panel remap: firmware picks one orientation at init and then addresses every
window through it, so the pixels arrive already in the order they are meant to be shown - which
is exactly what the vendor driver above does (`MADCTL=0xA8` once, then plain left-to-right,
top-to-bottom `display()` writes). A firmware that changes `MADCTL` between writes to rotate the
image would need that remap; this model would show such a frame unrotated. Same "model what real
firmware actually does, not the whole datasheet" scope as `Epd2in9G`.

No image-library dependency, same boundary `Epd2in9G` draws (0046): `on_frame` hands over the raw
RGB565 framebuffer (`bytes`, big-endian pairs exactly as firmware clocked them out, row-major over
the visible window), never a decoded picture - turning that into an image is the caller's job.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.peripherals.spi import RPSPI
    from rp2040py.rp2040 import RP2040

__all__ = ("GRAM_COLUMNS", "GRAM_ROWS", "LCD_COL_OFFSET", "LCD_HEIGHT", "LCD_ROW_OFFSET", "LCD_WIDTH", "St7735s")

# The panel Waveshare puts on RP2040-LCD-0.96 / Pico-LCD-0.96: 160x80 visible pixels, sitting in
# the ST7735S's own 132x162 frame memory at the offsets the vendor driver's SetWindows() adds
# (Xstart+1, Ystart+26) - not guesses, that arithmetic is in the driver linked above.
LCD_WIDTH = 160
LCD_HEIGHT = 80
LCD_COL_OFFSET = 1
LCD_ROW_OFFSET = 26

# ST7735S frame memory, as addressed through CASET/RASET in the orientation this panel is wired
# in (the datasheet's 132 x 162 GRAM, with the long axis on the column address here since the
# vendor driver's own offsets - +1 on a 160-wide axis, +26 on an 80-tall one - only fit that way).
GRAM_COLUMNS = 162
GRAM_ROWS = 132

_CMD_SWRESET = 0x01
_CMD_SLPOUT = 0x11
_CMD_INVOFF = 0x20
_CMD_INVON = 0x21
_CMD_DISPOFF = 0x28
_CMD_DISPON = 0x29
_CMD_CASET = 0x2A
_CMD_RASET = 0x2B
_CMD_RAMWR = 0x2C
_CMD_MADCTL = 0x36
_CMD_COLMOD = 0x3A

# COLMOD (0x3A) parameter for 16 bits/pixel - the only pixel format modelled here, and the one the
# vendor driver selects (`write_cmd(0x3A); write_data(0x05)`). RGB444 (0x03) / RGB666 (0x06) are
# real ST7735S options this emulation does not decode.
_COLMOD_16BPP = 0x05

_BYTES_PER_PIXEL = 2


class St7735s:
    """Implements `ExternalDevice` (`external/device.py`): `attach(rp2040)` wires this onto one
    RPSPI's `on_transmit` plus the CS/DC/RST GPIOs, then `on_frame(buf)` fires with the visible
    framebuffer's raw RGB565 bytes every time firmware finishes filling an addressed window
    (or lets CS go high part-way through one - a partial update still shows). Only safe to
    `attach()` before `Simulator.start_execution()`, same contract as every other `ExternalDevice`.

    Unlike an e-Paper panel there is no BUSY line and no refresh command to wait on: a TFT is
    scanned out continuously, so "a frame happened" is a property of the RAMWR stream itself,
    which is what the callback keys off.
    """

    def __init__(
        self,
        *,
        spi_index: int = 1,
        cs_pin: int = 9,
        dc_pin: int = 8,
        rst_pin: int = 12,
        width: int = LCD_WIDTH,
        height: int = LCD_HEIGHT,
        col_offset: int = LCD_COL_OFFSET,
        row_offset: int = LCD_ROW_OFFSET,
        on_frame: "Callable[[bytes], None] | None" = None,
    ) -> None:
        self.spi_index = spi_index
        self.cs_pin = cs_pin
        self.dc_pin = dc_pin
        self.rst_pin = rst_pin
        self.width = width
        self.height = height
        self.col_offset = col_offset
        self.row_offset = row_offset
        self.on_frame = on_frame

        self.madctl = 0
        self.colmod = _COLMOD_16BPP
        self.display_on = False
        self.inverted = False
        self.frame_buffer = bytearray(width * height * _BYTES_PER_PIXEL)

        self._command: int | None = None
        self._params = bytearray()
        self._col_start = 0
        self._col_end = GRAM_COLUMNS - 1
        self._row_start = 0
        self._row_end = GRAM_ROWS - 1
        self._pixel_index = 0  # position inside the addressed window, in pixels
        self._pixel_high: int | None = None  # first byte of an RGB565 pair, awaiting its second
        self._dirty = False

    def attach(self, rp2040: "RP2040") -> None:
        self._spi: RPSPI = rp2040.spi[self.spi_index]
        self._cs = rp2040.gpio[self.cs_pin]
        self._dc = rp2040.gpio[self.dc_pin]

        self._tx_alarm = rp2040.clock.create_alarm(self._complete_transmit)

        rp2040.gpio[self.rst_pin].add_listener(self._on_rst_changed)
        self._cs.add_listener(self._on_cs_changed)
        self._spi.on_transmit = self._on_transmit

    def _on_rst_changed(self, state: GPIOPinState, old_state: GPIOPinState) -> None:
        if state == GPIOPinState.LOW:
            self._reset_state()

    def _on_cs_changed(self, state: GPIOPinState, old_state: GPIOPinState) -> None:
        # A bulk `display()` write ends by raising CS, and firmware that repaints only part of the
        # screen never fills the window it addressed - flush what did arrive instead of holding it
        # back until some later write happens to complete a window.
        if state != GPIOPinState.LOW and old_state == GPIOPinState.LOW:
            # Raising CS ends the transaction, so a pixel whose second RGB565 byte never arrived is
            # dropped rather than silently completed by whatever the next transaction opens with.
            self._pixel_high = None
            self._flush()

    def _on_transmit(self, byte: int) -> None:
        if self._cs.value == GPIOPinState.LOW:
            if self._dc.value == GPIOPinState.LOW:
                self._on_command(byte)
            else:
                self._on_data(byte)
        # Deferred by one real SPI byte-time rather than completed synchronously, for the same
        # reason `Epd2in9G._on_transmit()` does it (see that file's own comment, and 0044): a bulk
        # framebuffer write is driven by a TX/RX DMA channel pair pacing themselves through the
        # shared simulated clock, and it is also the more realistic per-byte SPI clock model.
        frequency = self._spi.clock_frequency
        nanos_per_byte = int(self._spi.data_bits / frequency * 1_000_000_000) if frequency else 1_000
        self._tx_alarm.schedule(nanos_per_byte)

    def _complete_transmit(self) -> None:
        self._spi.complete_transmit(0)

    def _on_command(self, opcode: int) -> None:
        self._command = opcode
        self._params.clear()
        self._pixel_high = None
        if opcode == _CMD_RAMWR:
            self._pixel_index = 0
        elif opcode == _CMD_SWRESET:
            self._reset_state()
        elif opcode == _CMD_DISPON:
            self.display_on = True
        elif opcode == _CMD_DISPOFF:
            self.display_on = False
        elif opcode == _CMD_INVON:
            self.inverted = True
        elif opcode == _CMD_INVOFF:
            self.inverted = False

    def _on_data(self, byte: int) -> None:
        if self._command == _CMD_RAMWR:
            self._on_pixel_byte(byte)
            return

        self._params.append(byte)
        if self._command == _CMD_CASET and len(self._params) == 4:
            self._col_start = (self._params[0] << 8) | self._params[1]
            self._col_end = (self._params[2] << 8) | self._params[3]
        elif self._command == _CMD_RASET and len(self._params) == 4:
            self._row_start = (self._params[0] << 8) | self._params[1]
            self._row_end = (self._params[2] << 8) | self._params[3]
        elif self._command == _CMD_MADCTL and len(self._params) == 1:
            self.madctl = byte
        elif self._command == _CMD_COLMOD and len(self._params) == 1:
            self.colmod = byte

    def _on_pixel_byte(self, byte: int) -> None:
        if self.colmod != _COLMOD_16BPP:
            return  # only RGB565 is decoded - see _COLMOD_16BPP's comment
        if self._pixel_high is None:
            self._pixel_high = byte
            return
        high, self._pixel_high = self._pixel_high, None
        self._store_pixel(high, byte)

        self._pixel_index += 1
        if self._pixel_index >= self._window_pixels:
            # The address counter wraps back to the window's top-left on a real controller; here
            # that wrap is what "a whole window's worth of pixels arrived" means, i.e. a frame.
            self._pixel_index = 0
            self._flush()

    def _store_pixel(self, high: int, low: int) -> None:
        window_width = self._col_end - self._col_start + 1
        if window_width <= 0:
            return
        column = self._col_start + self._pixel_index % window_width
        row = self._row_start + self._pixel_index // window_width
        x = column - self.col_offset
        y = row - self.row_offset
        if not (0 <= x < self.width and 0 <= y < self.height):
            return  # addressed outside the visible window - real GRAM holds it, the panel doesn't
        offset = (y * self.width + x) * _BYTES_PER_PIXEL
        self.frame_buffer[offset] = high
        self.frame_buffer[offset + 1] = low
        self._dirty = True

    @property
    def _window_pixels(self) -> int:
        return max(0, self._col_end - self._col_start + 1) * max(0, self._row_end - self._row_start + 1)

    def _flush(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        if self.on_frame is not None:
            self.on_frame(bytes(self.frame_buffer))

    def _reset_state(self) -> None:
        self._command = None
        self._params.clear()
        self._pixel_index = 0
        self._pixel_high = None
        self._dirty = False
        self._col_start = 0
        self._col_end = GRAM_COLUMNS - 1
        self._row_start = 0
        self._row_end = GRAM_ROWS - 1
        self.display_on = False
        self.inverted = False
        # Real GRAM contents survive a reset undefined rather than cleared; blanking is the
        # predictable choice for a viewer, and firmware repaints before DISPON anyway.
        self.frame_buffer = bytearray(self.width * self.height * _BYTES_PER_PIXEL)
