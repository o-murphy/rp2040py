import struct
import time

import pytest

from rp2040py.device.kaluma_device import KalumaDevice
from rp2040py.device.load_flash import KALUMA_PROG_FLASH_START, KALUMA_PROG_MAX_SIZE, load_kaluma_program
from rp2040py.rp2040 import RP2040

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
    # exactly the "it never loads" scenario the timeout tests below exercise.
    path = tmp_path / "garbage.uf2"
    _write_minimal_uf2(path)
    return str(path)


def test_start_raises_timeout_error_instead_of_hanging_forever(garbage_image):
    device = KalumaDevice(garbage_image)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        device.start(timeout=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 5  # bounded failure, not a silent hang
    device.stop()


def test_second_start_call_raises_even_after_the_first_timed_out(garbage_image):
    device = KalumaDevice(garbage_image)
    with pytest.raises(TimeoutError):
        device.start(timeout=0.2)
    with pytest.raises(RuntimeError):
        device.start(timeout=0.2)
    device.stop()


def test_context_manager_calls_start_then_stop(garbage_image, monkeypatch):
    device = KalumaDevice(garbage_image)
    calls = []
    monkeypatch.setattr(device, "start", lambda timeout=30.0: calls.append("start"))
    monkeypatch.setattr(device, "stop", lambda: calls.append("stop"))

    with device:
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def test_littlefs_image_loaded_via_load_kaluma_flash_image(garbage_image, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "rp2040py.device.kaluma_device.load_kaluma_flash_image",
        lambda filename, rp2040: calls.append((filename, rp2040)),
    )

    device = KalumaDevice(garbage_image, littlefs="kaluma_littlefs.img")

    assert calls == [("kaluma_littlefs.img", device.mcu)]


def test_no_littlefs_image_by_default(garbage_image, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "rp2040py.device.kaluma_device.load_kaluma_flash_image",
        lambda filename, rp2040: calls.append((filename, rp2040)),
    )

    KalumaDevice(garbage_image)

    assert calls == []


def test_program_loaded_via_load_kaluma_program(garbage_image, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "rp2040py.device.kaluma_device.load_kaluma_program",
        lambda filename, rp2040: calls.append((filename, rp2040)),
    )

    device = KalumaDevice(garbage_image, program="index.js")

    assert calls == [("index.js", device.mcu)]


def test_no_program_by_default(garbage_image, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "rp2040py.device.kaluma_device.load_kaluma_program",
        lambda filename, rp2040: calls.append((filename, rp2040)),
    )

    KalumaDevice(garbage_image)

    assert calls == []


def test_load_kaluma_program_writes_source_plus_nul_terminator_at_the_prog_region(tmp_path):
    script = tmp_path / "index.js"
    script.write_text('console.log("hi");')
    rp2040 = RP2040()

    load_kaluma_program(str(script), rp2040)

    written = rp2040.flash[KALUMA_PROG_FLASH_START : KALUMA_PROG_FLASH_START + len(b'console.log("hi");') + 1]
    assert written == b'console.log("hi");\x00'


def test_load_kaluma_program_rejects_a_source_too_large_for_the_prog_region(tmp_path):
    script = tmp_path / "huge.js"
    script.write_bytes(b"x" * KALUMA_PROG_MAX_SIZE)  # +1 for the NUL terminator tips it over
    rp2040 = RP2040()

    with pytest.raises(ValueError, match="exceeds Kaluma's"):
        load_kaluma_program(str(script), rp2040)
