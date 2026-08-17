"""A WS2812/WS2812B ("NeoPixel") RGB LED, or a chain of them, as an `ExternalDevice`: watches one
GPIO pin, decodes the single-wire NRZ waveform firmware drives onto it, and hands over the raw
pixel values of each completed frame. Designed in docs/records/0062, whose addenda carry the
derivations every constant below comes from.

**Why this can be emulated at all, when docs/records/0060 rules WS2812 out.** That record's
ceiling is about *driving real hardware*: a physical WS2812 measures pulse widths with its own
silicon, in wall-clock nanoseconds, and this emulator runs ~20-30x slower than real time and in
bursts - so every "0" would arrive stretched into a "1". A decoder inside the emulator has no such
problem: it timestamps edges off `rp2040.clock` (emulated time), where `RPPIO` is stepped in lock
step with the CPU's own instruction loop (docs/records/0037), so the waveform's proportions are
exactly what firmware intended however slowly the host is going. Decoding in, not driving out.

**Timings are derived from CircuitPython's own driver, not from datasheet folklore**
(`ports/raspberrypi/common-hal/neopixel_write/__init__.c` - docs/records/0027's "3g rule" applied
to a device rather than a board). Its PIO program runs at 12.8 MHz (78.125 ns/cycle) and encodes:

    bit 0:  4 cycles high (312 ns) + 12 low (938 ns)
    bit 1:  9 cycles high (703 ns) +  7 low (547 ns)

both on the same 1.25 us period.

**A bit is classified by its *duty cycle*, not by an absolute pulse width.** A `0` occupies about
a quarter of its bit period and a `1` about half (312/1250 = 25%, 703/1250 = 56%), so
`HIGH_DUTY_THRESHOLD = 0.4` sits between them with room on both sides and needs no knowledge of the
clock a particular driver chose - it decodes CircuitPython's 12.8 MHz program, MicroPython's, and
pico-examples' `ws2812.pio` alike, and would still decode a 400 kHz "slow mode" part where an
absolute threshold in nanoseconds starts to strain. The bit period is measured rather than assumed:
each bit is judged once its own low is over, against the *shortest* period seen so far in the
frame, so a driver pausing mid-frame to refill a FIFO stretches one bit's low without corrupting
the estimate every other bit is judged by. `LATCH_NANOS` is absolute because the inter-frame gap is
CPU-timed, not PIO-timed (CircuitPython spins on `port_get_raw_ticks()` for >=300 us), and CPU-timed
intervals are faithful in this emulator.

**Live decoding does not work on this emulator yet, and that is not this device's fault.**
`RPPIO` stores `SM_CLKDIV` and accumulates `[delay]` cycles but paces itself by neither - it
advances every state machine once per CPU instruction (docs/records/0037) - so a PIO-generated
WS2812 waveform arrives with its two symbols collapsed and jittered into each other. Measured
against real CircuitPython firmware writing `ff 00 aa`, the eight `0` bits came out as high times
of 8, 16, 8, 16, 16, 16, 16, 16 ns while the eight `1` bits gave 16-40 ns: overlapping, so *no*
threshold can separate them. This device is written for the waveform real hardware produces (and
the tests hold it to CircuitPython's own numbers); making the emulator produce that waveform is
docs/records/0063's job, and until then only a bit-banged or CPU-driven WS2812 driver decodes
reliably here.

Boundary, same as `Epd2in9G`/`St7735s` (docs/records/0046): this hands over raw pixel *values*,
never a picture, so nothing in `src/` grows an image dependency. What a caller does with
`[(r, g, b), ...]` - draw it, assert on it, print it - is the caller's business.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.clock.clock import IAlarm, IClock
    from rp2040py.rp2040 import RP2040

__all__ = ("HIGH_DUTY_THRESHOLD", "LATCH_NANOS", "Ws2812")

# Fraction of a bit's own period spent high, above which it is a 1. See the module docstring: a
# real 0 is 25-28% and a real 1 is 56%, and those proportions survive the emulator's PIO time
# compression, where absolute pulse widths do not.
HIGH_DUTY_THRESHOLD = 0.4
# A low this long ends the frame. The WS2812 datasheet's own reset time; CircuitPython leaves a
# far more generous 300 us, so this errs toward working with the tightest driver rather than the
# one we happen to have measured.
LATCH_NANOS = 50_000.0

_CHANNELS = "RGBW"


class Ws2812:
    """Implements `ExternalDevice` structurally (see `external/device.py`).

    `on_pixels` fires once per latched frame with a list of pixel tuples - `(r, g, b)` for a
    3-channel `color_order`, `(r, g, b, w)` for a 4-channel one - regardless of the order those
    bytes actually travel in on the wire, which is what `color_order` describes ("GRB" for
    WS2812/WS2812B, "GRBW" for SK6812). Colour order is a constructor argument rather than
    something inferred, because the wire format itself carries no hint of it: the driver shifts
    out bytes MSB-first and the LED simply consumes them in the order its silicon expects.

    `.pixels` keeps the most recently latched frame and `.frame_count` counts them, so a test can
    assert on what was drawn without wiring a callback at all - the same shape `LEDMock.on`/
    `.toggle_count` use.
    """

    def __init__(
        self,
        gpio: int = 23,
        *,
        color_order: str = "GRB",
        on_pixels: "Callable[[list[tuple[int, ...]]], None] | None" = None,
        high_duty_threshold: float = HIGH_DUTY_THRESHOLD,
        latch_nanos: float = LATCH_NANOS,
    ) -> None:
        order = color_order.upper()
        if not order or any(channel not in _CHANNELS for channel in order) or len(set(order)) != len(order):
            raise ValueError(f"color_order must be distinct letters from {_CHANNELS!r}, got {color_order!r}")
        self.gpio = gpio
        self.color_order = order
        self.on_pixels = on_pixels
        self.high_duty_threshold = high_duty_threshold
        self.latch_nanos = latch_nanos

        self.pixels: list[tuple[int, ...]] = []
        self.frame_count = 0

        self._clock: IClock | None = None
        self._latch_alarm: IAlarm | None = None
        self._rise_nanos = 0.0
        self._fall_nanos = 0.0
        self._pending_high: float | None = None  # a bit whose low has not finished yet
        self._min_period = 0.0  # shortest high+low seen this frame - the true bit period
        self._bits = 0  # bits accumulated into the byte in progress, MSB-first
        self._bit_count = 0
        self._bytes = bytearray()
        self._unsubscribe: Callable[[], None] | None = None

    def attach(self, rp2040: "RP2040") -> None:
        self._clock = rp2040.clock
        # The latch is "the line stayed low long enough", which is the absence of an edge - so it
        # needs an alarm rather than another listener. Rescheduled on every falling edge (the same
        # way `Epd2in9G` reschedules its own tx alarm per byte), so it only ever fires once the
        # line has really gone quiet.
        self._latch_alarm = rp2040.clock.create_alarm(self._latch)
        self._unsubscribe = rp2040.gpio[self.gpio].add_listener(self._on_change)

    def _on_change(self, new_state: "GPIOPinState", old_state: "GPIOPinState") -> None:
        assert self._clock is not None and self._latch_alarm is not None  # set in attach()
        now = self._clock.nanos
        if new_state == GPIOPinState.HIGH:
            # A new bit starts, which is also the moment the previous one's low is finally known.
            self._close_pending(now - self._fall_nanos)
            self._rise_nanos = now
            return
        if new_state != GPIOPinState.LOW or old_state != GPIOPinState.HIGH:
            # `GPIOPin.value` reports the pull configuration (INPUT/INPUT_PULL_UP/...) whenever
            # firmware is not driving the pad at all, and those transitions are not edges of a
            # waveform - a driver that releases the pin mid-frame has stopped talking, it has not
            # sent a bit. Only a real HIGH->LOW is one, so anything else is ignored rather than
            # decoded into an invented 0.
            return
        # Falling edge: the high time is now known, but the bit cannot be judged until its low is
        # too (see the module docstring - a bit is its duty cycle, not its pulse width). The frame
        # is not over until the line stays low, so re-arm the latch on every falling edge.
        self._pending_high = now - self._rise_nanos
        self._fall_nanos = now
        self._latch_alarm.schedule(self.latch_nanos)

    def _close_pending(self, low_nanos: float) -> None:
        """Judges the bit whose low just ended, against the shortest bit period seen this frame."""
        high_nanos = self._pending_high
        if high_nanos is None:
            return
        self._pending_high = None
        period = high_nanos + low_nanos
        if self._min_period == 0.0 or period < self._min_period:
            self._min_period = period
        self._push_bit(high_nanos >= self.high_duty_threshold * self._min_period)

    def _push_bit(self, bit: bool) -> None:
        # MSB-first within each byte - CircuitPython's PIO autopulls 8 bits at a time and shifts
        # left, and every other driver does the same, because that is what the LED expects.
        self._bits = (self._bits << 1) | int(bit)
        self._bit_count += 1
        if self._bit_count == 8:
            self._bytes.append(self._bits)
            self._bits = 0
            self._bit_count = 0

    def _latch(self) -> None:
        """The line has been low for `latch_nanos`: whatever bytes arrived are one frame."""
        # The frame's last bit never got a low of its own - the latch is its low - so it is judged
        # against the period the rest of the frame established. A bit with no such reference (a
        # single stray pulse, a frame cut off after one bit) is dropped rather than guessed.
        if self._min_period:
            self._close_pending(self._min_period - (self._pending_high or 0.0))
        self._pending_high = None
        self._min_period = 0.0
        raw, self._bytes = self._bytes, bytearray()
        # Leftovers are dropped rather than padded: a partial byte, or a trailing partial pixel,
        # is a frame that was cut short (a reset mid-write, a driver stopped by the shutdown
        # coordinator), and inventing zeros for the missing channels would show up as a real black
        # pixel nobody drew.
        self._bits = 0
        self._bit_count = 0
        channels = len(self.color_order)
        if len(raw) < channels:
            return
        self.pixels = [self._to_rgb(raw[i : i + channels]) for i in range(0, len(raw) - channels + 1, channels)]
        self.frame_count += 1
        if self.on_pixels is not None:
            self.on_pixels(self.pixels)

    def _to_rgb(self, wire: "bytes | bytearray") -> "tuple[int, ...]":
        """One pixel's wire bytes, reordered from `color_order` into R, G, B (then W, if the order
        has four channels) - so a caller never has to know what order this particular LED wanted."""
        by_channel = dict(zip(self.color_order, wire))
        return tuple(by_channel[channel] for channel in _CHANNELS if channel in by_channel)
