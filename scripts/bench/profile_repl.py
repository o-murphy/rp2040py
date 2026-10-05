"""Where does the wall time of a real firmware session go now? (docs/records/0096-cpp-mcu-core.md, the 2026-10-05 re-profile.)

Boots MicroPython (or CircuitPython with ``--circuitpython``), runs a fixed set of REPL scripts three times each under cProfile, and prints the time per source
file. ``simulator.py:_execute_batch`` is the C++ instruction loop (the native time is booked to that Python frame); ``peripherals/usb.py`` is the USB controller that
is still Python. Needs the cached firmware image (``rp2040py micropython --image ... --fetch-fw-only``).

    python scripts/bench/profile_repl.py [--circuitpython] [--image PATH]
"""

import argparse
import asyncio
import collections
import cProfile
import os
import pstats
import time

from rp2040py.boards import BoardSpec
from rp2040py.device import MicroPythonDevice

CACHE = os.path.expanduser("~/.cache/rp2040py")
DEFAULT_MICROPYTHON = os.path.join(CACHE, "RPI_PICO-20260406-v1.28.0.uf2")
DEFAULT_CIRCUITPYTHON = os.path.join(CACHE, "adafruit-circuitpython-raspberry_pi_pico-en_US-10.2.1.uf2")

SCRIPTS = [
    "for i in range(300): print('line', i, 'x' * 40)",
    "import gc; gc.collect(); print(gc.mem_free())",
    "d = {i: str(i) * 3 for i in range(500)}; print(len(d))",
    "print(sum(i * i for i in range(20000)))",
]
ROUNDS = 3


async def session(image: str, circuitpython: bool) -> None:
    device = MicroPythonDevice(board=BoardSpec(image=image), circuitpython=circuitpython)
    wall = time.perf_counter()
    await device.astart(timeout=120)
    print(f"boot: {time.perf_counter() - wall:.3f} s wall, {device.simulator.clock.nanos / 1e9:.3f} s simulated")
    wall, sim = time.perf_counter(), device.simulator.clock.nanos
    for script in SCRIPTS * ROUNDS:
        await device.aexec(script, timeout=60)
    print(
        f"repl: {time.perf_counter() - wall:.3f} s wall, {(device.simulator.clock.nanos - sim) / 1e9:.3f} s simulated"
    )
    device.stop()


def report(profile: cProfile.Profile) -> None:
    stats = pstats.Stats(profile).stats
    total = sum(entry[2] for entry in stats.values())
    by_file: dict[str, float] = collections.defaultdict(float)
    for (filename, _line, _name), (_cc, _nc, tottime, _ct, _callers) in stats.items():
        key = (
            filename.split("rp2040py/")[-1]
            if "rp2040py/" in filename
            else ("<builtin>" if filename == "~" else os.path.basename(filename))
        )
        by_file[key] += tottime
    print(f"total {total:.3f} s of own time; by file:")
    for key, seconds in sorted(by_file.items(), key=lambda item: -item[1])[:10]:
        print(f"  {100 * seconds / total:5.1f}%  {seconds:7.3f} s  {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--circuitpython", action="store_true")
    parser.add_argument("--image", help="a .uf2 (default: the cached MicroPython 1.28 / CircuitPython 10.2.1 image)")
    args = parser.parse_args()
    image = args.image or (DEFAULT_CIRCUITPYTHON if args.circuitpython else DEFAULT_MICROPYTHON)
    profile = cProfile.Profile()
    profile.enable()
    asyncio.run(session(image, args.circuitpython))
    profile.disable()
    report(profile)


if __name__ == "__main__":
    main()
