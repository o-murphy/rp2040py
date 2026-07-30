from typing import TYPE_CHECKING

from rp2040py.interpolator import Interpolator
from rp2040py.utils.bit import s32, u32

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPSIO",)

CPUID = 0x000

# GPIO
GPIO_IN = 0x004  # Input value for GPIO pins
GPIO_HI_IN = 0x008  # Input value for QSPI pins
GPIO_OUT = 0x010  # GPIO output value
GPIO_OUT_SET = 0x014  # GPIO output value set
GPIO_OUT_CLR = 0x018  # GPIO output value clear
GPIO_OUT_XOR = 0x01C  # GPIO output value XOR
GPIO_OE = 0x020  # GPIO output enable
GPIO_OE_SET = 0x024  # GPIO output enable set
GPIO_OE_CLR = 0x028  # GPIO output enable clear
GPIO_OE_XOR = 0x02C  # GPIO output enable XOR
GPIO_HI_OUT = 0x030  # QSPI output value
GPIO_HI_OUT_SET = 0x034  # QSPI output value set
GPIO_HI_OUT_CLR = 0x038  # QSPI output value clear
GPIO_HI_OUT_XOR = 0x03C  # QSPI output value XOR
GPIO_HI_OE = 0x040  # QSPI output enable
GPIO_HI_OE_SET = 0x044  # QSPI output enable set
GPIO_HI_OE_CLR = 0x048  # QSPI output enable clear
GPIO_HI_OE_XOR = 0x04C  # QSPI output enable XOR

GPIO_MASK = 0x3FFFFFFF

# HARDWARE DIVIDER
DIV_UDIVIDEND = 0x060  # Divider unsigned dividend
DIV_UDIVISOR = 0x064  # Divider unsigned divisor
DIV_SDIVIDEND = 0x068  # Divider signed dividend
DIV_SDIVISOR = 0x06C  # Divider signed divisor
DIV_QUOTIENT = 0x070  # Divider result quotient
DIV_REMAINDER = 0x074  # Divider result remainder
DIV_CSR = 0x078

# INTERPOLATOR
INTERP0_ACCUM0 = 0x080  # Read/write access to accumulator 0
INTERP0_ACCUM1 = 0x084  # Read/write access to accumulator 1
INTERP0_BASE0 = 0x088  # Read/write access to BASE0 register
INTERP0_BASE1 = 0x08C  # Read/write access to BASE1 register
INTERP0_BASE2 = 0x090  # Read/write access to BASE2 register
INTERP0_POP_LANE0 = 0x094  # Read LANE0 result, and simultaneously write lane results to both accumulators (POP)
INTERP0_POP_LANE1 = 0x098  # Read LANE1 result, and simultaneously write lane results to both accumulators (POP)
INTERP0_POP_FULL = 0x09C  # Read FULL result, and simultaneously write lane results to both accumulators (POP)
INTERP0_PEEK_LANE0 = 0x0A0  # Read LANE0 result, without altering any internal state (PEEK)
INTERP0_PEEK_LANE1 = 0x0A4  # Read LANE1 result, without altering any internal state (PEEK)
INTERP0_PEEK_FULL = 0x0A8  # Read FULL result, without altering any internal state (PEEK)
INTERP0_CTRL_LANE0 = 0x0AC  # Control register for lane 0
INTERP0_CTRL_LANE1 = 0x0B0  # Control register for lane 1
INTERP0_ACCUM0_ADD = 0x0B4  # Values written here are atomically added to ACCUM0
INTERP0_ACCUM1_ADD = 0x0B8  # Values written here are atomically added to ACCUM1
INTERP0_BASE_1AND0 = 0x0BC  # On write, the lower 16 bits go to BASE0, upper bits to BASE1 simultaneously
INTERP1_ACCUM0 = 0x0C0  # Read/write access to accumulator 0
INTERP1_ACCUM1 = 0x0C4  # Read/write access to accumulator 1
INTERP1_BASE0 = 0x0C8  # Read/write access to BASE0 register
INTERP1_BASE1 = 0x0CC  # Read/write access to BASE1 register
INTERP1_BASE2 = 0x0D0  # Read/write access to BASE2 register
INTERP1_POP_LANE0 = 0x0D4  # Read LANE0 result, and simultaneously write lane results to both accumulators (POP)
INTERP1_POP_LANE1 = 0x0D8  # Read LANE1 result, and simultaneously write lane results to both accumulators (POP)
INTERP1_POP_FULL = 0x0DC  # Read FULL result, and simultaneously write lane results to both accumulators (POP)
INTERP1_PEEK_LANE0 = 0x0E0  # Read LANE0 result, without altering any internal state (PEEK)
INTERP1_PEEK_LANE1 = 0x0E4  # Read LANE1 result, without altering any internal state (PEEK)
INTERP1_PEEK_FULL = 0x0E8  # Read FULL result, without altering any internal state (PEEK)
INTERP1_CTRL_LANE0 = 0x0EC  # Control register for lane 0
INTERP1_CTRL_LANE1 = 0x0F0  # Control register for lane 1
INTERP1_ACCUM0_ADD = 0x0F4  # Values written here are atomically added to ACCUM0
INTERP1_ACCUM1_ADD = 0x0F8  # Values written here are atomically added to ACCUM1
INTERP1_BASE_1AND0 = 0x0FC  # On write, the lower 16 bits go to BASE0, upper bits to BASE1 simultaneously

# SPINLOCK
SPINLOCK_ST = 0x5C
SPINLOCK0 = 0x100
SPINLOCK31 = 0x17C


def _js_trunc_rem(a: int, b: int) -> int:
    """Mimics JS `%` (truncating remainder, sign of dividend), unlike Python's `%`
    (floored remainder, sign of divisor)."""
    return a - b * int(a / b)


class RPSIO:
    def __init__(self, rp2040: "RP2040"):
        self.rp2040 = rp2040

        self.gpio_value = 0
        self.gpio_output_enable = 0
        self.qspi_gpio_value = 0
        self.qspi_gpio_output_enable = 0
        self.div_dividend = 0
        self.div_divisor = 1
        # Genuinely fractional in the signed/unsigned division branches below (matches upstream
        # rp2040js, which never truncates the JS `/` result before storing it).
        self.div_quotient: int | float = 0
        self.div_remainder: int | float = 0
        self.div_csr = 0
        self.spin_lock = 0
        self.interp0 = Interpolator(0)
        self.interp1 = Interpolator(1)

    def update_hardware_divider(self, signed: bool) -> None:
        if self.div_divisor == 0:
            self.div_quotient = -1 if self.div_dividend > 0 else 1
            self.div_remainder = self.div_dividend
        elif signed:
            dividend, divisor = s32(self.div_dividend), s32(self.div_divisor)
            self.div_quotient = dividend / divisor
            self.div_remainder = _js_trunc_rem(dividend, divisor)
        else:
            dividend, divisor = u32(self.div_dividend), u32(self.div_divisor)
            self.div_quotient = dividend / divisor
            self.div_remainder = dividend % divisor
        self.div_csr = 0b11
        self.rp2040.core.cycles += 8

    def read_uint32(self, offset: int) -> int | float:
        if SPINLOCK0 <= offset <= SPINLOCK31:
            bit_index_mask = 1 << ((offset - SPINLOCK0) // 4)
            if self.spin_lock & bit_index_mask:
                return 0
            self.spin_lock |= bit_index_mask
            return bit_index_mask

        if offset == GPIO_IN:
            return self.rp2040.gpio_values
        if offset == GPIO_HI_IN:
            qspi = self.rp2040.qspi
            result = 0
            for qspi_index in range(len(qspi)):
                if qspi[qspi_index].input_value:
                    result |= 1 << qspi_index
            return result
        if offset == GPIO_OUT:
            return self.gpio_value
        if offset == GPIO_OE:
            return self.gpio_output_enable
        if offset == GPIO_HI_OUT:
            return self.qspi_gpio_value
        if offset == GPIO_HI_OE:
            return self.qspi_gpio_output_enable
        if offset in (
            GPIO_OUT_SET,
            GPIO_OUT_CLR,
            GPIO_OUT_XOR,
            GPIO_OE_SET,
            GPIO_OE_CLR,
            GPIO_OE_XOR,
            GPIO_HI_OUT_SET,
            GPIO_HI_OUT_CLR,
            GPIO_HI_OUT_XOR,
            GPIO_HI_OE_SET,
            GPIO_HI_OE_CLR,
            GPIO_HI_OE_XOR,
        ):
            return 0  # TODO verify with silicone
        if offset == CPUID:
            # Returns the current CPU core id (always 0 for now)
            return 0
        if offset == SPINLOCK_ST:
            return self.spin_lock
        if offset == DIV_UDIVIDEND:
            return self.div_dividend
        if offset == DIV_SDIVIDEND:
            return self.div_dividend
        if offset == DIV_UDIVISOR:
            return self.div_divisor
        if offset == DIV_SDIVISOR:
            return self.div_divisor
        if offset == DIV_QUOTIENT:
            self.div_csr &= ~0b10
            return self.div_quotient
        if offset == DIV_REMAINDER:
            return self.div_remainder
        if offset == DIV_CSR:
            return self.div_csr
        if offset == INTERP0_ACCUM0:
            return self.interp0.accum0
        if offset == INTERP0_ACCUM1:
            return self.interp0.accum1
        if offset == INTERP0_BASE0:
            return self.interp0.base0
        if offset == INTERP0_BASE1:
            return self.interp0.base1
        if offset == INTERP0_BASE2:
            return self.interp0.base2
        if offset == INTERP0_CTRL_LANE0:
            return self.interp0.ctrl0
        if offset == INTERP0_CTRL_LANE1:
            return self.interp0.ctrl1
        if offset == INTERP0_PEEK_LANE0:
            return self.interp0.result0
        if offset == INTERP0_PEEK_LANE1:
            return self.interp0.result1
        if offset == INTERP0_PEEK_FULL:
            return self.interp0.result2
        if offset == INTERP0_POP_LANE0:
            value = self.interp0.result0
            self.interp0.writeback()
            return value
        if offset == INTERP0_POP_LANE1:
            value = self.interp0.result1
            self.interp0.writeback()
            return value
        if offset == INTERP0_POP_FULL:
            value = self.interp0.result2
            self.interp0.writeback()
            return value
        if offset == INTERP0_ACCUM0_ADD:
            return self.interp0.smresult0
        if offset == INTERP0_ACCUM1_ADD:
            return self.interp0.smresult1
        if offset == INTERP1_ACCUM0:
            return self.interp1.accum0
        if offset == INTERP1_ACCUM1:
            return self.interp1.accum1
        if offset == INTERP1_BASE0:
            return self.interp1.base0
        if offset == INTERP1_BASE1:
            return self.interp1.base1
        if offset == INTERP1_BASE2:
            return self.interp1.base2
        if offset == INTERP1_CTRL_LANE0:
            return self.interp1.ctrl0
        if offset == INTERP1_CTRL_LANE1:
            return self.interp1.ctrl1
        if offset == INTERP1_PEEK_LANE0:
            return self.interp1.result0
        if offset == INTERP1_PEEK_LANE1:
            return self.interp1.result1
        if offset == INTERP1_PEEK_FULL:
            return self.interp1.result2
        if offset == INTERP1_POP_LANE0:
            value = self.interp1.result0
            self.interp1.writeback()
            return value
        if offset == INTERP1_POP_LANE1:
            value = self.interp1.result1
            self.interp1.writeback()
            return value
        if offset == INTERP1_POP_FULL:
            value = self.interp1.result2
            self.interp1.writeback()
            return value
        if offset == INTERP1_ACCUM0_ADD:
            return self.interp1.smresult0
        if offset == INTERP1_ACCUM1_ADD:
            return self.interp1.smresult1

        print(f"warning: Read from invalid SIO address: {offset:x}")
        return 0xFFFFFFFF

    def write_uint32(self, offset: int, value: int) -> None:
        if SPINLOCK0 <= offset <= SPINLOCK31:
            bit_index_mask = ~(1 << ((offset - SPINLOCK0) // 4))
            self.spin_lock &= bit_index_mask
            return

        prev_gpio_value = self.gpio_value
        prev_gpio_output_enable = self.gpio_output_enable

        if offset == GPIO_OUT:
            self.gpio_value = value & GPIO_MASK
        elif offset == GPIO_OUT_SET:
            self.gpio_value |= value & GPIO_MASK
        elif offset == GPIO_OUT_CLR:
            self.gpio_value &= ~value
        elif offset == GPIO_OUT_XOR:
            self.gpio_value ^= value & GPIO_MASK
        elif offset == GPIO_OE:
            self.gpio_output_enable = value & GPIO_MASK
        elif offset == GPIO_OE_SET:
            self.gpio_output_enable |= value & GPIO_MASK
        elif offset == GPIO_OE_CLR:
            self.gpio_output_enable &= ~value
        elif offset == GPIO_OE_XOR:
            self.gpio_output_enable ^= value & GPIO_MASK
        elif offset == GPIO_HI_OUT:
            self.qspi_gpio_value = value & GPIO_MASK
        elif offset == GPIO_HI_OUT_SET:
            self.qspi_gpio_value |= value & GPIO_MASK
        elif offset == GPIO_HI_OUT_CLR:
            self.qspi_gpio_value &= ~value
        elif offset == GPIO_HI_OUT_XOR:
            self.qspi_gpio_value ^= value & GPIO_MASK
        elif offset == GPIO_HI_OE:
            self.qspi_gpio_output_enable = value & GPIO_MASK
        elif offset == GPIO_HI_OE_SET:
            self.qspi_gpio_output_enable |= value & GPIO_MASK
        elif offset == GPIO_HI_OE_CLR:
            self.qspi_gpio_output_enable &= ~value
        elif offset == GPIO_HI_OE_XOR:
            self.qspi_gpio_output_enable ^= value & GPIO_MASK
        elif offset == DIV_UDIVIDEND:
            self.div_dividend = value
            self.update_hardware_divider(False)
        elif offset == DIV_SDIVIDEND:
            self.div_dividend = value
            self.update_hardware_divider(True)
        elif offset == DIV_UDIVISOR:
            self.div_divisor = value
            self.update_hardware_divider(False)
        elif offset == DIV_SDIVISOR:
            self.div_divisor = value
            self.update_hardware_divider(True)
        elif offset == DIV_QUOTIENT:
            self.div_quotient = value
            self.div_csr = 0b11
        elif offset == DIV_REMAINDER:
            self.div_remainder = value
            self.div_csr = 0b11
        elif offset == INTERP0_ACCUM0:
            self.interp0.accum0 = value
            self.interp0.update()
        elif offset == INTERP0_ACCUM1:
            self.interp0.accum1 = value
            self.interp0.update()
        elif offset == INTERP0_BASE0:
            self.interp0.base0 = value
            self.interp0.update()
        elif offset == INTERP0_BASE1:
            self.interp0.base1 = value
            self.interp0.update()
        elif offset == INTERP0_BASE2:
            self.interp0.base2 = value
            self.interp0.update()
        elif offset == INTERP0_CTRL_LANE0:
            self.interp0.ctrl0 = value
            self.interp0.update()
        elif offset == INTERP0_CTRL_LANE1:
            self.interp0.ctrl1 = value
            self.interp0.update()
        elif offset == INTERP0_ACCUM0_ADD:
            self.interp0.accum0 += value
            self.interp0.update()
        elif offset == INTERP0_ACCUM1_ADD:
            self.interp0.accum1 += value
            self.interp0.update()
        elif offset == INTERP0_BASE_1AND0:
            self.interp0.set_base01(value)
        elif offset == INTERP1_ACCUM0:
            self.interp1.accum0 = value
            self.interp1.update()
        elif offset == INTERP1_ACCUM1:
            self.interp1.accum1 = value
            self.interp1.update()
        elif offset == INTERP1_BASE0:
            self.interp1.base0 = value
            self.interp1.update()
        elif offset == INTERP1_BASE1:
            self.interp1.base1 = value
            self.interp1.update()
        elif offset == INTERP1_BASE2:
            self.interp1.base2 = value
            self.interp1.update()
        elif offset == INTERP1_CTRL_LANE0:
            self.interp1.ctrl0 = value
            self.interp1.update()
        elif offset == INTERP1_CTRL_LANE1:
            self.interp1.ctrl1 = value
            self.interp1.update()
        elif offset == INTERP1_ACCUM0_ADD:
            self.interp1.accum0 += value
            self.interp1.update()
        elif offset == INTERP1_ACCUM1_ADD:
            self.interp1.accum1 += value
            self.interp1.update()
        elif offset == INTERP1_BASE_1AND0:
            self.interp1.set_base01(value)
        else:
            print(f"warning: Write to invalid SIO address: {offset:x}, value={value:x}")

        pins_to_update = (self.gpio_value ^ prev_gpio_value) | (self.gpio_output_enable ^ prev_gpio_output_enable)
        if pins_to_update:
            gpio = self.rp2040.gpio
            for gpio_index in range(len(gpio)):
                if pins_to_update & (1 << gpio_index):
                    gpio[gpio_index].check_for_updates()
