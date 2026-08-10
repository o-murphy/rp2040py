# Using mpremote with rp2040py

[`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) is the official
MicroPython remote-control tool. rp2040py has two flags that serve the device's USB-CDC console
over something other than this process's own stdio, so `mpremote` (or anything else built on
pySerial) can drive an emulated device the same way it drives a real board:

- **`--tcp-port`**: a plain TCP socket, via pySerial's built-in `socket://host:port` URL support -
  no serial port or pty needed on the host at all, useful for CI, scripting, and environments with
  no serial support (e.g.
  [Pythonista on iOS](../README.md#environments-without-compiled-extension-support-pythonista-other-ios-apps)).
  `exec`/`fs`/`run`/... all work fine over it, but `mpremote`'s own **bare interactive REPL does
  not** - see "What doesn't work" below.
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
| `mpremote connect ... repl` (bare interactive REPL) | ✅ over `--pty` only | Does **not** work over `--tcp-port`'s `socket://` transport - see "Why `--pty` exists" and "What doesn't work" below. |
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

- **Bare interactive REPL over `--tcp-port`** (`mpremote repl`, or `mpremote connect
  socket://host:port` with no subcommand) - **not supported over `socket://`**, and not fixable
  from rp2040py's side - use `--pty` instead (see above), which does support it. `mpremote`'s
  interactive console (`console.py`'s `waitchar()`) does
  `select.select([self.infd, pyb_serial.fd], [], [])`, which requires the serial object to expose a
  `.fd` attribute - but pySerial's `socket://` URL handler
  (`serial.urlhandler.protocol_socket.Serial`) never defines `.fd` (only its POSIX serial
  implementation does, which is exactly what a real pty's slave side is). This is a genuine gap in
  `mpremote`/pySerial's own `socket://` support, not an rp2040py bug: the underlying connection and
  raw-REPL command execution both work fine over `socket://` too (confirmed by driving a real pty
  by hand and typing commands through it - the crash only happens in `mpremote`'s own
  terminal-multiplexing code, after the connection is already up). Use `exec`/`run`/one-shot
  commands (or switch to `--pty`) instead of the interactive REPL when driving rp2040py through
  `mpremote` over `--tcp-port`.
- **`--pty` on Windows** - not supported (`pty.openpty()`/`os.ttyname()` have no Windows
  equivalent); `rp2040py micropython --pty` exits with a clear error there instead of a crash. Use
  `--tcp-port` on Windows instead - its own limitation is only the bare interactive REPL above, not
  the whole transport.
