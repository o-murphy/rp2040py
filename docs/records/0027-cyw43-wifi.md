# 0027. External devices and CYW43439 / Pico W WiFi emulation (epic)

- Status: In progress
- Conceived: 2026-08-12
- Related: decisions 0028 (module layout), 0029 (board composition), 0030 (concurrency) · research note 0024

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 1-72 -->

# CYW43439 / Pico W WiFi emulation — research notes and implementation plan

**Paused mid-3g (2026-08-12) for a simulator performance side quest - see below.** Next step once
resumed: 3g — async events + scripted scan/join. Everything before it in
"Implementation order" below is done: step 0 (board-loading API), step 1 (`ExternalDevice` proven
via `LEDMock`), step 2 (F0 bus-level `GSPIBus` decode - real firmware boots past the F0 handshake),
and step 3's sub-steps 3a (generic word-aligned block transfers -
`GSPIBus._on_clock_rising()`/`_start_response()` handle any `size`, not just one 32-bit word, via
`_word_count()`/`_words_to_value()`/`_value_to_words()`), 3b (ALP/HT/KSO clock handshake), 3c (F1
windowed backplane addressing), 3d (ARM core reset/enable registers), 3e (firmware/CLM download
acceptance - free via 3a/3c's generic F1 block writes - plus F2 packet delivery:
`GSPIBus.queue_rx_packet()`, `SPI_STATUS_REGISTER`/`SPI_INTERRUPT_REGISTER` plumbing, and the
shared `WL_D` IRQ pin reflecting real pending-packet state instead of a hardcoded LOW placeholder),
and 3f (SDPCM framing + generic ioctl request/response: `GSPIBus._write_wlan()` parses inbound F2
ioctl requests and answers with a generic zero-length success response via
`_build_ioctl_success_response()`, echoing the request id and tracking `bus_data_credit` so the
driver's own flow-control never stalls - corrected the SDPCM header size from an originally
estimated 14 bytes to the real 12 along the way). **Verified against a real, unmodified
MicroPython Pico W boot (2026-08-12, post-3f)** - not just unit tests: found and fixed three real
bugs (sticky ALP/HT clock availability including a WLAN-ARM-core-triggered case with no explicit
HT request, F1's register bank missing its true 2-byte-lower bound, and missing
`CYW43_BACKPLANE_READ_PAD_LEN_BYTES` padding on backplane reads) that were silently aborting
bring-up before firmware download ever started; firmware download now genuinely runs against real
firmware (traced live), though impractically slowly - see step 3f's own "Real-firmware
verification" entry below for the full writeup. 35 tests total in `tests/test_cyw43_bus.py`. Real
per-ioctl content and events (`WLC_SET_SSID`/join's scripted `WLC_E_*` sequence, scan results)
still aren't built - that's 3g, next. See "Implementation
order"'s step 3 for the full sub-step breakdown and status detail.

**Performance side quest (2026-08-12), why 3g hasn't started yet.** Profiling the real-firmware
boot above (the same run that verified 3e/3f) found the slowness isn't cyw43 code at all -
`GSPIBus`'s own GPIO listeners were ≈0.4% of profiled time. The real cost is shared simulator
infrastructure: `simulator.py`'s per-instruction dispatch loop, `peripherals/pio.py` PIO stepping
(no Cython port existed at all, unlike the CPU core), and `clock/simulation_clock.py`'s `tick()`
call volume. Two of the three are now done and measured - a genuine Cython port of PIO's
`StateMachine` (found and fixed a real latent 32-bit-masking bug along the way) plus an opt-in
batched `clock.tick()` (`RP2040PY_CLOCK_TICK_BATCH`, default off) - **~9.2x more PIO steps
completed in the same wall-clock window**, combined. Full writeup, numbers, and the file-by-file
structure are in `docs/BACKLOG.md`'s own "Follow-up: PIO Cython port + opt-in `clock.tick()`
batching" section (under its "Cython port of the interpreter core" heading) - not duplicated here
since it's a simulator-wide concern, not cyw43-specific. Firmware download itself hasn't been
re-verified end-to-end against this faster baseline yet (the profiling runs used a bounded window,
not a full boot to completion) - worth doing before or after resuming 3g, not required to resume.

[docs/MAIN_THREAD_ASYNCIO_BACKLOG.md](MAIN_THREAD_ASYNCIO_BACKLOG.md) (all 5 phases, engine-room
concurrency model) landed first and unblocked this whole effort - `Simulator`'s engine-room loop
now runs on whichever loop the caller itself owns, via `bind_loop()`, matching upstream rp2040js's
single-threaded model for the common single-instance case; a dedicated background thread is now
the exception, not the default. Step 0 below (board-loading API - `ExternalDevice`, `boards.py`,
`schedule_threadsafe()`) was originally built and merged against the *old* background-thread
engine room on a since-abandoned branch, then rebuilt from scratch directly on top of the new
architecture (`feat/board-loading-api`, branched from `refactor/main-thread-asyncio`) rather than
ported as a literal patch.

Goal (per discussion 2026-08-11): real internet access from emulated firmware, SLIRP/NAT-style (see
"Implementation order" below), not just a canned "connected" stub.

This is a **new feature**, not a porting gap: upstream `rp2040js` has no CYW43439/WiFi support at
all (confirmed via `gh api search/code` against `wokwi/rp2040js` — zero hits for `cyw43`/`wifi`),
and it isn't part of the RP2040 chip itself (the WiFi chip is a separate, external Infineon part on
the Pico W board) — so it's out of scope for the file-by-file port tracked in `docs/PORTING.md`.
Lives in `src/rp2040py/` once built (it's board-level, not `demo/`-only, unlike the E-Ink virtual
display work — that one stayed demo-only because it's an arbitrary external SPI peripheral the user
wires up themselves; CYW43439 is fixed hardware on every Pico W).

**Reading this document cold, with no other context?** Skip to "Implementation order" near the end
— it's the actionable summary, in build order, and links back into everything above it. Everything
before it is either hardware/protocol reference material (needed once you're actually implementing
a given step) or the design decisions that shape *how* to build it. "Open questions" at the very
end is what's genuinely still undecided or unresearched — check it before assuming a gap is an
oversight.


<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 687-1015 -->

## Implementation order (revised 2026-08-11)

**Start here for a fresh implementation session.** Ordered so the general-purpose plumbing lands —
and gets proven on a low-stakes device — before building CYW43439 (the highest-complexity,
highest-risk piece) on top of it, rather than the other way around. Each step names the design
decision sections above that define what it actually means.

0. **Board-loading API — done (2026-08-12, rebuilt on `feat/board-loading-api` directly against
   the now-landed main-thread-asyncio architecture - see this doc's own header).** `boards.py`
   registry, `--board` CLI flag (`choices=["pico", "pico_w"]`), the `ExternalDevice` `Protocol`
   (`attach(rp2040)`), the standalone `attach_external_devices(mcu, *devices)` function, and
   `RP2040`/`Simulator.schedule_threadsafe()` from "Concurrency model" above — all built and
   tested (native + pure-Python), with no CYW43439-specific code yet. Also picked up a small CLI
   addition in the same pass, orthogonal to CYW43 itself: `--fetch-fw-only` on
   `run`/`micropython`/`kaluma`/`bench` downloads/caches firmware (and `--bootrom`, if a version
   tag) and exits without starting the simulator - for pre-warming the local cache. See "Board
   composition decision" and "Concurrency model" above for the full shape of step 0.
1. **Prove the `ExternalDevice` pattern on something already understood — done (2026-08-12), via
   `LEDMock` rather than the originally-planned eink retrofit.** `external/led_mock.py`'s
   `LEDMock(gpio=25)` - a minimal `ExternalDevice` that watches one GPIO pin via
   `GPIOPin.add_listener()` and tracks on/off state + toggle count - attached to *both* `pico` and
   `pico_w` in `boards.py`. Validates `attach()`/`attach_external_devices()`/`build_rp2040()` end
   to end with real (non-demo) tests (`tests/test_led_mock.py`, `tests/test_boards.py`) without
   depending on the still-unmerged `component/epd2in9g` branch at all - the eink retrofit described
   below is still worth doing eventually (it's a materially bigger/more realistic validation, real
   SPI framing instead of one GPIO), but wasn't necessary to unblock step 2. **Not hardware-accurate
   for `pico_w`** - see `led_mock.py`'s own docstring: a real Pico W's LED is wired to the CYW43439
   chip itself, not any RP2040 GPIO, so this is a placeholder there until `Cyw43439` (step 3) grows
   real LED handling.

   Eink retrofit (still deferred, not blocking, kept for whenever `component/epd2in9g` lands): the
   natural candidate is retrofitting `demo/components/virtual_eink.py`'s existing ad hoc
   `GPIOPin.add_listener()`/`RPSPI.on_transmit` wiring into the `ExternalDevice` interface. Not
   done here because that demo doesn't exist on this working branch: it lives on a separate,
   unmerged branch (`component/epd2in9g`, commit `a6d6f34` - `demo/components/virtual_eink.py`/
   `mp_eink_demo.py`, `demo/eink_run.py`), itself a few commits behind `main`. Confirmed with the
   user (2026-08-11): that branch is being kept as a plain reference for now, not pulled into this
   branch early - it gets migrated onto the `ExternalDevice` architecture as its own effort once
   `component/epd2in9g` is brought up to date and merged, not before.
2. **Bus level — done (2026-08-12), unit-tested; real-firmware boot not yet attempted.**
   `external/cyw43/bus.py`'s `GSPIBus`: a generic bit-banged half-duplex gSPI watcher hooked via plain
   `GPIOPin.add_listener()`/`set_input_value()` on GPIO24/25/29, decoding `make_cmd()`'s header on
   the first 32-bit word after select and dispatching `BUS_FUNCTION` (F0) register reads/writes -
   `SPI_READ_TEST_REGISTER` fixed at `TEST_PATTERN=0xFEEDBEAD`, everything else a plain
   byte-addressable register file. Synchronous, in-line - pure decode logic, no I/O, no
   `schedule_threadsafe()` needed, as planned.

   **Exact wire timing derived from the real source, not guessed**: read
   `cyw43_bus_pio_spi.c`/`.pio` directly (both checked out locally - see "Authoritative protocol
   reference" above for the paths) rather than relying on the Wokwi-investigation architecture
   alone. Confirmed from the PIO program (`spi_gap01_sample0`, the driver's own default): every
   real bit action (drive a TX bit, sample an RX bit) happens while `WL_CLK` is low, immediately
   before it's raised - so this decodes as sample-on-rising-edge (host drove the bit during the
   preceding low phase), drive-on-falling-edge (chip's own turn, so it's stable in time for the
   host's next low-phase sample). `bus.py`'s own module docstring has the full derivation.

   **Correction (2026-08-12), found by booting real firmware, not by reading source closer:**
   the original claim here - that the C driver's `SWAP32()`/DMA-`bswap` calls cancel out, so the
   wire always carries the natural `make_cmd()`/register value - is **wrong** for the two
   `_swap`-suffixed accessors (`read_reg_u32_swap()`/`write_reg_u32_swap()`) that
   `cyw43_ll_bus_init()` uses for the `SPI_READ_TEST_REGISTER` poll and the one `SPI_BUS_CONTROL`
   write that switches modes. `SWAP32()` there is actually `__swap16x2()`/`REV16` (swaps bytes
   *within* each 16-bit half, not a full 32-bit reversal) - composed with the DMA engine's own
   *full* 32-bit byte-swap (applied unconditionally to every gSPI DMA transfer, regardless of
   caller), the net wire transform for these two calls is "swap the two 16-bit halves of the word,
   bytes in-order within each half" - confirmed by capturing a real word off a live Pico W boot:
   the first test-register-poll header arrived as `0xa0044000`, exactly `0x4000a004` (the real
   `make_cmd()` value) with its halves swapped, not the identity. Every *other* accessor (used
   after that one `SPI_BUS_CONTROL` write) skips the C-level swap and relies on the DMA engine's
   full byte-swap plus the chip's own `ENDIAN_BIG` config (set by that same write) to net out to a
   *different* transform: a full 32-bit byte reversal instead. This is real, stateful gSPI hardware
   behavior gated by `SPI_BUS_CONTROL`'s `WORD_LENGTH_32` bit (0 = 16-bit word length, the chip's
   own power-on default; 1 = 32-bit) - not an implementation detail to paper over. `bus.py` now
   models it directly: `GSPIBus._word_length_32` starts `False`, `_word()` applies the matching
   self-inverse transform to every decoded/encoded 32-bit word, and `_write_f0()` flips the flag
   the instant a `SPI_BUS_CONTROL` write's value has `WORD_LENGTH_32` set - see `bus.py`'s module
   docstring for the full derivation and `_swap_halves()`/`_swap_bytes()` for the two transforms.
   `tests/test_cyw43_bus.py`'s `_FakeGSPIMaster` mirrors the same mode-tracking so its round-trip
   tests stay meaningful (both sides start in sync and flip in lockstep on the same rule, so
   correctness holds regardless of which mode is active at any given moment in a test).

   **Verified end-to-end against a synthetic gSPI master** (`tests/test_cyw43_bus.py`'s
   `_FakeGSPIMaster`, bit-banging GPIO24/25/29 via the same SIO-write pattern
   `tests/test_led_mock.py` established) - 13 tests (5 from step 2, 8 added for step 3b/3c/3d's
   ALP/HT/KSO/backplane-window/core-reset-default coverage), all passing.

   **Booted real, unmodified MicroPython Pico W firmware against this (2026-08-12) - the
   `[CYW43] Failed to start CYW43` warning the user's own live test first surfaced (before any of
   step 3's work, and before `Cyw43439`/`bus.py` were even wired into `boards.py`'s `pico_w`
   extras) is now gone entirely** on both `v1.28.0` and `v1.21.0`, confirming `cyw43_ll_bus_init()`
   (the F0-only handshake: test-register poll, the `SPI_BUS_CONTROL` word-length switch, and the
   interrupt-register writes) completes successfully end-to-end for the first time. This needed
   only the wire word-length/endian fix above, not step 3b/3c/3d's F1 work directly -
   `cyw43_ll_bus_init()` itself never touches `BACKPLANE_FUNCTION`. Not yet confirmed live: whether
   `cyw43_ll_wifi_on()` (the *later* call that actually exercises 3b/3c/3d's ALP/HT/KSO/backplane/
   core-reset registers) succeeds - `v1.28.0`'s `network_cyw43_active()` unconditionally sets its
   active flag regardless of `cyw43_wifi_set_up()`'s return code (confirmed by reading
   `extmod/network_cyw43.c` directly), so `nic.active(True)` "succeeding" there is silent either
   way; `v1.21.0` returned `active() == False` with no warning printed at all, which is *consistent
   with* (not proof of) an older port version's `active()` actually propagating a `cyw43_ll_wifi_on()`
   failure - plausible since 3e/3f (firmware/CLM download, SDPCM+ioctl) aren't built yet and
   `cyw43_ll_wifi_on()` needs both. Real, not yet closed, risk to revisit once 3e/3f land.
3. **Chip/backplane + SDPCM/WLC ioctl layer - revised into sub-steps (2026-08-12), see "Real
   bringup sequence beyond F0" above for the full derivation.** The original one-paragraph
   estimate here undersold the real scope (firmware/CLM blob download, ARM core reset registers,
   ALP/HT clock handshake, SDPCM framing all sit *between* F0 and the first `WLC_*` ioctl) - broken
   up so each piece can land and get tested independently, in dependency order:

   1. **3a. Block transfers in `GSPIBus` (step 2's own scope creeping forward).** Generalize
      `_on_clock_rising()`/`_on_clock_falling()` from exactly-one-32-bit-word to an arbitrary,
      word-aligned byte count (`make_cmd()`'s `size` field is 11 bits for exactly this reason).
      Nothing past this point is reachable without it - every SDPCM/ioctl/firmware-download
      transfer is multi-byte. **Done (2026-08-12)**, unit-tested (`bus.py` gained
      `_word_count()`/`_words_to_value()`/`_value_to_words()`; multi-word backplane and F0 block
      round-trips, plus a non-word-aligned 6-byte block, in `tests/test_cyw43_bus.py`). Existing
      single-register accesses (size<=4) are unaffected - `_word_count()` floors to one word, the
      same wire shape they always used, so this is a strict generalization, not a rewrite. Not yet
      exercised against real firmware (that needs 3e's firmware-download acceptance to actually
      trigger a block-sized transfer over the bus).
   2. **3b. ALP/HT clock handshake + KSO** on `BACKPLANE_FUNCTION`'s `SDIO_CHIP_CLOCK_CSR`
      (`0x1000e`) / `SDIO_SLEEP_CSR` (`0x1001f`) - a register model where writing a `*_REQ`/
      `KEEP_SDIO_ON` bit makes the corresponding `*_AVAIL`/`DEVICE_ON` bit immediately readable
      satisfies every poll loop in `cyw43_ll_bus_sleep()`/`cyw43_kso_set()` correctly, no real
      clock-startup latency needs modeling. **Done (2026-08-12)**, unit-tested (round-trip on both
      registers). Not yet confirmed against real firmware's `cyw43_ll_wifi_on()` specifically (see
      step 2's own progress-log entry above for why that's still open).
   3. **3c. Backplane (F1) windowed read/write.** A generic byte-addressable "backplane memory"
      model plus the `SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH` window-select registers on
      `BACKPLANE_FUNCTION` - same shape as `GSPIBus`'s own F0 register space (step 2), just a much
      bigger address space. Specific backplane *addresses'* meaning (core control registers,
      SOCSRAM, ...) layers on top in later sub-steps, not part of the windowing mechanism itself.
      **Done (2026-08-12)**, unit-tested (window-select bytes route a flagged address to the
      correct combined `(window << 15) | (addr & 0x7fff)` slot). Found and fixed a real bug along
      the way: `_f1`'s register-bank addresses (`0x1000a`-`0x1001f`) were being indexed directly
      into a from-zero 32-byte array, so every F1 register access silently fell out of bounds and
      no-op'd - fixed with a `_F1_REGISTER_BASE` offset.
   4. **3d. ARM core reset/enable registers** (`CORE_WLAN_ARM`/`CORE_SOCRAM`'s `AI_IOCTRL_OFFSET`/
      `AI_RESETCTRL_OFFSET`, reached via 3c's windowed access) - reflecting "reset" then "clocked,
      out of reset" on readback is enough; **no second CPU core needs emulating** - the host driver
      never talks to the WLAN core's own firmware except through SDPCM (3f), so nothing downstream
      can tell a real running core from a register model that remembers it was told to start.
      **Done (2026-08-12)**, unit-tested (both cores default to `AIRC_RESET` set; `AI_IOCTRL_OFFSET`
      round-trips through the window like any other backplane-memory address).

   **`Cyw43439` (`external/cyw43/chip.py`, 2026-08-12).** A minimal `ExternalDevice` that just owns
   a `GSPIBus` and calls `attach_gpio()` from its own `attach()` - enough to wire 3b/3c/3d (and
   step 2's F0 work) onto `boards.py`'s `pico_w` extras (alongside the existing placeholder
   `LEDMock`) so real firmware boots actually exercise this code, not just unit tests. 3e (below)
   also landed entirely in `bus.py`, not `chip.py` - continuing the precedent 3b/3c/3d already set
   (the doc's original plan to move "3d onward" into `chip.py` wasn't followed in practice; register/
   wire-level mechanics have all stayed in `GSPIBus` so far, `Cyw43439` remains a thin wrapper).
   Everything past this point (SDPCM/ioctl framing/events, 3f onward) still needs to land before
   `Cyw43439`/`GSPIBus` does anything beyond bus-level register bookkeeping.
   5. **3e. Firmware/CLM blob download acceptance + F2 packet-available status plumbing.** Accept
      (don't need to validate or store meaningfully - `cyw43_check_valid_chipset_firmware()` runs
      entirely driver-side, never reaches the chip) the `cyw43_write_bytes()` block writes real
      firmware/CLM download does; wire up `SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE` +
      `SPI_STATUS_REGISTER`'s `GSPI_PACKET_AVAILABLE`/pending-length field, the mechanism every
      later sub-step's own responses/events get delivered through. **Done (2026-08-12)**, split
      into two independently-confirmed halves:
      - **Firmware/CLM download acceptance turned out to need no new code at all.** Confirmed
        directly from source (`cyw43_ll.c:cyw43_download_resource()`): it writes through
        `cyw43_write_bytes(BACKPLANE_FUNCTION, dest_addr | SBSDIO_SB_ACCESS_2_4B_FLAG, sz, src)` in
        `CYW43_BUS_MAX_BLOCK_SIZE` (64 bytes for SPI, `cyw43_ll.h`) chunks, re-selecting the
        backplane window per chunk - exactly the generic F1 block-write path step 3a/3c already
        built. Payload content is simply stored (and ignored) in `GSPIBus`'s existing sparse
        `_backplane_memory` dict - real firmware download addresses land in SOCSRAM
        (`CORE_SOCRAM`), a normal backplane-window target, nothing WLAN-function-specific.
        Unit-tested (`test_firmware_download_shaped_block_writes_round_trip`) by replaying the real
        chunking algorithm across several sequential 64-byte writes.
      - **F2 packet delivery is genuinely new**: `GSPIBus.queue_rx_packet(data)` stages `data`,
        sets `SPI_STATUS_REGISTER`'s `STATUS_F2_PKT_AVAILABLE` bit + length field (bits 19:9 -
        confirmed the actual field real firmware's SPI-variant `cyw43_ll_sdpcm_poll_device()`
        trusts, `cyw43_ll.c:~1080`) and `SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE` bit (the
        earlier, cheaper gate the same function checks first, `cyw43_ll.c:~1008`), and - if CS is
        currently deasserted - immediately drives the shared `WL_D` pin's own IRQ level high too,
        since real firmware's `cyw43_cb_read_host_interrupt_pin()` (`cyw43_ctrl.c`) polls that pin
        directly and independently of any SPI transaction (confirmed via
        `CYW43_PIN_WL_HOST_WAKE`'s wiring in `pico_cyw43_driver/cyw43_driver.c` - same GPIO as
        `WL_D`). `GSPIBus._on_cs_change()`'s deselect handler, previously a hardcoded LOW placeholder
        (step 2's own note that this was deferred), now reflects `bool(self._rx_packet)` instead. A
        new `_read_wlan()` handles `WLAN_FUNCTION` reads (always fixed-address, `addr=0`, matching
        `cyw43_read_bytes(WLAN_FUNCTION, 0, ...)` - the `addr` argument is unused, mirroring real
        hardware's own FIFO semantics), draining the queue and clearing both status/interrupt bits
        once fully consumed. `WLAN_FUNCTION` *writes* (host-to-chip SDPCM/ioctl content) were still
        a no-op at the time this landed - step 3f (below) fills that in. Unit-tested (5 tests:
        delivery, status/length field, consume-clears-status, IRQ raised while idle, IRQ drops
        once consumed).
      **Flag, still not solved (unchanged from the original estimate):** profile whether bit-level
      `GPIOPin.add_listener()` simulation of a real (hundreds-of-KB) firmware image is practically
      fast enough now that this actually lands and could be exercised against real firmware - may
      need a batched/short-circuited path for large pure-write block transfers specifically. Not
      yet profiled against a real boot (3e's own tests use small synthetic payloads, not a real
      firmware-sized download).
   6. **3f. SDPCM framing + generic ioctl request/response.** **Done (2026-08-12)**, unit-tested (7
      tests in `tests/test_cyw43_bus.py`, 29 total). Landed entirely in `bus.py`, same as every
      prior sub-step:
      - **Correction while implementing: the SDPCM header is 12 bytes, not the 14 originally
        estimated here.** `struct sdpcm_header_t` (`cyw43_ll.c`) is 9 plain uint8/uint16 fields
        (`size`, `size_com`, `sequence`, `channel_and_flags`, `next_length`, `header_length`,
        `wireless_flow_control`, `bus_data_credit`, `reserved[2]`) - `2+2+1+1+1+1+1+1+2 = 12`, no
        compiler padding possible (no uint32 members, already 2-byte aligned). The original
        estimate had also missed the `next_length` field entirely. `SDPCM_HEADER_LEN = 12` in
        `bus.py`, confirmed against the struct's own field list, not assumed. The 16-byte ioctl
        header (`cmd`/`len`/`flags`/`status`, all `uint32_t`) was correct as originally estimated.
      - `GSPIBus._write_wlan()` parses an inbound F2 block write (already a single call thanks to
        step 3a - real firmware sends the whole SDPCM+ioctl+payload blob as one
        `cyw43_write_bytes(WLAN_FUNCTION, 0, ...)`): validates `size`/`~size_com`, and for
        `CONTROL_HEADER` frames only, calls `_build_ioctl_success_response()` and delivers it via
        step 3e's `queue_rx_packet()`. `DATA_HEADER` (outbound Ethernet, step 4) and anything
        malformed are silently ignored, not raised.
      - `_build_ioctl_success_response()` builds a zero-length "success" response echoing the
        request's own id (`ioctl_header_t.flags`'s `CDCF_IOC_ID_MASK` bits -
        `sdpcm_process_rx_packet()` drops any response whose id doesn't match the driver's last
        sent one), with `wireless_flow_control` always `0` and a `bus_data_credit` byte
        (`GSPIBus._bus_data_credit`, starting at 1 to match the driver's own initial
        `wwd_sdpcm_last_bus_data_credit`) incremented once per response. Both are real
        correctness requirements, not cosmetic: a nonzero `wireless_flow_control` or a
        `bus_data_credit` that doesn't stay strictly ahead of the driver's own send count makes
        `cyw43_sdpcm_send_common()`'s own STALL check block every later host send forever
        (confirmed by reading that function, not just the receive side).
      - One generic "echo a zero-length success response" handler - no branching on `cmd` at all -
        already satisfies the bulk of the real `WLC_*`/iovar vocabulary `cyw43_ll_wifi_on()`/
        `cyw43_ll_wifi_join()` send during bring-up, exactly as originally planned. Real per-ioctl
        content (`WLC_SET_SSID`/join's own scripted event sequence, `escan` results) is step 3g.

   **Real-firmware verification (2026-08-12), post-3f - three real bugs found and fixed, not just
   unit-tested in isolation.** `tests/micropython/main-cyw43.py` (a real MicroPython network-module
   doc snippet, run via `rp2040py micropython --board pico_w tests/micropython/main-cyw43.py`
   against an actual downloaded `v1.28.0` UF2) had never gotten past `nic.active(True)` silently
   failing (`itf_state` staying 0, later surfacing as a plain `OSError: EPERM` on the next call
   that checks it - `cyw43_ll_bus_init()`'s own `CYW43_WARN()` diagnostics are compiled out of
   release firmware, so nothing printed). Found by tracing every `GSPIBus` register access against
   a real boot (temporary instrumentation, removed once each bug was found) rather than guessing -
   each fix confirmed by watching the trace move past its failure point, not just by re-reading
   source:
   - **`SDIO_CHIP_CLOCK_CSR`'s `SBSDIO_ALP_AVAIL`/`SBSDIO_HT_AVAIL` bits needed to be genuinely
     sticky, on *both* read and write, not just OR'd into one write's value.** `cyw43_ll_bus_init()`
     clears the register to 0 right after achieving ALP (its own `alp_set:` label) - a non-sticky
     model silently un-set `SBSDIO_ALP_AVAIL` on that very write. Separately, and more
     significantly: **`SBSDIO_HT_AVAIL` is polled later (`cyw43_ll.c:~1655-1667`, right after
     `reset_device_core(CORE_WLAN_ARM, ...)`) with no `SBSDIO_HT_AVAIL_REQ` write anywhere in
     between** - real hardware brings HT up as a side effect of the ARM core actually running its
     own firmware, which this project deliberately doesn't emulate. Fix: `GSPIBus._alp_available`/
     `_ht_available` are now sticky booleans, re-applied by both `_read_f1()` and `_write_f1()`
     whenever `SDIO_CHIP_CLOCK_CSR` is touched (an earlier version of this fix only patched the
     write path and still failed, since nothing writes this register again after the ARM core
     comes up - the driver only ever reads it from that point on); a new
     `_maybe_mark_ht_available()`, called after every `BACKPLANE_FUNCTION` write, sets
     `_ht_available` the instant `CORE_WLAN_ARM`'s own registers reach `device_core_is_up()`'s
     exact "up" condition - scoped to `CORE_WLAN_ARM` specifically, not `CORE_SOCRAM` (only a
     running core would plausibly request its own clock).
   - **F1's register-bank lower bound was 2 bytes too high.** `SDIO_FUNCTION2_WATERMARK = 0x10008`
     sits below `SDIO_BACKPLANE_ADDRESS_LOW = 0x1000A` (the old `_F1_REGISTER_BASE`), so a
     Bluetooth-gated write-then-read-back check in `cyw43_ll_bus_init()` silently read back `0`
     instead of what it just wrote, failed its own equality check, and aborted bring-up immediately
     after the ALP handshake - before firmware download ever started. Fix: `_F1_REGISTER_BASE` is
     now `SDIO_FUNCTION2_WATERMARK` itself (the real lowest F1 register address used anywhere in
     `cyw43_ll.c`), growing `_f1`'s bounds by 2 bytes to include it.
   - **`BACKPLANE_FUNCTION` reads were missing `CYW43_BACKPLANE_READ_PAD_LEN_BYTES` (16 bytes = 4
     words) of leading dummy padding.** Confirmed from `cyw43_bus_pio_spi.c`'s `_cyw43_read_reg()`:
     real hardware needs extra turnaround time to actually fetch backplane-sourced data, so every
     F1 read (not just windowed `SB_ACCESS` ones - gated purely on `fn == BACKPLANE_FUNCTION`)
     clocks 4 dummy words before the real answer, and the driver reads the *last* word of the
     total response as the value, discarding everything before it. `GSPIBus` was driving the real
     value immediately after the header instead - the driver discarded it as padding and read
     stale/undriven bits as the "answer," corrupting that transaction and cascading into
     garbage-looking subsequent ones (this was the single most confusing symptom: two phantom
     `size=0` reads appearing with no `CS` deselect/reselect between them and the prior real read,
     which turned out to be the driver's own PIO clocking through what it correctly expected to be
     padding, while `GSPIBus` had already exhausted its one-word response and started
     misinterpreting the continued clock edges as a new command header). Fix: `_start_response()`
     now prepends `CYW43_BACKPLANE_READ_PAD_LEN_BYTES / 4` zero words before the real value,
     `BACKPLANE_FUNCTION` reads only - `BUS_FUNCTION`/`WLAN_FUNCTION` reads are unaffected (and a
     dedicated test confirms `SPI_READ_TEST_REGISTER` - the very first thing real firmware does -
     still gets exactly one word, unpadded).

   All three fixed and regression-tested in `tests/test_cyw43_bus.py` (35 tests total - 6 new: the
   three fixes above, plus a `BUS_FUNCTION`/`WLAN_FUNCTION`-gets-no-padding check, an
   ALP-survives-a-later-clear-to-zero check, and a `CORE_SOCRAM`-doesn't-trigger-HT check). Real
   effect, confirmed live: firmware download (step 3e/3a's own block-write path) now actually
   *runs* against real firmware for the first time - traced address-incrementing 64-byte
   `BACKPLANE_FUNCTION` writes of real compiled ARM firmware bytes, not just unit-tested in
   isolation.

   **Performance is a real, now-confirmed problem, not just the theoretical concern step 3e
   flagged - explicitly not fixed here, by design decision (2026-08-12).** Real firmware is
   ~229KB downloaded in `CYW43_BUS_MAX_BLOCK_SIZE` (64-byte) chunks - thousands of transactions,
   each involving hundreds of individual Python `GPIOPin.add_listener()` bit-level callback
   invocations. A live run against `tests/micropython/main-cyw43.py` was still mid-firmware-download
   after 8 minutes of wall-clock time (steadily progressing, not stuck - confirmed via address
   tracing) before being killed. Separately: `timeout <n>` did **not** reliably kill the process in
   this state - `uv run`'s child Python process kept running well past the timeout, burning 100%
   CPU, until explicitly `kill -TERM`'d by PID; a genuine operational hazard worth remembering when
   testing this manually, independent of the emulation-speed problem itself. **Deliberately
   deferred, not solved**: continuing with step 3g (the plan's own next step) rather than building
   the batched/short-circuited large-block-write path step 3e already flagged as the likely fix -
   correctness against real firmware is now demonstrated as far as SDPCM/ioctl bring-up; making
   that bring-up fast enough for interactive/CI use is real, separate, future work.
   7. **3g. Async events + scripted scan/join.** `cyw43_async_event_t`'s exact field layout
      (byte offsets matter - real firmware struct padding, not a natural dataclass shape),
      delivered as a fake-Ethernet-framed (`0x886c` + Broadcom OUI) SDPCM async packet. A fixed
      fake AP (mirroring `Wokwi-GUEST`'s shape) answers `scan()`'s single `escan` iovar with one
      `CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` event. `join()`'s own scripted `WLC_E_*`
      sequence (`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`/...) is the one piece not yet fully read from
      source (see "Research homework" below - `cyw43_ll_wifi_join()`'s tail end, past the SSID
      write) - this is the sub-step that actually makes `.status()` progress through real codes.

   Lives in `external/cyw43/chip.py` (3d onward - 3a is `external/cyw43/bus.py`, 3b/3c could
   reasonably live in either, judgment call when implementing); this is where `Cyw43439`
   implements `ExternalDevice.attach()`. Every sub-step stays synchronous, in-line - still a pure
   state machine, no real I/O (`schedule_threadsafe()`) until step 4.
4. **Real network bridge.** Once firmware believes it's associated and starts moving IP packets,
   the userspace NAT/SLIRP layer described earlier — real `socket.getaddrinfo`/TCP/UDP via the host,
   DNS included — so `urequests`-style code in emulated firmware reaches the real internet. Needs
   the SDPCM data-frame envelope format (how an actual Ethernet/IP frame is wrapped for the F2/WLAN
   data path) - not yet extracted from `cyw43_ll.c`, see "Open questions" below. **This is where
   `schedule_threadsafe()` actually gets used** — real socket I/O is the first genuinely slow/
   blocking work in the whole plan. Lives in `external/cyw43/nat.py`.

   *Not* the actual `libslirp` C library QEMU embeds — a SLIRP-**style** userspace NAT written in
   plain Python: ordinary unprivileged `socket.connect()`/`send()`/`recv()`/`getaddrinfo()` calls
   made by the `rp2040py` process itself, no TUN/TAP interface, no raw sockets, no root. Because it
   never drops below the ordinary client-socket API, it's expected to work identically on Linux,
   macOS, and Windows — unlike QEMU's `tap` netdev mode, which needs `CAP_NET_ADMIN`/root on Linux,
   `vmnet.framework` on macOS, or an installed TAP driver on Windows. Like real SLIRP/QEMU user-mode
   networking, inbound connections don't pass through without an explicit forwarded port (see the
   WebREPL note in "Open questions" below).


<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 1016-1143 -->

## Open questions for next session

Organized by kind, so a fresh session can tell "needs a decision" from "just needs source-reading"
from "closed, kept for the record."

### Historical / closed — no action needed

- Where `wifi.medium`/the fake-AP/NAT bridge actually lives in Wokwi's bundle — not located, and no
  longer worth chasing: our own network-bridge plan (step 4 above: direct host `socket` calls, no
  fake 802.11 layer needed) diverges from their approach here anyway.
- **WebREPL is not an automatic side effect of step 4 (the network bridge).** WebREPL is an
  *inbound* connection (something outside connects *to* the device on port 8266); step 4 is an
  *outbound* SLIRP-style bridge (the device itself reaches out to the internet, like QEMU's default
  `-netdev user` mode). SLIRP does not pass inbound connections through without an explicit
  port-forward rule (QEMU's equivalent is `hostfwd=tcp::8266-:8266`). So WebREPL support is a
  separate, additional feature layered on top of step 4 — it would need an explicit forwarded
  port from the host into the NAT. A natural follow-on, but not something step 4 delivers for
  free on its own.
- Explicitly **out of scope for the `firmware_retrieve.py` board-awareness work (done - see
  "Deferred, not designed" below): the `"bootrom"` entry.** The
  bootrom is a mask ROM baked into the RP2040 die itself at manufacturing time, identical across
  every board that chip ends up on — versioned only by silicon stepping (B0/B1/B2, already handled
  per issue #11/`docs/BACKLOG.md`), never by board. It has no awareness of externally-wired
  hardware like CYW43439 at all (that's purely a MicroPython/CircuitPython + `cyw43-driver`
  concern, running long after the bootrom has handed off). Only the
  `micropython`/`circuitpython`/`kaluma` entries in `firmware_specs.json` need board-variant
  resolution.

### Deferred, not designed — real future work

- **`IClock`'s protocol could eventually move to `external/`, mirroring `ExternalDevice` — not
  started, no code changed for this yet.** Raised 2026-08-11: from `RP2040`'s point of view,
  `IClock` (which alarm/timing source drives it) is arguably the same kind of externally-injectable
  dependency `ExternalDevice` formalizes for board-level hardware - both are interfaces `RP2040`
  is built against without owning a specific implementation of. If this is ever done, the split
  should follow the same pattern already established for `ExternalDevice`/`Cyw43439`: the
  **protocol** (`IClock`/`IAlarm`/`AlarmCallback`, today in `src/rp2040py/clock/clock.py`) moves to
  `src/rp2040py/external/clock.py`, while **concrete implementations** (`SimulationClock`,
  `MockClock`) stay in `src/rp2040py/clock/` - the same way a future `Cyw43439` (a concrete
  `ExternalDevice`) lives in `cyw43/`, not in `external/` itself. Not attempted yet: `IClock` is
  injected as a plain constructor argument to `RP2040.__init__()` (needed before peripheral
  construction, since several peripherals call `self.clock.create_alarm(...)` from their own
  `__init__`), unlike `ExternalDevice.attach()`, which runs *after* `RP2040` exists - so this isn't
  a pure rename, it would touch every one of `IClock`/`IAlarm`'s ~8 current import sites
  (`_rp2040.py`, `native/_rp2040.pyx`, `clock/simulation_clock.py`, `peripherals/pwm.py`,
  `peripherals/timer.py`, `peripherals/usb.py`, `utils/timer32.py`, plus this repo's own
  `boards.py`) for a naming/location change with no behavioral difference. Worth doing for
  consistency if/when someone's actively working in this area anyway; not worth a standalone PR on
  its own.

- **`ExternalDevice.detach()` / a standalone `detach_external_devices()` counterpart — not designed
  yet, may eventually be needed** as the symmetric counterpart to `attach()`/
  `attach_external_devices()`. Two concrete motivating cases, not just testing nicety:
  - **Virtual serial external devices** — something wired onto a UART/pin pair (a virtual modem,
    sensor, or other serial peripheral in the same spirit as `demo/components/virtual_eink.py`)
    that a user plugs in and back out over the course of one session, mirroring how a real serial
    device gets connected/disconnected on the fly.
  - **Fault injection** — once hot-plug is safe (see "rethink attach/detach to be safe regardless
    of run state" above), tests could inject a mid-run peripheral dropout/failure and observe how
    the emulated controller and firmware actually react.

  Both need `detach()` *and* the pre-run-only constraint lifted for hot-swap — see the dedicated
  subsection under "Board composition decision" above for why that's not just "add a running
  check" but a rethink using `schedule_threadsafe()`. Worth noting for whoever designs it:
  `GPIOPin.add_listener()` already returns an unsubscribe callable (`gpio_pin.py:315`,
  `lambda: self._listeners.discard(callback)`) — an `attach()` implementation should hang onto
  whatever `add_listener()` gives back for each pin it hooks, so a future `detach()` has something
  to call. Not needed for step 2 (bus level); flagging so `attach()` implementations don't throw
  that return value away.

- **Done (2026-08-12): `utils/firmware_retrieve.py` (moved from `cli/` - it's a generic tag/URL/
  path resolver with no argparse involvement, not CLI-specific) is now board-aware**, per the
  "Candidate redesign" this bullet originally proposed - implemented essentially as decided, both
  formerly-open sub-items resolved along the way:

  - `FirmwareSpec` dropped `filename_template`/`url_template` entirely - `boards: dict[board,
    dict[tag, url]]` (MicroPython/CircuitPython/Kaluma - genuinely different per-board builds) or a
    flat `known_versions: dict[tag, url]` (BOOTROM only - board-agnostic, a silicon-stepping-only
    mask ROM, deliberately *not* nested by board - see the "Historical/closed" section above for
    why). `retrieve(spec, image, board="pico")` resolves three ways: existing local path (unchanged),
    a direct `http(s)://` URL (new - downloads and caches it under its own basename, or a
    `sha256(url)[:16]` hash when the URL has no usable path component - the "how to name the cache
    entry" sub-item's resolution), or a version tag looked up in `spec.boards[board]` (an unknown
    board or unknown tag is now a clear, distinct logged error instead of the old silent
    "fall back to using the raw tag as a literal filename suffix" behavior). `board` is consulted
    only for the tag path against a `boards`-shaped spec, exactly as decided - a local path or raw
    URL is used exactly as given regardless of `--board`, and `board` is ignored entirely for
    `known_versions`-shaped BOOTROM.
  - **Refresh workflow (the other open sub-item), decided while implementing:** one script,
    `scripts/fetch_firmware.py`, not one per firmware family - fetches and writes all four
    families' real data in a single pass (run `uv run scripts/fetch_firmware.py`, diff, commit).
    Sources actually used, each confirmed live (2026-08-12), correcting a couple of assumptions
    from the original proposal along the way:
    - MicroPython: `https://micropython.org/download/{RPI_PICO,RPI_PICO_W}/`, scraped HTML - as
      originally proposed (the `tool/fetch-mp-firmware` branch's own prototype scraper, since
      superseded by this script - extended to emit full URLs instead of bare filenames).
    - CircuitPython: **not** the board pages (`circuitpython.org/board/<slug>/`, which - confirmed
      live - only ever show the *current* stable + prerelease, no history at all). The public S3
      bucket's own REST listing API instead (`?prefix=...` on the bucket root - not to be confused
      with `/index.html?prefix=...`, a JS-rendered page not scrapable without a JS engine) returns
      the *full* version history as a plain XML `ListBucketResult` - filtered to drop CI nightly/
      PR-preview builds that live in the same prefix (named `<8-digit-date>-<branch>-PR<n>-<hash>`,
      not a real version).
    - Kaluma: **the original "no clean per-board split" assumption was wrong** - confirmed directly
      against the GitHub releases API that Kaluma *does* publish separate
      `kaluma-rp2-pico-<version>.uf2`/`kaluma-rp2-pico-w-<version>.uf2` release assets, on every
      release since 1.1.0. No compromise/special-casing needed after all.
    - Bootrom: the GitHub releases API (`raspberrypi/pico-bootrom-rp2040`) - one `<tag>.elf` asset
      per release (b0/b1/b2) - fetched by the same script for consistency (one place the
      board-aware-vs-not distinction lives), written to the flat `known_versions` shape.
  - `firmware_specs.json` (now `utils/firmware_specs.json`) populated with real data from all four
    sources as of 2026-08-12 - 23/17 MicroPython pico/pico_w versions, 160/105 CircuitPython
    (limited to non-nightly releases), 17/9 Kaluma, 3 bootrom.

### Research homework — not decisions, just needs source-reading during implementation

- **Resolved (2026-08-12), see "Real bringup sequence beyond F0" above:** the backplane *core*
  address map (chip-common registers, ARM core reset/halt registers, SOCSRAM addresses) needed for
  bring-up, SDPCM header layout, and async-event framing are now all documented directly from
  `cyw43_ll.c`/`cyw43_ll.h` - no longer just "not yet dug out."
- **Still open: `cyw43_ll_wifi_join()`'s tail end** (past the point read for the "Real bringup
  sequence" section above - the actual `WLC_SET_SSID` call that triggers the join, and the
  `WLC_E_*` event sequence real firmware fires in response) - needed for step 3g specifically.
  Read the rest of `cyw43_ll_wifi_join()` (starts at `cyw43_ll.c:2051`) and
  `cyw43_ll_parse_async_event()`'s callers when implementing that sub-step.
- The SDPCM data-frame envelope byte layout for the actual WLAN RX/TX *data* path (as opposed to
  control/ioctl, already covered above) - step 4's concern, not step 3 - not yet dug out.
  - maybe document way to use gpiozero as global external device for emulations on the RP

## Performance side quest, continued (2026-08-12) - wild-execution finding, not yet root-caused

Re-verified the earlier "Performance side quest" entry's own open item ("firmware download itself
hasn't been re-verified end-to-end against this faster baseline") after 0034 (`_execute_batch()`
native port) landed on top of this entry's own PIO/tick-batching work. **No improvement**: a bounded
9-minute run of `tests/micropython/main-cyw43.py` (real `v1.28.0` UF2, `--board pico_w`) still never
reached the REPL, unchanged from before 0034. Investigating why turned up something more
fundamental than a throughput problem.

**Established by direct elimination testing, not guessed:**

1. A bounded (`asyncio.wait_for`-wrapped) profiling harness against the real boot found ~64% of
   profiled time going into `logging.py`'s `warning()` - 41 million calls in a 25-second window.
   Counting message shapes traced this to `RP2040`'s own "Read from invalid memory address"/"Write
   to undefined address" warnings (`_rp2040.py`), not anything CYW43-specific.
2. Tracing the actual addresses hit (not just counting) found **987,379 distinct addresses in 5
   seconds, spanning `0x0041fd80` to `0xffffffff`** - essentially the whole 32-bit space, not a
   bounded scan just past RAM. A PC trace confirmed the CPU actually reaches this - the same exact
   PC history and register snapshot (PC ending at `0xfffffffe`, a register holding `0xffffffff`
   used as an apparent branch target - consistent with reading a never-populated struct field/
   callback pointer and jumping through it) reproduces byte-for-byte across separate runs.
3. **Not caused by anything in `main-cyw43.py`'s own script content.** The identical PC
   history/register snapshot reproduces with `nic.connect(...)` commented out, and even with a
   completely trivial `print("hi")` script with no `import network` at all - ruling out
   `cyw43_ll_wifi_join()`'s ioctl cascade (the original hypothesis) as the cause. Also confirmed no
   output (`print("Initializing...")`, this script's very first line) ever appears before the
   crash, so it happens before user code meaningfully runs at all.
4. **Not caused by `Cyw43439`/`GSPIBus` being attached.** The same trivial script against the same
   `v1.28.0` `RPI_PICO_W` UF2, booted on the plain `"pico"` board spec (no `Cyw43439` in
   `boards.py`'s extras at all), reproduces identically.
5. **Specific to the `RPI_PICO_W` firmware image itself.** The same trivial script against the
   plain (non-`_W`) `RPI_PICO` `v1.28.0` UF2 does not reproduce it at all (only 2-3 benign
   out-of-range hits in a small, bounded address range, matching ordinary boot-time probing).

**Conclusion: this is a bug in how this emulator handles something the `pico_w`-*variant* firmware
image's own board-specific C init touches differently from plain `pico`'s init - not a CYW43/SDPCM/
ioctl protocol bug, and not related to WiFi scan/join logic at all.** Real root cause not yet
identified - would need symbol-matched disassembly (e.g. a local build of the matching
`micropython` v1.28.0 checkout, submodules not currently initialized) to pin down the exact C call
site; not done here. `tests/micropython/main-cyw43.py` was left unmodified rather than narrowed to
avoid `connect()` - see point 3 above for why that would not have addressed the actual cause. CI's
own `ci-micropython.yml` pico-w job timeout was bumped 10m → 15m as a stopgap in a separate, small
commit; **this alone does not fix the underlying issue** - if the process genuinely never
terminates (vs. being merely slow), no timeout bump makes that job reliably pass.

**Separately found while investigating, not yet root-caused either, and not the same failure
mode:** `rp2040py micropython --image <v1.28.0> tests/micropython/main-spi.py` (raw-REPL exec mode,
plain `RPI_PICO`, no CYW43/pico_w involvement at all) hangs indefinitely - `device.aexec(...,
timeout=None)` never returns. Debug-level logging showed active `[CortexM0Core] SEV`/`[USB] Start
USB transfer` traffic clustered inside a sub-100ms window of *simulated* time, repeating for the
entire real-time duration observed - looks like a live spin/protocol stall (host and device
retrying without making simulated-time progress) rather than a genuine wait-forever-for-input
condition, but not confirmed. Does **not** reproduce the wild-execution/invalid-address signature
above (a bounded trace of the same image+script showed only the same 2-3 benign hits plain `pico`
boots normally show) - a different bug, or a different symptom of a shared underlying cause; not
yet determined which. Not yet bisected to a specific commit/branch.
