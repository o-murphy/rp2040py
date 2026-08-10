# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Full `asyncio` migration**: `Simulator` now owns one persistent background thread hosting a
  real `asyncio` event loop (its "engine room") instead of the ad hoc mix of `threading.Timer`
  reschedule chains, dedicated reader threads, and a `ThreadPoolExecutor` this replaces. `execute()`
  is now `async def`, yielding via `await asyncio.sleep(0)` between batches (upstream rp2040js's
  direct analogue of `setTimeout(fn, 0)`) instead of rescheduling itself through a brand-new OS
  thread every batch. `RPPIO.run()` (`peripherals/pio.py`) schedules its continuation as a task on
  the same loop instead of a `threading.Timer` + `RLock`. `StdioInteractiveRepl` forwards stdin via
  `loop.add_reader()` registered directly on `Simulator`'s own engine-room loop rather than a
  separate reader thread, so `send_serial_byte()` is a plain synchronous call with no cross-thread
  bridge needed per keystroke. `GDBTCPServer` is rewritten on `asyncio.start_server()` with its own
  independent engine room (connection I/O doesn't touch CPU/peripheral state, so it doesn't share
  `Simulator`'s), bridging only `process_gdb_message()` per received chunk via
  `Simulator.acall()`/a new `IGDBTarget.acall()` Protocol method. `MicroPythonDevice`'s
  boot/`exec()` queueing moved from `ThreadPoolExecutor(max_workers=1)` to `Simulator.submit()` (new
  - `call()`'s non-blocking counterpart, returning the same `concurrent.futures.Future` type the
  executor did) + one `asyncio.Lock` per device. Three new bridge primitives on `Simulator` make
  every one of the above possible without bespoke locking per component: `call(coro, timeout=None)`
  (blocking), `acall(coro)` (async), `submit(coro)` (non-blocking, returns a `Future`) - all reused
  as-is by every migrated component, none needed changes to add the next one. Closes several real,
  independently-found `USBCDC.tx_fifo` races between whatever thread was driving `execute()` and a
  separate thread touching CDC state directly (interactive stdin, GDB's `process_gdb_message()`) -
  the same class of bug that once corrupted a raw-REPL upload
  (see [docs/ASYNCIO_MIGRATION_BACKLOG.md](docs/ASYNCIO_MIGRATION_BACKLOG.md) for the full,
  phase-by-phase writeup and every race found along the way). No public sync API changed shape:
  `Simulator.submit()`/`call()` return/accept the same types their threading predecessors did, and
  `cli/__init__.py` needed zero changes.
- `GDBTCPServer.close()`: stops and joins its engine-room thread so the process can actually exit.
  That thread is deliberately non-daemon ("a listening GDB server should keep the process alive by
  itself", matching Node's `net.Server.listen()`), which is exactly why nothing previously called a
  plain `sys.exit()`/return while `--gdb` was active - every exit path used `os._exit()` instead,
  which works but skips the terminal restore and other cleanup below.
- `Simulator.shutdown_request` / `Simulator.wait_for_shutdown(cleanup=...)`: a shared, thread-safe
  way for a REPL's Ctrl+X handler, a `--expect-text` watcher, a SIGTERM handler, or (later) a `--pty`
  disconnect handler to request a clean process exit instead of calling `os._exit()` itself.
  `os._exit()` works but unconditionally skips atexit/finally/normal cleanup wherever it's used;
  `wait_for_shutdown` - always running on the thread actually driving the `Simulator` - is the only
  thing that acts on a request, via one real `sys.exit()` after its own `cleanup` callback (composed
  per-caller via `contextlib.ExitStack` in `cli/__init__.py` - terminal restore, GDB server socket,
  device teardown) has run.
- Optional Cython-accelerated backend (`rp2040py.native`): a fully-typed port of `CortexM0Core`'s
  ~90 instruction handlers (real C function-pointer dispatch, not a Python-level table) and
  `RP2040`'s bus hot paths (`read`/`write_uint8/16/32`). Built automatically when a C compiler is
  available - falls back transparently to the existing pure-Python implementation otherwise, with
  identical behavior either way. Measured ~4x instruction throughput on both a synthetic benchmark
  and a real MicroPython 1.21 boot; see
  [docs/BACKLOG.md](docs/BACKLOG.md#cython-port-of-the-interpreter-core--implemented-on-by-default-real-world-win-confirmed-4x)
  for the full writeup and [README.md#performance](README.md#performance) for the short version.
- `RP2040PY_SKIP_CYTHON=1` (runtime) and `RP2040PY_SKIP_NATIVE_BUILD=1` (build-time) env vars to
  opt out of the native backend explicitly.
- `cp310-abi3` stable-ABI wheels: one compiled wheel covers every CPython 3.10+ interpreter instead
  of one per minor version (built via `cibuildwheel`, `Py_LIMITED_API`). Free-threaded builds and
  PyPy get a normal, version-specific build (abi3 and free-threading are mutually incompatible;
  PyPy's `cpyext` C-API emulation is slower than PyPy's own JIT for this kind of code, so
  compilation is skipped there rather than attempted).
- `--log-level {debug,info,warning,error}` on every subcommand: one flag now controls both this
  CLI's own progress/error messages (stdlib `logging`, replacing what used to be raw `print()`
  calls throughout `cli/__init__.py`) and the emulator's internal component logger
  (`rp2040.logger`/`ConsoleLogger`, previously only settable per call site, hardcoded). Left unset,
  both keep their existing defaults (progress messages at info, component logger at error) - no
  behavior change unless the flag is actually passed.
- `check_flash_image_size()` (`device/load_flash.py`): validates a `--littlefs`/`--fat12` image is
  exactly `block_size * block_count` bytes before loading it, raising a clear error instead of
  silently loading a truncated filesystem (image smaller than expected) or overrunning past the end
  of the flash region into whatever comes after it (image larger than expected - e.g. Kaluma's
  128-block littlefs image loaded where MicroPython's 352-block one is expected) - the loader had no
  bounds check of its own before this.
- `--dump-fs <path>` on `micropython`/`kaluma`: dumps the device's filesystem flash region (littlefs
  for MicroPython/Kaluma, FAT12 for CircuitPython) back out to a local file when the subcommand
  exits - Ctrl+X, `--expect-text` firing, or the end of a `-c`/`-m`/script run. `BaseDevice.
  dump_flash_image()` (`NotImplementedError` in the base class, overridden per device) plus
  `dump_micropython_flash_image()`/`dump_circuitpython_flash_image()`/`dump_kaluma_flash_image()`
  (`device/load_flash.py`) are the mirror image of the existing `load_*_flash_image()` functions -
  same flash regions/block layouts, opposite direction. Can point at the same path as `--littlefs`/
  `--fat12` for read-modify-write persistence across runs, or at a fresh path to capture whatever
  filesystem state a run produced. Doubles as a `littlefs-python`-free way to build a littlefs
  image in the first place: boot against blank flash, write files to it the normal way from device
  code, and dump the result - see README.md's "Filesystem support" section and the new
  `demo/mklittlefs_dump.py` below.
- `demo/mklittlefs_dump.py`: generates a raw-REPL script that writes a list of local files into
  MicroPython's filesystem via plain `open()`/`write()` calls (mirroring `mklittlefs`'s own
  basename/`--main`/collision handling, without needing `littlefs-python` to do it) - for use as
  the positional `<filename>` argument to `micropython --dump-fs <path> <script>`. Lets the actual
  on-device littlefs (whatever a given firmware bundles) build the image instead of a
  separately-installed host library.
- CI: a "flash dump is deterministic" check (`scripts/ci-common.sh`'s `run_micropython_dump_test()`,
  wired into `.github/workflows/ci-micropython.yml`) boots MicroPython against blank flash with
  `--dump-fs`, then boots again with that dump loaded via `--littlefs` and dumped again, asserting
  the two dumps are byte-identical - a regression test for `--dump-fs`/`--littlefs` round-tripping
  without silently drifting (e.g. reformatting instead of mounting cleanly).
- `--tcp-port <port>` on `micropython`/`kaluma`: serves the device's USB-CDC console over a plain
  TCP socket instead of this process's own stdio, for tools that expect a serial port but can't
  open one - notably `mpremote` in a sandboxed environment with no serial support at all (e.g.
  Pythonista). `cli/socket_repl.py`'s new `SocketInteractiveRepl` (an `InteractiveRepl`, alongside
  `StdioInteractiveRepl`) runs its `asyncio.start_server()` on the device's own engine-room loop -
  same requirement as `StdioInteractiveRepl`'s `add_reader()` - and needs no client-side patching:
  pySerial's own `socket://host:port` URL support (which `mpremote`'s `SerialTransport` already
  uses via `serial.serial_for_url()`) is a raw byte pipe with nothing layered on top, so `mpremote
  connect socket://host:port` talks directly to it. Serves one client at a time, matching a real
  serial port's exclusive-access semantics; unlike `StdioInteractiveRepl`, no byte is reserved as a
  quit signal (a real client's own protocol, e.g. raw-REPL's Ctrl-A/Ctrl-C/Ctrl-D, owns this byte
  stream) - quit the `rp2040py` process itself instead. Mutually exclusive with `micropython`'s
  `-c`/`-m`/`<filename>`. See README.md's new "mpremote" section.
  Ctrl+C is free (nothing here puts the real terminal in raw mode, so
  `Simulator.wait_for_shutdown()`'s own `KeyboardInterrupt` handling already covers it), but SIGTERM
  needed its own explicit handler - confirmed the hard way (`kill <pid>` on an early build exited
  the process at code 143 with `--dump-fs`'s cleanup callback never having run at all): Python's
  default SIGTERM disposition is immediate OS-level termination, bypassing every `finally`/context-
  manager exit in the interpreter, the same behavior `StdioInteractiveRepl`'s own SIGTERM handler
  exists to work around. `SocketInteractiveRepl` now takes the same `on_quit` constructor argument
  and installs/restores a `SIGTERM` handler around `start()`/`stop()`, mirroring
  `StdioInteractiveRepl`'s handler exactly - `--dump-fs` (and everything else `on_quit` gates) now
  actually runs on a plain `kill`, verified against real MicroPython firmware, not just
  `--expect-text`/a client disconnect.
- A connection already dead on arrival (its peer closed before, or while, being accepted) could
  wrongly cause a second, genuinely live connection landing in the same window to be rejected -
  `self._client_writer` stays set until the dying connection's own handler task actually finishes
  (its `reader.read()` resolving to EOF, then its own `finally` clearing it), which needs at least
  one more event-loop iteration and isn't bounded to any fixed number of them (confirmed: an
  earlier fix retrying a bounded number of `await asyncio.sleep(0)` yields still occasionally
  wasn't enough on a slower CI runner). Fixed by `await`ing the previous connection's actual task
  instead (shielded, with a generous timeout, so a genuinely-still-active connection isn't
  cancelled by it) - resolves the instant that task truly finishes rather than guessing a count.
- `mpremote` as a `dev` dependency group member, and `tests/test_mpremote_integration.py`: drives a
  real `mpremote` subprocess against `SocketInteractiveRepl` over an actual `socket://` connection
  (a scripted fake raw-REPL device stands in for real firmware, which needs a network download this
  environment's CI can't always assume - see the test module's own docstring), verifying `mpremote
  exec`/`mpremote fs cp` round-trip correctly through the new transport with zero pySerial/mpremote
  patching. `exec`/`fs cp`/`mount` (including running a script straight out of a mounted local
  directory) have also all been verified by hand against real MicroPython 1.21.0/1.28.0 firmware
  over this same transport.

### Changed
- `StdioInteractiveRepl(cdc, simulator, on_quit=...)` - `simulator` is now a required constructor
  argument (**breaking**, on top of the `on_quit` change below). Needed so stdin forwarding can
  register `loop.add_reader()` on `Simulator`'s own engine-room loop (see "Full `asyncio` migration"
  above) - the one-time registration itself goes through `simulator.call()`, everything after that
  runs directly on the right thread with no further bridging.
- `StdioInteractiveRepl(on_quit=...)` is now a required constructor argument (**breaking** for any
  caller constructing it without one) - collapses the class down to a single shutdown mode instead
  of two. It previously also supported a standalone mode (`on_quit=None`) where Ctrl+X/SIGTERM
  called `self.stop()` and `os._exit()` directly from whichever thread noticed; nothing in this
  repo's own CLI ever used that mode (`_cmd_micropython`/`_cmd_kaluma` always pass
  `on_quit=shutdown.request`), and it was the source of several follow-up bugs - a "don't join
  myself" special case needed only because standalone Ctrl+X called `stop()` from inside the
  stdin-reader thread itself, a module-global `_active_raw_repl` + exported `os_exit()` function
  (removed, along with its `"Pythonista3.app"`/`"Python IDE.app"` special-case) that existed purely
  so a raw `os._exit()` call could still find a terminal to restore, and a real fd-reuse race
  between sequential test runs (see Fixed, below). Every quit trigger now only ever calls
  `on_quit(code)` - never `sys.exit()`/`os._exit()` itself - leaving exactly one place in the
  codebase responsible for actually exiting the process: whatever thread drives the caller's own
  `on_quit`-consuming loop (`Simulator.wait_for_shutdown`, for every current caller).

### Fixed
- `tests/test_simulator.py::test_idle_core_advances_far_past_a_single_recurring_alarm_period_in_one_batch`
  was flaky on CI: it asserts a WFI'd core's idle alarm fires more than a hardcoded floor
  (1000/200 for 64/32-bit) within one `_execute_batch()` call, but that batch is itself bounded by
  a *real* wall-clock budget (`_BATCH_YIELD_BUDGET_SECONDS`, checked via `time.monotonic()`) - so
  how many idle iterations fit before the batch cuts itself off depends on the runner's CPU
  speed/load, not on the correctness this test actually checks (an idle jump costs ~1 iteration,
  not `nanos_jumped / cycle_nanos`). Confirmed failing for real on a GitHub-hosted CI runner (767
  firings, under the 1000 floor) despite no actual regression. Fixed by faking `time.monotonic()`
  to advance a fixed amount per call instead of tracking real elapsed time, so the number of idle
  iterations that fit before the budget trips is deterministic regardless of host speed - removes
  the CI-runner-speed dependency entirely rather than just loosening the threshold.
- Typing at the interactive REPL while the device sat idle (the common case, once booted) could
  take up to ~1-2 real seconds per keystroke to even reach the emulated device - a regression from
  the `asyncio` migration above, not present before it. Root cause:
  `Simulator._execute_batch()`'s idle branch could legitimately run its full 1,000,000-iteration
  ceiling before yielding back to the event loop, and `StdioInteractiveRepl`'s `add_reader()`
  callback (sharing that same loop) only gets a turn between batches - upstream rp2040js hits the
  identical 1,000,000-iteration ceiling per batch, but V8 clears it in low milliseconds; CPython's
  per-iteration overhead measured ~0.9-1.8s wall-clock for the same ceiling. A first fix attempt
  bounded only an *uninterrupted* idle run's own elapsed time and shipped with no measured
  real-world improvement - a real device idling at the REPL isn't purely WFI'd end to end (a
  periodic timer interrupt briefly wakes it before it goes back to waiting), and that reset the
  idle-run tracker on every such interruption, so the bound rarely actually fired. Fixed by tracking
  one wall-clock budget (`_BATCH_YIELD_BUDGET_SECONDS`, 5ms) from the start of the whole batch,
  unconditionally, checked every `_TIME_CHECK_INTERVAL` iterations rather than every one so
  `time.monotonic()` itself doesn't become the hot-path cost this avoids. Verified against a real
  pty-driven MicroPython session with a simulated periodic interrupt: ~1.6-1.8s per keystroke before
  the second fix, ~0.01-0.05s after - matching the pre-`asyncio` threading model's own latency, with
  no measured change to boot-to-prompt throughput (idle time is still uncapped in simulated units;
  only one uninterrupted batch's real-world length is bounded).
- `GDBTCPServer.close()` could hang the whole process indefinitely (confirmed on real CI: one
  wheel-build job sat for ~6 hours before GitHub's own outer timeout force-cancelled it, on a
  macOS + free-threaded-Python runner). `_aclose()`'s `await self._server.wait_closed()`/
  `gather(*self._connection_tasks)` could stall past `close()`'s own `timeout`, and
  `run_coroutine_threadsafe(...).result(timeout)` raising `TimeoutError` in that case used to skip
  `loop.stop()`/`_loop_thread.join()` entirely - leaving that thread (deliberately **non-daemon**,
  so a listening GDB server keeps the process alive by itself) running forever. `close()` now stops
  the loop and joins the thread unconditionally, even if `_aclose()` times out or raises; `_aclose()`
  itself also bounds each of its two stages to its own slice of the timeout instead of one
  unbounded `await` each, so a single stuck connection no longer consumes the entire budget and
  leaves nothing for the rest of cleanup.
- `test_stdio_repl.py`'s `_stable()` helper (used by the SIGTERM/termios regression test) compared
  the *entire* raw `c_cc` array from `tcgetattr()`, including slots past the last named control
  character - confirmed flaky specifically inside cibuildwheel's containerized Linux runners (two
  `tcgetattr()` snapshots of the same untouched pty differed in that unused tail, looking like
  uninitialized/padding memory rather than real terminal state; not reproducible outside that
  container). `_stable()` now narrows its `c_cc` comparison to the named `V*` control-char indices
  only.
- `StdioInteractiveRepl`'s stdin-reader thread wasn't joined on `stop()` - it stayed blocked in
  `os.read()` until its fd eventually went away, so its own `finally`-triggered terminal restore
  could fire *after* a later `StdioInteractiveRepl` instance (e.g. the next test in the same
  process) had already opened a new pty that reused the same fd number, corrupting that instance's
  termios state instead of its own. Fixed with a self-pipe the reader thread also selects on, so
  `stop()` can wake and join it deterministically before returning - closing the target fd itself to
  cancel the pending read was considered and rejected as no safer (another thread's fd reuse could
  get redirected onto it mid-syscall, the same failure mode this fixes).
- A plain `SIGTERM` (e.g. from `timeout`, or `kill` without `-9`) while `micropython`/`kaluma`'s
  interactive REPL had the terminal in raw mode left the real terminal stuck raw after the process
  died - no echo, no line buffering, looking like "the keyboard stopped working" until `stty sane`.
  The existing `atexit.register()`-based restore doesn't cover this: confirmed empirically that
  Python's default `SIGTERM` disposition kills the process at the OS level before the interpreter
  ever runs atexit callbacks, regardless of what registered one. Fixed by giving
  `StdioInteractiveRepl` its own `SIGTERM` handler (installed only when raw mode was actually
  engaged, restoring whatever handler was there before on `stop()`) routed through the same
  `on_quit`/`Simulator.shutdown_request` path as Ctrl+X and `--expect-text` (see
  `Simulator.wait_for_shutdown` above) - verified end to end against a real subprocess under a pty
  (boot, `SIGTERM` mid-session, confirm the terminal's actual termios state afterward), not just
  unit tests against the handler in isolation.
- `_restore_termios()`'s `except OSError` didn't actually catch `termios.error` - confirmed its MRO
  is just `(termios.error, Exception, BaseException, object)`, not an `OSError` subclass - so a
  legitimately-gone fd (e.g. the pty's other end already closed) could raise out of an `atexit`
  callback uncaught. Now catches both.
- `--littlefs`/`--fat12` on `micropython`/`kaluma`/`bench` no longer default to `littlefs.img`/
  `fat12.img` in the current directory - a stray leftover image from an earlier step (e.g. a shared
  CI working directory) used to get auto-loaded whenever one of those exact filenames happened to
  exist, regardless of whether the running command actually wanted a filesystem at all, sometimes
  hanging a boot that expected a clean image. Now only loaded when `--littlefs`/`--fat12` is passed
  explicitly.
- `firmware_retrieve.retrieve()`'s download had no timeout at all (`urllib.request.urlretrieve`
  doesn't accept one) - a stuck connection (server never responds, or goes silent mid-transfer)
  hung indefinitely with no feedback outside of CI's own outer `timeout` wrapper. Rewritten around
  `urlopen(url, timeout=30)` (per socket operation - connect and each read - not the download as a
  whole, so a slow-but-progressing transfer is unaffected) with manual chunked streaming; a partial
  file left behind by a download that dies mid-transfer is now cleaned up so a retry re-downloads
  instead of finding a corrupt "cached" image.

## [0.1.0] - 2026-08-04

### Fixed
- `RP2040.write_uint32()` checked `find_peripheral()` (a dict lookup) unconditionally before any
  other address range, unlike `read_uint32()`/`write_uint8()`/`write_uint16()`, which all check
  cheap RAM/flash/bootrom range comparisons first. Every 32-bit write to RAM - the overwhelmingly
  common case (stack spills, GC, locals) - paid for a peripheral-dict lookup that was always going
  to miss. Reordered to match `read_uint32()`'s range order. Found while profiling why ~23-28% of
  all instructions executed during a MicroPython boot go through USB-interrupt-adjacent code
  (`cProfile` on a real boot showed `find_peripheral()` called on almost every `write_uint32()`
  call, 1:1). Measured ~7-8% higher and less variable instructions/sec on a real MicroPython 1.21
  boot-to-first-print benchmark; full test suite green, no behavior change for actual peripheral
  writes.

## [0.1.0rc2] - 2026-08-03

### Added
- Real JEDEC SPI-NOR flash command emulation in `RPSSI` (`WREN`/`WRDI`, `RDSR1`/`RDSR2`, `WRSR`,
  `PAGE_PROGRAM`, `SECTOR_ERASE`, `BLOCK_ERASE`, `READ_DATA`, `READ_JEDEC_ID`), so the filesystem
  is now actually writeable - MicroPython's `os`/`rp2.Flash` can format, write, and read back files
  through it, not just read a pre-built image. Verified end to end against real MicroPython
  firmware (write a file, read it back, over a `--littlefs` image) as well as the existing
  `ci-micropython.yml` matrix (8 versions × 3 Python runtimes, all green).
- `tests/micropython/main-flash-rw.py` + a new `ci-micropython.yml` step exercising it: writes a
  file to the auto-mounted littlefs filesystem and reads it back, confirming the flash-write path
  above end to end (alongside the existing plain-boot and SPI0 tests).
- `mklittlefs --target {micropython,circuitpython,kaluma}`: presets `--block-size`/`--block-count`
  to a known firmware's own filesystem layout instead of spelling them out by hand (mutually
  exclusive with passing them explicitly - errors if both are given). Omitting both keeps today's
  default (MicroPython's `4096`/`352`).
- `tests/kaluma/index-flash-rw.js` + a new `ci-kaluma.yml` step exercising it: mounts a
  `--target kaluma`-sized littlefs image and writes/reads a file through Kaluma's own
  `require("fs")` (a different flash region/filesystem than MicroPython's), confirming the same
  flash-write path from Kaluma's side too.

### Fixed
- Two bugs in `RPSSI` that hung real boots before reaching the REPL, both surfaced while building
  the flash-write support above: `_cs_asserted` desynced from `QSPI_SS`'s real reset-time value
  (the first-ever chip-select assertion after reset went unnoticed, dropping every byte of that
  command); and `DR0` writes made while chip-select was deasserted were dropped entirely, though
  `flash_exit_xip()`'s dummy-clock compatibility sequence deliberately clocks bytes through `DR0`
  in that exact state (real SSI FIFO hardware doesn't gate on the `QSPI_SS` GPIO pin). Either one
  alone starved the bootrom's flash-command FIFO-drain loop of bytes it was waiting for, hanging it
  forever. See `docs/BACKLOG.md` for the full root-cause writeup.
- `Simulator.execute()` weighting an idle (`WFI`'d) core's jump-to-next-alarm by the simulated
  nanoseconds it covered, instead of its actual (near-zero) real cost - USB SOF's 1ms recurring
  alarm alone was enough to exhaust a whole execution batch (forcing a real `threading.Timer`
  OS-thread handoff) after only ~8 firings, turning "device connected and idle" into thousands of
  avoidable thread handoffs - each exposed to real OS-scheduler jitter - over a typical boot-to-REPL
  wait. This was the main driver behind the wildly variable `--expect-text` wall-clock times noted
  in `docs/BACKLOG.md`'s CDC investigation. See `docs/PORTING.md`'s "Threading model" section and
  `tests/test_simulator.py` for the fix and a regression test.

## [0.1.0rc1] - 2026-08-03

### Added
- `--bootrom <tag|path>` on `run`/`micropython`/`kaluma`/`bench`: boot a `b0`/`b1`/`b2` revision
  from [Raspberry Pi's `pico-bootrom-rp2040`
  releases](https://github.com/raspberrypi/pico-bootrom-rp2040/releases) (downloaded and cached
  the same way `--image` already resolves a firmware tag, via `firmware_retrieve.retrieve()`), or
  a local `.elf`/`.bin` path - instead of only the bundled `BOOTROM_B1`. Closes #11. `pyelftools`
  (parses the `PT_LOAD` segment real bootrom releases ship as `.elf`, no plain `.bin` published) is
  now a normal dependency rather than dev-only - it's a pure-Python wheel with no
  platform-specific build, unlike `littlefs-python`'s `fs` extra, so there's no packaging reason to
  gate it. B0/B2 verified booting MicroPython 1.21 to the REPL cleanly before building this
  (349,875 / 349,728 steps vs. B1's 349,642 - issue #11 flagged this as unconfirmed).

### Changed
- `firmware_retrieve.retrieve()` (used by `--image` on `micropython`/`circuitpython`/`kaluma` and
  the new `--bootrom` above) now caches downloads in `~/.cache/rp2040py` instead of the current
  directory, so e.g. `--image 1.21.0` doesn't re-download the same UF2 into every project checkout
  separately. Falls back to the old cwd-based behavior (with a warning) if the cache directory
  can't be created for any reason - no `HOME`, a read-only filesystem, a sandboxed environment.
  A local path passed directly (not a tag) is unaffected either way - still resolved relative to
  the current directory, exactly as before.

### Fixed
- `firmware_retrieve._resolve_version()`'s short-tag matching (e.g. `--image 1.19` resolving to
  MicroPython's `1.19.1` dated slug) used a raw string prefix (`known_tag.startswith(tag)`), which
  silently matched semantically unrelated versions sharing a digit prefix - `"1.2"` matched
  `"1.20.0"`/`"1.21.0"`/`"1.28.0"` alike (none of which are actually `1.2.x`), resolving to
  whichever happened to come first in `known_versions`' key order rather than raising or picking
  the intended one. Now uses `semver.Version.parse(..., optional_minor_and_patch=True)` (new
  dependency) to compare real `(major, minor, patch)` components truncated to how many the tag
  itself specified, so `"1.2"` correctly matches nothing (falls back to using it as the raw version
  suffix) while `"1.19"` still resolves to `1.19.1`, and an ambiguous bare-major tag like `"1"`
  picks the highest real match by semver precedence instead of key order.
- `rp2040py micropython`/`kaluma`'s interactive REPL could leave the real terminal stuck in raw
  mode (no echo, no line buffering - looks like "the keyboard stopped working") if the process
  exited any way other than a clean `stop()`: `os._exit()` (used by both the Ctrl+X quit path and
  `--expect-text` matching) skips atexit callbacks entirely, and an external kill (a hung boot,
  `timeout`, SIGTERM) skipped the restore path too. Now restored via a module-level "active raw
  terminal" registry `os_exit()` itself checks, plus an `atexit` handler for exits that don't go
  through `os_exit()` at all.

### Performance
- `CortexM0Core.registers` and `RP2040.bootrom` are now plain `list[int]` instead of
  `Uint32Array` - see
  [docs/PORTING.md](docs/PORTING.md#performance-pure-python-interpretation-is-much-slower-than-v8)
  for the full writeup. Combined, ~16% faster real MicroPython 1.28 + littlefs boot-and-run under
  CPython 3.10 (224.79s -> 188.98s), ~16% higher synthetic instructions/sec; PyPy unaffected (its
  JIT already optimized the old indirection away). `Uint32Array` itself (`utils/bit.py`) had no
  remaining callers after both changes and was removed.

## [0.1.0b6] - 2026-08-03

### Added
- `kaluma` subcommand: runs [Kaluma](https://kaluma.io/) (a JavaScript runtime for RP2040)
  UF2 images, interactive REPL only - Kaluma has no raw-REPL-equivalent protocol, so unlike
  `micropython` there's no `-c`/`-m`/`<filename>`. Missing firmware is downloaded automatically
  (`rp2040py.cli.firmware_retrieve`, defaulting to `1.2.1` - the newest release still shipping a
  plain, non-`-w`, RP2040 `pico` build). `demo/kaluma_run.py` is now a thin wrapper around it
  (`--image` is now optional there too), matching `demo/micropython_run.py`. `--expect-text` works
  the same way it does for `micropython` (watches serial output for a substring, exits 0 once
  found). Unlike `micropython`, `kaluma` sends nothing proactively after connecting - Kaluma's own
  one-time boot banner is racy regardless (gone by the time the emulated USB-CDC connection is
  actually up, same as real hardware racing a host terminal that isn't already attached yet -
  Kaluma's own docs: "if you cannot see the prompt, press Enter several times"; `.hi` reliably
  reprints it on demand if you need to see it), while a staged `<script.js>`'s own output isn't
  racy and needs no nudge at all (see below).
- `ci-kaluma.yml`: boots real Kaluma 1.2.1 firmware end to end (across the same `python_runtime`
  matrix as `ci-micropython.yml`), stages `tests/kaluma/index.js` into the "user program" flash
  region (see `kaluma <script.js>` below) and checks for its `console.log()` output via
  `--expect-text` - a genuine code-execution test, not just a boot check, now that auto-run is
  confirmed working end to end.
- Kaluma littlefs filesystem support: `--littlefs` on the `kaluma` subcommand
  (`rp2040py.device.load_flash.load_kaluma_flash_image`), mounted at the same flash region
  (`0x180000`, 4096-byte blocks, 128 blocks) Kaluma's own `pico`/`pico-w` `board.js` uses
  (`new Flash(132, 128)`) - confirmed by reading kaluma-project/kaluma's source directly. Build a
  compatible image with `rp2040py mklittlefs --block-size 4096 --block-count 128`.
- `rp2040py.device.base_device.BaseDevice`: the UF2-boot lifecycle (load image, create the
  USB-CDC console, block `start()`/`stop()` around actually running the emulator) shared by
  `MicroPythonDevice` and the new `KalumaDevice`, instead of each hand-rolling it.
- `kaluma <script.js>`: stages a local `.js` file into Kaluma's "user program" flash region
  (`rp2040py.device.load_flash.load_kaluma_program`, `KalumaDevice(program=...)`) before boot -
  the same flash region (offset `0x100000`, 512K, raw source + a `\0` terminator - no ELF/YMODEM
  framing) `kaluma flash <file>` writes to on real hardware, confirmed by reading
  kaluma-project/kaluma's `src/prog.c`/`src/runtime.c` directly, and auto-executed on every boot
  (see the GPIO pull-up/pull-down fix below - needed for this to actually run). Verified end to end
  manually and via `ci-kaluma.yml`. Unlike the one-time boot banner above, the auto-run program's
  own output isn't racy - it arrives on its own without needing a nudge, just takes a few real
  seconds after connecting (JerryScript engine init + running the script, same "real firmware boot
  takes real wall-clock time under an interpreted emulator" story as MicroPython's own boot time).
- `mklittlefs -f`/`--force`: required to overwrite an existing `--output` path, and `files` may
  now be empty (producing a freshly formatted, empty image) - see below.

### Changed
- **Breaking:** `mklittlefs`'s output image path is now `-o`/`--output <path>` (defaults to
  `littlefs.img`, matching `micropython --littlefs`'s own default) instead of a required
  positional argument - `files` is now the first positional instead of the second. Lets
  `rp2040py mklittlefs your_main.py --main your_main.py` work without also having to spell out
  `littlefs.img` explicitly, since that's the same default `micropython` already looks for.
- **Breaking:** `mklittlefs` no longer opens an existing `--output` and updates it in place -
  it now always builds a fresh image from scratch, and refuses to overwrite an existing file
  unless `-f`/`--force` is given (raising a clear error instead). The old "update in place"
  behavior silently trusted whatever `--block-size`/`--block-count` built the existing file,
  regardless of what was passed on this run - reusing an output path with different values (e.g.
  MicroPython's default block count vs Kaluma's) produced a corrupted or wrong-sized image with no
  warning; confirmed reproducible even against a validly-built image, not just a stale/foreign
  one. There's no way to recover the previous "merge new files into an existing image" behavior -
  rebuild from the full file list instead.

### Fixed
- `micropython --circuitpython --image v8.0.2` (a `v`-prefixed version tag) silently 404'd instead
  of downloading - CircuitPython's resolution path never stripped the `v`, unlike MicroPython's
  (dead code: `mp_retrieve.py`'s `is_circuitpython` branch skipped the very function that did the
  stripping). Fixed as part of unifying firmware retrieval below, which no longer has a
  CircuitPython-specific code path to skip it.

### Internal
- `RawReplRunner`'s FIFO-backpressure/threading plumbing (`pump()`/queueing, `cdc.on_serial_data`
  wiring) is now shared with the CLI's interactive stdin forwarding and `demo/kaluma_run.py`
  through a new `BaseReplRunner`/`InteractiveRepl` base (`device/repl_runner.py`) and
  `StdioInteractiveRepl` (`cli/stdio_repl.py`), instead of three separate hand-rolled copies of the
  same backpressure loop. No behavior change for CLI users.
- `cli/mp_retrieve.py` and `cli/kaluma_retrieve.py` merged into `cli/firmware_retrieve.py`: one
  declarative `FirmwareSpec` per firmware (filename/URL templates, default tag, optional
  known-version-tag table), loaded from `cli/firmware_specs.json`, plus a single generic
  `retrieve(spec, image)` instead of three near-duplicate implementations. Per-firmware data now
  lives in JSON rather than mixed into the retrieval logic as Python literals - adding a new
  MicroPython release to `known_versions` or bumping a default tag is a plain data edit. No
  behavior change for CLI users beyond the CircuitPython fix above.

## [0.1.0b5] - 2026-07-31

### Added
- `demo/kaluma_run.py`, a generic USB-CDC REPL runner for firmware other than
  MicroPython/CircuitPython - talks to `USBCDC` directly rather than wrapping an `rp2040py.cli`
  subcommand (unlike the other `demo/*.py` scripts), demonstrating that the USB/CDC emulation
  itself isn't MicroPython-specific. Verified against [Kaluma](https://kaluma.io/) 1.2.1: boots,
  USB enumerates, and evaluates real JS at its REPL prompt.

### Fixed
- **Raw-REPL code uploads (`micropython <filename>`, `MicroPythonDevice.exec()`/`exec_file()`)
  silently hung forever on any source over ~512 bytes**, with zero output - a real, previously
  undiscovered bug, not a throughput/timeout issue. `RawReplRunner.feed()` used to push the entire
  source into the device's USB-CDC receive FIFO (`TX_FIFO_SIZE = 512` in `usb/cdc.py`) in one
  synchronous burst; that FIFO silently drops pushes once full instead of raising or blocking, so
  anything past ~512 bytes - including the terminating Ctrl-D - was lost, leaving the device
  waiting forever for an end-of-paste marker it had already been sent but never actually received.
  Confirmed against real firmware: a 440-byte script ran fine, an otherwise-identical 890-byte one
  hung indefinitely. `RawReplRunner` now paces uploads via a new `pump()` method that only ever
  sends what currently fits, retried until the whole payload's out; `MicroPythonDevice` schedules
  those retries through the simulated clock (`Clock.create_alarm()`), not a real
  `threading.Timer` - the latter's callback runs on its own OS thread, racing `USBCDC.tx_fifo`
  against whatever thread is driving the simulator (`pull()` happens deep in the emulated USB
  peripheral's own read path, mid-instruction-execution) and intermittently corrupting uploads
  (confirmed the hard way: a different `IndentationError`/`SyntaxError` almost every run, same
  input) - `FIFO`/`USBCDC` were never meant to be thread-safe, and adding locking there wasn't an
  option (it's a hot path used everywhere in peripheral emulation). An alarm callback instead runs
  synchronously inside `Clock.tick()`, on whichever thread already drives the simulator - same
  thread `feed()`/`pull()` run on, no race. Verified end to end against a real natmod build
  ([ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc)'s
  ~13KB `tests/test_bclibc.py`, previously hanging indefinitely under both CPython and PyPy) -
  passes cleanly and repeatably now.
- The same unbounded-burst pattern in `micropython`'s interactive-mode stdin forwarding (and
  `demo/kaluma_run.py`'s) could hit the same FIFO-overflow silent-drop for a single large paste
  into the terminal (`os.read()` can return up to 4096 bytes in one chunk - well over the 512-byte
  FIFO). Both now back off and retry while the FIFO's full, rather than assuming
  `send_serial_byte()` always has room. This path runs on its own dedicated stdin-reader thread,
  not the simulator's, so a plain blocking retry (no clock-alarm scheduling needed) is safe here.

## [0.1.0b4] - 2026-07-31

### Changed
- **Breaking:** `mklittlefs` no longer auto-picks the first file as `main.py` - every file now
  keeps its own basename. Pass the new `--main <basename>` (`build_littlefs_image(..., main=...)`)
  to mark one of them as `main.py` explicitly - matched against each file's basename (e.g.
  `--main app.py` for a `files` entry of `src/app.py`), not the full path, so it doesn't need
  repeating; omit it entirely for filesystems that don't need an auto-run entry point (e.g. modules
  staged only for a raw-REPL-driven test). `--main` must match one of the given files' basenames,
  or `mklittlefs` exits with a clear error instead of silently writing no `main.py`. Two files that
  would land on the same destination name - a duplicate basename, or a file already named `main.py`
  colliding with `--main`'s target - is now also a clear error instead of one silently overwriting
  the other in the image.

### Fixed
- `mklittlefs` no longer crashes (SIGABRT, `lfs_file_sync: Assertion` \`lfs_mlist_isopen(...)\`
  `failed`) under PyPy after successfully writing the image - `littlefs-python`'s C objects were
  getting finalized out of order during PyPy's interpreter shutdown. Only reproducible when
  running under PyPy specifically (e.g. via `setup-rp2040py`'s composite action, which installs
  `rp2040py` under PyPy for the emulator speedup); not an issue under CPython.

## [0.1.0b3] - 2026-07-31

### Added
- Automatic MicroPython/CircuitPython firmware download: `micropython --image` now accepts a known
  version tag (e.g. `1.21.0`, `1.28.0`, `10.2.1` for CircuitPython) in addition to a local file path,
  downloading the matching UF2 from micropython.org/Adafruit's S3 bucket into the current directory
  on first use and reusing it thereafter (`rp2040py.cli.mp_retrieve`). Omitting `--image` now falls
  back to downloading the recommended version (MicroPython 1.21.0 / CircuitPython 8.0.2) instead of
  requiring it to already be present. `ci-micropython.yml`'s separate `curl` download step was
  removed in favor of this.
- `micropython --littlefs`/`--fat12` options to point at a littlefs/FAT12 filesystem image from a
  path other than the default `littlefs.img`/`fat12.img`.
- `mklittlefs --disk-version {2.0,2.1}` to choose the littlefs on-disk format explicitly (defaults
  to `2.0`, still the safe choice for MicroPython <=1.21 - see
  [docs/PORTING.md](docs/PORTING.md#littlefs-image-format-vs-old-micropython-not-actually-a-port-bug)).
- `-V` as a short alias for `--version`.
- Test coverage raised from 56% to 66% (241 -> 345 tests): `tests/test_cli.py`,
  `tests/test_cli_mklittlefs.py`, `tests/test_cli_mp_retrieve.py`, and `tests/test_cli_intelhex.py`
  cover the CLI package end to end (previously untested at 0%), including regression tests for the
  three bugs below. `tests/test_pio.py`, `tests/test_sio.py`, `tests/test_rp2040.py`, and
  `tests/test_cdc.py` port the remaining upstream `*.spec.ts` backlog from
  [docs/PORTING.md](docs/PORTING.md), each call individually verified against the emulator rather
  than translated by argument position (see
  [docs/PORTING.md](docs/PORTING.md#pio_assemblerpys-pio_jmppio_mov-argument-order-differs-from-upstream)
  for a `pio_jmp`/`pio_mov` argument-order gotcha found along the way).

### Changed
- `fs` extra's `littlefs-python` floor raised to `>=0.18.0` (from `>=0.4.0`), matching the version
  the pinned on-disk format was verified against.
- `mklittlefs` writes source files into the image in binary mode instead of text mode, fixing
  corruption of `.mpy` (compiled MicroPython bytecode) files - and non-UTF-8/binary files in
  general, plus platform-dependent line-ending translation on `.py` sources.
- `bootrom.py`'s large bootrom constant table is now imported lazily where it's actually needed
  (`run`/`micropython`/`bench`), so commands that don't boot a device (`--version`, `mklittlefs`,
  `--help`) start faster.
- `ci-micropython.yml`'s `micropython_version` matrix now uses bare version tags (e.g. `1.21.0`)
  instead of dated firmware slugs, resolved through the new download helper.

### Fixed
- `micropython --circuitpython` was silently never loading a `fat12.img`, regardless of whether one
  existed - a regression from refactoring the littlefs/fat12 path-resolution logic into a single
  `if not args.circuitpython` branch that (incorrectly) gated both.
- The "could not find image" error messages (`micropython`, `tests/micropython_spi_run.py`) always
  printed the literal string `None` instead of the version/path that was actually requested.
- `mklittlefs`/`build_littlefs_image` now raises a clear `ValueError` for an unrecognized
  `disk_version` instead of passing `None` through to `littlefs-python` and failing with an opaque
  `TypeError`.
- `tests/micropython_spi_run.py` passed the parsed `argparse.Namespace` to `load_uf2()` instead of
  the resolved image filename, which would always raise a `TypeError`.

## [0.1.0b2] - 2026-07-31

### Added
- `rp2040py` console script (and `python -m rp2040py`) with `run`, `micropython`, and `bench`
  subcommands, so the emulator is runnable from a plain `pip install rp2040py` / `uv add
  rp2040py` / `uvx rp2040py ...` - no git checkout required. `demo/*.py` remain as thin wrappers
  around the same code for anyone working from a checkout.
- `mklittlefs` subcommand (replacing `tests/mklittlefs.py`) to build or update a littlefs image
  for the `micropython` subcommand's filesystem support - opens and updates the image in place if
  it already exists, rather than always reformatting. Needs the new optional `fs` extra
  (`pip install rp2040py[fs]`), which keeps `littlefs-python` out of the zero-dependency default
  install. Only registered as a subcommand when `littlefs-python` is actually installed.
- `micropython -c <command>` / `-m <module>` / `<filename>` (mutually exclusive), matching
  `micropython`'s own CLI: instead of dropping into the REPL, runs the given command/module/script
  on the device non-interactively via the raw-REPL protocol, prints its stdout/stderr, and exits
  with its status (0, or 1 if it raised).
- `rp2040py.device.MicroPythonDevice`, a programmatic API for booting a MicroPython/CircuitPython
  image and running code on it from another Python program - previously this was CLI-only, and the
  CLI's `micropython -c/-m/<filename>` is now itself just a caller of this API. `start()`/`exec()`/
  `exec_file()` block the calling thread; each has a `_async` twin (`start_async()`/
  `exec_async()`/`exec_file_async()`) returning a `concurrent.futures.Future`, plus `astart()`/
  `aexec()`/`aexec_file()` for asyncio. All of these share one `ThreadPoolExecutor(max_workers=1)`
  per device: since the device only has one REPL channel and can't run two `exec()`s at once,
  overlapping calls queue behind each other automatically instead of erroring, and get
  cancellation of not-yet-started calls for free from the standard library.
  `bootrom.py`/`load_flash.py`/`raw_repl.py` moved from `cli/` to the new `device/` subpackage
  accordingly (they aren't CLI-specific, and `device` importing from `cli` would have been
  circular).
- `rp2040py --version`.

## [0.1.0b1] - 2026-07-30

Initial beta release: a complete port of [rp2040js](https://github.com/wokwi/rp2040js) to Python,
capable of booting real firmware (native `.hex`/`.uf2` images, MicroPython, CircuitPython) end to
end.

### Added
- Full RP2040 emulator core: Cortex-M0+ CPU, all peripherals (DMA, PIO, USB, UART, SPI, I2C, ADC,
  PWM, timers, GPIO, interpolators, etc.), GDB server, and the `demo/`/`tests/` runner scripts
  needed to actually boot firmware in the emulator.
- CI workflows (`ci-micropython.yml`, `ci-pico-sdk.yml`) that boot real firmware end to end across
  a `python_runtime` matrix (CPython 3.10, CPython 3.14 with `PYTHON_JIT=1`, PyPy 3.10), plus
  `pre-commit.yml` for lint/type/test checks and coverage reporting.
- `demo/benchmark.py`, a reproducible synthetic and real-firmware-boot benchmark.

### Fixed
- `tests/mklittlefs.py` now pins the littlefs on-disk format to v2.0 (`disk_version=0x00020000`)
  regardless of the installed `littlefs-python` version, so generated filesystem images stay
  mountable by MicroPython releases across the full range tested in CI, not just the newest ones.

### Performance
- `CortexM0Core.execute_instruction()` now dispatches through a precomputed O(1) table instead of
  a linear `if`/`elif` scan, alongside several smaller hot-path optimizations (see
  [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for the full breakdown and
  measurements). Combined effect versus the initial port: real MicroPython + littlefs boot time
  dropped from minutes to seconds under CPython, and to single-digit seconds under PyPy.

[Unreleased]: https://github.com/o-murphy/rp2040py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/o-murphy/rp2040py/compare/v0.1.0rc2...v0.1.0
[0.1.0rc2]: https://github.com/o-murphy/rp2040py/compare/v0.1.0rc1...v0.1.0rc2
[0.1.0rc1]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b6...v0.1.0rc1
[0.1.0b6]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b5...v0.1.0b6
[0.1.0b5]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b4...v0.1.0b5
[0.1.0b4]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b3...v0.1.0b4
[0.1.0b3]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/o-murphy/rp2040py/releases/tag/v0.1.0b1