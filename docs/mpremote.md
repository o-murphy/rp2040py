# Using mpremote with rp2040py

[`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) is the official
MicroPython remote-control tool. rp2040py's `--tcp-port` flag serves the device's USB-CDC console
over a plain TCP socket instead of this process's own stdio, so `mpremote` (or anything else built
on pySerial) can drive an emulated device the same way it drives a real board over a serial port -
useful for CI, scripting, and for environments with no serial support at all (e.g.
[Pythonista on iOS](../README.md#environments-without-compiled-extension-support-pythonista-other-ios-apps)).

No client-side patching needed: `mpremote`'s transport opens its connection via pySerial's
`serial.serial_for_url()`, which ships built-in support for `socket://host:port` URLs - a raw byte
pipe over TCP with nothing layered on top (unlike `rfc2217://`, which does Telnet option
negotiation) - so `mpremote connect socket://host:port` talks directly to rp2040py with no adapter
in between.

## Quick start

```sh
rp2040py micropython --tcp-port 4321
# in another terminal:
mpremote connect socket://127.0.0.1:4321 exec "print(1 + 1)"
mpremote connect socket://127.0.0.1:4321 fs cp your_script.py :main.py
```

`--tcp-port 0` asks the OS for a free port instead of a fixed one - watch the logged "TCP socket
REPL listening on ..." line for which port it picked.

Only one client is served at a time, matching a real serial port's exclusive-access semantics - a
second connection while one is already active is closed immediately (once the first disconnects,
the next one is accepted normally, so repeated `mpremote` invocations against the same
long-running `rp2040py micropython --tcp-port ...` process work as expected - each `mpremote`
subcommand below is its own short-lived connection, exactly like this).

`--tcp-port` is mutually exclusive with `-c`/`-m`/`<filename>` on `micropython` (those already run
once and exit; `--tcp-port` replaces the console for the device's whole lifetime instead).

## Quitting the emulator

Unlike the interactive REPL, Ctrl+X isn't intercepted on this transport - a real client like
`mpremote` runs its own protocol over the byte stream (raw-REPL's own Ctrl-A/Ctrl-C/Ctrl-D among
them), and stealing a byte meant for it would corrupt that protocol. Quit the `rp2040py` process
itself instead:

- **Ctrl+C** in the terminal running `rp2040py micropython --tcp-port ...`.
- **`kill <pid>`** (SIGTERM) - handled explicitly, same as Ctrl+C: `--dump-fs`'s cleanup (saving
  the filesystem back out) still runs before the process exits. Without this, `kill`'s default
  disposition would terminate the process immediately, skipping every `finally`/context-manager
  cleanup - simply not sending anything meant for `mpremote` isn't enough on its own.
- **`--expect-text`** (optionally with `--expect-regex`) - stop once given text appears on the
  device's console, same as every other subcommand; see the main
  [README](../README.md#micropython) for the current syntax.

## What's verified working

Verified by hand against real MicroPython firmware (1.21.0 and 1.28.0) over this exact transport,
plus an automated regression test for `exec`/`fs cp`
(`tests/test_mpremote_integration.py` - a real `mpremote` subprocess driven over a real
`socket://` connection against a scripted stand-in for firmware, since CI can't always assume a
network download for real firmware):

| Command | Status | Notes |
| --- | --- | --- |
| `mpremote connect socket://host:port exec "..."` | ✅ | Covered by an automated test. |
| `mpremote connect socket://host:port eval "..."` | ✅ | |
| `mpremote connect socket://host:port run script.py` | ✅ | |
| `mpremote connect socket://host:port fs cp/ls/cat/mkdir/rmdir/rm/touch/tree/sha256sum ...` | ✅ | `fs cp` is covered by an automated test. |
| `mpremote connect socket://host:port mount ./local_dir` | ✅ | Including running a script straight out of the mounted directory. |
| `mpremote connect socket://host:port soft-reset` / Ctrl-D at the raw-REPL prompt | ✅ | Handled entirely by firmware's own soft-reset code - no emulator-side reset needed. |
| `mpremote connect socket://host:port resume` | ✅ | |
| `mpremote connect socket://host:port rtc` | ✅ | |
| `mpremote connect socket://host:port reset` | ✅ | Triggers a real device reset (see below) - reconnect with a fresh `mpremote` invocation afterward, same as a real board re-enumerating over USB. |
| `mpremote connect socket://host:port bootloader` | ✅ (with a caveat) | Performs the same device reset as `reset` rather than actually entering the RP2040's USB mass-storage bootloader (BOOTSEL) mode - this emulator doesn't implement that mode. Useful for unblocking scripts that call it as part of a "reboot the device" step; not useful for actually re-flashing over USB mass storage. |
| `mpremote connect socket://host:port df` | ⚠️ firmware-version-dependent | Runs `import os, vfs` under the hood - `vfs` doesn't exist as a separate module on MicroPython ≤1.21 (VFS was still bundled directly in `os` then), so `df` fails there with an `ImportError`. Works on newer firmware (e.g. 1.28.0). This is a MicroPython-version compatibility detail, not an rp2040py/emulator bug - confirmed by testing the identical command against both firmware versions. |

`reset`/`bootloader` are implemented by `RPWatchdog.on_watchdog_trigger` (wired up in
`BaseDevice`): `machine.reset()`/`machine.bootloader()` write the watchdog's TRIGGER bit on real
hardware, which now resets the emulated CPU core and USB-CDC enumeration state in place and jumps
back to flash's entry point - preserving flash/filesystem content and every externally-referenced
peripheral object identity (no reconstruction), rather than leaving the emulated CPU spinning
forever (the behavior before this was implemented).

## What doesn't work

- **Bare interactive REPL** (`mpremote repl`, or `mpremote connect socket://host:port` with no
  subcommand) - **not supported**, and not fixable from rp2040py's side. `mpremote`'s interactive
  console (`console.py`'s `waitchar()`) does `select.select([self.infd, pyb_serial.fd], [], [])`,
  which requires the serial object to expose a `.fd` attribute - but pySerial's `socket://` URL
  handler (`serial.urlhandler.protocol_socket.Serial`) never defines `.fd` (only its POSIX serial
  implementation does). This is a genuine gap in `mpremote`/pySerial's own `socket://` support, not
  an rp2040py bug: the underlying connection and raw-REPL command execution both work fine over
  this transport (confirmed by driving a real pty by hand and typing commands through it - the
  crash only happens in `mpremote`'s own terminal-multiplexing code, after the connection is
  already up). Use `exec`/`run`/one-shot commands instead of the interactive REPL when driving
  rp2040py through `mpremote`.
