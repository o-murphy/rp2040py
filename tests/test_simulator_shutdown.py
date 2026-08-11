"""Integration coverage for Simulator's shutdown coordinator: ShutdownRequest +
Simulator.wait_for_shutdown() + a real GDBTCPServer together - `wait_for_shutdown()` is the
old, still-supported "drive Simulator from a plain synchronous/background-thread caller" model
(docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "What still needs a real thread"), so its own `cleanup`
callback contract is still a plain synchronous callable - even though `GDBTCPServer` itself is
async-only now (see its own class docstring), a caller integrating the two like this is
responsible for bridging, the same way any other plain synchronous caller would be."""

import asyncio
import threading

import pytest

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.simulator import Simulator


class _FakeTarget:
    rp2040 = None


def test_shutdown_request_first_call_wins():
    simulator = Simulator()
    simulator.shutdown_request.request(1)
    simulator.shutdown_request.request(2)
    assert simulator.shutdown_request.code == 1


def test_each_simulator_owns_its_own_shutdown_request():
    a, b = Simulator(), Simulator()
    a.shutdown_request.request(5)
    assert not b.shutdown_request.event.is_set()


def test_wait_for_shutdown_exits_via_shutdown_request_and_runs_cleanup():
    simulator = Simulator()  # freshly constructed, never executed - .executing is False already
    simulator.shutdown_request.request(7)
    cleanup_calls = []

    with pytest.raises(SystemExit) as exc_info:
        simulator.wait_for_shutdown(cleanup=lambda: cleanup_calls.append(True))

    assert exc_info.value.code == 7
    assert cleanup_calls == [True]


def test_wait_for_shutdown_closes_a_real_gdb_server_instead_of_hanging():
    """The actual regression this whole coordinator exists to prevent: `cleanup` must actually run
    (and actually close the server's listening socket) before sys.exit() below, not get skipped or
    silently dropped - which is exactly what passing `gdb_server.close` (now a coroutine function)
    directly as `cleanup` would do, since `wait_for_shutdown()` calls it as a plain synchronous
    callable and would just create-and-discard the coroutine without ever awaiting it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        simulator = Simulator()
        gdb_server = GDBTCPServer(_FakeTarget(), port=0)
        asyncio.run_coroutine_threadsafe(gdb_server.start(), loop).result(timeout=5.0)
        simulator.shutdown_request.request(0)

        def _sync_close() -> None:
            asyncio.run_coroutine_threadsafe(gdb_server.close(), loop).result(timeout=5.0)

        try:
            with pytest.raises(SystemExit) as exc_info:
                simulator.wait_for_shutdown(cleanup=_sync_close)
            assert exc_info.value.code == 0
            assert gdb_server.port is None
        finally:
            _sync_close()  # no-op if the test body already closed it - idempotent by design
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()
