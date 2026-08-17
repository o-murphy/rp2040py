"""WS2812/WS2812B decoding - see docs/records/0062.

Every waveform below is written in the timings of a *real* driver rather than round numbers:
CircuitPython's `ports/raspberrypi/common-hal/neopixel_write/__init__.c` runs its PIO at 12.8 MHz
and encodes a 0 as 4 cycles high + 12 low and a 1 as 9 high + 7 low (312/938 and 703/547 ns, both
on a 1.25 us period). Testing against upstream's own numbers is the point: a test written in the
decoder's own preferred units would pass no matter how wrong the threshold was.
"""

import pytest

from rp2040py.external.ws2812 import LATCH_NANOS, Ws2812
from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.sio import GPIO_OE_SET, GPIO_OUT_CLR, GPIO_OUT_SET

_CYCLE_NANOS = 1_000_000_000 / 12_800_000  # CircuitPython's PIO clock: 78.125 ns

# (high, low) per bit, in nanoseconds, exactly as that PIO program emits them.
_CIRCUITPYTHON = {0: (4 * _CYCLE_NANOS, 12 * _CYCLE_NANOS), 1: (9 * _CYCLE_NANOS, 7 * _CYCLE_NANOS)}
# pico-examples' ws2812.pio at its usual 800 kHz: T0H=350, T1H=700, same 1.25 us period. A
# different split of the same bit time - the threshold has to cope with both.
_PICO_EXAMPLES = {0: (350.0, 900.0), 1: (700.0, 550.0)}

_IO_BANK0_BASE = 0x40014000
_GPIO_CTRL = 0x4


class _Line:
    """Drives one GPIO the way firmware does - through SIO's own output registers - rather than
    via `set_input_value()`, which models an *external* drive and leaves `GPIOPin.value` (what
    listeners see) reporting the pad's pull configuration instead."""

    def __init__(self, rp2040, gpio: int) -> None:
        self.rp2040 = rp2040
        self.pin = rp2040.gpio[gpio]
        self.clock = rp2040.clock
        self.bit = 1 << gpio
        # Hand the pad to SIO and enable its output, exactly as `machine.Pin(n, Pin.OUT)` does -
        # until a function is selected, SIO's OE/OUT registers do not reach the pad at all.
        rp2040.write_uint32(_IO_BANK0_BASE + gpio * 8 + _GPIO_CTRL, FUNCTION_SIO)
        rp2040.sio.write_uint32(GPIO_OE_SET, self.bit)
        self.drive(False)

    def drive(self, high: bool) -> None:
        self.rp2040.sio.write_uint32(GPIO_OUT_SET if high else GPIO_OUT_CLR, self.bit)

    def write(self, data: "bytes", timings=None) -> None:
        """Shifts `data` out MSB-first, the way every WS2812 driver does."""
        timings = timings or _CIRCUITPYTHON
        for byte in data:
            for shift in range(7, -1, -1):
                high_nanos, low_nanos = timings[(byte >> shift) & 1]
                self.drive(True)
                self.clock.tick(high_nanos)
                self.drive(False)
                self.clock.tick(low_nanos)

    def latch(self) -> None:
        self.drive(False)
        self.clock.tick(LATCH_NANOS * 1.2)

    def idle(self, nanos: float) -> None:
        self.drive(False)
        self.clock.tick(nanos)


@pytest.fixture
def wired(rp2040_factory):
    """An attached `Ws2812` plus the line a test drives it through."""
    rp2040 = rp2040_factory()
    device = Ws2812(gpio=23)
    device.attach(rp2040)
    return device, _Line(rp2040, 23)


def test_decodes_one_pixel_from_circuitpythons_own_timings(wired):
    device, line = wired

    # GRB on the wire: green=0x00, red=0xFF, blue=0x80.
    line.write(bytes((0x00, 0xFF, 0x80)))
    line.latch()

    assert device.pixels == [(0xFF, 0x00, 0x80)]
    assert device.frame_count == 1


def test_the_same_frame_decodes_from_a_differently_clocked_driver(wired):
    """The threshold sits ~190 ns from either bit's high time, so a driver that splits the same
    1.25 us period differently (pico-examples' 350/700 ns) decodes identically - which is what
    makes one threshold enough for MicroPython, CircuitPython and hand-rolled PIO alike."""
    device, line = wired

    line.write(bytes((0x00, 0xFF, 0x80)), _PICO_EXAMPLES)
    line.latch()

    assert device.pixels == [(0xFF, 0x00, 0x80)]


def test_every_bit_pattern_survives_a_round_trip(wired):
    device, line = wired
    data = bytes((0x00, 0x01, 0x80, 0xFF, 0xAA, 0x55))

    line.write(data)
    line.latch()

    # Two GRB pixels: (G,R,B) = (00,01,80) and (FF,AA,55) -> (R,G,B).
    assert device.pixels == [(0x01, 0x00, 0x80), (0xAA, 0xFF, 0x55)]


def test_a_long_low_latches_and_separates_consecutive_frames(wired):
    device, line = wired
    frames = []
    device.on_pixels = frames.append

    line.write(bytes((0x00, 0x10, 0x00)))
    line.latch()
    line.write(bytes((0x00, 0x20, 0x00)))
    line.latch()

    assert frames == [[(0x10, 0x00, 0x00)], [(0x20, 0x00, 0x00)]]
    assert device.frame_count == 2


def test_a_gap_shorter_than_the_latch_keeps_one_frame_open(wired):
    """A driver stalling briefly mid-frame (the emulator is bursty by nature) must not be read as
    two frames - the latch is a real >=50 us silence, not "some idle time"."""
    device, line = wired

    line.write(bytes((0x00, 0x10)))
    line.idle(LATCH_NANOS * 0.5)
    line.write(bytes((0x00,)))
    line.latch()

    assert device.frame_count == 1
    assert device.pixels == [(0x10, 0x00, 0x00)]


def test_a_partial_pixel_is_dropped_rather_than_padded_with_zeros(wired):
    """Inventing zeros for the missing channels would surface as a real black pixel nobody drew."""
    device, line = wired

    line.write(bytes((0x11, 0x22, 0x33, 0x44)))  # one pixel + one stray byte
    line.latch()

    assert device.pixels == [(0x22, 0x11, 0x33)]


def test_a_frame_shorter_than_one_pixel_produces_nothing(wired):
    device, line = wired

    line.write(bytes((0x11,)))
    line.latch()

    assert device.frame_count == 0
    assert device.pixels == []


def test_a_partial_byte_does_not_leak_into_the_next_frame(wired):
    device, line = wired

    line.drive(True)  # one stray bit, then silence
    line.clock.tick(_CIRCUITPYTHON[1][0])
    line.drive(False)
    line.clock.tick(_CIRCUITPYTHON[1][1])
    line.latch()

    line.write(bytes((0x00, 0xFF, 0x80)))
    line.latch()

    assert device.pixels == [(0xFF, 0x00, 0x80)]
    assert device.frame_count == 1


def test_color_order_is_applied_rather_than_assumed(rp2040_factory):
    rp2040 = rp2040_factory()
    device = Ws2812(gpio=23, color_order="RGB")
    device.attach(rp2040)

    line = _Line(rp2040, 23)
    line.write(bytes((0x00, 0xFF, 0x80)))
    line.latch()

    assert device.pixels == [(0x00, 0xFF, 0x80)]


def test_a_four_channel_order_yields_rgbw(rp2040_factory):
    rp2040 = rp2040_factory()
    device = Ws2812(gpio=23, color_order="GRBW")
    device.attach(rp2040)

    line = _Line(rp2040, 23)
    line.write(bytes((0x11, 0x22, 0x33, 0x44)))
    line.latch()

    assert device.pixels == [(0x22, 0x11, 0x33, 0x44)]


@pytest.mark.parametrize("order", ["", "GRX", "GRR", "rgb "])
def test_an_impossible_color_order_is_rejected_at_construction(order):
    with pytest.raises(ValueError, match="color_order"):
        Ws2812(color_order=order)


def test_a_lowercase_color_order_is_accepted(rp2040_factory):
    assert Ws2812(color_order="grb").color_order == "GRB"


def test_on_pixels_fires_once_per_frame_with_the_same_list_as_the_attribute(wired):
    device, line = wired
    seen = []
    device.on_pixels = seen.append

    line.write(bytes((0x00, 0xFF, 0x80)))
    line.latch()

    assert seen == [[(0xFF, 0x00, 0x80)]]
    assert seen[0] == device.pixels
