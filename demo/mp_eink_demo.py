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


HORIZON_Y = EPD_HEIGHT - 40  # rows >= this are the black "ground" band
SUN_CX = EPD_WIDTH // 2
SUN_MIN_CY = 60  # how high above the horizon the sun's center reaches at t=1
SUN_MIN_RADIUS = 26
SUN_MAX_RADIUS = 42

# Row buffers are cached across the *whole* animation, not just within one frame - sky/ground
# never change color, and the sun's disc only ever has a handful of distinct chord half-widths
# (0..SUN_MAX_RADIUS) across all 6 frames combined, however many of `EPD_HEIGHT`'s rows actually
# cross it each frame - so this is at most a few dozen bytearrays built total for the whole demo,
# not one per row per frame. Plain integer Newton's-method `_isqrt` instead of `math.sqrt`
# on purpose: real Cortex-M0 has no hardware FPU, so `float` sqrt is emulated via a softfloat
# library call - measurably expensive per call in this project's own software CPU interpreter -
# while integer division/arithmetic isn't.
_solid_row_cache = {}
_sun_row_cache = {}


def _isqrt(n):
    if n <= 0:
        return 0
    x = n
    y = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x


def _solid_row(color, row_bytes):
    row = _solid_row_cache.get(color)
    if row is None:
        packed = (color << 6) | (color << 4) | (color << 2) | color
        row = bytearray(row_bytes)
        for i in range(row_bytes):
            row[i] = packed
        _solid_row_cache[color] = row
    return row


def _sun_row(half_width, row_bytes):
    """One row crossing the sun's disc: YELLOW within `half_width` of center, a thin RED rim just
    inside the edge (a cheap two-color "glow"), WHITE (sky) beyond it. Cached by `half_width` -
    every row at that same chord width looks identical regardless of which row/frame it came
    from."""
    row = _sun_row_cache.get(half_width)
    if row is not None:
        return row
    rim = half_width // 4 if half_width > 4 else 1
    row = bytearray(row_bytes)
    for xb in range(row_bytes):
        packed = 0
        for i in range(4):
            dx = xb * 4 + i - SUN_CX
            if dx < 0:
                dx = -dx
            if dx > half_width:
                color = WHITE
            elif dx > half_width - rim:
                color = RED
            else:
                color = YELLOW
            packed = (packed << 2) | color
        row[xb] = packed
    _sun_row_cache[half_width] = row
    return row


def make_sunrise_frame(t, row_bytes):
    """`t` from 0 (sun just below the horizon) to 1 (fully risen) - a growing sun climbing out of
    a black horizon into a white sky. Sky/ground rows reuse one cached buffer each (see
    `_solid_row`); rows crossing the sun's disc reuse one cached buffer per chord half-width (see
    `_sun_row`) instead of a fresh per-pixel build every time."""
    cy = int(HORIZON_Y - t * (HORIZON_Y - SUN_MIN_CY))
    radius = int(SUN_MIN_RADIUS + t * (SUN_MAX_RADIUS - SUN_MIN_RADIUS))
    buf = bytearray()
    for y in range(EPD_HEIGHT):
        if y >= HORIZON_Y:
            row = _solid_row(BLACK, row_bytes)
        else:
            dy = y - cy
            if dy < 0:
                dy = -dy
            if dy > radius:
                row = _solid_row(WHITE, row_bytes)
            else:
                row = _sun_row(_isqrt(radius * radius - dy * dy), row_bytes)
        buf.extend(row)
    return buf


spi = machine.SPI(1, baudrate=4_000_000, polarity=0, phase=0, sck=machine.Pin(10), mosi=machine.Pin(11))
cs = machine.Pin(9, machine.Pin.OUT, value=1)
dc = machine.Pin(8, machine.Pin.OUT)
rst = machine.Pin(12, machine.Pin.OUT)
busy = machine.Pin(13, machine.Pin.IN)

epd = EPD2in9G(spi, cs, dc, rst, busy)
epd.init()

N_FRAMES = 5
row_bytes = EPD_WIDTH // 4
for frame in range(N_FRAMES + 1):
    epd.display_frame(make_sunrise_frame(frame / N_FRAMES, row_bytes))
    print("frame", frame, "of", N_FRAMES, "done")

epd.sleep()
print("demo complete")
