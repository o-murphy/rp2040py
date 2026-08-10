import signal
import socket
import time

from rp2040py.cli.socket_repl import SocketInteractiveRepl
from rp2040py.simulator import Simulator


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


def _connect(port: int, timeout: float = 2.0) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(timeout)
    return sock


def _emit_from_device(simulator: Simulator, cdc: _FakeCdc, data: bytes) -> None:
    """Calls cdc.on_serial_data(data) on simulator's own engine-room thread - in real usage that
    callback only ever fires from inside execute()'s own call chain (a real device writing to its
    USB TX register), never from whatever thread happens to be calling this test - see
    SocketInteractiveRepl._dispatch()'s own docstring for why that matters here."""

    async def _emit() -> None:
        assert cdc.on_serial_data is not None
        cdc.on_serial_data(data)

    simulator.call(_emit())


def _wait_for_client(repl: SocketInteractiveRepl, timeout: float = 2.0) -> None:
    """Blocks until `repl`'s connection-handler task has actually registered the just-connected
    client (`repl._client_writer`). A completed TCP handshake at the socket layer doesn't mean
    asyncio's `Server` has run its accept callback yet - `_handle_connection()` is scheduled onto
    the same engine-room loop as everything else, so a caller that immediately pushes device
    output afterward (e.g. via `_emit_from_device()`) can otherwise race it and find no writer
    registered yet."""
    deadline = time.monotonic() + timeout
    while repl._client_writer is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert repl._client_writer is not None, "client was never accepted"


def _connect_and_wait_for_accept(repl: SocketInteractiveRepl, timeout: float = 5.0) -> socket.socket:
    """`_connect()` followed by `_wait_for_client()`, retried with a fresh connection on timeout.

    Reconnecting to the same port immediately after the previous connection's teardown was
    observed to occasionally leave the new connection's accept callback unscheduled for multiple
    seconds specifically on macOS CI runners (kqueue-based selector), even once the previous
    connection had already fully cleared `repl._client_writer` before this one connects - an OS/
    event-loop-scheduling timing quirk around a rapid close-then-reconnect burst, not reproduced
    on Linux, and not something clearable by waiting longer on a single connection attempt.
    Retrying the connection itself (as a real reconnecting client would do anyway) resolves it."""
    deadline = time.monotonic() + timeout
    while True:
        sock = _connect(repl.port)
        remaining = deadline - time.monotonic()
        per_attempt = min(1.0, max(remaining, 0.1))
        attempt_deadline = time.monotonic() + per_attempt
        while repl._client_writer is None and time.monotonic() < attempt_deadline:
            time.sleep(0.01)
        if repl._client_writer is not None:
            return sock
        sock.close()
        if time.monotonic() >= deadline:
            raise AssertionError("client was never accepted")


def _recv_until(sock: socket.socket, expected: bytes, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    deadline = time.monotonic() + timeout
    while buf != expected and time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


def test_start_listens_on_a_resolved_port():
    repl = SocketInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None, port=0)
    assert repl.port is None
    repl.start()
    try:
        assert repl.port is not None and repl.port > 0
    finally:
        repl.stop()


def test_device_output_is_forwarded_to_the_connected_client():
    cdc = _FakeCdc()
    simulator = Simulator()
    repl = SocketInteractiveRepl(cdc, simulator, on_quit=lambda code: None, port=0)
    repl.start()
    try:
        client = _connect(repl.port)
        try:
            _wait_for_client(repl)
            _emit_from_device(simulator, cdc, b"hello from device")
            assert _recv_until(client, b"hello from device") == b"hello from device"
        finally:
            client.close()
    finally:
        repl.stop()


def test_bytes_from_client_are_forwarded_to_the_device():
    cdc = _FakeCdc()
    repl = SocketInteractiveRepl(cdc, Simulator(), on_quit=lambda code: None, port=0)
    repl.start()
    try:
        client = _connect(repl.port)
        try:
            client.sendall(b"print(1+1)\r\n")

            deadline = time.monotonic() + 2.0
            while bytes(cdc.sent) != b"print(1+1)\r\n" and time.monotonic() < deadline:
                time.sleep(0.01)

            assert bytes(cdc.sent) == b"print(1+1)\r\n"
        finally:
            client.close()
    finally:
        repl.stop()


def test_second_connection_is_rejected_while_one_is_active():
    repl = SocketInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None, port=0)
    repl.start()
    try:
        first = _connect(repl.port)
        try:
            _wait_for_client(repl)
            # A comfortably longer read timeout than socket_repl.py's own
            # _STALE_CONNECTION_GRACE_SECONDS (1s): _handle_connection() waits up to that long for
            # a still-occupied slot's previous connection to finish tearing down before concluding
            # it's genuinely active and rejecting this one - `first` here is genuinely active for
            # this whole test, so that full grace period always elapses before the rejection.
            second = _connect(repl.port, timeout=5.0)
            try:
                # Closed server-side (after the grace period above) - recv() sees EOF (b""), not
                # more data.
                assert second.recv(4096) == b""
            finally:
                second.close()
        finally:
            first.close()
    finally:
        repl.stop()


def test_a_new_connection_is_accepted_after_the_previous_one_disconnects():
    cdc = _FakeCdc()
    simulator = Simulator()
    repl = SocketInteractiveRepl(cdc, simulator, on_quit=lambda code: None, port=0)
    repl.start()
    try:
        first = _connect(repl.port)
        first.close()

        deadline = time.monotonic() + 2.0
        while repl._client_writer is not None and time.monotonic() < deadline:
            time.sleep(0.01)

        second = _connect_and_wait_for_accept(repl)
        try:
            _emit_from_device(simulator, cdc, b"still alive")
            assert _recv_until(second, b"still alive") == b"still alive"
        finally:
            second.close()
    finally:
        repl.stop()


def test_on_data_sees_output_even_without_a_connected_client():
    """--expect-text watches every byte of device output regardless of whether a TCP client
    happens to be connected yet - mirrors StdioInteractiveRepl's own on_data contract."""
    cdc = _FakeCdc()
    simulator = Simulator()
    seen = bytearray()
    repl = SocketInteractiveRepl(cdc, simulator, on_quit=lambda code: None, port=0, on_data=seen.extend)
    repl.start()
    try:
        _emit_from_device(simulator, cdc, b"nobody listening yet")
        assert bytes(seen) == b"nobody listening yet"
    finally:
        repl.stop()


def test_paced_send_for_a_full_fifo():
    """Mirrors test_stdio_repl.py's identical test: a payload larger than the tx_fifo must be
    queued and pumped through, not silently truncated."""
    cdc = _FakeCdc(fifo_size=4)
    simulator = Simulator()
    repl = SocketInteractiveRepl(cdc, simulator, on_quit=lambda code: None, port=0)
    repl.start()
    try:
        client = _connect(repl.port)
        try:
            payload = b"0123456789"
            client.sendall(payload)

            async def _pump() -> None:
                repl.pump()

            deadline = time.monotonic() + 2.0
            while bytes(cdc.sent) != payload and time.monotonic() < deadline:
                cdc.tx_fifo.item_count = 0
                simulator.call(_pump())
                time.sleep(0.01)

            assert bytes(cdc.sent) == payload
        finally:
            client.close()
    finally:
        repl.stop()


def test_stop_closes_the_listening_socket():
    repl = SocketInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None, port=0)
    repl.start()
    port = repl.port
    repl.stop()

    assert repl.port is None
    try:
        _connect(port, timeout=0.5)
    except OSError:
        pass
    else:
        raise AssertionError("expected the listening socket to be closed after stop()")


def test_sigterm_signals_quit():
    """Regression test: Python's default SIGTERM disposition is immediate OS-level process
    termination, bypassing every finally/context-manager exit in the interpreter - so without an
    explicit handler here, a plain `kill <pid>` (no -9) on a `--tcp-port` process would skip
    --dump-fs's own cleanup entirely, unlike Ctrl+C (a catchable KeyboardInterrupt) or a client
    disconnect. Mirrors test_stdio_repl.py's identical SIGTERM test for StdioInteractiveRepl."""
    quit_calls = []

    repl = SocketInteractiveRepl(_FakeCdc(), Simulator(), on_quit=quit_calls.append, port=0)
    repl.start()
    try:
        handler = signal.getsignal(signal.SIGTERM)
        assert handler == repl._on_sigterm

        handler(signal.SIGTERM, None)

        assert quit_calls == [128 + signal.SIGTERM]
    finally:
        repl.stop()


def test_stop_restores_the_previous_sigterm_handler():
    original_handler = signal.getsignal(signal.SIGTERM)
    repl = SocketInteractiveRepl(_FakeCdc(), Simulator(), on_quit=lambda code: None, port=0)
    repl.start()
    assert signal.getsignal(signal.SIGTERM) == repl._on_sigterm
    repl.stop()
    assert signal.getsignal(signal.SIGTERM) == original_handler
