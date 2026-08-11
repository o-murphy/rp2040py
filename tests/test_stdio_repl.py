import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass

import pytest

from rp2040py.cli.stdio_repl import StdioInteractiveRepl
from rp2040py.simulator import Simulator

# pty/termios are POSIX-only (see stdio_repl.py's own termios import gating) - importing either
# unconditionally at module scope would crash *collection* on Windows (not just fail these tests),
# taking every other test module down with it since pytest aborts the whole session on a collection
# error. These tests exercise raw-tty/pty behavior specifically, which has no Windows equivalent
# (stdio_repl.py's own fallback path for that case - line-buffered, no raw mode - has no termios/pty
# involved to test here), so skipping the whole module is correct, not just expedient.
pty = pytest.importorskip("pty")
termios = pytest.importorskip("termios")
is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("Усі тести REPL вимагають PTY, які не підтримуються на Android", allow_module_level=True)


class _FakeFifo:
    def __init__(self, size: int) -> None:
        self.size = size
        self.item_count = 0


class _FakeCdc:
    def __init__(self, fifo_size: int = 1_000_000) -> None:
        self.on_serial_data = None
        self.on_device_connected = None
        self.sent = bytearray()
        self.tx_fifo = _FakeFifo(fifo_size)

    def send_serial_byte(self, byte: int) -> None:
        self.sent.append(byte)
        self.tx_fifo.item_count += 1


@dataclass
class _Pty:
    master_fd: int
    slave_fd: int


@pytest.fixture
def pty_stdin(monkeypatch):
    master_fd, slave_fd = pty.openpty()
    slave = os.fdopen(slave_fd, "rb+", buffering=0)
    monkeypatch.setattr(sys, "stdin", slave)
    yield _Pty(master_fd, slave_fd)
    os.close(master_fd)


def _run_test(body) -> None:
    """Runs an async test body via `asyncio.run()` on this (the main) thread - not a background
    loop thread: `ProcessInteractiveRepl._on_start()`/`_on_stop()` call `signal.signal()`, which
    CPython restricts to the main thread of the main interpreter, matching how a real caller only
    ever awaits `start()`/`stop()` from `cli/__init__.py`'s own main-thread `asyncio.run()` (see
    `process_repl.py`'s own docstring). Polling loops that used to `time.sleep()` while a dedicated
    reader thread ran in the background now `await asyncio.sleep()` instead, so this same loop gets
    to process the real pty's `add_reader()` callback in between polls."""
    asyncio.run(body())


# c_cc (tcgetattr()'s 7th element) is NCCS slots long (32 on Linux/macOS), but only these are ever
# assigned a meaning - everything past the highest one (VEOL2) is a reserved/padding slot the
# kernel makes no round-trip guarantee about. Confirmed flaky specifically inside cibuildwheel's
# containerized Linux runners: two back-to-back tcgetattr() calls on the same untouched pty
# differed in those tail slots (looked like uninitialized memory, not real terminal state) - not
# reproducible locally, only under that container. Comparing the whole raw list turned that kernel
# implementation detail into a test failure; naming just the real control chars avoids it.
_NAMED_CC_INDICES = sorted(
    {
        getattr(termios, name)
        for name in (
            "VINTR",
            "VQUIT",
            "VERASE",
            "VKILL",
            "VEOF",
            "VTIME",
            "VMIN",
            "VSWTC",
            "VSTART",
            "VSTOP",
            "VSUSP",
            "VEOL",
            "VREPRINT",
            "VDISCARD",
            "VWERASE",
            "VLNEXT",
            "VEOL2",
        )
        if hasattr(termios, name)
    }
)


def _stable(attrs):
    """Masks PENDIN out of a termios snapshot before comparing two of them for equality, and
    narrows c_cc down to its named control-char slots (see _NAMED_CC_INDICES above). PENDIN
    ("retype pending input at next read") is kernel-managed bookkeeping, not application state -
    tcsetattr() restoring every field this code actually controls doesn't guarantee PENDIN round-
    trips identically, and it has no bearing on whether the terminal is actually back in
    cooked/echoing mode. Confirmed on macOS CI: PENDIN = 0x20000000 there vs 0x4000 on Linux -
    different bit position per platform, hence getattr() rather than a hardcoded mask."""
    normalized = list(attrs)
    normalized[3] &= ~getattr(termios, "PENDIN", 0)
    normalized[6] = [normalized[6][i] for i in _NAMED_CC_INDICES if i < len(normalized[6])]
    return normalized


def test_sigterm_signals_quit_without_touching_terminal(pty_stdin):
    """A plain SIGTERM (e.g. from `timeout`) must route through on_quit like every other quit
    trigger - it must not restore the terminal or exit the process itself. `on_quit` is always
    just a signal; whatever handles it (e.g. Simulator.wait_for_shutdown) owns the actual
    cleanup/exit, via repl.stop()."""

    async def _body() -> None:
        canonical_before = termios.tcgetattr(pty_stdin.slave_fd)
        quit_calls = []

        repl = StdioInteractiveRepl(_FakeCdc(), Simulator(), on_quit=quit_calls.append)
        await repl.start()
        try:
            assert _stable(repl._old_termios) == _stable(canonical_before)

            handler = signal.getsignal(signal.SIGTERM)
            assert handler == repl._on_sigterm

            handler(signal.SIGTERM, None)

            assert quit_calls == [128 + signal.SIGTERM]
            # Terminal restore is deferred to whatever handles the quit request, not done here.
            assert termios.tcgetattr(pty_stdin.slave_fd) != canonical_before
        finally:
            await repl.stop()

    _run_test(_body)


def test_stop_restores_terminal(pty_stdin):
    async def _body() -> None:
        canonical_before = termios.tcgetattr(pty_stdin.slave_fd)

        repl = StdioInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None)
        await repl.start()
        assert termios.tcgetattr(pty_stdin.slave_fd) != canonical_before

        await repl.stop()

        assert _stable(termios.tcgetattr(pty_stdin.slave_fd)) == _stable(canonical_before)

    _run_test(_body)


def test_stop_restores_previous_sigterm_handler(pty_stdin):
    async def _body() -> None:
        original_handler = signal.getsignal(signal.SIGTERM)
        repl = StdioInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None)
        await repl.start()
        assert signal.getsignal(signal.SIGTERM) == repl._on_sigterm
        await repl.stop()
        assert signal.getsignal(signal.SIGTERM) == original_handler

    _run_test(_body)


def test_ctrl_x_requests_quit(pty_stdin):
    async def _body() -> None:
        quit_calls = []

        repl = StdioInteractiveRepl(_FakeCdc(), Simulator(), on_quit=quit_calls.append)
        await repl.start()
        try:
            os.write(pty_stdin.master_fd, bytes([24]))  # Ctrl+X

            deadline = time.monotonic() + 2.0
            while not quit_calls and time.monotonic() < deadline:
                await asyncio.sleep(0.01)

            assert quit_calls == [0]
        finally:
            await repl.stop()

    _run_test(_body)


def test_typed_bytes_are_forwarded_to_the_device(pty_stdin):
    """The actual data path this whole migration touches: bytes typed at the real pty must reach
    cdc.send_serial_byte() - now via add_reader() + the engine-room loop instead of a dedicated
    reader thread calling it directly."""

    async def _body() -> None:
        cdc = _FakeCdc()
        repl = StdioInteractiveRepl(cdc, Simulator(), on_quit=lambda code: None)
        await repl.start()
        try:
            os.write(pty_stdin.master_fd, b"hello")

            deadline = time.monotonic() + 2.0
            while bytes(cdc.sent) != b"hello" and time.monotonic() < deadline:
                await asyncio.sleep(0.01)

            assert bytes(cdc.sent) == b"hello"
        finally:
            await repl.stop()

    _run_test(_body)


def test_paste_larger_than_the_fifo_is_not_truncated(pty_stdin):
    """The race this migration actually closes: sending used to happen directly from the reader
    thread, unsynchronized with whatever thread drains cdc.tx_fifo - structurally the same bug
    that once silently truncated raw-REPL uploads over the FIFO size (device/mp_device.py's own
    comment documents that incident). A tiny fake FIFO forces the "queue now, pump the rest once
    there's room" path a real device relies on for a paste bigger than USBCDC's 512-byte FIFO -
    nothing here may be silently dropped."""

    async def _body() -> None:
        cdc = _FakeCdc(fifo_size=4)
        simulator = Simulator()
        repl = StdioInteractiveRepl(cdc, simulator, on_quit=lambda code: None)
        await repl.start()
        try:
            payload = b"0123456789"
            os.write(pty_stdin.master_fd, payload)

            deadline = time.monotonic() + 2.0
            while bytes(cdc.sent) != payload and time.monotonic() < deadline:
                # Nothing else drains this fake FIFO on its own - simulate the device consuming
                # what's in flight, same as test_repl_runner.py's own
                # test_queue_and_pump_paces_by_free_space, then nudge the queue along the same way
                # the real pump alarm eventually would.
                cdc.tx_fifo.item_count = 0
                repl.pump()
                await asyncio.sleep(0.01)

            assert bytes(cdc.sent) == payload
        finally:
            await repl.stop()

    _run_test(_body)
