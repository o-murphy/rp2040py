"""RPWatchdog.on_watchdog_trigger's real reset handler (BaseDevice._on_watchdog_trigger, wired up
in BaseDevice.__init__): a real `machine.reset()`/`machine.bootloader()` (mpremote's `reset`/
`bootloader` shortcuts) writes the watchdog's TRIGGER bit, which - before this handler existed -
just logged a warning and left the emulated CPU spinning forever waiting for a reset that never
happened. No real firmware needed here: these tests drive the watchdog register/USB-CDC state
directly, the same way tests/test_device.py's `garbage_image` fixture avoids booting real firmware
for its own timeout/ordering tests.
"""

import dataclasses
import struct

from rp2040py.boards import BOARDS
from rp2040py.device.mp_device import MicroPythonDevice
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.peripherals.watchdog import CTRL, TRIGGER

UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30


def _write_minimal_uf2(path, payload: bytes = b"\x00\x00\x00\x00") -> None:
    header = struct.pack("<8I", UF2_MAGIC_START0, UF2_MAGIC_START1, 0, FLASH_START_ADDRESS, len(payload), 0, 1, 0)
    block = header + payload + b"\x00" * (512 - 32 - len(payload) - 4) + struct.pack("<I", UF2_MAGIC_END)
    assert len(block) == 512
    path.write_bytes(block)


def _make_device(tmp_path) -> MicroPythonDevice:
    image = tmp_path / "garbage.uf2"
    _write_minimal_uf2(image, payload=b"\xab\xcd\xef\x01")
    board = dataclasses.replace(BOARDS["pico"], image=str(image))
    return MicroPythonDevice(board=board)


def _trigger_watchdog(device: MicroPythonDevice) -> None:
    device.mcu.watchdog.write_uint32(CTRL, TRIGGER)


def test_watchdog_trigger_resets_pc_to_the_flash_entry_point(tmp_path):
    device = _make_device(tmp_path)
    device.mcu.core.pc = 0x2000_1234  # simulate firmware having run for a while

    _trigger_watchdog(device)

    assert device.mcu.core.pc == FLASH_START_ADDRESS


def test_watchdog_trigger_preserves_flash_content(tmp_path):
    device = _make_device(tmp_path)
    flash_offset = 0x100
    device.mcu.flash[flash_offset] = 0x42

    _trigger_watchdog(device)

    assert device.mcu.flash[flash_offset] == 0x42


def test_watchdog_trigger_resets_usb_cdc_enumeration_state(tmp_path):
    device = _make_device(tmp_path)
    # Simulate a completed USB enumeration handshake, the way real firmware's boot drives it.
    device.cdc._initialized = True
    device.cdc._descriptors_size = 9
    device.cdc._descriptors = [1, 2, 3]
    device.cdc._in_endpoint = 2
    device.cdc._out_endpoint = 3
    device.cdc.tx_fifo.push(0xFF)

    _trigger_watchdog(device)

    assert device.cdc._initialized is False
    assert device.cdc._descriptors_size is None
    assert device.cdc._descriptors == []
    assert device.cdc._in_endpoint == -1
    assert device.cdc._out_endpoint == -1
    assert device.cdc.tx_fifo.item_count == 0


def test_watchdog_trigger_does_not_replace_the_usb_ctrl_object_cdc_holds(tmp_path):
    """USBCDC(mcu.usb_ctrl) captures a direct reference at construction time (see
    device/base_device.py) - the reset must reuse that same object in place, not reconstruct it,
    or device.cdc would end up silently wired to a stale, disconnected peripheral."""
    device = _make_device(tmp_path)
    usb_ctrl_before = device.mcu.usb_ctrl

    _trigger_watchdog(device)

    assert device.mcu.usb_ctrl is usb_ctrl_before
    assert device.cdc.usb is usb_ctrl_before


def test_watchdog_reason_register_reports_force_after_trigger(tmp_path):
    from rp2040py.peripherals.watchdog import FORCE, REASON

    device = _make_device(tmp_path)

    _trigger_watchdog(device)

    assert device.mcu.watchdog.read_uint32(REASON) == FORCE
