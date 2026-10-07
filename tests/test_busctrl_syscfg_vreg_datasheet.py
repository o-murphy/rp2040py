"""BUSCTRL, SYSCFG and VREG_AND_CHIP_RESET against the RP2040 datasheet (docs/records/0098-datasheet-conformance-audit.md). Run on both builds.

Sections: BUSCTRL 2.1.5 (tables 5-6), VREG_AND_CHIP_RESET 2.10.6 (tables 189-191), SYSCFG 2.21 (tables 354-360), RESETS 2.14.3 (SYSCFG is RESET bit 18).
"""

from rp2040py.rp2040 import RP2040

BUSCTRL, VREG, SYSCFG = 0x40030000, 0x40064000, 0x40004000


def _warnings(chip: RP2040) -> list[str]:
    messages: list[str] = []
    chip.logger.warning = lambda name, message: messages.append(message)  # type: ignore[method-assign]
    return messages


def test_bus_priority_is_four_stored_bits():
    chip = RP2040()
    messages = _warnings(chip)
    assert chip.read_uint32(BUSCTRL) == 0
    chip.write_uint32(BUSCTRL, 0xFFFFFFFF)
    assert chip.read_uint32(BUSCTRL) == 0x1111  # PROC0 0, PROC1 4, DMA_R 8, DMA_W 12
    assert messages == []


def test_bus_priority_ack_is_read_only():
    chip = RP2040()
    messages = _warnings(chip)
    chip.write_uint32(BUSCTRL + 4, 0)
    assert chip.read_uint32(BUSCTRL + 4) == 1 and messages == []


def test_vreg_and_bod_keep_their_reserved_and_read_only_bits():
    chip = RP2040()
    chip.write_uint32(VREG, 0xFFFFFFFF)
    chip.write_uint32(VREG + 4, 0xFFFFFFFF)
    assert chip.read_uint32(VREG) == 0xF3  # VSEL 7:4, HIZ 1, EN 0 - ROK (bit 12) and the reserved bits are not writable
    assert chip.read_uint32(VREG + 4) == 0xF1
    chip.write_uint32(VREG, 0)
    assert chip.read_uint32(VREG) == 0


def test_syscfg_registers_reset_and_widths():
    chip = RP2040()
    messages = _warnings(chip)
    expected_reset = {0x00: 0, 0x04: 0, 0x08: 0x10000000, 0x0C: 0, 0x10: 0, 0x14: 0x66, 0x18: 0}
    for offset, value in expected_reset.items():
        assert chip.read_uint32(SYSCFG + offset) == value, hex(offset)
    for offset in expected_reset:
        chip.write_uint32(SYSCFG + offset, 0xFFFFFFFF)
    assert chip.read_uint32(SYSCFG + 0x04) == 0xFFFFFFFF  # PROC1_NMI_MASK
    assert chip.read_uint32(SYSCFG + 0x08) == 0xFF000000  # DAP instance ids; HALTED 1:0 are read-only
    assert chip.read_uint32(SYSCFG + 0x0C) == 0x3FFFFFFF  # GPIO 0..29
    assert chip.read_uint32(SYSCFG + 0x10) == 0x3F  # GPIO 30..35
    assert chip.read_uint32(SYSCFG + 0x14) == 0xEE | 0x66  # SWDO (bits 4, 0) read-only
    assert chip.read_uint32(SYSCFG + 0x18) == 0xFF
    assert messages == []


def test_resetting_syscfg_through_resets_restores_every_register():
    chip = RP2040()
    for offset in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18):
        chip.write_uint32(SYSCFG + offset, 0xFFFFFFFF)
    chip.syscfg.reset()
    assert [chip.read_uint32(SYSCFG + o) for o in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18)] == [
        0,
        0,
        0x10000000,
        0,
        0,
        0x66,
        0,
    ]
