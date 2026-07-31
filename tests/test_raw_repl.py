import pytest

from rp2040py.device.raw_repl import CTRL_A, CTRL_C, CTRL_D, RawReplError, RawReplRunner

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"


def _runner(source: bytes = b"print(1)") -> tuple[RawReplRunner, list[int]]:
    sent: list[int] = []
    return RawReplRunner(source, sent.append), sent


def test_start_sends_interrupt_then_enter_raw_repl():
    runner, sent = _runner()
    runner.start()
    assert sent == [CTRL_C, CTRL_C, CTRL_A]


def test_full_protocol_success():
    runner, sent = _runner(b"print(1 + 1)")
    runner.start()
    sent.clear()

    runner.feed(RAW_REPL_BANNER)
    # Source is pasted and executed as soon as the banner is recognized.
    assert bytes(sent) == b"print(1 + 1)" + bytes([CTRL_D])

    runner.feed(b"OK")
    assert runner.result is None
    runner.feed(b"2\r\n")
    assert runner.result is None
    runner.feed(bytes([CTRL_D]))  # end of stdout
    assert runner.result is None
    runner.feed(bytes([CTRL_D]))  # end of stderr (empty - no error)
    assert runner.result == (b"2\r\n", b"")


def test_stderr_captured_on_error():
    runner, _sent = _runner(b"raise ValueError('boom')")
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
    runner, sent = _runner(b"print(1)")
    runner.start()
    sent.clear()

    runner.feed(b"MicroPython v1.21.0 on 2023-10-05\r\n>>> ")
    assert sent == []  # source not sent yet - this wasn't the raw-REPL banner
    assert runner.result is None

    runner.feed(RAW_REPL_BANNER)
    assert bytes(sent) == b"print(1)" + bytes([CTRL_D])


def test_byte_by_byte_feeding_matches_chunked_feeding():
    source = b"print('hi')"
    stream = RAW_REPL_BANNER + b"OK" + b"hi\r\n" + bytes([CTRL_D]) + b"" + bytes([CTRL_D])

    chunked, _sent = _runner(source)
    chunked.start()
    chunked.feed(stream)

    byte_by_byte, _sent = _runner(source)
    byte_by_byte.start()
    for byte in stream:
        byte_by_byte.feed(bytes([byte]))

    assert chunked.result == byte_by_byte.result == (b"hi\r\n", b"")


def test_malformed_ok_ack_raises_raw_repl_error():
    runner, _sent = _runner()
    runner.start()
    runner.feed(RAW_REPL_BANNER)
    with pytest.raises(RawReplError):
        runner.feed(b"XY")


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
    in_flight = 0
    sent = bytearray()

    def send_byte(byte: int) -> None:
        nonlocal in_flight
        sent.append(byte)
        in_flight += 1

    def free_space() -> int:
        return capacity - in_flight

    source = b"print(1 + 1)" * 10  # far bigger than `capacity`
    runner = RawReplRunner(source, send_byte, free_space)
    runner.start()
    sent.clear()
    in_flight = 0  # pretend the 3 start() control bytes have already been consumed

    runner.feed(RAW_REPL_BANNER)
    # Only as much as currently fits went out - not the whole burst at once.
    assert len(sent) == capacity

    previous_len = len(sent)
    while not runner.pump():
        assert len(sent) - previous_len <= capacity  # never oversends past free_space() in one call
        previous_len = len(sent)
        in_flight = 0  # the "device" finishes consuming what's in flight between attempts

    assert bytes(sent) == source + bytes([CTRL_D])
