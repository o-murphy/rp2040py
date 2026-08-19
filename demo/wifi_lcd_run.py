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
"""Boots a Pico W with an ST7735S panel wired to it and screenshots the CircuitPython WiFi join -
the demo that shows a real `wifi.radio.connect()` on a display, which demo/lcd_run.py structurally
cannot (the Waveshare RP2040-LCD-0.96 has no radio, and its CircuitPython build therefore has no
`wifi` module at all - see docs/records/0085).

    python demo/wifi_lcd_run.py --screenshot out
    python demo/wifi_lcd_run.py --image 9.2.9 --tkinter

Two emulated devices at once, which is the point: `Cyw43439` (docs/records/0027, 0048) comes from
the built-in `pico_w` board, and `St7735s` (docs/records/0056) is added to it here - the same
`ExternalDevice` composition a `--board-spec` board file does, done from an ordinary program.
Nothing about either device knows the other exists.

The guest half is demo/cp_wifi_lcd_demo.py, written into a CIRCUITPY image as `code.py` (via
demo/mkfat12.py) rather than pushed over the raw REPL: CircuitPython auto-runs it, so this is the
"standard CircuitPython setup" case, and it means the run needs no REPL interaction at all. That
file builds the display itself, since a Pico W's `board_init()` builds none.

`--image` defaults to CircuitPython 10.2.1. Note that docs/records/0048's live WiFi verification
was done against **9.2.9**, which is also what `.github/workflows/ci-circuitpython.yml` pins its
WLAN job to; `--image 9.2.9` is the flag to fall back to if 10.x misbehaves on the radio path.

Expect this to be slow even by this emulator's standards: the CYW43's PIO/gSPI hot path is the
heaviest thing here (docs/records/0047), and the panel adds SPI traffic on top - which is why the
guest code refreshes the display explicitly instead of leaving `auto_refresh` on, and why
`--timeout` defaults to 15 minutes rather than lcd_run.py's 2.
"""

import argparse
import dataclasses
import queue
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Reused rather than re-written: frame decoding and the two renderers are identical to the LCD
# demo's - this file differs in which board it builds and what it puts on the drive, not in how a
# frame becomes a picture.
from demo.lcd_run import Renderer, ScreenshotRenderer, TkinterRenderer, _budget, _decode_frame, _drain, _expired
from demo.mkfat12 import build_image
from rp2040py.boards import BOARDS, BoardSpec, resolve_firmware
from rp2040py.device import MicroPythonDevice
from rp2040py.external.st7735s import St7735s
from rp2040py.utils.logging import LogLevel

_DEMO_CODE = Path(__file__).parent / "cp_wifi_lcd_demo.py"
_DEFAULT_IMAGE = "10.2.1"
# The CIRCUITPY drive on a Pico W under CircuitPython: 2 MiB of flash minus this board's
# `CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR` of 0x180000 (its `firmware_size` override of 1532K, plus
# 4 KiB of NVM - the audit in docs/records/0035, and the `fs_start` its firmware_specs.json entry
# already carries). Half of what the Waveshare board's own 0x100000 leaves, hence not a constant
# shared with mkfat12's default.
_VOLUME_BYTES = 0x200000 - 0x180000


def board_with(on_frame: "object") -> BoardSpec:
    """`--board pico_w`'s own spec (LED, BOOTSEL, CYW43439) plus the panel, whose constructor
    defaults - SPI1, CS=GP9, DC=GP8, RST=GP12 - are already free of the radio's GP23/24/25/29."""
    pico_w = BOARDS["pico_w"]
    return dataclasses.replace(pico_w, extras=(*pico_w.extras, lambda: St7735s(on_frame=on_frame)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=_DEFAULT_IMAGE, help="CircuitPython version tag (%(default)s)")
    parser.add_argument("--code", type=Path, default=_DEMO_CODE, help="guest code.py (%(default)s)")
    parser.add_argument(
        "--no-code",
        action="store_true",
        help="boot with an empty CIRCUITPY instead - nothing builds a display, so no frames arrive at all",
    )
    parser.add_argument("--frames", type=int, default=0, help="stop after N frames (0: no limit)")
    parser.add_argument("--timeout", type=float, default=900.0, help="give up after N seconds (%(default)s)")
    parser.add_argument("--dump-fs", type=Path, metavar="PATH", help="write CIRCUITPY back out after the run")
    render_group = parser.add_mutually_exclusive_group()
    render_group.add_argument("--tkinter", action="store_true", help="show frames in a live Tk window")
    render_group.add_argument(
        "--screenshot", metavar="PREFIX", nargs="?", const="wifi_lcd_out", help="dump each frame as PREFIX_000.png"
    )
    args = parser.parse_args()

    renderer: Renderer = (
        TkinterRenderer() if args.tkinter else ScreenshotRenderer(Path(args.screenshot or "wifi_lcd_out"))
    )
    frame_queue: queue.Queue[Image.Image] = queue.Queue()
    board = resolve_firmware(board_with(lambda buf: frame_queue.put(_decode_frame(buf))), "circuitpython", args.image)
    assert board.layout is not None

    with tempfile.TemporaryDirectory() as tmp:
        fat12 = None
        if not args.no_code:
            fat12 = Path(tmp) / "circuitpy.img"
            fat12.write_bytes(
                build_image(
                    {"code.py": args.code.read_bytes()},
                    volume_bytes=_VOLUME_BYTES,
                    image_bytes=board.layout.fs_blocksize * board.layout.fs_blockcount,
                )
            )
        device = MicroPythonDevice(board=board, circuitpython=True, fat12=fat12, log_level=LogLevel.ERROR)
    # Same reason as lcd_run.py's own raised ceiling, doubled: a freshly formatted CIRCUITPY costs
    # the firmware several flash writes before USB comes up, and this board is running the CYW43's
    # hot path alongside everything else.
    device.start_async(timeout=360.0).result()

    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    shown = 0
    try:
        # Frames only start once the guest's own BusDisplay constructor runs, so an empty queue
        # early in the run is the normal case here, not a stall.
        while (args.frames <= 0 or shown < args.frames) and not _expired(deadline):
            shown += _drain(frame_queue, renderer, _budget(args.frames, shown))
            renderer.pump()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    if isinstance(renderer, TkinterRenderer):
        import tkinter

        try:
            while True:
                _drain(frame_queue, renderer)
                renderer.pump()
                time.sleep(0.05)
        except (KeyboardInterrupt, tkinter.TclError):
            pass

    if args.dump_fs is not None:
        device.dump_flash_image(args.dump_fs)
        print(f"CIRCUITPY written to {args.dump_fs}")
    print(f"{shown} frame(s)")
    device.stop()


if __name__ == "__main__":
    main()
