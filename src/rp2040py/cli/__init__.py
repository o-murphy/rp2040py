"""Unified command-line entry point for rp2040py.

Available both as the ``rp2040py`` console script (installed via pip/uv) and via
``python -m rp2040py``, so the emulator is runnable without a git checkout.

Subcommands:

- ``run``: generic hex/uf2 firmware runner with a GDB server, for native code (e.g. built from
  pico-examples).
- ``micropython``: MicroPython/CircuitPython UF2 runner with a USB CDC console. ``-c <command>``,
  ``-m <module>``, or a script ``<filename>`` run non-interactively via the raw-REPL protocol
  instead of dropping into the REPL, mirroring ``micropython``'s own CLI. ``--image`` accepts a
  known version tag (e.g. ``1.21.0``), a local file path, or is omitted entirely - either way,
  missing firmware is downloaded automatically (see ``rp2040py.cli.firmware_retrieve``).
- ``kaluma``: Kaluma (https://kaluma.io/) UF2 runner with a USB CDC console, interactive REPL
  only - Kaluma has no raw-REPL-equivalent protocol, so unlike ``micropython`` there's no
  ``-c``/``-m``/``<filename>``. ``--image`` accepts a version tag, a local file path, or is
  omitted entirely to download the default (see ``rp2040py.cli.firmware_retrieve``).
- ``bench``: synthetic and real-firmware-boot throughput benchmark for
  ``CortexM0Core.execute_instruction()``.
- ``mklittlefs``: build/update a littlefs image for ``micropython``'s filesystem support (needs
  the optional ``fs`` extra: ``pip install rp2040py[fs]``). ``--disk-version`` selects the
  littlefs on-disk format (defaults to ``2.0``, for compatibility with MicroPython <=1.21).
"""

import argparse
import importlib.util
import os
import sys
import time
from collections.abc import Callable
from importlib.metadata import version

from rp2040py.cli.firmware_retrieve import CIRCUITPYTHON, KALUMA, MICROPYTHON, retrieve
from rp2040py.cli.intelhex import load_hex
from rp2040py.cli.mklittlefs import LITTLEFS_DEFAULT_DISK_VERSION, LITTLEFS_DISK_VERSIONS, build_littlefs_image
from rp2040py.cli.stdio_repl import StdioInteractiveRepl
from rp2040py.cli.stdio_repl import buf_write as _buf_write
from rp2040py.cli.stdio_repl import os_exit as _os_exit
from rp2040py.device.kaluma_device import KalumaDevice
from rp2040py.device.load_flash import (
    MICROPYTHON_FS_BLOCKCOUNT,
    MICROPYTHON_FS_BLOCKSIZE,
    load_micropython_flash_image,
    load_uf2,
)
from rp2040py.device.mp_device import MicroPythonDevice
from rp2040py.device.raw_repl import RawReplError
from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.assembler import opcode_adds2, opcode_subs2
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("main",)

# mklittlefs's only dependency, littlefs-python, is the optional `fs` extra rather than a hard
# runtime dependency - only register the subcommand (and thus advertise it in --help) when it's
# actually installed, instead of adding it unconditionally and failing lazily once invoked.
_HAS_LITTLEFS = importlib.util.find_spec("littlefs") is not None


def _load_image(image_name: str, rp2040: RP2040) -> None:
    extension = image_name.rsplit(".", 1)[-1]
    if extension == "hex":
        print(f"Loading hex image: {image_name}")
        with open(image_name) as f:
            load_hex(f.read(), rp2040.flash, 0x10000000)
    elif extension == "uf2":
        print(f"Loading uf2 image: {image_name}")
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
        _os_exit(130)


def _cmd_run(args: argparse.Namespace) -> None:
    simulator = Simulator()
    mcu = simulator.rp2040

    from rp2040py.device.bootrom import BOOTROM_B1

    mcu.load_bootrom(BOOTROM_B1)

    _load_image(args.image, mcu)

    gdb_server = GDBTCPServer(simulator, args.gdb_port)
    print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    def _on_byte(value: int) -> None:
        _buf_write(sys.stdout, value)

    mcu.uart[0].on_byte = _on_byte

    mcu.core.pc = 0x10000000
    simulator.execute()
    _wait_for_simulator(simulator)


def _raw_repl_source(args: argparse.Namespace) -> "str | None":
    if args.command is not None:
        return args.command
    if args.module is not None:
        return f"import {args.module}"
    if args.filename is not None:
        with open(args.filename, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


def _make_expect_text_watcher(expect_text: "str | None") -> "Callable[[bytes | bytearray], None]":
    """Returns an `on_data` callback for `StdioInteractiveRepl` that scans serial output for
    `expect_text` and exits the process once found - the `--expect-text` CI-test-harness hook
    shared by `micropython` and `kaluma`."""
    current_line = ""

    def _watch(value: bytes | bytearray) -> None:
        nonlocal current_line
        for byte in value:
            char = chr(byte)
            if char == "\n":
                if expect_text and expect_text in current_line:
                    print(f'Expected text found: "{expect_text}"')
                    print("TEST PASSED.")
                    # _os_exit(), not sys.exit(): this callback runs on a Simulator worker thread
                    # (threading.Timer), and sys.exit() there only terminates that thread, not the
                    # whole process (unlike Node's process.exit(), which the upstream JS relies on
                    # here).
                    _os_exit(0)
                current_line = ""
            else:
                current_line += char

    return _watch


def _cmd_micropython(args: argparse.Namespace) -> None:
    image_name = retrieve(CIRCUITPYTHON if args.circuitpython else MICROPYTHON, args.image)
    if image_name is None:
        print(f"Could not find micropython image: {args.image}")
        sys.exit(1)

    print(f"Loading uf2 image: {image_name}")
    littlefs = args.littlefs if not args.circuitpython and os.path.exists(args.littlefs) else None
    fat12 = args.fat12 if args.circuitpython and os.path.exists(args.fat12) else None

    if littlefs is not None:
        print(f"Loading littlefs image: {littlefs}")

    if fat12 is not None:
        print(f"Loading fat12 image: {fat12}")

    device = MicroPythonDevice(image_name, littlefs=littlefs, fat12=fat12, circuitpython=args.circuitpython)

    if args.gdb:
        gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
        print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    raw_repl_source = _raw_repl_source(args)
    if raw_repl_source is not None:
        # No timeout (unlike MicroPythonDevice's library default): matches this CLI's existing
        # philosophy elsewhere of running until done or Ctrl+C, not an arbitrary deadline.
        try:
            device.start(timeout=None)
            stdout, stderr = device.exec(raw_repl_source, timeout=None)
        except KeyboardInterrupt:
            device.stop()
            sys.exit(130)
        except (TimeoutError, RawReplError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            device.stop()
            sys.exit(1)
        _buf_write(sys.stdout, stdout)
        if stderr:
            _buf_write(sys.stderr, stderr)
        device.stop()
        sys.exit(1 if stderr else 0)

    cdc = device.cdc

    # Constructed (and its on_serial_data wired) before start() so nothing the device prints
    # while enumerating is dropped.
    repl = StdioInteractiveRepl(cdc, on_data=_make_expect_text_watcher(args.expect_text))
    repl.start()

    device.start(timeout=None)
    if not args.circuitpython:
        # We send a newline so the user sees the MicroPython prompt
        cdc.send_serial_byte(ord("\r"))
        cdc.send_serial_byte(ord("\n"))
    else:
        cdc.send_serial_byte(3)

    _wait_for_simulator(device.simulator, on_interrupt=repl.stop)


def _cmd_kaluma(args: argparse.Namespace) -> None:
    image_name = retrieve(KALUMA, args.image)
    if image_name is None:
        print(f"Could not find kaluma image: {args.image}")
        sys.exit(1)

    print(f"Loading uf2 image: {image_name}")
    littlefs = args.littlefs if os.path.exists(args.littlefs) else None
    if littlefs is not None:
        print(f"Loading littlefs image: {littlefs}")

    device = KalumaDevice(image_name, littlefs=littlefs)

    if args.gdb:
        gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
        print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    cdc = device.cdc

    # Constructed (and its on_serial_data wired) before start() so nothing the device prints
    # while enumerating is dropped.
    repl = StdioInteractiveRepl(cdc, on_data=_make_expect_text_watcher(args.expect_text))
    repl.start()

    device.start(timeout=None)
    # A blank line nudges Kaluma's REPL into printing its prompt (its own docs: "if you cannot see
    # the prompt, press Enter several times") - no raw-REPL-equivalent nudge byte to send instead,
    # unlike micropython's --circuitpython branch.
    cdc.send_serial_byte(ord("\r"))
    cdc.send_serial_byte(ord("\n"))

    _wait_for_simulator(device.simulator, on_interrupt=repl.stop)


def _interpreter_label() -> str:
    impl = sys.implementation.name
    jit = getattr(sys, "_jit", None)
    if jit is not None and jit.is_enabled():
        impl += "+jit"
    return f"{impl} {'.'.join(str(part) for part in sys.version_info[:3])}"


def _bench_synthetic(instruction_count: int, block_size: int) -> None:
    rp2040 = RP2040()

    from rp2040py.device.bootrom import BOOTROM_B1

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

    from rp2040py.device.bootrom import BOOTROM_B1

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


def _cmd_mklittlefs(args: argparse.Namespace) -> None:
    try:
        build_littlefs_image(
            args.output,
            args.files,
            block_size=args.block_size,
            block_count=args.block_count,
            disk_version=args.disk_version,
            main=args.main,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote littlefs image: {args.output}")

    if sys.implementation.name == "pypy":
        # _os_exit(), not a normal return: under PyPy, littlefs-python's LittleFS/file C objects
        # can get finalized out of order during interpreter shutdown - even after an explicit
        # lfs.unmount() and gc.collect() - crashing the process with "lfs_file_sync: Assertion
        # `lfs_mlist_isopen(...)` failed" (SIGABRT) despite the image already having been written
        # correctly. Confirmed with `uv tool install --python pypy-3.10 rp2040py[fs]`; not
        # reproducible under CPython, hence gating this rather than doing it unconditionally - an
        # unconditional _os_exit() here would also kill the whole process if _cmd_mklittlefs (or
        # main()) is ever called in-process rather than as the actual entry point, e.g. from a test
        # suite. The image on disk is already complete and correct at this point, so skipping the
        # rest of shutdown is safe for the *pypy CLI* case specifically - it doesn't help a
        # long-running PyPy program that calls build_littlefs_image() as a library and keeps
        # running afterwards, which is a real upstream littlefs-python/PyPy limitation, not
        # something rp2040py can fully paper over.
        _os_exit(0)


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(prog="rp2040py", description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {version('rp2040py')}")
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
    mp_parser.add_argument("--littlefs", help="firmware mode: optional littlefs.img to load", default="littlefs.img")
    mp_parser.add_argument("--fat12", help="firmware mode: optional fat12.img to load", default="fat12.img")
    mp_source_group = mp_parser.add_mutually_exclusive_group()
    mp_source_group.add_argument(
        "-c", dest="command", metavar="<command>", help="execute the given command on the device, then exit"
    )
    mp_source_group.add_argument(
        "-m",
        dest="module",
        metavar="<module>",
        help="import the given module on the device (approximates `-m`), then exit",
    )
    mp_source_group.add_argument("filename", nargs="?", help="run the given local script file on the device, then exit")
    mp_parser.set_defaults(func=_cmd_micropython)

    kaluma_parser = subparsers.add_parser("kaluma", help="run a Kaluma UF2 image (interactive REPL only)")
    kaluma_parser.add_argument("--image", help="version tag, local file path, or omitted to download the default")
    kaluma_parser.add_argument("--expect-text")
    kaluma_parser.add_argument("--gdb", action="store_true")
    kaluma_parser.add_argument("--gdb-port", type=int, default=3333)
    kaluma_parser.add_argument("--littlefs", help="optional littlefs.img to load", default="kaluma_littlefs.img")
    kaluma_parser.set_defaults(func=_cmd_kaluma)

    bench_parser = subparsers.add_parser("bench", help="benchmark instruction-dispatch throughput")
    bench_parser.add_argument("--instructions", type=int, default=5_000_000, help="synthetic mode: instruction count")
    bench_parser.add_argument("--block-size", type=int, default=1000, help="synthetic mode: instructions per block")
    bench_parser.add_argument("--image", help="firmware mode: path to a .hex or .uf2 image")
    bench_parser.add_argument("--littlefs", help="firmware mode: optional littlefs.img to load")
    bench_parser.add_argument("--expect-text", help="firmware mode: stop once this text appears on UART0")
    bench_parser.add_argument("--timeout", type=float, default=60.0, help="firmware mode: seconds before giving up")
    bench_parser.set_defaults(func=_cmd_bench)

    if _HAS_LITTLEFS:
        mklittlefs_parser = subparsers.add_parser(
            "mklittlefs", help="build/update a littlefs image for `micropython`'s filesystem support"
        )
        mklittlefs_parser.add_argument("files", nargs="+", help="source files to add, keeping their own basename")
        mklittlefs_parser.add_argument(
            "--main", metavar="<basename>", help="write the `files` entry with this basename as main.py"
        )
        mklittlefs_parser.add_argument(
            "-o", "--output", default="littlefs.img", help="output image path (updated in place if it already exists)"
        )
        mklittlefs_parser.add_argument("--block-size", type=int, default=MICROPYTHON_FS_BLOCKSIZE)
        mklittlefs_parser.add_argument("--block-count", type=int, default=MICROPYTHON_FS_BLOCKCOUNT)
        mklittlefs_parser.add_argument(
            "--disk-version",
            type=str,
            choices=LITTLEFS_DISK_VERSIONS.keys(),
            default=LITTLEFS_DEFAULT_DISK_VERSION,
            help=f"(defaults to {LITTLEFS_DEFAULT_DISK_VERSION})",
        )
        mklittlefs_parser.set_defaults(func=_cmd_mklittlefs)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
