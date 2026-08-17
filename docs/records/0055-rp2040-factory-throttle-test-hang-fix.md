# 0055. `v0.2.2`/`v0.2.3` publish-workflow hang: unthrottled `RP2040()` construction in new CYW43 test coverage

- Status: Implemented — pending live-CI verification (2026-08-17)
- Conceived: 2026-08-17 · Implemented: 2026-08-17
- Related: 0027 (CYW43439/Pico W epic - the new NAT-bridge/disassoc test coverage that tipped this
  over), 0048 (NAT bridge - source of the large new `test_cyw43_nat.py`), 0054 (`disconnect()`
  fix - the new disassoc tests in `test_cyw43_bus.py` that also contributed)

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

## Verification

`uv run pre-commit run --all-files` (mypy/ruff/pytest, pure-Python and native builds) passes clean.

**Not yet verified against a live `cibuildwheel` run** at the time this record was written - the fix
lands as a normal commit on top of `v0.2.3`'s own commit (`v0.2.2`/`v0.2.3` themselves are
abandoned as release tags, never having published anything), to be validated via a
`workflow_dispatch` run before being tagged as the actual next release. This record's "Status" line
should be updated to "verified" once that run is watched end-to-end.

## Net effect

`v0.2.2` and `v0.2.3` are dead tags - nothing was ever published under either, and neither will be
reused. The fix is a normal forward commit; the next real release tag (expected `v0.2.4`) is cut
from a commit that includes it.
