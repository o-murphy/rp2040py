import socket

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer


class _FakeRP2040:
    """add_connection() assigns rp2040.on_break - just needs to be a settable attribute, never
    actually invoked in these tests (that only happens on a real breakpoint hit)."""

    on_break = None


class _FakeTarget:
    """GDBServer.__init__ only stores `target`, never calls it - these tests exercise socket
    lifecycle, not the GDB protocol. `rp2040` is set (even though otherwise unused here) because a
    real client connecting mid-test (test_gdb_client_can_still_connect_before_close) does spin up
    a handle_connection thread that touches target.rp2040 via GDBServer.add_connection."""

    rp2040 = _FakeRP2040()


def test_close_unblocks_and_joins_the_loop_thread():
    server = GDBTCPServer(_FakeTarget(), port=0)
    assert server._loop_thread.is_alive()

    server.close()

    assert not server._loop_thread.is_alive()


def test_close_is_idempotent():
    server = GDBTCPServer(_FakeTarget(), port=0)
    server.close()
    server.close()  # must not raise


def test_close_actually_lets_a_plain_process_exit_join_cleanly():
    """The whole point of close() - reproduces the exact hang `os._exit()` was working around:
    a non-daemon thread blocked forever with nothing telling it to stop. A join() with a short
    timeout is the regression check - without close(), this would time out instead of the thread
    having ended."""
    server = GDBTCPServer(_FakeTarget(), port=0)
    thread = server._loop_thread
    server.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_gdb_client_can_still_connect_before_close():
    server = GDBTCPServer(_FakeTarget(), port=0)
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        client.close()
    finally:
        server.close()
