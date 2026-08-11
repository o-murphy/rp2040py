import asyncio

import pytest

from rp2040py.device.repl_runner import BaseReplRunner, InteractiveRepl


class _FakeFifo:
    def __init__(self, size: int) -> None:
        self.size = size
        self.item_count = 0


class _FakeCdc:
    def __init__(self, size: int = 1_000_000) -> None:
        self.tx_fifo = _FakeFifo(size)
        self.sent = bytearray()
        self.on_serial_data = None

    def send_serial_byte(self, byte: int) -> None:
        self.sent.append(byte)
        self.tx_fifo.item_count += 1


class _RaisingRunner(BaseReplRunner):
    def feed(self, data):
        raise ValueError("boom")


def test_start_wires_and_stop_unwires_on_serial_data():
    cdc = _FakeCdc()
    runner = InteractiveRepl(cdc, on_data=lambda data: None)
    assert cdc.on_serial_data is None
    asyncio.run(runner.start())
    assert cdc.on_serial_data is not None
    asyncio.run(runner.stop())
    assert cdc.on_serial_data is None


def test_feed_safe_forwards_exception_to_on_error():
    cdc = _FakeCdc()
    errors = []
    runner = _RaisingRunner(cdc, on_error=errors.append)
    asyncio.run(runner.start())
    cdc.on_serial_data(b"x")
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_feed_safe_reraises_when_no_on_error_given():
    cdc = _FakeCdc()
    runner = _RaisingRunner(cdc)
    asyncio.run(runner.start())
    with pytest.raises(ValueError):
        cdc.on_serial_data(b"x")


def test_queue_and_pump_paces_by_free_space():
    cdc = _FakeCdc(size=4)
    runner = InteractiveRepl(cdc, on_data=lambda data: None)
    runner._queue(b"abcdefgh")

    assert runner.pump() is False
    assert bytes(cdc.sent) == b"abcd"

    cdc.tx_fifo.item_count = 0  # the "device" consumes what's in flight
    assert runner.pump() is True
    assert bytes(cdc.sent) == b"abcdefgh"


def test_interactive_repl_feed_forwards_to_on_data():
    received = []
    cdc = _FakeCdc()
    runner = InteractiveRepl(cdc, on_data=received.append)
    runner.feed(b"hello")
    assert received == [b"hello"]


def test_interactive_repl_send_queues_and_paces_via_pump():
    cdc = _FakeCdc(size=2)
    runner = InteractiveRepl(cdc, on_data=lambda data: None)

    # Only 2 bytes' worth of room - send() queues all 4 but can only pump what fits right now,
    # same "call pump() again while it returns False" contract as pump() itself.
    assert runner.send(b"abcd") is False
    assert bytes(cdc.sent) == b"ab"

    cdc.tx_fifo.item_count = 0  # the "device" consumes what's in flight
    assert runner.pump() is True
    assert bytes(cdc.sent) == b"abcd"
