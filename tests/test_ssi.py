"""Unit tests for RPSSI's software-driven SPI NOR flash command emulation (write support - see
issue about the SSI peripheral being a register-only stub with no real flash-write path).

These drive the peripheral's registers directly (SSIENR/DR0) plus the QSPI_SS pin (see ssi.py's
`_on_cs_pin_changed` docstring for why chip-select is that pin, not SSIENR/SER, on RP2040) -
exactly matching what real flash driver code does at the bus level, rather than booting a full
firmware image. The real bootrom's exact command sequence (probed separately, empirically, against
real boots) is a different concern from whether this peripheral correctly implements the JEDEC
command set once framed correctly.
"""

import pytest

from rp2040py.peripherals import ssi as ssi_mod
from rp2040py.rp2040 import RP2040

STATUS_WEL_BIT = 0x02

# GPIOPin.ctrl's output_override field (bits 9:8): 2 = force low, 3 = force high - matching
# pico-sdk's flash_cs_force()/IO_QSPI_GPIO_QSPI_SS_CTRL_OUTOVER_VALUE_LOW/HIGH exactly. Real
# flash_cs_force() only masks in the OUTOVER bits, relying on output-enable-override (bits 13:12)
# already being forced on from earlier bootrom setup (connect_internal_flash()) - forced here too,
# since these tests drive SSI directly without replicating that earlier real-bootrom setup step.
_OE_FORCE_ENABLED = 3 << 12
_CS_FORCE_LOW = _OE_FORCE_ENABLED | (2 << 8)
_CS_FORCE_HIGH = _OE_FORCE_ENABLED | (3 << 8)


def _ssi():
    rp2040 = RP2040()
    return rp2040, rp2040.peripherals[0x18000]


def _send(rp2040, ssi, *command_bytes: int) -> "list[int]":
    """Drives one full SPI transaction: chip-select assert (QSPI_SS forced low), each byte written
    to DR0 and immediately read back (matching real full-duplex shift-register semantics), then
    chip-select deassert (QSPI_SS forced high, which is when erase/program actually apply)."""
    ssi.write_uint32(ssi_mod.SSI_SSIENR, 1)
    ss_pin = rp2040.qspi[1]
    ss_pin.ctrl = _CS_FORCE_LOW
    ss_pin.check_for_updates()
    received = []
    for byte in command_bytes:
        ssi.write_uint32(ssi_mod.SSI_DR0, byte)
        received.append(ssi.read_uint32(ssi_mod.SSI_DR0))
    ss_pin.ctrl = _CS_FORCE_HIGH
    ss_pin.check_for_updates()
    return received


def _read_status(rp2040, ssi) -> int:
    return _send(rp2040, ssi, ssi_mod.CMD_READ_STATUS_1, 0x00)[1]


@pytest.mark.parametrize("size", [4096, 65536])
def test_erase_without_write_enable_is_a_noop(size):
    opcode = ssi_mod.CMD_SECTOR_ERASE if size == 4096 else ssi_mod.CMD_BLOCK_ERASE
    rp2040, ssi = _ssi()
    rp2040.flash[0:size] = bytes(range(256)) * (size // 256)
    before = bytes(rp2040.flash[0:size])

    _send(rp2040, ssi, opcode, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:size]) == before


def test_page_program_without_write_enable_is_a_noop():
    rp2040, ssi = _ssi()
    assert bytes(rp2040.flash[0:4]) == b"\xff\xff\xff\xff"  # freshly reset, erased flash

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x00, 0xAA, 0xBB)

    assert bytes(rp2040.flash[0:4]) == b"\xff\xff\xff\xff"


def test_write_enable_then_page_program_writes_data():
    rp2040, ssi = _ssi()
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x10, ord("h"), ord("i"))

    assert bytes(rp2040.flash[0x10:0x12]) == b"hi"


def test_program_can_only_clear_bits_not_set_them():
    # Real NOR flash physics: PROGRAM can only turn 1 bits into 0, never the reverse - only ERASE
    # resets a region back to all-1s. Programming without erasing first should AND with whatever
    # was already there, not overwrite it - the same thing well-behaved software avoids by always
    # erasing first, but the peripheral should still behave like real hardware if it doesn't.
    rp2040, ssi = _ssi()
    rp2040.flash[0x20] = 0b11110000
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x20, 0b10101010)

    assert rp2040.flash[0x20] == 0b11110000 & 0b10101010


def test_write_enable_auto_clears_after_program_completes():
    rp2040, ssi = _ssi()
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)
    assert _read_status(rp2040, ssi) & STATUS_WEL_BIT

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x00, 0x01)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_write_disable_clears_write_enable_latch():
    rp2040, ssi = _ssi()
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_DISABLE)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_sector_erase_resets_the_4k_region_to_ff():
    rp2040, ssi = _ssi()
    rp2040.flash[0:8192] = bytes([0x42]) * 8192
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_SECTOR_ERASE, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:4096]) == b"\xff" * 4096
    assert bytes(rp2040.flash[4096:8192]) == b"\x42" * 4096  # untouched, outside the erased sector


def test_block_erase_resets_the_64k_region_to_ff():
    rp2040, ssi = _ssi()
    rp2040.flash[0:65536] = bytes([0x42]) * 65536
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_BLOCK_ERASE, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:65536]) == b"\xff" * 65536


def test_erase_address_aligns_down_to_the_sector_boundary():
    rp2040, ssi = _ssi()
    rp2040.flash[0:8192] = bytes([0x42]) * 8192
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    # Address 0x0500 is mid-sector (sectors are 4096 = 0x1000 bytes) - real flash erases the whole
    # containing sector regardless, not just from the given address onward.
    _send(rp2040, ssi, ssi_mod.CMD_SECTOR_ERASE, 0x00, 0x05, 0x00)

    assert bytes(rp2040.flash[0:4096]) == b"\xff" * 4096
    assert bytes(rp2040.flash[4096:8192]) == b"\x42" * 4096


def test_read_status_1_reports_write_enable_latch():
    rp2040, ssi = _ssi()
    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)

    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    assert _read_status(rp2040, ssi) & STATUS_WEL_BIT


def test_read_status_2_reports_quad_enable_already_set():
    # See ssi.py's STATUS2_QE_BIT comment: reported permanently set so flash-detection code that
    # checks-then-sets quad mode sees it's already enabled and doesn't need to issue WRSR.
    rp2040, ssi = _ssi()
    received = _send(rp2040, ssi, ssi_mod.CMD_READ_STATUS_2, 0x00)
    assert received[1] & ssi_mod.STATUS2_QE_BIT


def test_write_status_is_accepted_and_clears_write_enable():
    rp2040, ssi = _ssi()
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_WRITE_STATUS, 0x00, 0x02)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_read_jedec_id_returns_the_configured_id():
    rp2040, ssi = _ssi()
    received = _send(rp2040, ssi, ssi_mod.CMD_READ_JEDEC_ID, 0x00, 0x00, 0x00)
    assert tuple(received[1:4]) == ssi_mod.JEDEC_ID


def test_read_data_returns_actual_flash_contents():
    rp2040, ssi = _ssi()
    rp2040.flash[0x30:0x34] = b"boot"

    received = _send(rp2040, ssi, ssi_mod.CMD_READ_DATA, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00)

    assert bytes(received[4:8]) == b"boot"


def test_command_interrupted_before_chip_select_deasserts_never_applies():
    # QSPI_SS never goes back to high (deasserted) here - real hardware only actually commits an
    # erase/program once chip-select deasserts (the whole point of deferring `_apply_command()` to
    # that transition, see ssi.py) - so nothing should happen yet.
    rp2040, ssi = _ssi()
    ss_pin = rp2040.qspi[1]
    ssi.write_uint32(ssi_mod.SSI_SSIENR, 1)
    ss_pin.ctrl = _CS_FORCE_LOW
    ss_pin.check_for_updates()
    ssi.write_uint32(ssi_mod.SSI_DR0, ssi_mod.CMD_WRITE_ENABLE)
    ss_pin.ctrl = _CS_FORCE_HIGH
    ss_pin.check_for_updates()  # complete the WREN
    ss_pin.ctrl = _CS_FORCE_LOW
    ss_pin.check_for_updates()  # start PAGE_PROGRAM, but never deassert below
    for byte in (ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x00, 0xAA):
        ssi.write_uint32(ssi_mod.SSI_DR0, byte)

    assert bytes(rp2040.flash[0:1]) == b"\xff"


def test_ssi_disabled_ignores_dr0_writes_even_with_chip_select_asserted():
    rp2040, ssi = _ssi()
    ss_pin = rp2040.qspi[1]
    ss_pin.ctrl = _CS_FORCE_LOW
    ss_pin.check_for_updates()

    ssi.write_uint32(ssi_mod.SSI_DR0, ssi_mod.CMD_WRITE_ENABLE)  # SSIENR never set to 1

    ss_pin.ctrl = _CS_FORCE_HIGH
    ss_pin.check_for_updates()
    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)
