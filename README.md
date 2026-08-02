# rp2040py

[![license]][license-url]
[![pypi version]][PyPiUrl]
[![python versions]][PyPiUrl]
[![Pre-commit]][pre-commit-workflow]
[![Test MicroPython Releases]][micropython-workflow]
[![Test Pi Pico SDK]][pico-sdk-workflow]
[![coverage]][CodecovUrl]

Raspberry Pi Pico (RP2040) Emulator in Python — a faithful port of [rp2040js](https://github.com/wokwi/rp2040js). It blinks, runs native code, and even the MicroPython REPL!

See [docs/PORTING.md](docs/PORTING.md) for the file-by-file port status against upstream rp2040js.

## Installation

```sh
pip install rp2040py
```

or, with [uv](https://docs.astral.sh/uv/):

```sh
uv add rp2040py       # into a project
uv tool install rp2040py   # as a standalone CLI tool
uvx rp2040py ...           # run without installing at all
```

Any of these gives you the `rp2040py` console script (`python -m rp2040py` works identically), so
the emulator is runnable without a git checkout - see [Run the demo project](#run-the-demo-project)
below for the checkout-equivalent commands.

## Run the demo project

The commands below assume `rp2040py` is installed (`pip install rp2040py` / `uv add rp2040py` /
`uv tool install rp2040py`, or run ad hoc with `uvx rp2040py ...`). From a checkout of this repo
instead, each maps 1:1 onto `uv run python demo/*.py` (`demo/*.py` are thin wrappers around the
same [src/rp2040py/cli](src/rp2040py/cli) code):

| `rp2040py` subcommand | Checkout equivalent |
|---|---|
| `rp2040py run ...` | `uv run python demo/emulator_run.py ...` |
| `rp2040py micropython ...` | `uv run python demo/micropython_run.py ...` |
| `rp2040py kaluma ...` | `uv run python demo/kaluma_run.py ...` |
| `rp2040py bench ...` | `uv run python demo/benchmark.py ...` |

### Native code

You'd need to get `hello_uart.hex` by building it from the [pico-examples repo](https://github.com/raspberrypi/pico-examples/tree/master/uart/hello_uart), then copy it to the rp2040py root directory and run:

```sh
rp2040py run
# or, without installing:
uvx rp2040py run
```

You can also specify the path to the image on the command line and/or load a UF2 image:

```sh
rp2040py run --image ./my-pico-project.uf2
```

A GDB server will be available on port 3333, and the data written to UART0 will be printed
to the console.

### MicroPython code

No manual download needed: just run

```sh
rp2040py micropython
# or, without installing:
uvx rp2040py micropython
```

and enjoy the MicroPython REPL! Quit the REPL with Ctrl+X. The first run fetches the recommended
MicroPython build (**1.21.0**, currently) from [micropython.org](https://micropython.org/download/RPI_PICO/)
into the current directory and reuses that local file afterwards. 1.21 is recommended: it does far
less work before dropping to the REPL prompt than newer releases, so it boots dramatically faster in
the emulator (see the benchmark below). Newer releases work too, just slower to reach the REPL -
e.g. 1.28.0.

A different version, a local UF2 file, or a CircuitPython version (`--circuitpython`, see below) can
be loaded by supplying the `--image` option - a known version tag (`1.28.0`), or a path to a UF2
file already on disk:

> [!TIP]
> Booting real firmware means executing millions of Thumb instructions through a pure-Python
> interpreter, which is dramatically slower than V8 JIT-compiling the equivalent JS in rp2040js.
> Measured with [demo/benchmark.py](demo/benchmark.py) booting MicroPython 1.28 + littlefs to the
> REPL:
>
> | Interpreter | Time |
> |---|---|
> | CPython 3.10 | 221.11s |
> | CPython 3.14 + `PYTHON_JIT=1` | 121.74s (~1.8x) |
> | PyPy 3.10 | 9.59s (~23x) |
>
> For CPU-bound runs, PyPy is the clear winner: `uv run --python pypy3.10 --no-dev -- rp2040py
> micropython ...` (or `... -- python demo/micropython_run.py ...` from a checkout). See
> [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for the full breakdown
> (including a synthetic instructions/sec benchmark) and CI's `python_runtime` matrix, which tests
> all three.
>
> This is also why **1.21 is the recommended version**: on the same machine and CPython 3.10,
> 1.21 reaches the REPL in 6.85s (2,000,000 steps) versus 1.28's 160.35s (65,000,000 steps) - over
> 20x fewer steps to boot the same emulator. 1.28 works fine too (as does the littlefs image
> produced by `mklittlefs`, mounting correctly on both), it's just a much slower REPL to reach
> interactively.

```sh
rp2040py micropython --image 1.28.0
rp2040py micropython --image my_image.uf2
```

A GDB server on port 3333 can be enabled by specifying the `--gdb` flag:

```sh
rp2040py micropython --gdb
```

For using the MicroPython demo code in tests, `--expect-text` can come in handy: it will look for the given text in the serial output and exit with code 0 if found, or 1 if not found. You can find an example in [the MicroPython CI test](./.github/workflows/ci-micropython.yml).

For one-shot, non-interactive runs (like `micropython`'s own CLI), pass one of `-c <command>`, `-m <module>`, or a script `<filename>` - mutually exclusive, matching `[-c <command> | -m <module> | <filename>]`. Instead of dropping into the REPL, rp2040py boots the device, runs it via the raw-REPL protocol, prints its stdout/stderr, and exits with the device's exit status (0 on success, 1 if it raised):

```sh
rp2040py micropython -c "print(1 + 1)"
rp2040py micropython -m sys
rp2040py micropython path/to/script.py
```

#### Filesystem support

With MicroPython, you can use the filesystem on the Pico. This becomes useful as more than one script file is used in your code. Just put a [LittleFS](https://github.com/littlefs-project/littlefs) formatted filesystem image called `littlefs.img` into the rp2040py root directory, and your `main.py` will be automatically started from there. A different path can be supplied with `--littlefs` (it's silently skipped, not an error, if the file doesn't exist).

The `mklittlefs` subcommand builds such an image (requires the optional `fs` extra: `pip install
rp2040py[fs]` / `uv sync --extra fs`). Every file keeps its own basename; pass `--main` to mark one
of them as `main.py` (auto-run on boot) - omit it entirely for a filesystem with no auto-run
script, e.g. modules staged for a raw-REPL-driven test, or omit `files` entirely for an empty
formatted image. Always builds fresh - pass `-f`/`--force` to overwrite an existing `--output`
(there's no "add these files to the existing image" mode; rebuild from the full file list):

```sh
rp2040py mklittlefs -o littlefs.img your_main.py your.py files.py here.py --main your_main.py
rp2040py mklittlefs -o littlefs.img --force your_main.py --main your_main.py  # to overwrite it later
```

`--disk-version {2.0,2.1}` selects the littlefs on-disk format (defaults to `2.0`): MicroPython
<=1.21's bundled littlefs can only mount `2.0`, while 1.28's reads both - see
[docs/PORTING.md](docs/PORTING.md#littlefs-image-format-vs-old-micropython-not-actually-a-port-bug)
for why.

Currently, the filesystem is not writeable, as the SSI peripheral required for flash writing is not implemented yet.

### CircuitPython code

To run the CircuitPython demo, follow the directions above for MicroPython but add `--circuitpython`:

```sh
rp2040py micropython --circuitpython
```

and start the CircuitPython REPL! As with MicroPython, the firmware (**8.0.2** by default) is
downloaded automatically on first use; a different version or a local file can be given via
`--image` (e.g. `--image 10.2.1` or a path to an already-downloaded UF2). The rest of the experience
is the same as the MicroPython demo (Ctrl+X to exit, the `--gdb` option, etc).

#### Filesystem support

For CircuitPython, you can create a FAT12 filesystem in Linux using the `truncate` and `mkfs.vfat` utilities:

```shell
truncate fat12.img -s 1M  # make the image file
mkfs.vfat -F12 -S512 fat12.img  # create the FAT12 filesystem
```

You can then mount the filesystem image and add files to it:

```shell
mkdir fat12  # create the mounting folder if needed
sudo mount -o loop fat12.img fat12/  # mount the filesystem to the folder
sudo cp code.py fat12/  # copy code.py to the filesystem
sudo umount fat12/  # unmount the filesystem
```

While CircuitPython does not typically use a writeable filesystem, note that this functionality is unavailable (see the MicroPython filesystem support section for more details).

### Kaluma (other USB-CDC firmware, not MicroPython/CircuitPython)

rp2040py's USB/CDC emulation isn't MicroPython-specific - any firmware presenting a CDC-ACM serial
console works the same way underneath. The `kaluma` subcommand runs [Kaluma](https://kaluma.io/)
(a JavaScript runtime for RP2040), verified against 1.2.1 - it boots, USB enumerates, and evaluates
real JS at its REPL prompt (e.g. sending `1+1` gets back `2`):

```sh
rp2040py kaluma
# or, without installing:
uvx rp2040py kaluma
rp2040py kaluma --image 1.2.1
rp2040py kaluma --image my_kaluma_image.uf2
```

As with `micropython`, missing firmware is downloaded automatically (**1.2.1** by default - the
newest release still shipping a plain, non-`-w`, RP2040 `pico` build; 1.3.0+ only ships
`pico2`/`pico2-w`). Ctrl+X to exit, same as the MicroPython demo. Unlike `micropython`, `kaluma` is
interactive-only - Kaluma has no raw-REPL-equivalent protocol, so there's no `-c`/`-m`/`<filename>`.

An optional `<script.js>` positional stages a local file into Kaluma's "user program" flash
region before boot - the same one `kaluma flash <file>` writes to on real hardware, which Kaluma
auto-executes on every boot:

```sh
rp2040py kaluma your_script.js
```

> [!WARNING]
> This isn't verified working end to end yet. The flash write itself is correct (unit-tested), but
> the staged program's output was never observed in manual testing. The littlefs-mount failure
> below turned out *not* to be the cause - Kaluma catches and prints that error without aborting,
> so auto-run still runs afterward regardless. Current best guess: Kaluma gates auto-run on reading
> a GPIO pin (GP22) with a pull-up enabled, and this emulator doesn't resolve pull-up/pull-down
> state into an actual bus reading for pins nothing drives - likely always reading "skip loading"
> here. Not yet fixed or confirmed. `--expect-text` against the program's own output can't be
> relied on here; `ci-kaluma.yml` deliberately only checks the boot banner, not this.

Separately, Kaluma has its own pluggable littlefs-backed filesystem (see
[its docs](https://kalumajs.org/docs/api/file-system)), mounted from a 512K region of flash with
4096-byte blocks - a *different* flash region than the user-program one above, with no auto-run
semantics of its own (plain storage, accessible from JS via `require('fs')`). Build a compatible
image with `mklittlefs` and pass it via `--littlefs` (defaults to `kaluma_littlefs.img` - a
different default than MicroPython's `littlefs.img`, since the block size/count differ):

```sh
rp2040py mklittlefs -o kaluma_littlefs.img --block-size 4096 --block-count 128 your_script.js
rp2040py kaluma --littlefs kaluma_littlefs.img
```

This is also where the boot-time littlefs-mount failure above comes from: Kaluma's own `board.js`
tries to mount this filesystem unconditionally at startup and currently fails in this emulator -
logging `Bad block at 0x0`/`Superblock 0x0 has become unwritable`/`Error: No space left on
device` - reproduced even against a validly-built `mklittlefs` image, not just blank flash.
`--expect-text` (same as `micropython`'s, watching serial output for a substring) still works for
a plain boot-smoke-test regardless: Kaluma prints its "Welcome to Kaluma" banner unconditionally
*before* `board.js` runs, so it's unaffected by this (see
[the Kaluma CI test](./.github/workflows/ci-kaluma.yml)).

### Library API

Everything above is the CLI, but the emulator is also usable programmatically - e.g. to run code against a device and check its output the way [Thonny](https://thonny.org/) does over a real serial port, from a test suite or another tool. `rp2040py.device.MicroPythonDevice` boots a UF2 image and lets you run code on it via the same raw-REPL protocol `mpremote run`/`tools/pyboard.py` use, interrupting anything already running on the device first (e.g. an auto-run `main.py` from a littlefs image).

**Blocking** (`exec()`/`exec_file()`) is the simplest form - each call returns once the device finishes, or raises `TimeoutError` after `timeout` elapses (30s by default, since unlike the CLI there's no Ctrl+C to fall back on):

```python
from rp2040py.device import MicroPythonDevice

with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
    stdout, stderr = device.exec("print(1 + 1)")
    assert stdout == b"2\r\n"

    stdout, stderr = device.exec_file("my_script.py")
```

**Callback style**, via `exec_async()`'s `concurrent.futures.Future` - no separate API needed, `Future.add_done_callback()` does this out of the box:

```python
def on_done(future):
    stdout, stderr = future.result()
    print(stdout.decode())


device.exec_async("print(1 + 1)").add_done_callback(on_done)
```

**asyncio**, via `astart()`/`aexec()`/`aexec_file()`:

```python
async def main():
    async with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
        stdout, stderr = await device.aexec("print(1 + 1)")
```

All of these - blocking, callback, and asyncio - share one `ThreadPoolExecutor(max_workers=1)` per device: since the device only has a single REPL channel and can't run two `exec()`s at once, calling `exec_async()`/`aexec()` again before a previous call finishes doesn't raise, it just queues behind it and runs once its turn comes. This is exactly what powers the CLI's own `micropython -c/-m/<filename>` batch mode - it's a caller of this same API, not a separate implementation. `start()`/`start_async()`/`stop()` are available directly if you want more control over the lifecycle than the context manager gives you.

## Learn more

- [rp2040js](https://github.com/wokwi/rp2040js) — the upstream TypeScript emulator this project is ported from.
- [docs/PORTING.md](docs/PORTING.md) — port status, file by file.

## License

Released under the MIT license. Copyright (c) 2021, Uri Shaked. Copyright (c) 2026, Dmytro Yaroshenko.

<!-- REUSABLE LINKS -->

[license]:
https://img.shields.io/github/license/o-murphy/rp2040py

[license-url]:
https://opensource.org/licenses/MIT

[pypi version]:
https://img.shields.io/pypi/v/rp2040py?logo=pypi

[python versions]:
https://img.shields.io/pypi/pyversions/rp2040py?logo=python

[PyPiUrl]:
https://pypi.org/project/rp2040py/

[Pre-commit]:
https://github.com/o-murphy/rp2040py/actions/workflows/pre-commit.yml/badge.svg

[pre-commit-workflow]:
https://github.com/o-murphy/rp2040py/actions/workflows/pre-commit.yml

[Test MicroPython Releases]:
https://github.com/o-murphy/rp2040py/actions/workflows/ci-micropython.yml/badge.svg

[micropython-workflow]:
https://github.com/o-murphy/rp2040py/actions/workflows/ci-micropython.yml

[Test Pi Pico SDK]:
https://github.com/o-murphy/rp2040py/actions/workflows/ci-pico-sdk.yml/badge.svg

[pico-sdk-workflow]:
https://github.com/o-murphy/rp2040py/actions/workflows/ci-pico-sdk.yml

[coverage]:
https://codecov.io/gh/o-murphy/rp2040py/graph/badge.svg

[CodecovUrl]:
https://codecov.io/gh/o-murphy/rp2040py
