import asyncio
import os
import select
import signal
import sys
import time

import pytest

from rp2040py.cli.pty_repl import PtyInteractiveRepl
from rp2040py.simulator import Simulator

# pty is POSIX-only (see pty_repl.py's own import gating) - importing it unconditionally at module
# scope would crash *collection* on Windows (not just fail these tests), taking every other test
# module down with it since pytest aborts the whole session on a collection error.
pty = pytest.importorskip("pty")
is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("PtyInteractiveRepl tests require PTY support, not available on Android", allow_module_level=True)


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


def _run_test(body) -> None:
    """Runs an async test body via `asyncio.run()` on this (the main) thread - not a background
    loop thread: `ProcessInteractiveRepl._on_start()`/`_on_stop()` call `signal.signal()`, which
    CPython restricts to the main thread of the main interpreter, matching how a real caller only
    ever awaits `start()`/`stop()` from `cli/__init__.py`'s own main-thread `asyncio.run()` (see
    `process_repl.py`'s own docstring). `repl` and this test body now share one loop/thread (per
    docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape"), so the blocking client-side pty read
    below runs via `asyncio.to_thread()` - not a plain blocking call - to keep that shared loop
    free to actually run the master fd's own reader/writer callbacks while a helper waits on them.
    """
    asyncio.run(body())


def _emit_from_device(cdc: _FakeCdc, data: bytes) -> None:
    """Mirrors test_socket_repl.py's own helper: calls cdc.on_serial_data(data) directly - this
    test body shares its one loop/thread with `repl` by construction, matching how a real device
    writing to its USB TX register only ever runs on that same engine-room loop in production."""
    assert cdc.on_serial_data is not None
    cdc.on_serial_data(data)


def _open_client(slave_path: str) -> int:
    return os.open(slave_path, os.O_RDWR | os.O_NOCTTY)


async def _recv_until(fd: int, expected: bytes, timeout: float = 2.0) -> bytes:
    def _do() -> bytes:
        buf = b""
        deadline = time.monotonic() + timeout
        while buf != expected and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        return buf

    return await asyncio.to_thread(_do)


def test_start_opens_a_pty_and_exposes_the_slave_path():
    async def _body() -> None:
        repl = PtyInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None)
        assert repl.slave_path is None
        await repl.start()
        try:
            assert repl.slave_path is not None
            assert os.path.exists(repl.slave_path)
        finally:
            await repl.stop()

    _run_test(_body)


def test_device_output_is_forwarded_to_a_client_on_the_slave_path():
    async def _body() -> None:
        cdc = _FakeCdc()
        simulator = Simulator()
        repl = PtyInteractiveRepl(cdc, simulator, on_quit=lambda code: None)
        await repl.start()
        try:
            client_fd = _open_client(repl.slave_path)
            try:
                _emit_from_device(cdc, b"hello from device")
                assert await _recv_until(client_fd, b"hello from device") == b"hello from device"
            finally:
                os.close(client_fd)
        finally:
            await repl.stop()

    _run_test(_body)


def test_bytes_from_client_are_forwarded_to_the_device():
    async def _body() -> None:
        cdc = _FakeCdc()
        repl = PtyInteractiveRepl(cdc, Simulator(), on_quit=lambda code: None)
        await repl.start()
        try:
            client_fd = _open_client(repl.slave_path)
            try:
                os.write(client_fd, b"print(1+1)\r\n")

                deadline = time.monotonic() + 2.0
                while bytes(cdc.sent) != b"print(1+1)\r\n" and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)

                assert bytes(cdc.sent) == b"print(1+1)\r\n"
            finally:
                os.close(client_fd)
        finally:
            await repl.stop()

    _run_test(_body)


def test_on_data_sees_output_even_without_a_connected_client():
    """--expect-text watches every byte of device output regardless of whether a client happens to
    be connected yet - mirrors SocketInteractiveRepl's own identical test."""

    async def _body() -> None:
        cdc = _FakeCdc()
        simulator = Simulator()
        seen = bytearray()
        repl = PtyInteractiveRepl(cdc, simulator, on_quit=lambda code: None, on_data=seen.extend)
        await repl.start()
        try:
            _emit_from_device(cdc, b"nobody listening yet")
            assert bytes(seen) == b"nobody listening yet"
        finally:
            await repl.stop()

    _run_test(_body)


def test_paced_send_for_a_full_fifo():
    """Mirrors test_stdio_repl.py's/test_socket_repl.py's identical test: a payload larger than
    the tx_fifo must be queued and pumped through, not silently truncated."""

    async def _body() -> None:
        cdc = _FakeCdc(fifo_size=4)
        simulator = Simulator()
        repl = PtyInteractiveRepl(cdc, simulator, on_quit=lambda code: None)
        await repl.start()
        try:
            client_fd = _open_client(repl.slave_path)
            try:
                payload = b"0123456789"
                os.write(client_fd, payload)

                deadline = time.monotonic() + 2.0
                while bytes(cdc.sent) != payload and time.monotonic() < deadline:
                    cdc.tx_fifo.item_count = 0
                    repl.pump()
                    await asyncio.sleep(0.01)

                assert bytes(cdc.sent) == payload
            finally:
                os.close(client_fd)
        finally:
            await repl.stop()

    _run_test(_body)


def test_device_output_larger_than_the_pty_buffer_is_not_truncated():
    """The write-side backpressure this class adds on top of what Stdio/Socket need: a big enough
    payload can make a non-blocking os.write() to the pty master hit BlockingIOError - must be
    queued and flushed via add_writer(), not silently dropped."""

    async def _body() -> None:
        cdc = _FakeCdc()
        simulator = Simulator()
        repl = PtyInteractiveRepl(cdc, simulator, on_quit=lambda code: None)
        await repl.start()
        try:
            client_fd = _open_client(repl.slave_path)
            try:
                payload = bytes(range(256)) * 256  # 64KiB - comfortably bigger than a pty's own buffer
                _emit_from_device(cdc, payload)

                received = b""
                deadline = time.monotonic() + 5.0
                while len(received) < len(payload) and time.monotonic() < deadline:
                    received += await _recv_until(client_fd, payload[len(received) :], timeout=0.5)

                assert received == payload
            finally:
                os.close(client_fd)
        finally:
            await repl.stop()

    _run_test(_body)


def test_sigterm_signals_quit():
    """Regression test mirroring test_socket_repl.py's identical one: a plain `kill <pid>` (no -9)
    on a `--pty` process must still run --dump-fs's own cleanup, not just quit on a client
    disconnect - see cli/process_repl.py's own docstring for why this needs an explicit handler."""

    async def _body() -> None:
        quit_calls = []

        repl = PtyInteractiveRepl(_FakeCdc(), Simulator(), on_quit=quit_calls.append)
        await repl.start()
        try:
            handler = signal.getsignal(signal.SIGTERM)
            assert handler == repl._on_sigterm

            handler(signal.SIGTERM, None)

            assert quit_calls == [128 + signal.SIGTERM]
        finally:
            await repl.stop()

    _run_test(_body)


def test_stop_restores_the_previous_sigterm_handler():
    async def _body() -> None:
        original_handler = signal.getsignal(signal.SIGTERM)
        repl = PtyInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None)
        await repl.start()
        assert signal.getsignal(signal.SIGTERM) == repl._on_sigterm
        await repl.stop()
        assert signal.getsignal(signal.SIGTERM) == original_handler

    _run_test(_body)


def test_stop_closes_the_pty():
    async def _body() -> None:
        repl = PtyInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None)
        await repl.start()
        slave_path = repl.slave_path
        await repl.stop()

        assert repl.slave_path is None
        try:
            _open_client(slave_path)
        except OSError:
            pass
        else:
            raise AssertionError("expected the pty to be closed after stop()")

    _run_test(_body)
