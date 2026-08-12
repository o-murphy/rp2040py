# 0018. Note — Raw-REPL cross-thread USBCDC.tx_fifo access (postmortem)

- Status: Note (postmortem, fixed)
- Recorded: 2026-08-05
- Related: 0014 (threading model)

<!-- migrated verbatim from docs/PORTING.md lines 362-410 -->

### Raw-REPL uploads and cross-thread `USBCDC.tx_fifo` access (a real, previously undiscovered bug)

`device/raw_repl.py`'s `RawReplRunner.feed()` originally pushed an entire raw-REPL upload (the
whole `source` argument to `MicroPythonDevice.exec()`/`exec_file()`, or the CLI's
`micropython <filename>`) into `send_byte` - ultimately `USBCDC.send_serial_byte()` - in one
synchronous burst, the instant the raw-REPL banner arrived. `send_serial_byte()` just pushes into
`tx_fifo`, a fixed-size `FIFO` (`TX_FIFO_SIZE = 512` in `usb/cdc.py`) that silently drops anything
pushed once full (`FIFO.push()` in `utils/fifo.py` just no-ops past capacity - no exception, no
blocking). Any upload over ~512 bytes therefore lost everything past that point, *including the
terminating Ctrl-D* - the device was left waiting forever for an end-of-paste marker it had
already been "sent" but never actually received. Confirmed against real firmware, not assumed: a
440-byte script ran fine; an otherwise-identical 890-byte one hung indefinitely with zero output.
Real-world impact was large - `tests/test_bclibc.py` in
[ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc), a
perfectly ordinary ~13KB test file, silently hung forever under both CPython and PyPy.

`feed()` can't drain the FIFO itself mid-burst to make room: it's invoked synchronously from
*inside* the emulated CPU's own `execute_instruction()` call chain (the device writing to its USB
TX register), so nothing else runs - and no bytes actually get pulled from `tx_fifo` - until it
returns. Pacing has to happen *across* separate calls instead. `RawReplRunner` gained a `pump()`
method that sends only as much as an optional `free_space()` callback currently allows, returning
whether everything's out yet; call it again while it isn't.

The first fix attempt scheduled those repeat `pump()` calls with `threading.Timer` from
`MicroPythonDevice`'s `_exec_blocking()`. That "worked" in the sense that uploads no longer hung -
but intermittently corrupted them instead: a different `IndentationError`, then a `SyntaxError` on
an otherwise-untouched line, on repeated runs of the identical input. Root cause: a real `Timer`
fires its callback on its own OS thread, which raced `tx_fifo.push()` (from `pump()`, called via
the timer) against `tx_fifo.pull()` (from the emulated USB peripheral's own register-read path,
invoked deep inside `execute_instruction()` on whichever thread is driving the simulator) - `FIFO`
was never written to be thread-safe, and it's a hot enough path (used by every peripheral's FIFO
registers, not just CDC) that adding locking there for this one caller wasn't acceptable. The fix
that actually stuck: schedule `pump()` retries via `simulator.clock.create_alarm(...)` instead of
`threading.Timer`. An alarm's callback runs synchronously inside `Clock.tick()`, on whatever thread
already drives the simulator - the same thread `feed()`/`pull()` run on - so there's no second
thread to race in the first place. `MicroPythonDevice._exec_blocking()` now takes the device's
`Simulator.clock` for exactly this. Re-verified against the same real `test_bclibc.py` build,
repeatably clean.

The same unbounded-burst pattern existed in `micropython`'s interactive-mode stdin-forwarding loop
(`cli/stdio_repl.py`) and `demo/kaluma_run.py`'s: `os.read()` can return up to 4096 bytes in one
chunk from a single large paste into the terminal, comfortably over the 512-byte FIFO. **Updated
for the `asyncio` migration**: `StdioInteractiveRepl`'s `add_reader()` callback now runs directly
on `Simulator`'s own engine-room loop (not a separate stdin-reader thread - that design predates
the migration), so a plain blocking retry-with-sleep would stall the same loop `execute()` needs to
keep advancing. It uses the same `simulator.clock.create_alarm(...)`-based pacing this section's
`RawReplRunner` fix above already established (`_queue()`/`pump()`, re-armed via a clock alarm
instead of a blocking sleep) rather than reinventing a third mechanism.

