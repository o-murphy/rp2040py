import asyncio
import socket
import struct
import time

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.gdb.gdb_utils import decode_hex_buf, decode_hex_uint32, gdb_message
from rp2040py.simulator import Simulator


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

    async def acall(self, coro):
        # None of these tests send any data (they exercise socket lifecycle only), so
        # _handle_connection() never actually calls this - present purely to satisfy IGDBTarget's
        # Protocol (checked structurally wherever _FakeTarget() is passed as `target`).
        return await coro


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


def test_close_does_not_hang_forever_if_aclose_stalls():
    """Reproduces the real ~6-hour CI hang this fix closes (macOS + free-threaded Python build):
    `_aclose()` timing out used to make `.result(timeout)` raise inside close(), which skipped
    `loop.stop()`/`_loop_thread.join()` entirely - leaving this deliberately **non-daemon** thread
    (see GDBTCPServer.__init__'s own NOTE on why) running forever, blocking the whole process from
    ever exiting. close() must still stop the loop and join the thread even when `_aclose()`
    itself never finishes."""
    server = GDBTCPServer(_FakeTarget(), port=0)
    thread = server._loop_thread

    async def _stuck_aclose() -> None:
        await asyncio.sleep(10)

    server._aclose = _stuck_aclose

    t0 = time.monotonic()
    server.close(timeout=0.3)
    elapsed = time.monotonic() - t0

    # Bounded by close()'s own timeout (0.3s for _aclose() + 0.3s for the join), not _aclose()'s
    # 10s sleep - the whole point of this test.
    assert elapsed < 3.0
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_gdb_client_can_still_connect_before_close():
    server = GDBTCPServer(_FakeTarget(), port=0)
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        client.close()
    finally:
        server.close()


def _recv_gdb_packet(sock: socket.socket) -> str:
    """Reads until one full "$...#xx" GDB packet has arrived and returns its payload (the part
    between "$" and "#") - ignoring any leading '+'/'-' ack bytes, including the one
    GDBConnection sends immediately on connect, before the request/response pair these tests
    actually care about. Not a general GDB client - just enough for these tests' single
    request-then-full-reply exchanges."""
    buf = b""
    while b"$" not in buf or b"#" not in buf.split(b"$", 1)[1]:
        buf += sock.recv(4096)
    text = buf.decode("utf-8")
    packet = text[text.index("$") :]
    return packet[1 : packet.index("#")]


def test_g_command_reads_real_register_state_through_the_bridge():
    """The bug this phase fixes: GDBServer.process_gdb_message() (invoked from feed_data(), called
    from _handle_connection() on GDBTCPServer's own engine-room thread) reads core.registers
    directly - it must run bridged onto the *target's* engine room (target.acall()), not
    GDBTCPServer's own, since Simulator.execute() could be mutating that same state concurrently
    on a different thread. A real Simulator (not _FakeTarget) is required to catch a bridge that
    silently no-ops - _FakeTarget's rp2040 is never actually driven."""
    simulator = Simulator()
    simulator.rp2040.core.registers[0] = 0x11223344
    server = GDBTCPServer(simulator, port=0)
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        try:
            client.sendall(gdb_message("g").encode("utf-8"))
            registers = struct.unpack("<17I", decode_hex_buf(_recv_gdb_packet(client)))
        finally:
            client.close()
    finally:
        server.close()

    assert registers[0] == 0x11223344


def test_register_write_then_read_round_trips_through_the_bridge():
    """Two bridged process_gdb_message() calls (a "P" write, then a "p" read) in sequence, over
    the same connection - confirms bridging via target.acall() per feed_data() call didn't break
    command ordering within (or across) reads."""
    simulator = Simulator()
    server = GDBTCPServer(simulator, port=0)
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        try:
            client.sendall(gdb_message("P3=44332211").encode("utf-8"))
            assert _recv_gdb_packet(client) == "OK"

            client.sendall(gdb_message("p3").encode("utf-8"))
            value = decode_hex_uint32(_recv_gdb_packet(client))
        finally:
            client.close()
    finally:
        server.close()

    assert value == 0x11223344
