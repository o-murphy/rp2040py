# MicroPython driver + demo for the virtual ST7735S panel on a Waveshare RP2040-LCD-0.96, pushed
# into the emulated MicroPython over the raw REPL by demo/lcd_run.py (this file is *not* CPython
# code - it never runs in the process that reads it, only inside the emulated firmware).
#
# Driver ported from Waveshare's own sample, command for command:
# https://github.com/waveshare/Pico_code/blob/main/Python/Pico-LCD-0.96/pico-lcd-0.96.py
# Only the backlight pin differs - GPIO25 on this board, GPIO13 on the Pico-LCD-0.96 HAT that
# sample targets (pico-sdk's waveshare_rp2040_lcd_0.96.h: WAVESHARE_LCD_BL_PIN 25).
#
# Pin wiring matches rp2040py.external.st7735s's defaults: SPI1, SCK=10, MOSI=11, CS=9, DC=8,
# RST=12. CircuitPython needs none of this - it initialises the same panel from its own
# board_init(), which is why demo/lcd_run.py --circuitpython takes no script at all.

import time

import framebuf
from machine import SPI, Pin

LCD_WIDTH = 160
LCD_HEIGHT = 80

BLACK = 0x0000
WHITE = 0xFFFF
# The vendor sample's own byte-swapped constants: framebuf.RGB565 stores little-endian, so these
# reach the wire as plain big-endian RGB565 (0x00f8 -> 0xf8,0x00 -> red).
RED = 0x00F8
GREEN = 0xE007
BLUE = 0x1F00

# Real hardware settles for ~200ms per reset step; the emulator runs firmware's wait loop for
# every millisecond of it (see docs/records/0046's timing addendum), so these are demo-tuned.
RESET_MS = 5


class LCD(framebuf.FrameBuffer):
    def __init__(self):
        self.width = LCD_WIDTH
        self.height = LCD_HEIGHT
        self.cs = Pin(9, Pin.OUT)
        self.rst = Pin(12, Pin.OUT)
        self.bl = Pin(25, Pin.OUT)
        self.cs(1)
        self.spi = SPI(1, 10_000_000, polarity=0, phase=0, sck=Pin(10), mosi=Pin(11), miso=None)
        self.dc = Pin(8, Pin.OUT)
        self.dc(1)
        self.buffer = bytearray(self.height * self.width * 2)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)
        self.init()

    def reset(self):
        self.rst(1)
        time.sleep_ms(RESET_MS)
        self.rst(0)
        time.sleep_ms(RESET_MS)
        self.rst(1)
        time.sleep_ms(RESET_MS)

    def write_cmd(self, cmd):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([cmd]))

    def write_data(self, value):
        self.dc(1)
        self.cs(0)
        self.spi.write(bytearray([value]))
        self.cs(1)

    def init(self):
        self.reset()
        self.bl(1)
        self.write_cmd(0x11)  # SLPOUT
        time.sleep_ms(12)
        self.write_cmd(0x21)  # INVON, twice - as the vendor sample does
        self.write_cmd(0x21)
        for cmd, params in (
            (0xB1, (0x05, 0x3A, 0x3A)),  # FRMCTR1
            (0xB2, (0x05, 0x3A, 0x3A)),  # FRMCTR2
            (0xB3, (0x05, 0x3A, 0x3A, 0x05, 0x3A, 0x3A)),  # FRMCTR3
            (0xB4, (0x03,)),  # INVCTR
            (0xC0, (0x62, 0x02, 0x04)),  # PWCTR1
            (0xC1, (0xC0,)),  # PWCTR2
            (0xC2, (0x0D, 0x00)),  # PWCTR3
            (0xC3, (0x8D, 0x6A)),  # PWCTR4
            (0xC4, (0x8D, 0xEE)),  # PWCTR5
            (0xC5, (0x0E,)),  # VMCTR1
            (0x3A, (0x05,)),  # COLMOD - 16 bits/pixel
            (0x36, (0xA8,)),  # MADCTL - this module's reference orientation
        ):
            self.write_cmd(cmd)
            for value in params:
                self.write_data(value)
        self.write_cmd(0x29)  # DISPON

    def set_window(self, x_start, y_start, x_end, y_end):
        # The panel's visible area sits at (+1, +26) in the controller's frame memory.
        self.write_cmd(0x2A)
        for value in (0x00, x_start + 1, 0x00, x_end + 1):
            self.write_data(value)
        self.write_cmd(0x2B)
        for value in (0x00, y_start + 26, 0x00, y_end + 26):
            self.write_data(value)
        self.write_cmd(0x2C)

    def show(self):
        self.set_window(0, 0, self.width - 1, self.height - 1)
        self.dc(1)
        self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)


lcd = LCD()

lcd.fill(BLACK)
lcd.text("rp2040py", 8, 8, GREEN)
lcd.text("ST7735S", 8, 24, RED)
lcd.hline(0, 40, LCD_WIDTH, BLUE)
lcd.fill_rect(8, 48, 40, 24, RED)
lcd.fill_rect(56, 48, 40, 24, GREEN)
lcd.fill_rect(104, 48, 40, 24, BLUE)
lcd.show()
print("frame 1 sent")

lcd.fill(WHITE)
lcd.text("frame 2", 40, 36, BLACK)
lcd.show()
print("frame 2 sent")
