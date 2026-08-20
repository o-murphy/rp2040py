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
from rp2040py.device.base_device import ResetCause
from rp2040py.device.mp_device import MicroPythonDevice
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.peripherals.vreg_and_chip_reset import (
    CHIP_RESET,
    HAD_POR,
    HAD_RUN,
    PSM_RESTART_FLAG,
)
from rp2040py.peripherals.watchdog import CTRL, FORCE, REASON, SCRATCH4, TIMER, TRIGGER

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


def test_hard_reset_called_directly_runs_the_same_sequence(tmp_path):
    """The watchdog hook is one caller of `BaseDevice.hard_reset()`, not the only way to reach it
    (docs/records/0089-one-reset-for-every-trigger.md, Phase 0.2) - a RUN pin/RESET button and a
    host-side API call are the others, and they must get this exact sequence rather than their own
    variant of it."""
    device = _make_device(tmp_path)
    device.mcu.core.pc = 0x2000_1234
    device.mcu.flash[0x100] = 0x42
    device.cdc._initialized = True
    usb_ctrl_before = device.mcu.usb_ctrl

    device.hard_reset()

    assert device.mcu.core.pc == FLASH_START_ADDRESS
    assert device.mcu.flash[0x100] == 0x42
    assert device.cdc._initialized is False
    assert device.mcu.usb_ctrl is usb_ctrl_before


# -- reset cause (docs/records/0089-one-reset-for-every-trigger.md §1.3, Phase 1) ---------------
# The table every trigger has to satisfy, because both firmwares expose it to user code
# (`machine.reset_cause()` on MicroPython, `microcontroller.cpu.reset_reason` on CircuitPython).
# WATCHDOG_MAGIC is what pico-sdk's `watchdog_enable()` writes to SCRATCH[4] and
# `watchdog_enable_caused_reboot()` checks, i.e. how CircuitPython tells a timeout (WATCHDOG) from
# a deliberate `watchdog_reboot()` (SOFTWARE) - both of which set REASON.
WATCHDOG_MAGIC = 0x6AB73121


def _chip_reset(device: MicroPythonDevice) -> int:
    return device.mcu.vreg_and_chip_reset.read_uint32(CHIP_RESET)


def test_a_fresh_device_reads_as_a_power_on_reset(tmp_path):
    device = _make_device(tmp_path)

    assert device.mcu.watchdog.read_uint32(REASON) == 0
    assert device.mcu.watchdog.scratch_data == [0] * 8
    assert _chip_reset(device) == HAD_POR


def test_watchdog_reset_keeps_its_own_reason_and_scratch_and_leaves_chip_reset_alone(tmp_path):
    """The watchdog block is not reset by a watchdog reboot on real silicon - that is what makes
    REASON readable afterwards at all, and what keeps watchdog_enable()'s magic alive long enough
    to distinguish a timeout from a deliberate reboot."""
    device = _make_device(tmp_path)
    device.mcu.watchdog.scratch_data[4] = WATCHDOG_MAGIC

    _trigger_watchdog(device)

    assert device.mcu.watchdog.read_uint32(REASON) == FORCE
    assert device.mcu.watchdog.read_uint32(SCRATCH4) == WATCHDOG_MAGIC
    assert _chip_reset(device) == HAD_POR  # unchanged - a watchdog reset does not touch it


def test_watchdog_timeout_reports_timer_not_force(tmp_path):
    device = _make_device(tmp_path)

    device.mcu.watchdog.alarm.callback()  # what Timer32PeriodicAlarm fires on a missed feed()

    assert device.mcu.watchdog.read_uint32(REASON) == TIMER
    assert _chip_reset(device) == HAD_POR


def test_run_pin_reset_clears_the_watchdog_and_records_had_run(tmp_path):
    """A RUN-pin reset resets the watchdog block too, so firmware must *not* keep reading the
    previous watchdog reboot's REASON and misreport why it came up - 0057's own open question."""
    device = _make_device(tmp_path)
    _trigger_watchdog(device)  # a guest machine.reset() earlier in the session
    device.mcu.watchdog.scratch_data[4] = WATCHDOG_MAGIC

    device.hard_reset(cause=ResetCause.RUN_PIN)

    assert device.mcu.watchdog.read_uint32(REASON) == 0
    assert device.mcu.watchdog.scratch_data == [0] * 8
    assert _chip_reset(device) == HAD_RUN


def test_hard_reset_defaults_to_the_run_pin(tmp_path):
    """0089's D4: a host-side "reset the board" is what pressing RESET corresponds to physically,
    and the alternative - leaving REASON at whatever the last watchdog write set - makes the guest
    report stale history instead of this reset."""
    device = _make_device(tmp_path)
    _trigger_watchdog(device)

    device.hard_reset()

    assert device.mcu.watchdog.read_uint32(REASON) == 0
    assert _chip_reset(device) == HAD_RUN


def test_releasing_the_run_pin_runs_the_full_device_level_reset(tmp_path):
    """0089's Phase 4/D3: `BaseDevice.__init__` installs its own `hard_reset()` into
    `mcu.on_run_pin_reset`, the same downward hook it already uses for the watchdog. Without that
    wiring an `ExternalDevice` - which only ever gets `attach(rp2040)` - could restart the chip but
    never `cdc.reset()`, leaving the host console stale."""
    device = _make_device(tmp_path)
    device.mcu.core.pc = 0x2000_1234
    device.mcu.flash[0x100] = 0x42
    device.cdc._initialized = True
    usb_ctrl_before = device.mcu.usb_ctrl

    device.mcu.set_run_pin(low=True)
    assert device.mcu.core.pc == 0x2000_1234, "the hold must not reset anything by itself"
    device.mcu.set_run_pin(low=False)

    assert device.mcu.core.pc == FLASH_START_ADDRESS
    assert device.mcu.flash[0x100] == 0x42
    assert device.cdc._initialized is False
    assert device.mcu.usb_ctrl is usb_ctrl_before


def test_a_run_pin_reset_reports_the_reset_pin_not_a_stale_watchdog_reboot(tmp_path):
    """The full path a RESET button takes, end to end: the same registers 0089 §1.3 requires, but
    reached through the pin rather than through `hard_reset(cause=...)` directly."""
    device = _make_device(tmp_path)
    _trigger_watchdog(device)  # a guest machine.reset() earlier in the session
    device.mcu.watchdog.scratch_data[4] = WATCHDOG_MAGIC

    device.mcu.set_run_pin(low=True)
    device.mcu.set_run_pin(low=False)

    assert device.mcu.watchdog.read_uint32(REASON) == 0
    assert device.mcu.watchdog.scratch_data == [0] * 8
    assert _chip_reset(device) == HAD_RUN


def test_power_on_reset_records_had_por(tmp_path):
    device = _make_device(tmp_path)
    device.hard_reset(cause=ResetCause.RUN_PIN)

    device.hard_reset(cause=ResetCause.POWER_ON)

    assert _chip_reset(device) == HAD_POR


def test_recording_a_cause_leaves_the_debugger_owned_psm_restart_flag_alone(tmp_path):
    """PSM_RESTART_FLAG is the block's one writable bit, cleared by the bootrom's write-1-to-clear
    handshake (record 0050) - a reset cause must not forge or clear it."""
    device = _make_device(tmp_path)
    device.mcu.vreg_and_chip_reset.chip_reset |= PSM_RESTART_FLAG

    device.hard_reset(cause=ResetCause.RUN_PIN)

    assert _chip_reset(device) == HAD_RUN | PSM_RESTART_FLAG
