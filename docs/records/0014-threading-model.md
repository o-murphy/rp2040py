# 0014. Threading model (Simulator.execute() / RPPIO.run())

- Status: Superseded by 0025 (full asyncio migration)
- Conceived: 2026-08-05 · #21
- Related: #21 · superseded-by 0025 · postmortem note 0018

<!-- migrated verbatim from docs/PORTING.md lines 259-361 -->

### Threading model (`Simulator.execute()` / `RPPIO.run()`)

**Superseded - this section described the pre-`asyncio` port; kept below for historical context
(the reasoning explains *why* the current design looks the way it does), but none of the advice
here reflects current code.** See
[docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md) for the full migration writeup.
Current shape, in short: `Simulator` owns one persistent background thread hosting a real
`asyncio` event loop (its "engine room"), created lazily on first use
(`Simulator._ensure_loop()`). `execute()` is `async def`, yielding via `await asyncio.sleep(0)`
between batches instead of rescheduling itself through a new OS thread every time
(`threading.Timer(0, self.execute)` is gone). Callers that used to call `simulator.execute()`
directly now call `simulator.start_execution()` (schedules `execute()` as a task on the engine
room and returns immediately) and `simulator.wait_for_shutdown()` to block until it's done - both
already used throughout `cli/__init__.py`; nothing outside this file needs the raw `threading`
patterns below anymore. Two bridge primitives make cross-thread calls into the engine room safe without bespoke locking
per caller: `Simulator.call(coro, timeout=None)` (blocking), `Simulator.submit(coro)`
(non-blocking, returns a `concurrent.futures.Future`) - a caller that already shares the engine
room's own loop (`Simulator.bind_loop()` - see docs/MAIN_THREAD_ASYNCIO_BACKLOG.md) just `await`s
directly instead, no bridge needed. `os._exit()`/raw `threading.Timer` scheduling from inside a
simulation callback (the old advice below) should no longer be necessary; use
`Simulator.shutdown_request.request(code)` and `simulator.clock.create_alarm(...)` instead, as the
old advice already recommended over the *other* raw-thread alternatives.

<details>
<summary>Original pre-<code>asyncio</code> analysis (historical)</summary>

Upstream JS yields back to Node's single-threaded event loop every N steps via
`setTimeout(() => this.execute(), 0)`, so an external `stop()` call (or the process exiting)
can interleave between bursts. Python has no equivalent single-threaded event loop, so this was
ported using `threading.Timer(0, self.execute)` instead - the closest analogue, but it introduces
**real concurrency** the JS version never had: every burst after the first runs on a new,
non-main thread.

Two concrete consequences, both already handled in the demo scripts but worth knowing if you
write new ones against `Simulator`:

- **Use `os._exit(code)`, not `sys.exit(code)`, to stop the process from inside a simulation
  callback** (GPIO listeners, `USBCDC.on_serial_data`, etc.). Those callbacks run on a
  `Simulator` worker thread once the first 1,000,000-step burst has completed, and
  `sys.exit()`/`SystemExit` only unwinds the thread that raised it - it will not terminate the
  process the way Node's `process.exit()` does. See `demo/micropython_run.py` and
  `tests/micropython_spi_run.py` for the pattern. **Prefer `Simulator.shutdown_request.request(code)`
  over a raw `os._exit()` where practical** (see `docs/BACKLOG.md`'s "Unified process-shutdown
  coordinator"): it flags the same cross-thread problem this bullet describes, but lets
  `Simulator.wait_for_shutdown()` - always running on the thread actually driving the simulator -
  do a real `sys.exit()` after running proper cleanup (terminal restore, `GDBTCPServer.close()`,
  etc.), instead of skipping straight past atexit/finally the way `os._exit()` does. `cli/__init__.py`
  uses this now; `demo/*.py`'s standalone scripts, listed below, still use the older
  `os._exit()`-direct pattern and haven't been migrated.
- **Wait on the main thread after calling `simulator.execute()`**, e.g.
  `while simulator.executing: time.sleep(0.1)` (or just call `simulator.wait_for_shutdown()`, which
  does exactly this). `execute()` only runs the first burst synchronously and then returns,
  rescheduling itself via a non-daemon `threading.Timer` so the process stays alive while the
  simulation runs (matching Node keeping the event loop alive). If `main()` returns without
  waiting, Python proceeds straight into interpreter shutdown, which blocks joining that non-daemon
  timer thread - and a Ctrl+C at that point produces an ugly `Exception ignored in: <module
  'threading'>` traceback instead of a clean exit. All four demo entry points
  (`demo/emulator_run.py`, `demo/micropython_run.py`, `demo/kaluma_run.py`,
  `tests/micropython_spi_run.py`) do this wait-then-`os._exit(130)`-on-`KeyboardInterrupt` dance.
- **Don't schedule follow-up work with `threading.Timer`/a real OS thread if it touches anything a
  `Simulator` worker thread also touches** (a FIFO, a peripheral register, `USBCDC.tx_fifo`, etc.)
  - use `simulator.clock.create_alarm(...)` instead, whose callback runs synchronously inside
    `Clock.tick()` on whichever thread is already driving the simulator. See the next section for
    a real bug this exact mistake caused.

Each `threading.Timer` handoff above is a real OS-thread creation, so how many of them a boot
needs matters, not just their existence. `execute()`'s inner loop bounds a batch to ~1,000,000
"units" before yielding via that handoff; an idle (`WFI`'d) core jumping straight to the next
clock alarm costs essentially nothing in real time no matter how far away that alarm is, so an
earlier version of this loop weighting that jump by the simulated nanoseconds it covered (instead
of counting it as ~1 unit, same as a real instruction) was a real bug, not just a style choice -
see `docs/BACKLOG.md`'s CDC performance investigation for the full writeup. USB SOF's 1ms recurring
alarm alone was enough to exhaust a whole batch after ~8 firings while idle, and a booted device is
idle almost all the time, so this turned "connected and waiting" into a thread handoff roughly
every 8ms of simulated idle time - the actual driver behind wildly variable `--expect-text`
wall-clock times, not anything USB-specific. Fixed by counting an idle jump as ~1 unit like
everything else, matching what `_bench_firmware`'s independent hand-rolled loop in
`cli/__init__.py` (which doesn't go through `Simulator.execute()`) already did.

That fix addresses *idle* runs specifically. A *busy* run (guest actively executing, not WFI'd -
e.g. MicroPython 1.28's resident-script loop) still pays for every handoff regardless: found while
investigating a report that `rp2040py.native` "shouldn't be 3x slower than expected" running real
guest code. A ~65M-step MicroPython 1.28 + littlefs boot needs ~65 `threading.Timer` handoffs at
the default 1,000,000-step batch size; a headless `rp2040py bench` run of the identical workload
(single tight loop, zero handoffs) finished in 24.95s against the CLI path's ~45s for the same
work - patching `execute()` to use one giant batch (no handoffs at all) brought the CLI path down
to 19.45s, actually beating the headless number. The yield-and-reschedule dance mirrors upstream
JS's `setTimeout(..., 0)`, necessary there because Node's event loop is single-threaded; CPython's
GIL already preemptively time-slices between real OS threads, so a tight loop on a background
thread doesn't starve the main thread the way single-threaded JS would - this port carried the
pattern over without needing it, and each handoff's cost (new thread creation, GIL contention
against the main thread's own periodic poll) was always there, just dwarfed by how slow pure-Python
instruction dispatch was until `rp2040py.native` made dispatch itself ~4x+ faster. See
`docs/BACKLOG.md`'s CDC investigation follow-up for the full numbers.

**This section used to end here with "not yet fixed."** It's fixed now, via the full `asyncio`
migration linked at the top of this section - `execute()`'s `threading.Timer` reschedule is gone,
replaced by the persistent-engine-room-thread design that section describes. That in turn
surfaced a *different* real-time cost specific to idle batches under the new model - see
`docs/BACKLOG.md`'s CDC section and CHANGELOG.md's `[Unreleased]` entry for that follow-up.

</details>

