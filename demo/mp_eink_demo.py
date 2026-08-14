# MicroPython driver + demo for the virtual Waveshare 2.9" e-Paper (G), pushed into the emulated
# MicroPython over the raw REPL by demo/eink_run.py (this file is *not* CPython code - it never
# runs in the process that reads it, only inside the emulated firmware). Driver ported from
# Waveshare's own epd2in9g.py - see rp2040py.external.epd2in9g's docstring for the vendor source
# link and the command-set/BUSY-polarity notes.
#
# Pin wiring matches demo/eink_run.py / rp2040py.external.epd2in9g's defaults (Pico ePaper HAT on
# SPI1): SCK=10, MOSI=11, CS=9, DC=8, RST=12, BUSY=13.

import machine
import utime

EPD_WIDTH = 128
EPD_HEIGHT = 296

# The emulator actually runs firmware's busy-wait loop for every millisecond of utime.sleep_ms()
# rather than fast-forwarding simulated time (measured: roughly 30x real time per simulated ms),
# so these are demo-tuned (short, but still nonzero) rather than the real datasheet values - real
# hardware uses RESET_MS=200/RESET_PULSE_MS=2/POWER_ON_SETTLE_MS=500/POWER_OFF_SETTLE_MS=100.
RESET_MS = 5
RESET_PULSE_MS = 1
POWER_ON_SETTLE_MS = 10
POWER_OFF_SETTLE_MS = 5
BUSY_POLL_MS = 2

BLACK = 0b00
WHITE = 0b01
YELLOW = 0b10
RED = 0b11


class EPD2in9G:
    def __init__(self, spi, cs, dc, rst, busy):
        self.spi = spi
        self.cs = cs
        self.dc = dc
        self.rst = rst
        self.busy = busy

    def reset(self):
        self.rst.value(1)
        utime.sleep_ms(RESET_MS)
        self.rst.value(0)
        utime.sleep_ms(RESET_PULSE_MS)
        self.rst.value(1)
        utime.sleep_ms(RESET_MS)

    def _cmd(self, command):
        self.dc.value(0)
        self.cs.value(0)
        self.spi.write(bytearray([command]))
        self.cs.value(1)

    def _data(self, data):
        self.dc.value(1)
        self.cs.value(0)
        self.spi.write(bytearray([data]))
        self.cs.value(1)

    def _data_bulk(self, buf):
        self.dc.value(1)
        self.cs.value(0)
        self.spi.write(buf)
        self.cs.value(1)

    def read_busy(self):
        utime.sleep_ms(BUSY_POLL_MS)
        # 0 = busy: matches the vendor driver's own polarity (its adjacent comment says the
        # opposite - see virtual_eink.py's docstring for that discrepancy).
        while self.busy.value() == 0:
            utime.sleep_ms(BUSY_POLL_MS)

    def init(self):
        self.reset()
        self.read_busy()

        self._cmd(0x4D)
        self._data(0x78)

        self._cmd(0x00)
        self._data(0x0F)
        self._data(0x29)

        self._cmd(0x01)
        self._data(0x07)
        self._data(0x00)

        self._cmd(0x03)
        self._data(0x10)
        self._data(0x54)
        self._data(0x44)

        self._cmd(0x06)
        for b in (0x0F, 0x0A, 0x2F, 0x25, 0x22, 0x2E, 0x21):
            self._data(b)

        self._cmd(0x41)
        self._data(0x00)

        self._cmd(0x50)
        self._data(0x37)

        self._cmd(0x60)
        self._data(0x02)
        self._data(0x02)

        self._cmd(0x61)
        self._data(EPD_WIDTH // 256)
        self._data(EPD_WIDTH % 256)
        self._data(EPD_HEIGHT // 256)
        self._data(EPD_HEIGHT % 256)

        self._cmd(0x65)
        for _ in range(4):
            self._data(0x00)

        self._cmd(0xE7)
        self._data(0x1C)

        self._cmd(0xE3)
        self._data(0x22)

        self._cmd(0xB4)
        self._data(0xD0)
        self._cmd(0xB5)
        self._data(0x03)

        self._cmd(0xE9)
        self._data(0x01)

        self._cmd(0x30)
        self._data(0x08)

        self._cmd(0x04)
        utime.sleep_ms(POWER_ON_SETTLE_MS)
        self.read_busy()

    def display_frame(self, buf):
        self._cmd(0x10)
        self._data_bulk(buf)
        self._cmd(0x12)
        self._data(0x00)
        self.read_busy()

    def sleep(self):
        self._cmd(0x02)
        self._data(0x00)
        utime.sleep_ms(POWER_OFF_SETTLE_MS)
        self._cmd(0x07)
        self._data(0xA5)


def make_wipe_frame(threshold, accent):
    """One row of the frame (BLACK left of `threshold`, `accent`-colored at it, WHITE right of
    it), replicated for every row - every row is identical, so building one and repeating it is
    both far less MicroPython bytecode to run and the same bytes `_decode_frame()` expects."""
    row_bytes = EPD_WIDTH // 4
    row = bytearray(row_bytes)
    for xb in range(row_bytes):
        packed = 0
        for i in range(4):
            x = xb * 4 + i
            if x < threshold:
                color = BLACK
            elif x == threshold:
                color = accent
            else:
                color = WHITE
            packed = (packed << 2) | color
        row[xb] = packed
    # bytearray * int repetition isn't supported on this MicroPython build - extend() in a loop
    # instead (still just row_bytes bytes copied per row, not a per-pixel Python loop).
    buf = bytearray()
    for _ in range(EPD_HEIGHT):
        buf.extend(row)
    return buf


spi = machine.SPI(1, baudrate=4_000_000, polarity=0, phase=0, sck=machine.Pin(10), mosi=machine.Pin(11))
cs = machine.Pin(9, machine.Pin.OUT, value=1)
dc = machine.Pin(8, machine.Pin.OUT)
rst = machine.Pin(12, machine.Pin.OUT)
busy = machine.Pin(13, machine.Pin.IN)

epd = EPD2in9G(spi, cs, dc, rst, busy)
epd.init()

N_FRAMES = 3
accents = (RED, YELLOW, RED, YELLOW)
for frame in range(N_FRAMES + 1):
    threshold = EPD_WIDTH * frame // N_FRAMES
    epd.display_frame(make_wipe_frame(threshold, accents[frame]))
    print("frame", frame, "of", N_FRAMES, "done")

epd.sleep()
print("demo complete")
