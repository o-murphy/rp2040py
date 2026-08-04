import os
import pty
import signal
import sys
import termios

import pytest

from rp2040py.cli.stdio_repl import StdioInteractiveRepl


class _FakeCdc:
    def __init__(self) -> None:
        self.on_serial_data = None
        self.on_device_connected = None


@pytest.fixture
def pty_stdin(monkeypatch):
    master_fd, slave_fd = pty.openpty()
    slave = os.fdopen(slave_fd, "rb+", buffering=0)
    monkeypatch.setattr(sys, "stdin", slave)
    yield slave_fd
    os.close(master_fd)


def test_sigterm_restores_terminal_before_exiting(pty_stdin, monkeypatch):
    """A plain SIGTERM (e.g. from `timeout`) must not leave the real terminal stuck in raw
    mode - atexit alone doesn't cover this, since the OS's default SIGTERM disposition kills the
    process before the interpreter ever runs atexit callbacks."""
    exit_calls = []
    monkeypatch.setattr(os, "_exit", exit_calls.append)

    canonical_before = termios.tcgetattr(pty_stdin)

    repl = StdioInteractiveRepl(_FakeCdc())
    repl.start()
    try:
        assert repl._old_termios == canonical_before

        handler = signal.getsignal(signal.SIGTERM)
        assert handler == repl._on_sigterm

        handler(signal.SIGTERM, None)

        assert termios.tcgetattr(pty_stdin) == canonical_before
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
