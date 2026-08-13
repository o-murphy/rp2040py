# 0027. External devices and CYW43439 / Pico W WiFi emulation (epic)

- Status: In progress
- Conceived: 2026-08-12
- Related: decisions 0028 (module layout), 0029 (board composition), 0030 (concurrency) · research
  note 0024 · fixes 0035 (wild-execution), 0037 (PIO/CPU scheduling), 0038 (ioctl-response
  correctness bug)

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 1-72 -->

# CYW43439 / Pico W WiFi emulation — research notes and implementation plan

**3g (async events + scripted scan/join) implemented and unit-tested (2026-08-13); live-boot
verification against `tests/micropython/main-cyw43.py` in progress - see its own progress-log entry
under step 3's "3g" bullet below for the outcome once that lands.** Everything before it in
"Implementation order" below is done: step 0 (board-loading API), step 1 (`ExternalDevice` proven
via `LEDMock`), step 2 (F0 bus-level `GSPIBus` decode - real firmware boots past the F0 handshake),
and step 3's sub-steps 3a (generic word-aligned block transfers -
`GSPIBus._on_clock_rising()`/`_start_response()` handle any `size`, not just one 32-bit word, via
`_word_count()`/`_words_to_value()`/`_value_to_words()`), 3b (ALP/HT/KSO clock handshake), 3c (F1
windowed backplane addressing), 3d (ARM core reset/enable registers), 3e (firmware/CLM download
acceptance - free via 3a/3c's generic F1 block writes - plus F2 packet delivery:
`GSPIBus.queue_rx_packet()`, `SPI_STATUS_REGISTER`/`SPI_INTERRUPT_REGISTER` plumbing, and the
shared `WL_D` IRQ pin reflecting real pending-packet state instead of a hardcoded LOW placeholder),
3f (SDPCM framing + generic ioctl request/response: `GSPIBus._write_wlan()` parses inbound F2
ioctl requests and answers with a generic zero-length success response via
`_build_ioctl_success_response()`, echoing the request id and tracking `bus_data_credit` so the
driver's own flow-control never stalls - corrected the SDPCM header size from an originally
estimated 14 bytes to the real 12 along the way), and now 3g (real `escan`/`WLC_SET_SSID` scripted
responses on top of 3f's generic ack - see below). **Verified against a real, unmodified
MicroPython Pico W boot (2026-08-12, post-3f)** - not just unit tests: found and fixed three real
bugs (sticky ALP/HT clock availability including a WLAN-ARM-core-triggered case with no explicit
HT request, F1's register bank missing its true 2-byte-lower bound, and missing
`CYW43_BACKPLANE_READ_PAD_LEN_BYTES` padding on backplane reads) that were silently aborting
bring-up before firmware download ever started; firmware download now genuinely runs against real
firmware (traced live), though impractically slowly - see step 3f's own "Real-firmware
verification" entry below for the full writeup. 41 tests total in `tests/test_cyw43_bus.py` as of
3g (35 before it). See "Implementation order"'s step 3 for the full sub-step breakdown and status
detail.

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

      **Done (2026-08-13), landed in `bus.py` same as every prior sub-step (not `chip.py` - see
      this bullet's own note below on why the original chip.py plan wasn't followed in practice).**
      Read the rest of `cyw43_ll_wifi_join()` (`cyw43_ll.c:2051`) and `cyw43_cb_process_async_event()`
      (`cyw43_ctrl.c`) as planned, not guessed - two real corrections to this bullet's own original
      estimate came out of that reading, both in `bus.py`'s module docstring ("Async events +
      scripted scan/join" section) in full:
      - **`escan` needs *two* events, not the one originally planned** - a `CYW43_STATUS_PARTIAL`
        result carrying the fake AP, *then* a `CYW43_STATUS_SUCCESS` completion event with no
        payload. Without the second one, `cyw43_cb_process_async_event()` never sets
        `wifi_scan_state = 2`, so `extmod/network_cyw43.c`'s own `network_cyw43_scan()` blocks for
        its full 10s `mp_event_wait_ms()` timeout on every `scan()` call instead of returning once
        the one fake result is in.
      - **Join event delivery must be queued *behind* the `WLC_SET_SSID` ack, not ahead of or
        interleaved with it** - confirmed by reading `cyw43_wifi_join()` (`cyw43_ctrl.c`):
        `self->wifi_join_state = WIFI_JOIN_STATE_ACTIVE;` there is a plain *assignment*, not an OR,
        executed the instant the SSID ack itself is received. Any join event delivered before that
        assignment would have its bits wiped out moments later. `GSPIBus.queue_rx_packet()` became
        a real FIFO (`_rx_queue`, previously a single slot silently clobbered by a second call
        before the first was drained) specifically to make "ack now, scripted events after" a safe
        sequence to queue in one call - each queued frame is still delivered as its own
        independently-framed F2 read, never concatenated.
      - The exact `cyw43_async_event_t` byte layout (2 reserved bytes, `flags` u16 BE,
        `event_type`/`status`/`reason` u32 BE each, 30 reserved bytes, `interface`, 1 reserved byte,
        then the `cyw43_ev_scan_result_t` union) was confirmed field-by-field from
        `cyw43_ll_parse_async_event()`'s own alignment-fixup copy, not assumed - see `bus.py`'s
        module docstring for the full derivation, including why `cyw43_ev_scan_result_t` must be
        built to satisfy *two* overlapping struct interpretations at once
        (`cyw43_ll_wifi_parse_scan_result()`'s own richer `_scan_result_t` view of the same bytes,
        used to compute `auth_mode` from an RSN/WPA IE scan).
      - The fixed fake AP mirrors `Wokwi-GUEST`'s own real captured shape exactly (docs/records/
        0024-cyw43-protocol.md): `ssid=b"Wokwi-GUEST"`, `bssid=42:13:37:55:aa:01`, `channel=6`,
        `rssi=-87`, open/no-privacy (`auth_mode` computes to 0 via `ie_length=0` - no IEs to scan,
        rather than fabricating real 802.11 elements).
      - Join's scripted sequence (`WLC_E_SET_SSID`/`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`) fires
        unconditionally regardless of the auth type actually requested, rather than tracking auth
        type across the whole ioctl/iovar sequence just to decide which events to send - confirmed
        safe by reading `cyw43_cb_process_async_event()`: every one of these is a plain OR into
        `wifi_join_state`'s bitmask, so an extra `_PSK_SUP` for an already-`KEYED` open network is a
        harmless no-op, not a correctness risk.
      6 new tests (41 total in `tests/test_cyw43_bus.py`, up from 35): the `escan` ack/event-pair/
      fake-AP-shape tests, the join-event-sequence-and-ordering test, and a FIFO regression test for
      `queue_rx_packet()`'s new multi-packet behavior.

      **Live-boot verification (2026-08-13) against `tests/micropython/main-cyw43.py`, both native
      CPython+Cython and PyPy+pure-Python - found and fixed one real, pre-existing bug that had
      nothing to do with scan/join scripting itself, only surfaced by actually letting real
      firmware run this far.** First attempt: both interpreters deadlocked identically (same exact
      `STALL(0;29-29)` repeating forever - this project's real-boot determinism holding even across
      interpreters) *before* `nic.active(True)` even finished printing, i.e. before `scan()`/
      `connect()` had any chance to run at all. Root-caused with a direct Python-level reproduction
      against `GSPIBus` (replaying `sdpcm_process_rx_packet()`'s own credit bookkeeping in ~50 lines
      of Python, no CPU emulation needed - milliseconds instead of another 450s boot) rather than
      re-running the full boot repeatedly to guess: `cyw43_sdpcm_send_common()`'s STALL pre-send
      check (`cyw43_ll.c:648-691`) shares *one* `bus_data_credit`/`wwd_sdpcm_packet_transmit_
      sequence_number` channel between ioctl (`CONTROL_HEADER`) sends *and* outbound Ethernet
      (`DATA_HEADER`) sends - `_write_wlan()` silently ignoring `DATA_HEADER` frames (the original,
      correct-at-the-time step 3f/g stance - real content there is step 4's NAT bridge, not built)
      meant `packet_transmit_sequence_number` still advanced for that send while
      `last_bus_data_credit` got zero corresponding update, permanently shrinking the gap between
      them; `cyw43_cb_tcpip_init()` sends at least one real outbound Ethernet frame (DHCP/ARP -
      `[CYW43] send_ethernet failed: -110` in the boot log) during `nic.active(True)` itself, well
      before any of this step's own code even runs. The reproduction confirmed the exact mechanism
      before touching any code: 20 ordinary generic-ack ioctls track credit exactly
      one-ahead-of-transmit-count forever as expected, then one unanswered data send makes them
      exactly equal, and every ioctl after that stalls out permanently.

      Fix: `_build_flow_control_response()` - a bare, ioctl-header-less SDPCM frame carrying only
      an incremented `bus_data_credit` - answers every `DATA_HEADER` write now, matching
      `sdpcm_process_rx_packet()`'s own named "flow control packet with no data" case
      (`header->size == SDPCM_HEADER_LEN`, a real, designed-for-this piece of the protocol, not
      invented here). Keeps outbound Ethernet frames themselves still silently dropped (still
      correctly step-4-deferred, no fabricated reply content) while keeping the shared credit
      channel honest. One existing test's expectation flipped to match (`DATA_HEADER` now gets a
      real, if content-free, response instead of literal silence) and one new regression test
      added for the desync itself (8 new tests total, 43 in `tests/test_cyw43_bus.py`) - the
      Python-level reproduction re-run clean afterward (200+ mixed ioctl/data sends, zero stalls,
      versus stalling by send ~21 before the fix). Re-verifying the full live boot with this fix
      in place was in progress as this entry was written - not yet folded in; check this record's
      own header for the final outcome once it lands, and don't assume "scripted scan/join events
      actually get exercised end-to-end" until that's confirmed.

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
- **Resolved (2026-08-13), see step 3's own "3g" bullet above:** `cyw43_ll_wifi_join()`'s tail end
  (`cyw43_ll.c:2051` onward - the actual `WLC_SET_SSID` call that triggers the join) and
  `cyw43_cb_process_async_event()` (`cyw43_ctrl.c` - the real consumer of the `WLC_E_*` sequence,
  not `cyw43_ll_parse_async_event()`'s callers, which just dispatch to it) are both read in full
  now; the exact scripted event sequence and ordering constraints are documented in `bus.py`'s own
  module docstring.
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

**Separately found while investigating, not yet root-caused, and not the same failure mode:**
`rp2040py micropython --image <v1.28.0> tests/micropython/main-spi.py` (raw-REPL exec mode, plain
`RPI_PICO`, no CYW43/pico_w involvement at all) hangs, still running at 100% CPU past 60 real
seconds (not confirmed whether it ever terminates). Does **not** reproduce the wild-execution/
invalid-address signature above (a bounded trace of the same image+script showed only the same 2-3
benign hits plain `pico` boots normally show) - a genuinely different bug.

Narrowed by elimination, correcting an earlier theory in this same investigation:

- **Not a general raw-REPL exec-mode issue.** A trivial `print("hi")` script (no SPI, no littlefs)
  via the identical `rp2040py micropython --image <v1.28.0> <file>` invocation completes in 0.4s,
  clean. The hang needs `main-spi.py`'s own content specifically.
- **Not (at least not simply) missing an SPI completion listener.** `peripherals/spi.py`'s
  `RPSPI.on_transmit` defaults to `lambda value: self.complete_transmit(0)` - i.e. every SPI byte
  self-completes synchronously unless something overrides it - so the initial theory ("nothing
  calls `complete_transmit()` without a listener like `tests/micropython_spi_run.py`'s") does not
  hold as stated; confirmed by reading the source, not just running it. The real mechanism is still
  unidentified - possibly DMA-channel-completion (a separate path from the plain
  `on_transmit`/`complete_transmit()` byte callback) behaves differently, not yet checked.
- **100% CPU, not idle** - real instructions are executing throughout (unlike a genuine
  wait-forever-for-an-event condition, which would show near-zero CPU), consistent with either a
  true infinite busy-loop in firmware polling a status flag that never sets, or a very slow but
  eventually-converging one (not distinguished - no run has been let go longer than ~60s).
- **Reproduces on `origin/main` and `refactor/main-thread-asyncio` too** (not yet checked on
  `feat/board-loading-api`) - i.e. not a regression from the main-thread-asyncio migration or any
  of this branch's own CYW43/`_execute_batch()` work, contrary to an initial suspicion that this
  might be the same class of cross-thread `tx_fifo` race 0018 already fixed once. Also confirmed
  CI never actually exercises this exact code path for `main-spi.py` at all - `ci-micropython.yml`
  embeds it as `main.py` via `mklittlefs` and drives it through the dedicated
  `tests/micropython_spi_run.py` (which *does* wire a real `on_transmit`/`complete_transmit()`
  listener, on a delay, matching real SPI clock timing) rather than raw-REPL exec - so this may be
  a previously never-exercised combination, not a new regression in the ordinary sense.

## Wild-execution finding root-caused and fixed (2026-08-12) - see 0035

The "wild-execution" entry above is resolved - root cause was `device/load_flash.py`'s
`MICROPYTHON_FS_FLASH_START` being a single hardcoded offset wrong for `pico_w` specifically
(silently overwriting the tail of that board's larger compiled `.data` region). Full derivation,
fix, and verification in 0035.

**A new, different, still-open issue found while verifying 0035's fix end-to-end via the real
CLI**, not the wild-execution bug (confirmed - that specific signature is gone): booting
`tests/micropython/main-cyw43.py` on `pico_w` still doesn't reach the script's own output within
30-35s. Debug logging shows a suspiciously uniform (~0.24s real-time interval) repeating sequence
- `[CortexM0Core] SEV` / `[USB] Start USB transfer, ...]` / `[PIO1] clkDivRestart not implemented`
- textually identical each cycle, reading more like a stalled retry loop than genuinely-slow-but-
progressing SPI bit-banging (the kind of shape 0027's own "Performance side quest" already
diagnosed as a real, separate throughput concern).

**Localized precisely (2026-08-12), via a live interactive session, not just the batch script:**
booting to the friendly REPL and typing `import network`/`nic = network.WLAN(network.WLAN.IF_STA)`
both return immediately and normally - **`nic.active(True)` is exactly where it hangs**, with
nothing printed afterward. This is `cyw43_wifi_up()`/`cyw43_ll_wifi_on()` in the real driver - the
ALP/HT clock handshake + ARM core bring-up (steps 3b/3c/3d), already implemented and unit-tested,
but evidently not converging against this exact real firmware end-to-end. `clkDivRestart not
implemented` (wherever that log line actually lives - not yet located) is the concrete next lead,
likely inside the gSPI bit-banging PIO program's own clock-divider handling. Not investigated
further in this pass.

## `nic.active(True)` "hang" root-caused (2026-08-12) - not a CYW43 protocol bug, a CPU/PIO/DMA scheduling-fairness problem

Follow-up session, live-traced end to end (not source-reading alone) via instrumented harnesses
driving `MicroPythonDevice` directly against the real, cached `v1.28.0` `RPI_PICO_W` UF2 - monkey-
patching `GSPIBus`/`StateMachine`/`RP2040.read_uint32`/`write_uint32` under `RP2040PY_SKIP_CYTHON=1`
(pure-Python mode, so the patches actually take effect), plus a bounded `cProfile` run and a direct
`arm-none-eabi-objdump -Mforce-thumb` disassembly of the UF2's flash image cross-checked against
live CPU register snapshots at the hot PC. Scripts are throwaway (scratchpad, not committed) but
the method - and the four findings below - are reproducible from a cold session in under an hour if
this needs re-verifying.

**Finding 1: it is not stuck - it is making genuine, measured forward progress, just catastrophically
slowly, and slowing down further over time.** A live trace of `GSPIBus._write_backplane_memory()`
(the sink for real compiled ARM firmware bytes during `cyw43_download_resource()`) shows the target
address genuinely incrementing (`0x0`, `0x40`, `0x80`, ... climbing steadily) over a 90+ second run,
not repeating. But the real-time cost *per 64-byte chunk* was measured growing across one run: ~85ms/
chunk for the first ~20 chunks, ~260ms/chunk by chunk ~40, ~420ms/chunk by chunk ~60, ~700ms+/chunk
by chunk ~115 - an accelerating, not flat, per-chunk cost. Real firmware is ~229KB / 64-byte chunks
≈ 3670 chunks total; even at the *initial* (fastest, not-yet-decelerated) rate this is minutes, and
the observed deceleration means it likely never finishes in practice - which is exactly what reads
as "hangs forever" to a live user, even though no step of it is a true infinite loop.

**Finding 2: root mechanism, confirmed via live register+disassembly cross-reference, not guessed.**
The CPU's hot PC (sampled directly from `mcu.core.registers`/`pc` during the stall) disassembles
(`arm-none-eabi-objdump` against a UF2→flat-binary conversion, Thumb mode, `--adjust-vma=0x10000000`)
to exactly pico-sdk's own `dma_channel_abort()` body: write `1 << channel` to `DMA_BASE +
CHAN_ABORT` (`0x50000444`), then busy-poll `AL1_CTRL`'s `BUSY` bit (`0x50000000 + channel*0x40 +
0x10`, bit 24 - both offsets confirmed byte-for-byte against
`pico-sdk/src/rp2040/hardware_regs/include/hardware/regs/dma.h`, local checkout, submodule already
initialized). Live-traced DMA register writes (`RP2040.write_uint32` patched to log every
`0x50000000-0x500FFFFF` access) show real firmware's `cyw43_bus_pio_spi.c` transfer helper
configuring **two chained DMA channels** - channel 0's `WRITE_ADDR=0x50300010` (PIO1's `TXF0`,
confirmed from `pio.py`'s own register layout), channel 1's `WRITE_ADDR` back into RAM off PIO1's
`RXF0` (`0x50300020`) - the standard SDK "DMA feeds PIO TX FIFO, DMA drains PIO RX FIFO" gSPI
bit-bang pattern, exactly matching `cyw43_bus_pio_spi.c`'s real implementation (nothing
CYW43-protocol-specific about this part - it's the generic mechanism every PIO-driven SPI transfer
in the SDK uses). **The abort+reconfigure+retrigger cycle repeats because, from the firmware's own
point of view, each attempt is legitimately timing out** - real firmware's own transfer helper has a
bounded wait-then-retry loop around the DMA, and our emulated DMA/PIO pairing is too slow to ever
finish inside that window, so the *retry* is real, correct driver behavior reacting to a real
(emulator-side) latency problem - not a CYW43 bug being masked.

**Finding 3: why the DMA/PIO pairing is this slow - a CPU/PIO scheduling-fairness gap, confirmed via
direct instrumentation of both sides.** `Simulator._execute_batch()`
([simulator.py:138](../../src/rp2040py/simulator.py)) runs up to 1,000,000 CPU instructions (or a
real-time budget, whichever comes first) before its *one* `await asyncio.sleep(0)` yields control
back to the engine-room event loop. `RPPIO.run()` ([peripherals/pio.py:321](../../src/rp2040py/peripherals/pio.py))
- the task that actually steps the gSPI bit-bang state machine - is a *separate*, competing
`asyncio.Task` on that same single-threaded loop, so it only gets a scheduling turn once per (up to)
million-instruction CPU batch. Live-patching `StateMachine.wait()`/sampling `waiting`/`wait_type`
every 3s over a 60s run shows PIO1 SM0 (the gSPI program) spending nearly all its time in
`WaitType.OUT` (autopull stall - blocked on the TX FIFO having data) and executing real instructions
only a couple of times across a 45-second `cProfile` window, against 18-24 **million** CPU
instructions executed in that same window (`_cortex_m0_core.py:1442(execute_instruction)` call
count) - `peripherals/dma.py` and `peripherals/_state_machine.py` functions barely register in that
profile at all. `RPDMAChannel.transfer()`/`schedule_transfer()` ([peripherals/dma.py:214](../../src/rp2040py/peripherals/dma.py))
were checked and are *not* the bug - they correctly pace one word per DREQ assertion via a
`SimulationClock` alarm (hardware-accurate FIFO backpressure) - but each such word now needs a full
CPU-batch/PIO-task/clock-alarm round trip, and the already-documented per-`clock.tick()`-call
overhead ("Performance side quest" above) compounds *per word* rather than per block. Net effect:
a real gSPI transaction that should take on the order of a microsecond of simulated time costs many
milliseconds of real wall-clock scheduling latency, and CYW43 is the first thing in this codebase
that drives PIO this heavily via chained DMA, which is why nothing else surfaced this before.

**Finding 4, not yet chased down - the likely explanation for the deceleration in Finding 1.** The
CPU/PIO scheduling-latency floor from Finding 3 alone would predict a *flat* per-chunk cost, not an
accelerating one. Something scales with total elapsed work on top of that floor - candidates not
yet isolated: `GSPIBus._backplane_memory`'s sparse dict growing to thousands of entries over a long
download, uncancelled `SimulationClock` alarms/callbacks accumulating, or GC pressure from the
sheer object count of a long-running trace. Next session's first move here should be a `cProfile`
diff between an early time slice and a late time slice of the same run (not just one aggregate
profile) to catch what's actually growing.

**Explicitly ruled out this session, with evidence - do not re-check:**
- **Not a CYW43/SDPCM/ioctl protocol bug.** `_write_wlan()`/`queue_rx_packet()`/ioctl-response
  building never even get exercised in these traces - firmware download itself hasn't finished, so
  the driver hasn't reached the ioctl-exchange phase yet at all.
- **Not the `clkDivRestart not implemented` warning itself.** It's benign - fires once per real gSPI
  transaction (`StateMachine.clk_div_restart()`, [peripherals/_state_machine.py:660](../../src/rp2040py/peripherals/_state_machine.py)),
  matching real hardware's own `pio_sm_clkdiv_restart()` SDK call; a real fix could implement it
  properly, but doing so would not touch the actual bottleneck above.
- **Not a DMA-to-peripheral read/write dispatch bug.** `write_uint32_atomic()` → `RPDMA.write_uint32()`
  → `RPDMAChannel.write_uint32()` was confirmed reaching the DMA peripheral correctly via live
  address tracing (register values read back exactly what was written).
- **Not a true infinite loop or protocol-level deadlock.** Backplane-write addresses genuinely,
  monotonically advance over a 90+ second trace - the earlier "textually identical, stalled-retry-
  loop" read on the debug log (previous session's finding, at the top of this section) undersold
  real slow progress as a hang signature; both readings were partially right; it *is* a retry loop
  (Finding 2), and it *is* real (if glacial) progress (Finding 1) at the same time.

**Not fixed here, per this repo's document-vs-implement convention** (see `CLAUDE.md`) - this is a
cross-cutting `Simulator`/`RPPIO`/`RPDMA`/`SimulationClock` scheduling-fairness redesign (e.g. CPU
batches yielding early when a PIO instance has pending DREQ-driven work, rather than always running
to their full instruction/time cap), not a CYW43-specific patch, and belongs as a continuation of
this doc's own "Performance side quest" work above, not a new independent bug. Recommend picking up
there next, starting with Finding 4's profile-diff, before attempting a scheduling change.

## Scheduling fix landed - but a separate, deeper stall found underneath (2026-08-13, see 0037)

Follow-up session, explicit user go-ahead to fix (not just document) the CPU/PIO scheduling gap
above. **Two of this entry's own findings turned out to be measurement artifacts of testing under
`RP2040PY_SKIP_CYTHON=1` (forced pure Python), not accurate for the real, default native path -
corrected here, not deleted, per this doc's append-only convention:**

- **Finding 1 ("not stuck, just catastrophically slow") was wrong for native mode.** A native-mode
  live trace of the same `nic.active(True)` scenario (`mcu.pio[1].machines[0].pc`/`.cycles`
  sampled every few seconds, no env var override) showed `pc` genuinely frozen at `27` and `cycles`
  cycling through exactly {260, 580, 900} - never higher - for a full 120s run. That *is* a true
  livelock in native mode, not merely slow; the pure-Python trace's "real, if glacial, progress"
  reading only held because forcing pure Python also happened to narrow the CPU/PIO speed gap
  enough to mask it.
- **Finding 3's mechanism (PIO "barely stepping," ~2 real instructions in a 45s profile window)
  was real for the specific window profiled, but not representative** - that window happened to
  land before firmware download reached its PIO-heavy phase. A `cProfile` diff gated on actual
  firmware-download chunk progress (not fixed wall-clock offsets) showed PIO stepping
  (`_state_machine.py`'s `execute_instruction()`/`step()`/`check_wait()`) as the dominant cost
  *during* that phase - the real bottleneck is the CPU/PIO scheduling gap itself (root-caused
  below), not PIO simply failing to run.

**Root mechanism, unchanged and confirmed correct**: `RPPIO.run()` (`peripherals/pio.py`) was a
separate, competing `asyncio.Task` on the same engine-room loop as `Simulator._execute_batch()`'s
own up-to-1,000,000-instruction CPU batches, entirely decoupled from the same simulated clock/
instruction cadence that `RPDMAChannel`'s DMA pacing and everything else correctly uses via
`clock.tick()`. **Fixed in 0037**: `_execute_batch.py`/`native/_simulator.pyx` now step every
non-stopped `RPPIO` once per CPU instruction/idle-jump directly, the same "driven inline, not a
competing task" shape `clock.tick()` already had; `RPPIO.write_uint32()`'s `CTRL` branch only
creates the old competing task when `rp2040.simulator is None` (the no-`Simulator`-owner test-
fixture path, unaffected). Full derivation, the fix itself, and its own verification
(`rp2040py bench`, `pre-commit run --all-files`) are in 0037 - not duplicated here.

**Verified this genuinely fixes the livelock, not just changes its shape**: re-tracing the same
native-mode scenario after 0037 landed shows real, multiple gSPI transactions completing - `cycles`
reaching well past the old 900 ceiling, `pc` moving through 0/26/27, `tx_fifo` genuinely filling
and draining - for roughly the first 35-40s of a boot, where before it was frozen from the first
few seconds. This is 0037's actual scope and it is done.

**A new, different, still-open issue found immediately after, live-traced but not root-caused
in this pass**: `nic.active(True)` still doesn't return, even given a genuinely long bound (traced
to a full 10 minutes/600s with no change - confirmed permanent within that window, not merely
slow). What actually happens, precisely, not guessed:

- Around the same ~35-40s mark every run (reproduced twice, same signature both times, not a
  one-off race), PIO1 SM0 enters a `WaitType.OUT` autopull stall (blocked wanting one more 32-bit
  word) and **stays there permanently** - `tx_fifo.empty=True`, `cycles` frozen at a fixed value
  (519 in both reproductions), never resumes. The DMA channel feeding it (`_read_addr=0x20002064`,
  a small fixed buffer matching the driver's own reusable `spid_buf`, not the growing firmware-
  image buffer) shows `active=False`, `_trans_count=0` - it finished its own configured transfer
  cleanly, with `_trans_count_reload=1` (a single 32-bit word).
- Read against the real source
  (`pico-sdk/src/rp2_common/pico_cyw43_driver/cyw43_bus_pio_spi.c`/`.pio`, local checkout): a
  write-only `cyw43_spi_transfer()` (`rx == NULL`) deliberately reconfigures the SM's `wrap_top` to
  loop back to the *start* of the TX loop once the driver's own requested bit count is exhausted -
  i.e. the SM structurally *always* ends a write-only transfer sitting in exactly this "wants one
  more TX word" stall by design. Real firmware's own termination signal for this case is *not*
  "DMA finished" - it explicitly clears `FDEBUG`'s `TXSTALL` bit for this SM
  (`pio->fdebug = fdebug_tx_stall`) then polls `while (!(pio->fdebug & fdebug_tx_stall))
  tight_loop_contents();`, relying on real silicon re-asserting `TXSTALL` continuously every cycle
  the SM is genuinely stalled. So a permanently-stalled SM is *expected* at this point - the open
  question is why the driver's own poll loop for it never resolves in our emulation.
- **Not yet confirmed which side is actually wrong.** `RPPIO`'s own `FDEBUG`/`tx_stall` plumbing
  (`peripherals/pio.py`/`peripherals/_state_machine.py`) looks structurally correct on inspection -
  sticky-until-`write_fifo()`-clears-it, re-asserted by `execute_instruction()`'s `OUT` branch the
  instant a real stall begins - but this was read, not empirically proven against a live trace of
  the driver's own `FDEBUG` reads. **Contradicting evidence against a simple "poll loop never
  sees the bit" theory**: if the CPU were genuinely stuck in that specific 2-3 instruction
  `tight_loop_contents()` spin, repeated PC sampling (every 0.4-2s) would keep landing on the same
  couple of addresses - instead it shows widely varying PCs (`0x10074xxx`, `0x10053axx`, and
  others) for the full 10 minutes, i.e. the CPU is doing real, varied work, not spinning on that
  poll. What that varied work actually is was not identified in this pass - one candidate lead
  (not confirmed): a register sampled mid-loop at one of those addresses looked like `memcmp()`
  being called with a *monotonically growing* pointer argument walking from an implausible low
  value (0xfe) up through and past valid SRAM's actual bounds (RP2040 SRAM ends around 0x20042000;
  the pointer was observed past 0x20313000) over several seconds - consistent with, but not proven
  to be, a walking-off-the-end memory scan (the same *class* of bug 0035 fixed a different instance
  of, not confirmed to be the same one). Could equally be unrelated, legitimate MicroPython
  background work (GC, import machinery) that's simply slow here for its own reasons.
- **Explicitly not chased further in this pass**: this needs either a symbol-matched disassembly
  (a real local MicroPython v1.28.0 build with debug info, not just the raw compiled UF2 via
  `arm-none-eabi-objdump` with no symbols - which is what every trace in this doc so far has used)
  or a live GDB session (`rp2040py micropython --gdb`, already supported by this CLI, not yet
  tried for this investigation) to identify the actual function this loop belongs to with
  confidence, rather than continuing to guess from raw addresses.

## Follow-up stall root-caused precisely, via a real symbol-matched build (2026-08-13)

The `memcmp`-with-a-growing-pointer/"walking off SRAM" theory above is **wrong - retracted, not
just superseded.** Built the actual firmware locally instead of guessing further: this machine
already has a `micropython` checkout at exactly `v1.28.0` (`git describe` confirms it, submodules
for `cyw43-driver`/`pico-sdk` already pinned to the matching versions) - `make BOARD=RPI_PICO_W
submodules && make BOARD=RPI_PICO_W` in `ports/rp2` (after fetching the additionally-needed
`lib/lwip`, `lib/btstack`, and `pico-sdk`'s own `lib/tinyusb` submodules, none initialized by
default) produces a real `firmware.elf` with full debug symbols. Booting the simulator against
*this build's own* `firmware.uf2` (not the originally-downloaded release UF2 - a different
compilation, so its addresses don't correspond to this ELF's symbols at all; this distinction
matters and cost real time to notice) reproduces the exact same stall signature (`dma0_reload=1`,
`cycles=519`, permanent `WaitType.OUT`), confirming it's the same bug, not build-specific -
`arm-none-eabi-addr2line -f -C -e firmware.elf <addr>` then resolves every address from this
session's traces with certainty:

- **The CPU's PC at the exact moment of the stall is `cyw43_delay_ms()`**
  (`extmod/cyw43_config_common.h:101`) - not stuck *inside* the SPI/PIO code at all by this point,
  already past it. Its body is a plain, correct busy-wait:
  ```c
  static inline void cyw43_delay_ms(uint32_t ms) {
      uint32_t us = ms * 1000;
      uint32_t start = mp_hal_ticks_us();
      while (mp_hal_ticks_us() - start < us) {
          CYW43_EVENT_POLL_HOOK;   // mp_event_handle_nowait()
      }
  }
  ```
  `cyw43_ll_wifi_on()` calls this with small, bounded values (50ms after setting country, 50ms
  after CLM load, 50ms after `event_msgs`, 50ms after `WLC_UP`, one `cyw43_delay_us(150000-dt)`) -
  a few hundred milliseconds of *requested* delay, total, across the whole call. The permanently-
  stalled PIO SM from the entry above is a red herring for this specific stall - it's an
  *abandoned* transaction the driver already timed out on and moved past (matching
  `cyw43_do_ioctl()`'s own bounded `CYW43_IOCTL_TIMEOUT_US` retry logic), not what's currently
  blocking forward progress.
- **The addresses sampled *after* the stall resolve to real, legitimate code, not a bug or a
  memory-scanning artifact**: `sync_ep_buffer`/`advance_index`
  (`lib/tinyusb/src/portable/raspberrypi/rp2040/rp2040_usb.c`,
  `lib/tinyusb/src/common/tusb_fifo.c`) and newlib's own `memcmp`. `rp2` doesn't define its own
  `MICROPY_INTERNAL_EVENT_HOOK` (`py/mphal.h`'s default, `(void)0`, applies), so
  `CYW43_EVENT_POLL_HOOK`/`mp_event_handle_nowait()` itself is a *cheap*, non-blocking call on this
  port (just `mp_handle_pending()`) - it is not where the USB activity comes from. `sync_ep_buffer`/
  `advance_index` are real **USB interrupt handler** code (tinyusb's `dcd_rp2040.c`/`tud_int_handler`
  path), confirmed against `peripherals/usb.py`: `USBCTRL` schedules a Start-of-Frame interrupt
  every 1ms of simulated time (`# SOF every 1ms = 1,000,000 ns`), correctly clock-coupled (a
  `SimulationClock` alarm, the same mechanism `RPDMAChannel` correctly uses - not the
  competing-task problem 0037 fixed). A `cyw43_delay_ms(50)` call should see roughly 50 real SOF
  interrupts fire during its 50 *simulated* milliseconds.
- **Conclusion: this is a real-wall-clock performance problem, not a correctness bug or hang** -
  the same *class* of issue as this doc's own "Performance side quest" (PIO stepping cost) and
  0037 (PIO/CPU scheduling), just manifesting via USB SOF-interrupt-handling cost instead. A few
  hundred milliseconds of *simulated* delay, spread across a few dozen SOF interrupts each costing
  real Python-level emulation time to service, is turning into multiple real minutes. Not measured
  precisely in this pass (no per-interrupt profiling done yet) - the next concrete step for
  whoever picks this up is a bounded `cProfile` run scoped specifically to the USB IRQ handler
  path (`peripherals/usb.py`'s interrupt dispatch, mirroring the profiling method 0031/0034/0037
  already used for PIO) to confirm the exact per-SOF real-time cost and decide whether it needs the
  same kind of fix (cheaper handling, or coarser interrupt granularity) or is simply inherent to
  emulating USB in pure Python at this fidelity.
- **Everything in the immediately-preceding entry that is now superseded, precisely**: the
  `memcmp`-with-growing-pointer read was a real observation but a wrong interpretation - it's
  `memcmp` being called repeatedly from legitimate tinyusb/newlib code (different call sites, not
  one call walking a buffer), not a single scan walking off SRAM's bounds; there is no
  `wild-execution`-class memory-safety bug here. The `dma_channel_abort()`/FDEBUG-polling
  mechanism read from `cyw43_bus_pio_spi.c` in that entry is real and accurate, just not what's
  currently executing by the time of the stall - the driver already moved past it via its own
  bounded retry/timeout logic, same conclusion reached differently.

## USB-SOF hypothesis retracted; real cost was 0037's own fix; still not fully resolved (2026-08-13)

The previous entry's "next concrete step" (profile the USB IRQ handler path) was done - and its
own hypothesis was wrong. A bounded `cProfile` run scoped to the `cyw43_delay_ms()` phase (gated
on real elapsed time against the just-built local firmware, not a fixed offset) showed
`peripherals/usb.py` at a combined ~0.02s out of a 20s window - negligible, not the cost. The
actual dominant cost, unexpectedly, was **0037's own fix**: `RPPIO.step()`/`check_changed_pins()`
at ~8s+3s of the same 20s window (14.37 million calls) - because PIO1's SM0, permanently
`waiting=True` after the stalled write-only transaction two entries up, never gets its `stopped`
flag set (a real hardware SM legitimately doesn't need to be disabled just because it's stalled -
`pio.stopped` only reflects the `CTRL` register's `should_run` bits, untouched here), so 0037's
"step every non-stopped `RPPIO` once per CPU instruction" was paying full `step()` cost for a
machine that could not possibly make progress - for the rest of the session, on every single
instruction.

**Fixed**: confirmed every `StateMachine` wait type (`IRQ`, `PIN`, `RX_FIFO`, `TX_FIFO`/`OUT`)
already has its own targeted, event-driven re-check elsewhere in the codebase
(`RPPIO.irq_updated()`, `GPIOPin._apply_input_value()`, `StateMachine.read_fifo()`/`write_fifo()`)
that flips `waiting` back to `False` the instant whatever it's blocked on actually changes - so
`_execute_batch.py`/`native/_simulator.pyx` now only call `pio.step()` when at least one machine is
`enabled and not waiting` (`_has_runnable_machine()`), skipping the wasted call entirely for a
PIO instance that's enabled but has nothing runnable right now. Verified via the same profiling
method (cost gone) and `rp2040py bench`/`pre-commit run --all-files` (no regression, both builds
pass) - see 0037's own updated Verification section.

**Still open after this fix, confirmed via a 600s bounded run against the real, locally-built
`v1.28.0` firmware (not the downloaded release UF2 - see the entry above for why that distinction
matters)**: `nic.active(True)` does not complete even given 10 real minutes. The CPU's PC keeps
varying throughout (genuine, ongoing execution, not a second livelock) and the previous entry's own
root cause still holds precisely: `cyw43_delay_ms()`'s busy-wait needs the *simulated* clock to
advance through the driver's own requested delay total (a few hundred milliseconds, spread across
several calls in `cyw43_ll_wifi_on()`), and advancing simulated time this way still costs far more
real wall-clock time in this Python-level emulator than the delay itself represents - the same
*class* of limitation as this doc's own "Performance side quest" (PIO stepping cost) and 0037 (the
scheduling gap), just now bottlenecked on raw per-instruction interpretation throughput with no
further starvation/scheduling bug found underneath it. Consistent with an independent data point
from live testing (not this investigation): `v1.23.0` (an older, presumably shorter `cyw43-driver`
bring-up path) returns from `nic.active(True)` quickly on this same emulator, while `v1.28.0`'s
longer sequence does not - matching "total requested delay scales with driver version," not a
version-specific bug.

**Correction (2026-08-13, later session): this "raw throughput ceiling" conclusion was
half-right, mixed together with a real, separate, previously-undiagnosed correctness bug - see
the next entry below, "`nic.active(True)` real root cause: a `GSPIBus` ioctl-response bug, not
(only) a throughput ceiling," for the fix and the corrected picture. Kept here, not deleted, per
this doc's append-only convention - the raw-throughput-ceiling class of limitation this entry
describes is still real and still unfixed for the portion of the stall that remains after that
fix, just smaller in scope than believed here.**

**Not fixed here** - this is a genuine raw-throughput ceiling, not a scheduling or correctness bug,
and needs its own investigation (most likely a native Cython port of more of the peripheral/bus
dispatch path, in the same spirit as 0013/0031/0034, rather than another `_execute_batch()`-level
change) rather than a quick follow-up. CI's own `ci-micropython.yml` was adjusted in this session
to stop running the pico_w WLAN job against `1.28.0` (kept only for `1.23.0`, which completes
quickly) - a pragmatic workaround given a 10+ minute runtime isn't practical for CI regardless of
correctness, not a fix.

## Same-day follow-up: several throughput ideas tried live, all rejected (2026-08-13)

After 0037 landed (see above), tried a handful of quick, uncommitted experiments against this
throughput ceiling before stopping for the day. None of them are in the tree - documented here
purely so a future session doesn't re-spend time on the same dead ends.

- **PyPy** (`uv run --python pypy3.11 ...`, native extensions unavailable under PyPy by design -
  see 0016's own PyPy note). Inconclusive at best: reached the same stall point *slower* than
  CPython+Cython (JIT warm-up cost), and in a 600s bounded run the CPU's PC sat completely frozen
  on one single address for the last 130+ seconds - a different, more suspicious shape than the
  CPython+Cython baseline (which kept visibly varying the whole time). Not root-caused; flagging
  as a possible PyPy-specific issue worth a closer look, not just "PyPy is slower here."
- **`RP2040PY_CLOCK_TICK_BATCH=1000`** (already-existing opt-in from 0031). Measured *slower* than
  no batching (~68s to first stall vs. ~38-40s baseline) - the alarms in this workload (DMA
  word-pacing, USB SOF) are frequent enough that batching's own accounting overhead isn't repaid.
  Also surfaced an `IndexError` in `peripherals/io.py`'s `get_pin_from_offset()` after
  `device.stop()` in the same run - not confirmed whether batching-specific or a pre-existing
  shutdown-ordering artifact; worth a dedicated look either way.
- **Underclocking the modeled CPU rate** (`_execute_batch.py`/`native/_simulator.pyx`'s
  `cycle_nanos`, 125MHz→12.5MHz - deliberately the *opposite* of the intuitive "overclock to go
  faster," since this constant controls how much simulated time each already-slow real instruction
  is credited for, not real execution speed; overclocking would need *more* real instructions per
  simulated millisecond, not fewer). Genuinely sped up the early gSPI-transaction phase as
  predicted, but has a real, confirmed side effect: USB SOF and other alarm-scheduled peripheral
  activity fire at fixed *simulated* nanosecond intervals independent of this constant, so
  underclocking makes them fire more often *relative to real CPU instructions executed* - measured
  making `v1.23.0` (normally ~30-33s) noticeably slower. Reverted.
- **A separate thread for PIO stepping.** Not implemented at all - ruled out on the same grounds
  0026 (main-thread-asyncio migration) already established: the GIL means no real parallelism for
  pure Python/Cython work without `nogil`, and this project already went through a
  "background-thread engine room → real race conditions (0018's `tx_fifo` race) → single-threaded
  main-thread model" cycle once; CPU/PIO/DMA interact too finely (potentially every CPU
  instruction) for cross-thread synchronization to pay for itself.
- **Removing the 1-step-per-CPU-instruction throttle 0037 itself introduced** - replace `if
  not pio.stopped and _has_runnable_machine(pio): pio.step()` with `while not pio.stopped and
  _has_runnable_machine(pio): pio.step()`, letting a runnable PIO run all the way to its own next
  real wait every time it's visited, not just one step. **Not actually disproven** - reverted
  because a fair comparison wasn't possible: the reverted-vs-with-PoC comparison that looked bad
  turned out to be confounded by the machine's own CPU frequency governor sitting in `powersave`
  (~1.4GHz) for unrelated reasons during part of that testing (confirmed via
  `/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`/`scaling_cur_freq` - `rp2040py bench`
  swung between ~8M and ~25M instructions/sec purely from this, independent of any code change).
  **This is the most promising lead from this session's follow-up and deserves a real, fairly-measured
  retry** - with one real risk to guard against first: if any PIO program loaded during boot
  (not necessarily the CYW43 gSPI one) never naturally reaches a wait state (a legitimate,
  intentionally free-running design, not unheard of for e.g. clock-generator-shaped programs),
  this loop would burn a huge number of extra `step()` calls on every single CPU instruction with
  no cap - needs a real, principled bound (or a check that the PC is making progress) before
  landing, not just an arbitrary iteration guard.
- **The user's own architectural idea, not yet tried**: since `Cyw43439`/`GSPIBus` already lives as
  a decoupled `ExternalDevice`, bypass bit-level PIO/DMA simulation entirely for CYW43 gSPI traffic
  specifically - detect a DMA transfer targeting PIO1's `TXF0`/`RXF0` (the addresses this
  investigation already confirmed: `0x50300010`/`0x50300020`), read the source buffer directly from
  RAM, dispatch it through `GSPIBus`'s own already-fast, already-correct logical
  `read_register()`/`write_register()` (or the full wire-level `_on_cs_change()`/`_on_clock_rising()`/
  `_on_clock_falling()` callbacks, sourced from RAM instead of real PIO-driven GPIO edges, to reuse
  proven framing/endian logic exactly rather than re-deriving it), write the result directly back to
  RAM, and mark the DMA/PIO state as if the transfer completed normally - crediting the clock with
  the real elapsed nanoseconds the bit-banged version would have taken, so wall-clock-dependent code
  downstream doesn't desync. Unlike every idea above, this is CYW43-specific (would live in
  `external/cyw43/`), doesn't touch shared simulator infrastructure, and removes the *need* for
  thousands of PIO steps per transaction instead of just making each step marginally cheaper -
  plausibly the highest-leverage idea from this whole investigation, but a real design/
  implementation effort, not a quick experiment.

**Non-technical lesson, worth repeating**: several hours of this follow-up were spent chasing
apparent regressions that were actually - or at least partly - explained by the test machine's own
CPU frequency governor silently sitting in `powersave` for stretches of the session. Check
`cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`/`scaling_cur_freq` *before* trusting
any wall-clock timing comparison in this kind of investigation, not after a result looks
surprising.

## `nic.active(True)` real root cause found and fixed (2026-08-13) - see 0038

The prior entry's "raw throughput ceiling" conclusion was real but incomplete: a live,
register-level trace against a symbol-matched build (new session, new machine) found a genuine,
previously-undiagnosed correctness bug underneath it - `GSPIBus`'s ioctl responses (step 3f) always
carried zero payload bytes, so a `SDPCM_GET` caller (`cyw43_ll_wifi_update_multicast_filter()`'s
`mcast_list` query) read its own unmodified request buffer back as the answer, misreading the ASCII
bytes of the iovar name itself as a ~1.9-billion-entry loop count and walking `memcmp()` off the end
of SRAM - indistinguishable from sustained real CPU execution until this trace pinned the exact
mechanism. Fixed by zero-filling the response payload to the request's own length instead. Verified
end-to-end: `v1.28.0` now completes `nic.active(True)` (and the rest of `main-cyw43.py`) in ~450s
native / ~212s under PyPy, `active: True`, no unhandled exception - the remaining per-call cost is
the same already-tracked raw-throughput ceiling (now correctly scoped to just the driver's own
bounded `STALL` retry loop, not an unbounded walk). Full derivation, the fix itself, and the timed
verification are in 0038 - not duplicated here since it's a fix to shared bus-response logic with
its own clear before/after, not cyw43-implementation-order-specific.
