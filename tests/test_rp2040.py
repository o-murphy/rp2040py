"""Ported from rp2040js's rp2040.spec.ts."""

from unittest.mock import Mock

from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.rp2040 import RP2040
from rp2040py.utils.assembler import opcode_bx, opcode_movs, opcode_nop, opcode_pop, opcode_push

r0 = 0
r4 = 4
lr = 14

VTOR = 0xE000ED08
NVIC_ISER = 0xE000E100
NVIC_ICER = 0xE000E180
NVIC_ISPR = 0xE000E200
NVIC_ICPR = 0xE000E280


def test_initializes_pc_and_sp_according_to_bootroms_vector_table(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.load_bootrom([0x20041F00, 0xEE])
    assert rp2040.core.sp == 0x20041F00
    assert rp2040.core.pc == 0xEE


class TestIoRegisterWrites:
    def _spy_peripheral(self, rp2040: RP2040, name: str, *, offset: int = 0x10) -> Mock:
        peripheral = BasePeripheral(rp2040, name)
        write_uint32 = Mock(wraps=peripheral.write_uint32)
        peripheral.write_uint32 = write_uint32  # type: ignore[method-assign]
        rp2040.peripherals[offset] = peripheral
        return write_uint32

    def test_replicates_8_bit_values_four_times(self, rp2040_factory):
        rp2040 = rp2040_factory()
        write_uint32 = self._spy_peripheral(rp2040, "TestPeripheral")
        rp2040.write_uint8(0x10123, 0x534)
        write_uint32.assert_called_with(0x120, 0x34343434)

    def test_replicates_16_bit_values_twice(self, rp2040_factory):
        rp2040 = rp2040_factory()
        write_uint32 = self._spy_peripheral(rp2040, "TestPeripheral")
        rp2040.write_uint16(0x10123, 0x12345678)
        write_uint32.assert_called_with(0x120, 0x56785678)

    def test_supports_atomic_io_register_write_addresses(self, rp2040_factory):
        rp2040 = rp2040_factory()
        peripheral = BasePeripheral(rp2040, "TestAtomic")
        peripheral.read_uint32 = Mock(return_value=0xFF)  # type: ignore[method-assign]
        write_uint32 = Mock(wraps=peripheral.write_uint32)
        peripheral.write_uint32 = write_uint32  # type: ignore[method-assign]
        rp2040.peripherals[0x10] = peripheral
        rp2040.write_uint32(0x11120, 0x0F)
        write_uint32.assert_called_with(0x120, 0xF0)


class TestExceptionEntryAndReturn:
    def test_executes_an_exception_handler_and_returns_from_it_correctly(self, rp2040_factory):
        int1 = 1 << 1
        int1_handler = 0x10000100
        exc_int1 = 16 + 1
        rp2040 = rp2040_factory()
        rp2040.core.sp = 0x20004000
        rp2040.core.pc = 0x10004001
        rp2040.core.registers[r0] = 0x44
        rp2040.core.pending_interrupts = int1
        rp2040.core.enabled_interrupts = int1
        rp2040.core.interrupts_updated = True
        rp2040.write_uint32(VTOR, 0x10000000)
        rp2040.write_uint32(0x10000000 + exc_int1 * 4, int1_handler)
        rp2040.write_uint16(int1_handler, opcode_movs(r0, 0x55))
        rp2040.write_uint16(int1_handler + 2, opcode_bx(lr))
        # Exception handler should start at this point.
        rp2040.step()  # MOVS r0, 0x55
        assert rp2040.core.ipsr == exc_int1
        assert rp2040.core.pc == int1_handler + 2
        assert rp2040.core.registers[r0] == 0x55
        rp2040.step()  # BX lr
        # Exception handler should return at this point.
        assert rp2040.core.pc == 0x10004000
        assert rp2040.core.registers[r0] == 0x44
        assert rp2040.core.ipsr == 0

    def test_returns_correctly_from_exception_with_pop_lr(self, rp2040_factory):
        int1 = 1 << 1
        int1_handler = 0x10000100
        exc_int1 = 16 + 1
        rp2040 = rp2040_factory()
        rp2040.core.sp = 0x20004000
        rp2040.core.pc = 0x10004001
        rp2040.core.registers[r4] = 105
        rp2040.core.pending_interrupts = int1
        rp2040.core.enabled_interrupts = int1
        rp2040.core.interrupts_updated = True
        rp2040.write_uint32(VTOR, 0x10000000)
        rp2040.write_uint32(0x10000000 + exc_int1 * 4, int1_handler)
        rp2040.write_uint16(int1_handler, opcode_push(True, 0b01110000))
        rp2040.write_uint16(int1_handler + 2, opcode_movs(r4, 42))
        rp2040.write_uint16(int1_handler + 4, opcode_pop(True, 0b01110000))
        # Exception handler should start at this point.
        rp2040.step()  # push {r4, r5, r6, lr}
        assert rp2040.core.ipsr == exc_int1
        assert rp2040.core.pc == int1_handler + 2
        rp2040.step()  # mov r4, 42
        assert rp2040.core.registers[r4] == 42
        rp2040.step()  # pop {r4, r5, r6, pc}
        # Exception handler should return at this point.
        assert rp2040.core.pc == 0x10004000
        assert rp2040.core.registers[r4] == 105
        assert rp2040.core.ipsr == 0

    def test_clears_the_pending_interrupt_flag_in_exception_entry_for_user_irqs_above_25(self, rp2040_factory):
        int31 = 1 << 31
        int31_handler = 0x10003100
        exc_int31 = 16 + 31
        rp2040 = rp2040_factory()
        rp2040.core.sp = 0x20004000
        rp2040.core.pc = 0x10004001
        rp2040.write_uint32(NVIC_ISPR, int31)  # Set IRQ31 to pending
        rp2040.core.enabled_interrupts = int31
        rp2040.core.interrupts_updated = True
        rp2040.write_uint32(VTOR, 0x10000000)
        rp2040.write_uint32(0x10000000 + exc_int31 * 4, int31_handler)
        rp2040.write_uint16(int31_handler, opcode_nop())
        assert rp2040.core.pending_interrupts == int31
        # Exception handler should start at this point.
        rp2040.step()  # nop
        assert rp2040.core.pending_interrupts == 0  # interrupt flag has been cleared
        assert rp2040.read_uint32(NVIC_ISPR) == 0


class TestNvicRegisters:
    def test_writing_to_nvic_ispr_sets_the_corresponding_pending_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.pending_interrupts = 0x1
        rp2040.write_uint32(NVIC_ISPR, 0x10)
        assert rp2040.core.pending_interrupts == 0x11

    def test_writing_to_nvic_icpr_clears_corresponding_pending_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.pending_interrupts = 0xFF00000F
        rp2040.write_uint32(NVIC_ICPR, 0x1000000F)
        # Only the high 6 bits are actually cleared (see commit 5bc96994 for details)
        assert rp2040.read_uint32(NVIC_ISPR) == 0xEF00000F

    def test_writing_to_nvic_iser_sets_the_corresponding_enabled_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.enabled_interrupts = 0x1
        rp2040.write_uint32(NVIC_ISER, 0x10)
        assert rp2040.core.enabled_interrupts == 0x11

    def test_writing_to_nvic_icer_clears_corresponding_enabled_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.enabled_interrupts = 0xFF
        rp2040.write_uint32(NVIC_ICER, 0x10)
        assert rp2040.core.enabled_interrupts == 0xEF

    def test_reading_from_nvic_iser_icer_returns_the_current_enabled_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.enabled_interrupts = 0x1
        assert rp2040.read_uint32(NVIC_ISER) == 0x1
        assert rp2040.read_uint32(NVIC_ICER) == 0x1

    def test_reading_from_nvic_ispr_icpr_returns_the_current_pending_interrupt_bits(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.pending_interrupts = 0x2
        assert rp2040.read_uint32(NVIC_ISPR) == 0x2
        assert rp2040.read_uint32(NVIC_ICPR) == 0x2

    def test_updates_the_interrupt_levels_correctly_when_writing_to_nvic_ipr3(self, rp2040_factory):
        rp2040 = rp2040_factory()
        # Set the priority of interrupt number 14 to 2
        rp2040.write_uint32(0xE000E40C, 0x00800000)
        interrupt_priorities = rp2040.core.interrupt_priorities
        assert (interrupt_priorities[0] & 0xFFFFFFFF) == (~(1 << 14) & 0xFFFFFFFF)
        assert interrupt_priorities[1] == 0
        assert interrupt_priorities[2] == 1 << 14
        assert interrupt_priorities[3] == 0
        assert rp2040.read_uint32(0xE000E40C) == 0x00800000

    def test_returns_the_correct_interrupt_priorities_when_reading_from_nvic_ipr5(self, rp2040_factory):
        rp2040 = rp2040_factory()
        rp2040.core.interrupt_priorities[0] = 0
        rp2040.core.interrupt_priorities[1] = 0x001FFFFF  # interrupts 0 ... 20
        rp2040.core.interrupt_priorities[2] = 0x00200000  # interrupt 21
        rp2040.core.interrupt_priorities[3] = 0xFFC00000  # interrupt 22 ... 31
        assert rp2040.read_uint32(0xE000E414) == 0xC0C08040
