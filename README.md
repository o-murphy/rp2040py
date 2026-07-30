![](../../actions/workflows/pre-commit.yml/badge.svg) ![](../../actions/workflows/ci-micropython.yml/badge.svg) ![](../../actions/workflows/ci-pico-sdk.yml/badge.svg)

# rp2040py

Raspberry Pi Pico (RP2040) Emulator in Python — a faithful port of [rp2040js](https://github.com/wokwi/rp2040js). It blinks, runs native code, and even the MicroPython REPL!

See [docs/PORTING.md](docs/PORTING.md) for the file-by-file port status against upstream rp2040js.

## Run the demo project

### Native code

You'd need to get `hello_uart.hex` by building it from the [pico-examples repo](https://github.com/raspberrypi/pico-examples/tree/master/uart/hello_uart), then copy it to the rp2040py root directory and run:

```sh
uv run python demo/emulator_run.py
```

You can also specify the path to the image on the command line and/or load a UF2 image:

```sh
uv run python demo/emulator_run.py --image ./my-pico-project.uf2
```

A GDB server will be available on port 3333, and the data written to UART0 will be printed
to the console.

### MicroPython code

To run the MicroPython demo, first download [RPI_PICO-20230426-v1.20.0.uf2](https://micropython.org/resources/firmware/RPI_PICO-20230426-v1.20.0.uf2), place it in the rp2040py root directory, then run:

```sh
uv run python demo/micropython_run.py
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
> | CPython 3.10 | 326.98s |
> | CPython 3.14 + `PYTHON_JIT=1` | 195.26s (~1.7x) |
> | PyPy 3.10 | 15.90s (~21x) |
>
> For CPU-bound runs, PyPy is the clear winner: `uv run --python pypy3.10 --no-dev -- python
> demo/micropython_run.py ...`. See
> [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for the full breakdown
> (including a synthetic instructions/sec benchmark) and CI's `python_runtime` matrix, which tests
> all three.

```sh
uv run python demo/micropython_run.py --image my_image.uf2
```

A GDB server on port 3333 can be enabled by specifying the `--gdb` flag:

```sh
uv run python demo/micropython_run.py --gdb
```

For using the MicroPython demo code in tests, `--expect-text` can come in handy: it will look for the given text in the serial output and exit with code 0 if found, or 1 if not found. You can find an example in [the MicroPython CI test](./.github/workflows/ci-micropython.yml).

#### Filesystem support

With MicroPython, you can use the filesystem on the Pico. This becomes useful as more than one script file is used in your code. Just put a [LittleFS](https://github.com/littlefs-project/littlefs) formatted filesystem image called `littlefs.img` into the rp2040py root directory, and your `main.py` will be automatically started from there.

Using [littlefs-python](https://pypi.org/project/littlefs-python/), you can create such an image like this:

```python
from littlefs import LittleFS

files = ["your.py", "files.py", "here.py", "main.py"]
output_image = "output/littlefs.img"  # symlinked/copied to rp2040py root directory
lfs = LittleFS(block_size=4096, block_count=352, prog_size=256)
for filename in files:
    with open(filename, "rb") as src_file, lfs.open(filename, "w") as lfs_file:
        lfs_file.write(src_file.read())
with open(output_image, "wb") as fh:
    fh.write(lfs.context.buffer)
```

See [tests/mklittlefs.py](tests/mklittlefs.py) for a minimal working example used by this project's own CI.

Currently, the filesystem is not writeable, as the SSI peripheral required for flash writing is not implemented yet.

### CircuitPython code

To run the CircuitPython demo, you can follow the directions above for MicroPython, except download [adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2](https://adafruit-circuit-python.s3.amazonaws.com/bin/raspberry_pi_pico/en_US/adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2) instead of the MicroPython UF2 file. Place it in the rp2040py root directory, then run:

```sh
uv run python demo/micropython_run.py --circuitpython
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
