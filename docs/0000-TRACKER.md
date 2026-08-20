# Tracker

Index and current state for the project's engineering records. These are working notes
that span multiple sessions — **not** user-facing docs (see `README.md`, `CHANGELOG.md`,
and `reference/` for those). The structure itself is decided in
[record 0032][0032].

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
  closed. A merged PR says the code shipped; it does not say the idea is done. (Currently: [0048].)
- **Granularity is tiered** (see [0032]): short-lived ideas are one file (**B1**);
  long-running, cross-cutting threads are split into several records (**B2**).
- **A row is an index entry, not a summary.** One line: state, number, title, and a short clause
  saying where it stands (and what it supersedes/depends on). Everything else — the reasoning, the
  measurements, the caveats, the open gaps — belongs in the record file, which is the only place it
  can be maintained. Rows that grow with each update stop being scannable, which is the one job
  this list has.
- **Newest first, and open work before finished work.** "In progress / Proposed" comes before
  "Implemented", and every section is ordered by *recency* with the newest at the top, the same way
  `CHANGELOG.md` reads — so what is being worked on now is what you see first, and the numbering
  order (which is arrival order, not implementation order) stays a property of the record files.
- **`tasks/NNN.md`** — working notes for investigations that aren't root-caused yet. Not
  numbered, not immutable, and **not permanent**: once a note is resolved it is folded into the
  record that resolves it (verbatim, as an `## Appendix: folded-in working note ...` section) and
  the file is deleted. Only still-open notes live in `tasks/`, and every one of them has a row in
  "In progress / Proposed" below. Records written before a fold-in still cite the old
  `docs/tasks/<name>.md` path — those references resolve to the appendix of whichever record the
  note was folded into (records are append-only, so the citations themselves are left as written).
  Folded in so far: `3g-scripted-scan-join` → [0027],
  `cyw43-3g-live-boot-verification` → [0027],
  `cyw43-post-data-header-freeze` → [0041],
  `main-spi-hang` → [0044],
  `simulation-clock-cython-port` → [0039],
  `circuitpython-10x-boot-stall` → [0050]
  (all five on 2026-08-16), `queued-exec-erroring-flaky-test` → [0065] (2026-08-18, closed
  dormant/not-root-caused rather than by a fix).
  (`littlefs-fat12-exclusivity`, cited by [0036], was
  deleted before this convention existed and was **not** preserved anywhere.)
- **`reference/`** — living how-to / checklists. Not numbered.

## Ideas

### In progress / Proposed

- [ ] [0072] W5500 Ethernet PHY `ExternalDevice` + `W5500_EVB_PICO` board (epic) | **Proposed, phased plan only.** MACRAW passthrough (MicroPython's default) reuses [0048]'s `NatBridge` almost directly; hardware TCP/UDP socket-engine mode (CircuitPython's `adafruit_wiznet5k`) is a separate, later phase. 5 phases, none started
- [ ] [0066] board support expansion: which RP2040 boards are addable, and what each still needs | **partly done** - the MicroPython list is 12/12 ([0080]); 4 of 37 CircuitPython-only, the rest gated on missing `ExternalDevice`s
- [ ] [0088] USB host side: mass storage, CDC control lines, and reset | measured, nothing built - `usb/cdc.py` is a CDC consumer, not a host; a 1200-bps-touch reset would do nothing on rp2; MSC is mutually exclusive with [0087]
- [ ] [0087] CircuitPython's CIRCUITPY is writable over the raw REPL we already have | measured, nothing built - `storage.remount()` succeeds because nothing here claims the MSC interface; rejected [0086], and now proposes `aexec()` + a host-side `areset()` (flash survives, so no image round trip) which needs [0057]'s USB step and 0087's own item 4 first
- [ ] (no record yet) test TinyGo-compiled firmware | idea only, not investigated - TinyGo (`tinygo build -target=pico`) emits a `.uf2`/`.hex`, which the existing `run` subcommand already loads (raw image + GDB server, no firmware-family resolution); unverified whether it actually boots/runs correctly, or what (if anything) blocks it
- [ ] [0061] one firmware command with `--family` | **Deferred, documented.** Step 1 is nearly free since [0059]
- [ ] [0064] read-only state server (WebSocket/Socket.IO) + web visualizer | **Deferred, documented, not planned near-term.** Splits the *watching* half out of [0060] (no wall-clock ceiling applies); blocked first on devices being able to describe themselves ([0049])
- [ ] [0060] external I/O bridges (web viewer, host GPIO) | **Deferred, documented.** Names the wall-clock ceiling a pin-level bridge cannot escape
- [ ] [0057] RESET button / RUN pin reset hook on `RP2040` | documented, not built - the `cdc.reset()` step it calls the blocker is what [0087]'s proposed `areset()` would provide; the `ExternalDevice`-can't-reach-`BaseDevice` half stands
- [ ] [0053] second core (core1) + inter-core FIFO | **Proposed, core1/the real FIFO not implemented.** Adding the registers alone would turn an honest warning into a silent hang; limitation stated user-facing. Addendum settles the execution model: interleaved in one loop, not a thread per core. Its own "Interim option" - a clearer read-side warning naming this record instead of a generic "invalid SIO address" line - landed 2026-08-18
- [ ] [0048] CYW43 step 4 NAT bridge (supersedes [0045]) | **4a-4e merged and live-verified**, open only for the record's own "Known gaps" - window backpressure, AP mode, multi-guest, IPv6
- [ ] [0065] `test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it` flaky on CI | **Closed dormant, not root-caused** (2026-08-18) - single 2026-08-14 occurrence, not reproduced in ~85 subsequent CI runs; reopen if it recurs

### Implemented

- [x] [0085] CircuitPython `code.py`/`boot.py` on the display demos, and where a WiFi screenshot has to come from | `demo/mkfat12.py` + `lcd_run.py --code/--boot/--fat12/--dump-fs`; `boot.py` output can never reach the panel, and the WiFi shot needs a Pico W with an `St7735s` wired on; **demo-half rework planned, not built** (appendix) on [0087]'s in-process route
- [x] [0084] 0xCB Helios board | fourth board off [0066]'s CircuitPython-only list; closes that record's own "not fully confirmed" flag on this board's RGB pin - the pico-sdk header's `PICO_DEFAULT_WS2812_PIN 25` settles it, since CircuitPython's own port source never says what protocol GPIO25 speaks. `LEDMock(gpio=17)` + `Ws2812(gpio=25)` + `BootselButton`, one firmware family, 16 MiB. Live boot: LED toggles 16 times as CircuitPython's status indicator, RGB decodes 0 frames with no guest code - the opposite WS2812-at-boot behavior from every other board here
- [x] [0083] 0xCB Gemini board | third board off [0066]'s CircuitPython-only list; `Ws2812(gpio=16)` + `BootselButton`, one firmware family, 16 MiB (`W25Q128JVxQ`, same capacity as [0080]'s part). First board with an identifier-unsafe filename (`0xcb_gemini` starts with a digit), so no `PYTHONPATH=. --board-spec module.path:ATTR` form exists for it - documented rather than silently dropped. 11 WS2812 status-LED frames decoded at boot, no guest code run
- [x] [0082] Waveshare RP2040-Tiny board | off [0066]'s CircuitPython-only list; `Ws2812(gpio=16)` + `BootselButton`, one firmware family. Narrowest pin breakout of any board here (`pins.c` declares GP0-16 + GP26-29 only); its 2 MiB part matches a plain Pico's byte for byte, so the local-path `--image` MicroPython fallback is electrically exact rather than a compromise
- [x] [0081] Waveshare RP2040-One board | **first board off [0066]'s CircuitPython-only list** (the MicroPython-port list is exhausted as of [0080]); `Ws2812(gpio=16)` + `BootselButton`, one firmware family, 4 MiB. Live boot confirms `pins.c`'s four absences (no LED/BUTTON/SPI/I2C) from the running firmware. Also found: `--image` needs a local `.uf2` path, not a bare tag, on a one-family spec - [0062]'s own board file documents the tag form and is wrong, left unfixed and flagged
- [x] [0080] SparkFun Pro Micro RP2040 board | off [0066]'s survey, and the row that **completes** its "addable now, has a MicroPython port" checklist (12/12); `Ws2812(gpio=25)` + `BootselButton` only - no plain LED at all, upstream says so explicitly rather than by omission. Largest flash of any board here (16 MiB, `fs_blockcount=3840`); ships neither a `pins.csv` nor a `set(PICO_BOARD ...)`, so its pico-sdk header is reached via `ports/rp2/CMakeLists.txt`'s lowercase fallback
- [x] [0079] Seeed Studio XIAO RP2040 board | off [0066]'s survey; the first board here with **both** kinds of RGB LED - `Ws2812(gpio=12)` (power pin GPIO11, not gated, same as [0071]) *and* 3×`LEDMock(active_low=True)` (GPIO16/17/25). Polarity came from Seeed's own wiki, not either firmware port: CircuitPython declares no RGB status LED here and pico-sdk's `PICO_DEFAULT_LED_PIN_INVERTED` qualifies only GPIO25
- [x] [0078] Waveshare RP2040-Plus board | off [0066]'s survey; plain `LEDMock(gpio=25)` + `BootselButton` only - no `USER_SW` equivalent exists on this board at all, so no open pull-direction gap; two flash variants (4/16 MiB, `BOARD`/`BOARD_16MB`), numerically identical flash geometry to [0076]'s own table
- [x] [0077] Pimoroni Tiny 2040 board | off [0066]'s survey; RGB LED as 3×`LEDMock(active_low=True)` (not a `Ws2812`, same shape [0075]), two flash variants (2/8 MiB, `BOARD`/`BOARD_8MB`); `USER_SW` left unmodelled (no schematic, same gap as [0076])
- [x] [0076] Pimoroni Pico LiPo board | off [0066]'s survey; two flash variants (4/16 MiB, `BOARD`/`BOARD_16MB`), a single flat file (not a directory - the first board built this way deliberately); `USER_SW` left unmodelled (no schematic). Also: 7 earlier boards this session retroactively flattened from directories to flat files, and the skill's own "never nest a device in boards/" text corrected against what 0059 actually says
- [x] [0075] nullbits Bit-C PRO board | off [0066]'s survey; RGB LED as 3×`LEDMock(active_low=True)` (confirmed by both firmware ports independently, not a `Ws2812`), CircuitPython drives it as a PWM status indicator from boot
- [x] [0074] Machdyne Werkzeug board | off [0066]'s survey; `LEDMock` gained `active_low` (this board's green LED is genuinely active-low, the first such case), red LED's polarity stays an open gap
- [x] [0073] Garatronic/McHobby PYBStick26 RP2040 board | smallest remaining board off [0066]'s survey; MicroPython-only, `LEDMock` (`board.json`'s own "RGB LED" tag contradicted by every real source), live-boot-verified
- [x] [0071] Adafruit QT Py RP2040 board | fourth board picked up off [0066]'s survey; `Ws2812`/`KeyMock`, no plain LED, both firmware families, live-boot-verified; power pin and diode-into-BOOTSEL button design component-for-component identical to [0070]'s ItsyBitsy
- [x] [0070] Adafruit ItsyBitsy RP2040 board | third board picked up off [0066]'s survey; `LEDMock`/`Ws2812`/`KeyMock`, both firmware families, live-boot-verified; BOOT button sourced from Adafruit's own schematic (also diode-coupled into real BOOTSEL, not modelled - analog cross-pin path)
- [x] [0069] Adafruit Feather RP2040 board | second board picked up off [0066]'s survey; zero new devices needed (`LEDMock`/`Ws2812`), both firmware families, live-boot-verified; documents that its marketing copy's NeoPixel-power-pin claim is contradicted by firmware source
- [x] [0068] Waveshare RP2040-Zero board | first board picked up off [0066]'s survey; zero new devices needed (`Ws2812` on GPIO16, already exists since [0062]), both firmware families, live-boot-verified
- [x] [0067] Claude Code skill for adding `ExternalDevice`s/boards | `.claude/skills/external-devices-and-boards/`; execution layer on top of [0049]'s reference doc, `.gitignore` fixed so it's actually tracked
- [x] [0063] `RPPIO` paces state machines by `SM_CLKDIV` and `[delay]` | both halves landed as a due-time skip; unblocks pulse-width protocols and fixes CYW43's gSPI clock, ~11% faster. Ceiling kept: one instruction per CPU instruction, which [0043] depends on
- [x] [0062] YD-RP2040 board + the `Ws2812` device | device and board landed and live-verified; the PIO-driven live decoding it was open for works since [0063]
- [x] [0059] firmware resolution inside `BoardSpec`: one path for `--board` and `--board-spec` | `firmware` keyed by family, resolved at use time; `boards/` is one directory per board; `--image`/`--fetch-fw-only` now work with `--board-spec`
- [x] [0049] document external devices/boards + how a user writes their own | all 5 phases landed; `reference/external-devices-and-boards.md` is the how-to
- [x] [0056] `St7735s` external device + Waveshare RP2040-LCD-0.96 board | first board whose point is its device; MADCTL geometry + RGB444/666 in the addendum
- [x] [0055] `v0.2.2`/`v0.2.3` publish-workflow hang | fixed; confirmed by the real `v0.2.5` release
- [x] [0027] CYW43439 / Pico W WiFi (epic) | steps 0-3g done and live-boot-verified; step 4 landed via [0048], whose own gaps are tracked there
- [x] [0047] CYW43 pure-Python hot path + `GPIOPin`/`RPPIO` Cython ports | ~2.6x to `scan()`; closes [0031]'s last gap
- [x] [0050] CircuitPython 10.x boot stall root cause + fix | `PADS_QSPI` reset values - `GPIO_QSPI_SS` needs its pull-up
- [x] [0054] CYW43 `disconnect()` root cause + fix | closes the only correctness bug among [0048]'s gaps
- [x] [0052] XIP_CTRL: implement the registers, not the cache | no cache modelled on purpose
- [x] [0051] BOOTSEL button as an `ExternalDevice` | on `GPIO_QSPI_SS`, active-low, released via `release_input()`
- [x] [0046] `epd2in9g` virtual e-paper promoted to `ExternalDevice` | ported off a stale branch, no Pillow in `src/`
- [x] [0040] `native/_simulator.pyx`: `libc.math.INFINITY` for `float("inf")` | kept; the `cpython.time` half was reverted (limited-API floor)
- [x] [0044] DMA-driven SPI TX/RX hang fix | stale DREQ cache + same-tick alarm starvation; unrelated to [0027]
- [x] [0043] `RPPIO` CTRL-enable first-batch/DMA-refill race fix | fixes v1.23.0's CYW43 boot
- [x] [0042] `GSPIBus` `SPI_INTERRUPT_REGISTER` write-1-to-clear fix | fixes a spurious bus-error warning
- [x] [0041] CYW43 live-boot freeze root cause + fix | unblocked [0027]'s 3g verification
- [x] [0039] `SimulationClock` native Cython port | ~2.7x synthetic; closes [0034]'s leftover gap
- [x] [0038] `GSPIBus` ioctl-response zero-fill fix | the real root cause of `nic.active(True)`
- [x] [0037] couple `RPPIO` stepping to the CPU's instruction loop | fixes the CYW43 native-mode livelock
- [x] [0036] `--littlefs`/`--fat12` mutual exclusivity | explicit validation instead of a silent drop
- [x] [0035] board-aware MicroPython/CircuitPython/Kaluma FS flash offset | fixes the pico_w wild-execution crash (its CircuitPython `pico_w` offset corrected by [0085] on 2026-08-19 - see this record's own appended Correction)
- [x] [0034] `_execute_batch()` native Cython port | follow-up of [0013]/[0031]
- [x] [0033] shell autocompletions for the CLI | accepted
- [x] [0032] documentation restructure | this scheme
- [x] [0031] PIO Cython + `clock.tick()` batching | follow-up of [0013]
- [x] [0030] `ExternalDevice` concurrency model | accepted
- [x] [0029] CYW43 board composition | accepted
- [x] [0028] CYW43 module layout | accepted
- [x] [0026] main-thread asyncio | 5 phases landed
- [x] [0025] full asyncio migration | phases 1-5 · **supersedes [0014]**
- [x] [0023] Termux mpremote compat | `#30`
- [x] [0022] mpremote REPL over `socket://` | `#27`
- [x] [0021] unified shutdown coordinator | Ctrl+X / `--expect-text` / SIGTERM
- [x] [0020] PTY / serial passthrough (`--pty`) | landed
- [x] [0019] asyncio idle-yield latency | `#23`
- [x] [0013] Cython interpreter core | ~4x, on by default `#20`
- [x] [0012] CDC (USB serial) performance | root-caused + fixed
- [x] [0011] MicroPython 1.21-vs-1.28 gap | upstream, not our bug
- [x] [0010] littlefs persistence → `--dump-fs` | `#24`
- [x] [0009] simulator fix | `#19`
- [x] [0008] SSI flash-write support | `#18`
- [x] [0007] configurable bootrom (`--bootrom`, B0/B2) | `#16`
- [x] [0006] GPIO pull-up/down on floating pin | `#13`
- [x] [0005] Kaluma firmware support | `#12`
- [x] [0004] buffer overflow / race conditions | `#7`
- [x] [0002] mklittlefs image handling | `#6`
- [x] [0001] CLI tool + public device API | `#3,#5,#10`

### Rejected / Superseded

- [ ] [0086] a FAT12 library dependency + a `mkfat12` CLI subcommand | **rejected (2026-08-20)** - [0087]'s firmware-written CIRCUITPY gives LFN and subdirectories for free; `demo/mkfat12.py`'s offline 8.3 builder stays for tests and fast runs
- [ ] [0045] CYW43 step 4 NAT bridge via `gVisor`'s `pkg/tcpip` | **Superseded → [0048]** - kept for its research trail
- [ ] [0016] basic-block fusion / mini-JIT | **Rejected** - net negative in every integration attempt
- [ ] [0015] HLE memcpy hook | **Rejected** - net negative (measurements kept)
- [ ] [0014] threading model | **Superseded → [0025]**

## Notes (no state row — linked from ideas)

- [0003] littlefs image format vs. old MicroPython → [0002], [0010]
- [0017] performance: pure-Python vs V8 → [0011], [0013], [0015], [0016]
- [0018] raw-REPL cross-thread `tx_fifo` (postmortem) → [0014]
- [0024] CYW43439 protocol reverse-engineering → [0027], [0028], [0029], [0030]

## Reference (living, unnumbered)

- [reference/porting-checklist.md](reference/porting-checklist.md) — port checklist + minor known differences
- [reference/mpremote.md](reference/mpremote.md) — using mpremote with rp2040py
- [reference/os-compatibility.md](reference/os-compatibility.md) — OS × feature compatibility matrix
- [reference/external-devices-and-boards.md](reference/external-devices-and-boards.md) — writing your own `ExternalDevice`/`BoardSpec`, the [0049](records/0049-external-device-authoring-docs.md) how-to
- [../demo/README.md](../demo/README.md) — what each demo script does, and a gallery of real emulator output from the two display panels ([0046]/[0056]), checked in under `demo/screenshots/`

## Record links

Reference-style link targets for every `[NNNN]` used above (records are immutable/append-only,
so a number's target never changes). Keep this sorted by number and add a row whenever a new
record is added.

[0001]: records/0001-cli-device-api.md
[0002]: records/0002-mklittlefs-image.md
[0003]: records/0003-littlefs-image-format.md
[0004]: records/0004-buffer-overflow-races.md
[0005]: records/0005-kaluma-firmware.md
[0006]: records/0006-gpio-pull-floating.md
[0007]: records/0007-bootrom-revisions.md
[0008]: records/0008-ssi-flash-write.md
[0009]: records/0009-simulator-fix.md
[0010]: records/0010-littlefs-dump-fs.md
[0011]: records/0011-mp-1.21-vs-1.28-gap.md
[0012]: records/0012-cdc-performance.md
[0013]: records/0013-cython-core.md
[0014]: records/0014-threading-model.md
[0015]: records/0015-memcpy-hle.md
[0016]: records/0016-jit-fusion.md
[0017]: records/0017-perf-python-vs-v8.md
[0018]: records/0018-raw-repl-txfifo.md
[0019]: records/0019-asyncio-idle-yield.md
[0020]: records/0020-pty-serial-passthrough.md
[0021]: records/0021-shutdown-coordinator.md
[0022]: records/0022-mpremote-socket-repl.md
[0023]: records/0023-termux-mpremote.md
[0024]: records/0024-cyw43-protocol.md
[0025]: records/0025-full-asyncio-migration.md
[0026]: records/0026-main-thread-asyncio.md
[0027]: records/0027-cyw43-wifi.md
[0028]: records/0028-cyw43-module-layout.md
[0029]: records/0029-cyw43-board-composition.md
[0030]: records/0030-external-device-concurrency.md
[0031]: records/0031-pio-cython-tick-batching.md
[0032]: records/0032-docs-restructure.md
[0033]: records/0033-completions-and-validation.md
[0034]: records/0034-execute-batch-native-port.md
[0035]: records/0035-board-aware-fs-flash-offset.md
[0036]: records/0036-littlefs-fat12-exclusivity.md
[0037]: records/0037-pio-clock-coupled-stepping.md
[0038]: records/0038-cyw43-ioctl-response-zero-fill.md
[0039]: records/0039-simulation-clock-native-port.md
[0040]: records/0040-time-monotonic-vs-cimport.md
[0041]: records/0041-cyw43-post-data-header-freeze-fix.md
[0042]: records/0042-cyw43-interrupt-register-w1c-fix.md
[0043]: records/0043-pio-dma-first-batch-race.md
[0044]: records/0044-spi-dma-tx-rx-starvation-fix.md
[0045]: records/0045-cyw43-nat-libslirp-cython.md
[0046]: records/0046-epd2in9g-external-device.md
[0047]: records/0047-cyw43-pio-gpio-hotpath.md
[0048]: records/0048-cyw43-nat-reflector.md
[0049]: records/0049-external-device-authoring-docs.md
[0050]: records/0050-qspi-pad-reset-values.md
[0051]: records/0051-bootsel-button.md
[0052]: records/0052-xip-ctrl-registers.md
[0053]: records/0053-core1-and-inter-core-fifo.md
[0054]: records/0054-cyw43-disassoc.md
[0055]: records/0055-rp2040-factory-throttle-test-hang-fix.md
[0056]: records/0056-st7735s-waveshare-lcd-board.md
[0057]: records/0057-run-pin-reset-hook.md
[0059]: records/0059-boardspec-firmware-resolution.md
[0060]: records/0060-external-io-bridges.md
[0061]: records/0061-cli-family-flag.md
[0062]: records/0062-yd-rp2040-board-and-ws2812.md
[0063]: records/0063-pio-clkdiv-and-delay-cycles.md
[0064]: records/0064-state-server-and-web-visualizer.md
[0065]: records/0065-queued-exec-erroring-flaky-test.md
[0066]: records/0066-board-support-expansion.md
[0067]: records/0067-external-devices-and-boards-skill.md
[0068]: records/0068-waveshare-rp2040-zero-board.md
[0069]: records/0069-adafruit-feather-rp2040-board.md
[0070]: records/0070-adafruit-itsybitsy-rp2040-board.md
[0071]: records/0071-adafruit-qtpy-rp2040-board.md
[0072]: records/0072-w5500-ethernet-and-board.md
[0073]: records/0073-garatronic-pybstick26-rp2040-board.md
[0074]: records/0074-machdyne-werkzeug-board.md
[0075]: records/0075-nullbits-bit-c-pro-board.md
[0076]: records/0076-pimoroni-picolipo-board.md
[0077]: records/0077-pimoroni-tiny2040-board.md
[0078]: records/0078-waveshare-rp2040-plus-board.md
[0079]: records/0079-seeed-xiao-rp2040-board.md
[0080]: records/0080-sparkfun-promicro-board.md
[0081]: records/0081-waveshare-rp2040-one-board.md
[0082]: records/0082-waveshare-rp2040-tiny-board.md
[0083]: records/0083-0xcb-gemini-board.md
[0084]: records/0084-0xcb-helios-board.md
[0085]: records/0085-circuitpython-code-py-and-wifi-on-screen.md
[0086]: records/0086-fat12-library-and-a-mkfat12-subcommand.md
[0087]: records/0087-circuitpython-writable-circuitpy-over-the-raw-repl.md
[0088]: records/0088-usb-host-side-msc-control-lines-and-reset.md
