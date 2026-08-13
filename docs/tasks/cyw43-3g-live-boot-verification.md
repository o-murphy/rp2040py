# Task: verify 3g's scripted scan/join actually completes on a live boot, not just "doesn't crash"

Working note - what's left to check now that
[0041](../records/0041-cyw43-post-data-header-freeze-fix.md) fixed the freeze that used to block
`tests/micropython/main-cyw43.py` from ever finishing. **Not yet investigated - flagged, not
started.**

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
    stdout, stderr = await device.aexec("""
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
""", timeout=120)
```

If `scan()` doesn't return the fake AP, or `connect()` never reaches `isconnected() == True`, that's
a real gap in 3g's own live-boot behavior worth its own investigation (separate from 0041's fix,
and separate from 3g's already-passing 42/42 unit tests - a live boot exercises the real firmware's
own driver state machine end to end, which unit tests exercising `GSPIBus` in isolation don't).
If it *does* complete, that's the formal live-boot confirmation
[0027](../records/0027-cyw43-wifi.md)'s own "3g" entry still calls out as "not yet separately
re-run/re-recorded" - worth folding back into that record once done.
