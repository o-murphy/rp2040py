#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=12.3.0",
#     "rp2040py",
# ]
#
# [tool.uv.sources]
# rp2040py = { path = "..", editable = true }
# ///
"""A Pico W with an ST7735S panel wired to it: CircuitPython joins the emulated CYW43439's
network and prints the result on the display, and this script saves a PNG of it.

    python demo/wifi_lcd_run.py                       # -> demo/screenshots/wifi-lcd-circuitpython-connected.png
    python demo/wifi_lcd_run.py --image 9.2.9 --all-frames out

One file, start to finish, and nothing is prepared on the host: the guest code below is **pushed
over the REPL** into the CIRCUITPY the firmware formatted itself, and a Ctrl-B/Ctrl-D soft reset
makes CircuitPython re-run it as `code.py` (not `supervisor.reload()` - see
`restart_into_code_py()`). That is the whole route
[record 0087](../docs/records/0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)
measured - `storage.remount('/', readonly=False)` works here because this emulator drives the CDC
interface only and never claims the mass-storage one, so the blockdev lock a real USB host would
hold is free. It replaces an earlier pair of files that built a FAT12 image on the host and handed
it over as flash content; nothing about the *emulator* needed that, only the demo did.

Two emulated devices at once, which is the point: `Cyw43439` (docs/records/0027, 0048) comes from
the built-in `pico_w` board, and `St7735s` (docs/records/0056) is added to it here - the same
`ExternalDevice` composition a `--board-spec` board file does, done from an ordinary program.
Nothing about either device knows the other exists. No RP2040 board has both an onboard radio and
an onboard display, so this wiring only exists in the emulator - which is exactly why the
screenshot it produces cannot be taken any other way (docs/records/0085).

Expect a minute and a half, most of it the CYW43's PIO/gSPI hot path - the heaviest thing in this
emulator (docs/records/0047) - with the panel's SPI traffic on top. That is why the guest refreshes
by hand (`auto_refresh=False`), why frames are decoded on *this* thread rather than in `on_frame`,
and why the loop below drains to the newest frame instead of decoding every one: doing either the
other way turned the same run into forty minutes. `--image` defaults to
CircuitPython 10.2.1; 0048's live WiFi verification and `ci-circuitpython.yml`'s WLAN job both pin
**9.2.9**, so `--image 9.2.9` is the fallback if 10.x ever misbehaves on the radio path.
"""

import argparse
import dataclasses
import queue
import sys
import time
from pathlib import Path

from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))  # boards/ lives at the repo root, outside the installed package

from rp2040py.boards import BOARDS, BoardSpec, resolve_firmware
from rp2040py.device import CTRL_B, CTRL_D, MicroPythonDevice
from rp2040py.external.st7735s import LCD_HEIGHT, LCD_WIDTH, St7735s
from rp2040py.utils.logging import LogLevel

_DEFAULT_IMAGE = "10.2.1"
_DEFAULT_SCREENSHOT = _REPO_ROOT / "demo" / "screenshots" / "wifi-lcd-circuitpython-connected.png"
# A freshly formatted CIRCUITPY costs the firmware several flash writes before USB even comes up,
# and this board runs the CYW43's hot path alongside everything else - so the boot budget is six
# times BaseDevice's own default rather than a shaved-close guess (0093's boot-timeout knife edge).
_START_TIMEOUT_SECONDS = 360.0

# The guest half, written to CIRCUITPY as `code.py` over the REPL. Pins are the ones the Waveshare
# RP2040-LCD-0.96 uses for the same panel (SPI1 CLK=GP10, MOSI=GP11, DC=GP8, CS=GP9, RST=GP12),
# because `rp2040py.external.st7735s.St7735s` defaults to that wiring and none of it collides with
# the CYW43439's own GP23/24/25/29. The backlight does not carry over: GP25 is the radio's chip
# select on a Pico W, so there is no backlight pin here.
GUEST_CODE = '''\
"""Joins the emulated CYW43439's network and prints the result onto an ST7735S panel."""

import time

import board
import busdisplay
import busio
import displayio
import fourwire
import wifi

# Byte-for-byte CircuitPython's own `display_init_sequence` for this panel
# (ports/raspberrypi/boards/waveshare_rp2040_lcd_0_96/board.c, itself taken from
# Adafruit_CircuitPython_ST7735R), in the packed format `BusDisplay` documents: command byte, then
# a parameter count whose 0x80 bit means "a delay byte follows the parameters".
_ST7735S_INIT = (
    b"\\x01\\x80\\x96"  # SWRESET, 150 ms
    b"\\x11\\x80\\xff"  # SLPOUT, 500 ms (0xff means "extra long" to BusDisplay)
    b"\\xb1\\x03\\x01\\x2c\\x2d"  # FRMCTR1
    b"\\xb3\\x06\\x01\\x2c\\x2d\\x01\\x2c\\x2d"  # FRMCTR3
    b"\\xb4\\x01\\x07"  # INVCTR, line inversion
    b"\\xc0\\x03\\xa2\\x02\\x84"  # PWCTR1
    b"\\xc1\\x01\\xc5"  # PWCTR2
    b"\\xc2\\x02\\x0a\\x00"  # PWCTR3
    b"\\xc3\\x02\\x8a\\x2a"  # PWCTR4
    b"\\xc4\\x02\\x8a\\xee"  # PWCTR5
    b"\\xc5\\x01\\x0e"  # VMCTR1
    b"\\x20\\x00"  # INVOFF
    b"\\x36\\x01\\x18"  # MADCTL, bottom-to-top refresh
    b"\\x3a\\x01\\x05"  # COLMOD, 16-bit colour
    b"\\xe0\\x10\\x02\\x1c\\x07\\x12\\x37\\x32\\x29\\x2d\\x29\\x25\\x2b\\x39\\x00\\x01\\x03\\x10"  # GMCTRP1
    b"\\xe1\\x10\\x03\\x1d\\x07\\x06\\x2e\\x2c\\x29\\x2d\\x2e\\x2e\\x37\\x3f\\x00\\x00\\x02\\x10"  # GMCTRN1
    b"\\x13\\x80\\x0a"  # NORON, 10 ms
    b"\\x29\\x80\\x64"  # DISPON, 100 ms
    b"\\x36\\x01\\xc8"  # MADCTL again: default rotation, RGB encoding
    b"\\x21\\x00"  # INVON
)

displayio.release_displays()
spi = busio.SPI(clock=board.GP10, MOSI=board.GP11)
display_bus = fourwire.FourWire(spi, command=board.GP8, chip_select=board.GP9, reset=board.GP12, baudrate=40_000_000)
display = busdisplay.BusDisplay(
    display_bus,
    _ST7735S_INIT,
    width=160,
    height=80,
    colstart=26,
    rowstart=1,
    rotation=90,
    # An auto-refreshing display repaints at 60 fps, and every frame is real SPI traffic the
    # emulator executes interleaved with the CYW43's own hot path. Refreshing once per status line
    # is what keeps this demo minutes rather than tens of minutes: a status panel, not an animation.
    auto_refresh=False,
)


def show(line):
    """Print one status line and push the panel once - the manual half of `auto_refresh=False`."""
    print(line)
    display.refresh()


# The panel is ~26x9 characters, so every line below is written to fit one row.
show("wifi test")
wifi.radio.enabled = True
show("mac ok")

# "RP2040PY-GUEST" is the fixed SSID the emulated chip answers scans with, and the passphrase's
# only requirement is WPA2's own 8-64 character rule - CircuitPython checks the length client-side
# before anything reaches the radio, while the emulator scripts the join unconditionally. Same
# call, and the same reasoning, as tests/circuitpython/main-cyw43.py.
wifi.radio.connect("RP2040PY-GUEST", "password")
show(f"connected: {wifi.radio.connected}")
# The last line the panel gets: a sixth would scroll this 5-row terminal, and a frame captured
# mid-scroll is a torn picture rather than a wrong one - real, but not what a screenshot is for.
# The gateway is `wifi.radio.ipv4_gateway`, one `print()` away if you want it on the console.
show(f"ip {wifi.radio.ipv4_address}")

# Idle rather than return: CircuitPython prints "Code done running." and its "Press any key to
# enter the REPL" message the moment code.py finishes, which on a 5-row terminal scrolls the lines
# above off the panel - i.e. off the screenshot this demo exists to produce.
#
# `auto_refresh` stays off in here too. Turning it back on once the join was over was tried, and it
# is what made an early version of this demo take forty minutes instead of one: 60 fps of
# full-framebuffer SPI pushes is exactly what the constructor comment above says it is.
while True:
    time.sleep(1)
'''

# What actually puts the file there: remount (CIRCUITPY is read-only to the REPL until something
# asks, and `[Errno 30] Read-only filesystem` is what you get if you skip this), write, and read
# the length back - the read is also what flushes CircuitPython's write cache.
_PUSH_TEMPLATE = """\
import os, storage
storage.remount('/', readonly=False)
with open('/code.py', 'wb') as fp:
    fp.write({code!r})
os.sync()
print('code.py written:', len(open('/code.py', 'rb').read()), 'bytes')
"""


def restart_into_code_py(device: MicroPythonDevice) -> None:
    """Make the firmware re-run `code.py`: Ctrl-B, then Ctrl-D.

    Measured 2026-08-20, and the measurement is the reason this is not the one-liner it looks like
    it should be. `supervisor.reload()` sent through `exec_async()` does **nothing** here - not a
    slow reload, no reload at all: `RawReplRunner` never sends Ctrl-B, so an exec leaves the device
    in the *raw* REPL, and a restart from there comes back to a raw prompt without running
    `code.py` (a bare Ctrl-D at the raw prompt behaves the same way - docs/reference/mpremote.md's
    "Soft reset: raw prompt vs friendly prompt"). A 20-minute run with `reload()` produced a silent
    console and zero frames. Ctrl-B first, so the Ctrl-D lands at the *friendly* prompt, and the
    console shows `soft reboot` -> `code.py output:` -> the new file's output."""
    for byte in (CTRL_B, CTRL_D):
        device.simulator.schedule_threadsafe(lambda value=byte: device.cdc.send_serial_byte(value))


def decode_frame(buf: bytes) -> Image.Image:
    """`St7735s.on_frame`'s raw RGB565 framebuffer (big-endian pairs, row-major over the visible
    window) as a Pillow image. The external device deliberately stops at bytes - turning them into
    a picture is the demo's job, not the emulator's."""
    image = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT))
    pixels = image.load()
    for y in range(LCD_HEIGHT):
        row = y * LCD_WIDTH * 2
        for x in range(LCD_WIDTH):
            value = (buf[row + x * 2] << 8) | buf[row + x * 2 + 1]
            pixels[x, y] = (((value >> 11) & 0x1F) << 3, ((value >> 5) & 0x3F) << 2, (value & 0x1F) << 3)
    return image


def text_lines(image: Image.Image) -> int:
    """How many text rows the panel is showing, below CircuitPython's status bar.

    Counting bands of non-black rows, not pixels: the terminal paints white-on-black, so each
    printed line is a band of rows with ink in them, and the topmost band is the status bar this
    firmware keeps repainting. Crude on purpose - it only has to answer "has the last line started
    appearing yet?", which no timer can answer here (the paint stalls for minutes mid-line)."""
    pixels = image.load()
    inked = [any(sum(pixels[x, y]) > 90 for x in range(image.width)) for y in range(image.height)]
    bands = sum(1 for y, ink in enumerate(inked) if ink and not inked[y - 1])
    return max(0, bands - 1)  # the status bar is a band too


def board_with(on_frame: "object") -> BoardSpec:
    """`--board pico_w`'s own spec (LED, BOOTSEL, CYW43439) plus the panel."""
    pico_w = BOARDS["pico_w"]
    return dataclasses.replace(pico_w, extras=(*pico_w.extras, lambda: St7735s(on_frame=on_frame)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=_DEFAULT_IMAGE, help="CircuitPython version tag (%(default)s)")
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=_DEFAULT_SCREENSHOT,
        metavar="PATH",
        help="write the last frame here (%(default)s)",
    )
    parser.add_argument(
        "--all-frames", metavar="PREFIX", help="also dump every frame as PREFIX_000.png, in the order they arrive"
    )
    parser.add_argument(
        "--frames", type=int, default=0, help="stop once N frames have arrived, 0 for no limit (%(default)s)"
    )
    parser.add_argument(
        "--until-text",
        default="ip ",
        help="stop once a console line *starts* with this (%(default)r) - the demo knows what its "
        "own last status line starts with, which beats guessing at a frame count. Line-anchored on "
        "purpose: the same text inside a status-bar escape sequence must not end the run",
    )
    parser.add_argument(
        "--console-log", type=Path, metavar="PATH", help="also write the guest's raw console bytes here"
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=300.0,
        help="after --until-text, give the panel at most N more seconds to catch up (%(default)s). "
        "It normally stops long before that, once frames stop changing",
    )
    parser.add_argument(
        "--until-lines",
        type=int,
        default=4,
        help="stop once the panel shows this many text lines below its status bar (%(default)s) - "
        "the guest prints exactly that many, and counting them beats waiting for the paint to stop, "
        "which it does for minutes at a time mid-line",
    )
    parser.add_argument(
        "--stable-seconds",
        type=float,
        default=30.0,
        help="after --until-lines, keep going until the panel has been unchanged for N seconds "
        "(%(default)s), so the last line finishes drawing",
    )
    parser.add_argument(
        "--idle",
        type=float,
        default=300.0,
        help="safety net: stop if the console says nothing for N seconds (%(default)s). Long, "
        "deliberately - the console finishes long before the panel does, and cutting the run off "
        "there costs you the last line",
    )
    parser.add_argument("--timeout", type=float, default=900.0, help="give up after N seconds (%(default)s)")
    args = parser.parse_args()

    # Raw bytes across the queue, decoded in the loop below. `on_frame` fires on the emulator's own
    # engine-room thread, so anything done in it - `decode_frame()` above, above all - is time the
    # emulated chip is not running. Measured: decoding inline throttled a run badly enough to look
    # like the panel itself was slow.
    frames: queue.Queue[bytes] = queue.Queue()
    board = resolve_firmware(board_with(frames.put), "circuitpython", args.image)
    device = MicroPythonDevice(board=board, circuitpython=True, log_level=LogLevel.ERROR)
    print(f"booting {board.image} (this takes minutes, not seconds)")
    device.start_async(timeout=_START_TIMEOUT_SECONDS).result()

    last: Image.Image | None = None
    seen = 0
    try:
        stdout, stderr = device.exec_async(
            _PUSH_TEMPLATE.format(code=GUEST_CODE.encode()), timeout=args.timeout
        ).result()
        print(stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip())
    except Exception as exc:  # noqa: BLE001 - report and still try the restart below
        print(f"pushing code.py failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    # Frames start arriving once the guest's own BusDisplay constructor runs, so an empty queue for
    # a while after this is the normal case, not a stall.
    restart_into_code_py(device)

    # What ends the run is the guest's own console, not the panel: CircuitPython repaints its status
    # bar every few seconds forever, so frames never stop arriving. Watching for the last status
    # line (`--until-text`) beats both a frame count and a quiet-console rule - the join itself is
    # silent for minutes, so "quiet" would stop the run in the middle of it, which is exactly what
    # a 60s version of this did: a screenshot that read `wifi test / mac ok` and nothing else.
    # A partial repaint is still a complete picture here - `on_frame` emits the whole visible
    # window, so untouched pixels are simply the ones already on the panel.
    spoke_last = [time.monotonic()]
    marker_at: list[float | None] = [None]
    console = bytearray()
    marker = ("\n" + args.until_text).encode()  # line-anchored; see --until-text

    def _console_activity(data: "bytes | bytearray") -> None:
        spoke_last[0] = time.monotonic()
        console.extend(data)
        if marker_at[0] is None and marker in bytes(console):
            marker_at[0] = time.monotonic()

    device.cdc.on_serial_data = _console_activity

    # The panel runs *minutes* behind the console. CircuitPython's terminal reveals text a glyph or
    # two per push - measured: a frame reading `wifi tes` while the console had already printed all
    # four lines - so the console marker is only the first half of "done". The second half is the
    # panel going still, and that has to be measured in seconds, not in frames: identical frames
    # arrive in bursts whenever the guest is busy, so "three the same" means nothing.
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    previous: bytes | None = None
    changed_at = time.monotonic()
    try:
        while (args.frames <= 0 or seen < args.frames) and (deadline is None or time.monotonic() < deadline):
            try:
                buffer = frames.get(timeout=0.5)
            except queue.Empty:
                pass
            else:
                # Drain to the newest frame before decoding. The emulator emits frames far faster
                # than a Python RGB565 decode can consume them, and a queue that grows means the
                # picture examined below is minutes stale - which looked exactly like a panel that
                # paints slowly. `--all-frames` opts back into decoding every one, for debugging.
                pending = [buffer]
                while not args.all_frames:
                    try:
                        pending.append(frames.get_nowait())
                    except queue.Empty:
                        break
                for raw in pending:
                    last = decode_frame(raw)
                    seen += 1
                    if args.all_frames:
                        last.save(f"{args.all_frames}_{seen - 1:03d}.png")
                assert last is not None
                current = last.tobytes()
                if current != previous:
                    changed_at = time.monotonic()
                previous = current
                print(f"frame {seen}")
            now = time.monotonic()
            if last is not None and text_lines(last) >= args.until_lines and now - changed_at > args.stable_seconds:
                print(f"panel shows {args.until_lines} lines and has been still for {args.stable_seconds:.0f}s")
                break
            if marker_at[0] is not None and now - marker_at[0] > args.settle:
                print(f"panel still changing {args.settle:.0f}s after {args.until_text!r} - taking what we have")
                break
            if now - spoke_last[0] > args.idle:
                print(f"console quiet for {args.idle:.0f}s - giving up on {args.until_text!r}")
                break
    except KeyboardInterrupt:
        pass
    finally:
        device.stop()

    if args.console_log is not None:
        args.console_log.write_bytes(bytes(console))
        print(f"console written to {args.console_log}")
    if last is None:
        print("no frames arrived - nothing to save", file=sys.stderr)
        raise SystemExit(1)
    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
    last.save(args.screenshot)
    print(f"{seen} frame(s); last one written to {args.screenshot}")


if __name__ == "__main__":
    main()
