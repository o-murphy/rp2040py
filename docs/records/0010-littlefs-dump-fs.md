# 0010. littlefs persistence via --dump-fs

- Status: Implemented
- Conceived: 2026-08-03 · Implemented: 2026-08-10 · #24
- Related: #24 · note 0003 (image format)

<!-- migrated verbatim from docs/BACKLOG.md lines 1136-1301 -->

## littlefs persistence to the host `--littlefs` image file — resolved, via `--dump-fs`

**Update: shipped, but via a simpler mechanism than the design sketch below planned - `--dump-fs
<path>` (see CHANGELOG.md's `[Unreleased]` Added section).** Rather than the sidecar-file +
`--persistent` flag design sketched out below, the actual implementation is a direct, explicit
`--dump-fs <path>` flag on `micropython`/`kaluma` that writes the device's flash region back out to
that path when the subcommand exits (Ctrl+X, `--expect-text` firing, or end of a `-c`/`-m`/script
run) - `BaseDevice.dump_flash_image()` plus per-device `dump_*_flash_image()` in
`device/load_flash.py`, the mirror image of the existing `load_*_flash_image()` functions. Point it
at the same path as `--littlefs` for read-modify-write persistence across runs
(`--littlefs img --dump-fs img`), or at a fresh path to capture a run's resulting filesystem state
without touching the original template - which covers this section's point 2 concern (never
clobbering a template in place) without needing an actual sidecar-file scheme. Wired into the same
unified shutdown coordinator (`on_quit`/`ShutdownRequest`) point 4 below identifies as the real
blocker, so it fires on every real exit path, not just clean ones. A CI check
(`scripts/ci-common.sh`'s `run_micropython_dump_test()`) boots against blank flash, dumps, reloads
via `--littlefs`, dumps again, and asserts the two dumps are byte-identical - a regression test for
this exact round-trip. See README.md's "Filesystem support" section for user-facing docs.

Left below for historical context (the alternatives considered, and why point-4's exit-path tracing
mattered) - not a live TODO anymore.

**Original goal:** let changes MicroPython makes to its filesystem during a session actually persist back
to the `--littlefs` image file on disk, instead of only existing in the emulated flash's in-memory
buffer for the lifetime of that one process. Right now `load_micropython_flash_image()`
(`src/rp2040py/device/load_flash.py`) only ever reads the image file *into* `rp2040.flash` once at
boot; nothing ever writes that flash region back out to the file, at exit or otherwise - so the
real JEDEC flash-write support landed in the SSI work above (`RPSSI`, see the first section of this
file) lets `os`/`rp2.Flash` write/erase/program the emulated flash correctly *within* a run, but
every one of those writes is silently discarded the moment the process exits. `--image`'s own UF2
firmware is separate and already read-only by design; this is specifically about the `--littlefs`
region.

**Design sketch (ideas for organizing this work; still not implemented):**

1. **Where the write-back function lives.** Add one helper next to the existing loaders in
   `load_flash.py`, e.g. `flush_flash_region(filename, rp2040, flash_start, block_size,
   block_count)` - the mirror image of `_load_flash_image()`, writing
   `rp2040.flash[flash_start : flash_start + block_size*block_count]` out instead of in. One
   generic function reused by MicroPython/CircuitPython/Kaluma's regions, rather than three
   near-duplicates, since loading and flushing differ only in direction (see point 6 on scope).

2. **Target file: a sidecar, never the original `--littlefs` path.** Write to
   `<littlefs-path>.persistent.img` (exact suffix bikesheddable), not back onto the file the user
   passed in. Reasons this beats overwriting in place:
   - The original is often a deliberately-built template (via `mklittlefs`); overwriting it means
     "start clean again" requires rebuilding it by hand instead of just deleting one sidecar file.
   - This is a dev/test tool - a logic bug in the flash-emulation path could silently corrupt the
     original fixture forever if written in place; with a sidecar, the original is always safe to
     fall back to, and a bad sidecar is just deleted.
   - Matches the base-image/overlay pattern used elsewhere for the same problem (qcow2 backing
     files, VM differencing disks): base stays immutable, session state lives in a separate layer.
   - **Loading logic changes accordingly:** at boot, prefer the sidecar if it already exists (it's
     newer than the base), falling back to the original `--littlefs` path only if no sidecar is
     present yet (first run). The original is thus read-only from this feature's point of view -
     only ever a load source, never a write target.
   - Note this is a *separate* decision from the write-safety mechanism below - writing to a
     sidecar path vs. the original path doesn't change how the write itself needs to be done.

3. **Write safety.** Write to `<sidecar-path>.tmp` then `os.replace()` onto the sidecar path -
   atomic on POSIX, so a process killed mid-write-back can't corrupt a previously-good sidecar.
   Closes the "no investigation done yet" gap noted before without needing to model real flash
   power-loss semantics. (Considered and rejected: mmap-backing `rp2040.flash` itself instead of an
   in-memory buffer + explicit flush - doesn't remove the "when to make it durable" question since
   OS page-cache writeback timing isn't ours to control either, can let a torn/mid-command state
   reach disk on its own schedule instead of only at a controlled commit point, and would require
   splitting `rp2040.flash`'s single unified bytearray - which also covers the UF2 firmware region,
   deliberately *not* persisted - into a composite structure. A plain in-memory buffer with an
   explicit, controlled flush point mirrors real flash hardware's own model anyway: even a real
   NOR chip buffers incoming bytes in an internal page register and only commits to persistent
   cells when a program/erase command completes, which is exactly what `_apply_command()` in
   `ssi.py` already emulates - so this isn't a compromise vs. "how real hardware does it," it's the
   same shape.)

4. **When to call it - the actual blocker, found by tracing the CLI's real exit paths.** Both
   `micropython`'s interactive-REPL branch and `kaluma`'s only path funnel through
   `_wait_for_simulator()` (`cli/__init__.py`), and *every intentional quit path calls `os_exit()`*
   (`cli/stdio_repl.py`) instead of returning normally. There are four call sites today, three for
   the same underlying reason: Ctrl+X (inside `StdioInteractiveRepl._read_stdin_loop`, its own
   dedicated daemon thread), an `--expect-text` match (fired from `_make_expect_text_watcher`,
   running on the simulator's `threading.Timer` reschedule chain - also a daemon thread, see
   `simulator.py`), and Ctrl+C/`KeyboardInterrupt` (main thread, but still routed through the same
   helper for consistency + guaranteed terminal restore). `os._exit()` is used for the first two
   specifically because `sys.exit()` called from a *non-main* thread only raises `SystemExit`
   inside that one thread - it doesn't end the process, so the main thread's own
   `while simulator.executing` loop in `_wait_for_simulator` would just keep polling forever,
   oblivious. (The fourth call site, in `_cmd_mklittlefs`, is unrelated - a PyPy-only workaround for
   `littlefs-python`'s C objects finalizing out of order during interpreter shutdown.) All of this
   skips ordinary Python shutdown (`atexit`, `finally`, context-manager `__exit__`) completely, so a
   write-back hook placed only in `BaseDevice.stop()`/`__exit__` would **never fire on either of the
   CLI's two real long-running exit paths** (Ctrl+X/`--expect-text` bypass `device.stop()`
   entirely; `_wait_for_simulator`'s `KeyboardInterrupt` branch calls `simulator.stop()` directly,
   not `device.stop()`). By contrast, the raw-REPL one-shot path (`-c`/`-m`/`<file>`) already calls
   `device.stop()` on every exit via a plain `try`/`except` in `_cmd_micropython`, so a
   `stop()`-based hook *would* cover that path for free.

   **Two ways to actually wire the flush call in, both discussed, neither implemented:**
   - **(a) One hook inside `os_exit()` itself (recommended first cut).** All four call sites
     already funnel through this one shared function in `stdio_repl.py` (it already tracks
     `_active_raw_repl` as module-level state to restore the terminal before exiting) - a
     similar registration mechanism (e.g. "the currently-active device to persist, if any," set by
     `_cmd_micropython`/`_cmd_kaluma` right after constructing `device`) lets `os_exit()` call
     `persist_littlefs()` once, centrally, before it calls `os._exit()`. Small, surgical, doesn't
     touch the threading/exit-timing model at all.
   - **(b) Bigger alternative: replace `os._exit()` with a `threading.Event` + a main-thread-driven
     `sys.exit()`.** Instead of the background thread (Ctrl+X handler, `--expect-text` watcher)
     tearing the whole process down itself, it would just set an `Event`; `_wait_for_simulator`'s
     loop (already polling every 100ms) would check that `Event` alongside `simulator.executing`
     and, once set, perform the actual shutdown itself - `simulator.stop()`, `persist_littlefs()`,
     then a normal `sys.exit()` - from the main thread, where it behaves correctly. Confirmed this
     is technically sound: both background threads in question (`stdio_repl.py`'s stdin reader,
     `simulator.py`'s `threading.Timer` chain) are already `daemon=True`, so a clean main-thread
     exit wouldn't hang waiting on them - Python's normal shutdown abandons daemon threads
     immediately regardless of what they're doing (including the stdin reader thread, permanently
     parked in a blocking `os.read()` that nothing can interrupt short of killing the process,
     which is fine since it's never waited on). This would make ordinary `atexit`/`finally` hooks
     work again, which is architecturally nicer, but it's a real refactor of working, tested exit
     machinery (`_wait_for_simulator`'s loop, `StdioInteractiveRepl`'s Ctrl+X handler,
     `_make_expect_text_watcher`, and deciding who calls `simulator.stop()` in each case - notably,
     the `--expect-text` path doesn't call it today at all, relying on `os._exit()` to make that
     moot) - worth doing if a general graceful-shutdown mechanism is wanted for its own sake, but
     more than persistence alone justifies. **(a) is the pragmatic choice for this feature
     specifically; (b) is a legitimate but separate, larger piece of work.**

     Update: (b) landed in spirit (see "Unified process-shutdown coordinator" below -
     `ShutdownRequest`/`wait_for_shutdown()` is exactly this), but still thread-based underneath.
     The *further* step this note gestures at - replacing the threads themselves, not just giving
     them a shared exit protocol - is scoped separately in
     [docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md).

5. **Flag surface: `--persistent PATH`, value required - not a boolean.** Both `micropython` and
   `kaluma` subcommands already have a positional `filename` argument (`nargs="?"`, e.g.
   `rp2040py micropython script.py`) - an optional-value flag (`nargs='?'` with a `const` for
   "given with no value") is a real argparse footgun here: `--persistent script.py` would get
   silently swallowed as `--persistent`'s own value instead of `filename`'s, depending on argument
   order. Requiring a value sidesteps this entirely, and also means "write in place" isn't a
   special case needing its own code path - it falls out for free: if the user passes
   `--persistent` pointing at the same path as `--littlefs`, the write function just writes there,
   no `if path == littlefs_path` branch needed anywhere. Not passing `--persistent` at all keeps
   the feature off (today's default, opt-in), and no auto-derived filename magic (e.g. inventing
   `.persistent.img` when no path is given) - simpler to keep the path fully explicit than to write
   and maintain path-derivation logic for a rarely-used flag.
   - Considered and rejected: dropping the flag entirely and always persisting in place by default,
     documenting it as the user's responsibility to back up their own template. Rejected because
     it's not hypothetical risk - it's already flagged below (see point 7's CI note) that
     `ci-micropython.yml`/`tests/test_device.py` may rely on every run starting from the same clean
     image; making persistence unconditional would silently violate that for existing CI, not just
     for new opt-in users. Only reasonable if that assumption is first audited and found not to
     hold - not done yet.

6. **Programmatic API (`MicroPythonDevice`/`KalumaDevice`).** Expose this as an explicit
   `persist_littlefs()` (or `flush()`) method rather than an implicit constructor flag or
   `__exit__`-only behavior - callers embedding this as a library (per `mp_device.py`'s own stated
   use cases: test runners, Thonny-style tools) are far more likely to want to control exactly
   *when* a flush happens (e.g. right after one specific `exec()` completes) than to get one
   silently attached to context-manager exit.

7. **Scope for a first cut.** Prototype against MicroPython only (extending the existing
   `tests/micropython/main-flash-rw.py`), matching the reasoning above, but keep the point-1
   helper's signature generic over `(flash_start, block_size, block_count)` from the start so
   wiring in CircuitPython's `--fat12` region and Kaluma's `--littlefs`/
   `KALUMA_PROG_FLASH_START` afterward is a call-site addition, not a rewrite.

This is a design sketch to make the work easier to pick up, not an implementation - none of the
above is committed yet.

