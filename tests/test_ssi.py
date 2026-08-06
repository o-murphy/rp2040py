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

from rp2040py.gpio_pin import GPIOPinState
from rp2040py.peripherals import ssi as ssi_mod

STATUS_WEL_BIT = 0x02

# GPIOPin.ctrl's output_override field (bits 9:8): 2 = force low, 3 = force high - matching
# pico-sdk's flash_cs_force()/IO_QSPI_GPIO_QSPI_SS_CTRL_OUTOVER_VALUE_LOW/HIGH exactly. Real
# flash_cs_force() only masks in the OUTOVER bits, relying on output-enable-override (bits 13:12)
# already being forced on from earlier bootrom setup (connect_internal_flash()) - forced here too,
# since these tests drive SSI directly without replicating that earlier real-bootrom setup step.
_OE_FORCE_ENABLED = 3 << 12
_CS_FORCE_LOW = _OE_FORCE_ENABLED | (2 << 8)
_CS_FORCE_HIGH = _OE_FORCE_ENABLED | (3 << 8)


def _ssi(rp2040_factory):
    rp2040 = rp2040_factory()
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
def test_erase_without_write_enable_is_a_noop(size, rp2040_factory):
    opcode = ssi_mod.CMD_SECTOR_ERASE if size == 4096 else ssi_mod.CMD_BLOCK_ERASE
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0:size] = bytes(range(256)) * (size // 256)
    before = bytes(rp2040.flash[0:size])

    _send(rp2040, ssi, opcode, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:size]) == before


def test_page_program_without_write_enable_is_a_noop(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    assert bytes(rp2040.flash[0:4]) == b"\xff\xff\xff\xff"  # freshly reset, erased flash

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x00, 0xAA, 0xBB)

    assert bytes(rp2040.flash[0:4]) == b"\xff\xff\xff\xff"


def test_write_enable_then_page_program_writes_data(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x10, ord("h"), ord("i"))

    assert bytes(rp2040.flash[0x10:0x12]) == b"hi"


def test_program_can_only_clear_bits_not_set_them(rp2040_factory):
    # Real NOR flash physics: PROGRAM can only turn 1 bits into 0, never the reverse - only ERASE
    # resets a region back to all-1s. Programming without erasing first should AND with whatever
    # was already there, not overwrite it - the same thing well-behaved software avoids by always
    # erasing first, but the peripheral should still behave like real hardware if it doesn't.
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0x20] = 0b11110000
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x20, 0b10101010)

    assert rp2040.flash[0x20] == 0b11110000 & 0b10101010


def test_write_enable_auto_clears_after_program_completes(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)
    assert _read_status(rp2040, ssi) & STATUS_WEL_BIT

    _send(rp2040, ssi, ssi_mod.CMD_PAGE_PROGRAM, 0x00, 0x00, 0x00, 0x01)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_write_disable_clears_write_enable_latch(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_DISABLE)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_sector_erase_resets_the_4k_region_to_ff(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0:8192] = bytes([0x42]) * 8192
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_SECTOR_ERASE, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:4096]) == b"\xff" * 4096
    assert bytes(rp2040.flash[4096:8192]) == b"\x42" * 4096  # untouched, outside the erased sector


def test_block_erase_resets_the_64k_region_to_ff(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0:65536] = bytes([0x42]) * 65536
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_BLOCK_ERASE, 0x00, 0x00, 0x00)

    assert bytes(rp2040.flash[0:65536]) == b"\xff" * 65536


def test_erase_address_aligns_down_to_the_sector_boundary(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0:8192] = bytes([0x42]) * 8192
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    # Address 0x0500 is mid-sector (sectors are 4096 = 0x1000 bytes) - real flash erases the whole
    # containing sector regardless, not just from the given address onward.
    _send(rp2040, ssi, ssi_mod.CMD_SECTOR_ERASE, 0x00, 0x05, 0x00)

    assert bytes(rp2040.flash[0:4096]) == b"\xff" * 4096
    assert bytes(rp2040.flash[4096:8192]) == b"\x42" * 4096


def test_read_status_1_reports_write_enable_latch(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)

    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    assert _read_status(rp2040, ssi) & STATUS_WEL_BIT


def test_read_status_2_reports_quad_enable_already_set(rp2040_factory):
    # See ssi.py's STATUS2_QE_BIT comment: reported permanently set so flash-detection code that
    # checks-then-sets quad mode sees it's already enabled and doesn't need to issue WRSR.
    rp2040, ssi = _ssi(rp2040_factory)
    received = _send(rp2040, ssi, ssi_mod.CMD_READ_STATUS_2, 0x00)
    assert received[1] & ssi_mod.STATUS2_QE_BIT


def test_write_status_is_accepted_and_clears_write_enable(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    _send(rp2040, ssi, ssi_mod.CMD_WRITE_ENABLE)

    _send(rp2040, ssi, ssi_mod.CMD_WRITE_STATUS, 0x00, 0x02)

    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_read_jedec_id_returns_the_configured_id(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    received = _send(rp2040, ssi, ssi_mod.CMD_READ_JEDEC_ID, 0x00, 0x00, 0x00)
    assert tuple(received[1:4]) == ssi_mod.JEDEC_ID


def test_read_data_returns_actual_flash_contents(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    rp2040.flash[0x30:0x34] = b"boot"

    received = _send(rp2040, ssi, ssi_mod.CMD_READ_DATA, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00)

    assert bytes(received[4:8]) == b"boot"


def test_command_interrupted_before_chip_select_deasserts_never_applies(rp2040_factory):
    # QSPI_SS never goes back to high (deasserted) here - real hardware only actually commits an
    # erase/program once chip-select deasserts (the whole point of deferring `_apply_command()` to
    # that transition, see ssi.py) - so nothing should happen yet.
    rp2040, ssi = _ssi(rp2040_factory)
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


def test_ssi_disabled_ignores_dr0_writes_even_with_chip_select_asserted(rp2040_factory):
    rp2040, ssi = _ssi(rp2040_factory)
    ss_pin = rp2040.qspi[1]
    ss_pin.ctrl = _CS_FORCE_LOW
    ss_pin.check_for_updates()

    ssi.write_uint32(ssi_mod.SSI_DR0, ssi_mod.CMD_WRITE_ENABLE)  # SSIENR never set to 1

    ss_pin.ctrl = _CS_FORCE_HIGH
    ss_pin.check_for_updates()
    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)


def test_chip_select_already_asserted_at_reset_is_not_silently_missed(rp2040_factory):
    # QSPI_SS's own reset-state resolved `.value` is LOW (asserted) - see gpio_pin.py: an
    # `always_output_enabled` pin with no function-select driving it yet resolves to LOW, same as a
    # regular disabled GPIO would resolve to floating/INPUT if it weren't hardcoded always-driven.
    # `RPSSI._cs_asserted` must start in sync with that, or the very first chip-select assertion
    # ever performed (a plain "force low" with the pin already reading low, i.e. no rising/falling
    # edge to fire `_on_cs_pin_changed`) is invisible to this peripheral, and every byte of that
    # first command is silently dropped by `write_uint32`'s `self._cs_asserted` guard - this
    # reproduces exactly the hang the bootrom's flash_do_cmd_cs()-equivalent loop suffered from
    # (see docs/BACKLOG.md).
    rp2040, ssi = _ssi(rp2040_factory)
    assert rp2040.qspi[1].value == GPIOPinState.LOW
    assert ssi._cs_asserted

    received = _send(rp2040, ssi, ssi_mod.CMD_READ_JEDEC_ID, 0x00, 0x00, 0x00)

    assert tuple(received[1:4]) == ssi_mod.JEDEC_ID


def test_dr0_writes_while_chip_select_deasserted_still_advance_the_fifo(rp2040_factory):
    # Real SSI FIFO hardware (TXFLR/RXFLR/DR0) is wired independently of the QSPI_SS GPIO pin - it
    # keeps shifting bytes regardless of chip-select state (CS is a software-only bit-banged GPIO
    # concern here, see ssi.py's chip-select docstring). Firmware relies on this: the bootrom's
    # flash_exit_xip() deliberately clocks dummy bytes through DR0 *while chip-select is forced
    # high* (pico-bootrom's program_flash_generic.c, the Micron-compatibility dummy-clock
    # sequence) - if those writes were silently dropped instead of still populating the RX FIFO,
    # firmware's TXFLR/RXFLR-driven flow-control loop spins forever waiting for bytes that will
    # never arrive (this reproduced the real boot hang, see docs/BACKLOG.md). None of this should
    # be interpreted as a real flash command, though - only bytes clocked in while actually
    # chip-selected go through `_shift_byte()`/affect flash state.
    rp2040, ssi = _ssi(rp2040_factory)
    ss_pin = rp2040.qspi[1]
    ssi.write_uint32(ssi_mod.SSI_SSIENR, 1)
    ss_pin.ctrl = _CS_FORCE_HIGH  # deasserted
    ss_pin.check_for_updates()

    ssi.write_uint32(ssi_mod.SSI_DR0, ssi_mod.CMD_WRITE_ENABLE)

    assert ssi.read_uint32(ssi_mod.SSI_RXFLR) == 1
    assert ssi.read_uint32(ssi_mod.SSI_DR0) == 0xFF
    assert not (_read_status(rp2040, ssi) & STATUS_WEL_BIT)  # not interpreted as a real command
