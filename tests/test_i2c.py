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
