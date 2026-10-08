"""Phase 0 of docs/records/0096-cpp-mcu-core.md: record a real firmware's traffic to one MCU block,
then replay it against a fresh instance and diff (tests/utils/mmio_trace.py has the machinery and
its docstring says what a trace does and does not capture).

    python scripts/bench/trace_block.py timer                    # MicroPython boot + a print
    python scripts/bench/trace_block.py sio --workload cp-boot   # another block / firmware
    python scripts/bench/trace_block.py timer --sleep --save timer.jsonl.gz
    python scripts/bench/trace_block.py pins                     # IO_BANK0 + PADS_BANK0 (+ the SIO writes that drive pins)
    python scripts/bench/trace_block.py pins --workload pins-mp  # a MicroPython script with an external button and a pin interrupt

A clean run (``0 mismatches``) of the Python block against *itself* is the precondition for using the
same trace on a C++ implementation: if the harness cannot reproduce Python-vs-Python, it has missed
an input and a later C++ diff would be meaningless.
"""

import argparse
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tests"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from profile_access import WORKLOADS
from utils.mmio_trace import PPB, SIO, record, replay, save
from utils.pin_trace import record_pins, replay_pins

from rp2040py.boards import resolve_board_spec
from rp2040py.device import MicroPythonDevice
from rp2040py.irq import IRQ
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, MICROPYTHON

# block name -> (peripherals-table key or "sio"/"ppb", the interrupt lines it raises)
BLOCKS = {
    "timer": (0x40054, (IRQ.TIMER_0, IRQ.TIMER_1, IRQ.TIMER_2, IRQ.TIMER_3)),
    "sio": (SIO, ()),
    "ppb": (PPB, ()),
}


def _chip_with_timer(kind: str):
    """A bare chip whose TIMER is the native block ("native", the default) or the pure-Python reference ("pure")."""
    from rp2040py.peripherals._timer import RPTimer as PureTimer
    from rp2040py.rp2040 import RP2040

    chip = RP2040()
    if kind == "pure":
        chip.timer = chip.peripherals[0x40054] = PureTimer(
            chip, "TIMER_BASE"
        )  # `chip.timer` too: the watchdog tells it the tick
    return chip


def _chip_with_sio(kind: str):
    """A bare chip whose SIO is the native block ("native", the default) or the pure-Python reference ("pure")."""
    from rp2040py._sio import RPSIO as PureSIO
    from rp2040py.rp2040 import RP2040

    chip = RP2040()
    if kind == "pure":
        chip.sio = PureSIO(chip)
    return chip


async def record_workload(
    workload_name: str, block: "int | str", irqs: "tuple[int, ...]", *, sleep: bool, timer: str = "native"
) -> "list":
    workload = next(w for w in WORKLOADS if w.name == workload_name)
    spec = CIRCUITPYTHON if workload.circuitpython else MICROPYTHON
    device = MicroPythonDevice(
        board=resolve_board_spec(workload.board, spec, workload.tag), circuitpython=workload.circuitpython
    )
    if block == SIO and timer == "pure":  # record the pure-Python SIO instead of the native one
        from rp2040py._sio import RPSIO as PureSIO

        device.mcu.sio = PureSIO(device.mcu)
    if block == 0x40054 and timer == "pure":  # record the pure-Python TIMER instead of the native one
        from rp2040py.peripherals._timer import RPTimer as PureTimer

        device.mcu.timer = device.mcu.peripherals[0x40054] = PureTimer(
            device.mcu, "TIMER_BASE"
        )  # `mcu.timer` too: the watchdog tells it the tick
    events = record(device.mcu, block, irqs)  # before anything runs: replay starts a fresh block at t=0
    try:
        await device.astart()
        for label, source, _ in workload.scripts:
            if label.startswith("sleep") and not sleep:
                continue
            await device.aexec(source)
    finally:
        device.stop()
    return events


# A script that exercises what a boot's idle REPL never does: an output pin, an input with each pull, an edge interrupt - the host plays the
# button (it drives GPIO 14 from outside, which no register access shows, so pin_trace samples it).
PINS_SETUP = """\
from machine import Pin
hits = []
out = Pin(15, Pin.OUT)
up = Pin(14, Pin.IN, Pin.PULL_UP)
down = Pin(13, Pin.IN, Pin.PULL_DOWN)
up.irq(lambda p: hits.append(p.value()), Pin.IRQ_FALLING | Pin.IRQ_RISING)
for i in range(8):
    out.value(i & 1)
print('ready', up.value(), down.value())
"""
PINS_REPORT = "print('hits', hits, up.value(), down.value(), out.value())"


async def record_pins_workload(workload_name: str) -> "list":
    spec_workload = "mp-idle" if workload_name == "pins-mp" else workload_name
    workload = next(w for w in WORKLOADS if w.name == spec_workload)
    spec = CIRCUITPYTHON if workload.circuitpython else MICROPYTHON
    device = MicroPythonDevice(
        board=resolve_board_spec(workload.board, spec, workload.tag), circuitpython=workload.circuitpython
    )
    events = record_pins(device.mcu)
    try:
        await device.astart()
        if workload_name == "pins-mp":
            await device.aexec(PINS_SETUP)
            button = device.mcu.gpio[14]
            for level in (False, True, False, True):  # press, release, press, release
                device.simulator.schedule_threadsafe(lambda level=level: button.set_input_value(level))
                await device.aexec("pass")
            device.simulator.schedule_threadsafe(button.release_input)
            await device.aexec(PINS_REPORT)
        else:
            for label, source, _ in workload.scripts:
                if label.startswith("sleep"):
                    continue
                await device.aexec(source)
    finally:
        device.stop()
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("block", choices=sorted(BLOCKS) + ["pins"])
    parser.add_argument("--workload", default="mp-idle", choices=[w.name for w in WORKLOADS] + ["pins-mp"])
    parser.add_argument("--sleep", action="store_true", help="also run the workload's 1 s sleep phase (~10^6 events)")
    parser.add_argument("--save", help="write the trace here (gzip JSON lines)")
    parser.add_argument(
        "--record-timer",
        choices=["native", "pure"],
        default="native",
        help="which TIMER/SIO to record (timer and sio blocks)",
    )
    parser.add_argument(
        "--replay-timer",
        choices=["native", "pure"],
        default="native",
        help="which TIMER/SIO to replay against (timer and sio blocks)",
    )
    args = parser.parse_args()

    if args.block == "pins":
        return _main_pins(args)
    key, irqs = BLOCKS[args.block]
    start = time.perf_counter()
    events = asyncio.run(record_workload(args.workload, key, irqs, sleep=args.sleep, timer=args.record_timer))
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event[1]] = kinds.get(event[1], 0) + 1
    print(f"recorded {len(events)} events in {time.perf_counter() - start:.1f}s: {kinds}")
    if args.save:
        save(events, args.save)
        print(f"saved {args.save}")

    start = time.perf_counter()
    make_chip = _chip_with_sio if key == SIO else _chip_with_timer
    mismatches = replay(events, key, irqs, factory=lambda: make_chip(args.replay_timer))
    print(f"replayed in {time.perf_counter() - start:.1f}s: {len(mismatches)} mismatches")
    for mismatch in mismatches[:10]:
        print("  ", mismatch)
    return 1 if mismatches else 0


def _main_pins(args: argparse.Namespace) -> int:
    start = time.perf_counter()
    events = asyncio.run(record_pins_workload(args.workload))
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event[1]] = kinds.get(event[1], 0) + 1
    print(f"recorded {len(events)} events in {time.perf_counter() - start:.1f}s: {kinds}")
    if args.save:
        save(events, args.save)
        print(f"saved {args.save}")
    start = time.perf_counter()
    mismatches = replay_pins(events)
    print(f"replayed in {time.perf_counter() - start:.1f}s: {len(mismatches)} mismatches")
    for mismatch in mismatches[:10]:
        print("  ", mismatch)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
