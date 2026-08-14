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
"""Boots MicroPython with a virtual Waveshare 2.9inch e-Paper (G) display attached over SPI1,
pushes demo/mp_eink_demo.py to it over the raw REPL (a from-scratch driver + a small wipe
animation - see that file's docstring), and shows every refreshed frame either in a live Tkinter
window or as a numbered PNG screenshot sequence.

Pin wiring (fixed - matches mp_eink_demo.py, not independently configurable here): SPI1,
SCK=10, MOSI=11, CS=9, DC=8, RST=12, BUSY=13 (the Pico ePaper HAT layout).

Usage:
    python demo/eink_run.py --image 1.28 --tkinter                 # version tag, auto-downloaded
    python demo/eink_run.py --image firmware.uf2 --screenshot out  # local .uf2/.hex path
    python demo/eink_run.py                                        # default MicroPython tag

Requires Pillow (`pip install Pillow`); --tkinter additionally needs a Tk-enabled Python build.
`rp2040py.external.epd2in9g.Epd2in9G` itself has no Pillow dependency - decoding its raw frame
bytes into a picture is this demo's job, via `_decode_frame` below.
"""

import argparse
import os
import queue
import sys
import time
from pathlib import Path

from PIL import Image

from rp2040py.device import MicroPythonDevice
from rp2040py.external.device import attach_external_devices
from rp2040py.external.epd2in9g import EPD_HEIGHT, EPD_WIDTH, PALETTE, Epd2in9G
from rp2040py.utils.firmware_retrieve import MICROPYTHON, retrieve
from rp2040py.utils.logging import LogLevel

_DEMO_SCRIPT = Path(__file__).parent / "mp_eink_demo.py"

# Real hardware's BUSY hold (~500ms power-on, ~15s full refresh) is faithfully slow to actually
# emulate too - the core has to execute every instruction of the firmware's busy-poll loop for
# that whole simulated duration (measured: roughly 30x real time per simulated ms of
# utime.sleep_ms()). Shortened here - together with mp_eink_demo.py's own scaled-down delays - so
# the demo's refreshes finish in a reasonable time; still nonzero, so the BUSY-pin protocol itself
# is genuinely exercised, not skipped. Matches mp_eink_demo.py's BUSY_POLL_MS=2 granularity.
_DEMO_BUSY_NANOS_POWER = 2_000_000  # 2ms
_DEMO_BUSY_NANOS_REFRESH = 4_000_000  # 4ms


def _decode_frame(buf: bytes) -> Image.Image:
    """Turns `Epd2in9G.on_frame`'s raw packed 2bpp frame buffer into a Pillow RGB image,
    using its `PALETTE` (2-bit index -> RGB, in the vendor driver's own quantization order)."""
    image = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT))
    pixels = image.load()
    row_bytes = EPD_WIDTH // 4
    for y in range(EPD_HEIGHT):
        row_offset = y * row_bytes
        for xb in range(row_bytes):
            packed = buf[row_offset + xb]
            for i in range(4):
                pixels[xb * 4 + i, y] = PALETTE[(packed >> (6 - i * 2)) & 0x3]
    return image


class Renderer:
    def show(self, image: Image.Image) -> None:
        raise NotImplementedError

    def pump(self) -> None:
        """Called on every idle tick so a GUI renderer can process its event loop even while
        waiting for the next DISPLAY_REFRESH."""


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
    def __init__(self, scale: int = 2) -> None:
        import tkinter as tk

        from PIL import ImageTk

        self._image_tk_module = ImageTk
        self.scale = scale
        self.root = tk.Tk()
        self.root.title('Virtual Waveshare 2.9" e-Paper (G)')
        self.label = tk.Label(self.root)
        self.label.pack()
        self._photo: ImageTk.PhotoImage | None = None
        # A Label with no image yet collapses to a handful of pixels (confirmed: 4x35) - show a
        # blank placeholder immediately so the window opens at the panel's real size instead of
        # looking "stuck"/tiny until the first DISPLAY_REFRESH arrives.
        self.show(Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), "white"))

    def show(self, image: Image.Image) -> None:
        scaled = image.resize((image.width * self.scale, image.height * self.scale), Image.Resampling.NEAREST)
        self._photo = self._image_tk_module.PhotoImage(scaled)
        self.label.configure(image=self._photo)
        self.pump()

    def pump(self) -> None:
        self.root.update_idletasks()
        self.root.update()


def _drain(frame_queue: queue.Queue[Image.Image], renderer: Renderer) -> None:
    while True:
        try:
            renderer.show(frame_queue.get_nowait())
        except queue.Empty:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", help="version tag, local .hex/.uf2 file path, or omitted to download the default")
    render_group = parser.add_mutually_exclusive_group()
    render_group.add_argument("--tkinter", action="store_true", help="show frames in a live Tk window")
    render_group.add_argument(
        "--screenshot",
        metavar="PREFIX",
        nargs="?",
        const="eink_out",
        help="dump each refreshed frame as PREFIX_000.png, PREFIX_001.png, ... (default: %(const)s)",
    )
    args = parser.parse_args()

    renderer: Renderer = TkinterRenderer() if args.tkinter else ScreenshotRenderer(Path(args.screenshot or "eink_out"))

    # frame_queue exists because Epd2in9G.on_frame fires from MicroPythonDevice's own
    # engine-room thread (Simulator._ensure_loop()'s lazily-created background thread, since this
    # script never calls astart()/bind_loop() - see the device.start_async() comment below), not
    # this one; Tkinter widgets may only be touched from the thread that created them, so frames
    # are decoded and handed off here, only ever drawn from this loop below.
    frame_queue: queue.Queue[Image.Image] = queue.Queue()

    image_path = retrieve(MICROPYTHON, args.image)
    if image_path is None:
        print(f"Could not find MicroPython image: {args.image}")
        os._exit(1)

    device = MicroPythonDevice(image_path, log_level=LogLevel.ERROR)

    # attach_external_devices() (external/device.py) is only safe before Simulator.start_execution()
    # - so this runs before start_async() below, not after.
    epd = Epd2in9G(
        on_frame=lambda buf: frame_queue.put(_decode_frame(buf)),
        busy_nanos_power=_DEMO_BUSY_NANOS_POWER,
        busy_nanos_refresh=_DEMO_BUSY_NANOS_REFRESH,
    )
    attach_external_devices(device.mcu, epd)

    # MicroPythonDevice is async-native now (no blocking start()) - exec_file_async() staying
    # Future-based is unaffected. start_async() alone (no astart()/bind_loop() call) leaves this
    # Simulator on its own lazily-created background thread (Simulator._ensure_loop()), exactly
    # like the old dedicated worker thread this blocking .result() replaces - which is what lets
    # Epd2in9G.on_frame fire off-thread from this Tkinter-driving main thread.
    device.start_async().result()

    future = device.exec_file_async(_DEMO_SCRIPT, timeout=None)
    try:
        while not future.done():
            _drain(frame_queue, renderer)
            renderer.pump()
            time.sleep(0.05)
    except KeyboardInterrupt:
        device.stop()
        os._exit(130)

    stdout, stderr = future.result()
    _drain(frame_queue, renderer)
    if stdout:
        print(stdout.decode(errors="replace"), end="")
    if stderr:
        print(stderr.decode(errors="replace"), end="", file=sys.stderr)

    # Keep a Tk window open (and responsive) after the demo finishes so the final frame stays
    # visible, instead of the window vanishing the instant the script completes.
    if isinstance(renderer, TkinterRenderer):
        try:
            while True:
                renderer.pump()
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass

    device.stop()


if __name__ == "__main__":
    main()
