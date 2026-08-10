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
  [Pythonista on iOS](../README.md#environments-without-compiled-extension-support-iosandroid)).
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
[Pythonista/PythonIDE](../README.md#environments-without-compiled-extension-support-iosandroid)
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
  [README](../README.md#micropython) for the current syntax.

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
| `mpremote connect ... soft-reset` / Ctrl-D at the raw-REPL prompt | ✅ | Handled entirely by firmware's own soft-reset code - no emulator-side reset needed. |
| `mpremote connect ... resume` | ✅ | |
| `mpremote connect ... rtc` | ✅ | |
| `mpremote connect ... reset` | ✅ | Triggers a real device reset (see below) - reconnect with a fresh `mpremote` invocation afterward, same as a real board re-enumerating over USB. |
| `mpremote connect ... bootloader` | ✅ (with a caveat) | Performs the same device reset as `reset` rather than actually entering the RP2040's USB mass-storage bootloader (BOOTSEL) mode - this emulator doesn't implement that mode. Useful for unblocking scripts that call it as part of a "reboot the device" step; not useful for actually re-flashing over USB mass storage. |
| `mpremote connect ... df` | ⚠️ firmware-version-dependent | Runs `import os, vfs` under the hood - `vfs` doesn't exist as a separate module on MicroPython ≤1.21 (VFS was still bundled directly in `os` then), so `df` fails there with an `ImportError`. Works on newer firmware (e.g. 1.28.0). This is a MicroPython-version compatibility detail, not an rp2040py/emulator bug - confirmed by testing the identical command against both firmware versions. |

`reset`/`bootloader` are implemented by `RPWatchdog.on_watchdog_trigger` (wired up in
`BaseDevice`): `machine.reset()`/`machine.bootloader()` write the watchdog's TRIGGER bit on real
hardware, which now resets the emulated CPU core and USB-CDC enumeration state in place and jumps
back to flash's entry point - preserving flash/filesystem content and every externally-referenced
peripheral object identity (no reconstruction), rather than leaving the emulated CPU spinning
forever (the behavior before this was implemented).

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
