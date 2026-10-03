"""Phase 0 of docs/records/0096-cpp-mcu-core.md: which MCU blocks does real firmware actually touch?

Wraps every entry of ``RP2040.peripherals`` (plus ``sio`` and ``ppb``, which the bus reaches
separately) in a counting proxy, boots a real firmware image through ``MicroPythonDevice`` and
counts the register reads/writes that go through each block - per *phase* (boot, then each script
the workload runs), so a boot's profile is not mixed with its steady state.

    python scripts/bench/profile_access.py                        # every workload
    python scripts/bench/profile_access.py mp-idle picow-scan     # some of them
    python scripts/bench/profile_access.py --json out.json        # machine-readable too

Counts are exact and cheap. ``--timed`` also times each call with ``perf_counter`` to show where
Python-peripheral time goes; that inflates the numbers (the proxy's own timer calls), so read it
as an upper bound. Needs the firmware images: they are downloaded and cached on first use
(``rp2040py.utils.firmware_retrieve``), so the first run needs network access.

Only accesses that reach a *Python* block through the bus are counted - SRAM/flash/bootrom
accesses never leave the memory fast path and are not "peripheral" traffic.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

# Per-block stats everywhere below: block name -> [reads, writes, seconds inside the block].


class _CountingProxy:
    """Stands in for one peripheral in the bus's dispatch table: counts the four bus-facing calls,
    forwards everything else (attributes, other methods) to the real block untouched."""

    def __init__(self, name: str, target: Any, stats: "dict[str, list[float]]", timed: bool) -> None:
        self._name = name
        self._target = target
        self._stats = stats
        self._timed = timed
        stats.setdefault(name, [0, 0, 0.0])

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._target, attribute)

    def read_uint32(self, offset: int) -> int:
        entry = self._stats[self._name]
        entry[0] += 1
        if not self._timed:
            return self._target.read_uint32(offset)
        start = time.perf_counter()
        value = self._target.read_uint32(offset)
        entry[2] += time.perf_counter() - start
        return value

    def write_uint32(self, offset: int, value: int) -> None:
        entry = self._stats[self._name]
        entry[1] += 1
        if not self._timed:
            self._target.write_uint32(offset, value)
            return
        start = time.perf_counter()
        self._target.write_uint32(offset, value)
        entry[2] += time.perf_counter() - start

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        entry = self._stats[self._name]
        entry[1] += 1
        if not self._timed:
            self._target.write_uint32_atomic(offset, value, atomic_type)
            return
        start = time.perf_counter()
        self._target.write_uint32_atomic(offset, value, atomic_type)
        entry[2] += time.perf_counter() - start

    def reset(self) -> None:
        self._target.reset()


def install(mcu: RP2040, *, timed: bool = False) -> "dict[str, list[float]]":
    """Wraps every bus-reachable block of `mcu`; returns the live stats dict the proxies update."""
    stats: dict[str, list[float]] = {}
    for address, peripheral in list(mcu.peripherals.items()):
        name = str(getattr(peripheral, "name", type(peripheral).__name__))
        mcu.peripherals[address] = _CountingProxy(name, peripheral, stats, timed)  # type: ignore[assignment]
    mcu.sio = _CountingProxy("SIO", mcu.sio, stats, timed)  # type: ignore[assignment]
    mcu.ppb = _CountingProxy("PPB", mcu.ppb, stats, timed)  # type: ignore[assignment]
    return stats


def _snapshot(stats: "dict[str, list[float]]") -> "dict[str, list[float]]":
    return {name: list(entry) for name, entry in stats.items()}


def _delta(after: "dict[str, list[float]]", before: "dict[str, list[float]]") -> "dict[str, list[float]]":
    zero = [0, 0, 0.0]
    return {n: [a - b for a, b in zip(e, before.get(n, zero), strict=True)] for n, e in after.items()}


# --- workloads ---------------------------------------------------------------------------------

SLEEP_SCRIPT = "import time\ntime.sleep_ms(1000)\nprint('slept')"

SCAN_SCRIPT = """\
import network
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print(nic.scan())
"""

# A PIO state machine fed by a DMA channel (DREQ-paced into TXF0): the block mix MicroPython's idle
# REPL never touches. 0x50200010 = PIO0 base + TXF0.
PIO_DMA_SCRIPT = """\
import array, machine, rp2

@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_RIGHT, autopull=True, pull_thresh=32)
def shifter():
    out(pins, 1)

sm = rp2.StateMachine(0, shifter, freq=2_000_000, out_base=machine.Pin(2))
sm.active(1)
src = array.array('I', range(256))
dma = rp2.DMA()
ctrl = dma.pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0)
for _ in range(4):
    dma.config(read=src, write=0x50200010, count=len(src), ctrl=ctrl, trigger=True)
    while dma.active():
        pass
sm.active(0)
print('dma-done')
"""


@dataclass
class Workload:
    name: str
    description: str
    board: str
    circuitpython: bool = False
    tag: "str | None" = None  # firmware version; None = the board's default
    scripts: "list[tuple[str, str, str]]" = field(default_factory=list)  # (phase, source, expected stdout fragment)


WORKLOADS = [
    Workload("mp-idle", "MicroPython 1.21 on Pico: boot, a print, then 1 s of time.sleep_ms()", "pico",
             scripts=[("print", "print(1 + 1)", "2"), ("sleep-1s", SLEEP_SCRIPT, "slept")]),
    Workload("cp-boot", "CircuitPython on Pico: boot, then a print and 1 s of time.sleep()", "pico", circuitpython=True,
             scripts=[("print", "print(1 + 1)", "2"), ("sleep-1s", "import time\ntime.sleep(1)\nprint('slept')", "slept")]),
    Workload("picow-scan", "MicroPython on Pico W: boot, bring the CYW43 up, scan", "pico_w",
             scripts=[("wlan-scan", SCAN_SCRIPT, "RP2040PY-GUEST")]),
    Workload("pio-dma", "MicroPython 1.23 on Pico (rp2.DMA is not in 1.21): a PIO state machine fed by DMA", "pico",
             tag="1.23.0", scripts=[("pio-dma", PIO_DMA_SCRIPT, "dma-done")]),
]  # fmt: skip


async def run_workload(workload: Workload, *, timed: bool, timeout: float) -> "dict[str, Any]":
    spec = CIRCUITPYTHON if workload.circuitpython else MICROPYTHON
    board = resolve_board_spec(workload.board, spec, workload.tag)
    device = MicroPythonDevice(board=board, circuitpython=workload.circuitpython)
    mcu = device.mcu
    stats = install(mcu, timed=timed)
    result: dict[str, Any] = {"workload": workload.name, "description": workload.description, "phases": []}

    def phase(label: str, before: "dict[str, list[float]]", sim0: float, wall0: float, ok: bool) -> None:
        result["phases"].append(
            {
                "phase": label,
                "ok": ok,
                "sim_ms": (mcu.clock.nanos - sim0) / 1e6,
                "wall_s": time.perf_counter() - wall0,
                "blocks": _delta(_snapshot(stats), before),
            }
        )

    try:
        before, sim0, wall0 = _snapshot(stats), mcu.clock.nanos, time.perf_counter()
        try:
            await device.astart(timeout=timeout)
            phase("boot", before, sim0, wall0, True)
        except Exception as error:  # noqa: BLE001 - a workload that cannot boot still reports what it touched
            phase("boot", before, sim0, wall0, False)
            result["error"] = f"boot: {type(error).__name__}: {error}"
            return result
        for label, source, expected in workload.scripts:
            before, sim0, wall0 = _snapshot(stats), mcu.clock.nanos, time.perf_counter()
            try:
                stdout, stderr = await device.aexec(source, timeout=timeout)
                phase(label, before, sim0, wall0, expected.encode() in stdout)
                if expected.encode() not in stdout:
                    result["error"] = (
                        f"{label}: expected {expected!r}; stdout {stdout[-120:]!r}, stderr {stderr[-300:]!r}"
                    )
            except Exception as error:  # noqa: BLE001 - any failure of a script is a result, not a crash
                phase(label, before, sim0, wall0, False)
                result["error"] = f"{label}: {type(error).__name__}: {error}"
                break
    finally:
        device.stop()
    return result


def render(result: "dict[str, Any]", top: int = 8) -> str:
    lines = [f"== {result['workload']}: {result['description']}"]
    if "error" in result:
        lines.append(f"   !! {result['error']}")
    for phase in result["phases"]:
        blocks = phase["blocks"]
        total = sum(e[0] + e[1] for e in blocks.values())
        seconds = sum(e[2] for e in blocks.values())
        timing = f", {seconds:.2f}s inside blocks" if seconds else ""
        lines.append(
            f"-- {phase['phase']}{'' if phase['ok'] else ' (FAILED)'}: {phase['sim_ms']:.0f} ms simulated, "
            f"{phase['wall_s']:.1f} s wall, {total:.0f} accesses ({total / max(phase['sim_ms'], 1e-9):.1f} per sim-ms){timing}"
        )
        ranked = sorted(blocks.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:top]
        for name, (reads, writes, secs) in ranked:
            if reads + writes == 0:
                continue
            share = 100 * (reads + writes) / total if total else 0.0
            extra = f"  {secs:8.3f}s" if secs else ""
            lines.append(f"     {name:22s}{reads:11.0f} r{writes:11.0f} w  {share:5.1f}%{extra}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workloads", nargs="*", help=f"any of: {', '.join(w.name for w in WORKLOADS)} (default: all)")
    parser.add_argument("--json", help="also write the results to this file")
    parser.add_argument("--timed", action="store_true", help="also time each call (inflates the numbers)")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-phase wall-clock timeout, seconds")
    args = parser.parse_args()

    known = {w.name: w for w in WORKLOADS}
    unknown = [n for n in args.workloads if n not in known]
    if unknown:
        parser.error(f"unknown workload(s): {', '.join(unknown)}")
    chosen = [known[n] for n in args.workloads] or WORKLOADS

    results = []
    for workload in chosen:
        result = asyncio.run(run_workload(workload, timed=args.timed, timeout=args.timeout))
        results.append(result)
        print(render(result), flush=True)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
    return 1 if any("error" in r for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
