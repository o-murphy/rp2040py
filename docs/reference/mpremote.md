<!-- Reference (living how-to). Migrated verbatim from docs/mpremote.md. -->

<!-- migrated verbatim from docs/mpremote.md lines 1-213 -->

# Using mpremote with rp2040py

[`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) is the official
MicroPython remote-control tool. rp2040py has two flags that serve the device's USB-CDC console
over something other than this process's own stdio, so `mpremote` (or anything else built on
pySerial) can drive an emulated device the same way it drives a real board, plus an `rp2040py
mpremote` proxy subcommand (see "mpremote proxy" below) that patches around a real upstream
`mpremote` bug so its bare interactive REPL works over `--tcp-port` too, not just `--pty`:

- **`--tcp-port`**: a plain TCP socket, via pySerial's built-in `socket://host:port` URL support -
  no serial port or pty needed on the host at all, useful for CI, scripting, and environments with
  no serial support (e.g.
  [Pythonista on iOS](../../README.md#installation)).
  `exec`/`fs`/`run`/... all work fine over it; the real (unpatched) `mpremote` binary's own **bare
  interactive REPL does not** - see "What doesn't work" below - but `rp2040py mpremote` (see
  "mpremote proxy" below) patches around exactly that, so use it instead if you want the
  interactive REPL over `--tcp-port` specifically.
- **`--pty`** (POSIX only): a real pseudo-terminal pair, whose slave side is a genuine POSIX serial
  device path (e.g. `/dev/pts/3` on Linux). Everything `--tcp-port` supports also works here,
  *plus* the bare interactive REPL - see "Why `--pty` exists" below for why that specifically needs
  a real pty rather than a socket.

Pick whichever your environment actually supports and your use case needs; both are otherwise
close to interchangeable (same quitting story, same reconnect-friendly one-client-at-a-time
behavior).

## Quick start

```sh
rp2040py micropython --tcp-port 4321
# in another terminal:
mpremote connect socket://127.0.0.1:4321 exec "print(1 + 1)"
mpremote connect socket://127.0.0.1:4321 fs cp your_script.py :main.py
```

```sh
rp2040py micropython --pty
# logs e.g. "PTY REPL listening on /dev/pts/3 - e.g. `mpremote connect /dev/pts/3`"
# in another terminal:
mpremote connect /dev/pts/3 repl
```

```sh
rp2040py micropython --tcp-port 4321
# in another terminal - rp2040py mpremote instead of plain mpremote, same arguments otherwise:
rp2040py mpremote connect socket://127.0.0.1:4321 repl
```

`--tcp-port 0` asks the OS for a free port instead of a fixed one - watch the logged "listening
on ..." line for which port it picked; `--pty` always logs the slave device path it opened the
same way, since there's no fixed/default path to guess otherwise.

Only one client is served at a time, matching a real serial port's exclusive-access semantics - a
second connection while one is already active is closed immediately over `--tcp-port` (over
`--pty`, nothing stops two processes from opening the same device path simultaneously and
corrupting each other's I/O either, exactly as with a real serial port). Once a client disconnects,
the next one is accepted normally, so repeated `mpremote` invocations against the same long-running
`rp2040py micropython --tcp-port/--pty ...` process work as expected - each `mpremote` subcommand
below is its own short-lived connection, exactly like this.

`--tcp-port` and `--pty` are mutually exclusive with each other and with `-c`/`-m`/`<filename>` on
`micropython` (those already run once and exit; `--tcp-port`/`--pty` replace the console for the
device's whole lifetime instead).

## Why `--pty` exists

`mpremote`'s bare interactive REPL (`mpremote repl`, or `mpremote connect ...` with no subcommand)
crashes over `--tcp-port`'s `socket://` transport: `mpremote`'s `console.py` does
`select.select([self.infd, pyb_serial.fd], [], [])`, which requires the connected pySerial object
to expose a `.fd` attribute - something pySerial's `socket://` URL handler never provides (only its
POSIX serial backend does). A real pty's slave side *is* a normal POSIX tty device that pySerial
opens via that POSIX backend, `.fd` included - so the same REPL code works against it exactly as it
would against a real board. See "What doesn't work" below for the exact traceback this avoids.

## mpremote proxy

`rp2040py mpremote <args...>` runs the real `mpremote` with every argument forwarded verbatim
(`rp2040py mpremote connect socket://host:port repl` is exactly `mpremote connect
socket://host:port repl`, argument for argument - including `-h`/`--help`/`--version`), but first
monkeypatches `mpremote.console.ConsolePosix.waitchar()` to fall back to the wrapped socket itself
(pySerial's own private `_socket` attribute - there's no public accessor) when `.fd` isn't there. A
raw `socket.socket` is select()-able on its own (it implements `fileno()`, all `select.select()`
actually needs) - `.fd` was never the only way to make this work, `mpremote` just never falls back
to it. This is a real bug in `mpremote`/pySerial, not in rp2040py; filed upstream at
https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170. `ConsoleWindows`
(the other half of `mpremote.console.Console`) never reads `.fd` in the first place - it polls
`pyb_serial.inWaiting()` instead - so nothing is patched, or needed, there.

Use this instead of the real `mpremote` binary whenever you want the bare interactive REPL over
`--tcp-port` specifically - or as a drop-in replacement for `mpremote` generally, since every other
command works identically either way (the patch only changes `waitchar()`'s behavior, and only when
`.fd` is actually missing). `--pty` (above) is still the better choice for anything besides
`mpremote` itself that needs a *real* serial device path (Thonny, `screen`, `minicom`, ...) - this
proxy is `mpremote`-specific and doesn't help those - but it needs no POSIX pty support at all, so
it also covers Windows and sandboxed/no-pty environments `--pty` can't reach.

> [!TIP]
> `rp2040py mpremote connect socket://host:port repl` also works against `rp2040py kaluma
> --tcp-port ...`, not just `micropython` - the patch is entirely inside `mpremote`'s own terminal
> code (`waitchar()`), with no dependency on which firmware is on the other end of the socket.
> Verified by hand: a typed expression echoes and evaluates correctly, and the session exits
> cleanly with no `AttributeError`.

### Android/Termux (and iOS): pySerial's `list_ports` `ImportError`

On Android (e.g. [Termux](https://github.com/termux)), the real `mpremote` binary fails before it
even parses its arguments - `mpremote`'s `commands.py` does `import serial.tools.list_ports`
unconditionally at module load, and pySerial's own `serial.tools.list_ports_posix` picks its
backend off `sys.platform` (`'linux'`, `'darwin'`, `'cygwin'`, ...), with no case for Android -
falling into a final `else` that raises `ImportError: Sorry: no implementation for your platform
('posix') available`. This happens even for e.g. `mpremote connect /dev/pts/1 repl`, which names
its port explicitly and never actually calls `comports()` to enumerate anything - the crash is at
import time, not at the point serial-port enumeration would happen. This is a real gap in
pySerial's own platform dispatch, not an rp2040py bug. Going by the same platform-string logic,
iOS's `sys.platform` (`'ios'`) isn't in pySerial's list either, so
[Pythonista/PythonIDE](../../README.md#installation)
hit the identical crash - confirmed on-device now, not just inferred from the platform-string logic
(see the README's "Tested" list).

`rp2040py mpremote` (the same proxy described above) also patches around this - on any platform,
not just Android specifically: before importing `mpremote.main`, it tries `import
serial.tools.list_ports` itself and, only if that raises `ImportError`, substitutes a stub module
whose `comports()` returns no devices. `connect <explicit-port>` never calls `comports()` in the
first place, so the stub is invisible there; `mpremote` subcommands that *do* enumerate devices
(e.g. auto-detecting a lone connected board) simply see none, which is also correct - there's no
real serial port list to report on these platforms anyway, whether real `mpremote` or the proxy is
asking. The plain `mpremote` binary is unaffected by this patch and still crashes the same way; use
`rp2040py mpremote` instead whenever running under Termux/Android, Pydroid 3, or iOS - confirmed
working over `--tcp-port` on all of them by hand (see the README's "Tested" list). Running the
emulator and `mpremote` at the same time within a sandboxed iOS app is a separate, unrelated
limitation - see "What doesn't work" below.

## Quitting the emulator

Unlike the interactive REPL, Ctrl+X isn't intercepted on either of these transports - a real client
like `mpremote` runs its own protocol over the byte stream (raw-REPL's own Ctrl-A/Ctrl-C/Ctrl-D
among them, or the REPL's own keystrokes once connected), and stealing a byte meant for it would
corrupt that protocol. Quit the `rp2040py` process itself instead:

- **Ctrl+C** in the terminal running `rp2040py micropython --tcp-port/--pty ...`.
- **`kill <pid>`** (SIGTERM) - handled explicitly, same as Ctrl+C: `--dump-fs`'s cleanup (saving
  the filesystem back out) still runs before the process exits. Without this, `kill`'s default
  disposition would terminate the process immediately, skipping every `finally`/context-manager
  cleanup - simply not sending anything meant for `mpremote` isn't enough on its own.
- **`--expect-text`** (optionally with `--expect-regex`) - stop once given text appears on the
  device's console, same as every other subcommand; see the main
  [README](../../README.md#micropython-code) for the current syntax.

## What's verified working

Verified by hand against real MicroPython firmware (1.21.0 and 1.28.0) over both transports, plus
automated regression tests: `tests/test_mpremote_integration.py` (a real `mpremote` subprocess
driven over a real `socket://` connection against a scripted stand-in for firmware, since CI can't
always assume a network download for real firmware) for `exec`/`fs cp` over `--tcp-port`, and
`tests/test_pty_repl.py` for `--pty`'s own transport-level behavior (device output/input forwarding,
backpressure, SIGTERM handling - not a full `mpremote` round trip, which needs a real pty/subprocess
combination outside what CI can assume either):

| Command | Status | Notes |
| --- | --- | --- |
| `mpremote connect socket://host:port exec "..."` / `mpremote connect /dev/pts/N exec "..."` | ✅ | `socket://` covered by an automated test; both verified by hand. |
| `mpremote connect ... eval "..."` | ✅ | |
| `mpremote connect ... run script.py` | ✅ | |
| `mpremote connect ... fs cp/ls/cat/mkdir/rmdir/rm/touch/tree/sha256sum ...` | ✅ | `fs cp` over `socket://` is covered by an automated test. |
| `mpremote connect ... mount ./local_dir` | ✅ | Including running a script straight out of the mounted directory. |
| `mpremote connect ... repl` (bare interactive REPL) | ✅ over `--pty`; ✅ over `--tcp-port` via `rp2040py mpremote` | The real `mpremote` binary crashes over `--tcp-port`'s `socket://` transport - see "What doesn't work" below - unless run through `rp2040py mpremote` (see "mpremote proxy" above), which patches around it. |
| `mpremote connect ... soft-reset` / Ctrl-D at the raw-REPL prompt | ✅ | Handled entirely by firmware's own soft-reset code - no emulator-side reset needed. **It restarts the VM but does not re-run `main.py`/`code.py`** - see "Soft reset: raw prompt vs friendly prompt" below. |
| `mpremote connect ... resume` | ✅ | |
| `mpremote connect ... rtc` | ✅ | |
| `mpremote connect ... reset` | ✅ | Triggers a real device reset (see below) - reconnect with a fresh `mpremote` invocation afterward, same as a real board re-enumerating over USB. |
| `mpremote connect ... bootloader` | ✅ (with a caveat) | Performs the same device reset as `reset` rather than actually entering the RP2040's USB mass-storage bootloader (BOOTSEL) mode - this emulator doesn't implement that mode. Useful for unblocking scripts that call it as part of a "reboot the device" step; not useful for actually re-flashing over USB mass storage. |
| `mpremote connect ... df` | ⚠️ firmware-version-dependent | Runs `import os, vfs` under the hood - `vfs` doesn't exist as a separate module on MicroPython ≤1.21 (VFS was still bundled directly in `os` then), so `df` fails there with an `ImportError`. Works on newer firmware (e.g. 1.28.0). This is a MicroPython-version compatibility detail, not an rp2040py/emulator bug - confirmed by testing the identical command against both firmware versions. |

`reset`/`bootloader` are implemented by `RPWatchdog.on_watchdog_trigger` (wired up in
`BaseDevice`): `machine.reset()`/`machine.bootloader()` write the watchdog's TRIGGER bit on real
hardware, which resets the emulated CPU core and USB-CDC enumeration state in place and jumps
back to flash's entry point - preserving flash/filesystem content and every externally-referenced
peripheral object identity (no reconstruction), rather than leaving the emulated CPU spinning
forever (the behavior before this was implemented).

That handler is now one caller of a single hard-reset owner rather than the only reset path
([record 0089](../records/0089-one-reset-for-every-trigger.md)). The others, reaching the same
sequence: a **RESET button** (`external/reset_button.py` - `press()` holds the chip in reset,
`release()` boots it), and a **host-side** `device.ahard_reset()`/`hard_reset_async()`. Since that
record's Phase 5 the reset also covers what a real one covers - pads, IO, SIO, clocks and the
peripheral blocks, gated by `PSM.WDSEL`/`RESETS.WDSEL` exactly as hardware gates them - so a GPIO
the guest left driving is released, and the firmware reports the right `machine.reset_cause()`
for the trigger that actually fired.

### Soft reset: raw prompt vs friendly prompt

A soft reset restarts the VM without resetting the chip, and **which prompt it is sent at decides
whether the startup script re-runs**. Measured against real MicroPython v1.23.0 and CircuitPython
10.2.1, byte for byte (0089's Appendix, point 1) - both families behave identically:

| Ctrl-D sent at... | firmware prints | `main.py`/`code.py` re-runs? |
| --- | --- | --- |
| the **raw** prompt (what `mpremote soft-reset` does) | `OK`, then `MPY: soft reboot` / `soft reboot`, then the raw banner | **no** |
| the **friendly** prompt | `MPY: soft reboot` / `soft reboot`, then the script's own output | **yes** |

This matches both firmwares' source: `pyexec_raw_repl()` answers an empty line + Ctrl-D with `OK`
and `PYEXEC_FORCED_EXIT`, and the main loop only re-runs the startup script when
`pyexec_mode_kind == PYEXEC_MODE_FRIENDLY_REPL`.

So `mpremote soft-reset` is not a way to re-run a script you just uploaded. Neither, it turns
out, is asking the guest to restart itself from an `aexec()`/`exec_async()`:

```python
await device.aexec("import supervisor\nsupervisor.reload()")  # CircuitPython: does NOT re-run code.py
```

Measured 2026-08-20 on CircuitPython 10.2.1: the call returns cleanly, and then **nothing
happens** - no `soft reboot`, no `code.py output:`, no console output at all, and a display demo
driven this way sat for 20 minutes without a single frame. The reason is the same raw-vs-friendly
split as the table above: `RawReplRunner` never sends Ctrl-B, so an exec leaves the device *in*
the raw REPL, and a restart from there comes back to a raw prompt with the startup script skipped.

What does work, from the device API, is sending the two bytes a person would type - Ctrl-B to
leave the raw REPL, then Ctrl-D at the friendly prompt:

```python
from rp2040py.device import CTRL_B, CTRL_D

for byte in (CTRL_B, CTRL_D):
    device.simulator.schedule_threadsafe(lambda value=byte: device.cdc.send_serial_byte(value))
```

Measured on the same firmware, the console then shows `soft reboot` -> `code.py output:` -> the
new file's output, and `demo/wifi_lcd_run.py` uses exactly this to run the `code.py` it just
pushed. A *hard* reset (`ahard_reset()`) re-runs everything too, including `boot.py`, at the cost
of re-enumerating USB.

There is deliberately **no** `asoft_reset()` on the device API: a soft reset is bytes the firmware
already answers, from paths that all already exist, so a second way to express it was built,
verified and then dropped (0089's Phase 3). A *hard* reset - which does re-run everything, because
the chip reboots - is `ahard_reset()`.

### Against CircuitPython firmware

Everything above was verified against MicroPython. `mpremote` is a MicroPython tool, and the split
against CircuitPython is not "works / doesn't" but **per subcommand**, decided entirely by which
`os` functions the firmware exposes. Measured 2026-08-20 against CircuitPython 10.2.1 (Pico W)
under `rp2040py micropython --circuitpython --tcp-port`, `mpremote` 1.28.0; the MicroPython column
is v1.23.0 in the same emulator for contrast.

| Command | CircuitPython | Why |
| --- | :---: | --- |
| `exec` / `eval` / `run script.py` | ✅ | The raw REPL is family-agnostic - `RawReplRunner` has one banner constant for both. |
| `soft-reset` | ✅ | Firmware's own; at the raw prompt it does not re-run `code.py` (see above) - same as MicroPython. |
| `fs cat` / `sha256sum` / `mkdir` / `touch` / `rm` | ✅ | Plain `open()` / `os.mkdir` / `os.remove`, all present. Writes need the remount below. |
| `fs cp` **device → host** | ✅ | Reads an existing file. |
| `fs cp` **host → device** | ⚠️ only if the destination file **already exists** | See "the errno name" below. Onto a new name it fails with `OSError: [Errno 2]` before writing anything. |
| `fs ls` / `fs tree` | ❌ | `AttributeError: 'module' object has no attribute 'ilistdir'`. |
| `mount ./local_dir` | ❌ | `AttributeError: 'module' object has no attribute 'mount'`. |
| `df` | ❌ | `ImportError: no module named 'vfs'` - the same shape as MicroPython ≤1.21 in the table above, for the same reason. |

The firmware's own answer to `dir(os)` is the whole story, and is worth quoting rather than
paraphrasing - CircuitPython 10.2.1:

```
['__class__', '__dict__', '__name__', 'chdir', 'getcwd', 'getenv', 'listdir', 'mkdir', 'remove',
 'rename', 'rmdir', 'sep', 'stat', 'statvfs', 'sync', 'uname', 'unlink', 'urandom', 'utime']
```

...against MicroPython v1.23.0, which has the three names the ❌ rows need (`ilistdir`, `mount`,
plus `VfsFat`/`VfsLfs2`, and a separate `vfs` module):

```
['VfsFat', 'VfsLfs2', '__class__', '__dict__', '__name__', 'chdir', 'dupterm', 'dupterm_notify',
 'getcwd', 'ilistdir', 'listdir', 'mkdir', 'mount', 'remove', 'rename', 'rmdir', 'stat',
 'statvfs', 'sync', 'umount', 'uname', 'unlink', 'urandom']
```

**Two CircuitPython-specific things to know before any write.**

1. **The filesystem starts read-only to the REPL** - `OSError: [Errno 30] Read-only filesystem`.
   One line fixes it for the session: `import storage; storage.remount('/', readonly=False)`.
   Measured: the flag **survives a soft reset** (`mpremote resume soft-reset`, then a write, still
   works) but not a chip reset, since it is RAM state. On real hardware this line raises instead,
   whenever a USB host holds the mass-storage lock - this emulator never claims that interface, so
   it is permanently in the state a real board reaches only after the drive is ejected
   ([record 0087](../records/0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)).
2. **The errno name**, which is the entire reason for the `fs cp` caveat. `fs cp` first probes
   whether the destination exists (`fs_exists` -> `os.stat`), expecting a catchable `OSError`.
   MicroPython renders one as `OSError: [Errno 2] ENOENT`; CircuitPython renders it as
   `OSError: [Errno 2] No such file/directory`. `mpremote`'s `_convert_filesystem_error`
   (`transport.py`) matches the *name* - it scans the traceback for `ENOENT` and friends, then for
   a bare `OSError: <number>` line - so the CircuitPython text matches neither, the raw
   `TransportExecError` escapes `except OSError`, and the copy dies on the probe. Traced call by
   call: the probe is the only thing that fails - `fs_writefile()` on the same connection writes
   the file fine, and `fs cp` onto a name that already exists succeeds.

None of this is an rp2040py limitation, and none of it is fixable here: it is `mpremote` talking
to firmware it was not written for, and a real CircuitPython board on a real serial port answers
identically. For pushing files at CircuitPython, use the guest itself - `remount()` plus
`open()`/`write()` over `rp2040py micropython --circuitpython -c ...`, which is what
`demo/mklittlefs_dump.py` does for MicroPython and what 0087 measured for CircuitPython.


## What doesn't work

- **Bare interactive REPL over `--tcp-port`, through the real (unpatched) `mpremote` binary**
  (`mpremote repl`, or `mpremote connect socket://host:port` with no subcommand) - **not
  supported over `socket://`** this way; use `rp2040py mpremote` instead (see "mpremote proxy"
  above), which patches around it, or switch to `--pty` (see above), which sidesteps it entirely.
  `mpremote`'s interactive console (`console.py`'s `waitchar()`) does
  `select.select([self.infd, pyb_serial.fd], [], [])`, which requires the serial object to expose a
  `.fd` attribute - but pySerial's `socket://` URL handler
  (`serial.urlhandler.protocol_socket.Serial`) never defines `.fd` (only its POSIX serial
  implementation does, which is exactly what a real pty's slave side is). This is a genuine gap in
  `mpremote`/pySerial's own `socket://` support, not an rp2040py bug (filed upstream at
  https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170): the underlying
  connection and raw-REPL command execution both work fine over `socket://` too (confirmed by
  driving a real pty by hand and typing commands through it - the crash only happens in
  `mpremote`'s own terminal-multiplexing code, after the connection is already up). Use
  `exec`/`run`/one-shot commands, `rp2040py mpremote` instead of plain `mpremote`, or switch to
  `--pty`, instead of the real `mpremote` binary's interactive REPL when driving rp2040py through
  `mpremote` over `--tcp-port`.
- **`--pty` on Windows** - not supported (`pty.openpty()`/`os.ttyname()` have no Windows
  equivalent); `rp2040py micropython --pty` exits with a clear error there instead of a crash. Use
  `--tcp-port` on Windows instead - its own limitation is only the bare interactive REPL above, not
  the whole transport.
- **Running the emulator and `mpremote` at the same time, inside a sandboxed iOS app**
  (Pythonista/PythonIDE) - confirmed by hand not to work, even though `mpremote` itself works fine
  standalone over `--tcp-port` on the same apps (see the README's "Tested" list). These apps appear
  to run only one Python process per app instance, with no real subprocess/multi-process support -
  so there's no way to have `rp2040py micropython --tcp-port ...` running in the background while a
  separate `mpremote` invocation talks to it, the way this works on a normal OS (Linux/Termux,
  Android/Pydroid 3, macOS, Windows). This is a constraint of the app sandbox itself, not an
  rp2040py bug, and not something `rp2040py mpremote` can patch around (unlike the `list_ports`
  `ImportError` above) - there's no known workaround on-device short of running the emulator and
  `mpremote` in two separate app instances/devices instead of one.
