import asyncio
import dataclasses
import struct
import threading
import time

import pytest

from rp2040py.boards import BOARDS, BoardSpec
from rp2040py.device.base_device import ResetCause
from rp2040py.device.mp_device import DEFAULT_TIMEOUT, MicroPythonDevice
from rp2040py.device.raw_repl import RawReplError

UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
FLASH_START_ADDRESS = 0x10000000


def _write_minimal_uf2(path, payload: bytes = b"\x00\x00\x00\x00") -> None:
    header = struct.pack("<8I", UF2_MAGIC_START0, UF2_MAGIC_START1, 0, FLASH_START_ADDRESS, len(payload), 0, 1, 0)
    block = header + payload + b"\x00" * (512 - 32 - len(payload) - 4) + struct.pack("<I", UF2_MAGIC_END)
    assert len(block) == 512
    path.write_bytes(block)


def _pico_board(image) -> BoardSpec:
    return dataclasses.replace(BOARDS["pico"], image=image)


@pytest.fixture
def garbage_image(tmp_path) -> str:
    # Not real firmware - just enough to satisfy load_uf2()'s block framing. Its payload never
    # implements a USB stack, so a device booted from it never enumerates over USB; that's
    # exactly the "it never loads" scenario the timeout/ordering tests below exercise.
    path = tmp_path / "garbage.uf2"
    _write_minimal_uf2(path)
    return str(path)


def test_exec_before_start_raises(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    with pytest.raises(RuntimeError):
        device.exec_async("1")


def test_start_raises_timeout_error_instead_of_hanging_forever(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        device.start_async(timeout=0.3).result(timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed < 5  # bounded failure, not a silent hang
    device.stop()


def test_second_start_call_raises_even_after_the_first_timed_out(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    with pytest.raises(TimeoutError):
        device.start_async(timeout=0.2).result(timeout=5)
    with pytest.raises(RuntimeError):
        device.start_async(timeout=0.2)
    device.stop()


def test_context_manager_calls_start_then_stop(garbage_image, monkeypatch):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    calls = []

    async def _fake_astart(timeout=DEFAULT_TIMEOUT) -> None:
        calls.append("start")

    monkeypatch.setattr(device, "astart", _fake_astart)
    monkeypatch.setattr(device, "stop", lambda: calls.append("stop"))

    async def _body() -> None:
        async with device:
            assert calls == ["start"]
        assert calls == ["start", "stop"]

    asyncio.run(_body())


def _pretend_started(device: MicroPythonDevice) -> None:
    # exec()'s only precondition check is "has start() been called" (self._started) - poke it
    # directly so exec()'s own logic can be tested by driving device.cdc (a public attribute) as
    # a stand-in for real firmware, without paying for an actual firmware boot.
    device._started = True


def _reply(device: MicroPythonDevice, *chunks: bytes) -> None:
    # cdc.on_serial_data must fire on the Simulator's own engine-room thread in real usage (that's
    # the whole point of phase 5 - see mp_device.py's module docstring): _aexec()'s `done` is a
    # plain asyncio.Event now, not a threading.Event, and calling .set() on it from a foreign
    # thread doesn't reliably wake an awaiter on the engine-room loop. Bridged via simulator.call()
    # (built in PR 1) so these synthetic device replies match that invariant instead of calling
    # on_serial_data from this thread directly.
    async def _send() -> None:
        for chunk in chunks:
            if device.cdc.on_serial_data is not None:
                device.cdc.on_serial_data(chunk)

    device.simulator.call(_send())


def test_exec_blocks_until_the_device_responds_and_returns_its_output(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    _pretend_started(device)

    def _fake_device_replies() -> None:
        # The exact byte sequence real MicroPython/CircuitPython send back for the raw-REPL
        # protocol, delivered the same way USBCDC's real endpoint dispatch would: by calling
        # on_serial_data. Runs on its own thread (bridged into the engine room via _reply()) to
        # simulate whatever's driving the Simulator.
        while device.cdc.on_serial_data is None:
            # exec() (called concurrently below) hasn't registered its handler yet.
            time.sleep(0.001)
        _reply(device, b"raw REPL; CTRL-B to exit\r\n>", b"OK", b"4\r\n", bytes([4]), bytes([4]))

    threading.Thread(target=_fake_device_replies).start()
    stdout, stderr = device.exec_async("print(2 + 2)", timeout=5).result(timeout=5)
    assert (stdout, stderr) == (b"4\r\n", b"")


def test_exec_raises_timeout_error_if_the_device_never_responds(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    _pretend_started(device)
    with pytest.raises(TimeoutError):
        device.exec_async("1", timeout=0.3).result(timeout=5)


def _serve_queued_execs(device: MicroPythonDevice, replies: "list[bytes]") -> None:
    # Answers each exec in turn, only once exec_async() has actually dequeued and started it
    # (i.e. registered a fresh on_serial_data handler for it) - mirrors real firmware only ever
    # answering whichever request currently owns the REPL, one at a time.
    last_handler = None
    for reply in replies:
        while device.cdc.on_serial_data is last_handler:
            time.sleep(0.001)
        # Capture *before* replying, not after: the final on_serial_data call below can
        # cascade all the way into starting the next queued exec on the engine-room loop (its
        # done-callback releases the repl lock as part of the same reply) - reading on_serial_data
        # afterwards would already see that next handler, not the one we're about to finish serving.
        handler_being_served = device.cdc.on_serial_data
        _reply(device, b"raw REPL; CTRL-B to exit\r\n>", b"OK", reply, bytes([4]), bytes([4]))
        last_handler = handler_being_served


def test_overlapping_exec_async_calls_queue_and_run_in_order(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    _pretend_started(device)
    replies = [b"1\r\n", b"2\r\n", b"3\r\n"]

    threading.Thread(target=_serve_queued_execs, args=(device, replies)).start()

    # All three are issued back-to-back, well before the first has a reply - exec_async() must
    # not raise or block for #2/#3 just because #1 is still in flight.
    futures = [device.exec_async(f"print({i})") for i in (1, 2, 3)]

    assert [f.result(timeout=5)[0] for f in futures] == replies


def test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    _pretend_started(device)

    def _serve() -> None:
        last_handler = None

        def _next_handler():
            nonlocal last_handler
            while device.cdc.on_serial_data is last_handler:
                time.sleep(0.001)
            last_handler = device.cdc.on_serial_data

        _next_handler()
        _reply(device, b"raw REPL; CTRL-B to exit\r\n>", b"OK", b"1\r\n", bytes([4]), bytes([4]))

        _next_handler()  # 2nd exec: malformed ack -> RawReplError, shouldn't wedge the 3rd
        _reply(device, b"raw REPL; CTRL-B to exit\r\n>", b"XY")

        _next_handler()
        _reply(device, b"raw REPL; CTRL-B to exit\r\n>", b"OK", b"3\r\n", bytes([4]), bytes([4]))

    threading.Thread(target=_serve).start()

    first, second, third = (device.exec_async(f"print({i})") for i in (1, 2, 3))
    assert first.result(timeout=5) == (b"1\r\n", b"")
    with pytest.raises(RawReplError):
        second.result(timeout=5)
    assert third.result(timeout=5) == (b"3\r\n", b"")


# -- post-boot handshake (docs/records/0089-one-reset-for-every-trigger.md, Phase 0.1) ----------
# Moved here from cli/__init__.py's `_micropython_async()`, which used to send these bytes itself
# after `astart()` returned: a device-level reset has to re-run the handshake and cannot reach
# into the CLI for it.


def _record_sent_bytes(device, monkeypatch) -> bytearray:
    sent = bytearray()
    monkeypatch.setattr(device.cdc, "send_serial_byte", sent.append)
    return sent


def test_post_boot_handshake_sends_a_newline_for_micropython(garbage_image, monkeypatch):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    sent = _record_sent_bytes(device, monkeypatch)

    device._post_boot_handshake()

    assert bytes(sent) == b"\r\n"


def test_post_boot_handshake_sends_a_newline_for_circuitpython_too(garbage_image, monkeypatch):
    """Not Ctrl-C, even though the CLI used to send one here: measured on CircuitPython 8.0.2, a
    newline gets the same banner and prompt out of an idle REPL, while Ctrl-C additionally kills a
    running code.py (see `_post_boot_handshake()`'s own docstring)."""
    device = MicroPythonDevice(board=_pico_board(garbage_image), circuitpython=True)
    sent = _record_sent_bytes(device, monkeypatch)

    device._post_boot_handshake()

    assert bytes(sent) == b"\r\n"


def test_connect_runs_the_post_boot_handshake_after_enumeration(garbage_image, monkeypatch):
    """...and only after: the nudge is a reply to a device that's already on the bus, so sending it
    any earlier would drop it into a USBCDC that isn't initialized yet."""
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    sent = _record_sent_bytes(device, monkeypatch)

    def _fake_start_execution() -> None:
        assert bytes(sent) == b"", "handshake sent before the device enumerated"
        device.cdc.on_device_connected()

    monkeypatch.setattr(device.simulator, "start_execution", _fake_start_execution)

    asyncio.run(device._aconnect(timeout=5))

    assert bytes(sent) == b"\r\n"


# -- host-initiated hard reset (0089 Phase 2) ---------------------------------------------------
# No real firmware here: the garbage image never enumerates on its own, so these drive
# `on_device_connected` the same way the handshake tests above do. The live-boot half (both
# firmwares, a variable gone across the reset) is tests/hard_reset_run.py, run from CI.


def _fake_reenumerating_reset(device, monkeypatch) -> list[ResetCause]:
    """Stand in for the chip actually rebooting: record the cause, then fire the enumeration
    callback `_arm_enumeration()` wired up."""
    causes: list[ResetCause] = []

    def _hard_reset(*, cause=ResetCause.RUN_PIN) -> None:
        causes.append(cause)
        device.cdc.on_device_connected()

    monkeypatch.setattr(device, "hard_reset", _hard_reset)
    return causes


def test_hard_reset_async_before_start_raises(garbage_image):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    with pytest.raises(RuntimeError):
        device.hard_reset_async()


def test_ahard_reset_waits_for_re_enumeration_then_redoes_the_handshake(garbage_image, monkeypatch):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    causes = _fake_reenumerating_reset(device, monkeypatch)
    sent = _record_sent_bytes(device, monkeypatch)

    asyncio.run(device._ahard_reset(5, ResetCause.RUN_PIN))

    assert causes == [ResetCause.RUN_PIN]
    assert bytes(sent) == b"\r\n"  # the console the CLI used to nudge is usable again


def test_ahard_reset_defaults_to_the_run_pin_cause(garbage_image, monkeypatch):
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    causes = _fake_reenumerating_reset(device, monkeypatch)
    device._started = True

    device.hard_reset_async(timeout=5).result(timeout=5)

    assert causes == [ResetCause.RUN_PIN]
    device.stop()


def test_ahard_reset_raises_timeout_error_instead_of_hanging_forever(garbage_image, monkeypatch):
    """A device that never comes back must fail the wait, not leave the caller pending forever."""
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    monkeypatch.setattr(device, "hard_reset", lambda **_kwargs: None)  # never re-enumerates

    with pytest.raises(TimeoutError):
        asyncio.run(device._ahard_reset(0.3, ResetCause.RUN_PIN))


def test_a_reset_is_not_a_second_start(garbage_image, monkeypatch):
    """0089 §2.3: `_started` stays True across a reset - `start_async()` must keep raising."""
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    _fake_reenumerating_reset(device, monkeypatch)
    device._started = True

    device.hard_reset_async(timeout=5).result(timeout=5)

    assert device._started is True
    with pytest.raises(RuntimeError):
        device.start_async()
    device.stop()


def test_a_host_reset_queues_behind_whatever_holds_the_repl_lock(garbage_image, monkeypatch):
    """The device has one REPL channel: a reset must not interleave with an exec in flight."""
    device = MicroPythonDevice(board=_pico_board(garbage_image))
    causes = _fake_reenumerating_reset(device, monkeypatch)

    async def scenario() -> None:
        async with device._repl_lock:
            reset = asyncio.ensure_future(device._ahard_reset(5, ResetCause.RUN_PIN))
            await asyncio.sleep(0.05)
            assert causes == [], "reset ran while the REPL lock was held"
        await reset
        assert causes == [ResetCause.RUN_PIN]

    asyncio.run(scenario())
