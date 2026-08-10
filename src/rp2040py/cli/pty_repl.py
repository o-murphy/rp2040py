"""Bridges a device's USB-CDC console to a real pseudo-terminal (pty) pair instead of this
process's own stdio or a TCP socket - a third `InteractiveRepl` transport (alongside
`StdioInteractiveRepl`/`SocketInteractiveRepl`, see `cli/process_repl.py` for what they share),
specifically so a client that needs a *real* POSIX serial-like file descriptor can have one.

Why this exists on top of `--tcp-port`: `mpremote`'s own interactive REPL (`mpremote ... repl`,
or bare `mpremote connect ...`) crashes when driven over `--tcp-port`'s `socket://` transport -
its `console.py` does `select.select([self.infd, pyb_serial.fd], [], [])`, which requires the
connected pySerial object to expose a `.fd` attribute, something pySerial's `socket://` URL
handler never provides (only its POSIX serial backend does - see docs/mpremote.md). A real pty's
slave side *is* a normal POSIX tty device (`/dev/pts/N` on Linux, `/dev/ttysNNN` on macOS) that
pySerial opens via its ordinary POSIX serial backend, `.fd` included - so `mpremote`'s REPL works
against it exactly as it would against a real board. `exec`/`fs`/`run`/... already work fine over
`--tcp-port`; `--pty` is specifically for unlocking the bare interactive REPL too.

POSIX-only (`pty.openpty()`/`os.ttyname()` have no Windows equivalent, matching `--tcp-port`'s own
Unix-vs-Pythonista motivation running the other way: this needs a real POSIX pty, which a sandboxed
environment with *no* serial/pty support at all - the whole reason `--tcp-port` exists - won't have
either. Pick whichever of `--tcp-port`/`--pty` your environment actually supports.
"""

import asyncio
import os
from collections.abc import Callable

from rp2040py.cli.process_repl import ProcessInteractiveRepl
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC

try:
    # POSIX-only - gated at import time so the module (and cli/__init__.py's top-level import of
    # it) stays importable on Windows instead of failing outright, mirroring stdio_repl.py's own
    # termios/tty gating. `pty is None` is what cli/__init__.py checks to give --pty a clear error
    # instead of an AttributeError/ImportError surfacing from deep inside start().
    import pty
    import tty
except ImportError:
    pty = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

__all__ = ("PtyInteractiveRepl",)


class PtyInteractiveRepl(ProcessInteractiveRepl):
    """Headless bridge between a device's USB-CDC console and a real pty pair - opened for this
    instance's whole `start()`-to-`stop()` lifetime, with both the master and slave ends held open
    throughout (matching how e.g. `socat pty,link=... pty,link=...` or QEMU's `-serial pty` keep
    both ends open): a client (`mpremote`, `screen`, Thonny, ...) opens `self.slave_path` itself,
    independently, whenever it wants to - including reconnecting more than once across this
    instance's lifetime, the same as `--tcp-port` allows.

    Unlike `SocketInteractiveRepl`, there's no explicit "one client at a time" bookkeeping: a real
    serial port doesn't stop two programs from opening it simultaneously and corrupting each
    other's I/O either, and multiple simultaneous clients on the same pty slave is exactly as much
    a misuse case here as it would be against real hardware - nothing this project needs to guard
    against specially.

    No in-band quit byte, same reasoning as `SocketInteractiveRepl`'s own docstring: a real client
    owns its own protocol on top of this byte stream. Quit via Ctrl+C/SIGTERM/`--expect-text` at
    the process level - see `cli/__init__.py`.

    Runs its `add_reader()`/`add_writer()` registration on `simulator`'s own engine-room loop
    (mirrors `StdioInteractiveRepl`'s raw-fd approach more than `SocketInteractiveRepl`'s
    asyncio-Streams one - a pty master fd is a plain low-level fd, not a socket asyncio already
    wraps in a Transport/Stream for us) - forwarding input bytes to the device is a plain
    synchronous call with no cross-thread bridge needed, same requirement as every other
    `InteractiveRepl` subclass. Writing device output back to the pty needs its own
    backpressure handling, unlike `StdioInteractiveRepl`'s stdout (buffered locally, blocking
    writes are fine) or `SocketInteractiveRepl`'s asyncio `StreamWriter` (already handles this) -
    a non-blocking pty master write can hit `BlockingIOError` if the client isn't reading fast
    enough, queued here and retried via `add_writer()` once the pty is writable again.
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
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._out_queue = b""
        self._write_registered = False
        # Resolved once start() has actually opened the pty pair - None until then.
        self.slave_path: str | None = None

    def _dispatch(self, data: "bytes | bytearray") -> None:
        # Always called from cdc.on_serial_data, itself only ever invoked from execute()'s own
        # call chain on the engine-room thread - the same thread this instance's reader/writer
        # callbacks are registered on, so touching self._out_queue here needs no cross-thread lock.
        if self._extra_on_data is not None:
            self._extra_on_data(data)
        if self._master_fd is None:
            return
        self._out_queue += bytes(data)
        self._flush_out_queue()

    def _on_start(self) -> None:
        super()._on_start()
        assert pty is not None, "PtyInteractiveRepl requires a POSIX pty (checked by the CLI before this runs)"
        self._master_fd, self._slave_fd = pty.openpty()
        # A freshly opened pty defaults to cooked/echoing mode (ECHO, ICANON, ICRNL, OPOST/ONLCR,
        # ...) - the same defaults a real terminal starts in. That's fine for a client that
        # configures the tty itself (pySerial's POSIX serial backend already does, via its own
        # cfmakeraw()-equivalent setup, which is why mpremote works against this unmodified) - but
        # this transport is meant to be a raw byte pipe like --tcp-port's socket, not a terminal
        # session, and nothing should have to depend on every possible client fixing that up
        # itself. Set raw mode here on the slave's own termios settings (shared by the whole pty
        # pair - either end can set them) so every byte round-trips unmodified regardless of what
        # opens the client side.
        assert tty is not None
        tty.setraw(self._slave_fd)
        os.set_blocking(self._master_fd, False)
        self.slave_path = os.ttyname(self._slave_fd)
        self._simulator.call(self._register_reader())

    def _on_stop(self) -> None:
        super()._on_stop()
        if self._master_fd is not None:
            self._simulator.call(self._unregister())
            _close_quietly(self._master_fd)
            self._master_fd = None
        if self._slave_fd is not None:
            _close_quietly(self._slave_fd)
            self._slave_fd = None
        self.slave_path = None
        self._out_queue = b""
        self._write_registered = False

    async def _register_reader(self) -> None:
        assert self._master_fd is not None
        asyncio.get_running_loop().add_reader(self._master_fd, self._on_master_readable)

    async def _unregister(self) -> None:
        assert self._master_fd is not None
        loop = asyncio.get_running_loop()
        loop.remove_reader(self._master_fd)
        if self._write_registered:
            loop.remove_writer(self._master_fd)

    def _on_master_readable(self) -> None:
        # Registered via loop.add_reader() - always called on simulator's own engine-room loop, the
        # same thread execute()/USBCDC state already only ever runs on, so self.send() below
        # (touching cdc.tx_fifo) is already safe here with no bridging needed.
        assert self._master_fd is not None
        try:
            chunk = os.read(self._master_fd, 4096)
        except OSError:
            # Nothing currently has the slave open for writing, or it just closed - not
            # fundamentally different from a clean EOF here: keep the reader registered (unlike
            # StdioInteractiveRepl's stdin, a pty master legitimately has no data for stretches at
            # a time between client connections, not a permanent end of input) and just skip this
            # empty read.
            chunk = b""
        if chunk:
            self._send_and_pace(chunk)

    def _flush_out_queue(self) -> None:
        assert self._master_fd is not None
        loop = asyncio.get_running_loop()
        try:
            while self._out_queue:
                written = os.write(self._master_fd, self._out_queue)
                self._out_queue = self._out_queue[written:]
        except BlockingIOError:
            pass
        except OSError:
            # The pty's gone (e.g. torn down mid-write) - nothing left to flush it to.
            self._out_queue = b""

        if self._out_queue:
            if not self._write_registered:
                loop.add_writer(self._master_fd, self._flush_out_queue)
                self._write_registered = True
        elif self._write_registered:
            loop.remove_writer(self._master_fd)
            self._write_registered = False


def _close_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
