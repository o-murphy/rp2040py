import struct
import threading
import time

import pytest

from rp2040py.device import DEFAULT_TIMEOUT, MicroPythonDevice

UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
FLASH_START_ADDRESS = 0x10000000


def _write_minimal_uf2(path, payload: bytes = b"\x00\x00\x00\x00") -> None:
    header = struct.pack("<8I", UF2_MAGIC_START0, UF2_MAGIC_START1, 0, FLASH_START_ADDRESS, len(payload), 0, 1, 0)
    block = header + payload + b"\x00" * (512 - 32 - len(payload) - 4) + struct.pack("<I", UF2_MAGIC_END)
    assert len(block) == 512
    path.write_bytes(block)


@pytest.fixture
def garbage_image(tmp_path) -> str:
    # Not real firmware - just enough to satisfy load_uf2()'s block framing. Its payload never
    # implements a USB stack, so a device booted from it never enumerates over USB; that's
    # exactly the "it never loads" scenario the timeout/ordering tests below exercise.
    path = tmp_path / "garbage.uf2"
    _write_minimal_uf2(path)
    return str(path)


def test_exec_before_start_raises(garbage_image):
    device = MicroPythonDevice(garbage_image)
    with pytest.raises(RuntimeError):
        device.exec("1")


def test_start_raises_timeout_error_instead_of_hanging_forever(garbage_image):
    device = MicroPythonDevice(garbage_image)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        device.start(timeout=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 5  # bounded failure, not a silent hang
    device.stop()


def test_second_start_call_raises_even_after_the_first_timed_out(garbage_image):
    device = MicroPythonDevice(garbage_image)
    with pytest.raises(TimeoutError):
        device.start(timeout=0.2)
    with pytest.raises(RuntimeError):
        device.start(timeout=0.2)
    device.stop()


def test_context_manager_calls_start_then_stop(garbage_image, monkeypatch):
    device = MicroPythonDevice(garbage_image)
    calls = []
    monkeypatch.setattr(device, "start", lambda timeout=DEFAULT_TIMEOUT: calls.append("start"))
    monkeypatch.setattr(device, "stop", lambda: calls.append("stop"))

    with device:
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def _pretend_started(device: MicroPythonDevice) -> None:
    # exec()'s only precondition check is "has start() been called" (self._thread is not None) -
    # poke it directly so exec()'s own logic can be tested by driving device.cdc (a public
    # attribute) as a stand-in for real firmware, without paying for an actual firmware boot.
    device._thread = threading.Thread()


def test_exec_blocks_until_the_device_responds_and_returns_its_output(garbage_image):
    device = MicroPythonDevice(garbage_image)
    _pretend_started(device)

    def _fake_device_replies() -> None:
        # The exact byte sequence real MicroPython/CircuitPython send back for the raw-REPL
        # protocol, delivered the same way USBCDC's real endpoint dispatch would: by calling
        # on_serial_data. Runs on its own thread to simulate the Simulator worker thread.
        while device.cdc.on_serial_data is None:
            # exec() (called concurrently below) hasn't registered its handler yet.
            time.sleep(0.001)
        device.cdc.on_serial_data(b"raw REPL; CTRL-B to exit\r\n>")
        device.cdc.on_serial_data(b"OK")
        device.cdc.on_serial_data(b"4\r\n")
        device.cdc.on_serial_data(bytes([4]))
        device.cdc.on_serial_data(bytes([4]))

    threading.Thread(target=_fake_device_replies).start()
    stdout, stderr = device.exec("print(2 + 2)", timeout=5)
    assert (stdout, stderr) == (b"4\r\n", b"")


def test_exec_raises_timeout_error_if_the_device_never_responds(garbage_image):
    device = MicroPythonDevice(garbage_image)
    _pretend_started(device)
    with pytest.raises(TimeoutError):
        device.exec("1", timeout=0.3)
