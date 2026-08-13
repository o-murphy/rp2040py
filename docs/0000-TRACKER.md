# Tracker

Index and current state for the project's engineering records. These are working notes
that span multiple sessions — **not** user-facing docs (see `README.md`, `CHANGELOG.md`,
and `reference/` for those). The structure itself is decided in
[record 0032](records/0032-docs-restructure.md).

## Conventions

- **`records/NNNN-*.md`** — numbered, immutable, append-only. The number is assigned in
  the order the idea/note arose; it is a stable ID, **not** a claim about implementation
  order (that lives in each record's dated events and in the columns below).
- **Events** inside a record: `Proposed` → `Implemented` / `Rejected` / `Superseded`,
  each dated and cross-linked. Content is never deleted — negative results stay.
- **Ideas** appear as a checkbox row below (`[x]` = accepted/implemented, `[ ]` = not
  yet / rejected / superseded). **Notes** (research, postmortems) are numbered too but
  have no state row — they are linked from the ideas that use them.
- **Granularity is tiered** (see 0032): short-lived ideas are one file (**B1**);
  long-running, cross-cutting threads are split into several records (**B2**).
- **`reference/`** — living how-to / checklists. Not numbered.

## Ideas

### Implemented

- [x] [0001](records/0001-cli-device-api.md) CLI tool + public device API | `#3,#5,#10`
- [x] [0002](records/0002-mklittlefs-image.md) mklittlefs image handling | `#6`
- [x] [0004](records/0004-buffer-overflow-races.md) buffer overflow / race conditions | `#7`
- [x] [0005](records/0005-kaluma-firmware.md) Kaluma firmware support | `#12`
- [x] [0006](records/0006-gpio-pull-floating.md) GPIO pull-up/down on floating pin | `#13`
- [x] [0007](records/0007-bootrom-revisions.md) configurable bootrom (`--bootrom`, B0/B2) | `#16`
- [x] [0008](records/0008-ssi-flash-write.md) SSI flash-write support | `#18`
- [x] [0009](records/0009-simulator-fix.md) simulator fix | `#19`
- [x] [0010](records/0010-littlefs-dump-fs.md) littlefs persistence → `--dump-fs` | `#24`
- [x] [0011](records/0011-mp-1.21-vs-1.28-gap.md) MicroPython 1.21-vs-1.28 gap | resolved — upstream, not our bug
- [x] [0012](records/0012-cdc-performance.md) CDC (USB serial) performance | root-caused + fixed
- [x] [0013](records/0013-cython-core.md) Cython interpreter core | ~4x, on by default `#20`
- [x] [0019](records/0019-asyncio-idle-yield.md) asyncio idle-yield latency | `#23`
- [x] [0020](records/0020-pty-serial-passthrough.md) PTY / serial passthrough (`--pty`) |
- [x] [0021](records/0021-shutdown-coordinator.md) unified shutdown coordinator | Ctrl+X / --expect-text / SIGTERM
- [x] [0022](records/0022-mpremote-socket-repl.md) mpremote REPL over `socket://` | `#27`
- [x] [0023](records/0023-termux-mpremote.md) Termux mpremote compat | `#30`
- [x] [0025](records/0025-full-asyncio-migration.md) full asyncio migration | phases 1–5 · **supersedes 0014**
- [x] [0026](records/0026-main-thread-asyncio.md) main-thread asyncio | 5 phases landed
- [x] [0028](records/0028-cyw43-module-layout.md) CYW43 module layout | accepted
- [x] [0029](records/0029-cyw43-board-composition.md) CYW43 board composition | accepted
- [x] [0030](records/0030-external-device-concurrency.md) ExternalDevice concurrency model | accepted
- [x] [0031](records/0031-pio-cython-tick-batching.md) PIO Cython + `clock.tick()` batching | follow-up of 0013
- [x] [0032](records/0032-docs-restructure.md) documentation restructure | this reorganization
- [x] [0033](records/0033-completions-and-validation.md) Add autocompletions for the cli tool | accepted
- [x] [0034](records/0034-execute-batch-native-port.md) `_execute_batch()` native Cython port | follow-up of 0013/0031
- [x] [0035](records/0035-board-aware-fs-flash-offset.md) board-aware MicroPython/CircuitPython/Kaluma FS flash offset | fixes pico_w wild-execution crash
- [x] [0036](records/0036-littlefs-fat12-exclusivity.md) `--littlefs`/`--fat12` mutual exclusivity | explicit validation, matches `--tcp-port`/`--pty` pattern
- [x] [0037](records/0037-pio-clock-coupled-stepping.md) couple `RPPIO` stepping to the CPU's own instruction loop | fixes CYW43 native-mode livelock, follow-up of 0031/0034
- [x] [0038](records/0038-cyw43-ioctl-response-zero-fill.md) `GSPIBus` ioctl-response zero-fill fix | fixes `nic.active(True)` real root cause (was misdiagnosed as throughput-only)
- [x] [0039](records/0039-simulation-clock-native-port.md) `SimulationClock` native Cython port | follow-up of 0013/0031/0034, closes 0034's own leftover gap; ~2.7x on a synthetic busy-spin benchmark, `powersave` governor caveat, literal 0038 repro not re-run

### In progress / Proposed

- [ ] [0016](records/0016-jit-fusion.md) basic-block fusion / mini-JIT | Proposed — isolated test done
- [ ] [0027](records/0027-cyw43-wifi.md) CYW43439 / Pico W WiFi (epic) | In progress — step 3g (scripted scan/join) done, unit-tested; live boot now blocked on a separate frozen-CPU issue (`docs/tasks/cyw43-post-data-header-freeze.md`), not the known raw-throughput ceiling; main-spi.py hang still open; step 4 (real network bridge) not started

### Rejected / Superseded

- [ ] [0014](records/0014-threading-model.md) threading model | **Superseded → 0025**
- [ ] [0015](records/0015-memcpy-hle.md) HLE memcpy hook | **Rejected** — net negative (measurements kept)

## Notes (no state row — linked from ideas)

- [0003](records/0003-littlefs-image-format.md) littlefs image format vs. old MicroPython → 0002, 0010
- [0017](records/0017-perf-python-vs-v8.md) performance: pure-Python vs V8 → 0011, 0013, 0015, 0016
- [0018](records/0018-raw-repl-txfifo.md) raw-REPL cross-thread `tx_fifo` (postmortem) → 0014
- [0024](records/0024-cyw43-protocol.md) CYW43439 protocol reverse-engineering → 0027, 0028, 0029, 0030
- [0040](records/0040-time-monotonic-vs-cimport.md) `time.monotonic()` vs `cimport`'d clock in `native/_simulator.pyx` → 0031, 0034, 0039

## Reference (living, unnumbered)

- [reference/porting-checklist.md](reference/porting-checklist.md) — port checklist + minor known differences
- [reference/mpremote.md](reference/mpremote.md) — using mpremote with rp2040py
