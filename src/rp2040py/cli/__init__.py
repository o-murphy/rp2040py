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
  ``-c``/``-m``/``<filename>`` exec mode. ``--image`` accepts a version tag, a local file path, or
  is omitted entirely to download the default (see ``rp2040py.cli.firmware_retrieve``). An
  optional ``<filename>`` positional stages a local ``.js`` file into Kaluma's "user program"
  flash region before boot, matching ``kaluma flash <file>`` on real hardware - Kaluma
  auto-executes it on every boot, unlike its ``--littlefs`` filesystem (plain storage, no
  auto-run of its own).
- ``bench``: synthetic and real-firmware-boot throughput benchmark for
  ``CortexM0Core.execute_instruction()``.
- ``mklittlefs``: build a littlefs image for ``micropython``'s filesystem support (needs
  the optional ``fs`` extra: ``pip install rp2040py[fs]``); ``-f``/``--force`` to overwrite an
  existing ``--output`` (always builds fresh - never merges with existing content, since a
  mismatched ``--block-size``/``--block-count`` against a stale image would otherwise silently
  produce a corrupted one). ``--disk-version`` selects the
  littlefs on-disk format (defaults to ``2.0``, for compatibility with MicroPython <=1.21).
"""

import argparse
import contextlib
import importlib.util
import logging
import struct
import sys
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from rp2040py.cli.firmware_retrieve import BOOTROM, CIRCUITPYTHON, KALUMA, MICROPYTHON, retrieve
from rp2040py.cli.intelhex import load_hex
from rp2040py.cli.mklittlefs import LITTLEFS_DEFAULT_DISK_VERSION, LITTLEFS_DISK_VERSIONS, build_littlefs_image
from rp2040py.cli.stdio_repl import StdioInteractiveRepl
from rp2040py.cli.stdio_repl import buf_write as _buf_write
from rp2040py.cli.stdio_repl import os_exit as _os_exit
from rp2040py.device.kaluma_device import KalumaDevice
from rp2040py.device.load_flash import (
    CIRCUITPYTHON_FS_BLOCKCOUNT,
    CIRCUITPYTHON_FS_BLOCKSIZE,
    KALUMA_FS_BLOCKCOUNT,
    KALUMA_FS_BLOCKSIZE,
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
from rp2040py.simulator import ShutdownRequest, Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.assembler import opcode_adds2, opcode_subs2
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("main",)

_logger = logging.getLogger(__name__)

# mklittlefs's only dependency, littlefs-python, is the optional `fs` extra rather than a hard
# runtime dependency - only register the subcommand (and thus advertise it in --help) when it's
# actually installed, instead of adding it unconditionally and failing lazily once invoked.
_HAS_LITTLEFS = importlib.util.find_spec("littlefs") is not None

_LOG_LEVEL_CHOICES = ("debug", "info", "warning", "error")
# The emulator's own component Logger (ConsoleLogger/LogLevel, see rp2040.logger) speaks a
# different vocabulary from stdlib logging - notably WARN, not WARNING - so --log-level's stdlib-
# style choices need translating rather than a 1:1 name lookup.
_CONSOLE_LOG_LEVEL = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warning": LogLevel.WARN,
    "error": LogLevel.ERROR,
}


def _console_log_level(args: argparse.Namespace) -> LogLevel:
    # Every rp2040.logger assignment below defaulted to ConsoleLogger(LogLevel.ERROR) before
    # --log-level existed (bar `run`, which inherited RP2040's own DEBUG default by omission - an
    # inconsistency fixed here rather than preserved) - ERROR is the fallback so leaving the flag
    # unset changes nothing.
    return _CONSOLE_LOG_LEVEL[args.log_level] if args.log_level else LogLevel.ERROR


def _load_image(image_name: str, rp2040: RP2040) -> None:
    extension = image_name.rsplit(".", 1)[-1]
    if extension == "hex":
        _logger.info("Loading hex image: %s", image_name)
        with open(image_name) as f:
            load_hex(f.read(), rp2040.flash, 0x10000000)
    elif extension == "uf2":
        _logger.info("Loading uf2 image: %s", image_name)
        load_uf2(image_name, rp2040)
    else:
        _logger.error("Unsupported file type: %s", extension)
        sys.exit(1)


def _resolve_bootrom_words(source: "str | None") -> "list[int]":
    """Resolves `--bootrom` (a `b0`/`b1`/`b2` version tag, or a local `.elf`/`.bin` path) into the
    4096-word list `RP2040.load_bootrom()` expects. `None` (the flag omitted) keeps today's exact
    behavior - the hardcoded `BOOTROM_B1` - so every existing caller is unaffected."""
    if source is None:
        from rp2040py.device.bootrom import BOOTROM_B1

        return BOOTROM_B1

    path = retrieve(BOOTROM, source)
    if path is None:
        _logger.error("Could not find bootrom: %s", source)
        sys.exit(1)

    extension = path.rsplit(".", 1)[-1].lower()
    if extension == "elf":
        from elftools.elf.elffile import ELFFile

        _logger.info("Loading bootrom elf: %s", path)
        with open(path, "rb") as f:
            elffile = ELFFile(f)
            segment = next(
                (seg for seg in elffile.iter_segments() if seg["p_type"] == "PT_LOAD" and seg["p_paddr"] == 0),
                None,
            )
            if segment is None:
                _logger.error("No PT_LOAD segment at address 0 found in %s", path)
                sys.exit(1)
            data = segment.data()
    else:
        _logger.info("Loading bootrom binary: %s", path)
        with open(path, "rb") as f:
            data = f.read()

    word_count = len(data) // 4
    return list(struct.unpack(f"<{word_count}I", data[: word_count * 4]))


def _cmd_run(args: argparse.Namespace) -> None:
    simulator = Simulator()
    mcu = simulator.rp2040

    mcu.load_bootrom(_resolve_bootrom_words(args.bootrom))
    mcu.logger = ConsoleLogger(_console_log_level(args))

    _load_image(args.image, mcu)

    gdb_server = GDBTCPServer(simulator, args.gdb_port)
    _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)

    def _on_byte(value: int) -> None:
        _buf_write(sys.stdout, value)

    mcu.uart[0].on_byte = _on_byte

    mcu.core.pc = 0x10000000
    simulator.execute()
    # gdb_server.close() as the cleanup hook: its accept thread is deliberately non-daemon (see
    # its own docstring), so a plain sys.exit() below would otherwise hang forever joining it.
    simulator.wait_for_shutdown(cleanup=gdb_server.close)


def _raw_repl_source(args: argparse.Namespace) -> "str | None":
    if args.command is not None:
        return args.command
    if args.module is not None:
        return f"import {args.module}"
    if args.filename is not None:
        with open(args.filename, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


def _make_expect_text_watcher(
    expect_text: "str | None", shutdown: "ShutdownRequest"
) -> "Callable[[bytes | bytearray], None]":
    """Returns an `on_data` callback for `StdioInteractiveRepl` that scans serial output for
    `expect_text` and requests a clean process exit once found - the `--expect-text`
    CI-test-harness hook shared by `micropython` and `kaluma`."""
    current_line = ""

    def _watch(value: bytes | bytearray) -> None:
        nonlocal current_line
        for byte in value:
            char = chr(byte)
            if char == "\n":
                if expect_text and expect_text in current_line:
                    print(f'Expected text found: "{expect_text}"')
                    print("TEST PASSED.")
                    # shutdown.request(), not os._exit(): this callback runs on a Simulator
                    # worker thread (threading.Timer), so it can't safely tear the process down
                    # itself - it just flags the thread driving wait_for_shutdown, which does the
                    # actual repl/GDB-server cleanup and sys.exit() from there.
                    shutdown.request(0)
                current_line = ""
            else:
                current_line += char

    return _watch


def _cmd_micropython(args: argparse.Namespace) -> None:
    image_name = retrieve(CIRCUITPYTHON if args.circuitpython else MICROPYTHON, args.image)
    if image_name is None:
        _logger.error("Could not find micropython image: %s", args.image)
        sys.exit(1)

    _logger.info("Loading uf2 image: %s", image_name)
    littlefs = (
        args.littlefs if not args.circuitpython and args.littlefs is not None and Path(args.littlefs).exists() else None
    )
    fat12 = args.fat12 if args.circuitpython and args.fat12 is not None and Path(args.fat12).exists() else None

    if littlefs is not None:
        _logger.info("Loading littlefs image: %s", littlefs)

    if fat12 is not None:
        _logger.info("Loading fat12 image: %s", fat12)

    try:
        device = MicroPythonDevice(
            image_name,
            littlefs=littlefs,
            fat12=fat12,
            circuitpython=args.circuitpython,
            bootrom_words=_resolve_bootrom_words(args.bootrom),
            log_level=_console_log_level(args),
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        sys.exit(1)

    # cleanup runs everything registered on it (in reverse order) whenever this block exits for
    # any reason - normal fall-through, an explicit sys.exit() below, or one raised from inside
    # wait_for_shutdown() - so each resource's teardown lives right next to where it's created,
    # instead of a separate hand-assembled "cleanup everything" function that has to be kept in
    # sync with whatever the rest of this function does or doesn't construct.
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(device.stop)

        gdb_server: GDBTCPServer | None = None
        if args.gdb:
            gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
            _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)
            # Its accept thread is deliberately non-daemon (see GDBTCPServer.close()'s own
            # docstring) - without this, any sys.exit() below would hang forever joining it.
            cleanup.callback(gdb_server.close)

        raw_repl_source = _raw_repl_source(args)
        if raw_repl_source is not None:
            # No timeout (unlike MicroPythonDevice's library default): matches this CLI's
            # existing philosophy elsewhere of running until done or Ctrl+C, not an arbitrary
            # deadline.
            try:
                device.start(timeout=None)
                stdout, stderr = device.exec(raw_repl_source, timeout=None)
            except KeyboardInterrupt:
                sys.exit(130)
            except (TimeoutError, RawReplError) as exc:
                _logger.error("%s", exc)
                sys.exit(1)
            _buf_write(sys.stdout, stdout)
            if stderr:
                _buf_write(sys.stderr, stderr)
            sys.exit(1 if stderr else 0)

        cdc = device.cdc
        shutdown = device.simulator.shutdown_request

        # Constructed (and its on_serial_data wired) before start() so nothing the device prints
        # while enumerating is dropped.
        repl = StdioInteractiveRepl(
            cdc, on_data=_make_expect_text_watcher(args.expect_text, shutdown), on_quit=shutdown.request
        )
        repl.start()
        cleanup.callback(repl.stop)

        device.start(timeout=None)
        if not args.circuitpython:
            # We send a newline so the user sees the MicroPython prompt
            cdc.send_serial_byte(ord("\r"))
            cdc.send_serial_byte(ord("\n"))
        else:
            cdc.send_serial_byte(3)

        device.simulator.wait_for_shutdown()


def _cmd_kaluma(args: argparse.Namespace) -> None:
    image_name = retrieve(KALUMA, args.image)
    if image_name is None:
        _logger.error("Could not find kaluma image: %s", args.image)
        sys.exit(1)

    _logger.info("Loading uf2 image: %s", image_name)
    littlefs = args.littlefs if args.littlefs is not None and Path(args.littlefs).exists() else None
    if littlefs is not None:
        _logger.info("Loading littlefs image: %s", littlefs)

    if args.filename is not None:
        _logger.info("Loading program: %s", args.filename)

    try:
        device = KalumaDevice(
            image_name,
            littlefs=littlefs,
            program=args.filename,
            bootrom_words=_resolve_bootrom_words(args.bootrom),
            log_level=_console_log_level(args),
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        sys.exit(1)

    with contextlib.ExitStack() as cleanup:
        cleanup.callback(device.stop)

        gdb_server: GDBTCPServer | None = None
        if args.gdb:
            gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
            _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)
            cleanup.callback(gdb_server.close)

        cdc = device.cdc
        shutdown = device.simulator.shutdown_request

        # Constructed (and its on_serial_data wired) before start() so nothing the device prints
        # while enumerating is dropped.
        repl = StdioInteractiveRepl(
            cdc, on_data=_make_expect_text_watcher(args.expect_text, shutdown), on_quit=shutdown.request
        )
        repl.start()
        cleanup.callback(repl.stop)

        device.start(timeout=None)
        # No nudge sent: Kaluma's own boot-time "Welcome to Kaluma" banner is racy (gone by the
        # time the USB-CDC connection is actually up, same as real hardware racing a host
        # terminal that isn't attached yet - Kaluma's own docs: "if you cannot see the prompt,
        # press Enter several times"), but a staged <script.js>'s own auto-run output isn't - it
        # arrives on its own a few real seconds after connecting, no nudge needed (unlike the
        # banner, confirmed empirically).

        device.simulator.wait_for_shutdown()


def _interpreter_label() -> str:
    impl = sys.implementation.name
    jit = getattr(sys, "_jit", None)
    if jit is not None and jit.is_enabled():
        impl += "+jit"
    return f"{impl} {'.'.join(str(part) for part in sys.version_info[:3])}"


def _bench_synthetic(instruction_count: int, block_size: int, log_level: LogLevel) -> None:
    rp2040 = RP2040()

    from rp2040py.device.bootrom import BOOTROM_B1

    rp2040.load_bootrom(BOOTROM_B1)
    rp2040.logger = ConsoleLogger(log_level)

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


def _bench_firmware(
    image: str,
    littlefs: str | None,
    expect_text: str | None,
    timeout: float,
    bootrom: str | None,
    log_level: LogLevel,
) -> None:
    # Uses Simulator (not a bare RP2040) so the clock actually advances: real firmware relies on
    # timer-based busy-waits during boot (e.g. hardware_timer's timer_busy_wait_until()), and those
    # spin forever if TIMERAWL/TIMERAWH never move - core.execute_instruction() alone does not tick
    # the clock, only Simulator.execute() (and this hand-rolled equivalent below) does.
    simulator = Simulator()
    rp2040 = simulator.rp2040
    clock = simulator.clock

    rp2040.load_bootrom(_resolve_bootrom_words(bootrom))
    rp2040.logger = ConsoleLogger(log_level)

    _load_image(image, rp2040)

    if littlefs:
        try:
            load_micropython_flash_image(littlefs, rp2040)
        except ValueError as exc:
            _logger.error("%s", exc)
            sys.exit(1)

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
    log_level = _console_log_level(args)
    if args.image:
        _bench_firmware(args.image, args.littlefs, args.expect_text, args.timeout, args.bootrom, log_level)
    else:
        _bench_synthetic(args.instructions, args.block_size, log_level)


_TARGET_FS_LAYOUTS = {
    "micropython": (MICROPYTHON_FS_BLOCKSIZE, MICROPYTHON_FS_BLOCKCOUNT),
    "circuitpython": (CIRCUITPYTHON_FS_BLOCKSIZE, CIRCUITPYTHON_FS_BLOCKCOUNT),
    "kaluma": (KALUMA_FS_BLOCKSIZE, KALUMA_FS_BLOCKCOUNT),
}


def _cmd_mklittlefs(args: argparse.Namespace) -> None:
    if args.target is not None and (args.block_size is not None or args.block_count is not None):
        _logger.error("--target is mutually exclusive with --block-size/--block-count")
        sys.exit(1)

    if args.target is not None:
        block_size, block_count = _TARGET_FS_LAYOUTS[args.target]
    else:
        block_size = args.block_size if args.block_size is not None else MICROPYTHON_FS_BLOCKSIZE
        block_count = args.block_count if args.block_count is not None else MICROPYTHON_FS_BLOCKCOUNT

    try:
        build_littlefs_image(
            args.output,
            args.files,
            block_size=block_size,
            block_count=block_count,
            disk_version=args.disk_version,
            main=args.main,
            force=args.force,
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        sys.exit(1)
    _logger.info("Wrote littlefs image: %s", args.output)

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


_IMAGE_TAG_HELP = "version tag, local file path, or omitted to download the default"
_IMAGE_PATH_HELP = "local .hex/.uf2 image path"
_BOOTROM_HELP = "b0/b1/b2 version tag, local .elf/.bin path, or omitted for the default (B1, bundled - no download)"
_EXPECT_TEXT_HELP = "stop once this text appears on the device's serial console"
_LITTLEFS_HELP = "optional littlefs.img to load"


def _shared_arg_parser(*names: str) -> argparse.ArgumentParser:
    """Builds an `add_help=False` parent parser carrying just the named shared arguments, for
    `subparsers.add_parser(..., parents=[...])` - argparse's own mechanism for reusing identical
    argument definitions across subcommands instead of repeating `add_argument(...)` calls with
    the same flags/kwargs in each. Each subcommand still opts in explicitly (via which parents it
    passes), so the fact that e.g. `run` has no `--gdb` toggle (it always starts one) stays
    visible at the call site rather than hidden behind a single shared "boot args" bundle."""
    definitions: dict[str, dict[str, Any]] = {
        "gdb-port": {"type": int, "default": 3333},
        "gdb": {"action": "store_true"},
        "bootrom": {"help": _BOOTROM_HELP},
        "expect-text": {"help": _EXPECT_TEXT_HELP},
        "littlefs": {"type": Path, "help": _LITTLEFS_HELP},
    }
    shared = argparse.ArgumentParser(add_help=False)
    for name in names:
        shared.add_argument(f"--{name}", **definitions[name])
    return shared


def main(argv: "list[str] | None" = None) -> None:
    prolog, _ = __doc__.split("\n", 1)
    parser = argparse.ArgumentParser(prog="rp2040py", description=prolog)
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {version('rp2040py')}")
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVEL_CHOICES,
        default=None,
        help=(
            "verbosity for both this CLI's own progress/error messages and the emulator's "
            "internal component logger (memory/peripheral access warnings, unimplemented "
            "opcodes, ...) - unset keeps each at its existing default (progress messages at "
            "info, component logger at error)"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        parents=[_shared_arg_parser("gdb-port", "bootrom")],
        help="run a native .hex/.uf2 image with a GDB server",
    )
    run_parser.add_argument("--image", default="hello_uart.hex", help=f"{_IMAGE_PATH_HELP} (default: %(default)s)")
    run_parser.set_defaults(func=_cmd_run)

    mp_parser = subparsers.add_parser(
        "micropython",
        parents=[_shared_arg_parser("gdb-port", "gdb", "bootrom", "expect-text", "littlefs")],
        help="run a MicroPython/CircuitPython UF2 image",
    )
    mp_parser.add_argument("--image", help=_IMAGE_TAG_HELP)
    mp_parser.add_argument("--circuitpython", action="store_true")
    mp_parser.add_argument("--fat12", type=Path, help="optional fat12.img to load")
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
    mp_source_group.add_argument(
        "filename", nargs="?", type=Path, help="run the given local script file on the device, then exit"
    )
    mp_parser.set_defaults(func=_cmd_micropython)

    kaluma_parser = subparsers.add_parser(
        "kaluma",
        parents=[_shared_arg_parser("gdb-port", "gdb", "bootrom", "expect-text", "littlefs")],
        help="run a Kaluma UF2 image (interactive REPL only)",
    )
    kaluma_parser.add_argument("--image", help=_IMAGE_TAG_HELP)
    kaluma_parser.add_argument(
        "filename", nargs="?", help="local .js file to stage as the auto-run user program, then boot"
    )
    kaluma_parser.set_defaults(func=_cmd_kaluma)

    bench_parser = subparsers.add_parser(
        "bench",
        parents=[_shared_arg_parser("bootrom", "expect-text", "littlefs")],
        help="benchmark instruction-dispatch throughput",
    )
    bench_parser.add_argument("--instructions", type=int, default=5_000_000, help="synthetic mode: instruction count")
    bench_parser.add_argument("--block-size", type=int, default=1000, help="synthetic mode: instructions per block")
    bench_parser.add_argument("--image", help=f"firmware mode: {_IMAGE_PATH_HELP}")
    bench_parser.add_argument("--timeout", type=float, default=60.0, help="firmware mode: seconds before giving up")
    bench_parser.set_defaults(func=_cmd_bench)

    if _HAS_LITTLEFS:
        mklittlefs_parser = subparsers.add_parser(
            "mklittlefs", help="build a littlefs image for `micropython`'s filesystem support"
        )
        mklittlefs_parser.add_argument(
            "files", nargs="*", type=Path, help="source files to add, keeping their own basename"
        )
        mklittlefs_parser.add_argument(
            "--main", metavar="<basename>", help="write the `files` entry with this basename as main.py"
        )
        mklittlefs_parser.add_argument("-o", "--output", type=Path, default="littlefs.img", help="output image path")
        mklittlefs_parser.add_argument(
            "-f", "--force", action="store_true", help="overwrite `--output` if it already exists"
        )
        mklittlefs_parser.add_argument(
            "--target",
            choices=tuple(_TARGET_FS_LAYOUTS.keys()),
            default=None,
            help=(
                "preset --block-size/--block-count for a known firmware's filesystem layout - "
                "mutually exclusive with passing them explicitly "
                f"(defaults to micropython's {MICROPYTHON_FS_BLOCKSIZE}/{MICROPYTHON_FS_BLOCKCOUNT} "
                "if neither --target nor --block-size/--block-count is given)"
            ),
        )
        mklittlefs_parser.add_argument("--block-size", type=int, default=None)
        mklittlefs_parser.add_argument("--block-count", type=int, default=None)
        mklittlefs_parser.add_argument(
            "--disk-version",
            type=str,
            choices=LITTLEFS_DISK_VERSIONS.keys(),
            default=LITTLEFS_DEFAULT_DISK_VERSION,
            help=f"(defaults to {LITTLEFS_DEFAULT_DISK_VERSION})",
        )
        mklittlefs_parser.set_defaults(func=_cmd_mklittlefs)

    args = parser.parse_args(argv)

    # format="%(message)s": without a handler configured anywhere, stdlib logging's own "handler
    # of last resort" only ever emits WARNING+ (to stderr) - INFO-level calls like
    # firmware_retrieve.py's "Found local image"/"Download: ..." progress messages would otherwise
    # be silently dropped, a real UX regression from when they were plain print() calls. The
    # plain-message format (no timestamp/level/logger-name prefix) keeps CLI output looking like
    # the print() calls it replaced rather than diagnostic log noise - this is the application
    # entry point, the conventional place for basicConfig(), not a library module like
    # firmware_retrieve.py itself.
    #
    # Not basicConfig(..., force=True): force=True tears out *every* handler already on the root
    # logger, including ones this process didn't install itself - e.g. pytest's caplog fixture
    # attaches its own handler before each test, and this project's own tests call cli.main()
    # repeatedly, so force=True would silently blank caplog's captures on the second call onward.
    # basicConfig() without force is a no-op once a handler exists (from a prior main() call, or
    # caplog's), which is what we want for the handler/format - but info (not warning) needs to
    # keep being the *default* level on every call, not just the first, so --log-level still takes
    # effect on repeated calls: setLevel() does that without touching any handler.
    logging.basicConfig(format="%(message)s")
    logging.getLogger().setLevel((args.log_level or "info").upper())
    args.func(args)


if __name__ == "__main__":
    main()
