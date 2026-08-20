"""Live-boot checks for docs/records/0089-one-reset-for-every-trigger.md's Phase 5: the fuller chip
reset, measured through the two observables the record wrote down in advance (its Appendix,
point 5) as *failing* before this phase.

Usage: python tests/chip_reset_run.py --pads [--image <tag>]
       python tests/chip_reset_run.py --cyw43 [--image <tag>]

`--pads` (MicroPython, `pico`): a guest drives a GPIO high, calls `machine.reset()`, and the pad
must be released on the way back up - `FUNCSEL` back to `0x1f`, output-enable clear. This is the
watchdog path, so it also proves the WDSEL model end to end: pico-sdk's `watchdog_reboot()` sets
`PSM.WDSEL` to everything-but-ROSC/XOSC, and the reset covers exactly what that selects.

`--cyw43` (CircuitPython, `pico_w`): WiFi must still work after a hard reset. It could not before
this phase - the pads were released, so the driver re-ran its init, but the emulated CYW43439 still
held every register from the previous session and answered nothing, filling the console with the
firmware's own `[CYW43] Failed to start CYW43`. Modelling `WL_ON` (0089's D7: an external chip sees
a reset only through the GPIO firmware drives) is what closes it.
"""

import argparse
import asyncio
import sys

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

TIMEOUT = 180.0
GPIO = 15


async def run_pads(image: str) -> int:
    board = resolve_board_spec("pico", MICROPYTHON, image)
    print(f"Loading uf2 image {board.image}")
    # astart() explicitly, not `async with`: the context manager boots with BaseDevice's default
    # 30s timeout, which is not the budget the rest of this script runs on. Measured 2026-08-20:
    # a CircuitPython pico_w boot is ~27s, so that default left a three-second margin and CI
    # eventually lost the coin flip on it (a red "CYW43 comes back after a chip reset" whose
    # traceback was a boot timeout, not a reset failure).
    device = MicroPythonDevice(board=board)
    await device.astart(timeout=TIMEOUT)
    try:
        mcu = device.mcu
        await device.aexec(f"from machine import Pin\np = Pin({GPIO}, Pin.OUT)\np.on()", timeout=TIMEOUT)
        pin = mcu.gpio[GPIO]
        driven_oe = bool(mcu.sio.gpio_output_enable >> GPIO & 1)
        print(f"driven: FUNCSEL={pin.ctrl & 0x1F:#x} OE={driven_oe} value={pin.value!r}")
        if pin.ctrl & 0x1F != 5 or not driven_oe:
            print("FAILED to set up guest state: the guest never drove the pin")
            return 1

        connected = device._arm_enumeration()
        try:
            # A guest hard reset drops USB mid-exec by construction, so this call cannot return -
            # the re-enumeration below is the real completion signal (0089's Appendix, point 4).
            await asyncio.wait_for(device.aexec("import machine\nmachine.reset()", timeout=5), timeout=6)
        except (TimeoutError, asyncio.TimeoutError, RuntimeError):
            pass
        wdsel = mcu.psm.wdsel
        print(f"PSM.WDSEL the firmware wrote before triggering: {wdsel:#x}")
        if not wdsel:
            print("FAILED: the firmware never selected any watchdog reset domain")
            return 1
        await device._await_enumeration(connected, TIMEOUT, "re-enumerate")
        device._post_boot_handshake()

        pin = mcu.gpio[GPIO]
        fsel, oe = pin.ctrl & 0x1F, bool(mcu.sio.gpio_output_enable >> GPIO & 1)
        print(f"after reset: FUNCSEL={fsel:#x} OE={oe} pad={pin.pad_value:#x} value={pin.value!r}")
        if fsel != 0x1F or oe:
            print("FAILED: the pad kept driving across the reset")
            return 1
    finally:
        device.stop()

    print("PAD RESET TEST PASSED.")
    return 0


async def run_cyw43(image: str) -> int:
    board = resolve_board_spec("pico_w", CIRCUITPYTHON, image)
    print(f"Loading uf2 image {board.image}")
    probe = "import wifi\nprint('mac', wifi.radio.mac_address)"
    # astart() explicitly, not `async with`: the context manager boots with BaseDevice's default
    # 30s timeout, which is not the budget the rest of this script runs on. Measured 2026-08-20:
    # a CircuitPython pico_w boot is ~27s, so that default left a three-second margin and CI
    # eventually lost the coin flip on it (a red "CYW43 comes back after a chip reset" whose
    # traceback was a boot timeout, not a reset failure).
    device = MicroPythonDevice(board=board, circuitpython=True)
    await device.astart(timeout=TIMEOUT)
    try:
        stdout, stderr = await device.aexec(probe, timeout=TIMEOUT)
        print(f"before reset: {stdout!r}")
        if b"mac" not in stdout:
            print(f"FAILED to set up guest state: WiFi was not up before the reset: {stderr!r}")
            return 1

        await device.ahard_reset(timeout=TIMEOUT)
        print("Re-enumerated after the reset")

        stdout, stderr = await device.aexec(probe, timeout=TIMEOUT)
        print(f"after reset: {stdout!r}")
        # Both halves matter, and the second is the one an earlier, weaker version of this check
        # missed: the firmware still prints a MAC when the chip is dead, it is just all zeros.
        if b"Failed to start CYW43" in stdout:
            print("FAILED: the firmware could not bring the CYW43439 back up")
            return 1
        if b"mac b'\\x00\\x00\\x00\\x00\\x00\\x00'" in stdout or b"mac" not in stdout:
            print(f"FAILED: WiFi came back with a null MAC, i.e. not really up: {stdout!r} {stderr!r}")
            return 1
    finally:
        device.stop()

    print("CYW43-AFTER-RESET TEST PASSED.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=None)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pads", action="store_true")
    group.add_argument("--cyw43", action="store_true")
    args = parser.parse_args()
    if args.pads:
        sys.exit(asyncio.run(run_pads(args.image or "1.23.0")))
    sys.exit(asyncio.run(run_cyw43(args.image or "10.2.1")))


if __name__ == "__main__":
    main()
