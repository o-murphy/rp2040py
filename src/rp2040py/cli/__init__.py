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
  missing firmware is downloaded automatically (see ``rp2040py.utils.firmware_retrieve``).
  ``--tcp-port`` serves the console over a plain TCP socket instead of this process's own stdio -
  for tools expecting a serial port that can't get one (e.g. ``mpremote``, via pySerial's built-in
  ``socket://host:port`` URL support - see ``rp2040py.cli.socket_repl``); mutually exclusive with
  ``-c``/``-m``/``<filename>``.
- ``kaluma``: Kaluma (https://kaluma.io/) UF2 runner with a USB CDC console, interactive REPL
  only - Kaluma has no raw-REPL-equivalent protocol, so unlike ``micropython`` there's no
  ``-c``/``-m``/``<filename>`` exec mode. ``--image`` accepts a version tag, a local file path, or
  is omitted entirely to download the default (see ``rp2040py.utils.firmware_retrieve``). An
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
- ``mpremote``: transparent proxy to the real ``mpremote`` CLI (every argument after ``mpremote``
  is forwarded as-is) that patches around a real upstream bug before handing off - ``mpremote``'s
  bare interactive REPL (``console.py``'s ``waitchar()``) unconditionally reads
  ``pyb_serial.fd``, which pySerial's ``socket://`` backend never defines (only its POSIX serial
  backend does - see ``docs/mpremote.md``), so ``mpremote repl``/``mpremote connect
  socket://host:port`` (no subcommand) crashes with an ``AttributeError`` there. See
  https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170.
"""

import argparse
import asyncio
import contextlib
import importlib.util
import logging
import os
import re
import signal
import struct
import sys
import time
from collections.abc import Callable
from importlib.metadata import version
from os import PathLike
from pathlib import Path
from typing import Any

import argcomplete

from rp2040py.boards import BOARDS, build_rp2040
from rp2040py.cli.intelhex import load_hex
from rp2040py.cli.mklittlefs import LITTLEFS_DEFAULT_DISK_VERSION, LITTLEFS_DISK_VERSIONS, build_littlefs_image
from rp2040py.cli.pty_repl import PtyInteractiveRepl
from rp2040py.cli.pty_repl import pty as _pty_module
from rp2040py.cli.socket_repl import SocketInteractiveRepl
from rp2040py.cli.stdio_repl import StdioInteractiveRepl
from rp2040py.cli.stdio_repl import buf_write as _buf_write
from rp2040py.device.base_device import BaseDevice
from rp2040py.device.kaluma_device import KalumaDevice
from rp2040py.device.load_flash import (
    CIRCUITPYTHON_FS_BLOCKSIZE,
    KALUMA_FS_BLOCKSIZE,
    MICROPYTHON_FS_BLOCKSIZE,
    load_micropython_flash_image,
    load_uf2,
)
from rp2040py.device.mp_device import MicroPythonDevice
from rp2040py.device.raw_repl import RawReplError
from rp2040py.device.repl_runner import BaseReplRunner
from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import ShutdownRequest, Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.assembler import opcode_adds2, opcode_subs2
from rp2040py.utils.firmware_retrieve import BOOTROM, CIRCUITPYTHON, KALUMA, MICROPYTHON, flash_layout, retrieve
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


def _load_image(image_name: PathLike, rp2040: RP2040) -> None:
    extension = Path(image_name).suffix
    if extension == ".hex":
        _logger.info("Loading hex image: %s", image_name)
        with open(image_name) as f:
            load_hex(f.read(), rp2040.flash, 0x10000000)
    elif extension == ".uf2":
        _logger.info("Loading uf2 image: %s", image_name)
        load_uf2(image_name, rp2040)
    else:
        _logger.error("Unsupported file type: %s", extension)
        sys.exit(1)


def _mk_dump_fs_callback(filename: PathLike | None, device: BaseDevice) -> Callable[[], None]:
    def dump_fs_callback() -> None:
        if filename is not None:
            _logger.info("Dumping flash image to: %s", filename)
            device.dump_flash_image(filename)

    return dump_fs_callback


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

    extension = path.suffix
    if extension == ".elf":
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


def _maybe_exit_after_fetch(
    args: argparse.Namespace, *, image_path: "PathLike | None" = None, bootrom_source: "str | None" = None
) -> None:
    """If `--fetch-fw-only` was given: makes sure `bootrom_source` (a version tag, if one was
    given via `--bootrom`) is downloaded and cached too - `image_path`, if given, is assumed
    already resolved/cached by the caller (mirroring how `--image` itself is resolved before this
    runs) - logs what's cached, then exits 0 without touching the simulator at all. A no-op
    (returns normally) when `--fetch-fw-only` wasn't passed."""
    if not args.fetch_fw_only:
        return
    if bootrom_source is not None:
        _resolve_bootrom_words(bootrom_source)  # downloads/caches as a side effect; words unused
    if image_path is not None:
        _logger.info("Fetched firmware image: %s", image_path)
    _logger.info("--fetch-fw-only: firmware cached, exiting without starting the simulator")
    sys.exit(0)


async def _run_async(args: argparse.Namespace) -> int:
    """`run`'s real body - a coroutine driven by `_cmd_run()`'s own `asyncio.run()` call, per
    docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape": this coroutine's own task *is* the
    engine room (`bind_loop()` registers it), on the process's actual main thread - not a
    separate thread `wait_for_shutdown()` used to poll from the outside. First component
    migrated (see that doc's "Phased plan") - `micropython`/`kaluma` still use the older
    `Simulator.start_execution()`/`wait_for_shutdown()` model via `BaseDevice`, unchanged for now.
    """
    simulator = Simulator(rp2040=build_rp2040(args.board))
    simulator.bind_loop()
    mcu = simulator.rp2040

    mcu.load_bootrom(_resolve_bootrom_words(args.bootrom))
    mcu.logger = ConsoleLogger(_console_log_level(args))

    _load_image(args.image, mcu)

    gdb_server = GDBTCPServer(simulator, args.gdb_port)
    await gdb_server.start()
    _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)

    def _on_byte(value: int) -> None:
        _buf_write(sys.stdout, value)

    mcu.uart[0].on_byte = _on_byte
    mcu.core.pc = 0x10000000

    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    exit_code = 0

    def _request_stop(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        stop_requested.set()

    # Real signal handling, not KeyboardInterrupt/the old wait_for_shutdown() poll loop - viable
    # now that this coroutine runs on the actual process main thread (add_signal_handler() is a
    # CPython main-thread-only restriction - see docs/MAIN_THREAD_ASYNCIO_BACKLOG.md). Not
    # available on Windows (ProactorEventLoop) - falls back to asyncio.run()'s own
    # KeyboardInterrupt propagation for Ctrl+C there (SIGTERM has no graceful-shutdown path on
    # Windows either way, matching this command's behavior before this migration).
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, _request_stop, 130)
        loop.add_signal_handler(signal.SIGTERM, _request_stop, 143)

    execute_task = loop.create_task(simulator.execute())
    stop_task = loop.create_task(stop_requested.wait())
    try:
        done, _pending = await asyncio.wait({execute_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            # A signal fired first - ask execute() to wind down gracefully (finish its
            # in-flight batch) rather than just dropping the task.
            simulator.stop()
            await execute_task
        else:
            # execute() ended on its own (RP2040.on_break, e.g. a bkpt-based firmware
            # convention) before any signal did - nothing left to wait for.
            stop_task.cancel()
    finally:
        await gdb_server.close()

    return exit_code


def _cmd_run(args: argparse.Namespace) -> None:
    _maybe_exit_after_fetch(args, bootrom_source=args.bootrom)
    try:
        exit_code = asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        sys.exit(130)
    if exit_code:
        sys.exit(exit_code)


def _raw_repl_source(args: argparse.Namespace) -> "str | None":
    if args.command is not None:
        return args.command
    if args.module is not None:
        return f"import {args.module}"
    if args.filename is not None:
        with open(args.filename, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


def _regex_matcher(pattern: "re.Pattern[str]") -> "Callable[[str], bool]":
    return lambda line: pattern.search(line) is not None


def _substring_matcher(substring: str) -> "Callable[[str], bool]":
    return lambda line: substring in line


class _ExpectTextTracker:
    """Tracks however many `--expect-text` patterns were given (plain substrings by default, or
    Python `re` patterns via `--expect-regex`) across arbitrarily many lines of device output.
    `found` flips to True once every pattern has matched *some* line - not necessarily the same
    one, and not necessarily in the order given, mirroring how a human skimming a log for several
    expected messages would judge "did they all show up" (deliberately not a single-line or
    sliding-window cross-line match - see docs/mpremote.md's discussion of why). Shared by the
    `micropython`/`kaluma` console watcher below and `bench`'s own firmware-mode loop, since both
    subcommands expose the same `--expect-text`/`--expect-regex` shared arguments."""

    def __init__(self, expect_text: "list[str] | None", expect_regex: bool) -> None:
        patterns = expect_text or []
        if expect_regex:
            compiled = [re.compile(pattern) for pattern in patterns]
            self._matches: list[Callable[[str], bool]] = [_regex_matcher(c) for c in compiled]
        else:
            self._matches = [_substring_matcher(p) for p in patterns]
        self._pending = set(range(len(self._matches)))

    @property
    def active(self) -> bool:
        return bool(self._matches)

    @property
    def found(self) -> bool:
        return self.active and not self._pending

    def feed_line(self, line: str) -> None:
        for index in list(self._pending):
            if self._matches[index](line):
                self._pending.discard(index)


def _make_expect_text_watcher(
    expect_text: "list[str] | None", expect_regex: bool, shutdown: "ShutdownRequest"
) -> "Callable[[bytes | bytearray], None]":
    """Returns an `on_data` callback for `StdioInteractiveRepl` that scans serial output against
    `_ExpectTextTracker` and requests a clean process exit once every `--expect-text` pattern has
    matched - the CI-test-harness hook shared by `micropython` and `kaluma`."""
    tracker = _ExpectTextTracker(expect_text, expect_regex)
    current_line = ""
    triggered = False

    def _watch(value: bytes | bytearray) -> None:
        nonlocal current_line, triggered
        for byte in value:
            char = chr(byte)
            if char == "\n":
                tracker.feed_line(current_line)
                if not triggered and tracker.found:
                    triggered = True
                    print(f"Expected text found: {expect_text!r}")
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


async def _start_console_repl(
    args: argparse.Namespace, cdc: USBCDC, simulator: Simulator, on_data: "Callable[[bytes | bytearray], None]"
) -> BaseReplRunner:
    """Starts (and returns, already `start()`ed) whichever console bridge `--tcp-port`/`--pty`
    selects: the normal interactive `StdioInteractiveRepl` (neither given), a headless
    `SocketInteractiveRepl` listening on `--tcp-port` (see `cli/socket_repl.py`), or a headless
    `PtyInteractiveRepl` (see `cli/pty_repl.py`) - both for driving the device from tools like
    `mpremote` that expect a serial port but can't get one, `--pty` specifically for the ones that
    additionally need a *real* one (see its own module docstring for why `--tcp-port` alone isn't
    always enough). Caller is responsible for registering `.stop` with its own cleanup - this only
    starts it."""
    if args.tcp_port is not None:
        socket_repl = SocketInteractiveRepl(
            cdc, simulator, on_quit=simulator.shutdown_request.request, port=args.tcp_port, on_data=on_data
        )
        await socket_repl.start()
        _logger.info(
            "TCP socket REPL listening on 127.0.0.1:%d - e.g. `mpremote connect socket://127.0.0.1:%d`",
            socket_repl.port,
            socket_repl.port,
        )
        return socket_repl

    if args.pty:
        pty_repl = PtyInteractiveRepl(cdc, simulator, on_quit=simulator.shutdown_request.request, on_data=on_data)
        await pty_repl.start()
        _logger.info("PTY REPL listening on %s - e.g. `mpremote connect %s`", pty_repl.slave_path, pty_repl.slave_path)
        return pty_repl

    stdio_repl = StdioInteractiveRepl(cdc, simulator, on_data=on_data, on_quit=simulator.shutdown_request.request)
    await stdio_repl.start()
    return stdio_repl


def _validate_console_mode(args: argparse.Namespace) -> None:
    """Shared `--tcp-port`/`--pty` validation for `micropython`/`kaluma`: mutually exclusive with
    each other (each replaces the console with a different transport - only one can), and `--pty`
    needs actual POSIX pty support (`cli/pty_repl.py`'s own `pty is None` gate) to mean anything."""
    if args.tcp_port is not None and args.pty:
        _logger.error("--tcp-port and --pty are mutually exclusive")
        sys.exit(1)
    if args.pty and _pty_module is None:
        _logger.error("--pty is not supported on this platform (no POSIX pty support)")
        sys.exit(1)


async def _await_shutdown(simulator: Simulator) -> "int | None":
    """Async equivalent of `Simulator.wait_for_shutdown()`'s poll loop, for a caller already
    running on `simulator`'s own loop (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape").
    Real `SIGINT`/`SIGTERM` handling via `loop.add_signal_handler()` - falls back to the caller's
    own `except KeyboardInterrupt` (see `_cmd_micropython`/`_cmd_kaluma`) on platforms without it.
    Returns the process exit code to `sys.exit()` explicitly with, or `None` if nothing ever
    requested a shutdown (e.g. natural completion) - matching `wait_for_shutdown()`'s own old
    distinction between "explicitly exit with this code" and "just let the process end normally",
    which a plain `int` can't represent on its own (0 is a legitimate explicit exit code too)."""
    loop = asyncio.get_running_loop()
    shutdown_request = simulator.shutdown_request

    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, shutdown_request.request, 130)
        loop.add_signal_handler(signal.SIGTERM, shutdown_request.request, 143)

    while simulator.executing:
        if shutdown_request.event.is_set():
            break
        await asyncio.sleep(0.1)
    return shutdown_request.code if shutdown_request.event.is_set() else None


async def _micropython_async(args: argparse.Namespace) -> "int | None":
    _validate_console_mode(args)
    # -c/-m/<filename> ("exec mode") runs one device.exec() call and exits based on its own
    # stdout/stderr (see below) - it never reaches the interactive/--tcp-port/--pty console loop
    # that either replaces, or that --expect-text's on_data watcher is wired into, so combining any
    # of them with exec mode wouldn't do anything (previously accepted and silently ignored for
    # --expect-text, confirmed - not what a caller passing both would reasonably expect).
    if args.command is not None or args.module is not None or args.filename is not None:
        if args.tcp_port is not None:
            _logger.error("--tcp-port cannot be combined with -c/-m/<filename>")
            return 1
        if args.pty:
            _logger.error("--pty cannot be combined with -c/-m/<filename>")
            return 1
        if args.expect_text is not None:
            _logger.error("--expect-text cannot be combined with -c/-m/<filename>")
            return 1

    image_name = retrieve(CIRCUITPYTHON if args.circuitpython else MICROPYTHON, args.image, board=args.board)
    if image_name is None:
        _logger.error("Could not find micropython image: %s", args.image)
        return 1

    _logger.info("Loading uf2 image: %s", image_name)
    _maybe_exit_after_fetch(args, image_path=image_name, bootrom_source=args.bootrom)
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
            board=args.board,
            littlefs=littlefs,
            fat12=fat12,
            circuitpython=args.circuitpython,
            bootrom_words=_resolve_bootrom_words(args.bootrom),
            log_level=_console_log_level(args),
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        return 1

    # cleanup runs everything registered on it (in reverse order) whenever this block exits for
    # any reason - normal fall-through, an early `return` below, or an exception - so each
    # resource's teardown lives right next to where it's created, instead of a separate
    # hand-assembled "cleanup everything" function that has to be kept in sync with whatever the
    # rest of this function does or doesn't construct. `Async`ExitStack (not plain ExitStack):
    # `repl.stop` is itself `async def` now (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md) - the other
    # callbacks registered here are still plain sync callables, which `.callback()` handles fine
    # alongside `.push_async_callback()`.
    async with contextlib.AsyncExitStack() as cleanup:
        cleanup.callback(device.stop)

        cleanup.callback(_mk_dump_fs_callback(args.dump_fs, device))

        gdb_server: GDBTCPServer | None = None
        if args.gdb:
            gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
            await gdb_server.start()
            _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)
            cleanup.push_async_callback(gdb_server.close)

        raw_repl_source = _raw_repl_source(args)
        if raw_repl_source is not None:
            # No timeout (unlike MicroPythonDevice's library default): matches this CLI's
            # existing philosophy elsewhere of running until done or Ctrl+C, not an arbitrary
            # deadline. KeyboardInterrupt isn't caught here - it propagates out of asyncio.run()
            # to _cmd_micropython()'s own try/except (no signal handler is registered for this
            # exec-only mode, so Ctrl+C keeps its default Python disposition).
            try:
                await device.astart(timeout=None)
                stdout, stderr = await device.aexec(raw_repl_source, timeout=None)
            except (TimeoutError, RawReplError) as exc:
                _logger.error("%s", exc)
                return 1
            _buf_write(sys.stdout, stdout)
            if stderr:
                _buf_write(sys.stderr, stderr)
            return 1 if stderr else 0

        cdc = device.cdc
        shutdown = device.simulator.shutdown_request

        # Constructed (and its on_serial_data wired) before start() so nothing the device prints
        # while enumerating is dropped.
        repl = await _start_console_repl(
            args, cdc, device.simulator, _make_expect_text_watcher(args.expect_text, args.expect_regex, shutdown)
        )
        cleanup.push_async_callback(repl.stop)

        await device.astart(timeout=None)
        if not args.circuitpython:
            # We send a newline so the user sees the MicroPython prompt
            cdc.send_serial_byte(ord("\r"))
            cdc.send_serial_byte(ord("\n"))
        else:
            cdc.send_serial_byte(3)

        return await _await_shutdown(device.simulator)


def _cmd_micropython(args: argparse.Namespace) -> None:
    try:
        exit_code = asyncio.run(_micropython_async(args))
    except KeyboardInterrupt:
        sys.exit(130)
    if exit_code is not None:
        sys.exit(exit_code)


async def _kaluma_async(args: argparse.Namespace) -> "int | None":
    _validate_console_mode(args)
    image_name = retrieve(KALUMA, args.image, board=args.board)
    if image_name is None:
        _logger.error("Could not find kaluma image: %s", args.image)
        return 1

    _logger.info("Loading uf2 image: %s", image_name)
    _maybe_exit_after_fetch(args, image_path=image_name, bootrom_source=args.bootrom)
    littlefs = args.littlefs if args.littlefs is not None and Path(args.littlefs).exists() else None
    if littlefs is not None:
        _logger.info("Loading littlefs image: %s", littlefs)

    if args.filename is not None:
        _logger.info("Loading program: %s", args.filename)

    try:
        device = KalumaDevice(
            image_name,
            board=args.board,
            littlefs=littlefs,
            program=args.filename,
            bootrom_words=_resolve_bootrom_words(args.bootrom),
            log_level=_console_log_level(args),
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        return 1

    async with contextlib.AsyncExitStack() as cleanup:
        cleanup.callback(device.stop)

        cleanup.callback(_mk_dump_fs_callback(args.dump_fs, device))

        gdb_server: GDBTCPServer | None = None
        if args.gdb:
            gdb_server = GDBTCPServer(device.simulator, args.gdb_port)
            await gdb_server.start()
            _logger.info("RP2040 GDB Server ready! Listening on port %d", gdb_server.port)
            cleanup.push_async_callback(gdb_server.close)

        cdc = device.cdc
        shutdown = device.simulator.shutdown_request

        # Constructed (and its on_serial_data wired) before start() so nothing the device prints
        # while enumerating is dropped.
        repl = await _start_console_repl(
            args, cdc, device.simulator, _make_expect_text_watcher(args.expect_text, args.expect_regex, shutdown)
        )
        cleanup.push_async_callback(repl.stop)

        await device.astart(timeout=None)
        # No nudge sent: Kaluma's own boot-time "Welcome to Kaluma" banner is racy (gone by the
        # time the USB-CDC connection is actually up, same as real hardware racing a host
        # terminal that isn't attached yet - Kaluma's own docs: "if you cannot see the prompt,
        # press Enter several times"), but a staged <script.js>'s own auto-run output isn't - it
        # arrives on its own a few real seconds after connecting, no nudge needed (unlike the
        # banner, confirmed empirically).

        return await _await_shutdown(device.simulator)


def _cmd_kaluma(args: argparse.Namespace) -> None:
    try:
        exit_code = asyncio.run(_kaluma_async(args))
    except KeyboardInterrupt:
        sys.exit(130)
    if exit_code is not None:
        sys.exit(exit_code)


def _interpreter_label() -> str:
    impl = sys.implementation.name
    jit = getattr(sys, "_jit", None)
    if jit is not None and jit.is_enabled():
        impl += "+jit"
    return f"{impl} {'.'.join(str(part) for part in sys.version_info[:3])}"


def _bench_synthetic(instruction_count: int, block_size: int, board: str, log_level: LogLevel) -> None:
    rp2040 = build_rp2040(board)

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
    image: PathLike,
    littlefs: PathLike | None,
    expect_text: "list[str] | None",
    expect_regex: bool,
    timeout: float,
    bootrom: str | None,
    board: str,
    log_level: LogLevel,
) -> None:
    # Uses Simulator (not a bare RP2040) so the clock actually advances: real firmware relies on
    # timer-based busy-waits during boot (e.g. hardware_timer's timer_busy_wait_until()), and those
    # spin forever if TIMERAWL/TIMERAWH never move - core.execute_instruction() alone does not tick
    # the clock, only Simulator.execute() (and this hand-rolled equivalent below) does.
    simulator = Simulator(rp2040=build_rp2040(board))
    rp2040 = simulator.rp2040
    clock = simulator.clock

    rp2040.load_bootrom(_resolve_bootrom_words(bootrom))
    rp2040.logger = ConsoleLogger(log_level)

    _load_image(image, rp2040)

    if littlefs:
        try:
            load_micropython_flash_image(littlefs, rp2040, board)
        except ValueError as exc:
            _logger.error("%s", exc)
            sys.exit(1)

    current_line = ""
    tracker = _ExpectTextTracker(expect_text, expect_regex)

    cdc = USBCDC(rp2040.usb_ctrl)

    def _on_device_connected() -> None:
        # nudge MicroPython to print its prompt, same as the micropython subcommand
        cdc.send_serial_byte(ord("\r"))
        cdc.send_serial_byte(ord("\n"))

    cdc.on_device_connected = _on_device_connected

    def _on_serial_data(value: bytes | bytearray) -> None:
        nonlocal current_line
        for byte in value:
            char = chr(byte)
            if char == "\n":
                tracker.feed_line(current_line)
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
    while not tracker.found and (time.perf_counter() - start) < timeout:
        for _ in range(step_batch):
            if rp2040.core.waiting:
                clock.tick(clock.nanos_to_next_alarm)
            else:
                cycles = rp2040.core.execute_instruction()
                clock.tick(cycles * cycle_nanos)
        executed += step_batch
    elapsed = time.perf_counter() - start

    status = "found expected text" if tracker.found else ("timed out" if expect_text else "step budget reached")
    print(
        f"{status}: executed {executed:,} instructions in {elapsed:.2f}s -> {executed / elapsed:,.0f} instructions/sec"
    )
    if expect_text and not tracker.found:
        sys.exit(1)


def _cmd_bench(args: argparse.Namespace) -> None:
    log_level = _console_log_level(args)
    # `--image` in bench mode is always a local path (never downloaded - see _IMAGE_PATH_HELP),
    # so only `--bootrom` (if a version tag) is ever fetched here, in either synthetic or firmware
    # mode.
    _maybe_exit_after_fetch(args, bootrom_source=args.bootrom)
    if args.image:
        _bench_firmware(
            args.image,
            args.littlefs,
            args.expect_text,
            args.expect_regex,
            args.timeout,
            args.bootrom,
            args.board,
            log_level,
        )
    else:
        _bench_synthetic(args.instructions, args.block_size, args.board, log_level)


def _patch_mpremote_console_waitchar() -> None:
    """Monkeypatches ``mpremote.console.ConsolePosix.waitchar`` so it tolerates a serial object
    with no ``.fd`` - the exact crash filed at
    https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170. Upstream's
    own ``waitchar()`` does ``select.select([self.infd, pyb_serial.fd], [], [])``, assuming every
    connected pySerial object exposes a raw POSIX file descriptor; true for pySerial's POSIX
    serial backend, but pySerial's ``socket://`` URL handler
    (``serial.urlhandler.protocol_socket.Serial``) wraps a plain ``socket.socket`` and never
    defines ``.fd``. That socket is select()-able on its own (sockets implement ``fileno()``,
    which is all ``select.select`` actually needs - ``.fd`` was never the only way), so falling
    back to the wrapped socket itself (pySerial's own private ``_socket`` attribute - there's no
    public accessor) fixes it without touching pySerial. ``ConsoleWindows`` (the other half of
    ``mpremote.console.Console``) never reads ``.fd`` in the first place - it polls
    ``pyb_serial.inWaiting()`` instead - so there's nothing to patch when ``select`` is unavailable
    (i.e. on Windows, where ``console.select`` is ``None``)."""
    from mpremote import console  # type: ignore[import-untyped]

    if console.select is None:
        return

    def _waitchar(self, pyb_serial):  # type: ignore[no-untyped-def]
        fd = getattr(pyb_serial, "fd", None)
        selectable = fd if fd is not None else getattr(pyb_serial, "_socket", pyb_serial)
        console.select.select([self.infd, selectable], [], [])

    console.ConsolePosix.waitchar = _waitchar


def _patch_serial_list_ports_missing_backend() -> None:
    """Stubs out ``serial.tools.list_ports`` when pySerial can't provide a real implementation for
    the current platform. ``serial.tools.list_ports_posix`` picks its backend off ``sys.platform``
    (checking for ``'linux'``/``'darwin'``/``'cygwin'``/...) and raises ``ImportError`` for anything
    it doesn't recognize - notably ``'android'`` (Termux) and, going by the same platform-string
    logic, almost certainly ``'ios'`` (Pythonista/PythonIDE) too, though that's unverified on an
    actual iOS device (see
    https://github.com/pyserial/pyserial/blob/master/serial/tools/list_ports_posix.py). ``mpremote``
    (``commands.py``) does ``import serial.tools.list_ports`` unconditionally at module load, so
    without this the whole ``rp2040py mpremote`` proxy fails before it even gets to argument
    parsing - even for e.g. ``connect /dev/pts/1 repl``, which names its port explicitly and never
    calls ``comports()`` to enumerate anything. Detecting the affected platforms up front
    (``sys.platform``, ``sys.getandroidapilevel``, env vars, ...) would be one option, but actually
    attempting the real import and reacting only if it fails is more robust: it self-heals if
    pySerial ever adds a real backend for one of them, and covers *any* platform pySerial's posix
    dispatch doesn't recognize without needing a name for it up front. The stub's ``comports()``
    returns no devices - correct for ``connect <explicit-port>`` (which never calls it) and merely
    unhelpful, not wrong, for ``mpremote devs``/`--list``-style auto-detection commands, which have
    no real device list to report on these platforms anyway."""
    try:
        import serial.tools.list_ports  # type: ignore[import-untyped] # noqa: F401

        return
    except ImportError:
        pass

    import types

    import serial.tools as _serial_tools  # type: ignore[import-untyped]

    stub = types.ModuleType("serial.tools.list_ports")
    stub.comports = lambda include_links=False: []  # type: ignore[attr-defined]
    stub.grep = lambda regexp, include_links=False: iter(())  # type: ignore[attr-defined]
    sys.modules["serial.tools.list_ports"] = stub
    _serial_tools.list_ports = stub  # type: ignore[attr-defined]


def _cmd_mpremote(args: argparse.Namespace) -> None:
    _patch_mpremote_console_waitchar()
    _patch_serial_list_ports_missing_backend()

    from mpremote.main import main as _mpremote_main  # type: ignore[import-untyped]

    # mpremote's own main() reads sys.argv directly rather than taking an argv parameter.
    sys.argv = ["mpremote", *args.mpremote_args]
    sys.exit(_mpremote_main())


_TARGET_NAMES = ("micropython", "circuitpython", "kaluma")


def _target_fs_layout(target: str, board: str) -> "tuple[int, int]":
    """(block_size, block_count) for `--target`, board-aware for every family - see
    docs/records/0035-board-aware-fs-flash-offset.md for why this can't be a plain per-target
    constant (a board's real firmware footprint changes where/how big its filesystem region is;
    building a littlefs image sized for the wrong board silently corrupts the firmware it's loaded
    alongside)."""
    if target == "micropython":
        return MICROPYTHON_FS_BLOCKSIZE, flash_layout(MICROPYTHON, board)["fs_blockcount"]
    if target == "kaluma":
        return KALUMA_FS_BLOCKSIZE, flash_layout(KALUMA, board)["fs_blockcount"]
    return CIRCUITPYTHON_FS_BLOCKSIZE, flash_layout(CIRCUITPYTHON, board)["fs_blockcount"]


def _cmd_mklittlefs(args: argparse.Namespace) -> None:
    if args.target is not None and (args.block_size is not None or args.block_count is not None):
        _logger.error("--target is mutually exclusive with --block-size/--block-count")
        sys.exit(1)

    if args.target is not None:
        block_size, block_count = _target_fs_layout(args.target, args.board)
    else:
        block_size = args.block_size if args.block_size is not None else MICROPYTHON_FS_BLOCKSIZE
        block_count = (
            args.block_count if args.block_count is not None else _target_fs_layout("micropython", args.board)[1]
        )

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
        # os._exit(), not a normal return: under PyPy, littlefs-python's LittleFS/file C objects
        # can get finalized out of order during interpreter shutdown - even after an explicit
        # lfs.unmount() and gc.collect() - crashing the process with "lfs_file_sync: Assertion
        # `lfs_mlist_isopen(...)` failed" (SIGABRT) despite the image already having been written
        # correctly. Confirmed with `uv tool install --python pypy-3.10 rp2040py[fs]`; not
        # reproducible under CPython, hence gating this rather than doing it unconditionally - an
        # unconditional os._exit() here would also kill the whole process if _cmd_mklittlefs (or
        # main()) is ever called in-process rather than as the actual entry point, e.g. from a test
        # suite. The image on disk is already complete and correct at this point, so skipping the
        # rest of shutdown is safe for the *pypy CLI* case specifically - it doesn't help a
        # long-running PyPy program that calls build_littlefs_image() as a library and keeps
        # running afterwards, which is a real upstream littlefs-python/PyPy limitation, not
        # something rp2040py can fully paper over. Unrelated to StdioInteractiveRepl/terminal
        # state - no REPL is ever active in this command - so it needs no shutdown coordination.
        os._exit(0)


_IMAGE_TAG_HELP = "version tag, local file path, or omitted to download the default"
_IMAGE_PATH_HELP = "local .hex/.uf2 image path"
_BOARD_HELP = "which board's MCU/fixed extras to construct (default: %(default)s)"
_FETCH_FW_ONLY_HELP = (
    "download/cache the firmware image (and --bootrom, if a version tag) then exit, without "
    "starting the simulator - for pre-warming the local cache (e.g. before going offline, or in CI)"
)
_BOOTROM_HELP = "b0/b1/b2 version tag, local .elf/.bin path, or omitted for the default (B1, bundled - no download)"
_EXPECT_TEXT_HELP = (
    "stop once this text appears on the device's serial console (any line, not necessarily the "
    "same one) - repeatable: with --expect-text given more than once, every one of them must be "
    "found before stopping"
)
_EXPECT_REGEX_HELP = "treat each --expect-text value as a Python `re` pattern (re.search) instead of a plain substring"
_LITTLEFS_HELP = "optional littlefs.img to load"
_DUMP_FS_HELP = "path to save filesystem state on exit (can be the same as --littlefs for persistence)"
_TCP_PORT_HELP = (
    "serve the console over this TCP port instead of this process's own stdio (0 for an "
    "OS-assigned port - see the logged 'listening on' message) - for tools expecting a serial "
    "port that can't get one (e.g. mpremote via `connect socket://host:port`, pySerial's own "
    "built-in URL scheme); mutually exclusive with -c/-m/<filename> on `micropython` and with --pty"
)
_PTY_HELP = (
    "serve the console over a real pseudo-terminal (POSIX only) instead of this process's own "
    "stdio - like --tcp-port, but a *real* POSIX serial-like device path (see the logged "
    "'listening on' message), for tools that need one (e.g. mpremote's own interactive REPL, which "
    "does not work over --tcp-port's socket:// transport - see docs/mpremote.md); mutually "
    "exclusive with -c/-m/<filename> on `micropython` and with --tcp-port"
)


def _mk_file_suffixes_validator(*suffixes: str) -> Callable[[str], Path]:
    valid_suffixes = {s.lower() for s in suffixes}

    def validator(p: str) -> Path:
        path = Path(p)
        if path.suffix.lower() in valid_suffixes:
            return path
        raise argparse.ArgumentTypeError(f"File must have one of the following extensions: {', '.join(valid_suffixes)}")

    return validator


_LITTLEFS_SUFFIX_VALIDATOR = _mk_file_suffixes_validator(".img", ".bin", ".lfs")
_FAT_SUFIX_VALIDATOR = _mk_file_suffixes_validator(".img", ".bin", ".fat", ".fat12")


def _shared_arg_parser(*names: str) -> argparse.ArgumentParser:
    """Builds an `add_help=False` parent parser carrying just the named shared arguments, for
    `subparsers.add_parser(..., parents=[...])` - argparse's own mechanism for reusing identical
    argument definitions across subcommands instead of repeating `add_argument(...)` calls with
    the same flags/kwargs in each. Each subcommand still opts in explicitly (via which parents it
    passes), so the fact that e.g. `run` has no `--gdb` toggle (it always starts one) stays
    visible at the call site rather than hidden behind a single shared "boot args" bundle."""
    definitions: dict[str, dict[str, Any]] = {
        "board": {"choices": tuple(BOARDS), "default": "pico", "help": _BOARD_HELP},
        "fetch-fw-only": {"action": "store_true", "help": _FETCH_FW_ONLY_HELP},
        "gdb-port": {"type": int, "default": 3333},
        "gdb": {"action": "store_true"},
        "bootrom": {"help": _BOOTROM_HELP},
        "expect-text": {"action": "append", "help": _EXPECT_TEXT_HELP},
        "expect-regex": {"action": "store_true", "help": _EXPECT_REGEX_HELP},
        "littlefs": {
            "type": _LITTLEFS_SUFFIX_VALIDATOR,
            "help": _LITTLEFS_HELP,
        },
        "dump-fs": {"type": _LITTLEFS_SUFFIX_VALIDATOR, "help": _DUMP_FS_HELP},
        "tcp-port": {"type": int, "default": None, "help": _TCP_PORT_HELP},
        "pty": {"action": "store_true", "help": _PTY_HELP},
    }
    shared = argparse.ArgumentParser(add_help=False)
    for name in names:
        shared.add_argument(f"--{name}", **definitions[name])
    return shared


def _cmd_install_completion(args: argparse.Namespace) -> None:
    # We define the configuration file more reliably
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        rc_file = "~/.zshrc"
    elif "bash" in shell:
        rc_file = "~/.bashrc"
    else:
        # By default for Linux/macOS, zsh (in modern macOS) or bash are more commonly used
        rc_file = "~/.zshrc" if os.path.exists(os.path.expanduser("~/.zshrc")) else "~/.bashrc"

    expanded_path = os.path.expanduser(rc_file)
    completion_cmd = 'eval "$(register-python-argcomplete rp2040py)"'

    try:
        with open(expanded_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    # Check for the presence of a command more flexibly (without taking into account extra spaces)
    if completion_cmd not in content:
        with open(expanded_path, "a", encoding="utf-8") as f:
            f.write(f"\n# rp2040py auto-completion\n{completion_cmd}\n")
        print(f"Autocomplete successfully added to {rc_file}!")
        print(f"Run this command to apply changes now:\n   source {rc_file}")
    else:
        print(f"Autocomplete is already configured in {rc_file}.")

    sys.exit(0)


def main(argv: "list[str] | None" = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Bypassed around argparse entirely (rather than relying on the `mpremote` subparser's own
    # REMAINDER positional registered below): argparse.REMAINDER has a well-known quirk where it
    # fails to capture a *leading* option-looking token (e.g. `rp2040py mpremote --version` -
    # REMAINDER never sees `--version`, argparse reports it as unrecognized instead) - fine for
    # plain positional mpremote commands (`mpremote repl`, `mpremote connect ...`), but breaks
    # forwarding mpremote's own `-h`/`--help`/`--version`. This handles the common invocation
    # (`mpremote` as the very first argument) perfectly instead; the registered subparser remains
    # only as a fallback for the rare case of a global flag before it (e.g. `--log-level debug
    # mpremote ...`) and so `--help` still lists it as a subcommand.
    if raw_argv[:1] == ["mpremote"]:
        _cmd_mpremote(argparse.Namespace(mpremote_args=raw_argv[1:]))
        return

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
        parents=[_shared_arg_parser("board", "gdb-port", "bootrom", "fetch-fw-only")],
        help="run a native .hex/.uf2 image with a GDB server",
    )
    run_parser.add_argument("--image", default="hello_uart.hex", help=f"{_IMAGE_PATH_HELP} (default: %(default)s)")
    run_parser.set_defaults(func=_cmd_run)

    mp_parser = subparsers.add_parser(
        "micropython",
        parents=[
            _shared_arg_parser(
                "board",
                "gdb-port",
                "gdb",
                "bootrom",
                "expect-text",
                "expect-regex",
                "littlefs",
                "dump-fs",
                "tcp-port",
                "pty",
                "fetch-fw-only",
            )
        ],
        help="run a MicroPython/CircuitPython UF2 image",
    )
    mp_parser.add_argument("--image", help=_IMAGE_TAG_HELP)
    mp_parser.add_argument("--circuitpython", action="store_true")
    mp_parser.add_argument("--fat12", type=_FAT_SUFIX_VALIDATOR, help="optional fat12.img to load")
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
        "filename",
        nargs="?",
        type=_mk_file_suffixes_validator(".py"),
        help="run the given local script file on the device, then exit",
    )
    mp_parser.set_defaults(func=_cmd_micropython)

    kaluma_parser = subparsers.add_parser(
        "kaluma",
        parents=[
            _shared_arg_parser(
                "board",
                "gdb-port",
                "gdb",
                "bootrom",
                "expect-text",
                "expect-regex",
                "littlefs",
                "dump-fs",
                "tcp-port",
                "pty",
                "fetch-fw-only",
            )
        ],
        help="run a Kaluma UF2 image (interactive REPL only)",
    )
    kaluma_parser.add_argument("--image", help=_IMAGE_TAG_HELP)
    kaluma_parser.add_argument(
        "filename",
        nargs="?",
        type=_mk_file_suffixes_validator(".js"),
        help="local .js file to stage as the auto-run user program, then boot",
    )
    kaluma_parser.set_defaults(func=_cmd_kaluma)

    bench_parser = subparsers.add_parser(
        "bench",
        parents=[_shared_arg_parser("board", "bootrom", "expect-text", "expect-regex", "littlefs", "fetch-fw-only")],
        help="benchmark instruction-dispatch throughput",
    )
    bench_parser.add_argument("--instructions", type=int, default=5_000_000, help="synthetic mode: instruction count")
    bench_parser.add_argument("--block-size", type=int, default=1000, help="synthetic mode: instructions per block")
    bench_parser.add_argument("--image", help=f"firmware mode: {_IMAGE_PATH_HELP}")
    bench_parser.add_argument("--timeout", type=float, default=60.0, help="firmware mode: seconds before giving up")
    bench_parser.set_defaults(func=_cmd_bench)

    # add_help=False + a bare REMAINDER positional: every argument (including `-h`/`--help`) is
    # forwarded to the real `mpremote` untouched rather than intercepted by this parser - `rp2040py
    # mpremote --help` should show mpremote's own help, not rp2040py's.
    mpremote_parser = subparsers.add_parser(
        "mpremote",
        add_help=False,
        help="run the real mpremote, patched to work around its socket:// bare-REPL crash (see docs/mpremote.md)",
    )
    mpremote_parser.add_argument("mpremote_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    mpremote_parser.set_defaults(func=_cmd_mpremote)

    if _HAS_LITTLEFS:
        mklittlefs_parser = subparsers.add_parser(
            "mklittlefs",
            parents=[_shared_arg_parser("board")],
            help="build a littlefs image for `micropython`'s filesystem support",
        )
        mklittlefs_parser.add_argument(
            "files",
            nargs="*",
            type=_mk_file_suffixes_validator(".py", ".js"),
            help="source files to add, keeping their own basename",
        )
        mklittlefs_parser.add_argument(
            "--main", metavar="<basename>", help="write the `files` entry with this basename as main.py"
        )
        mklittlefs_parser.add_argument(
            "-o", "--output", type=_LITTLEFS_SUFFIX_VALIDATOR, default="littlefs.img", help="output image path"
        )
        mklittlefs_parser.add_argument(
            "-f", "--force", action="store_true", help="overwrite `--output` if it already exists"
        )
        mklittlefs_parser.add_argument(
            "--target",
            choices=_TARGET_NAMES,
            default=None,
            help=(
                "preset --block-size/--block-count for a known firmware's filesystem layout - "
                "mutually exclusive with passing them explicitly, sized for --board (micropython/"
                "kaluma only - a board with a bigger compiled firmware needs a differently-sized/"
                "placed filesystem region, see docs/records/0035) "
                "(defaults to micropython's if neither --target nor --block-size/--block-count is "
                "given)"
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

    completion_parser = subparsers.add_parser(
        "install-completion",
        help="install tab-completion for bash/zsh",
    )
    completion_parser.set_defaults(func=_cmd_install_completion)

    argcomplete.autocomplete(parser)
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
