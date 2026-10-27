# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 I2C (DW_apb_i2c, master side): a Python-facing shell over the C++ `I2cBlock` of `core/i2c.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_i2c.py` is the pure-Python reference, kept as the oracle (tests/test_i2c_diff.py); `peripherals/i2c.py` is the facade that picks between them, as for the other
native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40044] = i2c`) it registers the block's own C++ read/write functions in the bus's
window table (see `_native_window`), so a register access never enters Python. What stays Python is the edge of the block, reached through trampolines whose failures are parked
in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line (`rp2040.set_interrupt`), the logger and the five callbacks of the device on the bus -
`on_start`, `on_connect`, `on_write_byte`, `on_read_byte`, `on_stop`.

Each `on_*` is a plain attribute of the shell that hands back the very object that was set (`tests/test_chip_reset.py` compares with `is`). With none set the reference's default
applies - start and stop complete at once, **every connect is NACKed**, a write is NACKed, a read returns 0xFF - and the trampoline completes it in C++ without calling Python.
A device that answers later calls `complete_*()` from a clock alarm, and one that answers at once calls it from inside the callback: the block is re-entered from its own host
callback, as the reference is, down the whole queue of commands in one call stack.

The API is the reference's: the registers' attributes (readable and writable), `_rx_fifo`/`_tx_fifo` (views with the reference FIFO's attribute names), `_state` (an `I2CState`),
`speed`, `scl_low_period`, `scl_high_period`, `master_bits`, `int_status`, `complete_*`, `arbitration_lost`, `check_interrupts`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._mcu_irq cimport Mcu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._i2c import I2CMode, I2CSpeed, I2CState

__all__ = ("RPI2C",)

cdef uint32_t SPEED_SHIFT = 1
cdef uint32_t SPEED_MASK = 0x3
cdef uint32_t SPEED_STANDARD = 1


cdef class RPI2C(NativeBlock):
    def __init__(self, rp2040, name, irq, mcu_index=-1):
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self._on_start = None
        self._on_connect = None
        self._on_write_byte = None
        self._on_read_byte = None
        self._on_stop = None
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus, its line and request on the chip - and this object is the Python view of it
            self._block = <I2cBlock*> <size_t> rp2040._native_i2c_address(mcu_index)
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPI2C is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _world_start(self, repeated):
        """The device on the bus is asked to produce a (repeated) START; with none attached, the reference's default device answers at once."""
        callback = self._on_start
        if callback is None:
            return bool(self._block.complete_start())
        callback(bool(repeated))
        return True

    def _world_connect(self, address, mode):
        callback = self._on_connect
        if callback is None:
            return bool(self._block.complete_connect(False, 0))  # the default device NACKs every address
        callback(address, I2CMode(mode))
        return True

    def _world_write(self, value):
        callback = self._on_write_byte
        if callback is None:
            return bool(self._block.complete_write(False))
        callback(value)
        return True

    def _world_read(self, ack):
        callback = self._on_read_byte
        if callback is None:
            return bool(self._block.complete_read(0xFF))
        callback(bool(ack))
        return True

    def _world_stop(self):
        callback = self._on_stop
        if callback is None:
            return bool(self._block.complete_stop())
        callback()
        return True

    # --- the device on the bus -------------------------------------------------------------------

    @property
    def on_start(self):
        if self._on_start is None:
            return lambda repeated_start: self.complete_start()  # the reference's default
        return self._on_start

    @on_start.setter
    def on_start(self, callback):
        self._on_start = callback

    @property
    def on_connect(self):
        if self._on_connect is None:
            return lambda address, mode: self.complete_connect(False)
        return self._on_connect

    @on_connect.setter
    def on_connect(self, callback):
        self._on_connect = callback

    @property
    def on_write_byte(self):
        if self._on_write_byte is None:
            return lambda value: self.complete_write(False)
        return self._on_write_byte

    @on_write_byte.setter
    def on_write_byte(self, callback):
        self._on_write_byte = callback

    @property
    def on_read_byte(self):
        if self._on_read_byte is None:
            return lambda ack: self.complete_read(0xFF)
        return self._on_read_byte

    @on_read_byte.setter
    def on_read_byte(self, callback):
        self._on_read_byte = callback

    @property
    def on_stop(self):
        if self._on_stop is None:
            return self.complete_stop
        return self._on_stop

    @on_stop.setter
    def on_stop(self, callback):
        self._on_stop = callback

    def complete_start(self):
        if not self._block.complete_start():
            raise_if_pending()

    def complete_connect(self, ack, nack_byte=0):
        if not self._block.complete_connect(bool(ack), <uint32_t> (<int64_t> nack_byte)):
            raise_if_pending()

    def complete_write(self, ack):
        if not self._block.complete_write(bool(ack)):
            raise_if_pending()

    def complete_read(self, value):
        if not self._block.complete_read(<uint32_t> (<int64_t> value)):
            raise_if_pending()

    def complete_stop(self):
        if not self._block.complete_stop():
            raise_if_pending()

    def arbitration_lost(self):
        if not self._block.arbitration_lost():
            raise_if_pending()

    # --- the state, in one call ------------------------------------------------------------------

    def snapshot(self):
        """The block's state in one call: the registers, the bus state machine, both FIFOs' contents and what is derived from them."""
        cdef I2cBlock* b = self._block
        cdef uint32_t i
        return {
            "state": I2CState(b.state),
            "busy": bool(b.busy),
            "stop": bool(b.stop),
            "pending_restart": bool(b.pending_restart),
            "abort_pending": bool(b.abort_pending),
            "first_byte": bool(b.first_byte),
            "abort_source": b.abort_source,
            "int_raw": b.int_raw,
            "int_enable": b.int_enable,
            "enable": b.enable,
            "rx_threshold": b.rx_threshold,
            "tx_threshold": b.tx_threshold,
            "control": b.control,
            "target_address": b.target_address,
            "slave_address": b.slave_address,
            "ss_clock_high_period": b.ss_clock_high,
            "ss_clock_low_period": b.ss_clock_low,
            "fs_clock_high_period": b.fs_clock_high,
            "fs_clock_low_period": b.fs_clock_low,
            "spikelen": b.spikelen,
            "sda_hold": b.sda_hold,
            "sda_setup": b.sda_setup,
            "ack_general_call": b.ack_general_call,
            "slv_data_nack_only": b.slv_data_nack_only,
            "dma_control": b.dma_control,
            "dma_tdlr": b.dma_tdlr,
            "dma_rdlr": b.dma_rdlr,
            "rx": tuple([b.rx.at(i) for i in range(b.rx.count())]),
            "tx": tuple([b.tx.at(i) for i in range(b.tx.count())]),
            "int_status": b.int_status(),
            "speed": int(self.speed),
            "scl_low_period": self.scl_low_period,
            "scl_high_period": self.scl_high_period,
            "master_bits": b.master_bits(),
        }

    # --- what a device on the bus reads of the configuration --------------------------------------

    @property
    def speed(self):
        return I2CSpeed((self._block.control >> SPEED_SHIFT) & SPEED_MASK)

    @property
    def scl_low_period(self):
        return self._block.ss_clock_low if self.speed == SPEED_STANDARD else self._block.fs_clock_low

    @property
    def scl_high_period(self):
        return self._block.ss_clock_high if self.speed == SPEED_STANDARD else self._block.fs_clock_high

    @property
    def master_bits(self):
        return self._block.master_bits()

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def reset(self):
        """Registers, both FIFOs and the bus state machine, back to power-on (0089 Phase 5). The five `on_*` callbacks are wiring - whatever is on the bus - and survive."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
