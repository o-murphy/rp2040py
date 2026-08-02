import pytest

from rp2040py.device.raw_repl import CTRL_A, CTRL_C, CTRL_D, RawReplError, RawReplRunner

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"


class _FakeFifo:
    def __init__(self, size: int) -> None:
        self.size = size
        self.item_count = 0


class _FakeCdc:
    """Minimal stand-in for USBCDC's public surface that BaseReplRunner needs - a `tx_fifo` with
    mutable `size`/`item_count`, `send_serial_byte()`, and a settable `on_serial_data`."""

    def __init__(self, size: int = 1_000_000) -> None:
        self.tx_fifo = _FakeFifo(size)
        self.sent = bytearray()
        self.on_serial_data = None

    def send_serial_byte(self, byte: int) -> None:
        self.sent.append(byte)
        self.tx_fifo.item_count += 1


def _runner(source: bytes = b"print(1)", **kwargs) -> "tuple[RawReplRunner, _FakeCdc]":
    cdc = _FakeCdc()
    return RawReplRunner(cdc, source, **kwargs), cdc


def test_start_sends_interrupt_then_enter_raw_repl():
    runner, cdc = _runner()
    runner.start()
    assert bytes(cdc.sent) == bytes([CTRL_C, CTRL_C, CTRL_A])


def test_full_protocol_success():
    runner, cdc = _runner(b"print(1 + 1)")
    runner.start()
    cdc.sent.clear()

    runner.feed(RAW_REPL_BANNER)
    # Source is pasted and executed as soon as the banner is recognized.
    assert bytes(cdc.sent) == b"print(1 + 1)" + bytes([CTRL_D])

    runner.feed(b"OK")
    assert runner.result is None
    runner.feed(b"2\r\n")
    assert runner.result is None
    runner.feed(bytes([CTRL_D]))  # end of stdout
    assert runner.result is None
    runner.feed(bytes([CTRL_D]))  # end of stderr (empty - no error)
    assert runner.result == (b"2\r\n", b"")


def test_on_result_called_when_exec_completes():
    results: list[tuple[bytes, bytes]] = []
    runner, _cdc = _runner(b"print(1)", on_result=results.append)
    runner.start()
    runner.feed(RAW_REPL_BANNER)
    runner.feed(b"OK")
    runner.feed(b"1\r\n")
    runner.feed(bytes([CTRL_D]))
    runner.feed(bytes([CTRL_D]))
    assert results == [(b"1\r\n", b"")]


def test_stderr_captured_on_error():
    runner, _cdc = _runner(b"raise ValueError('boom')")
    runner.start()
    runner.feed(RAW_REPL_BANNER)
    runner.feed(b"OK")
    runner.feed(bytes([CTRL_D]))  # empty stdout
    traceback = b'Traceback (most recent call last):\r\n  File "<stdin>", line 1\r\nValueError: boom\r\n'
    runner.feed(traceback)
    runner.feed(bytes([CTRL_D]))
    assert runner.result == (b"", traceback)


def test_ignores_a_lookalike_prompt_before_the_real_banner():
    # The friendly REPL's own idle prompt ("\r\n>>> ") also contains ">" - if that arrives (e.g.
    # from the normal boot banner racing with our Ctrl-A) it must not be mistaken for raw-REPL
    # readiness and trigger sending the source early.
    runner, cdc = _runner(b"print(1)")
    runner.start()
    cdc.sent.clear()

    runner.feed(b"MicroPython v1.21.0 on 2023-10-05\r\n>>> ")
    assert cdc.sent == b""  # source not sent yet - this wasn't the raw-REPL banner
    assert runner.result is None

    runner.feed(RAW_REPL_BANNER)
    assert bytes(cdc.sent) == b"print(1)" + bytes([CTRL_D])


def test_byte_by_byte_feeding_matches_chunked_feeding():
    source = b"print('hi')"
    stream = RAW_REPL_BANNER + b"OK" + b"hi\r\n" + bytes([CTRL_D]) + b"" + bytes([CTRL_D])

    chunked, _cdc = _runner(source)
    chunked.start()
    chunked.feed(stream)

    byte_by_byte, _cdc = _runner(source)
    byte_by_byte.start()
    for byte in stream:
        byte_by_byte.feed(bytes([byte]))

    assert chunked.result == byte_by_byte.result == (b"hi\r\n", b"")


def test_malformed_ok_ack_raises_raw_repl_error():
    runner, _cdc = _runner()
    runner.start()
    runner.feed(RAW_REPL_BANNER)
    with pytest.raises(RawReplError):
        runner.feed(b"XY")


def test_feed_via_cdc_wiring_reports_protocol_errors_via_on_error_not_raise():
    # feed() itself still raises (asserted above) - it's _feed_safe(), wired as cdc.on_serial_data
    # by start(), that must catch instead of letting the exception propagate into whatever's
    # driving the simulator.
    errors: list[Exception] = []
    runner, cdc = _runner(on_error=errors.append)
    runner.start()
    cdc.on_serial_data(RAW_REPL_BANNER)
    cdc.on_serial_data(b"XY")
    assert len(errors) == 1
    assert isinstance(errors[0], RawReplError)


def test_stop_unwires_on_serial_data():
    runner, cdc = _runner()
    runner.start()
    assert cdc.on_serial_data is not None
    runner.stop()
    assert cdc.on_serial_data is None


def test_pump_paces_uploads_larger_than_the_send_buffer():
    # Regression test: feed() used to push the whole source into send_byte in one synchronous
    # burst regardless of how much room the receiving end actually had. Against a real USBCDC,
    # that end is a fixed-size FIFO (512 bytes) that silently drops anything past capacity instead
    # of raising or blocking - so any source bigger than the FIFO lost everything past that point,
    # *including the terminating Ctrl-D*, leaving the device waiting forever for an end-of-paste
    # marker it never received. Confirmed against real firmware: a 440-byte script ran fine, an
    # otherwise-identical 890-byte one hung indefinitely with zero output. pump() must only ever
    # send what currently fits, relying on repeated calls (as the receiving buffer drains) to get
    # the rest out - never one unbounded burst.
    capacity = 8
    cdc = _FakeCdc(size=capacity)
    source = b"print(1 + 1)" * 10  # far bigger than `capacity`
    runner = RawReplRunner(cdc, source)
    runner.start()
    cdc.sent.clear()
    cdc.tx_fifo.item_count = 0  # pretend the 3 start() control bytes have already been consumed

    runner.feed(RAW_REPL_BANNER)
    # Only as much as currently fits went out - not the whole burst at once.
    assert len(cdc.sent) == capacity

    previous_len = len(cdc.sent)
    while not runner.pump():
        assert len(cdc.sent) - previous_len <= capacity  # never oversends past free space in one call
        previous_len = len(cdc.sent)
        cdc.tx_fifo.item_count = 0  # the "device" finishes consuming what's in flight between attempts

    assert bytes(cdc.sent) == source + bytes([CTRL_D])
