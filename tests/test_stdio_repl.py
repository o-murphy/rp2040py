import os
import pty
import signal
import sys
import termios
import time
from dataclasses import dataclass

import pytest

from rp2040py.cli.stdio_repl import StdioInteractiveRepl


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


def test_sigterm_restores_terminal_before_exiting(pty_stdin, monkeypatch):
    """A plain SIGTERM (e.g. from `timeout`) must not leave the real terminal stuck in raw
    mode - atexit alone doesn't cover this, since the OS's default SIGTERM disposition kills the
    process before the interpreter ever runs atexit callbacks."""
    exit_calls = []
    monkeypatch.setattr(os, "_exit", exit_calls.append)

    canonical_before = termios.tcgetattr(pty_stdin.slave_fd)

    repl = StdioInteractiveRepl(_FakeCdc())
    repl.start()
    try:
        assert repl._old_termios == canonical_before

        handler = signal.getsignal(signal.SIGTERM)
        assert handler == repl._on_sigterm

        handler(signal.SIGTERM, None)

        assert termios.tcgetattr(pty_stdin.slave_fd) == canonical_before
        assert exit_calls == [128 + signal.SIGTERM]
    finally:
        repl.stop()


def test_stop_restores_previous_sigterm_handler(pty_stdin):
    original_handler = signal.getsignal(signal.SIGTERM)
    repl = StdioInteractiveRepl(_FakeCdc())
    repl.start()
    assert signal.getsignal(signal.SIGTERM) == repl._on_sigterm
    repl.stop()
    assert signal.getsignal(signal.SIGTERM) == original_handler


def test_sigterm_with_on_quit_only_signals_and_does_not_exit_itself(pty_stdin, monkeypatch):
    """With on_quit wired up (the real cli/__init__.py usage), StdioInteractiveRepl must not
    restore the terminal or force-exit itself - that's centralized on the main thread by whatever
    on_quit ultimately drives (_wait_for_simulator). Getting this wrong would silently reintroduce
    two competing shutdown paths."""
    exit_calls = []
    monkeypatch.setattr(os, "_exit", exit_calls.append)
    quit_calls = []

    canonical_before = termios.tcgetattr(pty_stdin.slave_fd)
    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=quit_calls.append)
    repl.start()
    try:
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)

        assert quit_calls == [128 + signal.SIGTERM]
        assert exit_calls == []
        # Terminal restore is deferred to whatever handles the quit request, not done here.
        assert termios.tcgetattr(pty_stdin.slave_fd) != canonical_before
    finally:
        repl.stop()


def test_ctrl_x_with_on_quit_requests_quit_instead_of_exiting(pty_stdin, monkeypatch):
    exit_calls = []
    monkeypatch.setattr(os, "_exit", exit_calls.append)
    quit_calls = []

    repl = StdioInteractiveRepl(_FakeCdc(), on_quit=quit_calls.append)
    repl.start()
    try:
        os.write(pty_stdin.master_fd, bytes([24]))  # Ctrl+X

        deadline = time.monotonic() + 2.0
        while not quit_calls and time.monotonic() < deadline:
            time.sleep(0.01)

        assert quit_calls == [0]
        assert exit_calls == []
    finally:
        repl.stop()
