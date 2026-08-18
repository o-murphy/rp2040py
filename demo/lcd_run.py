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
"""Boots a Waveshare RP2040-LCD-0.96 with its onboard ST7735S panel emulated
(`rp2040py.external.st7735s`) and shows every frame the firmware draws, either in a live Tkinter
window or as a numbered PNG sequence - the LCD counterpart of demo/eink_run.py, and the thing that
produced the screenshots in docs/records/0056.

Two firmwares, from the one board file under boards/ (this script is also a worked example of
loading one from an ordinary Python program, the same `BoardSpec` a `--board-spec` CLI run gets,
resolved for a firmware family the same way the CLI resolves it):

    python demo/lcd_run.py --screenshot out            # MicroPython + demo/mp_lcd_demo.py
    python demo/lcd_run.py --circuitpython --tkinter   # CircuitPython, no guest code at all

`--circuitpython` needs no script: that firmware initialises the panel itself in `board_init()`
and auto-refreshes it, so frames start arriving from the boot alone. MicroPython does need a
driver, and gets `demo/mp_lcd_demo.py` pushed over the raw REPL.

The board file declares both firmwares as data and downloads nothing when imported;
`resolve_firmware(board, family)` below turns the one this run needs into a real image, through
`retrieve()` - which downloads on first use and caches under `~/.cache/rp2040py`. With no network,
drop the `.uf2` there yourself under the exact filename its download URL ends with - `retrieve()`
finds a cached file before it tries to fetch anything, so no flag is needed for an offline run.

Requires Pillow (`pip install Pillow`); `--tkinter` additionally needs a Tk-enabled Python build.
`rp2040py.external.st7735s.St7735s` itself has no Pillow dependency - decoding its raw RGB565
frame bytes into a picture is this demo's job, via `_decode_frame` below (same boundary
demo/eink_run.py draws for the e-paper panel).
"""

import argparse
import queue
import sys
import time
from pathlib import Path

from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))  # boards/ lives at the repo root, outside the installed package

from rp2040py.boards import resolve_firmware
from rp2040py.device import MicroPythonDevice
from rp2040py.external.st7735s import LCD_HEIGHT, LCD_WIDTH
from rp2040py.utils.logging import LogLevel

_DEMO_SCRIPT = Path(__file__).parent / "mp_lcd_demo.py"


def _decode_frame(buf: bytes) -> Image.Image:
    """Turns `St7735s.on_frame`'s raw RGB565 framebuffer (big-endian pairs, row-major over the
    visible window) into a Pillow RGB image."""
    image = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT))
    pixels = image.load()
    for y in range(LCD_HEIGHT):
        row = y * LCD_WIDTH * 2
        for x in range(LCD_WIDTH):
            value = (buf[row + x * 2] << 8) | buf[row + x * 2 + 1]
            pixels[x, y] = (((value >> 11) & 0x1F) << 3, ((value >> 5) & 0x3F) << 2, (value & 0x1F) << 3)
    return image


class Renderer:
    def show(self, image: Image.Image) -> None:
        raise NotImplementedError

    def pump(self) -> None:
        """Called on every idle tick so a GUI renderer can process its event loop."""


class ScreenshotRenderer(Renderer):
    def __init__(self, path_prefix: Path) -> None:
        self.path_prefix = path_prefix
        self._frame_index = 0

    def show(self, image: Image.Image) -> None:
        path = self.path_prefix.with_name(f"{self.path_prefix.name}_{self._frame_index:03d}.png")
        image.save(path)
        print(f"Frame {self._frame_index} written to {path}")
        self._frame_index += 1


class TkinterRenderer(Renderer):
    def __init__(self, scale: int = 3) -> None:
        import tkinter as tk

        from PIL import ImageTk

        self._image_tk_module = ImageTk
        self.scale = scale
        self.root = tk.Tk()
        self.root.title("Virtual Waveshare RP2040-LCD-0.96 (ST7735S)")
        self.label = tk.Label(self.root)
        self.label.pack()
        self._photo: ImageTk.PhotoImage | None = None
        self.show(Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), "black"))

    def show(self, image: Image.Image) -> None:
        scaled = image.resize((image.width * self.scale, image.height * self.scale), Image.Resampling.NEAREST)
        self._photo = self._image_tk_module.PhotoImage(scaled)
        self.label.configure(image=self._photo)
        self.pump()

    def pump(self) -> None:
        self.root.update_idletasks()
        self.root.update()


def _expired(deadline: "float | None") -> bool:
    return deadline is not None and time.monotonic() > deadline


def _budget(frame_limit: int, shown: int) -> "int | None":
    return None if frame_limit <= 0 else frame_limit - shown


def _drain(frame_queue: "queue.Queue[Image.Image]", renderer: Renderer, budget: "int | None" = None) -> int:
    """Shows every queued frame, or at most `budget` of them - the caller's frame limit has to
    bound the drain itself, not just how often it runs: CircuitPython queues frames faster than
    this loop spins, so draining "everything available" once overshoots any limit by a wide
    margin (measured: 87 PNGs written for a `--frames 3` run before this was fixed)."""
    drained = 0
    while budget is None or drained < budget:
        try:
            renderer.show(frame_queue.get_nowait())
        except queue.Empty:
            break
        drained += 1
    return drained


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--circuitpython",
        action="store_true",
        help="boot the CircuitPython board file instead of the MicroPython one (paints by itself)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="stop after N frames (default: 5 under --circuitpython --screenshot, since that "
        "firmware never stops painting; MicroPython instead runs until its driver script finishes)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="give up after this many seconds regardless (0: no limit). %(default)s by default - "
        "CircuitPython auto-refreshes forever, so an unbounded run has no natural end",
    )
    render_group = parser.add_mutually_exclusive_group()
    render_group.add_argument("--tkinter", action="store_true", help="show frames in a live Tk window")
    render_group.add_argument(
        "--screenshot",
        metavar="PREFIX",
        nargs="?",
        const="lcd_out",
        help="dump each frame as PREFIX_000.png, PREFIX_001.png, ... (default: %(const)s)",
    )
    args = parser.parse_args()

    renderer: Renderer = TkinterRenderer() if args.tkinter else ScreenshotRenderer(Path(args.screenshot or "lcd_out"))
    # A live window is watched until the user closes it; a PNG dump needs a bound, or a
    # CircuitPython run writes frames until something kills it.
    frame_limit = args.frames if args.frames is not None else (5 if args.circuitpython and not args.tkinter else 0)
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None

    # frame_queue exists because St7735s.on_frame fires from the device's own engine-room thread,
    # not this one; frames are decoded there and only ever drawn from the loop below (Tkinter
    # widgets may only be touched from the thread that created them). Same shape as eink_run.py.
    frame_queue: queue.Queue[Image.Image] = queue.Queue()

    from boards.waveshare_rp2040_lcd_0_96 import board_with

    # One board file, two firmware families - `--circuitpython` picks which of its own `firmware`
    # declarations to resolve, exactly as the CLI's own flag does (docs/records/0059).
    board = resolve_firmware(
        board_with(lambda buf: frame_queue.put(_decode_frame(buf))),
        "circuitpython" if args.circuitpython else "micropython",
    )
    device = MicroPythonDevice(board=board, circuitpython=args.circuitpython, log_level=LogLevel.ERROR)
    device.start_async().result()

    shown = 0
    try:
        if args.circuitpython:
            # Nothing to push: board_init() already started painting, and never stops - so this
            # loop ends on --frames, on --timeout, or on Ctrl-C, never "when the firmware is done".
            while (frame_limit <= 0 or shown < frame_limit) and not _expired(deadline):
                shown += _drain(frame_queue, renderer, _budget(frame_limit, shown))
                renderer.pump()
                time.sleep(0.05)
        else:
            future = device.exec_file_async(_DEMO_SCRIPT, timeout=None)
            while not future.done() and not _expired(deadline) and (frame_limit <= 0 or shown < frame_limit):
                shown += _drain(frame_queue, renderer, _budget(frame_limit, shown))
                renderer.pump()
                time.sleep(0.05)
            if not future.done():
                print(f"timed out after {args.timeout}s with {shown} frame(s)", file=sys.stderr)
                device.stop()
                return
            stdout, stderr = future.result()
            shown += _drain(frame_queue, renderer, _budget(frame_limit, shown))
            if stdout:
                print(stdout.decode(errors="replace"), end="")
            if stderr:
                print(stderr.decode(errors="replace"), end="", file=sys.stderr)
    except KeyboardInterrupt:
        pass

    # Keep a Tk window open (and responsive) after the run so the final frame stays visible.
    if isinstance(renderer, TkinterRenderer):
        import tkinter

        try:
            while True:
                _drain(frame_queue, renderer)
                renderer.pump()
                time.sleep(0.05)
        except (KeyboardInterrupt, tkinter.TclError):  # TclError: the user closed the window
            pass

    device.stop()


if __name__ == "__main__":
    main()
