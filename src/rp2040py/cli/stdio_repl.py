"""Bridges a device's interactive REPL to the process's own stdio: raw termios mode, Ctrl+X to
quit, echoing device output to stdout - the tty-specific half of `InteractiveRepl`
(`device/repl_runner.py`), which itself knows nothing about terminals. Shared by the `micropython`
CLI subcommand and demo/kaluma_run.py (any USB-CDC-console firmware, not just MicroPython/
CircuitPython).
"""

import atexit
import os
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

# The terminal-owning StdioInteractiveRepl currently in raw mode, if any - so os_exit() can put
# the terminal back before it tears down the process. os._exit() (below) skips atexit callbacks,
# object finalizers, and every other normal cleanup hook by design, and every quit path in this
# module (Ctrl+X, --expect-text matching in cli/__init__.py) goes through it rather than a plain
# sys.exit()/return, so atexit.register() alone (see StdioInteractiveRepl._on_start()) only
# catches the *other* ways this process can end (an uncaught exception, an external SIGTERM) -
# not this one.
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
    stdin to the device; `stop()` restores the terminal. Quit by typing Ctrl+X, which exits the
    whole process (`os._exit`) - matching the existing `rp2040py micropython`/`demo/kaluma_run.py`
    behavior this replaces.

    `on_data`, if given, is called with every chunk of device output in addition to it being
    echoed to stdout - e.g. for the CLI's `--expect-text` test-harness hook.
    """

    def __init__(self, cdc: USBCDC, on_data: "Callable[[bytes | bytearray], None] | None" = None) -> None:
        super().__init__(cdc, on_data=self._dispatch)
        self._extra_on_data = on_data
        self._stdin_fd: int | None = None
        self._old_termios: list[Any] | None = None

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
                # `_wait_for_simulator` can never fire from the keyboard while raw mode is active,
                # so it's no longer a reliable place to restore the terminal. Cover the two ways
                # this process actually ends: os_exit() (Ctrl+X, --expect-text match - see
                # `_active_raw_repl` above) and anything else (an uncaught exception, an external
                # SIGTERM from e.g. `timeout`), via atexit here - otherwise the real terminal is
                # left raw (no echo/line-buffering) after exit, which looks like "the keyboard
                # stopped working" until `stty sane`.
                _active_raw_repl = self
                atexit.register(self._restore_termios)
        except AttributeError:
            self._stdin_fd = None

        threading.Thread(target=self._read_stdin_loop, daemon=True).start()

    def _on_stop(self) -> None:
        self._restore_termios()

    def _restore_termios(self) -> None:
        global _active_raw_repl
        if self._stdin_fd is not None and self._old_termios is not None:
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
            except OSError:
                pass
        if _active_raw_repl is self:
            _active_raw_repl = None

    def _read_stdin_loop(self) -> None:
        try:
            if self._stdin_fd is not None:
                while True:
                    chunk = os.read(self._stdin_fd, 4096)
                    if not chunk:
                        break
                    if chunk[0] == _CTRL_X:
                        # os_exit(), not sys.exit(): this runs on the dedicated stdin reader
                        # thread, not the main thread, so sys.exit() would only terminate that
                        # thread instead of the whole process.
                        self.stop()
                        os_exit(0)
                    self.send(chunk)
            else:
                while True:
                    data = sys.stdin.read()
                    if not data:
                        break
                    byte_data = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                    for byte in byte_data:
                        if byte == _CTRL_X:
                            self.stop()
                            os_exit(0)
                        self._send_byte_blocking(byte)
                    self._send_byte_blocking(13)
        finally:
            self._restore_termios()
