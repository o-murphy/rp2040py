"""Live-boot check for docs/records/0089-one-reset-for-every-trigger.md's Phase 4: a `ResetButton`
pressed and released against real firmware really holds the chip in reset, really boots it, and
leaves the firmware reporting the RESET pin as the cause.

Usage: python tests/reset_button_run.py [--image <tag|path>] [--circuitpython]

Companion to tests/hard_reset_run.py, which does the same for the *host-side* trigger (Phase 2).
The two must agree - a RESET button and `ahard_reset()` are the same hard reset from 0089's D4's
point of view - so this script deliberately asserts the same three things, reached through the pin.

Attaches the button to a plain `pico`: a real Raspberry Pi Pico has no RESET button (BOOTSEL is its
only one, RUN is a bare test point), so this is a user wiring one to RUN themselves, which is
exactly what `attach_external_devices()` is for. Boards that ship one attach it in their own
`boards/` file instead - `vcc_gnd_yd_rp2040`, `waveshare_rp2040_lcd_0_96`, `weactstudio`.

Asserts on VM state, never on boot output, for the same reason hard_reset_run.py does: firmware
only flushes CDC once DTR is asserted, so whatever the post-reset boot prints is lost by
construction (0089's Appendix, point 4).
"""

import argparse
import asyncio
import sys

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.external.device import attach_external_devices
from rp2040py.external.reset_button import ResetButton
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

TIMEOUT = 120.0
# Long enough that a running chip would have executed millions of instructions in it, short enough
# not to pad the CI job: what it has to separate is "held" from "merely slow".
HOLD_SECONDS = 0.5


async def run(image: str, circuitpython: bool) -> int:
    family = CIRCUITPYTHON if circuitpython else MICROPYTHON
    board = resolve_board_spec("pico", family, image)
    print(f"Loading uf2 image {board.image}")

    device = MicroPythonDevice(board=board, circuitpython=circuitpython)
    button = ResetButton()
    # Before astart(), per the attach-timing rule (docs/records/0029) - a device may not be
    # hot-plugged onto a running simulation.
    attach_external_devices(device.mcu, button)

    # astart() explicitly, not `async with`: the context manager boots with BaseDevice's default
    # 30s timeout, which is not the budget the rest of this script runs on. Measured 2026-08-20:
    # a CircuitPython pico_w boot is ~27s, so that default left a three-second margin and CI
    # eventually lost the coin flip on it (a red "CYW43 comes back after a chip reset" whose
    # traceback was a boot timeout, not a reset failure).
    await device.astart(timeout=TIMEOUT)
    try:
        stdout, stderr = await device.aexec("marker = 1234\nprint(marker)", timeout=TIMEOUT)
        if b"1234" not in stdout:
            print(f"FAILED to set up guest state: {stdout!r} {stderr!r}")
            return 1

        # Arm the re-enumeration wait *before* the press: a button is not an awaitable API, so
        # there is nothing that would otherwise tell this script when the board is back. (A caller
        # that wants an awaitable reset uses `ahard_reset()` - same reset, host-side trigger.)
        connected = device._arm_enumeration()

        button.press()
        await asyncio.sleep(0.1)  # let the engine room apply the level
        if not device.mcu.run_pin_low:
            print("FAILED: RUN did not go low")
            return 1

        # The observable that only a real "held in reset" produces: the core stops dead. A running
        # chip - even a fully idle one parked in WFI - moves its PC constantly as timer/USB
        # interrupts fire, so two reads this far apart matching is not something an executing
        # emulator can fake. The value printed is the *reset* PC, not wherever the guest happened
        # to be: pressing RESET now enters reset rather than merely freezing (0089 Phase 4's own
        # known gap, closed - the device also drops off the USB bus for the duration).
        pc_before = device.mcu.core.pc
        await asyncio.sleep(HOLD_SECONDS)
        pc_after = device.mcu.core.pc
        if pc_before != pc_after:
            print(f"FAILED: the chip kept executing while RESET was held ({pc_before:#x} -> {pc_after:#x})")
            return 1
        print(f"Held in reset for {HOLD_SECONDS}s, PC frozen at {pc_before:#x}")

        button.release()
        await device._await_enumeration(connected, TIMEOUT, "re-enumerate after the RESET button")
        device._post_boot_handshake()
        print("Re-enumerated after the release")

        stdout, stderr = await device.aexec("print(marker)", timeout=TIMEOUT)
        if b"NameError" not in stderr:
            print(f"FAILED: guest state survived the reset: {stdout!r} {stderr!r}")
            return 1

        if circuitpython:
            probe = "import microcontroller\nprint(microcontroller.cpu.reset_reason)"
            expected = b"RESET_PIN"
        else:
            probe = "import machine\nprint(machine.reset_cause() == machine.PWRON_RESET)"
            expected = b"True"
        stdout, stderr = await device.aexec(probe, timeout=TIMEOUT)
        print(f"reset cause after the RESET button: {stdout!r}")
        if expected not in stdout:
            print(f"FAILED: expected {expected!r} in {stdout!r} {stderr!r}")
            return 1
    finally:
        device.stop()

    print("RESET BUTTON TEST PASSED.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="micropython.uf2")
    parser.add_argument("--circuitpython", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.image, args.circuitpython)))


if __name__ == "__main__":
    main()
