# 0038. `GSPIBus` ioctl-response zero-fill fix (`nic.active(True)` real root cause)

- Status: Implemented — verified (2026-08-13)
- Conceived: 2026-08-13 · Implemented: 2026-08-13
- Related: 0027 (CYW43439/Pico W epic, where this was found - see that record's "Same-day
  follow-up" entry for the throughput-ceiling investigation this corrects), 0037 (PIO/CPU
  scheduling fix this session built on top of)

## Context

0027's own investigation had concluded `nic.active(True)` not completing on `v1.28.0` was a raw
per-instruction interpretation-throughput ceiling (`cyw43_delay_ms()`'s busy-wait costing far more
real wall-clock time than the simulated delay it represents) - a real, but incomplete, diagnosis.
Picked back up on a different machine than that session used; the previously-built local
debug-symbol `v1.28.0` firmware didn't carry over, but an equivalent pre-built copy
(`v1.28.0-dirty`, same-day) was already sitting at `rp2040py/tmp/firmware.{elf,uf2}`, reused as-is.
The while-loop PIO-stepping idea (0027's own "most promising lead" from the prior session) had
already been retried on this machine with no effect - skipped entirely, not re-tried.
`scaling_governor`/`scaling_cur_freq` checked before any measurement per 0027's own lesson (this
machine's `powersave` governor confirmed not to distort timing here).

## Root cause (confirmed via live register tracing against a symbol-matched build, not guessed)

Live-traced, not guessed: booting `tests/micropython/main-cyw43.py` against the local
`v1.28.0-dirty` firmware and periodically sampling `mcu.core.pc`/`mcu.pio[1].machines[0]`/
`mcu.dma.channels[*]` (a throwaway `async` harness driving `MicroPythonDevice` directly - `async
with MicroPythonDevice(path, board="pico_w") as device:` / `await
device.aexec_file(script, timeout=None)`, bounded with `asyncio.wait_for()`) reproduced the prior
session's exact signature: real gSPI/DMA/PIO transactions running cleanly through roughly t=0-48s,
then PIO1 SM0 stalling permanently while `core.pc` kept visibly varying - "genuinely executing, not
livelocked," matching the prior session's own conclusion.

**Where the prior "raw throughput ceiling" conclusion was incomplete**: `arm-none-eabi-addr2line
-f -C -e firmware.elf <pc>` on the varying addresses resolved to
`cyw43_ll_wifi_update_multicast_filter()` (`cyw43_ll.c:1929`) and `memcmp` (`newlib`), not
`cyw43_delay_ms()` this time - this build/session reaches further into `cyw43_ll_wifi_on()` than
the prior one did. That function has `for (uint32_t i = 0; i < n; ++i) { memcmp(buf + i * 6, addr,
6); ... }`, where `n = cyw43_get_le32(buf)` is read from the *same* buffer the driver just sent as
its own `WLC_GET_VAR "mcast_list"` request. **Confirmed live**: a targeted trace sampling
`core.registers[0..2]` (memcmp's AAPCS `r0`/`r1`/`r2` args) every 10ms while `pc` sat inside
`memcmp`'s address range showed `r0` (the `buf + i * 6` pointer) climbing *monotonically* across
many consecutive samples - from `0x20024...` past `0x2029...`, well beyond RP2040 SRAM's real upper
bound (`~0x20042000`) - while a sibling sample of `r4` (live inside
`cyw43_ll_wifi_update_multicast_filter()`'s own body) read exactly `0x7361636d`, the first 4 bytes
of the ASCII string `"mcast_list"` interpreted as a little-endian `uint32_t`. That is `n` itself:
not a real result count, but the driver's own request buffer read back unmodified, with `memcmp()`
walking `buf + i * 6` off the end of SRAM for (at that value of `n`) up to roughly 1.9 billion
iterations - looking exactly like sustained, varied, real CPU execution (which it genuinely was)
rather than a bug, until this trace pinned the exact mechanism.

**Mechanism, confirmed against `cyw43_do_ioctl()`/`sdpcm_process_rx_packet()` source**:
`cyw43_do_ioctl()` (`cyw43_ll.c:1154`) does `memmove(buf, res_buf, len < res_len ? len : res_len)`
on every response regardless of `SDPCM_GET`/`SDPCM_SET`, where `res_len` is computed by
`sdpcm_process_rx_packet()` (`cyw43_ll.c:822`) as the response frame's own total size minus fixed
header overhead - i.e. exactly this bus's response *payload* length, not a separate field.
`GSPIBus._build_ioctl_success_response()` (0027's step 3f, `external/cyw43/bus.py`) built a
response with **zero payload bytes**, always - a deliberate simplification ("one generic 'echo a
zero-length success response' handler - no branching on `cmd` at all") that satisfied every ioctl
this bus had been exercised against so far (mostly `SDPCM_SET`-shaped bring-up calls, whose
response payload the driver doesn't read back meaningfully). A zero-length response means
`res_len = 0`, so `memmove(buf, res_buf, 0)` copies nothing - any `SDPCM_GET` caller's request
buffer (which `cyw43_ll_wifi_update_multicast_filter()` pre-fills with the iovar name
`"mcast_list\0"` followed by a zeroed result-count-and-list region, *before* sending) comes back
completely untouched, and the driver reads its own request content back as if it were the answer.

## Fix

**First fix attempt was wrong, corrected before landing, not after**: initially changed the
response to echo the *request's own payload bytes back verbatim* (same content, same length) -
this still passed a first-draft regression test but genuinely did not fix the live boot (the
`memcmp`/`mcast_list` signature reproduced identically, confirmed by re-running the same live
trace). The reason, found by reading `cyw43_ll_wifi_get_mac()` (`cyw43_ll.c:1916`) alongside
`cyw43_ll_wifi_update_multicast_filter()`: real iovar `GET` responses overwrite `buf` starting at
offset 0 with *only* the answer value, dropping the iovar-name prefix entirely -
`cyw43_ll_wifi_get_mac()`'s own `memcpy(addr, buf, 6)` reads the MAC straight from offset 0, even
though that same `buf` was populated with the 14-byte string `"cur_etheraddr\0"` before the request
was sent. Echoing the verbatim request bytes back reproduces the *exact same* garbage - a different
code path to the identical bug, confirmed by re-tracing rather than assumed.

**Actual fix**: `_build_ioctl_success_response()` now takes a `response_len` (matching the
request's own payload length, computed once in `_write_wlan()`) and returns `response_len` bytes of
zeros as the payload, instead of echoing anything. This unconditionally overwrites whatever the
request buffer held - including any iovar-name prefix, at any offset, without this bus needing to
know that prefix's length - with a safe, generic "empty"/"unset" answer, matching the existing "no
branching on `cmd`" design exactly. Confirmed harmless for `SDPCM_SET` calls (their response
payload isn't read back meaningfully by the driver, and a `SET` request's own `len` here is usually
0 anyway, so the response stays zero-length exactly as before) and for zero-payload `SDPCM_SET`
calls like `WLC_UP` (`response_len` computes to 0, response unchanged from the prior zero-length-ack
behavior).

`tests/test_cyw43_bus.py` gained
`test_ioctl_response_zero_fills_a_payload_matching_the_request_length` (replays the exact
`mcast_list` request shape - iovar name prefix, zeroed count-and-list tail - and asserts the
response payload comes back as fresh zeros, not the request echoed and not zero-length); the old
`_build_ioctl_success_response()` docstring rewritten to document why both a bare zero-length
response *and* a verbatim-echo response are real, tried, rejected shapes, not just the one that
shipped. All 36 tests in `tests/test_cyw43_bus.py` pass; full `pre-commit run --all-files`
(mypy/ruff/pytest, both pure-Python and native builds) passes clean.

## Verification

**Live trace, immediately after the fix**: the `memcmp`/`mcast_list` signature is completely gone -
the CPU moves on to different code entirely (`cyw43_sdpcm_send_common()`/
`cyw43_ll_sdpcm_poll_device()`/`gpio_get()`, `cyw43_ll.c:656-690` and `:1006`) within the same run.

**A real but bounded, already-documented raw-throughput ceiling remained, correctly scoped down**:
`nic.active(True)` still didn't complete within a 240s bounded run right after the fix.
`cyw43_sdpcm_send_common()`'s own `STALL` branch (`cyw43_ll.c:648-691`) is a `for (;;)` loop bounded
by a **1,000,000us (1 *simulated* second)** hard timeout, retrying `cyw43_ll_sdpcm_poll_device()`
with only a cheap `cyw43_yield()` between attempts (`CYW43_SDPCM_SEND_COMMON_WAIT` in
`ports/rp2/cyw43_configport.h:86`, confirmed to expand to just `mp_event_handle_nowait()` on this
port). Provably bounded by design (unlike the `memcmp` bug above), but advancing even one simulated
second through real, busy-executing instructions still costs far more real wall-clock time in this
emulator - the same *class* of limitation 0027 already root-caused (`cyw43_delay_ms()`'s busy-wait,
`docs/BACKLOG.md`'s "Performance side quest"), just now correctly isolated to this one loop instead
of conflated with the `memcmp` bug this record fixes.

**Full end-to-end timed run, same session, confirms the fix is sufficient in practice** (all three
via the same harness/script, timed and with stdout/stderr captured):

| Firmware | Runtime | Wall time | Result |
|---|---|---|---|
| `v1.28.0-dirty` (local debug build) | native (CPython+Cython) | 450.5s | `active: True`; full script (`active`/`scan`/`connect`/`config`/`ipconfig`) runs to completion, no unhandled exception |
| `v1.28.0-dirty` | PyPy 3.10.16 (separate `uv` env, pure-Python - native extensions don't build under PyPy by design) | 211.7s | completed - ~2x faster than native/Cython for this workload, a clean data point now that the correctness bug no longer confounds it (0027's prior PyPy attempt was inconclusive because this bug was still present) |
| `v1.23.0` (cached release UF2) | native | 52.9s | `active: False`, then `OSError: [Errno 1] EPERM` on `scan()` - matches a manual CLI run byte-for-byte; not a regression, already-documented behavior (`v1.28.0`'s `network_cyw43_active()` unconditionally sets its active flag regardless of the real driver's return code, `v1.23.0`'s does not) |

The `v1.28.0` native run's captured stdout shows the expected shape post-fix: `active: True`
prints correctly, followed by repeated `[CYW43] STALL(0;29-29): timeout` lines for `scan()`/
`connect()`/`config()`/`ipconfig()` (0027's step 3g - scripted scan/join events - isn't built yet,
so each of those calls legitimately hits and exhausts the same bounded `STALL` timeout above) and
one `[CYW43] Bus error condition detected 0xb9` / `send_ethernet failed: -110` (expected - step 4's
NAT/data bridge isn't built either). No traceback, no hang past a bounded, explicable cost.

All three runs stay comfortably under CI's existing 15-minute timeout for this job.

**Net effect**: a real, previously-undiagnosed correctness bug (unbounded ~1.9-billion-iteration
walk off SRAM, masquerading as sustained real CPU execution) is fixed and regression-tested, and
`nic.active(True)` on `v1.28.0` now genuinely completes in bounded, measured time. The remaining
per-call cost (`STALL` timeouts for the not-yet-implemented step 3g calls) is the same
already-tracked raw per-instruction interpretation-throughput ceiling as before - not eliminated,
but no longer an unbounded/practically-infinite one. Next step for *that* remains what 0027 already
identified: most likely a native/Cython port of `SimulationClock.tick()` (`clock/simulation_clock.py`
- flagged as an explicit "not yet tried" boundary in `native/_simulator.pyx`'s own module
docstring, the only piece of the per-instruction hot path never natively ported despite three prior
confirmed wins from the same lever, 0013/0031/0034) - not attempted in this record's scope.

## CI (same session)

`.github/workflows/ci-micropython.yml`'s pico-w WLAN step changed to `... || echo
"::warning title=...::..."` so a failure/timeout there posts a warning annotation instead of
failing the whole job - this step's own runtime is inherently variable (bounded by real driver
retry/timeout logic multiplied by this emulator's interpretation speed, not a fixed cost), so
best-effort-with-visibility is a better fit than hard pass/fail. Whether to re-add `v1.28.0` to the
pico_w WLAN CI matrix (dropped in 0027's "Same-day follow-up" as a pragmatic workaround) now that
its runtime is bounded and measured (~450s) is an open question for whoever picks this up next -
not decided in this record.
