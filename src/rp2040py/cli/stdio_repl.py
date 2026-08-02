"""Bridges a device's interactive REPL to the process's own stdio: raw termios mode, Ctrl+X to
quit, echoing device output to stdout - the tty-specific half of `InteractiveRepl`
(`device/repl_runner.py`), which itself knows nothing about terminals. Shared by the `micropython`
CLI subcommand and demo/kaluma_run.py (any USB-CDC-console firmware, not just MicroPython/
CircuitPython).
"""

import os
import sys
import termios
import threading
import tty
from collections.abc import Callable
from typing import Any

from rp2040py.device.repl_runner import InteractiveRepl
from rp2040py.usb.cdc import USBCDC

__all__ = ("StdioInteractiveRepl", "buf_write", "os_exit")

_CTRL_X = 24


def os_exit(status: int) -> None:
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
        try:
            self._stdin_fd = sys.stdin.fileno()
            if sys.stdin.isatty():
                self._old_termios = termios.tcgetattr(self._stdin_fd)
                tty.setraw(self._stdin_fd)
        except AttributeError:
            self._stdin_fd = None

        threading.Thread(target=self._read_stdin_loop, daemon=True).start()

    def _on_stop(self) -> None:
        self._restore_termios()

    def _restore_termios(self) -> None:
        if self._stdin_fd is not None and self._old_termios is not None:
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
            except OSError:
                pass

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
