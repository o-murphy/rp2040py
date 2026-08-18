"""Unit tests for `rp2040py.external.st7735s.St7735s` - the ST7735S TFT controller behind
Waveshare's RP2040-LCD-0.96 panel. Drives the same wire the emulated firmware does (SPI1's
`on_transmit`, with DC/CS/RST as real SIO-driven GPIOs), never the device's own internals."""

import pytest

from rp2040py.external.st7735s import (
    LCD_COL_OFFSET,
    LCD_HEIGHT,
    LCD_ROW_OFFSET,
    LCD_WIDTH,
    REFERENCE_MADCTL,
    St7735s,
)
from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.rp2040 import RP2040

_SPI = 1
_CS, _DC, _RST = 9, 8, 12


def _drive_gpio_high(rp2040: RP2040, gpio: int, high: bool) -> None:
    """What real firmware does via machine.Pin(gpio, Pin.OUT).value(x) - same helper shape as
    tests/test_led_mock.py's."""
    pin = rp2040.gpio[gpio]
    pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
    rp2040.sio.gpio_output_enable |= 1 << gpio
    if high:
        rp2040.sio.gpio_value |= 1 << gpio
    else:
        rp2040.sio.gpio_value &= ~(1 << gpio)
    pin.check_for_updates()


class Panel:
    """A `St7735s` attached to an `RP2040`, plus the vendor driver's own way of talking to it."""

    def __init__(self, rp2040: RP2040, **kwargs) -> None:
        self.rp2040 = rp2040
        self.frames: list[bytes] = []
        self.device = St7735s(on_frame=self.frames.append, **kwargs)
        self.device.attach(rp2040)
        for gpio in (_CS, _DC, _RST):
            _drive_gpio_high(rp2040, gpio, True)
        # Real firmware picks an orientation before drawing anything; the vendor driver's own
        # choice is the module's reference one, in which writes land unrotated.
        self.cmd(0x36, REFERENCE_MADCTL)

    def _transmit(self, byte: int) -> None:
        self.rp2040.spi[_SPI].on_transmit(byte)

    def cmd(self, opcode: int, *params: int) -> None:
        _drive_gpio_high(self.rp2040, _DC, False)
        _drive_gpio_high(self.rp2040, _CS, False)
        self._transmit(opcode)
        for param in params:
            _drive_gpio_high(self.rp2040, _DC, True)
            self._transmit(param)
        _drive_gpio_high(self.rp2040, _CS, True)

    def pixels(self, data: "bytes | bytearray") -> None:
        """A bulk RAMWR payload: CS held low across the whole buffer, like `display()` does."""
        _drive_gpio_high(self.rp2040, _DC, True)
        _drive_gpio_high(self.rp2040, _CS, False)
        for byte in data:
            self._transmit(byte)
        _drive_gpio_high(self.rp2040, _CS, True)

    def set_window(self, xs: int, ys: int, xe: int, ye: int) -> None:
        """The vendor driver's SetWindows(): visible coordinates plus the panel's GRAM offsets."""
        self.cmd(0x2A, 0x00, xs + LCD_COL_OFFSET, 0x00, xe + LCD_COL_OFFSET)
        self.cmd(0x2B, 0x00, ys + LCD_ROW_OFFSET, 0x00, ye + LCD_ROW_OFFSET)
        self.cmd(0x2C)

    def pixel(self, x: int, y: int) -> int:
        offset = (y * self.device.width + x) * 2
        return (self.device.frame_buffer[offset] << 8) | self.device.frame_buffer[offset + 1]


@pytest.fixture
def panel(rp2040_factory):
    return Panel(rp2040_factory())


def test_attach_produces_no_frame_on_its_own(panel):
    assert panel.frames == []
    assert panel.device.display_on is False


def test_full_window_write_emits_one_frame(panel):
    panel.cmd(0x3A, 0x05)
    panel.set_window(0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1)
    panel.pixels(b"\xf8\x00" * (LCD_WIDTH * LCD_HEIGHT))

    assert len(panel.frames) == 1
    assert len(panel.frames[0]) == LCD_WIDTH * LCD_HEIGHT * 2
    assert set(panel.frames[0]) == {0xF8, 0x00}


def test_window_offsets_place_pixels_at_the_visible_origin(panel):
    panel.set_window(0, 0, 1, 0)
    panel.pixels(b"\x12\x34\x56\x78")

    assert panel.pixel(0, 0) == 0x1234
    assert panel.pixel(1, 0) == 0x5678
    assert len(panel.frames) == 1  # the 2x1 window filled exactly


def test_window_rows_advance_by_the_window_width_not_the_panel_width(panel):
    panel.set_window(4, 2, 5, 3)
    panel.pixels(b"\x00\x01\x00\x02\x00\x03\x00\x04")

    assert (panel.pixel(4, 2), panel.pixel(5, 2)) == (0x0001, 0x0002)
    assert (panel.pixel(4, 3), panel.pixel(5, 3)) == (0x0003, 0x0004)


def test_pixels_addressed_outside_the_visible_window_are_dropped(panel):
    # Column 0 of the GRAM is left of the visible area (the panel starts at LCD_COL_OFFSET), so a
    # window that starts there writes its first pixel into invisible memory.
    panel.cmd(0x2A, 0x00, 0x00, 0x00, 0x01)
    panel.cmd(0x2B, 0x00, LCD_ROW_OFFSET, 0x00, LCD_ROW_OFFSET)
    panel.cmd(0x2C)
    panel.pixels(b"\xaa\xaa\xbb\xbb")

    assert panel.pixel(0, 0) == 0xBBBB
    assert 0xAAAA not in (panel.pixel(x, 0) for x in range(LCD_WIDTH))


def test_partial_window_write_is_flushed_when_cs_goes_high(panel):
    panel.set_window(0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1)
    panel.pixels(b"\x0f\xf0" * 4)  # nowhere near a full window

    assert len(panel.frames) == 1
    assert panel.pixel(3, 0) == 0x0FF0
    assert panel.pixel(4, 0) == 0x0000


def test_a_frame_is_only_emitted_once_per_completed_window(panel):
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\x11\x11")
    panel.pixels(b"\x22\x22")

    assert len(panel.frames) == 2
    assert panel.pixel(0, 0) == 0x2222


def test_odd_trailing_byte_is_held_until_its_pair_arrives(panel):
    panel.set_window(0, 0, 1, 0)
    panel.pixels(b"\x12")

    assert panel.frames == []
    assert panel.pixel(0, 0) == 0x0000

    panel.pixels(b"\x34")
    assert panel.pixel(0, 0) == 0x0000  # a new CS transaction restarts the pixel pair


def test_display_and_inversion_flags_track_their_commands(panel):
    panel.cmd(0x11)  # SLPOUT
    panel.cmd(0x21)  # INVON
    panel.cmd(0x29)  # DISPON
    assert (panel.device.display_on, panel.device.inverted) == (True, True)

    panel.cmd(0x20)  # INVOFF
    panel.cmd(0x28)  # DISPOFF
    assert (panel.device.display_on, panel.device.inverted) == (False, False)


def test_madctl_and_colmod_are_recorded(panel):
    panel.cmd(0x36, REFERENCE_MADCTL)  # the orientation the vendor driver picks
    panel.cmd(0x3A, 0x05)  # 16 bits/pixel

    assert (panel.device.madctl, panel.device.colmod) == (REFERENCE_MADCTL, 0x05)


def test_rgb666_pixels_are_normalized_into_the_rgb565_buffer(panel):
    panel.cmd(0x3A, 0x06)  # 18 bits/pixel, 6 significant bits per byte
    panel.set_window(0, 0, 1, 0)
    panel.pixels(bytes([0xFF, 0xFF, 0xFF, 0xFC, 0x00, 0x00]))

    assert panel.pixel(0, 0) == 0xFFFF  # full white
    assert panel.pixel(1, 0) == 0xF800  # full red, green/blue zero


def test_rgb444_packs_two_pixels_into_three_bytes(panel):
    panel.cmd(0x3A, 0x03)  # 12 bits/pixel
    panel.set_window(0, 0, 1, 0)
    # [R1 G1][B1 R2][G2 B2] - pixel 1 full red, pixel 2 full blue.
    panel.pixels(bytes([0xF0, 0x00, 0x0F]))

    # 4-bit channels widen by bit replication, so full scale stays full scale.
    assert panel.pixel(0, 0) == 0xF800
    assert panel.pixel(1, 0) == 0x001F
    assert len(panel.frames) == 1


def test_an_incomplete_rgb444_group_is_discarded_when_cs_goes_high(panel):
    panel.cmd(0x3A, 0x03)
    panel.set_window(0, 0, 1, 0)
    panel.pixels(bytes([0xF0, 0x00]))  # two of the three bytes a pixel pair needs

    assert panel.pixel(0, 0) == 0x0000
    assert panel.frames == []


def test_an_unimplemented_pixel_format_is_dropped_rather_than_misdecoded(panel):
    panel.cmd(0x3A, 0x07)  # not a format this model decodes
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\xff\xff\xff")

    assert panel.frames == []
    assert panel.pixel(0, 0) == 0x0000


def test_madctl_mx_mirrors_the_image_vertically(panel):
    panel.cmd(0x36, REFERENCE_MADCTL | 0x40)  # + MX (column address order)
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\x12\x34")

    assert panel.pixel(0, LCD_HEIGHT - 1) == 0x1234
    assert panel.pixel(0, 0) == 0x0000


def test_clearing_madctl_my_mirrors_the_image_horizontally(panel):
    panel.cmd(0x36, REFERENCE_MADCTL & ~0x80)  # - MY (row address order)
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\x12\x34")

    assert panel.pixel(LCD_WIDTH - 1, 0) == 0x1234
    assert panel.pixel(0, 0) == 0x0000


def test_portrait_madctl_rotates_onto_the_landscape_glass(panel):
    # MV cleared: firmware now addresses the controller's native axes directly, so the panel's
    # own offsets swap (+26 on columns, +1 on rows) - and its origin lands in the glass's corner
    # a quarter turn away, exactly as a real module shows a portrait-addressed write.
    panel.cmd(0x36, 0x08)  # BGR only: no MV, no mirrors
    panel.cmd(0x2A, 0x00, LCD_ROW_OFFSET, 0x00, LCD_ROW_OFFSET)
    panel.cmd(0x2B, 0x00, LCD_COL_OFFSET, 0x00, LCD_COL_OFFSET)
    panel.cmd(0x2C)
    panel.pixels(b"\x12\x34")

    assert panel.pixel(LCD_WIDTH - 1, 0) == 0x1234


def test_reset_clears_the_framebuffer_and_the_address_window(panel):
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\x77\x77")
    assert panel.pixel(0, 0) == 0x7777

    _drive_gpio_high(panel.rp2040, _RST, False)

    assert panel.pixel(0, 0) == 0x0000
    assert panel.device.display_on is False


def test_software_reset_command_clears_the_framebuffer_too(panel):
    panel.set_window(0, 0, 0, 0)
    panel.pixels(b"\x77\x77")

    panel.cmd(0x01)  # SWRESET

    assert panel.pixel(0, 0) == 0x0000


def test_bytes_sent_while_cs_is_high_are_ignored(panel):
    panel.set_window(0, 0, 0, 0)
    _drive_gpio_high(panel.rp2040, _DC, True)
    panel.rp2040.spi[_SPI].on_transmit(0x99)
    panel.rp2040.spi[_SPI].on_transmit(0x99)

    assert panel.frames == []
    assert panel.pixel(0, 0) == 0x0000
