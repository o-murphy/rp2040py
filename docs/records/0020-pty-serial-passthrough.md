# 0020. PTY / real serial-port passthrough (--pty, --tcp-port, rp2040py mpremote)

- Status: Implemented
- Conceived: 2026-08-10
- Related: reference/mpremote.md, 0022 (socket REPL)

<!-- migrated verbatim from docs/BACKLOG.md lines 1302-1340 -->

## PTY / real serial port passthrough for external tools — DONE, shipped as `--pty`

**Update: shipped.** `rp2040py micropython --pty` / `rp2040py kaluma --pty` (POSIX only) -
`src/rp2040py/cli/pty_repl.py`'s `PtyInteractiveRepl` - bridges `USBCDC`'s console to a real
`pty.openpty()` master/slave pair and logs the slave side's path (`/dev/pts/N`) for the user to
point an external tool at, exactly per the rough shape below. Ended up as a flag on the existing
`micropython`/`kaluma` subcommands, as the first bullet below guessed. See
README.md's "mpremote" section and [docs/mpremote.md](mpremote.md) ("Why `--pty` exists") for the
user-facing picture, and CHANGELOG.md's `[Unreleased]` Added section for the implementation
writeup. Windows has no `pty.openpty()`/`os.ttyname()` equivalent, so `--pty` there exits with a
clear error instead - use `--tcp-port` on Windows instead (see docs/mpremote.md's "What doesn't
work"). Left below for historical context on the original design questions.

**Original goal:** something like `rp2040py micropython --pty` / `rp2040py kaluma --pty` (exact flag name
not decided - "ttyrepl" was also floated) that exposes the emulated device's USB-CDC console as a
real host-side pseudo-terminal, instead of only being reachable through this project's own
`StdioInteractiveRepl`/raw-REPL API. The point is letting *external* tools that expect a real
serial port - Thonny IDE, `mpremote`, `screen`, `minicom`, etc. - connect to the emulator exactly
like they would to a real Pico over USB, with no rp2040py-specific client needed.

**Rough shape, not designed yet:**
- `pty.openpty()`/`os.openpty()` gives a master/slave fd pair; bridge `USBCDC`'s existing
  read/write path to the master fd instead of (or in addition to) stdio, and print the slave side's
  path (`/dev/pts/N` on Linux) for the user to point their tool at - similar in spirit to `socat`'s
  `PTY,link=<path>` convention, so it's discoverable without guessing.
- Decide whether this is a flag on the existing `micropython`/`kaluma` subcommands (probably
  simpler, reuses all existing boot/littlefs/bootrom plumbing) or a distinct mode.
  `demo/kaluma_run.py`'s door already models "boot + hand off a serial-shaped interface to
  something else" fairly closely, since that's what its own `StdioInteractiveRepl` does today -
  a PTY-backed swap-in for the stdio side is probably the smallest change, but hasn't been
  scoped out.
- No investigation done yet into interactions with the raw-REPL machinery (`device/raw_repl.py`)
  used by `-c`/`-m`/`<filename>` - those likely need to keep working independently of whether `--pty`
  is also active, or be explicitly mutually exclusive with it; not yet decided which.
- A disconnect handler here has somewhere to report to now: `Simulator.shutdown_request.request()`
  (see "Unified process-shutdown coordinator" below) - it didn't exist when this section was first
  written, and every shutdown trigger before it had to either duplicate `os._exit()`-based
  teardown or grow its own bespoke path. A `--pty` disconnect would just be one more caller.


<!-- migrated verbatim from docs/PORTING.md lines 782-801 -->

### External serial-tool passthrough (`--tcp-port`/`--pty`, `rp2040py mpremote`) - no rp2040js equivalent

`cli/socket_repl.py`'s `SocketInteractiveRepl` (`--tcp-port`) and `cli/pty_repl.py`'s
`PtyInteractiveRepl` (`--pty`, POSIX only) serve the device's USB-CDC console over a real TCP
socket or pseudo-terminal instead of this process's own stdio, so external serial-oriented tools -
`mpremote` chief among them - can drive the emulator exactly as they would a real board, with no
rp2040py-specific client needed for most commands (see `docs/mpremote.md`). `rp2040py mpremote
<args...>` (`cli/__init__.py`'s `_cmd_mpremote`) goes one step further for `mpremote` specifically:
a thin proxy that also monkeypatches around a real upstream `mpremote`/pySerial bug
(`mpremote.console.ConsolePosix.waitchar()` unconditionally reading a `.fd` attribute pySerial's
`socket://` backend never defines - filed at
https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170), so `mpremote`'s
own bare interactive REPL works over `--tcp-port` too, not just `--pty`.

Confirmed by reading upstream's `src/usb/cdc.ts` and the rest of `src/`/`demo/` directly: there is
no pty/socket-backed serial passthrough anywhere in rp2040js - `grep -rl "pty\|socket\|net\."`
across its source turns up nothing beyond its own GDB TCP server (`src/gdb/gdb-tcp-server.ts`,
unrelated to the USB-CDC console) and unrelated matches in PIO/FIFO/peripheral code. Every
rp2040js demo drives the emulated console through its own process's stdio only - there is no
built-in way for an external tool like `mpremote` to attach to it at all.
