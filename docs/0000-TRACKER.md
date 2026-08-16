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
- **A record whose code landed is not automatically `[x]`.** If the record's own writeup still
  carries open work against itself — a "Known gaps"/"Still open" section listing things that are
  wrong or unbuilt, not merely ideas for later — the row stays `[ ]` under "In progress /
  Proposed", with those gaps as nested checkboxes, and only moves to "Implemented" once they are
  closed. A merged PR says the code shipped; it does not say the idea is done. (Currently: 0048.)
- **Granularity is tiered** (see 0032): short-lived ideas are one file (**B1**);
  long-running, cross-cutting threads are split into several records (**B2**).
- **`tasks/NNN.md`** — working notes for investigations that aren't root-caused yet. Not
  numbered, not immutable, and **not permanent**: once a note is resolved it is folded into the
  record that resolves it (verbatim, as an `## Appendix: folded-in working note ...` section) and
  the file is deleted. Only still-open notes live in `tasks/`, and every one of them has a row in
  "In progress / Proposed" below. Records written before a fold-in still cite the old
  `docs/tasks/<name>.md` path — those references resolve to the appendix of whichever record the
  note was folded into (records are append-only, so the citations themselves are left as written).
  Folded in so far, all on 2026-08-16: `3g-scripted-scan-join` → [0027](records/0027-cyw43-wifi.md),
  `cyw43-3g-live-boot-verification` → [0027](records/0027-cyw43-wifi.md),
  `cyw43-post-data-header-freeze` → [0041](records/0041-cyw43-post-data-header-freeze-fix.md),
  `main-spi-hang` → [0044](records/0044-spi-dma-tx-rx-starvation-fix.md),
  `simulation-clock-cython-port` → [0039](records/0039-simulation-clock-native-port.md),
  `circuitpython-10x-boot-stall` → [0050](records/0050-qspi-pad-reset-values.md).
  (`littlefs-fat12-exclusivity`, cited by [0036](records/0036-littlefs-fat12-exclusivity.md), was
  deleted before this convention existed and was **not** preserved anywhere.)
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
- [x] [0041](records/0041-cyw43-post-data-header-freeze-fix.md) CYW43 live-boot freeze root cause + fix | fixes the `cyw43-post-data-header-freeze` working note (folded into 0041's own appendix); unblocks 0027 step 3g live-boot verification
- [x] [0042](records/0042-cyw43-interrupt-register-w1c-fix.md) `GSPIBus` `SPI_INTERRUPT_REGISTER` write-1-to-clear (W1C) fix | fixes spurious `[CYW43] Bus error condition detected 0xb9` warning during live boot
- [x] [0043](records/0043-pio-dma-first-batch-race.md) `RPPIO` CTRL-enable first-batch/DMA-refill race fix | fixes MicroPython v1.23.0's CYW43 boot (`scan()` raising `EPERM`), follow-up of 0037
- [x] [0044](records/0044-spi-dma-tx-rx-starvation-fix.md) DMA-driven SPI TX/RX hang fix | fixes the `main-spi-hang` working note, folded into 0044's own appendix (stale DREQ cache after `RPDMA.reset()` + same-tick `SimulationClock` alarm starvation), confirmed unrelated to CYW43/0027
- [x] [0040](records/0040-time-monotonic-vs-cimport.md) `native/_simulator.pyx` hot loop: `libc.math.INFINITY` for `float("inf")` | kept; `cpython.time cimport monotonic` was also tried but **reverted** - broke real CI (`PyTime_t` not in the limited-API surface until 3.13, this project's `Py_LIMITED_API` floor is 3.11), back to plain `time.monotonic()`, follow-up of 0031/0034/0039
- [x] [0046](records/0046-epd2in9g-external-device.md) `epd2in9g` virtual e-paper ported forward + promoted to `ExternalDevice` | ported from the stale `component/epd2in9g` branch (fixes API drift vs. async-native `MicroPythonDevice`/`utils/firmware_retrieve.py`), moved `src/rp2040py/external/epd2in9g.py` alongside `external/cyw43/`, no Pillow dependency in `src/`, follow-up of 0029/0030
- [x] [0050](records/0050-qspi-pad-reset-values.md) CircuitPython 10.x boot stall root cause + fix | `PADS_QSPI` reset values: `GPIO_QSPI_SS` needs its **pull-up** (`0x5A`), not bank0's pull-down default, or an undriven SS reads low forever - a permanently-held BOOTSEL button. 10.x polls it from a RAM-resident loop during boot and hung; 9.x/8.x never did, so the defect was latent for years. Also folds in the `circuitpython-10x-boot-stall` working note, and keeps two unrelated defects found on the way (SEV never set the event register; `VREG_AND_CHIP_RESET` was unimplemented so `CHIP_RESET` read all-ones) - both proven by ablation *not* to be this bug
- [x] [0047](records/0047-cyw43-pio-gpio-hotpath.md) CYW43 pure-Python hot path: `check_changed_pins` fix + `GPIOPin`/`RPPIO` Cython ports | ~2.6x on CYW43 `pico_w` boot-to-`scan()`; `RPPIO` satisfies the `Peripheral` Protocol (can't inherit `BasePeripheral`), one-way `_pio`→`_gpio_pin`/`StateMachine` cimport (mutual cimport reverted — circular runtime import), abi3-verified on 3.14, `.pyi` stubs for `native.*` (surfaced+fixed the `IClock` protocol gap + `key_mock.drive_input` latent bug); PyPy still ~1.16x ahead — finishes 0031's "not yet tried #4", follow-up of 0013/0034/0039
- [x] [0027](records/0027-cyw43-wifi.md) CYW43439 / Pico W WiFi (epic) | steps 0-3g done, live-boot-verified on both v1.23.0 and v1.28.0 (2026-08-13/14, see 0042/0043); step 4 (real network bridge, including DNS) implemented + live-boot-verified via [0048](records/0048-cyw43-nat-reflector.md) (2026-08-16), **which is still open under "In progress / Proposed" for its own "Known gaps"** — this epic's own steps are done, step 4's remaining work is tracked on 0048's row, not here; `main-spi.py` hang fixed but confirmed unrelated to this epic, see 0044

### In progress / Proposed

- [ ] [0049](records/0049-external-device-authoring-docs.md) document external devices + how a user writes their own | **Proposed, nothing implemented** — `ExternalDevice`/`attach_external_devices()` and four in-tree devices (`LEDMock`/`key_mock`/`Epd2in9G`/`Cyw43439`) have no user-facing docs at all, while `reference/os-compatibility.md` already carries per-OS rows for two of them; `--board` can only pick from `boards.py`'s fixed registry, so attaching your own device means dropping to the library API - the "when the CLI isn't enough" path nothing documents. Open questions (README vs `reference/`, whether `ExternalDevice`'s attach-only surface is ready to be public API, whether the CLI should grow a `--device` hook) are listed in the record and deliberately left undecided
- [ ] (no record yet) `test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it` flaky on CI | not root-caused - see [docs/tasks/queued-exec-erroring-flaky-test.md](tasks/queued-exec-erroring-flaky-test.md); observed on Windows + Ubuntu `pre-commit` CI (2026-08-14), unrelated to 0040/0044
- [ ] [0048](records/0048-cyw43-nat-reflector.md) CYW43 step 4 NAT bridge: custom minimal hand-rolled TCP reflector + UDP relay (supersedes 0045's engine choice) | **4a-4e implemented, live-boot-verified on v1.23.0/v1.28.0, and merged** ([PR #37](https://github.com/o-murphy/rp2040py/pull/37), 2026-08-16, `9f5348f`, all 62 checks green incl. `pre-commit` on `windows-latest`) — **but this row stays open until the record's own "Known gaps" section is closed**, since a working happy path is not the same as a complete WiFi emulation. What landed: guest-facing leg reuses `bus.py`'s own lossless in-order FIFO (so no retransmission/congestion-control/reassembly needed), host-facing leg is a real `asyncio` socket; `main-cyw43.py` completes a real TCP round trip to `1.1.1.1:80`, `mip.install()` resolves DNS and installs a real package, `ntptime.settime()` round-trips real NTP. Same-day follow-up closed 3 gaps (real-RST-vs-clean-FIN, connect-timeout/flow-table leak, DNS-only→general UDP) plus a latent `except TimeoutError` bug that silently never fired on Python 3.10, and fixed **two separate** `windows-latest` CI flakes (a fixed-`asyncio.sleep()` race in the test suite; the RST test's `SO_LINGER` real-kernel-RST trick not reliably surfacing as `ConnectionResetError` through Windows' asyncio). Still open, full inventory in the record itself:
  - [ ] **`disconnect()` is a no-op** — real correctness gap: only link-*up* is ever scripted, `bus.py` has no `WLC_DISASSOC`/deauth handling, so a guest that calls `disconnect()` keeps believing it's connected. Needs the same kind of real `cyw43-driver` protocol research 4a-4c needed - not attempted
  - [ ] **no window backpressure from the real destination socket** — accepted v1 simplification, not an oversight: shrink the advertised guest-facing window by `transport.get_write_buffer_size()` if a slow real destination + fast guest sender ever proves it matters
  - [ ] **unbuilt: AP mode, multi-AP/hidden-SSID/negative-auth scan results, IPv6, multi-guest** — none partially-done; join is scripted unconditionally, so a *wrong* password currently "succeeds" too. Guest/gateway IP+MAC are fixed module constants in `nat.py` with no config surface
  - [x] ~~unverified: real TLS/HTTPS + WebSocket end-to-end~~ — **verified 2026-08-16** on both `v1.23.0` and `v1.28.0`: real TLS to `micropython.org:443` (real cert chain), RFC 6455 WebSocket and WebSocket-over-TLS against an echo server on a non-loopback address. TLS step landed in `tests/micropython/main-cyw43.py`; WebSocket verified by hand, deliberately not wired into CI (see the record)
  - [x] ~~unverified: CircuitPython live boot~~ — **verified 2026-08-16** against CircuitPython `9.2.9` on `--board pico_w`: scan/join/DHCP/TCP/DNS all work through `wifi`/`socketpool`. Landed `tests/circuitpython/main-cyw43.py` + `.github/workflows/ci-circuitpython.yml` (the project's first CircuitPython CI). Found a separate, unrelated blocker on the way — see the CircuitPython 10.x row below

### Rejected / Superseded

- [ ] [0014](records/0014-threading-model.md) threading model | **Superseded → 0025**
- [ ] [0015](records/0015-memcpy-hle.md) HLE memcpy hook | **Rejected** — net negative (measurements kept)
- [ ] [0016](records/0016-jit-fusion.md) basic-block fusion / mini-JIT | **Rejected** — net negative in every real integration attempt; Cython interpreter core (0013) used instead
- [ ] [0045](records/0045-cyw43-nat-libslirp-cython.md) CYW43 step 4 NAT bridge: embed `gVisor`'s `pkg/tcpip` via `cgo` | **Superseded → 0048** (2026-08-16, hand-rolled reflector, no new toolchain) — kept verbatim for its own research trail (the `PyTCP` negative result, the `gVisor` empirical PoC, and the SDPCM `DATA_HEADER` envelope derivation 0048 itself reuses directly)

## Notes (no state row — linked from ideas)

- [0003](records/0003-littlefs-image-format.md) littlefs image format vs. old MicroPython → 0002, 0010
- [0017](records/0017-perf-python-vs-v8.md) performance: pure-Python vs V8 → 0011, 0013, 0015, 0016
- [0018](records/0018-raw-repl-txfifo.md) raw-REPL cross-thread `tx_fifo` (postmortem) → 0014
- [0024](records/0024-cyw43-protocol.md) CYW43439 protocol reverse-engineering → 0027, 0028, 0029, 0030

## Reference (living, unnumbered)

- [reference/porting-checklist.md](reference/porting-checklist.md) — port checklist + minor known differences
- [reference/mpremote.md](reference/mpremote.md) — using mpremote with rp2040py
- [reference/os-compatibility.md](reference/os-compatibility.md) — OS × feature compatibility matrix
