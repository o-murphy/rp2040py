"""SYSINFO and TBMAN against the datasheet and pico-sdk (docs/records/0098-datasheet-conformance-audit.md, "SYSINFO and TBMAN"). Run on both builds."""

from rp2040py.device.bootrom import BOOTROM_B1
from rp2040py.rp2040 import RP2040

SYSINFO = 0x40000000
TBMAN = 0x4006C000
CHIP_ID, PLATFORM, GITREF = 0x00, 0x04, 0x40


def _chip_with_rom_version(version: int) -> RP2040:
    chip = RP2040()
    chip.bootrom[4] = (chip.bootrom[4] & 0x00FFFFFF) | (version << 24)  # ROM address 0x13: rp2040_rom_version()
    return chip


def test_chip_id_names_the_manufacturer_and_part_pico_sdk_asserts():
    chip_id = RP2040().read_uint32(SYSINFO + CHIP_ID)
    assert chip_id & 0xFFF == 0x927  # pico-sdk platform.c: MANUFACTURER_RPI
    assert (chip_id >> 12) & 0xFFFF == 0x2  # PART_RP2


def test_chip_revision_follows_the_loaded_bootrom():
    # platform.h: "1 for B0/B1, 2 for B2"; rp2040_rom_version(): 1 B0, 2 B1, 3 B2
    for version, revision in ((0, 1), (1, 1), (2, 1), (3, 2), (4, 1)):
        assert _chip_with_rom_version(version).read_uint32(SYSINFO + CHIP_ID) >> 28 == revision, version


def test_the_bundled_b1_bootrom_reads_revision_1():
    chip = RP2040()
    chip.load_bootrom(BOOTROM_B1)
    assert chip.read_uint32(SYSINFO + CHIP_ID) == 0x10002927


def test_platform_and_tbman_say_asic():
    chip = RP2040()
    assert chip.read_uint32(SYSINFO + PLATFORM) == 0x2
    assert chip.read_uint32(TBMAN) == 0x1  # datasheet 2.22: ASIC (bit 0) resets to 1, FPGA (bit 1) to 0


def test_writes_to_the_read_only_registers_change_nothing_and_log_nothing():
    chip = _chip_with_rom_version(3)
    messages: list[str] = []
    chip.logger.warning = lambda name, message: messages.append(message)  # type: ignore[method-assign]
    for offset in (CHIP_ID, PLATFORM, GITREF):
        chip.write_uint32(SYSINFO + offset, 0xFFFFFFFF)
    chip.write_uint32(TBMAN, 0xFFFFFFFF)
    assert messages == []
    assert chip.read_uint32(SYSINFO + CHIP_ID) == 0x20002927
    assert chip.read_uint32(SYSINFO + PLATFORM) == 0x2
    assert chip.read_uint32(SYSINFO + GITREF) == 0xE0C912E8
    assert chip.read_uint32(TBMAN) == 0x1


def test_an_offset_that_is_no_register_still_warns():
    chip = RP2040()
    messages: list[str] = []
    chip.logger.warning = lambda name, message: messages.append(message)  # type: ignore[method-assign]
    chip.write_uint32(SYSINFO + 0x8, 1)
    assert chip.read_uint32(TBMAN + 0x4) == 0xFFFFFFFF
    assert len(messages) == 2 and "write" in messages[0] and "read" in messages[1]
