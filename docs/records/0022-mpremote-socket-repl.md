# 0022. mpremote bare interactive REPL crash over socket:// (upstream micropython#18660)

- Status: Implemented
- Conceived: 2026-08-10 · #27
- Related: #27, #30 (termux) · 0020 (passthrough)

<!-- migrated verbatim from docs/BACKLOG.md lines 1426-1459 -->

## `mpremote` bare interactive REPL crash over `socket://` (upstream micropython#18660) — DONE

**Problem:** `mpremote repl` / `mpremote connect socket://host:port` (no subcommand) crashes with
`AttributeError: 'Serial' object has no attribute 'fd'`. Root cause is entirely inside `mpremote`
itself: `console.py`'s `Console.waitchar()` unconditionally does
`select.select([self.infd, pyb_serial.fd], [], [])`, assuming every connected pySerial object
exposes a raw POSIX file descriptor via `.fd` - true of pySerial's POSIX serial backend (which is
exactly what `--pty`'s slave side goes through, see the "PTY / real serial port passthrough"
section above and `docs/mpremote.md`), but pySerial's `socket://` URL handler
(`serial.urlhandler.protocol_socket.Serial`) wraps a plain `socket.socket` and never defines
`.fd`. Filed upstream (this project's own maintainer) at
https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170 - `--tcp-port`'s
transport and raw-REPL command execution both work fine over `socket://`; only `mpremote`'s own
terminal-multiplexing code crashes.

**What landed:** a new `rp2040py mpremote <args...>` subcommand
(`cli/__init__.py`'s `_cmd_mpremote`/`_patch_mpremote_console_waitchar`) that forwards every
argument verbatim to the real `mpremote`, after monkeypatching
`mpremote.console.ConsolePosix.waitchar()` to fall back to the wrapped socket itself
(`pyb_serial._socket` - pySerial's own private attribute, there's no public accessor) when `.fd`
isn't there. Sockets are select()-able on their own (they implement `fileno()`, all
`select.select()` actually needs) - `.fd` was never the only way to make this work.
`ConsoleWindows` (the other half of `mpremote.console.Console`) never reads `.fd` in the first
place (it polls `pyb_serial.inWaiting()` instead), so nothing needs patching, or is patched, on
Windows. `mpremote` moved from a `dev`-only dependency to a normal `pyproject.toml` runtime
dependency, since the subcommand needs it importable outside a dev checkout.

This is a narrower fix than `--pty` (still the right choice for anything needing a *real* serial
device path, e.g. tools other than `mpremote`) but doesn't need POSIX pty support at all, so it
also covers Windows and sandboxed/no-pty environments `--pty` can't reach - see
`docs/mpremote.md`'s "mpremote proxy" section for the up-to-date picture of which approach to
reach for. Verified against the running emulator: `rp2040py mpremote connect
socket://host:port repl`, driven over a real pty to simulate an interactive terminal, connects,
executes a typed command, and exits cleanly with no `AttributeError`.
