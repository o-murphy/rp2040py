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
    i2c.on_start = lambda repeated: None  # a bus that never answers by itself: the completions below are the test's own
    i2c.on_connect = lambda address, mode: None
    chip.write_uint32(I2C0_BASE + 0x6C, 1)  # IC_ENABLE
    for command in (1, 2, 3):
        chip.write_uint32(I2C0_BASE + IC_DATA_CMD, command)
    i2c.complete_start()
    i2c.complete_connect(False)  # the address is not acknowledged: 7B_ADDR_NOACK, the three commands flushed
    assert i2c.snapshot()["abort_source"] == 1 | (3 << 23)
    chip.write_uint32(I2C0_BASE + IC_DATA_CMD, 4)
    chip.write_uint32(I2C0_BASE + IC_DATA_CMD, 5)
    i2c.arbitration_lost()  # a second abort, with commands queued
    abort_source = i2c.snapshot()["abort_source"]
    assert abort_source & 0x1FF == 1  # the earlier reason is still there
    assert abort_source & (1 << 12)  # and the new one joined it
    assert abort_source >> 23 == 2  # the count is this abort's, not 3 | 2


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
    i2c.on_start = lambda repeated: None
    i2c.on_connect = lambda address, mode: None
    i2c.on_read_byte = lambda ack: None
    chip.write_uint32(I2C0_BASE + 0x6C, 1)  # IC_ENABLE
    chip.write_uint32(I2C0_BASE + IC_DATA_CMD, 1 << 8)  # a read command
    i2c.complete_start()
    i2c.complete_connect(True)
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


def _bus(chip):
    """A device that answers everything at once; the events are recorded."""
    i2c = chip.i2c[0]
    events: list[str] = []
    i2c.on_start = lambda repeated: (events.append("start"), i2c.complete_start())
    i2c.on_connect = lambda address, mode: (events.append("connect"), i2c.complete_connect(True))
    i2c.on_write_byte = lambda byte: (events.append(f"byte {byte:x}"), i2c.complete_write(True))
    i2c.on_stop = lambda: (events.append("stop"), i2c.complete_stop())
    return i2c, events


IC_ENABLE, IC_TX_ABRT_SOURCE = 0x6C, 0x80
ABORT_BIT, ENABLE_BIT = 1 << 1, 1
R_TX_ABRT, R_STOP_DET = 1 << 6, 1 << 9


def test_abort_while_the_master_waits_for_a_command_issues_the_stop_and_clears_the_bit():
    """Datasheet 4.3.10.3/4.3.10.4 and IC_ENABLE.ABORT (table 477): "In response to an ABORT, the controller issues a STOP and flushes the Tx FIFO after completing the current transfer, then sets the TX_ABORT
    interrupt after the abort operation. The ABORT bit is cleared automatically after the abort operation." A master that has sent a byte without STOP and has nothing more to send holds SCL low; ABORT is
    how it lets go ("you can issue the ABORT (IC_ENABLE.ABORT) to relinquish the I2C bus and then disable DW_apb_i2c")."""
    chip, _ = _chip()
    _i2c, events = _bus(chip)
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 0)
    chip.write_uint32(I2C0_BASE + 0x04, 0x50)  # IC_TAR
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT)
    chip.write_uint32(I2C0_BASE + IC_DATA_CMD, 0x11)  # a byte, no STOP: the master waits for the next command
    assert events == ["start", "connect", "byte 11"]
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT | ABORT_BIT)
    assert events[-1] == "stop"  # the STOP is issued
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == ENABLE_BIT  # ABORT cleared by hardware
    raw = chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT)
    assert raw & R_TX_ABRT and raw & R_STOP_DET
    assert chip.read_uint32(I2C0_BASE + IC_TX_ABRT_SOURCE) & (1 << 16)  # ABRT_USER_ABRT


def test_abort_is_ignored_unless_enable_is_already_set_or_the_bus_is_idle():
    chip, _ = _chip()
    _i2c, events = _bus(chip)
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ABORT_BIT)  # not enabled: "the controller ignores any write to ABORT bit"
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == 0
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT | ABORT_BIT)  # ENABLE set by this very write: not "already set"
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == ENABLE_BIT
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT | ABORT_BIT)  # enabled, but nothing in progress
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == ENABLE_BIT and not events
    assert not chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & R_TX_ABRT


def test_abort_in_the_middle_of_a_transfer_completes_the_current_transfer_then_stops_and_flushes():
    chip, _ = _chip()
    i2c = chip.i2c[0]
    events: list[str] = []
    held: list = []
    i2c.on_start = lambda repeated: (events.append("start"), i2c.complete_start())
    i2c.on_connect = lambda address, mode: (events.append("connect"), i2c.complete_connect(True))
    i2c.on_write_byte = lambda byte: (
        events.append(f"byte {byte:x}"),
        held.append(1),
    )  # the device is slow: the byte is in flight
    i2c.on_stop = lambda: (events.append("stop"), i2c.complete_stop())
    chip.write_uint32(I2C0_BASE + IC_ENABLE, 0)
    chip.write_uint32(I2C0_BASE + 0x04, 0x50)
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT)
    for byte in (0x11, 0x22, 0x33):
        chip.write_uint32(I2C0_BASE + IC_DATA_CMD, byte)
    assert events == ["start", "connect", "byte 11"]
    chip.write_uint32(I2C0_BASE + IC_ENABLE, ENABLE_BIT | ABORT_BIT)
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == ENABLE_BIT | ABORT_BIT  # "ABORT operation in progress"
    assert (
        not chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & R_TX_ABRT
    )  # TX_ABRT comes after the abort operation, not before
    i2c.complete_write(True)  # the current byte completes
    assert events[-1] == "stop" and "byte 22" not in events  # then the STOP, and the rest is flushed
    assert chip.read_uint32(I2C0_BASE + IC_ENABLE) == ENABLE_BIT
    assert chip.read_uint32(I2C0_BASE + IC_RAW_INTR_STAT) & R_TX_ABRT
    assert chip.read_uint32(I2C0_BASE + IC_TX_ABRT_SOURCE) >> 23 == 2  # two commands flushed
