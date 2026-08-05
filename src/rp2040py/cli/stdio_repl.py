"""Bridges a device's interactive REPL to the process's own stdio: raw termios mode, Ctrl+X to
quit, echoing device output to stdout - the tty-specific half of `InteractiveRepl`
(`device/repl_runner.py`), which itself knows nothing about terminals. Shared by the `micropython`
CLI subcommand and demo/kaluma_run.py (any USB-CDC-console firmware, not just MicroPython/
CircuitPython).
"""

import atexit
import os
import select
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
from rp2040py.usb.cdc import USBCDC

__all__ = ("StdioInteractiveRepl", "buf_write", "os_exit")

_CTRL_X = 24

# The terminal-owning StdioInteractiveRepl currently in raw mode, if any - so os_exit() (the
# standalone fallback below, used only when a caller doesn't supply on_quit) can put the terminal
# back before it tears down the process. os._exit() skips atexit callbacks, object finalizers,
# and every other normal cleanup hook by design. cli/__init__.py's commands instead pass on_quit=
# a Simulator.shutdown_request.request (see rp2040py.simulator), so Ctrl+X/--expect-text/SIGTERM
# here just flag that request and let Simulator.wait_for_shutdown - on the thread driving the
# simulator - do the actual repl/GDB-server cleanup and a real sys.exit(); atexit.register() (see
# _on_start()) is the last-resort net for anything that still bypasses that (an uncaught
# exception, or this class used standalone with no on_quit).
_active_raw_repl: "StdioInteractiveRepl | None" = None


def os_exit(status: int) -> None:
    if _active_raw_repl is not None:
        _active_raw_repl._restore_termios()
    if "Pythonista3.app" in sys.executable or "Python IDE.app" in sys.executable:
        sys.exit(status)
    os._exit(status)


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
    `start()` puts the terminal (if any) into raw mode and spawns a daemon thread forwarding
    stdin to the device; `stop()` restores the terminal. Quit by typing Ctrl+X.

    `on_data`, if given, is called with every chunk of device output in addition to it being
    echoed to stdout - e.g. for the CLI's `--expect-text` test-harness hook.

    `on_quit`, if given, is called with an exit code on Ctrl+X or a SIGTERM received while this
    instance owns the terminal - instead of this class tearing the process down itself, letting a
    caller (typically `Simulator.shutdown_request.request` - see `rp2040py.simulator`) centralize
    cleanup (restoring the terminal, closing a GDB server's socket, etc.) before a real
    `sys.exit()`. Without `on_quit`, this class falls back to its original standalone behavior:
    restore the terminal itself and call `os._exit()` directly - for any caller that just wants a
    working REPL without wiring up a shutdown coordinator.
    """

    def __init__(
        self,
        cdc: USBCDC,
        on_data: "Callable[[bytes | bytearray], None] | None" = None,
        on_quit: "Callable[[int], None] | None" = None,
    ) -> None:
        super().__init__(cdc, on_data=self._dispatch)
        self._extra_on_data = on_data
        self._on_quit = on_quit
        self._stdin_fd: int | None = None
        self._old_termios: list[Any] | None = None
        self._old_sigterm_handler: Any = None
        self._reader_thread: threading.Thread | None = None
        # Self-pipe so stop() can unblock the reader thread's select()/os.read() deterministically
        # instead of relying on the fd it's reading from eventually going away. Closing the target
        # fd itself to cancel a pending read would be racy: another thread's fd reuse could get
        # redirected onto it mid-syscall (this is precisely the bug this fixes - see _on_stop()).
        self._wake_r: int | None = None
        self._wake_w: int | None = None

    def _dispatch(self, data: "bytes | bytearray") -> None:
        buf_write(sys.stdout, data)
        if self._extra_on_data is not None:
            self._extra_on_data(data)

    def _on_start(self) -> None:
        global _active_raw_repl
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
                # anything that still bypasses that (an uncaught exception, or standalone use with
                # no on_quit) - otherwise the real terminal is left raw (no echo/line-buffering)
                # after exit, which looks like "the keyboard stopped working" until `stty sane`.
                #
                # atexit does NOT cover a plain SIGTERM (e.g. from `timeout`, or `kill` without
                # -9): Python's default SIGTERM disposition kills the process at the OS level
                # before the interpreter ever gets to run atexit callbacks - confirmed empirically
                # (a bare `atexit.register(...)` + `kill -TERM` never fires it). Needs its own
                # signal handler, routed through the same _request_quit() as Ctrl+X.
                _active_raw_repl = self
                atexit.register(self._restore_termios)
                self._old_sigterm_handler = signal.signal(signal.SIGTERM, self._on_sigterm)
        except AttributeError:
            self._stdin_fd = None

        if self._stdin_fd is not None:
            self._wake_r, self._wake_w = os.pipe()
        self._reader_thread = threading.Thread(target=self._read_stdin_loop, daemon=True)
        self._reader_thread.start()

    def _on_stop(self) -> None:
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass
        # Guard against joining ourselves: on standalone Ctrl+X (no on_quit), _request_quit() calls
        # stop() from inside _read_stdin_loop itself, after it's already past the select() below -
        # joining here would deadlock (Thread.join() on the current thread raises RuntimeError).
        if self._reader_thread is not None and threading.current_thread() is not self._reader_thread:
            self._reader_thread.join(timeout=1.0)
        self._restore_termios()
        for fd in (self._wake_r, self._wake_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._wake_r = self._wake_w = None

    def _on_sigterm(self, signum: int, _frame: Any) -> None:
        # 128+signum matches the convention the shell/`timeout` itself would otherwise report for
        # a signal-terminated process.
        self._request_quit(128 + signum)

    def _request_quit(self, code: int) -> None:
        if self._on_quit is not None:
            # Just flag it - the caller's Simulator.wait_for_shutdown loop does the actual
            # repl.stop()/gdb_server.close()/sys.exit(). This runs from the stdin-reader thread
            # (Ctrl+X) or the main thread via a signal handler (SIGTERM) - either way, it must not
            # block or do teardown itself, since on_quit just sets an Event and returns.
            self._on_quit(code)
        else:
            # Standalone fallback (no shutdown coordinator wired up): behave like this always did
            # before on_quit existed - restore the terminal and force-exit directly.
            self.stop()
            os_exit(code)

    def _restore_termios(self) -> None:
        global _active_raw_repl
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
            # signal.signal() only works from the main thread - this can also run from
            # _read_stdin_loop's daemon thread (its own `finally`), where restoring is skipped
            # rather than raising; the process is tearing down either way at that point.
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm_handler)
            except ValueError:
                pass
            self._old_sigterm_handler = None
        if _active_raw_repl is self:
            _active_raw_repl = None

    def _read_stdin_loop(self) -> None:
        try:
            if self._stdin_fd is not None:
                # _on_start() always pairs _wake_r/_wake_w with a non-None _stdin_fd.
                assert self._wake_r is not None
                while True:
                    try:
                        ready, _, _ = select.select([self._stdin_fd, self._wake_r], [], [])
                    except OSError:
                        break
                    if self._wake_r in ready:
                        # stop() woke us up via the self-pipe - unrelated to stdin having data.
                        break
                    try:
                        chunk = os.read(self._stdin_fd, 4096)
                    except OSError:
                        # The read side going away out from under us (e.g. a real terminal
                        # disconnecting, or - relevant for a future --pty mode - the pty's other
                        # end closing) is not fundamentally different from a clean EOF here: this
                        # thread's only job is forwarding bytes, and there's nothing left to
                        # forward. Treated as EOF rather than left to propagate and print an
                        # unhandled-thread-exception traceback for what's really just "the other
                        # end hung up."
                        break
                    if not chunk:
                        break
                    if chunk[0] == _CTRL_X:
                        # _request_quit(), not sys.exit(): this runs on the dedicated stdin
                        # reader thread, not the main thread, so sys.exit() would only terminate
                        # that thread instead of the whole process.
                        self._request_quit(0)
                        return
                    self.send(chunk)
            else:
                while True:
                    data = sys.stdin.read()
                    if not data:
                        break
                    byte_data = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                    for byte in byte_data:
                        if byte == _CTRL_X:
                            self._request_quit(0)
                            return
                        self._send_byte_blocking(byte)
                    self._send_byte_blocking(13)
        finally:
            self._restore_termios()
