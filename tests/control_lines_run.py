"""Live-boot check for docs/records/0088-usb-host-side-msc-control-lines-and-reset.md's section 1:
the CDC control lines and line coding can be changed *after* enumeration, and real firmware sees
the change.

Usage: python tests/control_lines_run.py [--image <tag|path>] [--circuitpython]

Host-side rather than a guest script, because the trigger is host-side - `set_control_lines()` /
`set_line_coding()` on the device, which is the surface this proves exists.

The two families prove different amounts, and deliberately so:

- **CircuitPython** exposes DTR as guest state: `supervisor.runtime.serial_connected` *is* the DTR
  bit (TinyUSB's `tud_cdc_n_connected()`). So the guest samples it in a loop while the host drops
  DTR underneath it, and reports afterwards - the sampling has to be the guest's own, because
  output written while DTR is low is exactly what the firmware holds back.
- **MicroPython** has no Python-visible equivalent (`tud_cdc_line_state_cb()` only arms its TX
  flush delay). What is checkable there is that the requests are accepted and the console is still
  usable across them - which is the regression that would matter, since enumeration sends the same
  request on every boot.

**Neither family exposes the line coding to Python**, measured rather than assumed: CircuitPython
10.2.1 answers `AttributeError: 'Serial' object has no attribute 'baudrate'` for
`usb_cdc.console.baudrate`, and MicroPython has no equivalent at all - TinyUSB keeps it for
`tud_cdc_n_get_line_coding()`, which nothing here calls. So `SET_LINE_CODING` is checked the only
way it can be from outside: the request is accepted and the console survives it.
"""

import argparse
import asyncio
import sys

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

TIMEOUT = 120.0

# DTR is dropped and raised again at chosen points of the *guest's* time, not of the host's wall clock. CircuitPython discards
# what it prints to the console while DTR is low (as a real board does), so the result line of the sampler below only reaches
# the host if DTR is back up before the sampler ends. An earlier version held DTR low for 5 real seconds and so relied on the
# emulation being slower than real time: once it got faster, the sampler (3 s of guest time) finished inside the window, its
# output was dropped, and the exec never completed - a hang that depended on the machine's speed, not on the code under test.
# Here the window is 0.5 s .. 1.5 s of guest time after the sampler is sent, so it falls inside the sampler's run on any host,
# and the samples taken during it still have to show both a connected and a not-connected console.
_SAMPLER = """
import supervisor
import time
samples = []
for _ in range(60):
    samples.append(supervisor.runtime.serial_connected)
    time.sleep(0.05)
print("SAMPLES", samples.count(True), samples.count(False))
"""
_DTR_LOW_AT_GUEST_SECONDS = 0.5
_DTR_HIGH_AT_GUEST_SECONDS = 1.5


async def _circuitpython(device: MicroPythonDevice) -> int:
    stdout, stderr = await device.aexec("import usb_cdc\nprint(usb_cdc.console.connected)", timeout=TIMEOUT)
    if b"True" not in stdout:
        print(f"FAILED: the console is not connected at boot: {stdout!r} {stderr!r}")
        return 1

    clock = device.mcu.clock
    started = clock.nanos

    async def _guest_seconds_since_start(seconds: float) -> None:
        while clock.nanos - started < seconds * 1e9:
            await asyncio.sleep(0.001)

    async def _toggle_dtr() -> None:
        await _guest_seconds_since_start(_DTR_LOW_AT_GUEST_SECONDS)
        device.set_control_lines(dtr=False, rts=False)
        await _guest_seconds_since_start(_DTR_HIGH_AT_GUEST_SECONDS)
        device.set_control_lines(dtr=True, rts=True)

    toggling = asyncio.ensure_future(_toggle_dtr())
    stdout, stderr = await device.aexec(_SAMPLER, timeout=TIMEOUT)
    await toggling
    print(f"serial_connected samples: {stdout!r}")
    if b"SAMPLES" not in stdout:
        print(f"FAILED to sample serial_connected: {stdout!r} {stderr!r}")
        return 1
    trues, falses = (int(field) for field in stdout.split(b"SAMPLES")[1].split()[:2])
    if not trues or not falses:
        print(f"FAILED: DTR never changed as the guest saw it ({trues} connected, {falses} not)")
        return 1

    device.set_line_coding(1200)
    stdout, stderr = await device.aexec(
        "import supervisor\nprint('AFTER LINE CODING', supervisor.runtime.serial_connected)", timeout=TIMEOUT
    )
    if b"AFTER LINE CODING True" not in stdout:
        print(f"FAILED: the console did not survive a line-coding change: {stdout!r} {stderr!r}")
        return 1
    return 0


async def _micropython(device: MicroPythonDevice) -> int:
    device.set_control_lines(dtr=False, rts=False)
    device.set_control_lines(dtr=True, rts=True)
    stdout, stderr = await device.aexec("print('AFTER CONTROL LINES')", timeout=TIMEOUT)
    if b"AFTER CONTROL LINES" not in stdout:
        print(f"FAILED: the console did not survive a DTR/RTS change: {stdout!r} {stderr!r}")
        return 1

    device.set_line_coding(1200)
    stdout, stderr = await device.aexec("print('AFTER LINE CODING')", timeout=TIMEOUT)
    if b"AFTER LINE CODING" not in stdout:
        print(f"FAILED: the console did not survive a line-coding change: {stdout!r} {stderr!r}")
        return 1
    return 0


async def run(image: str, circuitpython: bool) -> int:
    family = CIRCUITPYTHON if circuitpython else MICROPYTHON
    board = resolve_board_spec("pico", family, image)
    print(f"Loading uf2 image {board.image}")

    # astart() explicitly, not `async with`: the context manager boots with BaseDevice's default
    # 30s timeout, which is not the budget the rest of this script runs on. Measured 2026-08-20:
    # a CircuitPython pico_w boot is ~27s, so that default left a three-second margin and CI
    # eventually lost the coin flip on it (a red "CYW43 comes back after a chip reset" whose
    # traceback was a boot timeout, not a reset failure).
    device = MicroPythonDevice(board=board, circuitpython=circuitpython)
    await device.astart(timeout=TIMEOUT)
    try:
        exit_code = await (_circuitpython(device) if circuitpython else _micropython(device))
        if exit_code:
            return exit_code
    finally:
        device.stop()

    print("CONTROL LINES TEST PASSED.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="micropython.uf2")
    parser.add_argument("--circuitpython", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.image, args.circuitpython)))


if __name__ == "__main__":
    main()
