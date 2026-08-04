"""Integration coverage for Simulator's shutdown coordinator: ShutdownRequest +
Simulator.wait_for_shutdown() + a real GDBTCPServer together - the actual failure mode this
exists to prevent is a plain sys.exit() hanging forever on GDBTCPServer's deliberately non-daemon
accept thread (see its own docstring), which unit tests for each piece in isolation wouldn't
catch."""

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
    """The actual regression this whole coordinator exists to prevent: a real GDBTCPServer's
    accept thread is non-daemon, so without closing it first, sys.exit() below would hang the
    test (and the real CLI) forever instead of returning."""
    simulator = Simulator()
    gdb_server = GDBTCPServer(_FakeTarget(), port=0)
    simulator.shutdown_request.request(0)

    try:
        with pytest.raises(SystemExit) as exc_info:
            simulator.wait_for_shutdown(cleanup=gdb_server.close)
        assert exc_info.value.code == 0
        assert not gdb_server._accept_thread.is_alive()
    finally:
        gdb_server.close()  # no-op if the test body already closed it - idempotent by design
