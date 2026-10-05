"""A lockstep differential oracle for the I2C (DW_apb_i2c): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the I2C design note). The method is the PIO's, the DMA's, the SSI's, the UART's and the SPI's (tests/utils/*_diff.py): one chip whose I2C0 is
the *pure-Python* reference (``peripherals/_i2c.py``, built explicitly because the facade would hand out the native one) and one whose I2C0 is whatever the facade gives (today the same
class, later the C++ one), fed one stream of operations:

* writes and reads of **every register offset**, through the bus and through its four aliases (normal, XOR, SET, CLR - an alias write decodes against a *read*, and most of the
  ``IC_CLR_*`` registers, ``IC_DATA_CMD`` and ``IC_TX_ABRT_SOURCE`` change state when *read*), unimplemented offsets included (so the warnings are compared);
* a **command stream** the way a driver issues it - ``IC_DATA_CMD`` entries with the CMD (read), STOP and RESTART bits, ``IC_ENABLE`` with ENABLE, ABORT and TX_CMD_BLOCK, the target
  address in 7 and 10 bit mode, thresholds, the speed field (including the invalid 0 that the block rewrites);
* the **device on the bus**, one mode per callback (start, connect, write byte, read byte, stop): the reference's own defaults (start and stop complete at once, a connect is NACKed),
  **auto** (the callback completes from inside itself - re-entrancy: the block is in the middle of ``_next_command`` when it is entered again), **deferred** (completes on a later
  operation, as a device with a clock alarm does) or **silent** (never) - and the completions themselves as operations, in any order and state, including ones nothing asked for
  and an arbitration loss;
* ``reset()``, ``check_interrupts()``, and the interrupt mask set directly as well as through ``IC_INTR_MASK`` (writable, 13 bits, reset value 0x8FF).

After **each** step: the registers that have no side effect when read, the whole private state (the bus state machine, the busy/stop/restart/first-byte flags, the abort source, the
interrupt registers, the thresholds, the clock counts, the spike length), both FIFOs' level *and contents*, the derived properties, the NVIC's pending bits, and the ordered log of
everything that left the block (IRQ line changes, each device callback with its arguments, warnings); an exception on one side and not the other is a difference like any other.

The rig *wraps* ``set_interrupt`` and the logger and attaches the device callbacks itself.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.peripherals import _i2c as I
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.rp2040 import RP2040

I2C0_BASE = 0x40044000
NVIC_ISPR = 0xE000E200
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

# registers whose read has no side effect: observed after every step
PLAIN_REGISTERS = (
    I.IC_CON, I.IC_TAR, I.IC_SAR, I.IC_SS_SCL_HCNT, I.IC_SS_SCL_LCNT, I.IC_FS_SCL_HCNT, I.IC_FS_SCL_LCNT, I.IC_INTR_STAT, I.IC_INTR_MASK, I.IC_RAW_INTR_STAT, I.IC_RX_TL,
    I.IC_TX_TL, I.IC_ENABLE, I.IC_STATUS, I.IC_TXFLR, I.IC_RXFLR, I.IC_SDA_HOLD, I.IC_ENABLE_STATUS, I.IC_FS_SPKLEN, I.IC_COMP_PARAM_1, I.IC_COMP_VERSION, I.IC_COMP_TYPE,
)  # fmt: skip
# registers whose read changes state: read only as operations
EFFECT_REGISTERS = (
    I.IC_DATA_CMD, I.IC_CLR_INTR, I.IC_CLR_RX_UNDER, I.IC_CLR_RX_OVER, I.IC_CLR_TX_OVER, I.IC_CLR_RD_REQ, I.IC_CLR_TX_ABRT, I.IC_CLR_RX_DONE, I.IC_CLR_ACTIVITY, I.IC_CLR_STOP_DET,
    I.IC_CLR_START_DET, I.IC_CLR_GEN_CALL, I.IC_TX_ABRT_SOURCE,
)  # fmt: skip
WRITABLE = (
    I.IC_CON, I.IC_TAR, I.IC_SAR, I.IC_DATA_CMD, I.IC_SS_SCL_HCNT, I.IC_SS_SCL_LCNT, I.IC_FS_SCL_HCNT, I.IC_FS_SCL_LCNT, I.IC_SDA_HOLD, I.IC_RX_TL, I.IC_TX_TL, I.IC_ENABLE,
    I.IC_FS_SPKLEN, I.IC_INTR_MASK,
)  # fmt: skip
UNIMPLEMENTED = (
    0x0C,
    0x24,
    0x28,
    0x84,
    I.IC_DMA_CR,
    I.IC_DMA_TDLR,
    I.IC_DMA_RDLR,
    I.IC_SDA_SETUP,
    I.IC_ACK_GENERAL_CALL,
    I.IC_CLR_RESTART_DET,
    0xAC,
    0xF0,
    0x100,
    I.IC_INTR_STAT,
)
CALLBACKS = ("start", "connect", "write", "read", "stop")
MODES = ("auto", "defer", "silent", "default")


class Rig:
    """One chip plus the log of everything that left its I2C0."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        self.counter = 0  # drives the auto devices' answers: identical on both rigs because they see identical streams
        if kind == "pure":
            self._replace_the_i2c(I.RPI2C)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_i2c(factory)
        self._tap()
        self.modes = dict.fromkeys(CALLBACKS, "default")

    def _replace_the_i2c(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(chip, "I2C0", IRQ.I2C0)
        chip.i2c[0] = new
        chip.peripherals[I2C0_BASE >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger, method, lambda name, message, _m=method: log.append(("log", _m, str(name), str(message)))
            )

    # --- the device on the bus -------------------------------------------------------------------------------------------

    def _answer(self) -> int:
        self.counter += 1
        return self.counter

    def attach_device(self, modes: "dict[str, str]") -> None:
        """One mode per callback. 'default' puts the reference's own handler back (a fresh lambda of the kind it installs, or the bound `complete_stop`)."""
        i2c, log = self.chip.i2c[0], self.log
        self.modes = dict(modes)

        def on_start(repeated: bool) -> None:
            log.append(("start", bool(repeated)))
            if modes["start"] == "auto":
                i2c.complete_start()

        def on_connect(address: int, mode: Any) -> None:
            log.append(("connect", int(address), int(mode)))
            if modes["connect"] == "auto":
                n = self._answer()
                i2c.complete_connect(n % 4 != 0, n % 2)

        def on_write_byte(value: int) -> None:
            log.append(("write", int(value)))
            if modes["write"] == "auto":
                i2c.complete_write(self._answer() % 5 != 0)

        def on_read_byte(ack: bool) -> None:
            log.append(("read", bool(ack)))
            if modes["read"] == "auto":
                i2c.complete_read(self._answer() & 0xFF)

        def on_stop() -> None:
            log.append(("stop",))
            if modes["stop"] == "auto":
                i2c.complete_stop()

        callbacks = {
            "start": on_start,
            "connect": on_connect,
            "write": on_write_byte,
            "read": on_read_byte,
            "stop": on_stop,
        }
        attrs = {
            "start": "on_start",
            "connect": "on_connect",
            "write": "on_write_byte",
            "read": "on_read_byte",
            "stop": "on_stop",
        }
        for name in CALLBACKS:
            if modes[name] != "default":
                setattr(i2c, attrs[name], callbacks[name])

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        i2c = chip.i2c[0]
        if kind == "write":
            chip.write_uint32(I2C0_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(I2C0_BASE + op[2] + op[1]))
        elif kind == "device":
            self.attach_device(op[1])
        elif kind == "complete_start":
            i2c.complete_start()
        elif kind == "complete_connect":
            i2c.complete_connect(op[1], op[2])
        elif kind == "complete_write":
            i2c.complete_write(op[1])
        elif kind == "complete_read":
            i2c.complete_read(op[1])
        elif kind == "complete_stop":
            i2c.complete_stop()
        elif kind == "arbitration_lost":
            i2c.arbitration_lost()
        elif (
            kind == "mask"
        ):  # the mask set behind the register's back (a value the register's 13 bits could not hold included)
            i2c.int_enable = op[1]
        elif kind == "check":
            i2c.check_interrupts()
        elif kind == "reset":
            i2c.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, i2c = self.chip, self.chip.i2c[0]
        rx, tx = i2c._rx_fifo, i2c._tx_fifo
        return {
            "regs": tuple(int(chip.read_uint32(I2C0_BASE + offset)) for offset in PLAIN_REGISTERS),
            "state": (
                int(i2c._state),
                bool(i2c._busy),
                bool(i2c._stop),
                bool(i2c._pending_restart),
                bool(i2c._first_byte),
                int(i2c.abort_source),
                int(i2c.int_raw),
                int(i2c.int_enable),
                int(i2c.enable),
                int(i2c.rx_threshold),
                int(i2c.tx_threshold),
                int(i2c.control),
                int(i2c.target_address),
                int(i2c.slave_address),
                int(i2c.ss_clock_high_period),
                int(i2c.ss_clock_low_period),
                int(i2c.fs_clock_high_period),
                int(i2c.fs_clock_low_period),
                int(i2c._spikelen),
                int(i2c.raw_write_value),
            ),
            "rx": (bool(rx.empty), bool(rx.full), int(rx.item_count), tuple(int(v) for v in rx.items)),
            "tx": (bool(tx.empty), bool(tx.full), int(tx.item_count), tuple(int(v) for v in tx.items)),
            "props": (
                int(i2c.int_status),
                int(i2c.speed),
                int(i2c.scl_low_period),
                int(i2c.scl_high_period),
                int(i2c.master_bits),
            ),
            "nvic": int(chip.read_uint32(NVIC_ISPR)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose I2C is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""

    class Mutant(I.RPI2C):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if name == "fifo_depth_32":
                self._rx_fifo = I.FIFO(32)
                self._tx_fifo = I.FIFO(32)
            if name == "intr_mask_reset_zero":
                self.int_enable = 0
            if name == "initial_target_wrong":
                self.target_address = 0x56

        # -- the command engine
        def _next_command(self) -> None:
            if name == "restart_ignored":
                enabled, blocked = self.enable & I.ENABLE, self.enable & I.TX_CMD_BLOCK
                if self._tx_fifo.empty or self._busy or blocked or not enabled:
                    return
                self._busy = True
                if self._state == I.I2CState.IDLE:
                    self._pending_restart = False
                    self._stop = False
                    self._state = I.I2CState.START
                    self.on_start(False)
                    return
                self._pending_restart = False
                cmd = self._tx_fifo.pull()
                self._stop = bool(cmd & I.STOP)
                if cmd & I.CMD:
                    self.on_read_byte(not self._stop)
                else:
                    self.on_write_byte(cmd & 0xFF)
                if self._tx_fifo.item_count <= self.tx_threshold:
                    self._set_interrupts(I.R_TX_EMPTY)
                return
            if name == "block_ignored":
                saved = self.enable
                self.enable &= ~I.TX_CMD_BLOCK
                try:
                    super()._next_command()
                finally:
                    self.enable = saved
                return
            if name == "tx_empty_lt":
                enabled, blocked = self.enable & I.ENABLE, self.enable & I.TX_CMD_BLOCK
                if self._tx_fifo.empty or self._busy or blocked or not enabled:
                    return
                self._busy = True
                restart = bool(self._tx_fifo.peek() & I.RESTART) and not self._pending_restart and not self._stop
                if self._state == I.I2CState.IDLE or restart:
                    self._pending_restart = restart
                    self._stop = False
                    self._state = I.I2CState.START
                    self.on_start(restart)
                    return
                self._pending_restart = False
                cmd = self._tx_fifo.pull()
                self._stop = bool(cmd & I.STOP)
                if cmd & I.CMD:
                    self.on_read_byte(not self._stop)
                else:
                    self.on_write_byte(cmd & 0xFF)
                if self._tx_fifo.item_count < self.tx_threshold:
                    self._set_interrupts(I.R_TX_EMPTY)
                return
            if name == "read_ack_inverted":
                enabled, blocked = self.enable & I.ENABLE, self.enable & I.TX_CMD_BLOCK
                if self._tx_fifo.empty or self._busy or blocked or not enabled:
                    return
                self._busy = True
                restart = bool(self._tx_fifo.peek() & I.RESTART) and not self._pending_restart and not self._stop
                if self._state == I.I2CState.IDLE or restart:
                    self._pending_restart = restart
                    self._stop = False
                    self._state = I.I2CState.START
                    self.on_start(restart)
                    return
                self._pending_restart = False
                cmd = self._tx_fifo.pull()
                self._stop = bool(cmd & I.STOP)
                if cmd & I.CMD:
                    self.on_read_byte(self._stop)
                else:
                    self.on_write_byte(cmd & 0xFF)
                if self._tx_fifo.item_count <= self.tx_threshold:
                    self._set_interrupts(I.R_TX_EMPTY)
                return
            super()._next_command()

        def _push_rx(self, value: int) -> None:
            if name == "rx_overflow_silent":
                if self._rx_fifo.full:
                    return
                self._rx_fifo.push(value)
                if self._rx_fifo.item_count > self.rx_threshold:
                    self._set_interrupts(I.R_RX_FULL)
                return
            if name == "rx_threshold_ge":
                if self._rx_fifo.full:
                    self._set_interrupts(I.R_RX_OVER)
                    return
                self._rx_fifo.push(value)
                if self._rx_fifo.item_count >= self.rx_threshold:
                    self._set_interrupts(I.R_RX_FULL)
                return
            super()._push_rx(value)

        def _abort(self, reason: int) -> None:
            if name == "abort_keeps_tx_fifo":
                self.abort_source &= ~I.TX_FLUSH_CNT_MASK
                self.abort_source |= reason | (self._tx_fifo.item_count << I.TX_FLUSH_CNT_SHIFT)
                self._set_interrupts(I.R_TX_ABRT)
                return
            if name == "abort_no_flush_count":
                self.abort_source &= ~I.TX_FLUSH_CNT_MASK
                self.abort_source |= reason
                self._tx_fifo.reset()
                self._set_interrupts(I.R_TX_ABRT)
                return
            super()._abort(reason)

        # -- the completions
        def complete_start(self) -> None:
            if name == "start_no_start_det":
                if self._tx_fifo.empty or self._state != I.I2CState.START or self._stop:
                    self.on_stop()
                    return
                mode = I.I2CMode.READ if self._tx_fifo.peek() & I.CMD else I.I2CMode.WRITE
                self._state = I.I2CState.CONNECT
                self.on_connect(self.target_address & (0x3FF if self.master_bits == 10 else 0xFF), mode)
                return
            if name == "start_mode_inverted":
                if self._tx_fifo.empty or self._state != I.I2CState.START or self._stop:
                    self.on_stop()
                    return
                mode = I.I2CMode.WRITE if self._tx_fifo.peek() & I.CMD else I.I2CMode.READ
                self._state = I.I2CState.CONNECT
                self._set_interrupts(I.R_START_DET)
                self.on_connect(self.target_address & (0x3FF if self.master_bits == 10 else 0xFF), mode)
                return
            if name == "address_mask_wrong":
                if self._tx_fifo.empty or self._state != I.I2CState.START or self._stop:
                    self.on_stop()
                    return
                mode = I.I2CMode.READ if self._tx_fifo.peek() & I.CMD else I.I2CMode.WRITE
                self._state = I.I2CState.CONNECT
                self._set_interrupts(I.R_START_DET)
                self.on_connect(self.target_address & 0xFF, mode)
                return
            super().complete_start()

        def complete_connect(self, ack: bool, nack_byte: int = 0) -> None:
            if name == "connect_nack_no_abort" and (not ack or self._stop):
                self._state = I.I2CState.STOP
                self.on_stop()
                return
            if name == "connect_first_byte_missing" and ack and not self._stop:
                self._state = I.I2CState.CONNECTED
                self._busy = False
                self._next_command()
                return
            if name == "connect_nack_always_7b" and not ack and not self._stop:
                self._abort(I.ABRT_7B_ADDR_NOACK)
                self._state = I.I2CState.STOP
                self.on_stop()
                return
            super().complete_connect(ack, nack_byte)

        def complete_write(self, ack: bool) -> None:
            if name == "write_nack_no_abort" and (not ack or self._stop):
                self._state = I.I2CState.STOP
                self.on_stop()
                return
            super().complete_write(ack)

        def complete_read(self, value: int) -> None:
            if name == "read_first_byte_flag_missing":
                self._push_rx(value)
                if self._stop:
                    self._state = I.I2CState.STOP
                    self.on_stop()
                    return
                self._first_byte = False
                self._busy = False
                self._next_command()
                return
            if name == "read_stop_ignored":
                self._push_rx(value | (I.FIRST_DATA_BYTE if self._first_byte else 0))
                self._first_byte = False
                self._busy = False
                self._next_command()
                return
            super().complete_read(value)

        def complete_stop(self) -> None:
            if name == "stop_no_next_command":
                self._state = I.I2CState.IDLE
                self._set_interrupts(I.R_STOP_DET)
                self._busy = False
                self._pending_restart = False
                if self.enable & I.ABORT:
                    self.enable &= ~I.ABORT
                return
            if name == "stop_keeps_abort_bit":
                self._state = I.I2CState.IDLE
                self._set_interrupts(I.R_STOP_DET)
                self._busy = False
                self._pending_restart = False
                self._next_command()
                return
            super().complete_stop()

        def arbitration_lost(self) -> None:
            if name == "arb_lost_keeps_state":
                self._abort(I.ARB_LOST)
                return
            super().arbitration_lost()

        # -- the registers
        def read_uint32(self, offset: int) -> int:
            if offset == I.IC_DATA_CMD and name == "data_rx_under_missing" and self._rx_fifo.empty:
                return 0
            if offset == I.IC_DATA_CMD and name == "data_read_keeps_rx_full":
                if self._rx_fifo.empty:
                    self._set_interrupts(I.R_RX_UNDER)
                    return 0
                return self._rx_fifo.pull()
            if offset == I.IC_STATUS and name == "status_no_activity":
                return super().read_uint32(offset) & ~(I.MST_ACTIVITY | I.ACTIVITY)
            if offset == I.IC_STATUS and name == "status_tfe_wrong":
                return super().read_uint32(offset) ^ I.TFE
            if offset == I.IC_TXFLR and name == "levels_swapped":
                return self._rx_fifo.item_count
            if offset == I.IC_COMP_VERSION and name == "comp_version_wrong":
                return 0x3230312B
            if offset == I.IC_TX_ABRT_SOURCE and name == "abrt_source_read_keeps":
                return self.abort_source
            if offset == I.IC_CLR_INTR and name == "clr_intr_keeps_abrt_source":
                return self._clear_interrupts(
                    I.R_RX_UNDER
                    | I.R_RX_OVER
                    | I.R_TX_OVER
                    | I.R_RD_REQ
                    | I.R_TX_ABRT
                    | I.R_RX_DONE
                    | I.R_ACTIVITY
                    | I.R_STOP_DET
                    | I.R_START_DET
                    | I.R_GEN_CALL
                )
            if offset == I.IC_CLR_RX_UNDER and name == "clr_rx_under_returns_zero":
                self._clear_interrupts(I.R_RX_UNDER)
                return 0
            if offset == I.IC_CLR_STOP_DET and name == "clr_stop_det_clears_start":
                return self._clear_interrupts(I.R_START_DET)
            if offset == I.IC_ENABLE_STATUS and name == "enable_status_wrong":
                return self.enable & 0x7
            if offset == I.IC_FS_SPKLEN and name == "spklen_unmasked":
                return self._spikelen
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if offset == I.IC_CON and name == "speed_fix_missing":
                self.control = value
                return
            if offset == I.IC_TAR and name == "tar_mask_wrong":
                self.target_address = value & 0x7F
                return
            if offset == I.IC_SAR and name == "sar_mask_wrong":
                self.slave_address = value & 0x7F
                return
            if offset == I.IC_DATA_CMD and name == "tx_overflow_silent":
                if not self._tx_fifo.full:
                    self._tx_fifo.push(value)
                    self._clear_interrupts(I.R_TX_EMPTY)
                    self._next_command()
                return
            if offset == I.IC_DATA_CMD and name == "tx_empty_not_cleared":
                if self._tx_fifo.full:
                    self._set_interrupts(I.R_TX_OVER)
                else:
                    self._tx_fifo.push(value)
                    self._next_command()
                return
            if offset == I.IC_RX_TL and name == "rx_tl_clamp_missing":
                self.rx_threshold = value & 0xFF
                return
            if offset == I.IC_TX_TL and name == "tx_tl_mask_wrong":
                self.tx_threshold = min(value & 0xF, self._tx_fifo.size)
                return
            if offset == I.IC_FS_SCL_HCNT and name == "hcnt_mask_wrong":
                self.fs_clock_high_period = value & 0xFF
                return
            if offset == I.IC_ENABLE and name == "enable_fifos_kept":
                value |= self.enable & I.ABORT
                if value & I.ABORT:
                    if self._state == I.I2CState.IDLE:
                        value &= ~I.ABORT
                    else:
                        self._abort(I.ABRT_USER_ABRT)
                        self._stop = True
                self.enable = value
                self._next_command()
                return
            if offset == I.IC_ENABLE and name == "enable_abort_when_idle":
                value |= self.enable & I.ABORT
                if value & I.ABORT:
                    self._abort(I.ABRT_USER_ABRT)
                    self._stop = True
                if not (value & I.ENABLE):
                    self._tx_fifo.reset()
                    self._rx_fifo.reset()
                self.enable = value
                self._next_command()
                return
            if offset == I.IC_ENABLE and name == "enable_abort_not_sticky":
                if value & I.ABORT:
                    if self._state == I.I2CState.IDLE:
                        value &= ~I.ABORT
                    else:
                        self._abort(I.ABRT_USER_ABRT)
                        self._stop = True
                if not (value & I.ENABLE):
                    self._tx_fifo.reset()
                    self._rx_fifo.reset()
                self.enable = value
                self._next_command()
                return
            if offset == I.IC_ENABLE and name == "enable_no_next_command":
                value |= self.enable & I.ABORT
                if value & I.ABORT:
                    if self._state == I.I2CState.IDLE:
                        value &= ~I.ABORT
                    else:
                        self._abort(I.ABRT_USER_ABRT)
                        self._stop = True
                if not (value & I.ENABLE):
                    self._tx_fifo.reset()
                    self._rx_fifo.reset()
                self.enable = value
                return
            if offset == I.IC_INTR_MASK and name == "intr_mask_not_writable":
                BasePeripheral.write_uint32(self, offset, value)
                return
            if offset == I.IC_INTR_MASK and name == "intr_mask_unmasked":
                self.int_enable = value
                self.check_interrupts()
                return
            if offset == I.IC_INTR_MASK and name == "intr_mask_no_line_update":
                self.int_enable = value & I.INTR_MASK_BITS
                return
            if offset == I.IC_FS_SPKLEN and name == "spklen_always":
                self._spikelen = value
                return
            super().write_uint32(offset, value)

        @property
        def master_bits(self) -> int:
            if name == "master_bits_wrong":
                return 10 if self.control & I.IC_10BITADDR_SLAVE else 7
            return super().master_bits

        @property
        def scl_low_period(self) -> int:
            if name == "scl_period_speed_wrong":
                return self.fs_clock_low_period if self.speed == I.I2CSpeed.STANDARD else self.ss_clock_low_period
            return super().scl_low_period

        def reset(self) -> None:
            callbacks = (self.on_start, self.on_connect, self.on_write_byte, self.on_read_byte, self.on_stop)
            if name == "reset_keeps_target":
                target = self.target_address
                super().reset()
                self.target_address = target
                self.on_start, self.on_connect, self.on_write_byte, self.on_read_byte, self.on_stop = callbacks
                return
            if name == "reset_keeps_fifos":
                tx, rx = list(self._tx_fifo.items), list(self._rx_fifo.items)
                super().reset()
                for item in tx:
                    self._tx_fifo.push(item)
                for item in rx:
                    self._rx_fifo.push(item)
                self.on_start, self.on_connect, self.on_write_byte, self.on_read_byte, self.on_stop = callbacks
                return
            super().reset()
            if name == "intr_mask_reset_zero":
                self.int_enable = 0
            if name == "reset_clears_callbacks":
                self.on_start = lambda repeated_start: self.complete_start()
                self.on_connect = lambda address, mode: self.complete_connect(False)
                self.on_write_byte = lambda value: self.complete_write(False)
                self.on_read_byte = lambda ack: self.complete_read(0xFF)
                self.on_stop = self.complete_stop
            else:
                self.on_start, self.on_connect, self.on_write_byte, self.on_read_byte, self.on_stop = callbacks

    return Rig("mutant", Mutant)


MUTANTS = (
    "fifo_depth_32", "initial_target_wrong", "restart_ignored", "block_ignored", "tx_empty_lt", "read_ack_inverted", "rx_overflow_silent", "rx_threshold_ge", "abort_keeps_tx_fifo",
    "abort_no_flush_count", "start_no_start_det", "start_mode_inverted", "address_mask_wrong", "connect_nack_no_abort", "connect_first_byte_missing", "connect_nack_always_7b",
    "write_nack_no_abort", "read_first_byte_flag_missing", "read_stop_ignored", "stop_no_next_command", "stop_keeps_abort_bit", "arb_lost_keeps_state", "data_rx_under_missing",
    "data_read_keeps_rx_full", "status_no_activity", "status_tfe_wrong", "levels_swapped", "comp_version_wrong", "abrt_source_read_keeps",
    "clr_intr_keeps_abrt_source", "clr_rx_under_returns_zero", "clr_stop_det_clears_start", "enable_status_wrong", "spklen_unmasked", "speed_fix_missing", "tar_mask_wrong",
    "sar_mask_wrong", "tx_overflow_silent", "tx_empty_not_cleared", "rx_tl_clamp_missing", "tx_tl_mask_wrong", "hcnt_mask_wrong", "enable_fifos_kept", "enable_abort_when_idle",
    "enable_abort_not_sticky", "enable_no_next_command", "spklen_always", "master_bits_wrong", "scl_period_speed_wrong", "reset_keeps_target", "reset_keeps_fifos",
    "reset_clears_callbacks", "intr_mask_not_writable", "intr_mask_unmasked", "intr_mask_no_line_update", "intr_mask_reset_zero",
)  # fmt: skip


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == I.IC_CON:
        return r.choice((0x65, 0x63, 0x61, 0x6B, 0x75, 0x7F, 0x1, 0x3, 0x5, r.getrandbits(11), r.getrandbits(32)))
    if offset in (I.IC_TAR, I.IC_SAR):
        return r.choice((0x50, 0x3C, 0x68, 0, 0x7F, 0x3FF, 0x2AA, r.getrandbits(12), r.getrandbits(32)))
    if offset == I.IC_DATA_CMD:
        flags = (
            (I.CMD if r.random() < 0.4 else 0)
            | (I.STOP if r.random() < 0.3 else 0)
            | (I.RESTART if r.random() < 0.25 else 0)
        )
        return r.getrandbits(8) | flags if roll < 0.92 else r.getrandbits(32)
    if offset == I.IC_ENABLE:
        return r.choice((0, 1, 1, 1, 3, 5, 7, 2, 4, r.getrandbits(32)))
    if offset in (I.IC_RX_TL, I.IC_TX_TL):
        return r.choice((0, 1, 4, 8, 15, 16, 17, 0xFF, 0x1FF, r.getrandbits(32)))
    if offset == I.IC_SDA_HOLD:
        return r.choice((0, 1, 2, 3, r.getrandbits(32)))
    if offset == I.IC_FS_SPKLEN:
        return r.choice((0, 1, 2, 4, 7, 0x100, r.getrandbits(32)))
    if offset == I.IC_INTR_MASK:
        return r.choice((0, 0x10, 0x4, 0x40, 0x200, 0x8FF, 0x1FFF, 0x2000, r.getrandbits(13), r.getrandbits(32)))
    return r.getrandbits(r.choice((8, 16, 32)))


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if (
            roll < 0.10 and ops
        ):  # attach a device only after a while: the reference's defaults get a prefix of their own
            ops.append(
                (
                    "device",
                    {name: r.choice(("auto", "auto", "auto", "defer", "silent", "default")) for name in CALLBACKS},
                )
            )
        elif roll < 0.22:  # a driver bringing the block up
            ops.append(("write", I.IC_ENABLE, 1, ALIASES[0]))
            ops.append(("write", I.IC_TAR, r.choice((0x50, 0x3C, 0x68, 0x3FF)), ALIASES[0]))
        elif roll < 0.50:
            offset = r.choice(WRITABLE + (I.IC_DATA_CMD, I.IC_DATA_CMD, I.IC_DATA_CMD, I.IC_ENABLE))
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.54:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.64:  # a burst of commands - overflows the 16-entry TX FIFO when nothing completes
            ops.extend(
                ("write", I.IC_DATA_CMD, _value(r, I.IC_DATA_CMD), ALIASES[0])
                for _ in range(r.choice((1, 2, 4, 17, 20)))
            )
        elif roll < 0.72:
            ops.extend(("read", I.IC_DATA_CMD, ALIASES[0]) for _ in range(r.choice((1, 1, 3, 17))))
        elif roll < 0.80:
            ops.append(
                (
                    "read",
                    r.choice(PLAIN_REGISTERS + EFFECT_REGISTERS + UNIMPLEMENTED),
                    r.choice(ALIASES) if r.random() < 0.12 else ALIASES[0],
                )
            )
        elif roll < 0.84:
            ops.append(("read", r.choice(EFFECT_REGISTERS), ALIASES[0]))
        elif roll < 0.93:
            which = r.choice(("complete_start", "complete_connect", "complete_write", "complete_read", "complete_stop"))
            args: tuple
            if which == "complete_connect":
                args = (r.random() < 0.6, r.choice((0, 1)))
            elif which == "complete_write":
                args = (r.random() < 0.7,)
            elif which == "complete_read":
                args = (r.getrandbits(r.choice((8, 8, 16))),)
            else:
                args = ()
            ops.append((which, *args))
        elif roll < 0.945:  # a burst of received bytes - overflows the 16-entry RX FIFO when nothing drains it
            ops.extend(("complete_read", r.getrandbits(8)) for _ in range(r.choice((17, 18, 20))))
        elif roll < 0.95:
            ops.append(("arbitration_lost",))
        elif roll < 0.96:
            ops.append(("mask", r.choice((0, 0x10, 0x4, 0x40, 0x200, 0x1FFF, r.getrandbits(13)))))
        elif roll < 0.97:
            ops.append(("reset",))
        else:
            ops.append(("check",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b[key]
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:60] for i in where[:6]]} vs {[str(value_b[i])[:60] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    i2c = rig.chip.i2c[0]
    for op in ops:
        before = len(rig.log)
        tx_full, rx_full, rx_empty = i2c._tx_fifo.full, i2c._rx_fifo.full, i2c._rx_fifo.empty
        if op[0] == "write" and op[1] == I.IC_DATA_CMD and op[3] == 0 and tx_full:
            count("tx.overflow")
        if op[0] == "read" and op[1] == I.IC_DATA_CMD and op[2] == 0:
            count("rx.under" if rx_empty else "rx.data")
        if op[0] == "device":
            for name, mode in op[1].items():
                count(f"device.{name}.{mode}")
        if op[0].startswith("complete_") or op[0] == "arbitration_lost":
            count(f"{op[0]}.state{int(i2c._state)}")
        was_state = int(i2c._state)
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        if rx_full and any(e == ("irq", int(IRQ.I2C0), 1) for e in rig.log[before:]):
            count("rx.over.line")
        for event in rig.log[before:]:
            count(f"log.{event[0]}" + (f".{event[2]}" if event[0] == "irq" else ""))
        if int(i2c._state) != was_state:
            count(f"transition.{was_state}->{int(i2c._state)}")
        if i2c.abort_source:
            count("abort.nonzero")
        if op[0] == "reset":
            count("reset.busy" if was_state else "reset")
    return counts
