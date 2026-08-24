# rp2040py

[![license]][license-url]
[![pypi version]][PyPiUrl]
[![python versions]][PyPiUrl]
[![Pre-commit]][pre-commit-workflow]
[![Test MicroPython Releases]][micropython-workflow]
[![Test Pi Pico SDK]][pico-sdk-workflow]
[![coverage]][CodecovUrl]

Raspberry Pi Pico (RP2040) Emulator in Python — started as a port of [rp2040js](https://github.com/wokwi/rp2040js), now grown into its own CLI/SDK toolkit around it (see [Differences from upstream rp2040js](#differences-from-upstream-rp2040js) below). It blinks, runs native code, and even the MicroPython REPL!

See [docs/reference/porting-checklist.md](docs/reference/porting-checklist.md) for the file-by-file port status against upstream rp2040js.

> [!IMPORTANT]
> **Single-core only.** rp2040py emulates `core0`; there is no `core1`, and SIO's inter-core FIFO
> registers (`FIFO_ST`/`FIFO_WR`/`FIFO_RD`) are *recognised but non-functional*. The three
> addresses are named rather than falling through as unknown, so a read logs `"Inter-core FIFO
> (0x50-0x58) is not implemented. core1/_thread is unsupported"` and returns all-ones; a write is
> still on the generic path, logging `"Write to invalid SIO address"` before the value is
> discarded. `CPUID` always reads `0`. Firmware that starts the second core
> (`multicore_launch_core1()`, MicroPython's `_thread`) will not work. This is deliberate rather
> than pending: adding the FIFO registers *without* a second core to answer them would turn today's
> loud failure into a silent infinite hang inside `multicore_fifo_pop_blocking()`, which is strictly
> worse — see [docs/records/0053](docs/records/0053-core1-and-inter-core-fifo.md) for what building
> it properly involves. Single-core firmware — the default for MicroPython, CircuitPython, Kaluma
> and the pico-examples — is unaffected.

## Quick start

```sh
pip install rp2040py
rp2040py micropython          # boots real MicroPython firmware, drops you into its REPL (Ctrl+X to quit)
rp2040py micropython -c "print(1 + 1)"   # or run one command non-interactively and exit
```

That's it — no manual firmware download, no board wiring. Everything below is depth: more firmware
families (CircuitPython, Kaluma), WiFi, filesystems, a programmatic API, and how to add your own
boards/devices. See [Run the demo project](#run-the-demo-project) for the full CLI, or jump straight
to [External devices & custom boards](#external-devices--custom-boards) if you're here to extend it.

## Table of Contents

- [Quick start](#quick-start)
- [Installation](#installation)
  - [Shell completions](#shell-completions)
- [Run the demo project](#run-the-demo-project)
  - [Native code](#native-code)
  - [MicroPython code](#micropython-code)
    - [mpremote](#mpremote)
  - [CircuitPython code](#circuitpython-code)
  - [Kaluma](#kaluma)
  - [Filesystem support](#filesystem-support)
  - [WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439)
  - [Bootrom revisions](#bootrom-revisions)
- [Library API](#library-api)
  - [External devices & custom boards](#external-devices--custom-boards)
    - [Adding your own](#adding-your-own)
    - [Ready-made example boards](#ready-made-example-boards)
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

> [!NOTE]
> A handful of fully-sandboxed environments (iOS app runtimes - Pythonista, PythonIDE) can't load
> `rp2040py`'s compiled extension; plain `pip install rp2040py` resolves to a wheel that won't
> load there. Force the pure-Python one instead: `pip download rp2040py --only-binary=:all:
> --platform any --abi none && pip install rp2040py-*.whl --upgrade` (the same artifact the
> release pipeline itself publishes, not a degraded build). Full platform × feature matrix -
> including Android, which works fine with the compiled extension - in
> [docs/reference/os-compatibility.md](docs/reference/os-compatibility.md).

### Shell completions

`rp2040py install-completion` sets up tab completion for every subcommand and flag (`--board`,
`--log-level`, `--littlefs`, ...) in Bash or Zsh, via [`argcomplete`](https://github.com/kislyuk/argcomplete):

```sh
rp2040py install-completion
# then open a new shell, or:
source ~/.bashrc   # or ~/.zshrc
```

This appends the shell's `register-python-argcomplete` hook to `~/.bashrc`/`~/.zshrc` (detected
from `$SHELL`) - a one-time setup step, not something run on every invocation.

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

Two demos have no CLI equivalent because they emulate hardware rather than run firmware: the
Waveshare 2.9″ e-Paper panel (`demo/eink_run.py`) and the RP2040-LCD-0.96's onboard ST7735S
(`demo/lcd_run.py`). [demo/README.md](demo/README.md) shows what both actually draw.

`--board {pico,pico_w}` (default `pico`) is available on all four and picks which board's fixed
extras get attached alongside the RP2040 itself - the onboard LED and the BOOTSEL button on both,
plus an emulated CYW43439 WiFi/Bluetooth chip on `pico_w`; see
[WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439) below.

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
> interpreter - dramatically slower than V8 JIT-compiling the equivalent JS in rp2040js, though the
> compiled `rp2040py.native` backend (on by default, see [Performance](#performance) below) closes
> most of that gap:
>
> | Interpreter | Time to a resident script's first output (MicroPython 1.28 boot) |
> |---|---|
> | CPython 3.10 | 133.3s |
> | CPython 3.10 + `rp2040py.native` (on by default) | 11.3s (~11.8x) |
> | PyPy 3.10 | 8.9s (~15x) |
>
> This is also why **1.21 is the recommended default version**: both 1.21 and 1.28 reach the bare
> REPL prompt in well under a second, but *running* a typical resident script afterward is ~45x
> more expensive under 1.28 than 1.21 - real work MicroPython 1.28's own firmware does per loop
> iteration, not an emulator bug. See
> [docs/records/0013-cython-core.md](docs/records/0013-cython-core.md) for the full measured
> breakdown (methodology, PyPy/CPython-JIT comparisons, the 1.21-vs-1.28 instruction-count numbers)
> and [docs/reference/porting-checklist.md](docs/reference/porting-checklist.md#known-differences-from-rp2040js)
> for a synthetic instructions/sec benchmark across all three runtimes.

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
stdio, so [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) can connect
directly via pySerial's built-in `socket://` support - no client-side patching needed:

```sh
rp2040py micropython --tcp-port 4321
# in another terminal:
mpremote connect socket://127.0.0.1:4321 exec "print(1 + 1)"
mpremote connect socket://127.0.0.1:4321 fs cp your_script.py :main.py
```

`--pty` (POSIX only) is the alternative - a real pseudo-terminal, which additionally supports
`mpremote`'s own bare interactive REPL (`rp2040py mpremote`, a thin proxy subcommand, gets that
working over `--tcp-port` too, patching around an upstream `mpremote` bug).

See **[docs/reference/mpremote.md](docs/reference/mpremote.md)** for the full picture: connection
details for both flags, the proxy and the bug it patches around, how to quit the emulator when
`mpremote` owns the console, and exactly which `mpremote` commands are verified working where.

Filesystem support and WiFi both work here too, and cover MicroPython/CircuitPython/Kaluma in one
place below - see [Filesystem support](#filesystem-support) and
[WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439).

### CircuitPython code

To run the CircuitPython demo, follow the directions above for MicroPython but add `--circuitpython`:

```sh
rp2040py micropython --circuitpython
```

and start the CircuitPython REPL! As with MicroPython, the firmware (**10.2.1** by default) is
downloaded automatically on first use; a different version or a local file can be given via
`--image` (e.g. `--image 8.0.2` or a path to an already-downloaded UF2). The rest of the experience
is the same as the MicroPython demo (Ctrl+X to exit, the `--gdb` option, etc). Filesystem support
(a FAT12 image, not littlefs) and WiFi both work here too - see
[Filesystem support](#filesystem-support) and [WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439)
below, which cover all three firmware families in one place.

### Kaluma

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

`--board pico_w` works here too - Kaluma's own `require('wifi')` scans, joins, gets a DHCP lease,
and opens real `net.Socket` connections to the internet through the same bridge described under
[WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439) below (`tests/kaluma/main-cyw43.js` is a
runnable example), and filesystem support is covered under
[Filesystem support](#filesystem-support) below too, alongside MicroPython/CircuitPython's.

Give it a few real seconds after connecting before expecting output - like MicroPython, booting
real firmware through an interpreted emulator takes actual wall-clock time (JerryScript engine
init, then running your script), not something `--expect-text` needs to work around, just something
to expect if driving this non-interactively.

`--tcp-port <port>`/`--pty` also work here, same as `micropython` - see [mpremote](#mpremote) above
(that section is `mpremote`-specific, but the underlying mechanism, a plain socket/pty serving the
console instead of this process's own stdio, is not).

Kaluma prints its "Welcome to Kaluma" banner exactly once, right at boot - but that's before the
emulated USB-CDC connection to the host is actually up, so (same as real hardware racing a host
terminal that isn't already attached - Kaluma's own docs: "if you cannot see the prompt, press
Enter several times") those bytes are typically gone by the time anything's listening. `kaluma`
doesn't send anything to work around this - type `.hi` yourself at the prompt to reprint the same
banner on demand if you need to see it; if you're scripting against a device's output instead of
typing at it interactively, stage a `<script.js>` and match against *its* output, which isn't racy
(see [the Kaluma CI test](./.github/workflows/ci-kaluma.yml), which does exactly that).

### Filesystem support

`mklittlefs` builds a writeable [LittleFS](https://github.com/littlefs-project/littlefs)-formatted
image on the host (needs the optional `fs` extra: `pip install rp2040py[fs]` / `uv sync --extra
fs`) - shared by MicroPython and Kaluma, which both boot from real littlefs flash:

```sh
rp2040py mklittlefs -o littlefs.img your_main.py your.py files.py here.py --main your_main.py
```

Every file keeps its own basename; `--main` marks one as auto-run on boot (omit it for a
filesystem with no auto-run script, or omit `files` entirely for an empty formatted image).
Always builds fresh - pass `-f`/`--force` to overwrite an existing `--output`.
`--target {micropython,circuitpython,kaluma}` presets `--block-size`/`--block-count` to a known
firmware's own layout instead of spelling them out by hand (mutually exclusive with passing them
explicitly - the three differ, see the per-firmware notes below). `--disk-version {2.0,2.1}`
selects the littlefs on-disk format (defaults to `2.0`: MicroPython <=1.21's bundled littlefs can
only mount `2.0`, 1.28's reads both - see
[docs/records/0003](docs/records/0003-littlefs-image-format.md)).

- **MicroPython**: `--littlefs path/to/littlefs.img` mounts it and auto-runs `main.py` if present
  (silently skipped, not an error, if it isn't - but never loaded at all unless `--littlefs` is
  given explicitly). The filesystem is writeable at runtime - `os`/`rp2.Flash` calls go through a
  real JEDEC SPI-NOR command emulation in the SSI peripheral (`RPSSI`), the same one real flash
  hardware uses.
- **Kaluma**: its own pluggable littlefs-backed filesystem (see
  [its docs](https://kalumajs.org/docs/api/file-system)) lives in a *different*, fixed 512K flash
  region (4096-byte blocks) than the `<script.js>` user-program staging area above, with no
  auto-run semantics of its own - plain storage, accessed from JS via `require('fs')`. Pass it via
  `--littlefs` explicitly (never picked up implicitly, even from a `kaluma_littlefs.img` in the
  current directory):

  ```sh
  rp2040py mklittlefs -o kaluma_littlefs.img --target kaluma your_script.js
  rp2040py kaluma --littlefs kaluma_littlefs.img
  ```

  Without a valid image, `board.js`'s unconditional mount-at-startup logs cosmetic `Bad block`/
  `Superblock ... unwritable`/`No space left on device` errors against unformatted flash - Kaluma
  catches and prints them without aborting, so boot and `<script.js>` auto-run continue normally.
- **CircuitPython**: a FAT12 image instead of littlefs - build one with `truncate`/`mkfs.vfat`
  (not `mklittlefs`) and pass it via `--fat12` (no default, never picked up implicitly):

  ```sh
  truncate fat12.img -s 1M && mkfs.vfat -F12 -S512 fat12.img
  mkdir fat12 && sudo mount -o loop fat12.img fat12/ && sudo cp code.py fat12/ && sudo umount fat12/
  rp2040py micropython --circuitpython --fat12 fat12.img
  ```

  It can also write its own drive, which is usually the easier route: `storage.remount('/',
  readonly=False)` at the REPL, then plain `open()`/`write()`. On real hardware that raises while a
  USB host holds the mass-storage lock; this emulator claims only the CDC interface, so the lock is
  free and the firmware builds the volume itself - long names and subdirectories included. Restart
  it afterwards (Ctrl-B then Ctrl-D at the console) to make CircuitPython re-run `code.py`, and
  `--dump-fs` if you want to keep the image. `demo/lcd_run.py --code` and `demo/wifi_lcd_run.py`
  both work this way; see
  [docs/records/0087](docs/records/0087-circuitpython-writable-circuitpy-over-the-raw-repl.md).

The format is a property of the firmware family, not a choice, so the two flags are mutually
exclusive *and* family-checked: `--fat12` needs `--circuitpython`, `--littlefs` needs its absence,
and the wrong one is a startup error rather than a flag that is quietly ignored (a
`--fat12 image.img` run without `--circuitpython` used to boot with no filesystem at all and no
hint as to why). A named image that doesn't exist is still skipped silently - that is about the
file, not the flag.

`--dump-fs <path>` dumps a device's filesystem flash region back out to a local file on exit
(Ctrl+X, `--expect-text`, or the end of a run) - the same layout `--littlefs`/`--fat12` reads back
in, so it round-trips for persistence across runs. Works for all three families - littlefs for
**MicroPython** and **Kaluma**, FAT12 for **CircuitPython**; MicroPython
additionally supports scripting it non-interactively via `-c`/`-m`/`<filename>` (see
[demo/mklittlefs_dump.py](demo/mklittlefs_dump.py), which builds such a script from local files) -
Kaluma has no non-interactive exec mode, so use `require('fs')` at its REPL instead. This makes
`--dump-fs` a `littlefs-python`-free alternative to `mklittlefs` on either firmware: boot against
blank flash, write files the normal way, dump the result - built by that firmware's own bundled
littlefs, not a separately-installed library.

### WiFi (Pico W / CYW43439)

`--board pico_w` (default: `pico`, any firmware) attaches an emulated CYW43439 - the WiFi/Bluetooth
chip on a real Pico W - over the same gSPI bus real firmware drives it through. `network.WLAN`
(MicroPython), `wifi`/`socketpool` (CircuitPython), and `require('wifi')`/`net` (Kaluma) all work
against it - three independent network stacks over one bus, none of them needing anything
CYW43-specific from the emulator:

```sh
rp2040py micropython --board pico_w
```

`nic.active(True)`, `nic.scan()`, and `nic.connect(ssid, key)` all complete, answered by a fixed
fake `"RP2040PY-GUEST"` access point built into the emulation. The association is fake, but **the
network behind it is real** - a NAT bridge gives the guest a DHCP lease, answers its ARP, and
splices its TCP connections and UDP datagrams onto real sockets on your machine, so code running
on the emulated Pico W reaches the actual internet. Live-boot verified against real, unmodified
MicroPython firmware on both 1.23.0 and 1.28.0:

```python
import network, socket, mip, ntptime

nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print(nic.scan())  # [(b'RP2040PY-GUEST', ...)]
nic.connect("RP2040PY-GUEST", "key")  # any password is accepted
print(nic.isconnected(), nic.ipconfig("addr4"))  # True ('10.0.0.2', '255.255.255.0')

s = socket.socket()  # real TCP, out through your host's network
s.connect(("1.1.1.1", 80))
s.send(b"GET / HTTP/1.0\r\n\r\n")
print(s.recv(64))  # b'HTTP/1.1 301 Moved Permanently\r\n...'

mip.install("os-path")  # real DNS + a real HTTPS download
ntptime.settime()  # real NTP, sets the emulated RTC
nic.disconnect()  # link really goes down: isconnected() -> False, status() -> 0
```

TLS works through the same path (the reflector relays bytes without inspecting them), and so do
WebSockets over both `ws://` and `wss://`. CircuitPython
([tests/circuitpython/main-cyw43.py](tests/circuitpython/main-cyw43.py)) and Kaluma
(`tests/kaluma/main-cyw43.js`) have their own runnable examples; CircuitPython additionally
enforces WPA2's 8-64 character passphrase rule client-side, so the password you pass must be at
least 8 characters even though the emulated AP accepts anything.

What is **not** emulated, so you don't discover it the hard way:

- The AP is a fixture. `scan()` always returns the one fake `"RP2040PY-GUEST"` network, any
  password "succeeds," and there's no hidden-SSID or auth-failure path to test against.
- No AP mode (`network.WLAN.IF_AP`), no IPv6, and one guest only (the guest/gateway IP and MAC are
  fixed constants, with no config surface yet).
- No flow-control backpressure from the real destination onto the guest: the emulator always
  advertises a fixed TCP receive window, so a guest that outran a slow destination would grow the
  host process's socket buffer rather than being told to slow down. An emulated Cortex-M0 can't
  realistically outrun a real socket, which is why this hasn't mattered in practice.

See [docs/records/0027-cyw43-wifi.md](docs/records/0027-cyw43-wifi.md) for what's emulated at the
gSPI/SDPCM protocol level and [docs/records/0048-cyw43-nat-reflector.md](docs/records/0048-cyw43-nat-reflector.md)
for the network bridge (how the reflector works, and the full list of what's still open).

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

## Library API

Everything above is the CLI, but the emulator is also usable programmatically - e.g. to run code against a device and check its output the way [Thonny](https://thonny.org/) does over a real serial port, from a test suite or another tool. `rp2040py.device.MicroPythonDevice` boots a board and lets you run code on it via the same raw-REPL protocol `mpremote run`/`tools/pyboard.py` use, interrupting anything already running on the device first (e.g. an auto-run `main.py` from a littlefs image). `board` is keyword-only and is the *only* board-related argument - a resolved `BoardSpec` carrying its own firmware image, never a board-name string or a separate `image=` kwarg; see [docs/reference/external-devices-and-boards.md](docs/reference/external-devices-and-boards.md#using-a-boardspec) for building one of your own.

> [!NOTE]
> **Async-native only, no blocking API.** `MicroPythonDevice`/`KalumaDevice`/`BaseDevice` boot and
> run as coroutines on an `asyncio` event loop (the same "engine room" the CLI itself runs on) -
> there is no blocking `start()`/`exec()`/`exec_file()` and no synchronous `with device:` form.
> Calling a blocking wrapper's `Future.result()` from the same loop it would need to run on
> deadlocks (the loop can't process the coroutine that resolves the Future while its own thread is
> stuck waiting on it), so this project stopped offering one rather than ship that footgun - wrap
> a call in `asyncio.run(...)` yourself if you want blocking behavior from a plain script.

**asyncio**, via `astart()`/`aexec()`/`aexec_file()`, entered as an `async with` context manager:

```python
import asyncio
from rp2040py.boards import BOARDS, resolve_firmware
from rp2040py.device import MicroPythonDevice


async def main():
    # Downloads and caches the family's default firmware; pass a third argument
    # ("1.23.0", a local .uf2 path, a URL) to pin a different one.
    board = resolve_firmware(BOARDS["pico"], "micropython")
    async with MicroPythonDevice(board=board) as device:
        stdout, stderr = await device.aexec("print(1 + 1)")
        assert stdout == b"2\r\n"

        stdout, stderr = await device.aexec_file("my_script.py")


asyncio.run(main())
```

**Callback style**, via `exec_async()`'s `concurrent.futures.Future` - no separate API needed, `Future.add_done_callback()` does this out of the box. Requires the device already started (`astart()`/`start_async()` first, or already inside `async with`):

```python
def on_done(future):
    stdout, stderr = future.result()
    print(stdout.decode())


device.exec_async("print(1 + 1)").add_done_callback(on_done)
```

Both share one `asyncio.Lock` per device: since the device only has a single REPL channel and can't run two `exec()`s at once, calling `exec_async()`/`aexec()` again before a previous call finishes doesn't raise, it just queues behind it and runs once its turn comes. This is exactly what powers the CLI's own `micropython -c/-m/<filename>` batch mode - it's a caller of this same API, not a separate implementation. `start_async()`/`astart()`/`stop()` are available directly if you want more control over the lifecycle than the context manager gives you - `stop()` itself stays a plain synchronous call.

### External devices & custom boards

Beyond the built-in `--board {pico,pico_w}` presets, the emulator has a real extension point for
hardware it doesn't model out of the box:

- **`ExternalDevice`** (`rp2040py.external.device`) - a device implements `attach(rp2040)` and gets
  wired up via `attach_external_devices()`. Devices already shipping in-tree this way: the onboard
  LED, the BOOTSEL button, the RESET button (the RUN pin), a generic button/key, the CYW43439 WiFi
  chip behind `pico_w`, a Waveshare 2.9″ e-Paper panel, an ST7735S TFT controller, and a
  WS2812/WS2812B "NeoPixel" RGB LED.
- **`boards.BoardSpec`** (what `--board` itself resolves to internally) - a public dataclass you
  build your own instance of: your own device mix on an existing firmware family, or a fully custom
  board with its own firmware and flash layout. Hand it to any `Device` class (`board=...`) or the
  CLI (`--board-spec target:attr` / `RP2040PY_BOARD_SPEC`, on `run`/`micropython`/`kaluma`/
  `mklittlefs`). A board declares its firmware as data - a `firmware` dict keyed by family
  (`micropython`/`circuitpython`/`kaluma`), each entry a tag→URL-or-local-path map plus that
  family's flash layout - so one file covers one *board* for every firmware that runs on it,
  downloads nothing when imported, and works with `--image`/`--fetch-fw-only` exactly as `--board`
  does.

#### Adding your own

**[docs/reference/external-devices-and-boards.md](docs/reference/external-devices-and-boards.md)**
is the full how-to: worked examples for both a new device and a new board, the attach-timing rule,
and the caveats worth knowing before you start. If you're working in Claude Code, the
**[external-devices-and-boards skill](.claude/skills/external-devices-and-boards/SKILL.md)** turns
that into a step-by-step execution checklist (which template to copy, which test proves what, and
the "3g rule" - every electrical fact cited to a real upstream source, never guessed).

#### Ready-made example boards

19 worked `--board-spec` targets for real third-party hardware live in [boards/](boards/) - every
number sourced from that board's own upstream firmware config (never guessed), live-boot-verified
against real firmware. See
**[docs/reference/external-devices-and-boards.md](docs/reference/external-devices-and-boards.md#ready-made-examples-in-this-repo)**
for the full list with what each one demonstrates. Screenshots of what the two emulated display
panels actually draw are in [demo/README.md](demo/README.md); see
[docs/records/0049](docs/records/0049-external-device-authoring-docs.md)/
[0059](docs/records/0059-boardspec-firmware-resolution.md) for the design history behind the
extension points themselves.

## Performance

The interpreter core (`CortexM0Core`) and the memory bus's hot read/write paths are also available
as a compiled Cython extension (`rp2040py.native`), giving roughly **7x** the instruction
throughput of the pure-Python implementation on both a synthetic benchmark and a real MicroPython
boot (see [docs/records/0013-cython-core.md](docs/records/0013-cython-core.md#cython-port-of-the-interpreter-core--implemented-on-by-default-real-world-win-confirmed-4x)
for the full measured breakdown).

That extension has since grown past the core itself: the PIO block and its state machines
([0031](docs/records/0031-pio-cython-tick-batching.md),
[0047](docs/records/0047-cyw43-pio-gpio-hotpath.md)), the per-batch execution loop
([0034](docs/records/0034-execute-batch-native-port.md)), the simulation clock
([0039](docs/records/0039-simulation-clock-native-port.md)) and GPIO pins
([0047](docs/records/0047-cyw43-pio-gpio-hotpath.md)) are all native too. Those are wins on top of
the 7x above, on the paths each one covers rather than across the board - the most recent, measured
end to end, is **~2.6x** on a Pico W CYW43 boot through to `scan()` (0047), a PIO/GPIO-heavy
workload the original core port barely touched.

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
CPU/peripheral emulation still tracks it closely, and [docs/reference/porting-checklist.md](docs/reference/porting-checklist.md) keeps a
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
  MicroPython/CircuitPython on rp2040py *used* to have (see `docs/records/0008-ssi-flash-write.md`'s "SSI flash-write
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
- **A real chip reset, from every trigger that has one**: rp2040js's own
  `RPWatchdog.onWatchdogTrigger` (`src/peripherals/watchdog.ts`) defaults to logging "Watchdog
  triggered, but no reset handler provided" and does nothing else - the emulated CPU spins forever
  waiting for a reset that never happens. Here `machine.reset()`/`machine.bootloader()` work, and
  they are one caller of a single reset owner rather than the only path: a **RESET button**
  (`external/reset_button.py` - a real RUN-pin level, so holding it holds the chip in reset) and a
  host-side **`device.ahard_reset()`** reach the same sequence. What that sequence covers is the
  blocks a real reset covers - pads, IO, SIO, clocks, UART/SPI/I2C/PIO/TIMER/ADC/USB/RTC/BUSCTRL
  and the XIP domain - gated by
  `PSM.WDSEL`/`RESETS.WDSEL` exactly as hardware gates them, so a GPIO the guest left driving is
  released and WiFi comes back up on a Pico W. Flash/filesystem content and every
  externally-referenced peripheral object's identity survive (the reset is in place, never a
  reconstruction), and the firmware reports the right `machine.reset_cause()` /
  `microcontroller.cpu.reset_reason` for the trigger that actually fired. `mpremote reset`/
  `mpremote bootloader` (the latter performs the same reset rather than entering actual BOOTSEL
  mode, which isn't implemented) both return promptly instead of hanging.
- **Configurable bootrom revision** (`--bootrom b0`/`b1`/`b2`, or a local `.elf`/`.bin`) - see
  [Bootrom revisions](#bootrom-revisions) below - auto-downloaded and cached the same way firmware
  images are. rp2040js ships exactly one hardcoded bootrom build (`demo/bootrom.ts`, revision B1),
  with no way to select a different revision at all.
- **An optional native-compiled backend** (`rp2040py.native`, Cython) for when pure-Python
  instruction dispatch is the bottleneck - see [Performance](#performance) above - alongside a
  pure-Python universal wheel for environments that can't load compiled extensions at all (e.g.
  Pythonista, see [Installation](#installation)).
- **Pico W / CYW43439 WiFi emulation** (`--board pico_w`) - real `network.WLAN` calls
  (`active()`/`scan()`/`connect()`) against a real, unmodified MicroPython firmware's CYW43439
  driver are answered at the actual gSPI/SDPCM protocol level, not stubbed out, and a NAT bridge
  carries the guest's TCP/UDP traffic onto your host's real network (`socket`, `mip.install()` and
  `ntptime` all reach the actual internet) - something rp2040js has no equivalent of at all (no
  `--board` concept, no WiFi chip emulation). See
  [WiFi (Pico W / CYW43439)](#wifi-pico-w--cyw43439) above.
- **A real extension point for third-party hardware** (`ExternalDevice`/`boards.BoardSpec`) -
  rp2040js has no board or device abstraction at all, only whatever's hardcoded into its own demo
  scripts. rp2040py ships 19 worked `--board-spec` examples for real vendor boards (WeAct Studio,
  four Waveshare boards, VCC-GND Studio, three Adafruit boards, McHobby's PYBStick26, Machdyne,
  nullbits, Pimoroni, Seeed Studio, SparkFun, two 0xCB boards), every electrical fact cited to that board's own upstream firmware source and
  live-boot-verified, plus a documented how-to for writing your own. See
  [External devices & custom boards](#external-devices--custom-boards) above.

See [docs/reference/porting-checklist.md#known-differences-from-rp2040js](docs/reference/porting-checklist.md#known-differences-from-rp2040js)
for the exhaustive, file-level breakdown (including behavioral divergences found while porting,
not just added features).

## Used by

- [ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc) — tests
  its RP2040 `usermod`/`natmod` builds in CI by actually booting real firmware through this
  emulator (`o-murphy/rp2040py/.github/actions/setup-rp2040py`), not just compiling it.

## Learn more

- [rp2040js](https://github.com/wokwi/rp2040js) — the upstream TypeScript emulator this project is ported from.
- [docs/reference/porting-checklist.md](docs/reference/porting-checklist.md) — port status, file by file.
- [docs/reference/os-compatibility.md](docs/reference/os-compatibility.md) — OS × feature compatibility matrix.
- [docs/reference/mpremote.md](docs/reference/mpremote.md) — using `mpremote` with rp2040py in full.
- [docs/reference/external-devices-and-boards.md](docs/reference/external-devices-and-boards.md) — writing your own `ExternalDevice`/`BoardSpec`.
- [docs/0000-TRACKER.md](docs/0000-TRACKER.md) — the engineering-notes index behind every design decision cited above (`docs/records/`).
- [demo/README.md](demo/README.md) — what each demo script does, and a gallery of real emulator output.

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
