# 0055. `v0.2.2`/`v0.2.3` publish-workflow hang: `asyncio.Server.wait_closed()`'s 3.12.1 fix exposing a missing flow close in `test_cyw43_nat.py`

- Status: Implemented — verified locally (both root causes below), pending live-CI verification
  (2026-08-17)
- Conceived: 2026-08-17 · Implemented: 2026-08-17
- Related: 0027 (CYW43439/Pico W epic - the new NAT-bridge/disassoc test coverage involved), 0048
  (NAT bridge - source of the large new `test_cyw43_nat.py`), 0054 (`disconnect()` fix - the new
  disassoc tests in `test_cyw43_bus.py` that prompted the first, independent fix below)

## Update (2026-08-17): the real root cause, found after the fix below didn't unblock CI

The unthrottled-`RP2040()` fix in this record's original "Root cause"/"Fix" sections below is a
real, worthwhile improvement (kept), but it turned out **not** to be why the publish workflow was
actually stalling - a `workflow_dispatch` run on top of it ([32015218682](https://github.com/o-murphy/rp2040py/actions/runs/32015218682))
hung at the exact same point. Root-caused for real this time, with a genuine stack trace (see
"Second root cause" below): every `cibuildwheel` leg testing a `cp313`+ wheel (`cp314t` on
Linux/Windows/macOS, `cp313`/`cp314` on Android) hangs in
`test_cyw43_nat.py::test_reset_clears_flows_so_a_reused_port_can_connect_again`, forever, inside
`echo_server.wait_closed()` - **not** free-threading-specific (confirmed hanging identically under
plain `--python 3.14`, not just `--python 3.14t`), and **not** a memory issue at all. See "Second
root cause" and "Second fix" below for the actual mechanism and the actual fix
(`tests/test_cyw43_nat.py`, commit `6479095`).

## Context

Both the `v0.2.2` (`bf3da81`) and `v0.2.3` (`2fdd23d`) tag pushes triggered `publish.yml`, and in
both runs **every** `Build wheels on *` matrix job - `ubuntu-latest` (Linux x64/x86, incl. `i686`
under QEMU), `ubuntu-24.04-arm` (incl. `armv7l` under QEMU), both Windows legs, both macOS legs,
and Android - stalled deterministically at almost the same point inside `pytest /project/tests`,
mid `test_cyw43_bus.py` / at the `test_cyw43_bus.py`→`test_cyw43_nat.py` boundary, identically
across native x86_64, 32-bit-under-QEMU, ARM64, and every OS. Runs
[31966405339](https://github.com/o-murphy/rp2040py/actions/runs/31966405339) (`v0.2.2`) and
[31978409034](https://github.com/o-murphy/rp2040py/actions/runs/31978409034) (`v0.2.3`) were both
cancelled (~15min and ~30min in respectively) once it was clear these weren't going to finish.
Neither ever reached the `deploy` job, so nothing was published to PyPI or as a GitHub Release
under either tag.

(A separate, unrelated finding along the way: `v0.2.2`'s jobs additionally sat queued ~11h before a
runner was even assigned - a GitHub Actions runner-capacity issue, most likely tied to the newer/
scarcer `macos-26`/`windows-11-arm` runner images, not a code bug. No GitHub-wide incident was
found for that window. Not this record's subject.)

## Root cause

`tests/conftest.py`'s `rp2040_factory` fixture exists specifically to bound how many `RP2040()`
instances are alive concurrently - each allocates a 16 MB `flash` `bytearray` (+264 KB `sram`) - via
a `threading.Semaphore(4 if IS32BIT else 8)`. The `4`-on-32-bit number is sized exactly for the
kind of 32-bit wheel testing (`i686`, `armv7l`) this release matrix runs under `cibuildwheel`,
where process address space is tight.

The CYW43 NAT-bridge test coverage that landed for this release - `test_cyw43_bus.py`'s new
disassoc/escan tests ([0054](0054-cyw43-disassoc.md)) and the entirely new, 875-line
`test_cyw43_nat.py` ([0048](0048-cyw43-nat-reflector.md)) -
constructs `RP2040()`/`Simulator()` directly in ~60 call sites, bypassing the throttle entirely, on
top of a handful of pre-existing files that already did the same
(`test_external_device.py`, `test_led_mock.py`, `test_schedule_threadsafe.py`,
`test_cli_board_spec.py`). None of that unthrottled construction is itself new - but the volume the
NAT-bridge coverage added pushed cumulative/concurrent flash-bytearray allocation well past what
the `cibuildwheel` wheel-test containers can sustain, especially the 32-bit legs. The failure mode
there is not a clean `MemoryError` - it's the container's allocator/pager grinding to a crawl, which
in a streamed CI log is indistinguishable from a genuine hang.

## Fix

Threaded the existing `rp2040_factory` fixture through every `RP2040()`/`Simulator()` construction
in `tests/test_cyw43_bus.py`, `tests/test_cyw43_nat.py`, `tests/test_external_device.py`,
`tests/test_led_mock.py`, `tests/test_schedule_threadsafe.py`, and the two `build_rp2040`-monkeypatch
lambdas in `tests/test_cli_board_spec.py` - matching the pattern already established in
`tests/test_gpio_pin.py`/`test_dma.py`/`test_bootsel_button.py`/`test_kaluma_device.py`. Helper
functions that construct `RP2040` on tests' behalf (`_wire_up()`/`_wire_up_with_bus()` in
`test_cyw43_bus.py`, the local `_wire_up_with_nat_bridge()` wrapper in `test_cyw43_nat.py`) now take
`rp2040_factory` as their own parameter and thread it through, rather than constructing directly.

**Explicitly not touched** - a broader, pre-existing set of files also construct
`RP2040()`/`Simulator()` directly, unthrottled: `test_pty_repl.py`, `test_stdio_repl.py`,
`test_socket_repl.py`, `test_simulator.py`, `test_simulator_shutdown.py`,
`test_simulator_engine_room_crash.py`, `test_gdb_tcp_server.py`, `test_cli.py`, `test_boards.py`,
`test_pio.py`, `test_mpremote_integration.py`, `tests/utils/create_test_driver.py`,
`tests/micropython_spi_run.py`. These predate `v0.2.2`, and every prior tagged release up to and
including `v0.2.1` built/published cleanly through the same `cibuildwheel` matrix - so they are not
implicated in this specific regression. Left as a known gap for whoever next needs to raise the
suite's overall memory ceiling further, rather than swept in here under release-incident time
pressure.

## First fix's verification

`uv run pre-commit run --all-files` (mypy/ruff/pytest, pure-Python and native builds) passes clean.
Landed as commit `a92c7a1`, then a `workflow_dispatch` ([32015218682](https://github.com/o-murphy/rp2040py/actions/runs/32015218682))
on top of it hung identically - see "Update" above.

## Second root cause

With the first fix landed and still hanging live in CI, watched the `workflow_dispatch` run
directly instead of theorizing further: two `cibuildwheel` test sessions (`cp310`, `cp311-abi3`)
passed in seconds each; the third (`cp314-cp314t`) hung and stayed hung until the run was
cancelled ~12 real minutes later, stuck immediately after `test_cyw43_bus.py` completed, before
`test_cyw43_nat.py`'s first test even printed. The very first hang (before either fix, run
[31978409034](https://github.com/o-murphy/rp2040py/actions/runs/31978409034)) showed the identical
shape on the identical wheel (`cp314-cp314t`).

Reproduced locally (`uv run --python 3.14t -- pytest tests/`, hung; normal 3x runs under the
project's default 3.10 interpreter never do). Got a real stack trace without `ptrace` (blocked by
`ptrace_scope=1` here too, same as [0041](0041-cyw43-post-data-header-freeze-fix.md)) by setting
`PYTHONFAULTHANDLER=1` and sending `SIGABRT` to the hung process - faulthandler dumps every
thread's Python stack on that signal. Pointed straight at
`test_cyw43_nat.py::test_reset_clears_flows_so_a_reused_port_can_connect_again`, stuck inside
`asyncio.run()`'s own `_run_once()`/`select()`.

Bisection from there:

- A minimal standalone asyncio repro (cancel a task blocked on a real socket read, immediately
  start a fresh one to the same destination - both via plain `asyncio.create_task()` and via the
  same fire-and-forget `asyncio.run_coroutine_threadsafe()` shape `Simulator.schedule_threadsafe()`
  uses) did **not** reproduce the hang under `--python 3.14t`, on its own or under regular
  `--python 3.14` - ruling out a generic CPython/asyncio scheduling bug and a `TcpReflector`-level
  race.
- Temporarily instrumented the actual failing test with diagnostics (`asyncio.all_tasks()`,
  `flow.pump_task` state, the bus's own `_rx_packet`/`_rx_queue`/`SPI_STATUS_REGISTER` polled both
  through `master.read_register()` and directly via `bus._read_f0()`). The second flow's SYN-ACK
  **was** correctly queued and visible (`STATUS_F2_PKT_AVAILABLE` set from the very first poll) -
  the reflector/bus code was doing its job correctly the whole time.
- Re-ran the single failing test under plain (non-free-threaded) `--python 3.14`: **hung
  identically**. This ruled out free-threading entirely - the earlier "GIL-masked race" framing in
  this record's original title was wrong.
- `python3 -c "import asyncio, inspect; print(inspect.getsource(asyncio.base_events.Server.wait_closed))"`
  turned up the real answer, in `wait_closed()`'s own docstring: *"In 3.11 and before, this was
  broken, returning immediately if the server was already closed, even if there were still active
  connections. An attempted fix in 3.12.0 was still broken... Hopefully in 3.12.1 we have it
  right."*

The test reconnects (reusing the same `(src_port, dst_ip, dst_port)` triple) after
`bus.nat_bridge.reset()`, asserts the new SYN-ACK, and calls `echo_server.close()` +
`await echo_server.wait_closed()` - but never closes or cancels the **second** flow's own
still-open connection to the echo server first (only the *first* flow got torn down, by the
`reset()` call the test itself is exercising). On `cp310`/`cp311` (both pre-3.12.1),
`wait_closed()`'s bug silently ignored that dangling connection and returned immediately. On
`cp313`+ (where the fix has landed, in this project's build matrix: `cp314t` on
Linux/Windows/macOS, `cp313`/`cp314` on Android - free-threaded or not, the fix is
version-gated, not GIL-gated), `wait_closed()` now correctly waits for that connection to close
too - which nothing in the test or the code under test was ever going to do, so it waits forever.
Every other `echo_server`-using test in this file drives its flow to a real, natural close (FIN/FIN
-ACK, or a guest RST that cancels the pump task and closes the writer) before its own
`wait_closed()` call - only this one test left a flow dangling, because reconnect-after-reset was
never actually driven to its own natural end within the test.

**Why nothing in this project's regular CI/pre-commit ever caught this**: `pre-commit.yml` and
`coverage.yml` both pin `python-version: "3.10"` for their entire `pytest tests/` run, and
`ci-micropython.yml`'s own `cpython-3.14` matrix leg never runs `pytest` at all (only live-boots
real MicroPython firmware). The **only** place this project's full unit-test suite ever runs under
`cp313`+ is `publish.yml`'s `cibuildwheel` wheel-testing step - which only fires on an actual
release tag push or `workflow_dispatch`. `test_cyw43_nat.py` landed 2026-08-16 ([0048]); `v0.2.2`
was the first release cut since then - so this was the literal first time this test ever ran under
a Python new enough to have the `wait_closed()` fix.

## Second fix

`tests/test_cyw43_nat.py`, commit `6479095`: added `bus.nat_bridge.reset()` again, right before
`echo_server.close()`, to close the second flow's connection too (cancels its pump task, closes its
writer) - the same call the test is already exercising, just applied to the flow it left open.

## Verification

Reran the previously-hanging test directly against both interpreters that reproduced it:
`uv run --python 3.14t -- pytest tests/test_cyw43_nat.py::test_reset_clears_flows_so_a_reused_port_can_connect_again`
and the same under plain `--python 3.14` - both now pass in well under a second (previously: hung
until forcibly killed). `uv run pre-commit run --all-files` (mypy/ruff/pytest, pure-Python and
native builds) passes clean.

**Not yet verified against a live `cibuildwheel` run** at the time this record was written - both
fixes land as normal commits on top of `v0.2.3`'s own commit (`v0.2.2`/`v0.2.3` themselves are
abandoned as release tags, never having published anything), to be validated via a fresh
`workflow_dispatch` run before being tagged as the actual next release. This record's "Status" line
should be updated once that run is watched end-to-end through `deploy`.

## Net effect

`v0.2.2` and `v0.2.3` are dead tags - nothing was ever published under either, and neither will be
reused. Both fixes are normal forward commits; the next real release tag (expected `v0.2.4`) is cut
from a commit that includes them.
