"""`St7735s` - wire-protocol emulation of a Sitronix ST7735S TFT controller driving the 0.96inch
160x80 65K IPS panel found on Waveshare's RP2040-LCD-0.96 board (and on the pin-compatible
Pico-LCD-0.96 HAT). A concrete `ExternalDevice` (`external/device.py`'s Protocol), flat sibling to
`external/epd2in9g.py`/`external/led_mock.py` rather than its own subpackage, same reasoning: this
is one class in one file (see CLAUDE.md's module-layout default, and 0028 for the one case -
`cyw43/` - that genuinely earned a package).

Decodes the SPI/DC/CS/RST wire protocol the vendor's own MicroPython driver speaks:
https://github.com/waveshare/Pico_code/blob/main/Python/Pico-LCD-0.96/pico-lcd-0.96.py
(`LCD_0inch96`) - `Init()`'s command stream, `SetWindows()`'s CASET/RASET + the panel's `+1`/`+26`
GRAM offsets, and `display()`'s bulk RAMWR of a 160x80 RGB565 framebuffer.

Addressing model (see docs/records/0056's MADCTL addendum for the full derivation): the address
counter is mapped
through `MADCTL` (0x36) exactly as the controller does - MV exchanges the row/column axes, then
MX/MY mirror them - into the ST7735S's own 80x160 native frame memory, whose visible sub-window
sits at (26, 1) in it. That native pixel is then placed on the glass through one *fixed* mapping,
the module's own physical orientation, chosen so that `MADCTL=0xA8` (what the vendor driver sets,
and the only value real firmware for this module uses) comes out as the identity: a 160x80
landscape picture written left-to-right, top-to-bottom. Firmware that picks a different
orientation - portrait, or a mirrored variant - is therefore shown rotated/mirrored on the output
exactly as a real module would show it, rather than silently unrotated.

`MADCTL`'s BGR bit (0x08) is recorded but **not** applied, unlike the geometry bits. Whether it
swaps red and blue as seen by a human depends on how the glass's own subpixels are wired, which is
a module fact the controller's datasheet cannot supply - and on this module the vendor driver sets
the bit *and* its own color constants come out correct without any swap here (verified against
real firmware, see 0056).

No image-library dependency, same boundary `Epd2in9G` draws (0046): `on_frame` hands over the raw
RGB565 framebuffer (`bytes`, big-endian pairs, row-major over the visible window), never a decoded
picture - turning that into an image is the caller's job. RGB444/RGB666 (`COLMOD` 0x03/0x06) are
decoded too, normalized up/down into that same RGB565 buffer so the callback's contract stays one
format regardless of what firmware chose to clock over the wire.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.peripherals.spi import RPSPI
    from rp2040py.rp2040 import RP2040

__all__ = (
    "GRAM_COLUMNS",
    "GRAM_ROWS",
    "LCD_COL_OFFSET",
    "LCD_HEIGHT",
    "LCD_ROW_OFFSET",
    "LCD_WIDTH",
    "REFERENCE_MADCTL",
    "St7735s",
)

# The panel Waveshare puts on RP2040-LCD-0.96 / Pico-LCD-0.96: 160x80 visible pixels, sitting in
# the ST7735S's own frame memory at the offsets the vendor driver's SetWindows() adds (Xstart+1,
# Ystart+26) - not guesses, that arithmetic is in the driver linked above.
LCD_WIDTH = 160
LCD_HEIGHT = 80
LCD_COL_OFFSET = 1
LCD_ROW_OFFSET = 26

# What the vendor driver writes to MADCTL, once, in Init(): MY | MV | BGR. Taken as this module's
# reference orientation - the one in which the glass shows a 160x80 landscape picture in write
# order - so every other MADCTL value is rendered *relative* to it.
REFERENCE_MADCTL = 0xA8

# ST7735S frame memory as the datasheet describes it, in the controller's own native axes: 132
# columns x 162 rows. The visible 80x160 native window sits inside it at the offsets above,
# transposed - the vendor driver's `+1` lands on the 162-long axis and its `+26` on the 132-wide
# one, because REFERENCE_MADCTL has MV set.
GRAM_COLUMNS = 132
GRAM_ROWS = 162

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

_MADCTL_MY = 0x80  # row address order
_MADCTL_MX = 0x40  # column address order
_MADCTL_MV = 0x20  # row/column exchange

# COLMOD (0x3A) pixel formats, and how many wire bytes each group of pixels takes: RGB444 packs
# two pixels into three bytes, RGB565 is two bytes per pixel, RGB666 is three (only each byte's
# top 6 bits carry data). The vendor driver selects RGB565 (`write_cmd(0x3A); write_data(0x05)`).
_COLMOD_12BPP = 0x03
_COLMOD_16BPP = 0x05
_COLMOD_18BPP = 0x06
_GROUP_BYTES = {_COLMOD_12BPP: 3, _COLMOD_16BPP: 2, _COLMOD_18BPP: 3}  # colmod -> wire bytes per group

_BYTES_PER_PIXEL = 2  # of the *framebuffer*, which is always RGB565 - see the module docstring


def _rgb565(red5: int, green6: int, blue5: int) -> "tuple[int, int]":
    value = (red5 << 11) | (green6 << 5) | blue5
    return value >> 8, value & 0xFF


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
        # `width`/`height`/`col_offset`/`row_offset` describe the module as it is meant to be
        # *seen* (and as the vendor driver addresses it under REFERENCE_MADCTL). The controller's
        # own native frame memory is that transposed, since the reference orientation sets MV.
        self.width = width
        self.height = height
        self.col_offset = col_offset
        self.row_offset = row_offset
        self._native_width = height
        self._native_height = width
        self._native_col_offset = row_offset
        self._native_row_offset = col_offset
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
        self._pixel_bytes = bytearray()  # wire bytes of a pixel group still being assembled
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
            # Raising CS ends the transaction, so a pixel group whose remaining bytes never
            # arrived is dropped rather than silently completed from the next transaction's.
            self._pixel_bytes.clear()
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
        self._pixel_bytes.clear()
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
        group_bytes = _GROUP_BYTES.get(self.colmod)
        if group_bytes is None:
            return  # an unimplemented pixel format - see _GROUP_BYTES

        self._pixel_bytes.append(byte)
        if len(self._pixel_bytes) < group_bytes:
            return
        raw = bytes(self._pixel_bytes)
        self._pixel_bytes.clear()

        for high, low in self._decode_group(raw):
            self._store_pixel(high, low)
            self._pixel_index += 1
            if self._pixel_index >= self._window_pixels:
                # The address counter wraps back to the window's top-left on a real controller;
                # here that wrap is what "a whole window's worth of pixels arrived" means.
                self._pixel_index = 0
                self._flush()

    def _decode_group(self, raw: bytes) -> "tuple[tuple[int, int], ...]":
        """One wire group -> RGB565 (high, low) byte pairs, normalizing whichever `COLMOD`
        firmware picked into the single format `frame_buffer`/`on_frame` speak."""
        if self.colmod == _COLMOD_16BPP:
            return ((raw[0], raw[1]),)
        if self.colmod == _COLMOD_18BPP:
            # 6 significant bits per channel, left-aligned in each byte (datasheet 8.8.34).
            return (_rgb565(raw[0] >> 3, raw[1] >> 2, raw[2] >> 3),)
        # RGB444: [R1 G1][B1 R2][G2 B2], 4 bits per channel. Widen by replicating the high bits
        # rather than shifting in zeros, so full-scale stays full-scale (0xf -> 0x1f, not 0x1e).
        nibbles = (raw[0] >> 4, raw[0] & 0xF, raw[1] >> 4, raw[1] & 0xF, raw[2] >> 4, raw[2] & 0xF)
        return tuple(
            _rgb565((r4 << 1) | (r4 >> 3), (g4 << 2) | (g4 >> 2), (b4 << 1) | (b4 >> 3))
            for r4, g4, b4 in (nibbles[0:3], nibbles[3:6])
        )

    def _store_pixel(self, high: int, low: int) -> None:
        position = self._panel_position()
        if position is None:
            return
        x, y = position
        offset = (y * self.width + x) * _BYTES_PER_PIXEL
        self.frame_buffer[offset] = high
        self.frame_buffer[offset + 1] = low
        self._dirty = True

    def _panel_position(self) -> "tuple[int, int] | None":
        """Current address-counter position -> visible (x, y), or `None` for a pixel addressed
        outside the glass. Three steps, mirroring the controller: address -> native frame memory
        (through MADCTL's MV/MX/MY), then native -> panel through this module's fixed physical
        orientation, the one that makes `REFERENCE_MADCTL` the identity."""
        window_width = self._col_end - self._col_start + 1
        if window_width <= 0:
            return None
        column = self._col_start + self._pixel_index % window_width
        row = self._row_start + self._pixel_index // window_width

        if self.madctl & _MADCTL_MV:
            column, row = row, column
        native_x = column - self._native_col_offset
        native_y = row - self._native_row_offset
        if not (0 <= native_x < self._native_width and 0 <= native_y < self._native_height):
            return None  # real GRAM holds it, the glass does not show it
        if self.madctl & _MADCTL_MX:
            native_x = self._native_width - 1 - native_x
        if self.madctl & _MADCTL_MY:
            native_y = self._native_height - 1 - native_y

        # The glass's own orientation: its long axis is the native row axis, running right-to-left
        # (so REFERENCE_MADCTL, which sets MY and therefore already flipped it once above, comes
        # out as a plain left-to-right landscape picture).
        return self._native_height - 1 - native_y, native_x

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
        self._pixel_bytes.clear()
        self._dirty = False
        self._col_start = 0
        self._col_end = GRAM_COLUMNS - 1
        self._row_start = 0
        self._row_end = GRAM_ROWS - 1
        self.madctl = 0
        self.colmod = _COLMOD_16BPP
        self.display_on = False
        self.inverted = False
        # Real GRAM contents survive a reset undefined rather than cleared; blanking is the
        # predictable choice for a viewer, and firmware repaints before DISPON anyway.
        self.frame_buffer = bytearray(self.width * self.height * _BYTES_PER_PIXEL)
