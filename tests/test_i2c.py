"""Regression tests for the I2C reference (docs/records/0096-cpp-mcu-core.md, Phase 4): behaviours the lockstep differential pins as a pair but a firmware depends on as a fact."""

from rp2040py.irq import IRQ
from rp2040py.peripherals import i2c as i2c_module
from rp2040py.rp2040 import RP2040

I2C0_BASE = 0x40044000
IC_INTR_STAT, IC_INTR_MASK, IC_RAW_INTR_STAT, IC_DATA_CMD, IC_CLR_RX_UNDER = 0x2C, 0x30, 0x34, 0x10, 0x44
R_RX_UNDER = 1 << 0


def _chip():
    chip = RP2040()
    lines: list[bool] = []
    original = chip.set_interrupt

    def set_interrupt(irq: int, value: bool) -> None:
        if irq == IRQ.I2C0:
            lines.append(bool(value))
        original(irq, value)

    chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
    assert i2c_module.RPI2C is not None
    return chip, lines


def test_ic_intr_mask_is_writable_and_gates_the_interrupt_line():
    chip, lines = _chip()
    assert chip.read_uint32(I2C0_BASE + IC_INTR_MASK) == 0x8FF  # the datasheet's reset value
    chip.write_uint32(I2C0_BASE + IC_INTR_MASK, R_RX_UNDER)
    assert chip.read_uint32(I2C0_BASE + IC_INTR_MASK) == R_RX_UNDER
    chip.read_uint32(I2C0_BASE + IC_DATA_CMD)  # an empty RX FIFO: RX_UNDER
    assert chip.read_uint32(I2C0_BASE + IC_INTR_STAT) == R_RX_UNDER
    assert lines and lines[-1] is True
    chip.write_uint32(I2C0_BASE + IC_INTR_MASK, 0)  # masking it again drops the line at once
    assert lines[-1] is False
    assert chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & R_RX_UNDER


def test_ic_intr_mask_holds_13_bits():
    chip, _ = _chip()
    chip.write_uint32(I2C0_BASE + IC_INTR_MASK, 0xFFFFFFFF)
    assert chip.read_uint32(I2C0_BASE + IC_INTR_MASK) == 0x1FFF


def test_a_second_abort_keeps_the_earlier_reasons_and_replaces_the_flush_count():
    chip, _ = _chip()
    i2c = chip.i2c[0]
    i2c.abort_source = 1 << 3 | (2 << 23)  # TXDATA_NOACK, two commands flushed
    i2c._tx_fifo.push(1)
    i2c.arbitration_lost()  # a second abort, with one command queued
    assert i2c.abort_source & 0x1FF == 1 << 3  # the earlier reason is still there
    assert i2c.abort_source & (1 << 12)  # and the new one joined it
    assert i2c.abort_source >> 23 == 1  # the count is this abort's, not 2 | 1


def test_fs_spklen_is_writable_only_while_the_i2c_is_disabled():
    chip, _ = _chip()
    IC_ENABLE, IC_FS_SPKLEN = 0x6C, 0xA0
    chip.write_uint32(I2C0_BASE + IC_FS_SPKLEN, 5)  # bit 0 of the VALUE is set: the old condition refused it
    assert chip.read_uint32(I2C0_BASE + IC_FS_SPKLEN) == 5
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 1)
    chip.write_uint32(I2C0_BASE + IC_FS_SPKLEN, 8)  # enabled: not writable (the old condition accepted it)
    assert chip.read_uint32(I2C0_BASE + IC_FS_SPKLEN) == 5
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 0)
    chip.write_uint32(
        I2C0_BASE + IC_FS_SPKLEN, 0x100
    )  # the 8-bit field is 0: "the minimum valid value is 1 ... if attempted results in 1 being set"
    assert chip.read_uint32(I2C0_BASE + IC_FS_SPKLEN) == 1


IC_CON, IC_TAR, IC_SAR, IC_ENABLE, IC_SS_SCL_HCNT, IC_SDA_HOLD, IC_RX_TL, IC_CLR_ACTIVITY, IC_RAW_INTR_STAT = (
    0x00,
    0x04,
    0x08,
    0x6C,
    0x14,
    0x7C,
    0x38,
    0x5C,
    0x34,
)


def test_configuration_registers_are_written_only_while_disabled_and_keep_their_bits():
    """Datasheet 4.3.17: IC_CON, IC_TAR, IC_SAR, the SCL counts and IC_SDA_HOLD "can be written only when the DW_apb_i2c is disabled"; widths: IC_CON 9:0, IC_TAR 11:0, IC_SDA_HOLD 23:0."""
    chip, _ = _chip()
    chip.write_uint32(I2C0_BASE + IC_CON, 0xFFFFFFFF)
    chip.write_uint32(I2C0_BASE + IC_TAR, 0xFFFFFFFF)
    chip.write_uint32(I2C0_BASE + IC_SDA_HOLD, 0xFFFFFFFF)
    assert chip.read_uint32(I2C0_BASE + IC_CON) == 0x3FF
    assert chip.read_uint32(I2C0_BASE + IC_TAR) == 0xFFF
    assert chip.read_uint32(I2C0_BASE + IC_SDA_HOLD) == 0xFFFFFF
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 1)
    for offset in (IC_CON, IC_TAR, IC_SAR, IC_SS_SCL_HCNT, IC_SDA_HOLD):
        before = chip.read_uint32(I2C0_BASE + offset)
        chip.write_uint32(I2C0_BASE + offset, 0x15)
        assert chip.read_uint32(I2C0_BASE + offset) == before, hex(offset)
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 0)
    chip.write_uint32(I2C0_BASE + IC_SS_SCL_HCNT, 5)  # "The minimum valid value is 6 ... results in 6 being set"
    assert chip.read_uint32(I2C0_BASE + IC_SS_SCL_HCNT) == 6


def test_the_first_data_byte_flag_is_bit_11_of_ic_data_cmd():
    chip, _ = _chip()
    i2c = chip.i2c[0]
    i2c._first_byte = True
    i2c.complete_read(0x42)
    assert chip.read_uint32(I2C0_BASE + 0x10) == 0x42 | (1 << 11)


def test_rx_full_is_a_level_and_the_abort_flushes_the_rx_fifo():
    chip, _ = _chip()
    i2c = chip.i2c[0]
    chip.write_uint32(I2C0_BASE + IC_RX_TL, 1)
    for value in (1, 2, 3):
        i2c.complete_read(value)
    assert chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & (1 << 2)
    chip.read_uint32(I2C0_BASE + 0x10)  # 2 left, above the threshold of 1: still raised
    assert chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & (1 << 2)
    chip.read_uint32(I2C0_BASE + 0x10)  # 1 left: no longer above it
    assert not chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & (1 << 2)
    i2c.complete_read(9)
    i2c.arbitration_lost()  # a transmit abort flushes both FIFOs
    assert chip.read_uint32(I2C0_BASE + 0x78) == 0
    assert not chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & (1 << 2)


def test_a_command_written_while_the_i2c_is_disabled_is_lost():
    """Datasheet 4.3.10.2.1: "If the IC_DATA_CMD register is written before the DW_apb_i2c is enabled, the data and commands are lost as the buffers are kept cleared"."""
    chip, _ = _chip()
    chip.i2c[0].on_start = lambda repeated: None  # a device that never answers: an accepted command stays in the FIFO
    chip.write_uint32(I2C0_BASE + 0x10, 0x155)  # IC_DATA_CMD
    assert chip.read_uint32(I2C0_BASE + 0x74) == 0  # IC_TXFLR
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 1)
    chip.write_uint32(I2C0_BASE + 0x10, 0x155)
    assert chip.read_uint32(I2C0_BASE + 0x74) == 1  # accepted now
