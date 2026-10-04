"""Phase 3 baseline of docs/records/0096-cpp-mcu-core.md: the ordered stream of GPIO pin changes a real boot produces.

Hooks a listener on every ``RP2040.gpio[n]`` before anything runs and records ``(nanos, pin, new, old)`` for every change
the listeners are told about, in the order they are told. That is exactly the stream an ``ExternalDevice`` (the CYW43 gSPI
listener included) sees, so it is the behaviour a C++ pin/PIO layer has to reproduce *bit for bit*: same events, same
order, same simulated time.

    python scripts/bench/pin_events.py picow-scan                       # counts per pin + the stream's hash
    python scripts/bench/pin_events.py picow-scan --save picow.json.gz  # keep the full stream
    python scripts/bench/pin_events.py picow-scan --compare picow.json.gz  # diff against a saved run (first divergence)

What is compared, and why not everything exactly. The *order* hash is over ``(pin, new, old)`` and must match exactly between
any two runs: the same pin changes in the same sequence. ``--compare`` fails only on that. The *spacing* between events is
reported (the share of gaps within ``--tolerance-ns`` and the largest deviation) but never fails the run, because it cannot be exact
even between two runs of the very same build (measured on ``pio-dma``): the asynchronous host (the REPL round trip) injects a
script at a wall-clock-dependent simulated time, so a PIO clock period that is not a whole number of core cycles (2 MHz = 62.5 cycles
of 8 ns) is quantised differently, and the guest code that runs between DMA transfers takes a host-dependent time. Cycle-exact
timing is what the instruction-level golden traces and ``tests/test_pio*.py`` cover. A gap longer than ``HOST_GAP_NANOS`` is the
host's doing and is not compared. Recording adds one cheap Python listener per pin, so it measures the stream, not the speed.
"""

import argparse
import asyncio
import collections
import gzip
import hashlib
import json
import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tests"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from profile_access import WORKLOADS

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

_EVENT = struct.Struct("<dBBB")  # gap in ns (or -1.0 for a host-decided gap), pin, new, old (GPIOPinState values)
_ORDER = struct.Struct("<BBB")
HOST_GAP_NANOS = 1_000_000.0  # a longer silence is the host's doing, not the guest's


async def record(workload_name: str) -> "tuple[list[tuple[float, int, int, int]], float]":
    workload = next(w for w in WORKLOADS if w.name == workload_name)
    spec = CIRCUITPYTHON if workload.circuitpython else MICROPYTHON
    device = MicroPythonDevice(
        board=resolve_board_spec(workload.board, spec, workload.tag), circuitpython=workload.circuitpython
    )
    mcu = device.mcu
    clock = mcu.clock
    events: list[tuple[float, int, int, int]] = []

    def make_listener(pin: int):
        def listener(new_state, old_state) -> None:
            events.append((clock.nanos, pin, int(new_state), int(old_state)))

        return listener

    for number, pin in enumerate(mcu.gpio):
        pin.add_listener(make_listener(number))
    try:
        await device.astart(timeout=120)
        for _label, source, _expected in workload.scripts:
            await device.aexec(source, timeout=300)
    finally:
        sim_seconds = clock.nanos / 1e9
        device.stop()
    return events, sim_seconds


def normalised(events: "list[tuple[float, int, int, int]]") -> "list[tuple[float, int, int, int]]":
    """Each event's time replaced by the gap since the previous one, or -1.0 when that gap is the host's (see the module docstring)."""
    out = []
    previous: float | None = None
    for nanos, pin, new, old in events:
        gap = -1.0 if previous is None or nanos - previous > HOST_GAP_NANOS else nanos - previous
        out.append((gap, pin, new, old))
        previous = nanos
    return out


def order_digest(events: "list[tuple[float, int, int, int]]") -> str:
    hasher = hashlib.blake2b(digest_size=16)
    for _nanos, pin, new, old in events:
        hasher.update(_ORDER.pack(pin, new, old))
    return hasher.hexdigest()


def save(path: str, events: "list[tuple[float, int, int, int]]") -> None:
    with gzip.open(path, "wt") as handle:
        json.dump(events, handle)


def load(path: str) -> "list[tuple[float, int, int, int]]":
    with gzip.open(path, "rt") as handle:
        return [tuple(event) for event in json.load(handle)]  # type: ignore[misc]


def compare(mine: "list", theirs: "list", tolerance_ns: float) -> "tuple[bool, str]":
    """(ok, message): ok iff the order is identical; the spacing is only described."""
    for index, (a, b) in enumerate(zip(mine, theirs, strict=False)):
        if a[1:] != b[1:]:
            return False, f"order differs at event {index}: this run {a}, saved {b}"
    if len(mine) != len(theirs):
        return (
            False,
            f"order agrees for {min(len(mine), len(theirs))} events, then lengths differ: {len(mine)} vs {len(theirs)}",
        )
    deviations = [
        abs(gap_a[0] - gap_b[0])
        for gap_a, gap_b in zip(normalised(mine), normalised(theirs), strict=True)
        if gap_a[0] >= 0 and gap_b[0] >= 0
    ]
    if not deviations:
        return True, "order identical; no comparable gaps"
    within = sum(1 for deviation in deviations if deviation <= tolerance_ns)
    return True, (
        f"order identical; spacing: {100 * within / len(deviations):.2f}% of {len(deviations)} gaps within "
        f"{tolerance_ns:.0f} ns, largest deviation {max(deviations):.0f} ns"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workload", choices=[w.name for w in WORKLOADS])
    parser.add_argument("--save", metavar="FILE", help="write the full stream (gzip JSON)")
    parser.add_argument("--compare", metavar="FILE", help="diff against a stream saved earlier")
    parser.add_argument(
        "--tolerance-ns", type=float, default=64.0, help="spacing deviation counted as close (default 64)"
    )
    args = parser.parse_args()

    started = time.perf_counter()
    events, sim_seconds = asyncio.run(record(args.workload))
    wall = time.perf_counter() - started
    per_pin = collections.Counter(pin for _nanos, pin, _new, _old in events)
    print(f"{args.workload}: {len(events)} pin changes in {sim_seconds:.3f} s simulated ({wall:.1f} s wall)")
    for pin, count in sorted(per_pin.items()):
        print(f"  GPIO {pin:2d}: {count:9d}  ({count / sim_seconds:12.0f} per simulated second)")
    print(f"  order hash  {order_digest(events)}")
    if args.save:
        save(args.save, events)
        print(f"  saved {args.save}")
    if args.compare:
        ok, message = compare(events, load(args.compare), args.tolerance_ns)
        print(f"  compare: {'OK' if ok else 'DIFFERENT'} - {message}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
