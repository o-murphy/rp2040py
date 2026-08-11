import asyncio
import socket
import struct

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
    a handle_connection task that touches target.rp2040 via GDBServer.add_connection."""

    rp2040 = _FakeRP2040()


def _run_test(body) -> None:
    """Runs an async test body via `asyncio.run()`: `GDBTCPServer` now runs its listening socket
    directly on whatever loop `start()`/`close()` are awaited from (docs/
    MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") instead of a separate dedicated thread - see
    its own class docstring."""
    asyncio.run(body())


def test_close_is_idempotent():
    async def _body() -> None:
        server = GDBTCPServer(_FakeTarget(), port=0)
        await server.start()
        await server.close()
        await server.close()  # must not raise

    _run_test(_body)


def test_close_before_start_does_not_raise():
    async def _body() -> None:
        server = GDBTCPServer(_FakeTarget(), port=0)
        await server.close()  # never started - must not raise

    _run_test(_body)


def test_gdb_client_can_still_connect_before_close():
    async def _body() -> None:
        server = GDBTCPServer(_FakeTarget(), port=0)
        await server.start()
        try:
            client = await asyncio.to_thread(socket.create_connection, ("127.0.0.1", server.port), 2.0)
            await asyncio.to_thread(client.close)
        finally:
            await server.close()

    _run_test(_body)


def test_stop_closes_the_listening_socket():
    async def _body() -> None:
        server = GDBTCPServer(_FakeTarget(), port=0)
        await server.start()
        port = server.port
        await server.close()

        assert server.port is None
        try:
            await asyncio.to_thread(socket.create_connection, ("127.0.0.1", port), 0.5)
        except OSError:
            pass
        else:
            raise AssertionError("expected the listening socket to be closed after close()")

    _run_test(_body)


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


def test_g_command_reads_real_register_state():
    """`GDBServer.process_gdb_message()` (invoked from `feed_data()`, called from
    `_handle_connection()`) reads `core.registers` directly - now safe with no bridging needed
    since both `execute()` and the connection handler run as plain coroutines on one shared loop
    (see GDBTCPServer's own class docstring). A real Simulator (not _FakeTarget) is required here:
    _FakeTarget's rp2040 is never actually driven."""

    async def _body() -> None:
        simulator = Simulator()
        simulator.rp2040.core.registers[0] = 0x11223344
        server = GDBTCPServer(simulator, port=0)
        await server.start()
        try:
            client = await asyncio.to_thread(socket.create_connection, ("127.0.0.1", server.port), 2.0)
            try:
                await asyncio.to_thread(client.sendall, gdb_message("g").encode("utf-8"))
                registers = struct.unpack("<17I", decode_hex_buf(await asyncio.to_thread(_recv_gdb_packet, client)))
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await server.close()

        assert registers[0] == 0x11223344

    _run_test(_body)


def test_register_write_then_read_round_trips():
    """Two `process_gdb_message()` calls (a "P" write, then a "p" read) in sequence, over the same
    connection - confirms command ordering within (or across) reads still holds now that nothing
    bridges each message onto a separate thread anymore."""

    async def _body() -> None:
        simulator = Simulator()
        server = GDBTCPServer(simulator, port=0)
        await server.start()
        try:
            client = await asyncio.to_thread(socket.create_connection, ("127.0.0.1", server.port), 2.0)
            try:
                await asyncio.to_thread(client.sendall, gdb_message("P3=44332211").encode("utf-8"))
                assert await asyncio.to_thread(_recv_gdb_packet, client) == "OK"

                await asyncio.to_thread(client.sendall, gdb_message("p3").encode("utf-8"))
                value = decode_hex_uint32(await asyncio.to_thread(_recv_gdb_packet, client))
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await server.close()

        assert value == 0x11223344

    _run_test(_body)
