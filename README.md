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

To run the MicroPython demo, first download [RPI_PICO-20230426-v1.20.0.uf2](https://micropython.org/resources/firmware/RPI_PICO-20230426-v1.20.0.uf2), place it in the rp2040py root directory, then run:

```sh
rp2040py micropython
# or, without installing:
uvx rp2040py micropython
```

and enjoy the MicroPython REPL! Quit the REPL with Ctrl+X. A different MicroPython UF2 image can be loaded by supplying the `--image` option:

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

```sh
rp2040py micropython --image my_image.uf2
```

A GDB server on port 3333 can be enabled by specifying the `--gdb` flag:

```sh
rp2040py micropython --gdb
```

For using the MicroPython demo code in tests, `--expect-text` can come in handy: it will look for the given text in the serial output and exit with code 0 if found, or 1 if not found. You can find an example in [the MicroPython CI test](./.github/workflows/ci-micropython.yml).

#### Filesystem support

With MicroPython, you can use the filesystem on the Pico. This becomes useful as more than one script file is used in your code. Just put a [LittleFS](https://github.com/littlefs-project/littlefs) formatted filesystem image called `littlefs.img` into the rp2040py root directory, and your `main.py` will be automatically started from there.

The `mklittlefs` subcommand builds such an image (requires the optional `fs` extra: `pip install
rp2040py[fs]` / `uv sync --extra fs`). The first file becomes `main.py`; if the output image
already exists, it's opened and updated in place rather than reformatted:

```sh
rp2040py mklittlefs littlefs.img your_main.py your.py files.py here.py
```

Currently, the filesystem is not writeable, as the SSI peripheral required for flash writing is not implemented yet.

### CircuitPython code

To run the CircuitPython demo, you can follow the directions above for MicroPython, except download [adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2](https://adafruit-circuit-python.s3.amazonaws.com/bin/raspberry_pi_pico/en_US/adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2) instead of the MicroPython UF2 file. Place it in the rp2040py root directory, then run:

```sh
rp2040py micropython --circuitpython
```

and start the CircuitPython REPL! The rest of the experience is the same as the MicroPython demo (Ctrl+X to exit, using the `--image` and `--gdb` options, etc).

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
https://img.shields.io/pypi/v/rp2040py

[python versions]:
https://img.shields.io/pypi/pyversions/rp2040py

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
