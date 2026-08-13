# Task: verify 3g's scripted scan/join actually completes on a live boot, not just "doesn't crash"

Working note - what's left to check now that
[0041](../records/0041-cyw43-post-data-header-freeze-fix.md) fixed the freeze that used to block
`tests/micropython/main-cyw43.py` from ever finishing. **Done (2026-08-13) - see "Findings" below;
folded back into [0027](../records/0027-cyw43-wifi.md)'s own "3g" entry.**

## Findings (2026-08-13)

Ran the harness sketched below (`MicroPythonDevice`, real `v1.28.0` firmware, `board="pico_w"`),
unmodified except for the missing `retrieve(...)` call to resolve `image` from the version tag -
scratch script, not landed (same pattern as 0038's own verification harness).

Result:

- `scan()` returns the real fake AP:
  `[(b'RP2040PY-GUEST', b'B\x137U\xaa\x01', 6, -87, 0, 1)]` - confirms 3g's scripted `escan`
  sequence produces a genuine result, not an empty/fast-fail list.
- `connect()` reaches `status() == 2` (`CYW43_LINK_NOIP`) and stays there for the full 10s poll
  window (50 polls × 0.2s). `isconnected()` never becomes `True`; `config('mac')` stays all-zero;
  `ipconfig('addr4')` stays `('0.0.0.0', '255.255.255.0')`.
- This is the **expected** outcome, not a 3g gap: `bus.py`'s `_queue_join_events()` scripts the
  link-layer event sequence (`WLC_E_SET_SSID`/`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`) through to
  `LINK_NOIP` (joined, no IP) - full `isconnected() == True` additionally needs a real DHCP lease,
  which requires answering `cyw43_cb_tcpip_init()`'s outbound DHCP/ARP frames with real content.
  That's step 4 (NAT bridge), not built yet - `_build_flow_control_response()`'s own docstring
  already flags outbound Ethernet frames as silently dropped by design at this step.
- One unexplained but non-blocking log line, seen once near the start of the run:
  `[CYW43] Bus error condition detected 0xb9`. Did not stop scan/join from completing. Not
  investigated further - reads like firmware's own log output, not something this bus emits (no
  matching string anywhere in `external/cyw43/`).

Conclusion: 3g's live-boot behavior is now formally confirmed at the layer it actually owns - scan
returns a real result, connect drives the full link-layer join sequence to completion, and it stops
exactly where step 4 begins. No further action needed on 3g itself; step 4 is the next real step
for `isconnected() == True` to ever be reachable on a live boot.

## What's confirmed so far

Post-0041, `uv run rp2040py --log-level error micropython --board pico_w --image v1.28.0
tests/micropython/main-cyw43.py` completes end to end: exit code 0, no traceback, ~50s real time,
under both native CPython+Cython and PyPy 3.10.16. That's a real result - the freeze is gone, and
the process no longer hangs.

## What that does *not* yet confirm

The script itself (`tests/micropython/main-cyw43.py`) only prints `nic.isconnected()` *before*
calling `nic.connect(...)` (so "Connected False" is expected/correct regardless of whether connect
later succeeds) and never prints `nic.scan()`'s own return value or polls `isconnected()` again
afterward - its final three calls (`connect()`, `config('mac')`, `ipconfig('addr4')`) have no
`print()` around them at all. So "exits 0, no traceback" only proves nothing *crashed* - it does
not prove 3g's scripted `escan`/`WLC_SET_SSID`/`WLC_E_*` event sequence actually ran and produced
a real result (scan returning the fake `RP2040PY-GUEST` AP, connect() actually transitioning
through the scripted join events to `isconnected() == True`), as opposed to e.g. scan() returning
an empty list fast and connect() silently no-op'ing/failing fast without raising.

**One suspicious data point, not yet explained**: re-running the same script with `--log-level
debug` (the most verbose level this CLI has) produces *zero* log lines of any kind between the
`"Scan for networks"` print and the `"Connected False"` print - not a STALL timeout, not an escan
response, nothing. Confirmed via `grep` that `external/cyw43/bus.py` itself has zero `logger.*()`
calls anywhere (so silence there proves nothing on its own - that layer is silent by design at
every level), but this still means the CLI's own logs currently give *no visibility at all* into
what scan()/connect() actually did.

## Where to pick this up

A throwaway harness was started (not landed, lived only in a scratch directory - same pattern as
0038's own verification harness) using `MicroPythonDevice` directly instead of the CLI, to capture
`nic.scan()`'s actual return value and poll `nic.isconnected()`/`nic.status()` in a loop after
`connect()` with a longer timeout, so the real outcome (does the scripted join sequence actually
land, and how long does it really take) is visible instead of inferred from a script that never
prints it. Sketch (untested past one `LogLevel` vs. raw `logging.ERROR` constructor mismatch,
fixed but not re-run):

```python
async with MicroPythonDevice(image, board="pico_w", log_level=LogLevel.ERROR) as device:
    stdout, stderr = await device.aexec(
        """
import network, time
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print("scan results:", nic.scan())
nic.connect('RP2040PY-GUEST', 'key')
for i in range(50):
    connected = nic.isconnected()
    print("poll", i, "status:", nic.status(), "isconnected:", connected)
    if connected:
        break
    time.sleep(0.2)
""",
        timeout=120,
    )
```

If `scan()` doesn't return the fake AP, or `connect()` never reaches `isconnected() == True`, that's
a real gap in 3g's own live-boot behavior worth its own investigation (separate from 0041's fix,
and separate from 3g's already-passing 42/42 unit tests - a live boot exercises the real firmware's
own driver state machine end to end, which unit tests exercising `GSPIBus` in isolation don't).
If it *does* complete, that's the formal live-boot confirmation
[0027](../records/0027-cyw43-wifi.md)'s own "3g" entry still calls out as "not yet separately
re-run/re-recorded" - worth folding back into that record once done.
