"""Bridges a device's interactive REPL to the process's own stdio: raw termios mode, Ctrl+X to
quit, echoing device output to stdout - the tty-specific half of `InteractiveRepl`
(`device/repl_runner.py`), which itself knows nothing about terminals. Shared by the `micropython`
CLI subcommand and demo/kaluma_run.py (any USB-CDC-console firmware, not just MicroPython/
CircuitPython).
"""

import asyncio
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

from rp2040py.cli.process_repl import ProcessInteractiveRepl
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC

__all__ = ("StdioInteractiveRepl", "buf_write")

_CTRL_X = 24


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


class StdioInteractiveRepl(ProcessInteractiveRepl):
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
        super().__init__(cdc, simulator, on_quit, on_data=self._dispatch)
        self._extra_on_data = on_data
        self._stdin_fd: int | None = None
        self._old_termios: list[Any] | None = None
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
        super()._on_start()
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
                # active, so it's no longer a reliable place to restore the terminal - on_quit()
                # (Ctrl+X, --expect-text, SIGTERM) is. atexit here is the last-resort net for
                # anything that still bypasses that (an uncaught exception) - otherwise the real
                # terminal is left raw (no echo/line-buffering) after exit, which looks like "the
                # keyboard stopped working" until `stty sane`.
                #
                # atexit does NOT cover a plain SIGTERM (e.g. from `timeout`, or `kill` without
                # -9) - that's what super()._on_start() above installs a real signal handler for
                # (unconditionally, not just here in the tty branch - ProcessInteractiveRepl's own
                # docstring covers why).
                atexit.register(self._restore_termios)
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
        # signal.signal() only works from the main thread - this can also run from the non-tty
        # fallback's own daemon thread (its own `finally`), where ProcessInteractiveRepl's own
        # thread guard skips restoring rather than raising; the process is tearing down either way
        # at that point.
        self._restore_sigterm_handler()

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
            self._on_quit(0)
            return
        self._send_and_pace(chunk)

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
                    self._on_quit(0)
                    return
                # Matches the original line-buffered fallback's behavior: a trailing CR after
                # each read, since a canonical-mode stdin read already stripped/consumed the
                # newline that would otherwise represent pressing Enter.
                self._simulator.call(self._send_async(byte_data + bytes([13])))
        finally:
            self._restore_termios()
