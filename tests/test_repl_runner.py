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
    runner.start()
    assert cdc.on_serial_data is not None
    runner.stop()
    assert cdc.on_serial_data is None


def test_feed_safe_forwards_exception_to_on_error():
    cdc = _FakeCdc()
    errors = []
    runner = _RaisingRunner(cdc, on_error=errors.append)
    runner.start()
    cdc.on_serial_data(b"x")
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_feed_safe_reraises_when_no_on_error_given():
    cdc = _FakeCdc()
    runner = _RaisingRunner(cdc)
    runner.start()
    with pytest.raises(ValueError):
        cdc.on_serial_data(b"x")


def test_send_byte_blocking_waits_for_fifo_room(monkeypatch):
    cdc = _FakeCdc(size=1)
    cdc.tx_fifo.item_count = 1  # full
    runner = InteractiveRepl(cdc, on_data=lambda data: None)

    sleeps: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        cdc.tx_fifo.item_count = 0  # pretend the device drained it

    monkeypatch.setattr("rp2040py.device.repl_runner.time.sleep", _fake_sleep)
    runner._send_byte_blocking(ord("a"))
    assert sleeps
    assert bytes(cdc.sent) == b"a"


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


def test_interactive_repl_send_paces_via_blocking_backpressure(monkeypatch):
    cdc = _FakeCdc(size=2)
    runner = InteractiveRepl(cdc, on_data=lambda data: None)

    def _fake_sleep(seconds: float) -> None:
        cdc.tx_fifo.item_count = 0

    monkeypatch.setattr("rp2040py.device.repl_runner.time.sleep", _fake_sleep)
    runner.send(b"abcd")
    assert bytes(cdc.sent) == b"abcd"
