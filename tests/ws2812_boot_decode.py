"""Boots the YD-RP2040 board file on real CircuitPython, has the *guest* drive its WS2812 through
the PIO, and checks both what the device decoded and the waveform it decoded it from.

This is docs/records/0063's own acceptance test, kept runnable: `RPPIO` paces its state machines by
`SM_CLKDIV` and `[delay]`, so CircuitPython's 12.8 MHz `neopixel_write` program produces real
silicon's own 312 ns / 703 ns pulses. Before that it produced 8-40 ns pulses with the two symbol
populations *overlapping*, and nothing downstream could tell a `0` from a `1`.

**Both halves are checked on purpose.** `Ws2812` classifies bits by duty cycle against the frame's
own measured period (docs/records/0062), so it is deliberately scale-free - it would keep decoding
correctly off a waveform running at, say, a tenth of its configured rate. Asserting the decoded
bytes alone therefore cannot catch a partial regression in the pacing; asserting the pulse widths
in nanoseconds can, and that is the number 0063 actually fixed.

Usage:

    python tests/ws2812_boot_decode.py
    python tests/ws2812_boot_decode.py --image path/to/adafruit-circuitpython-...uf2

`--image` defaults to whatever the board file's own `firmware` declaration resolves to
(docs/records/0059), downloading it if it is not already cached.
"""

import argparse
import asyncio
import importlib.util
import statistics
import sys
from pathlib import Path

from rp2040py.boards import resolve_firmware
from rp2040py.device.mp_device import MicroPythonDevice
from rp2040py.utils.logging import LogLevel

BOARD_FILE = Path(__file__).resolve().parent.parent / "boards" / "vcc_gnd_yd_rp2040" / "__init__.py"

# What the guest writes, and what must come back off the wire. GRB order on the wire, so this is a
# green pixel at full brightness with a bit of blue - but the point is the *bytes*, chosen in
# docs/records/0062 because 0xFF/0x00/0xAA covers all-ones, all-zeros and an alternating byte.
WRITTEN = b"\xff\x00\xaa"

GUEST_SOURCE = """
import board, digitalio, neopixel_write
pin = digitalio.DigitalInOut(board.NEOPIXEL)
pin.direction = digitalio.Direction.OUTPUT
neopixel_write.neopixel_write(pin, bytearray([0xFF, 0x00, 0xAA]))
print("wrote")
"""

# CircuitPython's own PIO program, walked in docs/records/0062: 4 PIO cycles high for a `0` and 9
# for a `1` at 12.8 MHz, i.e. 312 ns and 703 ns on a fixed 1.25 us period. The windows are generous
# either side - an edge lands inside whichever CPU instruction was executing (1-3 system clocks,
# ~±40 ns measured) - but nowhere near wide enough to admit the 8-40 ns the emulator produced
# before docs/records/0063. The threshold between them is that record's own derived 500 ns, with
# ~190 ns of margin on each side.
ZERO_NANOS_RANGE = (200.0, 450.0)
ONE_NANOS_RANGE = (550.0, 900.0)
SYMBOL_THRESHOLD_NANOS = 500.0

# A bit's high can never outlast its own 1.25 us period, so anything longer is not a symbol: the
# line idling high between frames (a full second, in one measured run), or a mid-frame stall while
# it happened to be asserted. `Ws2812` itself tolerates those - it judges each bit against the
# *shortest* period in the frame - and they must not be classified as symbols here either, since a
# single one of them is enough to swamp a population's average.
MAX_BIT_HIGH_NANOS = 2_000.0


def _load_board_with():
    """`board_with(on_pixels)` off the board file, by path - the same
    `spec_from_file_location` mechanism `--board-spec` itself uses (docs/records/0049). The CLI's
    own loader insists the named attribute *is* a `BoardSpec`, which a factory taking a callback
    cannot be: `extras` holds zero-arg factories, so a device with a callback can be booted from
    the CLI but cannot hand anything back to it. That limitation is why this script exists rather
    than being one more `rp2040py micropython -c ...` line in CI."""
    module_spec = importlib.util.spec_from_file_location("_yd_rp2040_board", BOARD_FILE)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.board_with, module.RGB_GPIO


def _high_times(edges: "list[tuple[float, int]]") -> "tuple[list[float], int]":
    """Nanoseconds each pulse spent high, from a (timestamp, level) edge log, keeping only pulses
    short enough to be a WS2812 bit at all (see `MAX_BIT_HIGH_NANOS`). Also returns how many were
    dropped as too long, so that stays visible rather than silent."""
    highs: list[float] = []
    dropped = 0
    rise = None
    for nanos, level in edges:
        if level:
            rise = nanos
        elif rise is not None:
            high = nanos - rise
            if high < MAX_BIT_HIGH_NANOS:
                highs.append(high)
            else:
                dropped += 1
            rise = None
    return highs, dropped


def _check_waveform(highs: "list[float]") -> "list[str]":
    """The pulse widths themselves, split into the two symbol populations at the protocol's own
    500 ns decision point. Returns a list of failure messages (empty if the waveform is what real
    hardware produces).

    A fixed threshold rather than one derived from the data on purpose: the whole point is to catch
    a waveform that has drifted off the rate `SM_CLKDIV` asks for, and anything computed from the
    pulses themselves drifts along with them. Before docs/records/0063 every pulse here was 8-40 ns,
    which lands wholly on one side and fails as "only one symbol population" - exactly right."""
    failures = []
    if len(highs) < 24:
        return [f"expected at least one 24-bit frame of pulses, saw {len(highs)}"]

    zeros = [h for h in highs if h < SYMBOL_THRESHOLD_NANOS]
    ones = [h for h in highs if h >= SYMBOL_THRESHOLD_NANOS]
    if not zeros or not ones:
        return [f"only one symbol population in {len(highs)} pulses ({min(highs):.0f}-{max(highs):.0f} ns)"]

    if max(zeros) >= min(ones):
        failures.append(
            f"the two symbol populations overlap: `0` up to {max(zeros):.0f} ns, `1` from "
            f"{min(ones):.0f} ns - no threshold can separate them (this is docs/records/0063's "
            f"original failure)"
        )
    for name, population, (low, high) in (
        ("0", zeros, ZERO_NANOS_RANGE),
        ("1", ones, ONE_NANOS_RANGE),
    ):
        mean = statistics.fmean(population)
        if not low <= mean <= high:
            failures.append(
                f"`{name}` bits average {mean:.0f} ns high ({min(population):.0f}-"
                f"{max(population):.0f} ns over {len(population)} pulses), outside {low:.0f}-"
                f"{high:.0f} ns - the PIO is not running at the rate SM_CLKDIV asks for"
            )
    return failures


async def _run(image: "str | None") -> int:
    board_with, rgb_gpio = _load_board_with()

    frames: list[list[tuple[int, ...]]] = []
    board = resolve_firmware(board_with(frames.append), "circuitpython", image)
    print(f"Booting {board.image}")

    device = MicroPythonDevice(board=board, circuitpython=True, log_level=LogLevel.ERROR)
    clock = device.simulator.clock
    edges: list[tuple[float, int]] = []
    device.mcu.gpio[rgb_gpio].add_listener(lambda new, _old: edges.append((clock.nanos, int(new))))

    try:
        await device.astart(timeout=None)
        boot_frames = len(frames)
        stdout, stderr = await device.aexec(GUEST_SOURCE, timeout=None)
    finally:
        device.stop()

    if stderr:
        print(f"guest raised:\n{stderr.decode(errors='replace')}", file=sys.stderr)
        return 1
    print(f"guest said: {stdout!r}")
    # Not asserted, only reported: CircuitPython drives this LED as its status indicator, so frames
    # arrive before any guest code runs - but how many depends on where boot happened to be.
    print(f"{boot_frames} frame(s) decoded during boot alone, {len(frames)} in total")

    highs, dropped = _high_times(edges)
    print(f"{len(highs)} bit-length pulses on GPIO{rgb_gpio} ({dropped} longer than a bit cell, ignored)")
    failures = _check_waveform(highs)

    written = [f for f in frames if bytes(_wire_bytes(f)) == WRITTEN]
    if not written:
        decoded = ", ".join(" ".join(f"{v:02x}" for v in _wire_bytes(f)) for f in frames[-4:]) or "(nothing)"
        failures.append(f"no frame decoded as {WRITTEN.hex(' ')} - last frames on the wire: {decoded}")
    else:
        print(f"decoded {WRITTEN.hex(' ')} off the wire, {len(written)} time(s)")

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("WS2812 BOOT DECODE OK")
    return 0


def _wire_bytes(frame: "list[tuple[int, ...]]") -> "list[int]":
    """A decoded frame back as the bytes that were on the wire. `Ws2812` hands over pixels in RGB
    order whatever the wire order was (`GRB` here), so this undoes that to compare against what the
    guest actually wrote."""
    return [component for pixel in frame for component in (pixel[1], pixel[0], *pixel[2:])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        default=None,
        help="CircuitPython .uf2 path or version tag; defaults to the board file's own declaration",
    )
    sys.exit(asyncio.run(_run(parser.parse_args().image)))


if __name__ == "__main__":
    main()
