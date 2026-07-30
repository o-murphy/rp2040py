"""Unified command-line entry point for rp2040py.

Available both as the ``rp2040py`` console script (installed via pip/uv) and via
``python -m rp2040py``, so the emulator is runnable without a git checkout.

Subcommands:

- ``run``: generic hex/uf2 firmware runner with a GDB server, for native code (e.g. built from
  pico-examples).
- ``micropython``: MicroPython/CircuitPython UF2 runner with a USB CDC console.
- ``bench``: synthetic and real-firmware-boot throughput benchmark for
  ``CortexM0Core.execute_instruction()``.
"""

import argparse
import os
import sys
import termios
import threading
import time
import tty
from collections.abc import Callable

from rp2040py.cli.bootrom import BOOTROM_B1
from rp2040py.cli.intelhex import load_hex
from rp2040py.cli.load_flash import load_circuitpython_flash_image, load_micropython_flash_image, load_uf2
from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.assembler import opcode_adds2, opcode_subs2
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("main",)


def _load_image(image_name: str, rp2040: RP2040) -> None:
    extension = image_name.rsplit(".", 1)[-1]
    if extension == "hex":
        print(f"Loading hex image {image_name}")
        with open(image_name) as f:
            load_hex(f.read(), rp2040.flash, 0x10000000)
    elif extension == "uf2":
        print(f"Loading uf2 image {image_name}")
        load_uf2(image_name, rp2040)
    else:
        print(f"Unsupported file type: {extension}")
        sys.exit(1)


def _wait_for_simulator(simulator: Simulator, on_interrupt: "Callable[[], None] | None" = None) -> None:
    # simulator.execute() only runs the first burst synchronously and then reschedules itself via
    # threading.Timer, so the caller would otherwise return immediately and leave the process
    # hanging in interpreter shutdown, joining that non-daemon timer chain forever - and
    # unresponsive to Ctrl+C there. Waiting here on the main thread keeps KeyboardInterrupt
    # handling clean.
    try:
        while simulator.executing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        if on_interrupt is not None:
            on_interrupt()
        simulator.stop()
        os._exit(130)


def _cmd_run(args: argparse.Namespace) -> None:
    simulator = Simulator()
    mcu = simulator.rp2040
    mcu.load_bootrom(BOOTROM_B1)

    _load_image(args.image, mcu)

    gdb_server = GDBTCPServer(simulator, args.gdb_port)
    print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    def _on_byte(value: int) -> None:
        sys.stdout.buffer.write(bytes([value]))
        sys.stdout.flush()

    mcu.uart[0].on_byte = _on_byte

    mcu.core.pc = 0x10000000
    simulator.execute()
    _wait_for_simulator(simulator)


def _cmd_micropython(args: argparse.Namespace) -> None:
    simulator = Simulator()
    mcu = simulator.rp2040
    mcu.load_bootrom(BOOTROM_B1)
    mcu.logger = ConsoleLogger(LogLevel.ERROR)

    if not args.circuitpython:
        image_name = args.image or "RPI_PICO-20230426-v1.20.0.uf2"
    else:
        image_name = args.image or "adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2"
    print(f"Loading uf2 image {image_name}")
    load_uf2(image_name, mcu)

    if os.path.exists("littlefs.img") and not args.circuitpython:
        print("Loading uf2 image littlefs.img")
        load_micropython_flash_image("littlefs.img", mcu)
    elif os.path.exists("fat12.img") and args.circuitpython:
        load_circuitpython_flash_image("fat12.img", mcu)

    if args.gdb:
        gdb_server = GDBTCPServer(simulator, args.gdb_port)
        print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    cdc = USBCDC(mcu.usb_ctrl)

    def _on_device_connected() -> None:
        if not args.circuitpython:
            # We send a newline so the user sees the MicroPython prompt
            cdc.send_serial_byte(ord("\r"))
            cdc.send_serial_byte(ord("\n"))
        else:
            cdc.send_serial_byte(3)

    cdc.on_device_connected = _on_device_connected

    current_line = ""

    def _on_serial_data(value: bytes | bytearray) -> None:
        nonlocal current_line
        sys.stdout.buffer.write(value)
        sys.stdout.flush()

        for byte in value:
            char = chr(byte)
            if char == "\n":
                if args.expect_text and args.expect_text in current_line:
                    print(f'Expected text found: "{args.expect_text}"')
                    print("TEST PASSED.")
                    # os._exit(), not sys.exit(): this callback runs on a Simulator worker thread
                    # (threading.Timer), and sys.exit() there only terminates that thread, not the
                    # whole process (unlike Node's process.exit(), which the upstream JS relies on
                    # here).
                    os._exit(0)
                current_line = ""
            else:
                current_line += char

    cdc.on_serial_data = _on_serial_data

    stdin_fd = sys.stdin.fileno()
    old_termios = None
    if sys.stdin.isatty():
        old_termios = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    def _restore_termios() -> None:
        if old_termios is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)

    def _read_stdin_loop() -> None:
        try:
            while True:
                chunk = os.read(stdin_fd, 4096)
                if not chunk:
                    break
                # 24 is Ctrl+X
                if chunk[0] == 24:
                    # os._exit(), not sys.exit(): this runs on the dedicated stdin reader thread,
                    # not the main thread, so sys.exit() would only terminate that thread instead
                    # of the whole process.
                    _restore_termios()
                    os._exit(0)
                for byte in chunk:
                    cdc.send_serial_byte(byte)
        finally:
            _restore_termios()

    threading.Thread(target=_read_stdin_loop, daemon=True).start()

    mcu.core.pc = 0x10000000
    simulator.execute()
    _wait_for_simulator(simulator, on_interrupt=_restore_termios)


def _interpreter_label() -> str:
    impl = sys.implementation.name
    jit = getattr(sys, "_jit", None)
    if jit is not None and jit.is_enabled():
        impl += "+jit"
    return f"{impl} {'.'.join(str(part) for part in sys.version_info[:3])}"


def _bench_synthetic(instruction_count: int, block_size: int) -> None:
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


def _bench_firmware(image: str, littlefs: str | None, expect_text: str | None, timeout: float) -> None:
    # Uses Simulator (not a bare RP2040) so the clock actually advances: real firmware relies on
    # timer-based busy-waits during boot (e.g. hardware_timer's timer_busy_wait_until()), and those
    # spin forever if TIMERAWL/TIMERAWH never move - core.execute_instruction() alone does not tick
    # the clock, only Simulator.execute() (and this hand-rolled equivalent below) does.
    simulator = Simulator()
    rp2040 = simulator.rp2040
    clock = simulator.clock
    rp2040.load_bootrom(BOOTROM_B1)
    rp2040.logger = ConsoleLogger(LogLevel.ERROR)

    _load_image(image, rp2040)

    if littlefs:
        load_micropython_flash_image(littlefs, rp2040)

    current_line = ""
    found = False

    cdc = USBCDC(rp2040.usb_ctrl)

    def _on_device_connected() -> None:
        # nudge MicroPython to print its prompt, same as the micropython subcommand
        cdc.send_serial_byte(ord("\r"))
        cdc.send_serial_byte(ord("\n"))

    cdc.on_device_connected = _on_device_connected

    def _on_serial_data(value: bytes | bytearray) -> None:
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


def _cmd_bench(args: argparse.Namespace) -> None:
    if args.image:
        _bench_firmware(args.image, args.littlefs, args.expect_text, args.timeout)
    else:
        _bench_synthetic(args.instructions, args.block_size)


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(prog="rp2040py", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a native .hex/.uf2 image with a GDB server")
    run_parser.add_argument("--image", default="hello_uart.hex")
    run_parser.add_argument("--gdb-port", type=int, default=3333)
    run_parser.set_defaults(func=_cmd_run)

    mp_parser = subparsers.add_parser("micropython", help="run a MicroPython/CircuitPython UF2 image")
    mp_parser.add_argument("--image")
    mp_parser.add_argument("--expect-text")
    mp_parser.add_argument("--gdb", action="store_true")
    mp_parser.add_argument("--gdb-port", type=int, default=3333)
    mp_parser.add_argument("--circuitpython", action="store_true")
    mp_parser.set_defaults(func=_cmd_micropython)

    bench_parser = subparsers.add_parser("bench", help="benchmark instruction-dispatch throughput")
    bench_parser.add_argument("--instructions", type=int, default=5_000_000, help="synthetic mode: instruction count")
    bench_parser.add_argument("--block-size", type=int, default=1000, help="synthetic mode: instructions per block")
    bench_parser.add_argument("--image", help="firmware mode: path to a .hex or .uf2 image")
    bench_parser.add_argument("--littlefs", help="firmware mode: optional littlefs.img to load")
    bench_parser.add_argument("--expect-text", help="firmware mode: stop once this text appears on UART0")
    bench_parser.add_argument("--timeout", type=float, default=60.0, help="firmware mode: seconds before giving up")
    bench_parser.set_defaults(func=_cmd_bench)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
