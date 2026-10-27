from typing import TYPE_CHECKING, Any

from rp2040py.interpolator import Interpolator
from rp2040py.utils.bit import s32, u32

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPSIO",)

# A variable, not an inline literal, at the two logger.warning() call sites below - matches
# _rp2040.py's own LOG_NAME convention, and (not just style) ruff's PLE1205 can statically count
# %-placeholders in a literal string argument but not in a variable, so an inline "SIO" literal
# gets misread as a stdlib logging.warning(msg, *args)-style call with a stray extra positional
# arg (this project's own Logger Protocol is warning(component_name, message), a different
# signature entirely) - a real, reproduced pre-commit failure, not just an IDE hint.
LOG_NAME = "SIO"

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

GPIO_MASK = 0x3FFFFFFF  # GPIO0..29 (datasheet: bits 31:30 reserved)
QSPI_MASK = 0x3F  # the QSPI pins SCLK, SSn, SD0..3: GPIO_HI_OUT / GPIO_HI_OE are 5:0
INTERP_ADD_MASK = (
    0xFFFFFF  # INTERPx_ACCUMy_ADD is 23:0 (datasheet, and SIO_INTERP0_ACCUM0_ADD_BITS 0x00ffffff in pico-sdk's sio.h)
)

# INTER-CORE FIFO - the offsets; the FIFOs themselves are in RPSIO (docs/records/0104-second-core-seams-and-architecture.md)
FIFO_ST = 0x050
FIFO_WR = 0x054
FIFO_RD = 0x058

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


class _CoreState:
    """What SIO keeps per core (datasheet 2.3.1): the divider and the two interpolators. Core 0's live in the `RPSIO` itself (the attributes tests and shells have always used); core 1's are
    here, and an access by core 1 swaps the two for its duration (`RPSIO._swap`), so the register code below is written once."""

    def __init__(self) -> None:
        self.div_dividend = 0
        self.div_divisor = 0
        self.div_quotient: int | float = 0
        self.div_remainder: int | float = 0
        self.div_csr = 1
        self.interp0 = Interpolator(0)
        self.interp1 = Interpolator(1)

    def reset(self) -> None:
        self.div_dividend = 0
        self.div_divisor = 0
        self.div_quotient = 0
        self.div_remainder = 0
        self.div_csr = 1
        self.interp0.reset()
        self.interp1.reset()


class RPSIO:
    def __init__(self, rp2040: "RP2040"):
        self.rp2040 = rp2040
        self._core1 = _CoreState()  # core 1's divider and interpolators, while core 0's are in the attributes below (and the other way round during an access by core 1)
        # The inter-core FIFOs (datasheet 2.3.1.4) and the boot ROM's launch handshake, answered here (record 0104, D7 b); `multicore = False` gives back the old stub (the registers warn and read as
        # 0xFFFFFFFF) for a test of that. `_fifo[0]` carries core 0 -> core 1, `_fifo[1]` core 1 -> core 0.
        self.multicore = True
        self._fifo: list[list[int]] = [[], []]
        self._roe = [False, False]
        self._wof = [False, False]
        self._fifo_irq = [False, False]
        self._launch_seq = 0
        self._launch_words = [0, 0, 0]
        self._core1_launched = False

        self.gpio_value = 0
        self.gpio_output_enable = 0
        self.qspi_gpio_value = 0
        self.qspi_gpio_output_enable = 0
        self.div_dividend = 0
        self.div_divisor = 0  # the datasheet's reset value of DIV_xDIVISOR
        # Genuinely fractional in the signed/unsigned division branches below (matches upstream
        # rp2040js, which never truncates the JS `/` result before storing it).
        self.div_quotient: int | float = 0
        self.div_remainder: int | float = 0
        self.div_csr = 1  # DIV_CSR.READY resets to 1 (DIRTY to 0)
        self.spin_lock = 0
        self.interp0 = Interpolator(0)
        self.interp1 = Interpolator(1)

    def reset(self) -> None:
        """SIO's own registers, back to power-on (0089 Phase 5).

        `gpio_output_enable` is the one that matters most: together with each pad's own `ctrl`
        (`GPIOPin.reset()`), it is what makes a GPIO stop driving across a reset - an LED left on
        by firmware goes dark, the way it does on silicon. `rp2040` is wiring, not state, and is
        left alone.

        Note SIO is not in `RESETS`/`PSM.WDSEL` at all on real hardware - it sits in the core's own
        domain - so unlike the peripherals in `RP2040.reset()`'s WDSEL-gated list, this runs on
        every reset path."""
        self.gpio_value = 0
        self.gpio_output_enable = 0
        self.qspi_gpio_value = 0
        self.qspi_gpio_output_enable = 0
        self.div_dividend = 0
        self.div_divisor = 0  # the datasheet's reset value of DIV_xDIVISOR
        self.div_quotient = 0
        self.div_remainder = 0
        self.div_csr = 1  # DIV_CSR.READY resets to 1 (DIRTY to 0)
        self.spin_lock = 0
        self.interp0.reset()
        self.interp1.reset()
        self._core1.reset()
        self.reset_core1()

    def reset_core1(self) -> None:
        """Core 1 goes back to its boot state (PSM, a chip reset): the FIFOs are emptied and the handshake starts over."""
        self._fifo = [[], []]
        self._roe = [False, False]
        self._wof = [False, False]
        self._launch_seq = 0
        self._core1_launched = False
        self._update_fifo_irqs()

    def core1_boot_done(self) -> None:
        """Core 1 has come out of reset and the boot ROM it runs has drained its mailbox: it pushes a 0 to core 0 (and a SEV after it), which the pico-sdk's `multicore_reset_core1()` waits for."""
        if not self.multicore:
            return
        self._fifo[1].append(0)
        self.rp2040.send_event(0)
        self._update_fifo_irqs()

    def _swap(self) -> None:
        """Exchange the per-core registers with core 1's (an access by core 1 runs between two of these)."""
        other = self._core1
        self.div_dividend, other.div_dividend = other.div_dividend, self.div_dividend
        self.div_divisor, other.div_divisor = other.div_divisor, self.div_divisor
        self.div_quotient, other.div_quotient = other.div_quotient, self.div_quotient
        self.div_remainder, other.div_remainder = other.div_remainder, self.div_remainder
        self.div_csr, other.div_csr = other.div_csr, self.div_csr
        self.interp0, other.interp0 = other.interp0, self.interp0
        self.interp1, other.interp1 = other.interp1, self.interp1

    # -- the FIFOs -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _fifo_status(self, core: int) -> int:
        return (
            (8 if self._roe[core] else 0)
            | (4 if self._wof[core] else 0)
            | (2 if len(self._fifo[core]) < 8 else 0)
            | (1 if self._fifo[1 - core] else 0)
        )

    def _update_fifo_irqs(self) -> None:
        """The IRQ of each core (SIO_IRQ_PROC0 / PROC1, lines 15 and 16) is the OR of VLD, ROE and WOF of its FIFO_ST; a line is told only when it changes."""
        for core in range(2):
            level = (self._fifo_status(core) & 0b1101) != 0
            if level != self._fifo_irq[core]:
                self._fifo_irq[core] = level
                self.rp2040.set_interrupt(15 + core, level)

    def _fifo_read(self, offset: int, core: int) -> int:
        if offset == FIFO_ST:
            return self._fifo_status(core)
        if offset == FIFO_WR:  # write-only
            self.rp2040.logger.warning(LOG_NAME, f"Read from invalid SIO address: {offset:x}")
            return 0xFFFFFFFF
        rx = self._fifo[1 - core]
        word = 0
        if rx:
            word = rx.pop(0)
        else:
            self._roe[core] = True  # a read of an empty FIFO is ignored, and remembered
        self._update_fifo_irqs()
        return word

    def _fifo_write(self, offset: int, value: int, core: int) -> None:
        if offset == FIFO_ST:  # writing anything clears the two sticky flags
            self._roe[core] = self._wof[core] = False
            self._update_fifo_irqs()
            return
        if (
            core == 0 and not self._core1_launched and hasattr(self.rp2040, "launch_core1")
        ):  # (a chip that cannot launch core 1 has no boot ROM to answer)
            self._boot_rom_answer(value & 0xFFFFFFFF)
            return
        if len(self._fifo[core]) < 8:
            self._fifo[core].append(value & 0xFFFFFFFF)
        else:
            self._wof[core] = True  # a write to a full FIFO is ignored, and remembered
        self._update_fifo_irqs()

    def _boot_rom_answer(self, word: int) -> None:
        """Core 1 is still in the boot ROM: it echoes every word of core 0's handshake (0, 0, 1, vector table, sp, entry; datasheet 2.8.2) with a SEV after it, starts over on a wrong word and is
        launched by the last one."""
        expected = (0, 0, 1)
        if self._launch_seq < 3:
            if word == expected[self._launch_seq]:
                self._launch_seq += 1
            else:
                self._launch_seq = 1 if word == 0 else 0
        elif word == 0:
            self._launch_seq = 1  # core 0 restarts (it drains before a 0)
        else:
            self._launch_words[self._launch_seq - 3] = word
            self._launch_seq += 1
        self._fifo[1].append(word)  # the echo
        self.rp2040.send_event(0)  # (the ROM's SEV)
        self._update_fifo_irqs()
        if self._launch_seq >= 6:
            self._launch_seq = 0
            self._core1_launched = True
            self.rp2040.launch_core1(*self._launch_words)

    def snapshot(self) -> dict[str, Any]:
        """The block's state as plain values: the four GPIO words, core 0's divider, the spin locks and both interpolators (`i0.accum0` ...)."""
        state: dict[str, Any] = {
            "gpio_value": self.gpio_value,
            "gpio_output_enable": self.gpio_output_enable,
            "qspi_gpio_value": self.qspi_gpio_value,
            "qspi_gpio_output_enable": self.qspi_gpio_output_enable,
            "div_dividend": self.div_dividend,
            "div_divisor": self.div_divisor,
            "div_quotient": self.div_quotient,
            "div_remainder": self.div_remainder,
            "div_csr": self.div_csr,
            "spin_lock": self.spin_lock,
        }
        for index, interp in enumerate((self.interp0, self.interp1)):
            for name in [
                "accum0",
                "accum1",
                "base0",
                "base1",
                "base2",
                "ctrl0",
                "ctrl1",
                "result0",
                "result1",
                "result2",
                "smresult0",
                "smresult1",
            ]:
                state[f"i{index}.{name}"] = getattr(interp, name)
        return state

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
        (self.rp2040.cores[1] if getattr(self.rp2040, "initiator", 0) else self.rp2040.core).cycles += 8

    def read_uint32(self, offset: int) -> int | float:
        if getattr(self.rp2040, "initiator", 0):  # (a stand-in chip of a test has no second core)
            self._swap()
            try:
                return self._read_uint32(offset)
            finally:
                self._swap()
        return self._read_uint32(offset)

    def _read_uint32(self, offset: int) -> int | float:
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
            # The id of the core that is reading
            return getattr(self.rp2040, "initiator", 0)
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
        if offset in (FIFO_ST, FIFO_WR, FIFO_RD):
            if self.multicore:
                return self._fifo_read(offset, getattr(self.rp2040, "initiator", 0))
            self.rp2040.logger.warning(
                LOG_NAME,
                "Inter-core FIFO (0x50-0x58) is not implemented. core1/_thread is unsupported "
                "(see docs/records/0053-core1-and-inter-core-fifo.md)",
            )
            return 0xFFFFFFFF

        self.rp2040.logger.warning(LOG_NAME, f"Read from invalid SIO address: {offset:x}")
        return 0xFFFFFFFF

    def write_uint32(self, offset: int, value: int) -> None:
        if getattr(self.rp2040, "initiator", 0):
            self._swap()
            try:
                self._write_uint32(offset, value)
            finally:
                self._swap()
            return
        self._write_uint32(offset, value)

    def _write_uint32(self, offset: int, value: int) -> None:
        if self.multicore and offset in (FIFO_ST, FIFO_WR):
            self._fifo_write(offset, value, getattr(self.rp2040, "initiator", 0))
            return
        if SPINLOCK0 <= offset <= SPINLOCK31:
            bit_index_mask = ~(1 << ((offset - SPINLOCK0) // 4))
            self.spin_lock &= bit_index_mask
            return

        prev_gpio_value = self.gpio_value
        prev_gpio_output_enable = self.gpio_output_enable
        prev_qspi_gpio_value = self.qspi_gpio_value
        prev_qspi_gpio_output_enable = self.qspi_gpio_output_enable

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
            self.qspi_gpio_value = value & QSPI_MASK
        elif offset == GPIO_HI_OUT_SET:
            self.qspi_gpio_value |= value & QSPI_MASK
        elif offset == GPIO_HI_OUT_CLR:
            self.qspi_gpio_value &= ~value
        elif offset == GPIO_HI_OUT_XOR:
            self.qspi_gpio_value ^= value & QSPI_MASK
        elif offset == GPIO_HI_OE:
            self.qspi_gpio_output_enable = value & QSPI_MASK
        elif offset == GPIO_HI_OE_SET:
            self.qspi_gpio_output_enable |= value & QSPI_MASK
        elif offset == GPIO_HI_OE_CLR:
            self.qspi_gpio_output_enable &= ~value
        elif offset == GPIO_HI_OE_XOR:
            self.qspi_gpio_output_enable ^= value & QSPI_MASK
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
            self.interp0.accum0 += value & INTERP_ADD_MASK
            self.interp0.update()
        elif offset == INTERP0_ACCUM1_ADD:
            self.interp0.accum1 += value & INTERP_ADD_MASK
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
            self.interp1.accum0 += value & INTERP_ADD_MASK
            self.interp1.update()
        elif offset == INTERP1_ACCUM1_ADD:
            self.interp1.accum1 += value & INTERP_ADD_MASK
            self.interp1.update()
        elif offset == INTERP1_BASE_1AND0:
            self.interp1.set_base01(value)
        else:
            self.rp2040.logger.warning(LOG_NAME, f"Write to invalid SIO address: {offset:x}, value={value:x}")

        pins_to_update = (self.gpio_value ^ prev_gpio_value) | (self.gpio_output_enable ^ prev_gpio_output_enable)
        if pins_to_update:
            gpio = self.rp2040.gpio
            for gpio_index in range(len(gpio)):
                if pins_to_update & (1 << gpio_index):
                    gpio[gpio_index].check_for_updates()
        # the QSPI pins too: GPIO_HI_OUT / GPIO_HI_OE drive them (datasheet table 282), and whatever listens to them must hear it
        qspi_to_update = (self.qspi_gpio_value ^ prev_qspi_gpio_value) | (
            self.qspi_gpio_output_enable ^ prev_qspi_gpio_output_enable
        )
        if qspi_to_update:
            qspi = self.rp2040.qspi
            for qspi_index in range(len(qspi)):
                if qspi_to_update & (1 << qspi_index):
                    qspi[qspi_index].check_for_updates()
