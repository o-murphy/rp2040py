"""Ported from rp2040js's sio.spec.ts."""

import pytest
from utils.create_test_driver import create_test_driver

from rp2040py.rp2040 import SIO_START_ADDRESS

# Hardware Divider registers absolute address
SIO_DIV_UDIVIDEND = SIO_START_ADDRESS + 0x060  # Divider unsigned dividend
SIO_DIV_UDIVISOR = SIO_START_ADDRESS + 0x064  # Divider unsigned divisor
SIO_DIV_SDIVIDEND = SIO_START_ADDRESS + 0x068  # Divider signed dividend
SIO_DIV_SDIVISOR = SIO_START_ADDRESS + 0x06C  # Divider signed divisor
SIO_DIV_QUOTIENT = SIO_START_ADDRESS + 0x070  # Divider result quotient
SIO_DIV_REMAINDER = SIO_START_ADDRESS + 0x074  # Divider result remainder
SIO_DIV_CSR = SIO_START_ADDRESS + 0x078

# SPINLOCK
SIO_SPINLOCK10 = SIO_START_ADDRESS + 0x128
SIO_SPINLOCKST = SIO_START_ADDRESS + 0x5C


@pytest.fixture
def cpu():
    driver = create_test_driver()
    driver.init()
    yield driver
    driver.tear_down()


class TestHardwareDivider:
    def test_signed_divider_123456_over_negative_321_equals_negative_384_rem_192(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, 123456)
        assert cpu.read_int32(SIO_DIV_SDIVIDEND) == 123456
        cpu.write_uint32(SIO_DIV_SDIVISOR, -321)
        assert cpu.read_uint32(SIO_DIV_CSR) == 3
        assert cpu.read_int32(SIO_DIV_SDIVISOR) == -321
        assert cpu.read_int32(SIO_DIV_REMAINDER) == 192
        assert cpu.read_int32(SIO_DIV_QUOTIENT) == -384
        assert cpu.read_uint32(SIO_DIV_CSR) == 1

    def test_signed_divider_negative_3000_over_2_equals_negative_1500_rem_0(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, -3000)
        assert cpu.read_int32(SIO_DIV_SDIVIDEND) == -3000
        cpu.write_uint32(SIO_DIV_SDIVISOR, 2)
        assert cpu.read_uint32(SIO_DIV_CSR) == 3
        assert cpu.read_int32(SIO_DIV_SDIVISOR) == 2
        assert cpu.read_int32(SIO_DIV_REMAINDER) == 0
        assert cpu.read_int32(SIO_DIV_QUOTIENT) == -1500
        assert cpu.read_uint32(SIO_DIV_CSR) == 1

    def test_unsigned_divider_123456_over_321_equals_384_rem_192(self, cpu):
        cpu.write_uint32(SIO_DIV_UDIVIDEND, 123456)
        cpu.write_uint32(SIO_DIV_UDIVISOR, 321)
        assert cpu.read_uint32(SIO_DIV_CSR) == 3
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 192
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 384
        assert cpu.read_uint32(SIO_DIV_CSR) == 1

    def test_division_result_can_be_saved_and_restored_around_another_division(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, 123456)
        cpu.write_uint32(SIO_DIV_SDIVISOR, -321)
        remainder = cpu.read_int32(SIO_DIV_REMAINDER)
        quotient = cpu.read_int32(SIO_DIV_QUOTIENT)
        assert remainder == 192
        assert quotient == -384
        cpu.write_uint32(SIO_DIV_UDIVIDEND, 123)
        cpu.write_uint32(SIO_DIV_UDIVISOR, 7)
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 4
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 17
        cpu.write_uint32(SIO_DIV_REMAINDER, remainder)
        cpu.write_uint32(SIO_DIV_QUOTIENT, quotient)
        assert cpu.read_uint32(SIO_DIV_CSR) == 3
        assert cpu.read_int32(SIO_DIV_REMAINDER) == 192
        assert cpu.read_int32(SIO_DIV_QUOTIENT) == -384

    def test_unsigned_division_by_zero_123456_over_0(self, cpu):
        cpu.write_uint32(SIO_DIV_UDIVIDEND, 123456)
        cpu.write_uint32(SIO_DIV_UDIVISOR, 0)
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 123456
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 0xFFFFFFFF

    def test_unsigned_division_by_zero_0x80000000_over_0(self, cpu):
        cpu.write_uint32(SIO_DIV_UDIVIDEND, 0x80000000)
        cpu.write_uint32(SIO_DIV_UDIVISOR, 0)
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 0x80000000
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 0xFFFFFFFF

    def test_signed_division_by_zero_3000_over_0(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, 3000)
        cpu.write_uint32(SIO_DIV_SDIVISOR, 0)
        assert cpu.read_int32(SIO_DIV_REMAINDER) == 3000
        assert cpu.read_int32(SIO_DIV_QUOTIENT) == -1

    def test_signed_division_by_zero_negative_3000_over_0(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, -3000)
        cpu.write_uint32(SIO_DIV_SDIVISOR, 0)
        assert cpu.read_int32(SIO_DIV_REMAINDER) == -3000
        assert cpu.read_int32(SIO_DIV_QUOTIENT) == 1

    def test_signed_division_0x80000000_over_2(self, cpu):
        cpu.write_uint32(SIO_DIV_SDIVIDEND, 0x80000000)
        cpu.write_uint32(SIO_DIV_SDIVISOR, 2)
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 0
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 0xC0000000

    def test_unsigned_division_0x80000000_over_2(self, cpu):
        cpu.write_uint32(SIO_DIV_UDIVIDEND, 0x80000000)
        cpu.write_uint32(SIO_DIV_UDIVISOR, 2)
        assert cpu.read_uint32(SIO_DIV_REMAINDER) == 0
        assert cpu.read_uint32(SIO_DIV_QUOTIENT) == 0x40000000


def test_reading_inter_core_fifo_addresses_names_0053_instead_of_generic_invalid_address(cpu):
    """docs/records/0053's "Interim option": FIFO_ST/FIFO_WR/FIFO_RD (0x50/0x54/0x58) aren't
    implemented, but a read there should say so plainly instead of falling through to the generic
    "invalid SIO address" warning - so `import _thread`/`multicore_launch_core1()` points straight
    at the record explaining why, not a mystery address."""
    warnings: list[str] = []
    cpu.rp2040.logger.warning = lambda component_name, message: warnings.append(message)

    for offset in (0x050, 0x054, 0x058):
        warnings.clear()
        assert cpu.read_uint32(SIO_START_ADDRESS + offset) == 0xFFFFFFFF
        assert len(warnings) == 1
        assert "0053" in warnings[0]
        assert "core1" in warnings[0] or "_thread" in warnings[0]


def test_unlock_lock_and_check_lock_status_of_spinlock10(cpu):
    cpu.write_uint32(SIO_SPINLOCK10, 0x00000001)  # ensure the spinlock is released
    assert cpu.read_uint32(SIO_SPINLOCK10) == 1024  # lock spinlock, return 1<<spinlock num if previously unlocked
    assert cpu.read_uint32(SIO_SPINLOCKST) == 1024  # bit mask of all spinlocks, locked=1<<spinlock
    assert cpu.read_uint32(SIO_SPINLOCK10) == 0  # 0=already locked
    assert cpu.read_uint32(SIO_SPINLOCKST) == 1024
    cpu.write_uint32(SIO_SPINLOCK10, 0x00000001)  # release the spinlock
    assert cpu.read_uint32(SIO_SPINLOCKST) == 0
