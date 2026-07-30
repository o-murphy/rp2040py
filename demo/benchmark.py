"""Benchmark CortexM0Core.execute_instruction() throughput.

Two modes:

- Synthetic (default): writes a straight-line block of alternating ADDS/SUBS instructions to
  RAM and repeatedly executes it, resetting PC between blocks. No branches, no bus/peripheral
  traffic beyond RAM fetches - isolates raw instruction-dispatch overhead, useful for comparing
  interpreters (CPython vs PyPy vs CPython 3.14+ JIT) or measuring the effect of core-level
  optimizations.
- Firmware boot (--image): loads a real hex/uf2 image (plus an optional littlefs.img) and runs
  it to completion (or until --expect-text is found, or --timeout elapses), reporting wall time
  and instructions/sec for a realistic end-to-end workload. Mirrors demo/micropython_run.py's
  boot path (including the USB CDC console MicroPython/CircuitPython print to) but headless - no
  GDB server, no stdin thread - and exits on its own.

Usage:
    python demo/benchmark.py                                   # synthetic, 5,000,000 instructions
    python demo/benchmark.py --instructions 20000000            # synthetic, more instructions
    python demo/benchmark.py --image micropython.uf2 --expect-text "Hello, MicroPython!"
"""

import argparse
import sys
import time

from bootrom import BOOTROM_B1
from intelhex import load_hex
from load_flash import load_micropython_flash_image, load_uf2

from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.assembler import opcode_adds2, opcode_subs2
from rp2040py.utils.logging import ConsoleLogger, LogLevel


def _interpreter_label() -> str:
    impl = sys.implementation.name
    jit = getattr(sys, "_jit", None)
    if jit is not None and jit.is_enabled():
        impl += "+jit"
    return f"{impl} {'.'.join(str(part) for part in sys.version_info[:3])}"


def run_synthetic(instruction_count: int, block_size: int) -> None:
    rp2040 = RP2040()
    rp2040.load_bootrom(BOOTROM_B1)
    rp2040.logger = ConsoleLogger(LogLevel.ERROR)

    core = rp2040.core
    addr = RAM_START_ADDRESS
    for i in range(block_size):
        opcode = opcode_adds2(0, 1) if i % 2 == 0 else opcode_subs2(0, 1)
        rp2040.write_uint16(addr, opcode)
        addr += 2

    print(f"Interpreter: {_interpreter_label()}")
    print(f"Synthetic benchmark: {instruction_count:,} instructions, {block_size}-instruction block")

    executed = 0
    start = time.perf_counter()
    while executed < instruction_count:
        core.registers[15] = RAM_START_ADDRESS
        for _ in range(block_size):
            core.execute_instruction()
        executed += block_size
    elapsed = time.perf_counter() - start

    print(f"Executed {executed:,} instructions in {elapsed:.2f}s -> {executed / elapsed:,.0f} instructions/sec")


def run_firmware(image: str, littlefs: str | None, expect_text: str | None, timeout: float) -> None:
    # Uses Simulator (not a bare RP2040) so the clock actually advances: real firmware relies on
    # timer-based busy-waits during boot (e.g. hardware_timer's timer_busy_wait_until()), and
    # those spin forever if TIMERAWL/TIMERAWH never move - core.execute_instruction() alone does
    # not tick the clock, only Simulator.execute() (and this hand-rolled equivalent below) does.
    simulator = Simulator()
    rp2040 = simulator.rp2040
    clock = simulator.clock
    rp2040.load_bootrom(BOOTROM_B1)
    rp2040.logger = ConsoleLogger(LogLevel.ERROR)

    extension = image.rsplit(".", 1)[-1]
    if extension == "hex":
        with open(image) as f:
            load_hex(f.read(), rp2040.flash, 0x10000000)
    elif extension == "uf2":
        load_uf2(image, rp2040)
    else:
        print(f"Unsupported file type: {extension}")
        sys.exit(1)

    if littlefs:
        load_micropython_flash_image(littlefs, rp2040)

    current_line = ""
    found = False

    cdc = USBCDC(rp2040.usb_ctrl)

    def _on_device_connected() -> None:
        # nudge MicroPython to print its prompt, same as demo/micropython_run.py
        cdc.send_serial_byte(ord("\r"))
        cdc.send_serial_byte(ord("\n"))

    cdc.on_device_connected = _on_device_connected

    def _on_serial_data(value: bytes) -> None:
        nonlocal current_line, found
        for byte in value:
            char = chr(byte)
            if char == "\n":
                if expect_text and expect_text in current_line:
                    found = True
                current_line = ""
            else:
                current_line += char

    cdc.on_serial_data = _on_serial_data

    print(f"Interpreter: {_interpreter_label()}")
    print(f"Firmware benchmark: {image}" + (f" (expecting {expect_text!r})" if expect_text else ""))

    rp2040.core.pc = 0x10000000
    cycle_nanos = 1e9 / 125_000_000  # 125 MHz
    start = time.perf_counter()
    step_batch = 1_000_000
    executed = 0
    while not found and (time.perf_counter() - start) < timeout:
        for _ in range(step_batch):
            if rp2040.core.waiting:
                clock.tick(clock.nanos_to_next_alarm)
            else:
                cycles = rp2040.core.execute_instruction()
                clock.tick(cycles * cycle_nanos)
        executed += step_batch
    elapsed = time.perf_counter() - start

    status = "found expected text" if found else ("timed out" if expect_text else "step budget reached")
    print(
        f"{status}: executed {executed:,} instructions in {elapsed:.2f}s -> {executed / elapsed:,.0f} instructions/sec"
    )
    if expect_text and not found:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instructions", type=int, default=5_000_000, help="synthetic mode: instructions to execute")
    parser.add_argument(
        "--block-size", type=int, default=1000, help="synthetic mode: instructions per straight-line block"
    )
    parser.add_argument("--image", help="firmware mode: path to a .hex or .uf2 image")
    parser.add_argument("--littlefs", help="firmware mode: optional littlefs.img to load at the MicroPython fs offset")
    parser.add_argument("--expect-text", help="firmware mode: stop once this text appears on UART0")
    parser.add_argument("--timeout", type=float, default=60.0, help="firmware mode: seconds before giving up")
    args = parser.parse_args()

    if args.image:
        run_firmware(args.image, args.littlefs, args.expect_text, args.timeout)
    else:
        run_synthetic(args.instructions, args.block_size)


if __name__ == "__main__":
    main()
