"""Bridges a device's interactive REPL to the process's own stdio: raw termios mode, Ctrl+X to
quit, echoing device output to stdout - the tty-specific half of `InteractiveRepl`
(`device/repl_runner.py`), which itself knows nothing about terminals. Shared by the `micropython`
CLI subcommand and demo/kaluma_run.py (any USB-CDC-console firmware, not just MicroPython/
CircuitPython).
"""

import asyncio
import atexit
import os
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

try:
    # POSIX-only (raw terminal mode has no Windows equivalent - Windows console handling is a
    # completely different API, e.g. msvcrt, not a drop-in replacement). Gated at import time so
    # the whole module - and everything that transitively imports it, notably cli/__init__.py's
    # top-level `from rp2040py.cli.stdio_repl import StdioInteractiveRepl` - stays importable on
    # Windows instead of failing outright (a real, CI-caught regression: this broke test
    # collection for every test that merely imports rp2040py.cli, not just ones exercising the
    # interactive REPL). `termios is None` below reuses _on_start()'s existing isatty()-gated
    # fallback to line-buffered sys.stdin.read() - the same degraded path already used when stdin
    # isn't a real tty at all (e.g. piped input) - so Windows gets that same graceful behavior
    # rather than a new, separate code path: no raw mode / no Ctrl+X-without-Enter, but the REPL
    # itself still works.
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

from rp2040py.device.repl_runner import InteractiveRepl
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC

__all__ = ("StdioInteractiveRepl", "buf_write")

_CTRL_X = 24
# Matches mp_device.py's _exec_blocking pacing: 1ms of simulated time between repeat pump()
# attempts while the tx_fifo is still full, giving the simulator loop room to actually drain it
# between tries instead of busy-looping.
_PUMP_PERIOD_NANOS = 1_000_000


def buf_write(buf, data: "int | bytes | bytearray") -> None:
    b: bytearray | bytes
    if isinstance(data, int):
        b = bytes([data])
    else:
        b = data
    if hasattr(buf, "buffer"):
        buf.buffer.write(b)
    else:
        buf.write(b.decode("utf-8", errors="replace"))
    buf.flush()


class StdioInteractiveRepl(InteractiveRepl):
    """Interactive bridge between a device's USB-CDC console and this process's stdin/stdout.
    `start()` puts the terminal (if any) into raw mode and starts forwarding stdin to the device;
    `stop()` restores the terminal. Quit by typing Ctrl+X.

    `simulator` is the `Simulator` that owns `cdc` - stdin is read via `asyncio.loop.add_reader()`
    on `simulator`'s own engine-room loop (`Simulator._ensure_loop()`), the same thread
    `execute()`/`USBCDC` state already only ever runs on, so forwarding bytes to the device never
    needs a cross-thread bridge for the common case: the read callback already *is* on the right
    thread.

    `on_data`, if given, is called with every chunk of device output in addition to it being
    echoed to stdout - e.g. for the CLI's `--expect-text` test-harness hook.

    `on_quit` is called with an exit code on Ctrl+X or a SIGTERM received while this instance owns
    the terminal - it must only signal (e.g. `Simulator.shutdown_request.request` - see
    `rp2040py.simulator`), never block or tear the process down itself: this class never calls
    `sys.exit()`/`os._exit()` on its own. Centralizing cleanup (restoring the terminal, closing a
    GDB server's socket, etc.) before a real `sys.exit()` is entirely the caller's job - typically
    `Simulator.wait_for_shutdown`, always run from one known thread.
    """

    def __init__(
        self,
        cdc: USBCDC,
        simulator: Simulator,
        on_quit: "Callable[[int], None]",
        on_data: "Callable[[bytes | bytearray], None] | None" = None,
    ) -> None:
        super().__init__(cdc, on_data=self._dispatch)
        self._simulator = simulator
        self._extra_on_data = on_data
        self._on_quit = on_quit
        self._stdin_fd: int | None = None
        self._old_termios: list[Any] | None = None
        self._old_sigterm_handler: Any = None
        self._pump_alarm: Any = None
        # Only used for the non-tty/Windows fallback (no fd to add_reader() on) - a small
        # dedicated thread for the one thing that still has to block: sys.stdin.read() itself.
        # Each chunk it reads is still forwarded onto the engine-room loop via simulator.call(),
        # not sent directly from this thread - see _fallback_read_loop().
        self._fallback_thread: threading.Thread | None = None

    def _dispatch(self, data: "bytes | bytearray") -> None:
        buf_write(sys.stdout, data)
        if self._extra_on_data is not None:
            self._extra_on_data(data)

    def _on_start(self) -> None:
        try:
            self._stdin_fd = sys.stdin.fileno()
            if termios is not None and sys.stdin.isatty():
                self._old_termios = termios.tcgetattr(self._stdin_fd)
                tty.setraw(self._stdin_fd)
                # tty.setraw() disables ISIG, so a real Ctrl+C no longer generates SIGINT at all -
                # it's just forwarded to the device instead (deliberate: matches screen/mpremote,
                # letting Ctrl+C interrupt whatever's running on the emulated device rather than
                # this process). That means the normal `except KeyboardInterrupt` path in
                # `Simulator.wait_for_shutdown` can never fire from the keyboard while raw mode is
                # active, so it's no longer a reliable place to restore the terminal - _request_quit()
                # (Ctrl+X, --expect-text, SIGTERM) is. atexit here is the last-resort net for
                # anything that still bypasses that (an uncaught exception) - otherwise the real
                # terminal is left raw (no echo/line-buffering) after exit, which looks like "the
                # keyboard stopped working" until `stty sane`.
                #
                # atexit does NOT cover a plain SIGTERM (e.g. from `timeout`, or `kill` without
                # -9): Python's default SIGTERM disposition kills the process at the OS level
                # before the interpreter ever gets to run atexit callbacks - confirmed empirically
                # (a bare `atexit.register(...)` + `kill -TERM` never fires it). Needs its own
                # signal handler, routed through the same _request_quit() as Ctrl+X. This can only
                # be installed on the actual process main thread (a signal-module restriction, not
                # an asyncio one) - `_on_start()` itself always runs there today (nothing async
                # calls it), so this is unaffected by simulator's own engine-room thread.
                atexit.register(self._restore_termios)
                self._old_sigterm_handler = signal.signal(signal.SIGTERM, self._on_sigterm)
        except AttributeError:
            self._stdin_fd = None

        if self._stdin_fd is not None:
            self._simulator.call(self._register_reader())
        else:
            self._fallback_thread = threading.Thread(target=self._fallback_read_loop, daemon=True)
            self._fallback_thread.start()

    async def _register_reader(self) -> None:
        assert self._stdin_fd is not None
        asyncio.get_running_loop().add_reader(self._stdin_fd, self._on_stdin_readable)

    async def _unregister_reader(self) -> None:
        assert self._stdin_fd is not None
        asyncio.get_running_loop().remove_reader(self._stdin_fd)

    def _on_stop(self) -> None:
        if self._stdin_fd is not None:
            self._simulator.call(self._unregister_reader())
        if self._fallback_thread is not None:
            self._fallback_thread.join(timeout=1.0)
        self._restore_termios()

    def _on_sigterm(self, signum: int, _frame: Any) -> None:
        # 128+signum matches the convention the shell/`timeout` itself would otherwise report for
        # a signal-terminated process.
        self._request_quit(128 + signum)

    def _request_quit(self, code: int) -> None:
        # Just flag it - the caller's Simulator.wait_for_shutdown loop does the actual
        # repl.stop()/gdb_server.close()/sys.exit(). This can run from the engine-room loop
        # (Ctrl+X, via _on_stdin_readable) or the main thread via a signal handler (SIGTERM) -
        # either way, it must not block or do teardown itself, since on_quit just sets an Event
        # and returns.
        self._on_quit(code)

    def _restore_termios(self) -> None:
        atexit.unregister(self._restore_termios)
        if self._stdin_fd is not None and self._old_termios is not None:
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
            except (OSError, termios.error):
                # termios.error isn't an OSError subclass (confirmed: its MRO is just
                # (termios.error, Exception, BaseException, object)) - both are caught here since
                # the fd can legitimately go bad before this runs (e.g. the pty's other end
                # already closed).
                pass
        if self._old_sigterm_handler is not None and threading.current_thread() is threading.main_thread():
            # signal.signal() only works from the main thread - this can also run from the
            # non-tty fallback's own daemon thread (its own `finally`), where restoring is
            # skipped rather than raising; the process is tearing down either way at that point.
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm_handler)
            except ValueError:
                pass
            self._old_sigterm_handler = None

    def _on_stdin_readable(self) -> None:
        # Registered via loop.add_reader() - always called on simulator's own engine-room loop,
        # the same thread execute()/USBCDC state already only ever runs on, so self.send() below
        # (touching cdc.tx_fifo) is already safe here with no bridging needed.
        assert self._stdin_fd is not None
        try:
            chunk = os.read(self._stdin_fd, 4096)
        except OSError:
            # The read side going away out from under us (e.g. a real terminal disconnecting, or
            # - relevant for a future --pty mode - the pty's other end closing) is not
            # fundamentally different from a clean EOF here: this callback's only job is
            # forwarding bytes, and there's nothing left to forward.
            chunk = b""
        if not chunk:
            asyncio.get_running_loop().remove_reader(self._stdin_fd)
            self._restore_termios()
            return
        if chunk[0] == _CTRL_X:
            asyncio.get_running_loop().remove_reader(self._stdin_fd)
            self._request_quit(0)
            return
        self._send_and_pace(chunk)

    def _send_and_pace(self, data: bytes) -> None:
        """send()s `data`, scheduling a repeat-pump alarm if the tx_fifo didn't have room for all
        of it. Must only be called from simulator's own engine-room thread - same requirement as
        send()/pump() themselves (they touch cdc.tx_fifo)."""
        if not self.send(data):
            self._schedule_pump()

    def _schedule_pump(self) -> None:
        if self._pump_alarm is None:
            self._pump_alarm = self._simulator.clock.create_alarm(self._pump_tick)
        self._pump_alarm.schedule(_PUMP_PERIOD_NANOS)

    def _pump_tick(self) -> None:
        # A clock alarm callback always fires from Clock.tick()'s own call chain, i.e. already on
        # the engine-room thread - no bridging needed here either, matching mp_device.py's
        # _exec_blocking's identical _pump_until_sent pattern.
        if not self.pump():
            self._schedule_pump()

    async def _send_async(self, data: bytes) -> None:
        self._send_and_pace(data)

    def _fallback_read_loop(self) -> None:
        # No raw mode / no Ctrl+X-without-Enter here (Windows, or stdin isn't a real tty at all -
        # e.g. piped input) - sys.stdin.read() is genuinely blocking with no portable non-thread
        # alternative, so this keeps its own small dedicated thread. Each chunk is still forwarded
        # through simulator.call() rather than sent directly from here, though: this thread is not
        # the engine room, and cdc.tx_fifo isn't safe to touch from anywhere else.
        try:
            while True:
                data = sys.stdin.read()
                if not data:
                    break
                byte_data = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                ctrl_x_index = byte_data.find(_CTRL_X)
                if ctrl_x_index != -1:
                    to_send = byte_data[:ctrl_x_index]
                    if to_send:
                        self._simulator.call(self._send_async(to_send))
                    self._request_quit(0)
                    return
                # Matches the original line-buffered fallback's behavior: a trailing CR after
                # each read, since a canonical-mode stdin read already stripped/consumed the
                # newline that would otherwise represent pressing Enter.
                self._simulator.call(self._send_async(byte_data + bytes([13])))
        finally:
            self._restore_termios()
