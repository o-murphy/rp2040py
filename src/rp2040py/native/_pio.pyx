# cython: language_level=3
"""Native Cython port of `_pio.py`'s `RPPIO` - the biggest remaining pure-Python cost during a
CYW43 (PIO-driven SPI) boot (~43% of profiled self-time after the GPIOPin port): `step()`,
`check_changed_pins()` and `pin_values_changed()`/`pin_directions_changed()` run once per PIO clock
edge (millions of times) driving the emulated gSPI bit-bang.

Satisfies the `Peripheral` Protocol (peripherals/peripheral.py) structurally rather than inheriting
`BasePeripheral` - a `cdef class` can't inherit a pure-Python base, but the bus only needs
`read_uint32`/`write_uint32`/`write_uint32_atomic` (+ `rp2040`/`name`/`raw_write_value`), so the
small `BasePeripheral` surface is reproduced inline here. `rp2040`/`machines`/`gpio` stay
object-typed (same reasoning as native/_state_machine.pyx): the StateMachines are reached as
ordinary objects, and their own methods are already native. Behaviourally identical to `_pio.py`;
the pure module remains the reference the facade (`peripherals/pio.py`) falls back to.

`pin_values`/`pin_directions` are exposed in `_pio.pxd` so native/_gpio_pin.pyx can cimport RPPIO
and read them as direct C field reads in its hot path.
"""
import asyncio

from rp2040py.peripherals.peripheral import ATOMIC_NORMAL, atomic_update
from rp2040py.peripherals.pio_registers import (
    CTRL,
    DBG_CFGINFO,
    DBG_PADOE,
    DBG_PADOUT,
    DREQ_RX0,
    DREQ_RX1,
    DREQ_TX0,
    DREQ_TX1,
    FDEBUG,
    FLEVEL,
    FSTAT,
    INPUT_SYNC_BYPASS,
    INSTR_MEM0,
    INSTR_MEM31,
    INTR,
    IRQ,
    IRQ0_INTE,
    IRQ0_INTF,
    IRQ0_INTS,
    IRQ1_INTE,
    IRQ1_INTF,
    IRQ1_INTS,
    IRQ_FORCE,
    RXF0,
    RXF1,
    RXF2,
    RXF3,
    SM0_CLKDIV,
    SM0_PINCTRL,
    SM1_CLKDIV,
    SM1_PINCTRL,
    SM2_CLKDIV,
    SM2_PINCTRL,
    SM3_CLKDIV,
    SM3_PINCTRL,
    TXF0,
    TXF1,
    TXF2,
    TXF3,
)

# native StateMachine, cimported for direct C-level `machine.step()`/`.check_wait()`/`.enabled`
# access in the hot loops below - not the `peripherals.state_machine` facade: native `_pio` is only
# ever active when native `_state_machine` is too (setup.py builds both, the facade gates both on
# native_disabled() identically), so the concrete native class is always the right one here.
from rp2040py.native._state_machine cimport StateMachine
# GPIOPin, cimported for a direct C-level check_for_updates() call in check_changed_pins (mutual
# cimport with _gpio_pin.pyx, which cimports RPPIO - the paired .pxd files resolve the cycle).
from rp2040py.native._gpio_pin cimport GPIOPin


# Portable count-trailing-zeros (index of the lowest set bit), no Python boxing - replaces
# `(x & -x).bit_length() - 1`, which converted the C uint to a Python int every iteration of
# check_changed_pins' set-bit walk. GCC/Clang use __builtin_ctz; MSVC (the Windows wheels) has no
# such builtin, so a _BitScanForward shim keeps it portable. Caller guarantees x != 0.
cdef extern from *:
    """
    #if defined(_MSC_VER)
    #include <intrin.h>
    static unsigned int rp2040py_ctz(unsigned int x) { unsigned long i; _BitScanForward(&i, x); return (unsigned int)i; }
    #else
    static unsigned int rp2040py_ctz(unsigned int x) { return (unsigned int)__builtin_ctz(x); }
    #endif
    """
    unsigned int rp2040py_ctz(unsigned int x) nogil


cdef class RPPIO:
    def __cinit__(self, rp2040, str name, unsigned int first_irq, unsigned int index):
        self.rp2040 = rp2040
        self.name = name
        self.raw_write_value = 0
        self.first_irq = first_irq
        self.index = index

        self.instructions = [0] * 32
        self.dreq_rx = DREQ_RX1 if self.index else DREQ_RX0
        self.dreq_tx = DREQ_TX1 if self.index else DREQ_TX0
        self.machines = [StateMachine(self.rp2040, self, i) for i in range(4)]

        self._run_task = None

        self.stopped = True
        self.fdebug = 0
        self.tx_stall = 0
        self.rx_stall = 0
        self.input_sync_bypass = 0
        self.irq = 0
        self.pin_values = 0
        self.pin_directions = 0
        self.old_pin_values = 0
        self.old_pin_directions = 0

        self.irq0_int_enable = 0
        self.irq0_int_force = 0
        self.irq1_int_enable = 0
        self.irq1_int_force = 0

    # --- BasePeripheral surface (Peripheral Protocol) ---------------------------------------

    cdef _warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    def write_uint32_atomic(self, offset, value, atomic_type):
        self.raw_write_value = value
        new_value = (
            atomic_update(self.read_uint32(offset), atomic_type, value) if atomic_type != ATOMIC_NORMAL else value
        )
        self.write_uint32(offset, new_value)

    # --- properties -------------------------------------------------------------------------

    @property
    def int_raw(self):
        return (
            ((self.irq & 0xF) << 8)
            | (0x80 if not self.machines[3].tx_fifo.full else 0)
            | (0x40 if not self.machines[2].tx_fifo.full else 0)
            | (0x20 if not self.machines[1].tx_fifo.full else 0)
            | (0x10 if not self.machines[0].tx_fifo.full else 0)
            | (0x08 if not self.machines[3].rx_fifo.empty else 0)
            | (0x04 if not self.machines[2].rx_fifo.empty else 0)
            | (0x02 if not self.machines[1].rx_fifo.empty else 0)
            | (0x01 if not self.machines[0].rx_fifo.empty else 0)
        )

    @property
    def irq0_int_status(self):
        return (self.int_raw & self.irq0_int_enable) | self.irq0_int_force

    @property
    def irq1_int_status(self):
        return (self.int_raw & self.irq1_int_enable) | self.irq1_int_force

    # --- register dispatch ------------------------------------------------------------------

    def read_uint32(self, offset):
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            return self.machines[0].read_uint32(offset - SM0_CLKDIV)
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            return self.machines[1].read_uint32(offset - SM1_CLKDIV)
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            return self.machines[2].read_uint32(offset - SM2_CLKDIV)
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            return self.machines[3].read_uint32(offset - SM3_CLKDIV)

        if offset == CTRL:
            return (
                (1 << 0 if self.machines[0].enabled else 0)
                | (1 << 1 if self.machines[1].enabled else 0)
                | (1 << 2 if self.machines[2].enabled else 0)
                | (1 << 3 if self.machines[3].enabled else 0)
            )
        if offset == FSTAT:
            return (
                self.machines[0].fifo_stat
                | self.machines[1].fifo_stat
                | self.machines[2].fifo_stat
                | self.machines[3].fifo_stat
            )
        if offset == FDEBUG:
            return self.fdebug
        if offset == FLEVEL:
            return (
                (self.machines[0].tx_fifo.item_count & 0xF)
                | ((self.machines[0].rx_fifo.item_count & 0xF) << 4)
                | ((self.machines[1].tx_fifo.item_count & 0xF) << 8)
                | ((self.machines[1].rx_fifo.item_count & 0xF) << 12)
                | ((self.machines[2].tx_fifo.item_count & 0xF) << 16)
                | ((self.machines[2].rx_fifo.item_count & 0xF) << 20)
                | ((self.machines[3].tx_fifo.item_count & 0xF) << 24)
                | ((self.machines[3].rx_fifo.item_count & 0xF) << 28)
            )

        if offset == RXF0:
            return self.machines[0].read_fifo()
        if offset == RXF1:
            return self.machines[1].read_fifo()
        if offset == RXF2:
            return self.machines[2].read_fifo()
        if offset == RXF3:
            return self.machines[3].read_fifo()
        if offset == IRQ:
            return self.irq
        if offset == IRQ_FORCE:
            return 0
        if offset == INPUT_SYNC_BYPASS:
            return self.input_sync_bypass
        if offset == DBG_PADOUT:
            return self.pin_values
        if offset == DBG_PADOE:
            return self.pin_directions
        if offset == DBG_CFGINFO:
            return 0x200404
        if offset == INTR:
            return self.int_raw
        if offset == IRQ0_INTE:
            return self.irq0_int_enable
        if offset == IRQ0_INTF:
            return self.irq0_int_force
        if offset == IRQ0_INTS:
            return self.irq0_int_status
        if offset == IRQ1_INTE:
            return self.irq1_int_enable
        if offset == IRQ1_INTF:
            return self.irq1_int_force
        if offset == IRQ1_INTS:
            return self.irq1_int_status

        self._warn(f"Unimplemented peripheral read from 0x{offset:x}")
        if offset > 0x1000:
            self._warn("Unimplemented read from peripheral in the atomic operation region")
        return 0xFFFFFFFF

    def write_uint32(self, offset, value):
        if INSTR_MEM0 <= offset <= INSTR_MEM31:
            self.instructions[(offset - INSTR_MEM0) >> 2] = value & 0xFFFF
            return
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            self.machines[0].write_uint32(offset - SM0_CLKDIV, value)
            return
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            self.machines[1].write_uint32(offset - SM1_CLKDIV, value)
            return
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            self.machines[2].write_uint32(offset - SM2_CLKDIV, value)
            return
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            self.machines[3].write_uint32(offset - SM3_CLKDIV, value)
            return

        if offset == CTRL:
            for index in range(4):
                self.machines[index].enabled = bool(value & (1 << index))
                if value & (1 << (4 + index)):
                    self.machines[index].restart()
                if value & (1 << (8 + index)):
                    self.machines[index].clk_div_restart()
            should_run = value & 0xF
            if self.stopped and should_run:
                self.stopped = False
                # Only run the first batch synchronously inline when no Simulator owns this RP2040
                # (docs/records/0043-pio-dma-first-batch-race.md / 0037) - a real Simulator's own
                # _execute_batch() steps every non-stopped RPPIO once per CPU instruction instead,
                # so clock.tick() runs between every PIO step and a DMA-fed FIFO can't be outrun.
                if self.rp2040.simulator is None:
                    self._step_batch()
                if not self.stopped and self.rp2040.simulator is None:
                    self._run_task = asyncio.get_running_loop().create_task(self.run())
            if not should_run:
                self.stopped = True

        elif offset == FDEBUG:
            self.fdebug &= ~self.raw_write_value
            self.fdebug |= self.tx_stall | self.rx_stall

        elif offset == TXF0:
            self.machines[0].write_fifo(value)
        elif offset == TXF1:
            self.machines[1].write_fifo(value)
        elif offset == TXF2:
            self.machines[2].write_fifo(value)
        elif offset == TXF3:
            self.machines[3].write_fifo(value)

        elif offset == IRQ:
            self.irq &= ~self.raw_write_value
            self.irq_updated()

        elif offset == INPUT_SYNC_BYPASS:
            self.input_sync_bypass = value

        elif offset == IRQ_FORCE:
            self.irq |= value
            self.irq_updated()

        elif offset == IRQ0_INTE:
            self.irq0_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ0_INTF:
            self.irq0_int_force = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTE:
            self.irq1_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTF:
            self.irq1_int_force = value & 0xFFF
            self.check_interrupts()

        else:
            self._warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    # --- hot path ---------------------------------------------------------------------------

    def pin_values_changed(self, unsigned int value, unsigned int first_pin, unsigned int count):
        cdef unsigned int mask
        if count > 31:
            mask = 0xFFFFFFFF
        else:
            mask = (((<unsigned int>1) << count) - 1) << first_pin
        self.pin_values = ((self.pin_values & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF

    def pin_directions_changed(self, unsigned int value, unsigned int first_pin, unsigned int count):
        cdef unsigned int mask
        if count > 31:
            mask = 0xFFFFFFFF
        else:
            mask = (((<unsigned int>1) << count) - 1) << first_pin
        self.pin_directions = ((self.pin_directions & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF

    def check_interrupts(self):
        cdef unsigned int first_irq = self.first_irq
        self.rp2040.set_interrupt(first_irq, bool(self.irq0_int_status))
        self.rp2040.set_interrupt(first_irq + 1, bool(self.irq1_int_status))

    def irq_updated(self):
        cdef StateMachine machine
        for machine in self.machines:
            machine.check_wait()
        self.check_interrupts()

    cpdef check_changed_pins(self):
        cdef unsigned int changed_pins = (self.old_pin_directions ^ self.pin_directions) | (
            self.old_pin_values ^ self.pin_values
        )
        cdef unsigned int remaining
        cdef unsigned int gpio_index
        cdef unsigned int n
        if changed_pins:
            self.old_pin_directions = self.pin_directions
            self.old_pin_values = self.pin_values
            # Iterate only the set bits of changed_pins (typically ~1) instead of scanning all 30
            # GPIO every step - see the pure _pio.py comment / commit history for the measurement.
            gpio = self.rp2040.gpio
            n = len(gpio)
            remaining = changed_pins
            while remaining:
                gpio_index = rp2040py_ctz(remaining)
                if gpio_index < n:
                    (<GPIOPin>gpio[gpio_index]).check_for_updates()
                remaining &= remaining - 1

    cpdef step(self):
        cdef StateMachine machine
        for machine in self.machines:
            machine.step()
        self.check_changed_pins()

    def _step_batch(self):
        cdef int i = 0
        while i < 1000 and not self.stopped:
            self.step()
            i += 1

    async def run(self):
        while not self.stopped:
            await asyncio.sleep(0)
            self._step_batch()

    def stop(self):
        cdef StateMachine machine
        for machine in self.machines:
            machine.enabled = False
        self.stopped = True
