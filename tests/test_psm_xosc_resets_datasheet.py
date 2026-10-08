"""PSM, XOSC and RESETS against the RP2040 datasheet (docs/records/0098-datasheet-conformance-audit.md). Run on both builds.

Sections: PSM 2.13.5 (tables 196-200), RESETS 2.14.2-2.14.3 (tables 202-204), XOSC 2.16.6-2.16.7 (tables 258-263).
"""

from rp2040py.rp2040 import RP2040

RESETS, PSM, XOSC = 0x4000C000, 0x40010000, 0x40024000
CTRL, STATUS, DORMANT, STARTUP, COUNT = 0x00, 0x04, 0x08, 0x0C, 0x1C
BADWRITE = 1 << 24
WAKE, DORMANT_VALUE = 0x77616B65, 0x636F6D61


def _warnings(chip: RP2040) -> list[str]:
    messages: list[str] = []
    chip.logger.warning = lambda name, message: messages.append(message)  # type: ignore[method-assign]
    return messages


def test_reset_done_is_set_once_the_peripheral_is_out_of_reset():
    chip = RP2040()
    assert chip.read_uint32(RESETS + 8) == 0x01FFFFFF  # nothing is held in reset
    chip.write_uint32(RESETS, 0x00400000)  # UART0 into reset
    assert chip.read_uint32(RESETS + 8) == 0x01FFFFFF & ~0x00400000
    chip.write_uint32(RESETS, 0)
    assert chip.read_uint32(RESETS + 8) == 0x01FFFFFF


def test_xosc_reset_values():
    chip = RP2040()
    assert chip.read_uint32(XOSC + CTRL) == 0xAA0  # FREQ_RANGE resets to 0xAA0 (1_15MHZ), ENABLE is not set
    assert chip.read_uint32(XOSC + STARTUP) == 0xC4  # DELAY resets to 0xc4 (table 262)
    assert chip.read_uint32(XOSC + DORMANT) == WAKE  # "On power-up this field is initialised to WAKE"
    assert chip.read_uint32(XOSC + STATUS) == 0


def test_xosc_ctrl_keeps_its_reserved_bits_and_a_fixed_freq_range():
    chip = RP2040()
    messages = _warnings(chip)
    chip.write_uint32(XOSC + CTRL, 0xFFFFFFFF)
    assert chip.read_uint32(XOSC + CTRL) & 0xFF000000 == 0  # 31:24 reserved
    assert chip.read_uint32(XOSC + CTRL) & 0xFFF == 0xAA0  # "cannot be changed"
    assert chip.read_uint32(XOSC + STATUS) & BADWRITE  # "An invalid value has been written to ... CTRL_FREQ_RANGE"
    assert messages


def test_the_sdk_startup_sequence_enables_and_stabilises_the_xosc():
    chip = RP2040()
    messages = _warnings(chip)
    chip.write_uint32(XOSC + CTRL, 0xAA0)  # xosc_init(): FREQ_RANGE = 1_15MHZ
    chip.write_uint32(XOSC + STARTUP, 47)
    chip.write_uint32(XOSC + 0x2000, 0xFAB << 12)  # hw_set_bits(): the SET alias
    assert chip.read_uint32(XOSC + STATUS) == 0x80001000  # STABLE | ENABLED
    assert not messages and not chip.read_uint32(XOSC + STATUS) & BADWRITE


def test_an_invalid_dormant_write_selects_wake_and_sets_badwrite():
    chip = RP2040()
    chip.write_uint32(XOSC + DORMANT, 0x12345678)
    assert chip.read_uint32(XOSC + DORMANT) == WAKE  # "An invalid write will also select WAKE"
    assert chip.read_uint32(XOSC + STATUS) & BADWRITE
    chip.write_uint32(XOSC + STATUS, BADWRITE)  # write 1 to clear
    assert not chip.read_uint32(XOSC + STATUS) & BADWRITE
    chip.write_uint32(XOSC + DORMANT, DORMANT_VALUE)
    assert chip.read_uint32(XOSC + DORMANT) == DORMANT_VALUE


def test_psm_registers_match_the_tables():
    chip = RP2040()
    messages = _warnings(chip)
    for offset in (0x0, 0x4, 0x8):
        assert chip.read_uint32(PSM + offset) == 0
        chip.write_uint32(PSM + offset, 0xFFFFFFFF)
        assert chip.read_uint32(PSM + offset) == 0x1FFFF  # 16:0, bit 17 and up reserved
    assert chip.read_uint32(PSM + 0xC) == 0x1FFFF  # FRCE_ON overrides FRCE_OFF: every domain stays done
    chip.write_uint32(PSM + 0x0, 0)
    assert chip.read_uint32(PSM + 0xC) == 0  # forced off and not forced on
    assert not messages
