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
- **`tasks/NNN.md`** — working notes for investigations that aren't root-caused yet. Not
  numbered, not immutable, and **not permanent**: once a note is resolved it is folded into the
  record that resolves it (verbatim, as an `## Appendix: folded-in working note ...` section) and
  the file is deleted. Only still-open notes live in `tasks/`, and every one of them has a row in
  "In progress / Proposed" below. Records written before a fold-in still cite the old
  `docs/tasks/<name>.md` path — those references resolve to the appendix of whichever record the
  note was folded into (records are append-only, so the citations themselves are left as written).
  Folded in so far, all on 2026-08-16: `3g-scripted-scan-join` → [0027],
  `cyw43-3g-live-boot-verification` → [0027],
  `cyw43-post-data-header-freeze` → [0041],
  `main-spi-hang` → [0044],
  `simulation-clock-cython-port` → [0039],
  `circuitpython-10x-boot-stall` → [0050].
  (`littlefs-fat12-exclusivity`, cited by [0036], was
  deleted before this convention existed and was **not** preserved anywhere.)
- **`reference/`** — living how-to / checklists. Not numbered.

## Ideas

### Implemented

- [x] [0001] CLI tool + public device API | `#3,#5,#10`
- [x] [0002] mklittlefs image handling | `#6`
- [x] [0004] buffer overflow / race conditions | `#7`
- [x] [0005] Kaluma firmware support | `#12`
- [x] [0006] GPIO pull-up/down on floating pin | `#13`
- [x] [0007] configurable bootrom (`--bootrom`, B0/B2) | `#16`
- [x] [0008] SSI flash-write support | `#18`
- [x] [0009] simulator fix | `#19`
- [x] [0010] littlefs persistence → `--dump-fs` | `#24`
- [x] [0011] MicroPython 1.21-vs-1.28 gap | resolved — upstream, not our bug
- [x] [0012] CDC (USB serial) performance | root-caused + fixed
- [x] [0013] Cython interpreter core | ~4x, on by default `#20`
- [x] [0019] asyncio idle-yield latency | `#23`
- [x] [0020] PTY / serial passthrough (`--pty`) |
- [x] [0021] unified shutdown coordinator | Ctrl+X / --expect-text / SIGTERM
- [x] [0022] mpremote REPL over `socket://` | `#27`
- [x] [0023] Termux mpremote compat | `#30`
- [x] [0025] full asyncio migration | phases 1–5 · **supersedes [0014]**
- [x] [0026] main-thread asyncio | 5 phases landed
- [x] [0028] CYW43 module layout | accepted
- [x] [0029] CYW43 board composition | accepted
- [x] [0030] ExternalDevice concurrency model | accepted
- [x] [0031] PIO Cython + `clock.tick()` batching | follow-up of [0013]
- [x] [0032] documentation restructure | this reorganization
- [x] [0033] Add autocompletions for the cli tool | accepted
- [x] [0034] `_execute_batch()` native Cython port | follow-up of [0013]/[0031]
- [x] [0035] board-aware MicroPython/CircuitPython/Kaluma FS flash offset | fixes pico_w wild-execution crash
- [x] [0036] `--littlefs`/`--fat12` mutual exclusivity | explicit validation, matches `--tcp-port`/`--pty` pattern
- [x] [0037] couple `RPPIO` stepping to the CPU's own instruction loop | fixes CYW43 native-mode livelock, follow-up of [0031]/[0034]
- [x] [0038] `GSPIBus` ioctl-response zero-fill fix | fixes `nic.active(True)` real root cause (was misdiagnosed as throughput-only)
- [x] [0039] `SimulationClock` native Cython port | follow-up of [0013]/[0031]/[0034], closes [0034]'s own leftover gap; ~2.7x on a synthetic busy-spin benchmark, `powersave` governor caveat, literal [0038] repro not re-run
- [x] [0041] CYW43 live-boot freeze root cause + fix | fixes the `cyw43-post-data-header-freeze` working note (folded into [0041]'s own appendix); unblocks [0027] step 3g live-boot verification
- [x] [0042] `GSPIBus` `SPI_INTERRUPT_REGISTER` write-1-to-clear (W1C) fix | fixes spurious `[CYW43] Bus error condition detected 0xb9` warning during live boot
- [x] [0043] `RPPIO` CTRL-enable first-batch/DMA-refill race fix | fixes MicroPython v1.23.0's CYW43 boot (`scan()` raising `EPERM`), follow-up of [0037]
- [x] [0044] DMA-driven SPI TX/RX hang fix | fixes the `main-spi-hang` working note, folded into [0044]'s own appendix (stale DREQ cache after `RPDMA.reset()` + same-tick `SimulationClock` alarm starvation), confirmed unrelated to CYW43/[0027]
- [x] [0040] `native/_simulator.pyx` hot loop: `libc.math.INFINITY` for `float("inf")` | kept; `cpython.time cimport monotonic` was also tried but **reverted** - broke real CI (`PyTime_t` not in the limited-API surface until 3.13, this project's `Py_LIMITED_API` floor is 3.11), back to plain `time.monotonic()`, follow-up of [0031]/[0034]/[0039]
- [x] [0046] `epd2in9g` virtual e-paper ported forward + promoted to `ExternalDevice` | ported from the stale `component/epd2in9g` branch (fixes API drift vs. async-native `MicroPythonDevice`/`utils/firmware_retrieve.py`), moved `src/rp2040py/external/epd2in9g.py` alongside `external/cyw43/`, no Pillow dependency in `src/`, follow-up of [0029]/[0030]
- [x] [0051] BOOTSEL button as an `ExternalDevice` | wired to `GPIO_QSPI_SS` (not any GPIO) and active-low, identically on both boards, so `boards.py` attaches it unconditionally; `release()` hands the pad back to its pull-up via a new `GPIOPin.release_input()` rather than driving it high - forcing it high would read the same but would mask a regression in the very pad defaults ([0050]) this device exists to exercise
- [x] [0052] XIP_CTRL: implement the registers, not the cache | no cache is modelled on purpose (nothing observable would change); the registers exist so firmware polling them stops reading `BasePeripheral`'s `0xFFFFFFFF`, where every status bit looks set - the same hazard class that made `CHIP_RESET` look like a permanent psm_restart. Explicitly **not** what fixed CircuitPython 10.x
- [x] [0054] CYW43 `disconnect()` root cause + fix | closes the only real correctness bug among [0048]'s remaining gaps; ioctl/event shapes derived from the vendored `cyw43-driver` source rather than guessed, per [0027]'s own 3g rule
- [x] [0050] CircuitPython 10.x boot stall root cause + fix | `PADS_QSPI` reset values: `GPIO_QSPI_SS` needs its **pull-up** (`0x5A`), not bank0's pull-down default, or an undriven SS reads low forever - a permanently-held BOOTSEL button. 10.x polls it from a RAM-resident loop during boot and hung; 9.x/8.x never did, so the defect was latent for years. Also folds in the `circuitpython-10x-boot-stall` working note, and keeps two unrelated defects found on the way (SEV never set the event register; `VREG_AND_CHIP_RESET` was unimplemented so `CHIP_RESET` read all-ones) - both proven by ablation *not* to be this bug
- [x] [0047] CYW43 pure-Python hot path: `check_changed_pins` fix + `GPIOPin`/`RPPIO` Cython ports | ~2.6x on CYW43 `pico_w` boot-to-`scan()`; `RPPIO` satisfies the `Peripheral` Protocol (can't inherit `BasePeripheral`), one-way `_pio`→`_gpio_pin`/`StateMachine` cimport (mutual cimport reverted — circular runtime import), abi3-verified on 3.14, `.pyi` stubs for `native.*` (surfaced+fixed the `IClock` protocol gap + `key_mock.drive_input` latent bug); PyPy still ~1.16x ahead — finishes [0031]'s "not yet tried #4", follow-up of [0013]/[0034]/[0039]
- [x] [0027] CYW43439 / Pico W WiFi (epic) | steps 0-3g done, live-boot-verified on both v1.23.0 and v1.28.0 (2026-08-13/14, see [0042]/[0043]); step 4 (real network bridge, including DNS) implemented + live-boot-verified via [0048] (2026-08-16), **which is still open under "In progress / Proposed" for its own "Known gaps"** — this epic's own steps are done, step 4's remaining work is tracked on [0048]'s row, not here; `main-spi.py` hang fixed but confirmed unrelated to this epic, see [0044]
- [x] [0055] `v0.2.2`/`v0.2.3` publish-workflow hang: `asyncio.Server.wait_closed()`'s 3.12.1 fix exposing a missing flow close | **Fixed and confirmed by a real release: `v0.2.5` published 2026-08-17** ([release](https://github.com/o-murphy/rp2040py/releases/tag/v0.2.5), and `v0.2.4` before it), so the `publish.yml` run this row was waiting on completed cleanly through `deploy` - the row's former "watch a real run finish" checkbox is what that closes. First pass fixed a real but secondary issue (unthrottled `RP2040()` construction in new CYW43 test coverage bypassing `conftest.py`'s memory-throttling `rp2040_factory` semaphore) - landed, but a `workflow_dispatch` on top of it hung identically, so it wasn't the actual blocker. Real cause, found via a `PYTHONFAULTHANDLER`+`SIGABRT` stack trace and local repro: `test_cyw43_nat.py::test_reset_clears_flows_so_a_reused_port_can_connect_again` never closed its second (post-reset) flow's connection before `echo_server.wait_closed()` - harmless on `cp310`/`cp311` (`wait_closed()` was buggy pre-3.12.1, ignored active connections), a permanent hang on `cp313`+ (`cp314t` on Linux/Windows/macOS, `cp313`/`cp314` on Android - not free-threading-specific, confirmed hanging under plain `--python 3.14` too). This project's regular CI/`pre-commit` never runs the full suite under `cp313`+ at all, so this was invisible until the first release cut since `test_cyw43_nat.py` landed. Fixed by closing the second flow before `wait_closed()`; `v0.2.2`/`v0.2.3` abandoned as dead tags (nothing published under either)
- [x] [0056] `St7735s` external device + `WAVESHARE_RP2040_LCD_0_96` board spec | the first community board whose point *is* a device: `boards/micropython/WAVESHARE_RP2040_LCD_0_96/` attaches an emulated ST7735S (`external/st7735s.py`, new - CASET/RASET/RAMWR/MADCTL/COLMOD decoded off the vendor's own MicroPython driver, `on_frame` handing raw RGB565 bytes, no Pillow in `src/` - same boundary as [0046]) alongside `BootselButton` and an `LEDMock` standing in for the LCD backlight on GPIO25. Flash numbers derived from upstream (`MICROPY_HW_FLASH_STORAGE_BYTES = 1441792`, pico-sdk `PICO_FLASH_SIZE_BYTES = 2 MiB` -> `fs_start=0xa0000`, `fs_blockcount=352`) and **live-verified** against the real `WAVESHARE_RP2040_LCD_0_96` v1.28.0 image: littlefs round trip through `mklittlefs --board-spec`, `os.statvfs('/')` = `(4096, 4096, 352, ...)`, and two frames drawn by a vendor-shaped guest driver decoded into correct 160x80 pictures. Names the directory after MicroPython's own board id (not a vendor-level `waveshare/`), and re-surfaces [0049]'s open "`BoardSpec.extras` hands the caller no device handle" gap - worked around with a `board_with(on_frame)` helper in the board file, not solved globally. **Addendum, same day**: two of the record's three deliberate non-goals closed on request - `MADCTL` geometry (MV/MX/MY) is now really applied, anchored so the module's own `REFERENCE_MADCTL = 0xA8` is the identity (re-verified byte-identical against real firmware), and RGB444/RGB666 are decoded and normalized into the same RGB565 buffer; backlight brightness stays documented-only (a GPIO listener counts PWM edges, not duty). The RESET button became [0057]. **Second addendum**: `boards/circuitpython/waveshare_rp2040_lcd_0_96/` - the same physical board under CircuitPython `10.2.1` (own flash layout, `fs_start=0x100000` derived from `CIRCUITPY_FIRMWARE_SIZE`+NVM with no board override), which needed **no** new device code and doubles as an independent check of the MADCTL work: CircuitPython initialises the panel itself in `board_init()` (`MADCTL=0xC8`, `colstart=26/rowstart=1`, displayio `rotation=90` - the transposed counterpart of MicroPython's `0xA8`/`1`/`26`), and its console renders upright through the same fixed glass mapping. Live-verified: 58 frames off a boot with zero guest code, plus `board.DISPLAY` 160x80/rot 90 and a formatted CIRCUITPY drive over the CLI's `--circuitpython --board-spec` path; frames from both firmwares are checked in under `demo/screenshots/` and shown in [../demo/README.md](../demo/README.md)
- [x] [0049] document external devices/boards + how a user writes their own | all 5 phases of the `BoardSpec` board-authoring design landed 2026-08-16/17: `boards.py` gained `layout`/`image`/`FlashLayout`/`resolve_board_spec()`/`build_rp2040_from_spec()`; `load_flash.py`'s six functions take a resolved layout instead of a board-name string; `BaseDevice`/`MicroPythonDevice`/`KalumaDevice`'s breaking constructor change (keyword-only `board: BoardSpec`, no more separate `image` arg) shipped without a deprecation shim (no doc had promised the old shape as stable API); the CLI's `--board-spec target:attr`/`RP2040PY_BOARD_SPEC` shipped on `micropython`/`kaluma`/`mklittlefs`/`run` (`bench` excluded, nobody's asked); new test coverage (`tests/test_boards.py`, `tests/test_cli_board_spec.py`) plus the actual how-to doc, [reference/external-devices-and-boards.md](reference/external-devices-and-boards.md), linked from `README.md`. Live-verified against real MicroPython `1.28.0` firmware, locally and via CI (`tests/pico_spec.py` + `ci-micropython.yml`'s `test-board-spec` job, both `--board-spec` and the env var). Two narrow design questions remain genuinely open (not blockers): whether `ExternalDevice`'s attach-only surface is public-API-ready, and whether `demo/eink_run.py` gets its own dedicated example beyond being cited from the how-to — see the record's own closing section. **Addendum, 2026-08-17**: `boards/micropython/WEACTSTUDIO/` added as a real, live-verified `--board-spec` community-board example (WeAct Studio RP2040, not "YD-RP2040" - a different board; 4 flash-size variants, derived from real upstream MicroPython board headers, not guessed), which surfaced and fixed a real bug in `external/key_mock.py` (`release()` was force-driving the pin instead of `GPIOPin.release_input()`, the same masking-a-firmware-bug class `bootsel_button.py` was already written to avoid) - see the record's own "Addendum" section for the full story, including a `--board-spec` package-loading design that was built then deliberately reverted in favor of the dotted-module form (`PYTHONPATH=.`, "like `-m`")

- [x] [0059] firmware resolution inside `BoardSpec`: one path for `--board` and `--board-spec` | landed 2026-08-17 exactly as designed, bar one deliberate departure (below). `BoardSpec` gained one declarative field, `firmware: dict[family, BoardFirmwareSpec]` (an *existing* type, keyed by the family names `firmware_specs.json` already uses), and resolution moved from import time to use time: `resolve_firmware(spec, family, image=None)` is now the single path both `--board` and `--board-spec` come through (`BOARDS["pico"]`/`["pico_w"]` carry `firmware` straight from `firmware_specs.json`; `resolve_board_spec()` survives as a thin wrapper over it, still adapting a caller-built `FirmwareSpec` - including BOOTROM's `known_versions` shape), and `resolve_layout()` is its image-free half, so `mklittlefs --board-spec` needs no network at all. Closes two live defects rather than fixing them separately: no board file downloads on `import` any more (which is what made offline `--board-spec` need cache-seeding during [0056]), and `--image`/`--fetch-fw-only` now work with `--board-spec` - superseding one row of [0049]'s flag table. `boards/` reorganised to one directory per *board*: [0056]'s board is now a single two-family file (`boards/waveshare_rp2040_lcd_0_96/`, MicroPython + CircuitPython off identical `extras`), `WEACTSTUDIO` became `boards/weactstudio/` and immediately used the new field for real - it gained a **CircuitPython** declaration (upstream ships that board under a different id, `weact_studio_pico`, which is exactly the "two firmwares disagree on the id" case the naming rule anticipated) - and the per-family directories are gone along with `pyproject.toml`'s `N999` exemption for them. **Departure from the record's own flag table**: `--target` + `--board-spec` on `mklittlefs` is now *allowed* and selects the family - once one spec carries two families with different layouts, something has to pick, and `mklittlefs` has no `--circuitpython` flag; it is only needed when a spec declares more than one, and declaring several without it is a clear error rather than a silent pick. Live-verified offline against real firmware: both families of the Waveshare board boot from the one file (MicroPython `1.28.0`; CircuitPython `10.2.1` with `board.DISPLAY` 160x80), `--board-spec --image <tag>` boots, an `mklittlefs --board-spec --target micropython` image round-trips back through a boot (`os.statvfs('/')` = `(4096, 4096, 352, 350)`), and `boards/weactstudio/` boots under both its families too (MicroPython `1.28.0` reporting the 16 MiB variant's 3840 blocks; CircuitPython `10.2.1` with `board.board_id == "weact_studio_pico"` and CIRCUITPY mounted). `ci-micropython.yml`'s `test-board-spec` job covers the same ground. Unblocks [0061] (its step 1 is now nearly free) and is the precondition the record names for answering "ship community boards in the package" at all - the promotion checklist for `boards.BOARDS` is in the record, along with its rejection of a third `community` tier

### In progress / Proposed

- [ ] [0053] second core (core1) + inter-core FIFO | **Proposed, nothing implemented.** `sio.py` has no `FIFO_ST`/`FIFO_WR`/`FIFO_RD` and there is no core1. Key point in the record: adding the registers *alone* is worse than leaving them out - `multicore_launch_core1()` blocks reading back an echo, so a faithful-looking FIFO turns today's honest warning into a silent infinite hang. Concrete trigger for building it: MicroPython's `_thread`, which runs on core1. The limitation is now stated user-facing (2026-08-17): an IMPORTANT callout in `README.md` and a host-independent ❌ row + note in [reference/os-compatibility.md](reference/os-compatibility.md)
- [ ] (no record yet) `test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it` flaky on CI | not root-caused - see [docs/tasks/queued-exec-erroring-flaky-test.md](tasks/queued-exec-erroring-flaky-test.md); observed on Windows + Ubuntu `pre-commit` CI (2026-08-14), unrelated to [0040]/[0044]
- [ ] [0048] CYW43 step 4 NAT bridge: custom minimal hand-rolled TCP reflector + UDP relay (supersedes [0045]'s engine choice) | **4a-4e implemented, live-boot-verified on v1.23.0/v1.28.0, and merged** ([PR #37](https://github.com/o-murphy/rp2040py/pull/37), 2026-08-16, `9f5348f`, all 62 checks green incl. `pre-commit` on `windows-latest`) — **but this row stays open until the record's own "Known gaps" section is closed**, since a working happy path is not the same as a complete WiFi emulation. What landed: guest-facing leg reuses `bus.py`'s own lossless in-order FIFO (so no retransmission/congestion-control/reassembly needed), host-facing leg is a real `asyncio` socket; `main-cyw43.py` completes a real TCP round trip to `1.1.1.1:80`, `mip.install()` resolves DNS and installs a real package, `ntptime.settime()` round-trips real NTP. Same-day follow-up closed 3 gaps (real-RST-vs-clean-FIN, connect-timeout/flow-table leak, DNS-only→general UDP) plus a latent `except TimeoutError` bug that silently never fired on Python 3.10, and fixed **two separate** `windows-latest` CI flakes (a fixed-`asyncio.sleep()` race in the test suite; the RST test's `SO_LINGER` real-kernel-RST trick not reliably surfacing as `ConnectionResetError` through Windows' asyncio). Still open, full inventory in the record itself:
  - [x] ~~**`disconnect()` is a no-op**~~ — **fixed 2026-08-16** in [0054]: `WLC_DISASSOC` (52, derived from `CYW43_IOCTL_SET_DISASSOC`'s `cmd >> 1` encoding) now gets a scripted `CYW43_EV_DISASSOC`/`CYW43_EV_LINK(down)` pair, and also resets the NAT bridge - a flow outliving its association swallowed the guest's next SYN on a reused port triple. Live-verified on v1.23.0/v1.28.0: `isconnected()` `True`→`False`, `status()` `3`→`0`
  - [ ] **no window backpressure from the real destination socket** — accepted v1 simplification, not an oversight: shrink the advertised guest-facing window by `transport.get_write_buffer_size()` if a slow real destination + fast guest sender ever proves it matters
  - [ ] **unbuilt: AP mode, multi-AP/hidden-SSID/negative-auth scan results, IPv6, multi-guest** — none partially-done; join is scripted unconditionally, so a *wrong* password currently "succeeds" too. Guest/gateway IP+MAC are fixed module constants in `nat.py` with no config surface
  - [x] ~~unverified: real TLS/HTTPS + WebSocket end-to-end~~ — **verified 2026-08-16** on both `v1.23.0` and `v1.28.0`: real TLS to `micropython.org:443` (real cert chain), RFC 6455 WebSocket and WebSocket-over-TLS against an echo server on a non-loopback address. TLS step landed in `tests/micropython/main-cyw43.py`; WebSocket verified by hand, deliberately not wired into CI (see the record)
  - [x] ~~unverified: CircuitPython live boot~~ — **verified 2026-08-16** against CircuitPython `9.2.9` on `--board pico_w`: scan/join/DHCP/TCP/DNS all work through `wifi`/`socketpool`. Landed `tests/circuitpython/main-cyw43.py` + `.github/workflows/ci-circuitpython.yml` (the project's first CircuitPython CI). Found a separate, unrelated blocker on the way — see the CircuitPython 10.x row below

- [ ] [0060] external I/O bridges: an `ExternalDevice` with one foot outside the emulator | **Deferred, not rejected - documented, nothing started.** One record for two asks that are the same class of thing: a web bridge so the MIT-licensed `@wokwi/elements` components can render emulated pins/displays in a browser, and an `RPZeroGPIO` mapping emulated pins onto a real host's GPIO via `gpiozero`/`lgpio` (unit-testable through `gpiozero`'s `MockFactory`). Both hit the same three walls, decided once here: outbound changes must be *coalesced snapshots* at wall-clock rate rather than one message per edge (PWM/PIO produce kHz edge rates, and [0046]'s measurements show how little headroom there is), inbound events must go through `schedule_threadsafe()` ([0030]), and each needs an optional dependency extra imported inside `attach()` (the `[fs]` precedent). Records the honest ceiling for the GPIO bridge - LEDs/buttons/relays yes, WS2812/servo/bit-banged buses no, since the emulator runs ~20-30x slower than real time and in bursts - and names the more valuable sibling: bridging SPI/I2C *transactions* (tapping `spi.on_transmit`, as `Epd2in9G` already does) instead of pin edges
- [ ] [0061] one firmware command with `--family`, instead of `micropython` + `kaluma` | **Deferred, documented, nothing implemented - depended on [0059]**, which landed 2026-08-17 and made `family` a real key instead of a branch into separate `FirmwareSpec` constants (the CLI's own `_resolve_board()` already takes a family *name* now, so step 1 is nearly free). Finding: the two subcommands already share ~all their flags via `_shared_arg_parser()`, and the axis they really differ on is not the firmware family but the *protocol* - `MicroPythonDevice` has the raw-REPL runner (serving both MicroPython and CircuitPython, which is why CP is a boolean flag on a command named after another firmware), while `KalumaDevice` has no exec surface at all. Proposes step 1 regardless: `--family {micropython,circuitpython}` replacing the `--circuitpython` boolean; step 2 (one `fw` command, old names as aliases) only with a hand-validated per-family flag matrix, the way [0036]/[0049] already validate; step 3 (full merge) only if `KalumaDevice` ever gains script execution. Names the trap: the positional argument means *execute* for `.py` and *stage into flash* for `.js`, so a merge has to split it into explicit `--exec`/`--program`
- [ ] [0062] YD-RP2040 (VCC-GND Studio) board + the `Ws2812` device it needs | **Proposed, nothing implemented.** The board is a Pico plus USB-C, a PWR LED, a RESET button ([0057]), a USRKEY button on GPIO24 and - the only genuinely new part - a **WS2812 RGB LED on GPIO23**, which nothing in this project can model. Confirmed *not* WEACTSTUDIO (different vendor, USR button on 24 not 23, and this one has the RGB LED WeAct's lacks), the distinction [0049]'s addendum first drew. Numbers derived from the upstream CircuitPython port (`vcc_gnd_yd_rp2040`): no `CIRCUITPY_FIRMWARE_SIZE` and no `link.ld`, so `fs_start=0x100000`; `MICROPY_HW_NEOPIXEL = GPIO23`, i.e. that LED is CircuitPython's *status* indicator. Two findings the record turns on: (1) **MicroPython has no port for this board**, and whether a board file may declare someone else's image (the generic `RPI_PICO` build, which runs here but freezes the FS at 2 MiB geometry) is a real question - proposed rule, a `firmware` key means "built *for* this board", not "runs here", so declare `circuitpython` only and leave MicroPython to an explicit `--image`; (2) **emulating WS2812 is not what [0060] ruled out** - that ceiling is about driving *real* hardware in wall-clock time, while a decoder reads edges in emulated time, where PIO is cycle-coupled to the CPU ([0037]). Measured as evidence: the stock CircuitPython `10.2.1` image boots in the emulator today (`--board pico --image <file>`, no board file), CIRCUITPY mounts at the derived offset, and **GPIO23 sees 606 edges in ~8.5 s of boot with zero guest code** - so the device would be testable against real firmware, not just a hand-written stimulus. Would live in `boards/` as an example, not `boards.BOARDS` ([0059]'s promotion checklist: no `firmware_specs.json` entry, no named maintainer). **Addendum, same day**: the WS2812 timings to build against come from CircuitPython's own driver (`ports/raspberrypi/common-hal/neopixel_write/__init__.c`), not from datasheet folklore - its PIO program at 12.8 MHz gives exactly 312 ns high for a `0` and 703 ns for a `1` on a 1.25 us period, so the decoder classifies on high-time with a ~500 ns threshold (~190 ns margin either side, wider than the +/-1 PIO cycle a differently-clocked driver shifts by), and CircuitPython enforces a **300 us** inter-frame gap where the datasheet asks 50 us, making latch detection unambiguous. Wire format is MSB-first per byte, so GRB/GRBW colour order is a layer above it - a constructor argument, not an inference. **Second addendum**: USRKEY's wiring resolved from the vendor schematic (YD-2040 2022 V1.1) - `GPIO24 -[R13 10k]- switch -> GND`, with C18 100n as a hardware debounce and **no external pull-up**, so it is active-low *and* its released level comes entirely from the RP2040's internal pull. `KeyMock(gpio=24, active_high=False)` is right, and the board is a sharper test than `weactstudio` of why `release()` must hand the pad back to firmware's own pull ([0051]/[0006]) instead of driving it high. Same schematic confirms the RGB LED is a real **XL-5050RGBC-WS2812B** on GPIO23, and that RST pulls RUN ([0057])
- [ ] [0057] RESET button / RUN pin: a reset hook on `RP2040` | **Proposed, nothing implemented** - a design-only record, written because [0056]'s board has a RESET button and modelling it needs a public-API decision, not a board-file guess. RESET pulls **RUN**, which is not a GPIO (unlike BOOTSEL's `GPIO_QSPI_SS`, [0051]) and has no model at all; and the reset it must perform is `BaseDevice._on_watchdog_trigger()`'s three-step sequence, whose `cdc.reset()` half no `ExternalDevice` can reach (nothing hangs the `USBCDC` off `usb_ctrl`). Three options weighed - a `set_reset_hook()` on `RP2040` (recommended, mirrors `RPWatchdog.on_watchdog_trigger`), moving the whole sequence into the MCU with `USBCDC` observing a `usb_ctrl`-level reset instead (cleaner layering, bigger change), or modelling RUN as a real held-low pin (needs a new execution state in `_execute_batch.py`) - **Addendum 2026-08-17**: a real vendor schematic (via [0062]) shows RST grounding RUN *directly* for as long as it is held, against an external 10k pull-up - so a press is a level, not a pulse, which favours the held-low-pin option; and RUN has no pull semantics to model at all (unlike BOOTSEL's, [0050]/[0051]), leaving all the difficulty on the "what does a reset do" side. Plus the cost of `RP2040` existing twice (`_rp2040.py` + `native/_rp2040.pyx` `cdef class` + its `.pyi`), and the semantics still to settle (`WATCHDOG.REASON`/`machine.reset_cause()` after a RUN reset, BOOTSEL held during reset entering the bootrom, whether attached devices get told)

### Rejected / Superseded

- [ ] [0014] threading model | **Superseded → [0025]**
- [ ] [0015] HLE memcpy hook | **Rejected** — net negative (measurements kept)
- [ ] [0016] basic-block fusion / mini-JIT | **Rejected** — net negative in every real integration attempt; Cython interpreter core ([0013]) used instead
- [ ] [0045] CYW43 step 4 NAT bridge: embed `gVisor`'s `pkg/tcpip` via `cgo` | **Superseded → [0048]** (2026-08-16, hand-rolled reflector, no new toolchain) — kept verbatim for its own research trail (the `PyTCP` negative result, the `gVisor` empirical PoC, and the SDPCM `DATA_HEADER` envelope derivation [0048] itself reuses directly)

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
