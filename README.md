# rp2040py

[![license]][license-url]
[![pypi version]][PyPiUrl]
[![python versions]][PyPiUrl]
[![Pre-commit]][pre-commit-workflow]
[![Test MicroPython Releases]][micropython-workflow]
[![Test Pi Pico SDK]][pico-sdk-workflow]
[![coverage]][CodecovUrl]

Raspberry Pi Pico (RP2040) Emulator in Python — started as a port of [rp2040js](https://github.com/wokwi/rp2040js), now grown into its own CLI/SDK toolkit around it (see [Differences from upstream rp2040js](#differences-from-upstream-rp2040js) below). It blinks, runs native code, and even the MicroPython REPL!

See [docs/PORTING.md](docs/PORTING.md) for the file-by-file port status against upstream rp2040js.

## Table of Contents

- [rp2040py](#rp2040py)
  - [Table of Contents](#table-of-contents)
  - [Installation](#installation)
    - [Environments without compiled-extension support (iOS/Android)](#environments-without-compiled-extension-support-iosandroid)
  - [Run the demo project](#run-the-demo-project)
    - [Native code](#native-code)
    - [MicroPython code](#micropython-code)
      - [mpremote](#mpremote)
      - [Filesystem support](#filesystem-support)
    - [CircuitPython code](#circuitpython-code)
      - [Filesystem support](#filesystem-support-1)
    - [Kaluma (other USB-CDC firmware, not MicroPython/CircuitPython)](#kaluma-other-usb-cdc-firmware-not-micropythoncircuitpython)
    - [Bootrom revisions](#bootrom-revisions)
    - [Library API](#library-api)
  - [Performance](#performance)
  - [Differences from upstream rp2040js](#differences-from-upstream-rp2040js)
  - [Used by](#used-by)
  - [Learn more](#learn-more)
  - [License](#license)

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

### Environments without compiled-extension support (iOS/Android)

`rp2040py` ships an optional Cython-accelerated backend as a compiled extension (see
[Performance](#performance)) alongside a pure-Python fallback with identical behavior - but a
handful of environments, notably iOS apps like [Pythonista](http://omz-software.com/pythonista/)
and some Android Python apps, sandboxed by the OS, can't load compiled `.so` extensions or import
native code dynamically at all. A plain `pip install rp2040py` there resolves to a
platform-specific wheel that simply won't load. Force the pure-Python universal wheel instead:

```sh
pip download rp2040py --only-binary=:all: --platform any --abi none
pip install rp2040py-*.whl --upgrade
```

This is the exact same artifact `rp2040py`'s own release pipeline builds and publishes for every
release (`RP2040PY_SKIP_NATIVE_BUILD=1`, see `.github/workflows/publish.yml`'s `build-pure` job) -
not a degraded or unsupported build, just the emulator without the compiled speedup.

To confirm you actually got it:
- **Before installing**: the downloaded file's name - a genuine pure-Python wheel is
  `rp2040py-<version>-py3-none-any.whl`, with no platform/ABI tag (e.g. no `cp310-abi3-manylinux...`)
  anywhere in the filename.
- **At runtime**: `rp2040py.rp2040.RP2040.__module__` is `"rp2040py._rp2040"` (pure Python) rather
  than `"rp2040py.native._rp2040"` (compiled) - equivalently, watch for the `UserWarning` ("Native
  extensions are not available...") `rp2040py.native` raises on import once the compiled backend
  isn't present, which is the expected, harmless case here rather than an error.

**Tested:**
- iOS
  - [Pythonista](http://omz-software.com/pythonista/) - full support (no `[fs]` optional
    dependency, no `mpremote` support for now)
  - [PythonIDE](https://apps.apple.com/ua/app/pythonide/id6753987304) - full support (no `[fs]`
    optional dependency, no `mpremote` support for now)
- Android
  - [Termux](https://github.com/termux) - full support (`mpremote` untested, work in progress)
  - [Python 3 IDE (Pydroid 3)](https://play.google.com/store/apps/details?id=ru.iiec.pydroid3) -
    not supported for now (testing in progress)

## Run the demo project

The commands below assume `rp2040py` is installed (`pip install rp2040py` / `uv add rp2040py` /
`uv tool install rp2040py`, or run ad hoc with `uvx rp2040py ...`). From a checkout of this repo
instead, each maps 1:1 onto `uv run python demo/*.py` (`demo/*.py` are thin wrappers around the
same [src/rp2040py/cli](src/rp2040py/cli) code):

| `rp2040py` subcommand      | Checkout equivalent                         |
| -------------------------- | ------------------------------------------- |
| `rp2040py run ...`         | `uv run python demo/emulator_run.py ...`    |
| `rp2040py micropython ...` | `uv run python demo/micropython_run.py ...` |
| `rp2040py kaluma ...`      | `uv run python demo/kaluma_run.py ...`      |
| `rp2040py bench ...`       | `uv run python demo/benchmark.py ...`       |

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
into `~/.cache/rp2040py` and reuses that cached file afterwards (falls back to the current
directory if the cache directory isn't writable). 1.21 is recommended: it does far
less work before dropping to the REPL prompt than newer releases, so it boots dramatically faster in
the emulator (see the benchmark below). Newer releases work too, just slower to reach the REPL -
e.g. 1.28.0.

A different version, a local UF2 file, or a CircuitPython version (`--circuitpython`, see below) can
be loaded by supplying the `--image` option - a known version tag (`1.28.0`), or a path to a UF2
file already on disk:

> [!TIP]
> Booting real firmware means executing millions of Thumb instructions through a pure-Python
> interpreter, which is dramatically slower than V8 JIT-compiling the equivalent JS in rp2040js.
> Measured with [demo/benchmark.py](demo/benchmark.py) booting MicroPython 1.28 + littlefs, then
> running a typical resident script (`while True: print(...); time.sleep(1)`, same as
> [ci-micropython.yml](.github/workflows/ci-micropython.yml)'s fixture) to its first output:
>
> | Interpreter | Time |
> |---|---|
> | CPython 3.10 | 188.98s |
> | CPython 3.10 + `rp2040py.native` (Cython, on by default) | 25.83s (~7.3x) |
> | CPython 3.14 + `PYTHON_JIT=1` | 113.77s (~1.7x) |
> | PyPy 3.10 | 8.75s (~22x) |
>
> The `rp2040py.native` figure improved from an earlier 46.65s (~4.1x) after fixing two Cython
> compilation gotchas in the bus/interpreter hot path (untyped `address`/`value` parameters forcing
> a `PyLong` box on every memory access, and bare `0x80000000`+ hex literals silently compiling as
> Python-object constants instead of C literals) - see
> [docs/BACKLOG.md](docs/BACKLOG.md#follow-up-two-more-boxing-sources-found-by-reading-the-generated-c-not-by-guessing)
> for the full writeup, including why PyPy's gap didn't close by the same amount (a real boot
> spends a large, unchanged share of its time in still-Python peripheral emulation that these fixes
> don't touch). **This row is CPython 3.10 specifically** (this project's default target, and below
> the abi3 floor - see [Performance](#performance) below): CPython 3.11+ actually measures slower
> in absolute terms (33.90s, ~5.6x) on the *same* fixed source, purely from the stable-ABI
> (`Py_LIMITED_API`) build every 3.11+ wheel uses - see the same BACKLOG.md section for that gap
> too, found (and initially mismeasured!) while producing these very numbers.
>
> The `rp2040py.native` row is what most installs actually get with no extra effort - see
> [Performance](#performance) below. It doesn't help PyPy (compilation is deliberately skipped
> there - PyPy's own JIT already does better on its own than routing through `rp2040py.native`'s
> CPython-C-API-based extension would), so for CPU-bound runs PyPy is still the clear winner:
> `uv run --python pypy3.10 --no-dev -- rp2040py micropython ...` (or `... -- python
> demo/micropython_run.py ...` from a checkout). See
> [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for the full breakdown
> (including a synthetic instructions/sec benchmark) and CI's `python_runtime` matrix, which tests
> all three.
>
> This is also why **1.21 is the recommended version**: reaching the bare REPL prompt is fast on
> *both* 1.21 and 1.28 (well under a second, whether or not a littlefs `main.py` auto-runs first) -
> the gap above is specifically about *running* a script shaped like the one above afterward. On
> the same machine and CPython 3.10, that same script reaches its first `print()` in 3.72s
> (1,418,835 steps) under 1.21 versus 188.98s (64,679,599 steps) under 1.28 - identical instruction
> counts run-to-run (this is deterministic, not host-speed noise), so the ~45x gap is a real
> difference in how much work 1.28 does per loop iteration, not an emulator bug: profiling shows
> the core essentially never reaches `WFI`/idle during that time, so it's real Thumb instructions
> being interpreted, not something hanging. 1.28 still boots and mounts a `mklittlefs`-built
> littlefs image correctly (that's exactly the version pinned `disk_version` fixed compatibility
> for, see below); it's simply much more expensive to actually *run* typical resident scripts on.
>
> Core-level per-instruction throughput work continues independently of this version gap (most
> recently: `RP2040.write_uint32()` was checking a peripheral dict lookup before cheap RAM/flash
> range comparisons - see [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for
> the running log). These are general wins, not something that closes the 1.21-vs-1.28 gap itself -
> that gap is real work MicroPython 1.28's own compiled firmware does per loop iteration, not
> something this project's emulator code controls.

```sh
rp2040py micropython --image 1.28.0
rp2040py micropython --image my_image.uf2
```

A GDB server on port 3333 can be enabled by specifying the `--gdb` flag:

```sh
rp2040py micropython --gdb
```

For using the MicroPython demo code in tests, `--expect-text` can come in handy: it will look for the given text in the serial output and exit with code 0 if found, or 1 if not found. It's repeatable (`--expect-text foo --expect-text bar` stops once *both* have appeared, on any line, not necessarily the same one or in that order) and, with `--expect-regex`, each `--expect-text` value is matched as a Python `re` pattern (via `re.search`) instead of a plain substring. You can find an example in [the MicroPython CI test](./.github/workflows/ci-micropython.yml).

For one-shot, non-interactive runs (like `micropython`'s own CLI), pass one of `-c <command>`, `-m <module>`, or a script `<filename>` - mutually exclusive, matching `[-c <command> | -m <module> | <filename>]`. Instead of dropping into the REPL, rp2040py boots the device, runs it via the raw-REPL protocol, prints its stdout/stderr, and exits with the device's exit status (0 on success, 1 if it raised):

```sh
rp2040py micropython -c "print(1 + 1)"
rp2040py micropython -m sys
rp2040py micropython path/to/script.py
```

#### mpremote

`--tcp-port <port>` serves the console over a plain TCP socket instead of this process's own
stdio - for tools that expect a serial port but can't open one, notably
[`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) in a sandboxed
environment with no serial support at all (e.g.
[Pythonista](#environments-without-compiled-extension-support-iosandroid), see above). No
client-side patching needed - `mpremote connect socket://host:port` just talks directly
to rp2040py, via pySerial's own built-in `socket://` URL support:

```sh
rp2040py micropython --tcp-port 4321
# in another terminal:
mpremote connect socket://127.0.0.1:4321 exec "print(1 + 1)"
mpremote connect socket://127.0.0.1:4321 fs cp your_script.py :main.py
```

`--pty` (POSIX only) is the alternative: a real pseudo-terminal instead of a TCP socket, whose
slave side (e.g. `/dev/pts/3`) is a genuine POSIX serial device path - everything `--tcp-port`
supports also works here, *plus* `mpremote`'s own bare interactive REPL, which does not work over
`--tcp-port`'s `socket://` transport through the real `mpremote` binary (see below) - though
`rp2040py mpremote` (a thin proxy subcommand, same arguments as `mpremote` itself) patches around
that specific crash, so `rp2040py mpremote connect socket://host:port repl` works too, no `--pty`
needed.

See **[docs/mpremote.md](docs/mpremote.md)** for the full picture: connection details for both
flags, the `rp2040py mpremote` proxy and the upstream bug it patches around
([micropython#18660](https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170)),
how to quit the emulator when `mpremote` owns the console, and an explicit list of which `mpremote`
commands are verified working against each transport (`exec`, `fs`, `mount`, `run`,
`reset`/`bootloader`, the interactive `repl` over `--pty` or `rp2040py mpremote`, ...) versus the
remaining documented limitations (the real `mpremote` binary's own bare interactive REPL over
`--tcp-port`, `--pty` on Windows, and `df` on MicroPython ≤1.21).

#### Filesystem support

With MicroPython, you can use the filesystem on the Pico. This becomes useful as more than one script file is used in your code. Build a [LittleFS](https://github.com/littlefs-project/littlefs) formatted filesystem image (see `mklittlefs` below) and pass it with `--littlefs path/to/littlefs.img`, and your `main.py` will be automatically started from there (it's silently skipped, not an error, if the file doesn't exist - but it's never loaded unless `--littlefs` is given explicitly, even if a `littlefs.img` happens to sit in the current directory).

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

`--target {micropython,circuitpython,kaluma}` presets `--block-size`/`--block-count` to a known
firmware's own filesystem layout instead of spelling them out by hand (mutually exclusive with
passing them explicitly) - see the Kaluma section below for why its layout differs from
MicroPython/CircuitPython's.

The filesystem is writeable - MicroPython's `os`/`rp2.Flash` calls go through a real JEDEC
SPI-NOR flash command emulation in the SSI peripheral (`RPSSI`), the same peripheral real
hardware uses to erase/program flash.

`--dump-fs <path>` dumps the device's filesystem flash region back out to a local file when the
`micropython`/`kaluma` subcommand exits (Ctrl+X, `--expect-text`, or the end of a `-c`/`-m`/script
run) - the same layout `--littlefs path/to/littlefs.img` reads back in, so a dump can be fed
straight back with `--littlefs`/`--dump-fs` pointing at the same path for persistence across runs:

```sh
rp2040py micropython --littlefs littlefs.img --dump-fs littlefs.img -c "open('log.txt', 'a').write('run\n')"
```

> [!TIP]
> This makes `--dump-fs` a `littlefs-python`-free alternative to `mklittlefs`: instead of building
> the image on the host with `littlefs-python`, boot the real emulated MicroPython firmware
> against blank flash, write files to it the normal way (`open(path, "wb").write(data)`, exactly
> as code running on a real Pico would), and dump the resulting filesystem - built by MicroPython's
> own bundled littlefs, not a separately-installed library. [demo/mklittlefs_dump.py](demo/mklittlefs_dump.py)
> generates such a script from a list of local files (mirroring `mklittlefs`'s own `--main`
> semantics) for use as the positional `<filename>` argument:
>
> ```sh
> python demo/mklittlefs_dump.py your_main.py your.py files.py here.py --main your_main.py \
>     --output flash_script.py
> rp2040py micropython --dump-fs littlefs.img flash_script.py
> ```
>
> Useful when the `fs` extra isn't installed, or to build against exactly the same littlefs
> version/behavior a given firmware boots with instead of whatever `littlefs-python` happens to
> bundle.

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

Then pass it explicitly with `--fat12` (no default - `fat12.img` sitting in the current directory
is never picked up implicitly):

```sh
rp2040py micropython --circuitpython --fat12 fat12.img
```

CircuitPython doesn't typically write to its own filesystem at runtime the way MicroPython's
`os`/`rp2.Flash` does, so this hasn't been separately exercised - but the underlying flash-write
path (see the MicroPython filesystem support section) is the same SSI peripheral either way.

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

Give it a few real seconds after connecting before expecting output - like MicroPython, booting
real firmware through an interpreted emulator takes actual wall-clock time (JerryScript engine
init, then running your script), not something `--expect-text` needs to work around, just something
to expect if driving this non-interactively.

Separately, Kaluma has its own pluggable littlefs-backed filesystem (see
[its docs](https://kalumajs.org/docs/api/file-system)), mounted from a 512K region of flash with
4096-byte blocks - a *different* flash region than the user-program one above, with no auto-run
semantics of its own (plain storage, accessible from JS via `require('fs')`). Build a compatible
image with `mklittlefs` and pass it via `--littlefs` explicitly (no default - unlike MicroPython's
`--littlefs`, it's never picked up implicitly, even from a `kaluma_littlefs.img` sitting in the
current directory):

```sh
rp2040py mklittlefs -o kaluma_littlefs.img --target kaluma your_script.js
rp2040py kaluma --littlefs kaluma_littlefs.img
```

`--target {micropython,circuitpython,kaluma}` presets `--block-size`/`--block-count` for a known
firmware's filesystem layout (mutually exclusive with passing them explicitly) - omit both for
MicroPython's own defaults.

`--dump-fs <path>` works here too, dumping the same littlefs-formatted region back out on exit -
but unlike `micropython`, `kaluma` has no non-interactive exec mode to script filesystem writes
through (no `-c`/`<filename>` raw-REPL equivalent, see above), so the `mklittlefs`-without-
`littlefs-python` trick above doesn't apply the same way; use it interactively via `require('fs')`
at the REPL instead, or stick with `mklittlefs`.

`--tcp-port <port>`/`--pty` also work here, same as `micropython` - see [mpremote](#mpremote) above
(that section is `mpremote`-specific, but the underlying mechanism, a plain socket/pty serving the
console instead of this process's own stdio, is not).

> [!NOTE]
> Without a valid `--littlefs` image, `board.js`'s unconditional mount-at-startup logs `Bad block
> at 0x0`/`Superblock 0x0 has become unwritable`/`Error: No space left on device` against the
> unformatted flash region - purely cosmetic, Kaluma catches and prints the error without aborting,
> so boot and `<script.js>` auto-run both continue normally past it. This used to reproduce even
> against a validly-built `mklittlefs` image, not just blank flash - no longer reproduced after the
> SSI flash-read/write fixes in `docs/BACKLOG.md` (a real `--target kaluma` image now mounts and
> reads/writes cleanly, verified via `tests/kaluma/index-flash-rw.js`), though that wasn't a
> deliberate target of those fixes and hasn't been separately root-caused - flag it if it resurfaces.

Kaluma prints its "Welcome to Kaluma" banner exactly once, right at boot - but that's before the
emulated USB-CDC connection to the host is actually up, so (same as real hardware racing a host
terminal that isn't already attached - Kaluma's own docs: "if you cannot see the prompt, press
Enter several times") those bytes are typically gone by the time anything's listening. `kaluma`
doesn't send anything to work around this - type `.hi` yourself at the prompt to reprint the same
banner on demand if you need to see it; if you're scripting against a device's output instead of
typing at it interactively, stage a `<script.js>` and match against *its* output, which isn't racy
(see [the Kaluma CI test](./.github/workflows/ci-kaluma.yml), which does exactly that).

### Bootrom revisions

`run`, `micropython`, `kaluma`, and `bench` all boot a fixed bootrom (`B1`, bundled - no download
needed) by default. `--bootrom` picks a different one: a `b0`/`b1`/`b2` version tag (downloaded
automatically from [Raspberry Pi's `pico-bootrom-rp2040`
releases](https://github.com/raspberrypi/pico-bootrom-rp2040/releases) and cached locally, same as
`--image`), or a local `.elf`/`.bin` path:

```sh
rp2040py micropython --bootrom b2
rp2040py micropython --bootrom path/to/custom.elf
```

Raspberry Pi only publishes `.elf` for each revision - `pyelftools` (a normal dependency, not an
extra: it's a pure-Python wheel with no platform-specific build to justify gating it) parses out
the ROM image on the fly, no separate conversion step needed. A local `.bin` (e.g. produced with
`objcopy -O binary`) is loaded directly with no parsing at all.

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

## Performance

The interpreter core (`CortexM0Core`) and the memory bus's hot read/write paths are also available
as a compiled Cython extension (`rp2040py.native`), giving roughly **7x** the instruction
throughput of the pure-Python implementation on both a synthetic benchmark and a real MicroPython
boot (see [docs/BACKLOG.md](docs/BACKLOG.md#cython-port-of-the-interpreter-core--implemented-on-by-default-real-world-win-confirmed-4x)
for the full measured breakdown).

This is on by default and needs nothing from you: `pip install rp2040py` builds it automatically
when a C compiler is available (prebuilt wheels are published for common platforms, so most
installs don't even need one) and falls back to the identical pure-Python implementation otherwise
- correctness is the same either way, just the speed differs. A couple of environment variables
exist for cases where you want to control this explicitly:

- `RP2040PY_SKIP_CYTHON=1` - force the pure-Python implementation at runtime, even if the compiled
  extension is installed (e.g. to rule out a native-specific issue).
- `RP2040PY_SKIP_NATIVE_BUILD=1` - skip compiling the extension at *build* time, for a
  deliberately pure-Python install/wheel.

## Differences from upstream rp2040js

rp2040py started as a straight port of [rp2040js](https://github.com/wokwi/rp2040js) - the core
CPU/peripheral emulation still tracks it closely, and [docs/PORTING.md](docs/PORTING.md) keeps a
file-by-file checklist of that. But it's grown well past a 1:1 translation into its own toolkit
with no rp2040js equivalent, built around actually running real firmware from a shell rather than
embedding the emulator as a library (rp2040js's own primary use case, e.g. inside Wokwi):

- **A real packaged CLI** - `rp2040py`/`python -m rp2040py`, installable via `pip`/`uv`, not just a
  checkout-only `demo/*.ts` script. Firmware (MicroPython/CircuitPython/Kaluma) is auto-downloaded
  and cached by version tag instead of needing to be fetched and placed by hand.
- **A real, writeable filesystem**: `RPSSI` (the SSI peripheral MicroPython/CircuitPython's
  `os`/`rp2.Flash` calls go through to erase/program flash) implements the actual JEDEC SPI-NOR
  command set (`WREN`/`WRDI`, status/JEDEC-ID reads, page program, sector/block erase) - the same
  commands real flash hardware understands - not just a register stub. rp2040js has the same gap
  MicroPython/CircuitPython on rp2040py *used* to have (see `docs/BACKLOG.md`'s "SSI flash-write
  support"): on-device `open(path, "w")`/`os.remove()`/... genuinely persist to the emulated flash
  now, instead of raising/no-opping against an unimplemented peripheral.
- **A filesystem toolkit**: `mklittlefs` builds a littlefs image on the host (needs
  `littlefs-python`, the optional `fs` extra); `--dump-fs` builds one *without* that dependency
  instead, by writing files to a booted device's real filesystem the normal way and reading the
  resulting flash region back out - see [mpremote](#mpremote) and
  [Filesystem support](#filesystem-support) above.
- **A programmatic device API** (`rp2040py.device.MicroPythonDevice`/`KalumaDevice`) for driving a
  booted device from another Python program over the raw-REPL protocol
  (`device.exec("print(1+1)")`) - the same API `micropython -c/-m/<filename>` and `--tcp-port`
  themselves are built on, not a separate implementation. `--tcp-port`/`--pty` in particular let
  any serial-oriented external tool - `mpremote` chief among them, including its own bare
  interactive REPL via `rp2040py mpremote` (see [mpremote](#mpremote)) - drive the emulator over a
  real socket or pty, something rp2040js has no analogue for at all (no pty/socket-backed USB-CDC
  passthrough anywhere in its source, only stdio-driven demo scripts).
- **Broader firmware coverage**: MicroPython, CircuitPython, and [Kaluma](https://kaluma.io/) (a
  second, independent USB-CDC-console JS runtime for RP2040 - unrelated to rp2040js despite both
  being JS) all boot and run against this emulator; a built-in GDB server (`--gdb`) works against
  any of them.
- **`machine.reset()`/`machine.bootloader()` actually reset the device**: rp2040js's own
  `RPWatchdog.onWatchdogTrigger` (`src/peripherals/watchdog.ts`) defaults to logging "Watchdog
  triggered, but no reset handler provided" and does nothing else - the emulated CPU spins forever
  waiting for a reset that never happens. rp2040py's `RPWatchdog.on_watchdog_trigger` performs a
  real in-place reset (CPU core, PWM/DMA/USB-CDC peripheral state) and jumps back to flash's entry
  point, preserving flash/filesystem content and every externally-referenced peripheral object's
  identity - `mpremote reset`/`mpremote bootloader` (the latter performs the same reset rather than
  entering actual BOOTSEL mode, which isn't implemented) both return promptly instead of hanging.
- **Configurable bootrom revision** (`--bootrom b0`/`b1`/`b2`, or a local `.elf`/`.bin`) - see
  [Bootrom revisions](#bootrom-revisions) below - auto-downloaded and cached the same way firmware
  images are. rp2040js ships exactly one hardcoded bootrom build (`demo/bootrom.ts`, revision B1),
  with no way to select a different revision at all.
- **An optional native-compiled backend** (`rp2040py.native`, Cython) for when pure-Python
  instruction dispatch is the bottleneck - see [Performance](#performance) above - alongside a
  pure-Python universal wheel for environments that can't load compiled extensions at all (e.g.
  [Pythonista](#environments-without-compiled-extension-support-iosandroid)).

See [docs/PORTING.md#known-differences-from-rp2040js](docs/PORTING.md#known-differences-from-rp2040js)
for the exhaustive, file-level breakdown (including behavioral divergences found while porting,
not just added features).

## Used by

- [ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc) — tests
  its RP2040 `usermod`/`natmod` builds in CI by actually booting real firmware through this
  emulator (`o-murphy/rp2040py/.github/actions/setup-rp2040py`), not just compiling it.

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
