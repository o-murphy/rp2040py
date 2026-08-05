import os
import signal
import sys
import time
from dataclasses import dataclass

import pytest

from rp2040py.cli.stdio_repl import StdioInteractiveRepl

# pty/termios are POSIX-only (see stdio_repl.py's own termios import gating) - importing either
# unconditionally at module scope would crash *collection* on Windows (not just fail these tests),
# taking every other test module down with it since pytest aborts the whole session on a collection
# error. These tests exercise raw-tty/pty behavior specifically, which has no Windows equivalent
# (stdio_repl.py's own fallback path for that case - line-buffered, no raw mode - has no termios/pty
# involved to test here), so skipping the whole module is correct, not just expedient.
pty = pytest.importorskip("pty")
termios = pytest.importorskip("termios")


class _FakeCdc:
    def __init__(self) -> None:
        self.on_serial_data = None
        self.on_device_connected = None
        self.sent = bytearray()

    def send_serial_byte(self, byte: int) -> None:
        self.sent.append(byte)


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


def test_sigterm_signals_quit_without_touching_terminal(pty_stdin):
    """A plain SIGTERM (e.g. from `timeout`) must route through on_quit like every other quit
    trigger - it must not restore the terminal or exit the process itself. `on_quit` is always
    just a signal; whatever handles it (e.g. Simulator.wait_for_shutdown) owns the actual
    cleanup/exit, via repl.stop()."""
    canonical_before = termios.tcgetattr(pty_stdin.slave_fd)
    quit_calls = []

    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=quit_calls.append)
    repl.start()
    try:
        assert repl._old_termios == canonical_before

        handler = signal.getsignal(signal.SIGTERM)
        assert handler == repl._on_sigterm

        handler(signal.SIGTERM, None)

        assert quit_calls == [128 + signal.SIGTERM]
        # Terminal restore is deferred to whatever handles the quit request, not done here.
        assert termios.tcgetattr(pty_stdin.slave_fd) != canonical_before
    finally:
        repl.stop()


def test_stop_restores_terminal(pty_stdin):
    canonical_before = termios.tcgetattr(pty_stdin.slave_fd)

    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=lambda code: None)
    repl.start()
    assert termios.tcgetattr(pty_stdin.slave_fd) != canonical_before

    repl.stop()

    assert termios.tcgetattr(pty_stdin.slave_fd) == canonical_before


def test_stop_restores_previous_sigterm_handler(pty_stdin):
    original_handler = signal.getsignal(signal.SIGTERM)
    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=lambda code: None)
    repl.start()
    assert signal.getsignal(signal.SIGTERM) == repl._on_sigterm
    repl.stop()
    assert signal.getsignal(signal.SIGTERM) == original_handler


def test_ctrl_x_requests_quit(pty_stdin):
    quit_calls = []

    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=quit_calls.append)
    repl.start()
    try:
        os.write(pty_stdin.master_fd, bytes([24]))  # Ctrl+X

        deadline = time.monotonic() + 2.0
        while not quit_calls and time.monotonic() < deadline:
            time.sleep(0.01)

        assert quit_calls == [0]
    finally:
        repl.stop()
