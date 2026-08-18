# Demos

Runnable examples. The `*_run.py` scripts are host-side programs; the `mp_*.py` files are
MicroPython code that runs *inside* the emulated firmware, pushed over the raw REPL by their
runner.

| script | what it does |
|---|---|
| [`emulator_run.py`](emulator_run.py) | raw `.hex`/`.uf2` program, GDB server on 3333 (`rp2040py run`) |
| [`micropython_run.py`](micropython_run.py) | MicroPython/CircuitPython REPL (`rp2040py micropython`) |
| [`kaluma_run.py`](kaluma_run.py) | Kaluma REPL (`rp2040py kaluma`) |
| [`benchmark.py`](benchmark.py) | instruction-throughput benchmark (`rp2040py bench`) |
| [`mklittlefs_dump.py`](mklittlefs_dump.py) | build/inspect a littlefs image |
| [`eink_run.py`](eink_run.py) + [`mp_eink_demo.py`](mp_eink_demo.py) | virtual Waveshare 2.9″ e-Paper (G) over SPI1, with a from-scratch driver |
| [`lcd_run.py`](lcd_run.py) + [`mp_lcd_demo.py`](mp_lcd_demo.py) | Waveshare RP2040-LCD-0.96's onboard 160×80 ST7735S panel, MicroPython or CircuitPython |

## What the display demos actually produce

Every image below came out of the emulator - each is the raw framebuffer a device emitted
(`on_frame`), decoded to PNG by the runner that ran it. Shown scaled up; the files in
[`screenshots/`](screenshots/) are the panels' real pixel sizes.

### `lcd_run.py` - ST7735S, 160×80

```sh
uv run --with pillow python demo/lcd_run.py --screenshot out
```

MicroPython, driven by `mp_lcd_demo.py` (a port of Waveshare's own sample driver):

<img src="screenshots/lcd-micropython-frame1.png" width="480" alt="ST7735S showing green 'rp2040py' and red 'ST7735S' text, a blue line and red/green/blue rectangles">
<img src="screenshots/lcd-micropython-frame2.png" width="480" alt="ST7735S showing black 'frame 2' text on white">

```sh
uv run --with pillow python demo/lcd_run.py --circuitpython --screenshot out
```

CircuitPython, with **no guest code at all** - that firmware initialises the panel in
`board_init()` and repaints it itself, so booting the board is the whole demo. The frames are
partial repaints, which is exactly how the firmware draws:

<img src="screenshots/lcd-circuitpython-boot-banner.png" width="480" alt="ST7735S showing part of the CircuitPython boot banner being drawn">
<img src="screenshots/lcd-circuitpython-console.png" width="480" alt="ST7735S showing the CircuitPython console with the board id">

The two runs also drive the panel through *different* orientations (`MADCTL` `0xA8` vs `0xC8`,
with transposed window offsets); both come out upright because the emulated controller applies
that mapping - see [record 0056](../docs/records/0056-st7735s-waveshare-lcd-board.md).

### `eink_run.py` - Waveshare 2.9″ e-Paper (G), 128×296

```sh
uv run --with pillow python demo/eink_run.py --screenshot out
```

First and last frames of the sunrise animation (4-colour BWYR panel, so the sky quantises to
white and the sun to yellow/red):

<img src="screenshots/eink-sunrise-frame0.png" width="256" alt="e-paper frame with the sun just below the horizon">
<img src="screenshots/eink-sunrise-frame5.png" width="256" alt="e-paper frame with the sun fully risen">

Both runners also take `--tkinter` for a live window instead of PNGs, and `--timeout` to bound a
run. See
[docs/reference/external-devices-and-boards.md](../docs/reference/external-devices-and-boards.md)'s
"Seeing what a display device drew" for how to point either at your own device.
