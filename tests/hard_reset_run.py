"""Live-boot check for docs/records/0089-one-reset-for-every-trigger.md's Phase 2: a
host-initiated hard reset (`MicroPythonDevice.ahard_reset()`) really resets the chip, comes back,
and leaves the firmware reporting the reset it actually was.

Usage: python tests/hard_reset_run.py [--image <tag|path>] [--circuitpython]

Host-side rather than a guest script (unlike tests/micropython/main-reset-cause.py) because the
trigger itself is host-side: nothing a guest can print proves that `ahard_reset()` waited for the
re-enumeration rather than returning early.

Asserts on VM state, never on boot output: firmware only flushes CDC once DTR is asserted, so
whatever the post-reset boot prints is lost by construction (0089's Appendix, point 4) - a test
that greps for a banner here would be testing the artifact, not the reset.
"""

import argparse
import asyncio
import sys

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

TIMEOUT = 120.0


async def run(image: str, circuitpython: bool) -> int:
    family = CIRCUITPYTHON if circuitpython else MICROPYTHON
    board = resolve_board_spec("pico", family, image)
    print(f"Loading uf2 image {board.image}")

    async with MicroPythonDevice(board=board, circuitpython=circuitpython) as device:
        stdout, stderr = await device.aexec("marker = 1234\nprint(marker)", timeout=TIMEOUT)
        if b"1234" not in stdout:
            print(f"FAILED to set up guest state: {stdout!r} {stderr!r}")
            return 1

        await device.ahard_reset(timeout=TIMEOUT)
        print("Re-enumerated after the reset")

        # The REPL is usable again straight away - and the guest lost its RAM, so `marker` must be
        # gone. A device that merely kept running (no reset at all) would still answer 1234 here.
        stdout, stderr = await device.aexec("print(marker)", timeout=TIMEOUT)
        if b"NameError" not in stderr:
            print(f"FAILED: guest state survived the reset: {stdout!r} {stderr!r}")
            return 1

        # ...and it must report *this* reset, not the last watchdog reboot or a stale power-on.
        # 0089's D4: a host-side reset behaves as the RUN pin, which MicroPython can only report as
        # PWRON (it has two values) but CircuitPython reports precisely.
        if circuitpython:
            probe = "import microcontroller\nprint(microcontroller.cpu.reset_reason)"
            expected = b"RESET_PIN"
        else:
            probe = "import machine\nprint(machine.reset_cause() == machine.PWRON_RESET)"
            expected = b"True"
        stdout, stderr = await device.aexec(probe, timeout=TIMEOUT)
        print(f"reset cause after a host-initiated reset: {stdout!r}")
        if expected not in stdout:
            print(f"FAILED: expected {expected!r} in {stdout!r} {stderr!r}")
            return 1

    print("HARD RESET TEST PASSED.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="micropython.uf2")
    parser.add_argument("--circuitpython", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.image, args.circuitpython)))


if __name__ == "__main__":
    main()
