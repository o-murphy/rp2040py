# Task: CYW43 step 3g — async events + scripted scan/join

**Done (2026-08-13): implemented, unit-tested, live-boot-verified as far as currently possible.**
Not a `docs/records/` entry — this is a working checklist, not an immutable decision record; the
durable writeup (full derivation, real corrections found while implementing, byte-offset
citations) lives in `docs/records/0027-cyw43-wifi.md`'s own step-3 "3g" bullet, not duplicated
here. Landed in `external/cyw43/bus.py` (`_build_async_event()`/`_build_scan_result_bytes()`/
`_queue_scan_events()`/`_queue_join_events()`, plus `queue_rx_packet()` becoming a real FIFO, plus
a separate, necessary `_build_flow_control_response()` fix for a real `DATA_HEADER` credit-desync
deadlock found along the way), same as every prior sub-step - see "Where it lives" below for why.
8 new tests in `tests/test_cyw43_bus.py` (43 total, all passing), full `pre-commit run
--all-files` clean.

**Live-boot verification (native CPython+Cython and PyPy+pure-Python, both against a real
`v1.28.0` UF2) ran as this task's own required last step and found the `DATA_HEADER` bug above -
but did not reach full script completion**: fixing that bug lets real firmware's execution
progress further than any prior verification of this bus (genuinely exercises the credit path the
fix targets), but then hits a *separate*, pre-existing, already-partially-flagged simulator-level
freeze (0% CPU, not a hang in this step's own code, and not reached until well after 3g's own
`escan`/`WLC_SET_SSID` code would even run) - full writeup, repro, and what's needed next in the
new `docs/tasks/cyw43-post-data-header-freeze.md`. This task is being called done on the strength
of unit-verified correctness plus a live boot that demonstrably gets further than ever before, not
a full end-to-end script completion - that remains blocked on the separate freeze, not on
anything in this task's own scope.

## Where things stood before this landed (2026-08-13)

`nic.active(True)` now completes (0038, ~450s native / ~212s PyPy on `v1.28.0`). The rest of
`tests/micropython/main-cyw43.py` - `scan()`, `connect('ssid', 'key')`, `config('mac')`,
`ipconfig('addr4')` - currently "completes" only because `GSPIBus` acks every ioctl generically
(0027 step 3f's `_build_ioctl_success_response()`), so each of those calls hits and exhausts the
real driver's own bounded `STALL` retry timeout (`cyw43_sdpcm_send_common()`, `cyw43_ll.c:648-691`,
~1 simulated second each - see 0038) rather than getting a real answer. That's slow *and* wrong:
`scan()` returns nothing, `connect()` never actually associates, `isconnected()` stays `False`.
This task is what makes those calls behave like a real chip instead of quietly timing out.

## What's needed

1. **`cyw43_async_event_t` framing** - exact field layout (byte offsets matter, real firmware
   struct padding, not a natural dataclass shape). Delivered as a fake-Ethernet-framed (`0x886c` +
   Broadcom OUI) SDPCM async packet via the existing `queue_rx_packet()` (0027 step 3e).
2. **`scan()`** - a fixed fake AP (0027's plan: mirror `Wokwi-GUEST`'s shape) answers the single
   `escan` iovar with one `CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` event.
3. **`join()`** - `WLC_SET_SSID`'s own scripted `WLC_E_*` sequence (`_AUTH`/`_ASSOC`/`_PSK_SUP`/
   `_LINK`/...). This is the one piece 0027 flagged as **not yet read from source** - read the rest
   of `cyw43_ll_wifi_join()` (`cyw43-driver/src/cyw43_ll.c`, starts at line 2051 as of the
   `v1.1.1` checkout used this session) and `cyw43_ll_parse_async_event()`'s callers before
   implementing, don't guess the event sequence.

## Where it lives

`external/cyw43/chip.py` (`Cyw43439`) is where 3d onward was meant to live per 0027's original
plan, but in practice every sub-step through 3f landed in `bus.py` instead (`Cyw43439` stayed a
thin wrapper) - follow that precedent unless there's a real reason to finally move logic into
`chip.py`. Either way, needs `tests/test_cyw43_bus.py` coverage the same way every prior sub-step
got it (synthetic `_FakeGSPIMaster`-driven unit tests), *and* a live-boot check against
`tests/micropython/main-cyw43.py` before calling it done - 0027/0038 both found real bugs that only
showed up against a real boot, not unit tests alone.

## Don't re-derive

- The `_write_wlan()`/`_build_ioctl_success_response()` generic-ack mechanism (0027 step 3f,
  corrected in 0038) is correctness-verified now - reuse it for anything that doesn't need scripted
  content, don't rebuild it.
- The bounded `STALL` retry cost (0038) is expected/inherent for any real chip round-trip in this
  emulator - not something this task needs to fix. If a *real* `WLC_E_*` event response, once
  built, still measures unreasonably slow, that's the separate raw-throughput-ceiling task (see
  `docs/tasks/simulation-clock-cython-port.md`), not a 3g bug.
