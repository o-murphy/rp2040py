"""A lockstep differential oracle for the USB controller: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/tasks/usb-port.md. The reference side is the *pure-Python chip* (``rp2040py._rp2040.RP2040``) and its controller (``peripherals/_usb.py``); the candidate is whatever
``rp2040py.rp2040.RP2040`` gives (the native chip and the C++ block when the extensions are built, the same pure chip otherwise). Both are fed one stream of operations:

* **every register** of the block (and offsets between and past them) through the bus and its XOR/SET/CLR aliases - the masks, the write-1-to-clear ones, the self-clearing ones;
* the **DPRAM** at every width (a 16- or 8-bit write reaches the controller through the bus's read-modify-write, as the firmware's does): endpoint control words, buffer control words built the
  way TinyUSB builds them (single and double buffered, IN and OUT, pid / last / STALL / reset-select / isochronous offset), the setup area, data bytes;
* the **host end of the wire as ``usb/cdc.py`` plays it in device mode**: setup packets, the data an OUT buffer asked for (with any delay), and what the device sends is logged with the time it arrived;
* **host mode**: a scripted ``USBDevice`` attached and detached, SOF generation, setup / IN / OUT transactions of the endpoint-X registers and the polled interrupt endpoints;
* **simulated time** (``clock.tick()``, which runs the transfer, SOF and reset alarms), ``reset_device()`` and the block's ``reset()``.

After **each** step: every register that a read leaves alone, the interrupt state (raw, held, forced), the status machinery (the per-endpoint completed-buffer lists, the buffer select, the
two-buffer counters, the OUT requests in flight), all 4 KiB of the DPRAM, and the ordered, timestamped log of what the model told the outside (callbacks, the interrupt line, warnings);
an exception on one side and not the other is a difference like any other.

Logic mutants of the reference are made by changing its *source text* (``MUTATIONS``) and loading the result as a module of its own, so a mutant is exactly the reference with one expression
different.
"""

import gc
import inspect
import random
import types
from collections.abc import Callable
from typing import Any

import rp2040py._cortex_m0_core as pure_core
from rp2040py import _rp2040
from rp2040py.peripherals import _usb as U
from rp2040py.rp2040 import RP2040
from rp2040py.usb.usb_device import USBTransferResult
from utils.is32bit import IS32BIT

USB_BASE = 0x50110000
DPRAM_BASE = 0x50100000
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTER_OFFSETS = tuple(range(0x00, 0x40, 4)) + (
    U.MAIN_CTRL, U.SOF_WR, U.SOF_RD, U.SIE_CTRL, U.SIE_STATUS, U.INT_EP_CTRL, U.BUFF_STATUS, U.BUFF_CPU_SHOULD_HANDLE, U.EP_ABORT, U.EP_ABORT_DONE, U.EP_STALL_ARM, U.NAK_POLL,
    U.EP_STATUS_STALL_NAK, U.USB_MUXING, U.USB_PWR, U.USBPHY_DIRECT, U.USBPHY_DIRECT_OVERRIDE, U.USBPHY_TRIM, U.INTR, U.INTE, U.INTF, U.INTS,
)  # fmt: skip
SIDE_EFFECT_FREE = tuple(offset for offset in REGISTER_OFFSETS if offset != U.SOF_RD)
PAST_THE_BLOCK = (0x88, 0x9C, 0xA0, 0x100, 0x800, 0xFFC, 0x1)
TICKS = (0, 1, 500, 5_000, 9_999, 10_000, 10_001, 25_000, 100_000, 999_999, 1_000_000, 1_000_001, 3_000_000, 12_000_000)
BUFFER_BASES = (
    0x180,
    0x1C0,
    0x200,
    0x240,
    0x300,
    0x400,
    0x600,
    0xF00,
)  # 64-byte aligned buffers in the data area (0xF00 leaves 256 bytes)
ENDPOINTS = (0, 1, 2, 3, 15)


class FakeDevice:
    """A scripted ``USBDevice`` for host mode: its answers depend only on the number of requests it has had, so both rigs see the same device."""

    def __init__(self, log: list) -> None:
        self.address = 0
        self._log = log
        self._in = 0
        self._out = 0

    def handle_setup_packet(self, setup: bytes) -> USBTransferResult:
        self._log.append(("dev.setup", bytes(setup)))
        if setup[0] & 0x80:
            length = min(setup[6] | (setup[7] << 8), 20)
            return USBTransferResult(status="ack", data=bytes((setup[1] + i) & 0xFF for i in range(length)))
        return USBTransferResult(status="ack")

    def handle_data_in(self, endpoint_address: int) -> USBTransferResult:
        n = self._in
        self._in += 1
        self._log.append(("dev.in", endpoint_address, n))
        if n % 5 == 3:
            return USBTransferResult(status="nak")
        if n % 11 == 10:
            return USBTransferResult(status="stall")
        return USBTransferResult(status="ack", data=bytes((n * 3 + i) & 0xFF for i in range(1 + (n * 5) % 9)))

    def handle_data_out(self, endpoint_address: int, data: bytes) -> USBTransferResult:
        n = self._out
        self._out += 1
        self._log.append(("dev.out", endpoint_address, bytes(data)))
        if n % 6 == 5:
            return USBTransferResult(status="nak")
        if n % 13 == 12:
            return USBTransferResult(status="stall")
        return USBTransferResult(status="ack")

    def on_reset(self) -> None:
        self._log.append(("dev.reset",))

    def on_address_assigned(self, address: int) -> None:
        self._log.append(("dev.address", address))


def _pure_chip() -> Any:
    """The pure-Python chip. It builds its CPU through the facade, which hands back the *native* core whenever the extension is installed - and a native core only accepts a native chip - so it is given the
    pure core, as the RP2040PY_SKIP_CYTHON=1 build would."""
    saved, _rp2040.CortexM0Core = _rp2040.CortexM0Core, pure_core.CortexM0Core
    try:
        return _rp2040.RP2040()
    finally:
        _rp2040.CortexM0Core = saved


class Rig:
    """One chip plus the timestamped log of everything the USB model reported."""

    def __init__(self, kind: str, module: "types.ModuleType | None" = None) -> None:
        self.kind = kind
        self.chip = _pure_chip() if kind in ("pure", "mutant") else RP2040()
        if kind == "mutant":
            assert module is not None
            new = module.RPUSBController(self.chip, "USB")
            self.chip.usb_ctrl = new
            self.chip.peripherals[USB_BASE >> 12] = new
        self.log: list[tuple] = []
        self._tap()

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        usb = chip.usb_ctrl

        def stamp() -> int:
            return int(chip.clock.ticks)

        chip.logger.debug = lambda name, message: None  # type: ignore[method-assign]  # the controller narrates every transfer; the state compared below says the same
        for method in ("warning", "error", "info"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", stamp(), _m, str(name), str(message))),
            )
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", stamp(), int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        usb.on_usb_enabled = lambda: log.append(("enabled", stamp()))
        usb.on_reset_received = lambda: log.append(("reset_received", stamp()))
        usb.on_reset = lambda: log.append(("chip_reset", stamp()))
        usb.on_endpoint_write = lambda endpoint, buffer: log.append(("write", stamp(), int(endpoint), bytes(buffer)))
        usb.on_endpoint_read = lambda endpoint, size: log.append(("read", stamp(), int(endpoint), int(size)))

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip, usb = self.chip, self.chip.usb_ctrl
        if kind == "reg_read":
            return int(chip.read_uint32(USB_BASE + op[2] + op[1]))
        if kind == "reg_write":
            chip.write_uint32(USB_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "dpram_write":
            _, offset, value, width = op
            (chip.write_uint32, chip.write_uint16, chip.write_uint8)[(4, 2, 1).index(width)](DPRAM_BASE + offset, value)
        elif kind == "dpram_read":
            return int(chip.read_uint32(DPRAM_BASE + op[1]))
        elif kind == "dpram_bytes":
            _, offset, data = op
            chip.usb_dpram[offset : offset + len(data)] = data  # firmware filling a data buffer: plain memory, no hook
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "setup":
            usb.send_setup_packet(op[1])
        elif kind == "host_data":
            usb.endpoint_read_done(op[1], op[2], op[3])
        elif (
            kind == "poke_status"
        ):  # a SIE_STATUS bit that no modelled event sets (suspend, resume, errors): the level a future token-level host would raise
            usb.raise_sie_status(op[1])
        elif kind == "poke_stall_nak":  # EP_STATUS_STALL_NAK bits, which only a handshake sets
            usb.raise_stall_nak(op[1])
        elif kind == "reset_device":
            usb.reset_device()
        elif kind == "reset":
            usb.reset()
        elif kind == "connect":
            usb.connect_device(FakeDevice(self.log))
        elif kind == "disconnect":
            usb.disconnect_device()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, usb = self.chip, self.chip.usb_ctrl
        out: dict[str, Any] = {
            "regs": tuple(int(chip.read_uint32(USB_BASE + offset)) for offset in SIDE_EFFECT_FREE),
            "state": tuple(sorted(usb.snapshot().items())),
            "dpram": bytes(chip.usb_dpram),
            "clock": (int(chip.clock.ticks), bool(chip.clock.has_scheduled_alarm)),
        }
        return out


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _usb.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    # -- registers: masks, resets, layouts
    "sof_rd_keeps_ints": (
        "                self._int_raw &= ~(INTR_DEV_SOF | INTR_HOST_SOF)\n",
        "                pass\n",
        1,
    ),
    "cpu_should_handle_zero": ("                if done and done[0]:\n", "                if False:\n", 1),
    "addr_endp_unmasked": (
        "self._addr_endp = value & ADDR_ENDP_MASK\n        elif offset == MAIN_CTRL",
        "self._addr_endp = value\n        elif offset == MAIN_CTRL",
        1,
    ),
    "addr_endpn_unmasked": (
        "self._int_ep_addr_ctrl[ep_index] = value & ADDR_ENDPN_MASK",
        "self._int_ep_addr_ctrl[ep_index] = value",
        1,
    ),
    "sof_wr_unmasked": ("self._sof_frame_number = value & SOF_COUNT_MASK", "self._sof_frame_number = value", 1),
    "sie_ctrl_unmasked": (
        "self._handle_sie_ctrl_write(value & SIE_CTRL_MASK)",
        "self._handle_sie_ctrl_write(value)",
        1,
    ),
    "int_ep_ctrl_unmasked": ("self._int_ep_ctrl = value & INT_EP_CTRL_MASK", "self._int_ep_ctrl = value", 1),
    "ep_stall_arm_unmasked": ("self._ep_stall_arm = value & EP_STALL_ARM_MASK", "self._ep_stall_arm = value", 1),
    "nak_poll_unmasked": ("self._nak_poll = value & NAK_POLL_MASK", "self._nak_poll = value", 1),
    "nak_poll_resets_to_zero": ("NAK_POLL_RESET = 0x00100010", "NAK_POLL_RESET = 0", 1),
    "muxing_not_stored": ("self._usb_muxing = value & USB_MUXING_MASK", "self._usb_muxing = 0", 1),
    "pwr_unmasked": ("self._usb_pwr = value & PWR_MASK", "self._usb_pwr = value", 1),
    "pwr_override_enable_ignored": (
        "if self._usb_pwr & PWR_VBUS_DETECT_OVERRIDE_EN and self._usb_pwr & PWR_VBUS_DETECT:",
        "if self._usb_pwr & PWR_VBUS_DETECT:",
        1,
    ),
    "overcurrent_enable_ignored": (
        "if self._usb_pwr & PWR_OVERCURR_DETECT_EN and self._usb_pwr & PWR_OVERCURR_DETECT:",
        "if self._usb_pwr & PWR_OVERCURR_DETECT:",
        1,
    ),
    "phy_direct_unmasked": ("self._usbphy_direct = value & USBPHY_DIRECT_MASK", "self._usbphy_direct = value", 1),
    "phy_override_unmasked": (
        "self._usbphy_direct_override = value & USBPHY_DIRECT_OVERRIDE_MASK",
        "self._usbphy_direct_override = value",
        1,
    ),
    "phy_trim_unmasked": ("self._usbphy_trim = value & USBPHY_TRIM_MASK", "self._usbphy_trim = value", 1),
    "phy_trim_resets_to_zero": ("USBPHY_TRIM_RESET = 0x00001F1F", "USBPHY_TRIM_RESET = 0", 1),
    "inte_unmasked": ("self._int_enable = value & INT_MASK", "self._int_enable = value", 1),
    "intf_unmasked": ("self._int_force = value & INT_MASK", "self._int_force = value", 1),
    "force_masked_by_enable": (
        "return (self._int_raw & self._int_enable) | self._int_force",
        "return (self._int_raw | self._int_force) & self._int_enable",
        1,
    ),
    "main_ctrl_unmasked": (
        "self._main_ctrl = value & (SIM_TIMING | CONTROLLER_EN | HOST_NDEVICE)",
        "self._main_ctrl = value",
        1,
    ),
    # -- SIE_STATUS and the interrupt flags
    "suspended_not_write_clear": ("    | SIE_SUSPENDED\n)", ")", 1),
    "speed_cleared_by_write": (
        "                self._host_connect_event = False\n            if self.raw_write_value & SIE_BUS_RESET:",
        "                self._host_connect_event = False\n                self._sie_status &= ~SIE_SPEED_MASK\n            if self.raw_write_value & SIE_BUS_RESET:",
        1,
    ),
    "connect_event_not_cleared": (
        "                self._connect_event = False\n            if self.raw_write_value & SIE_SPEED_MASK:",
        "                pass\n            if self.raw_write_value & SIE_SPEED_MASK:",
        1,
    ),
    "host_event_not_acknowledged": (
        "                self._host_connect_event = False\n            if self.raw_write_value & SIE_BUS_RESET:",
        "                pass\n            if self.raw_write_value & SIE_BUS_RESET:",
        1,
    ),
    "host_attach_raises_no_event": (
        "        self._host_connect_event = True\n        self._sie_status_updated()\n        self.debug",
        "        self._sie_status_updated()\n        self.debug",
        1,
    ),
    "trans_complete_not_an_interrupt": ("            (SIE_TRANS_COMPLETE, INTR_TRANS_COMPLETE),\n", "", 1),
    "resume_is_always_the_devices": (
        "INTR_HOST_RESUME if self._host_mode else INTR_DEV_RESUME_FROM_HOST",
        "INTR_DEV_RESUME_FROM_HOST",
        1,
    ),
    "abort_done_raises_nothing": (
        "        if self._ep_abort_done:\n            self._int_raw |= INTR_ABORT_DONE",
        "        if False:\n            self._int_raw |= INTR_ABORT_DONE",
        1,
    ),
    "stall_nak_raises_nothing": (
        "        if self._ep_status_stall_nak:\n            self._int_raw |= INTR_EP_STALL_NAK",
        "        if False:\n            self._int_raw |= INTR_EP_STALL_NAK",
        1,
    ),
    "vbus_detect_not_an_interrupt": ("            (SIE_VBUS_DETECTED, INTR_VBUS_DETECT),\n", "", 1),
    "setup_does_not_clear_the_arm": ("        self._ep_stall_arm = 0  # a SETUP", "        pass  # a SETUP", 1),
    "enabled_callback_dropped": (
        "                elif self.on_usb_enabled:\n                    self.on_usb_enabled()",
        "                elif self.on_usb_enabled:\n                    pass",
        1,
    ),
    # -- device mode: the buffers
    "in_ignores_full": ("            if bool(state & USB_BUF_CTRL_FULL) == out:\n                return\n", "", 1),
    "stall_ignored": (
        "if not state & USB_BUF_CTRL_AVAILABLE or state & USB_BUF_CTRL_STALL:",
        "if not state & USB_BUF_CTRL_AVAILABLE:",
        1,
    ),
    "writeback_keeps_the_rest": (
        "            | (state & (USB_BUF_CTRL_DATA1_PID | USB_BUF_CTRL_LAST))\n",
        "            | (state & ~USB_BUF_CTRL_AVAILABLE & ~USB_BUF_CTRL_FULL)\n",
        1,
    ),
    "writeback_drops_the_pid": (
        "(state & (USB_BUF_CTRL_DATA1_PID | USB_BUF_CTRL_LAST))",
        "(state & USB_BUF_CTRL_LAST)",
        1,
    ),
    "writeback_drops_last": (
        "(state & (USB_BUF_CTRL_DATA1_PID | USB_BUF_CTRL_LAST))",
        "(state & USB_BUF_CTRL_DATA1_PID)",
        1,
    ),
    "out_not_marked_full": ("            | (USB_BUF_CTRL_FULL if out else 0)\n", "", 1),
    "last_buffer_no_trans_complete": (
        "        if state & USB_BUF_CTRL_LAST:\n            self._sie_status |= SIE_TRANS_COMPLETE",
        "        if False:\n            self._sie_status |= SIE_TRANS_COMPLETE",
        1,
    ),
    "in_no_ack_rec": (
        "        if not out:\n            self._sie_status |= SIE_ACK_REC",
        "        if False:\n            self._sie_status |= SIE_ACK_REC",
        1,
    ),
    "interrupt_enable_ignored": (
        "        if per_buffer:\n            self._push_done(slot, half)",
        "        if True:\n            self._push_done(slot, half)",
        1,
    ),
    "per_two_counts_to_one": ("if self._two_buffer_count[slot] == 2:", "if self._two_buffer_count[slot] == 1:", 1),
    "per_two_on_single_buffer": ("elif per_two and self._is_double_buffered(endpoint, out):", "elif per_two:", 1),
    "select_not_flipped": (
        "            if double:\n                self._buf_select[slot] ^= 1",
        "            if False:\n                self._buf_select[slot] ^= 1",
        1,
    ),
    "reset_select_ignored": ("                self._buf_select[endpoint * 2 + out] = 0", "                pass", 1),
    "second_buffer_not_chased": (
        "        # the other buffer of a double-buffered endpoint may already be waiting\n        self._service_endpoint(endpoint, out)",
        "        # (dropped)",
        1,
    ),
    "iso_offset_is_64": (
        "return base + (128 << ((buffer_control >> USB_BUF_CTRL_ISO_OFFSET_SHIFT) & 0x3))",
        "return base + USB_BUF1_OFFSET",
        1,
    ),
    "half_one_has_stall": (
        "        if half:\n            state &= ~(USB_BUF_CTRL_STALL | USB_BUF_CTRL_RESET_SELECT)",
        "        if False:\n            state &= ~(USB_BUF_CTRL_STALL | USB_BUF_CTRL_RESET_SELECT)",
        1,
    ),
    "out_always_buffer_zero": ("half = pending.pop(0) if pending else 0", "half = 0", 1),
    "read_alarm_serves_one": (
        "            for buffer in buffers:  # every answer that arrived before the alarm ran\n",
        "            for buffer in buffers[:1]:\n",
        1,
    ),
    "write_delay_11": (
        "self.write_delay_microseconds = 10  # Determined empirically",
        "self.write_delay_microseconds = 11",
        1,
    ),
    "reaction_delay_11": (
        "self.host_reaction_microseconds = 10\n",
        "self.host_reaction_microseconds = 11\n",
        1,
    ),
    "host_reacts_at_the_ack": (
        "schedule(buffer, self.host_reaction_microseconds * 1000)",
        "schedule(buffer, 0)",
        1,
    ),
    "reaction_serves_one": (
        "            for buffer in buffers:  # the host sees every buffer that was acknowledged before the alarm ran\n",
        "            for buffer in buffers[:1]:\n",
        1,
    ),
    "read_delay_11": ("self.read_delay_microseconds = 10\n", "self.read_delay_microseconds = 11\n", 1),
    "host_mode_ignores_dpram": (
        "        if self._host_mode:\n            return\n        if EP0_IN_BUFFER_CONTROL <= offset",
        "        if EP0_IN_BUFFER_CONTROL <= offset",
        1,
    ),
    "endpoint_zero_double_ignored": (
        "            return bool(self._sie_ctrl & SIE_CTRL_EP0_DOUBLE_BUF)",
        "            return False",
        1,
    ),
    "buffer_status_pops_nothing": (
        "                    self._buff_done[index].pop(0)\n",
        "                    pass\n",
        1,
    ),
    # -- host mode
    "sof_every_999999": ("self.sof_alarm.schedule(1_000_000)", "self.sof_alarm.schedule(999_999)", 1),
    "host_delay_6": ("self.host_transaction_delay_microseconds = 5", "self.host_transaction_delay_microseconds = 6", 1),
    "int_endpoint_ignores_interval": (
        "            if self._sof_frame_number % interval != 0:\n                continue\n",
        "",
        1,
    ),
    "data_pid_not_toggled": ("            self._control_data_pid ^= 1\n", "            pass\n", 1),
    "setup_not_acked": (
        "        self._sie_status |= SIE_ACK_REC\n\n        # Schedule transaction completion",
        "        # Schedule transaction completion",
        1,
    ),
    "status_phase_not_recognised": ("if ep_num == 0 and self._expecting_status_phase:", "if False:", 1),
    "start_trans_not_cleared": ("        self._sie_ctrl &= ~SIE_CTRL_START_TRANS\n", "", 1),
    "bus_reset_keeps_pid": ("            self._control_data_pid = 1  # Reset data toggle", "            pass", 1),
    "sof_does_not_raise_its_interrupt": ("        self._int_raw |= INTR_HOST_SOF\n", "", 1),
}
MUTANTS = tuple(MUTATIONS)


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(U)
    assert source.count(old) == count, f"{name}: {old!r} occurs {source.count(old)} times in _usb.py, expected {count}"
    module = types.ModuleType(f"rp2040py.peripherals._usb_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_usb mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def mutant_rig(name: str) -> Rig:
    """A rig whose controller is the pure-Python one with one expression changed, to prove the comparison sees a change of *logic*."""
    return Rig("mutant", _mutant_module(name))


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _reg_value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == U.MAIN_CTRL:
        return r.choice((0, 1, 2, 3, 0x80000000, 0x80000003, r.getrandbits(32)))
    if offset == U.SIE_CTRL:
        bits = (
            U.SIE_CTRL_EP0_INT_STALL, U.SIE_CTRL_EP0_DOUBLE_BUF, U.SIE_CTRL_EP0_INT_1BUF, U.SIE_CTRL_EP0_INT_2BUF, U.SIE_CTRL_EP0_INT_NAK, U.SIE_CTRL_PULLUP_EN,
            U.SIE_CTRL_PULLDOWN_EN, U.SIE_CTRL_RESET_BUS, U.SIE_CTRL_VBUS_EN, U.SIE_CTRL_KEEP_ALIVE_EN, U.SIE_CTRL_SOF_EN, U.SIE_CTRL_SOF_SYNC, U.SIE_CTRL_PREAMBLE_EN,
            U.SIE_CTRL_RECEIVE_DATA, U.SIE_CTRL_SEND_DATA, U.SIE_CTRL_SEND_SETUP, U.SIE_CTRL_START_TRANS, U.SIE_CTRL_STOP_TRANS, U.SIE_CTRL_RESUME,
        )  # fmt: skip
        if roll < 0.15:
            return r.getrandbits(32)
        value = 0
        for bit in bits:
            if r.random() < 0.25:
                value |= bit
        return value
    if offset == U.SIE_STATUS:
        return r.choice(
            (
                0xFFFFFFFF,
                0,
                U.SIE_SETUP_REC,
                U.SIE_TRANS_COMPLETE,
                U.SIE_BUS_RESET,
                U.SIE_CONNECTED,
                U.SIE_RESUME,
                U.SIE_SUSPENDED,
                0x300,
                r.getrandbits(32),
            )
        )
    if offset in (U.BUFF_STATUS, U.EP_ABORT_DONE, U.EP_STATUS_STALL_NAK):
        return r.choice((0xFFFFFFFF, 1 << r.randrange(32), 1 << r.randrange(8), r.getrandbits(32)))
    if offset == U.EP_ABORT:
        return r.choice((0, 1 << r.randrange(32), r.getrandbits(32)))
    if offset == U.USB_PWR:
        return r.choice((0, 0x3F, 0xC, 0x8, 0x4, 0x30, 0x20, 0x10, 0x3, r.getrandbits(32)))
    if offset == U.USB_MUXING:
        return r.choice((0, 1, 4, 8, 9, 0xC, 0xF, r.getrandbits(32)))
    if offset in (U.INTE, U.INTF):
        return r.choice((0, 0xFFFFF, 1 << r.randrange(20), r.getrandbits(20), r.getrandbits(32)))
    if offset == U.NAK_POLL:
        return r.choice((0, 0x00100010, 0x03FF03FF, r.getrandbits(32)))
    if offset == U.INT_EP_CTRL:
        return r.choice((0, 0xFFFE, 1 << r.randrange(1, 16), r.getrandbits(32)))
    if offset in (U.ADDR_ENDP, *range(U.ADDR_ENDP1, U.ADDR_ENDP15 + 4, 4)):
        return r.choice((0, 0x7F, 1, 0x00010001, 0x000F007F, 0x06010002, r.getrandbits(32)))
    if offset == U.SOF_WR:
        return r.choice((0, 0x7FF, 5, r.getrandbits(32)))
    if offset == U.EP_STALL_ARM:
        return r.choice((0, 1, 2, 3, r.getrandbits(32)))
    return r.getrandbits(32)


def _ep_control(r: random.Random, *, double: bool | None = None, iso: bool | None = None) -> int:
    value = r.choice(BUFFER_BASES)
    if r.random() < 0.9:
        value |= U.EP_CTRL_ENABLE_BITS
    if double is None:
        double = r.random() < 0.5
    if double:
        value |= U.USB_CTRL_DOUBLE_BUF
    roll = r.random()
    if roll < 0.7:
        value |= U.USB_CTRL_INTERRUPT_PER_TRANSFER
    elif roll < 0.85 and double:
        value |= U.EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER
    if iso is None:
        iso = r.random() < 0.15
    value |= (U.EP_CTRL_TYPE_ISOCHRONOUS if iso else r.choice((0, 2, 3))) << U.EP_CTRL_TYPE_SHIFT
    if r.random() < 0.2:
        value |= r.getrandbits(2) << 16  # interrupt on NAK / STALL
    return value


def _half(r: random.Random, *, out: bool, first: bool) -> int:
    length = r.choice((0, 1, 2, 7, 8, 32, 63, 64, 100, 1023)) if r.random() < 0.8 else r.randrange(1024)
    value = length
    if r.random() < 0.9:
        value |= U.USB_BUF_CTRL_AVAILABLE
    if (not out and r.random() < 0.9) or (out and r.random() < 0.08):
        value |= U.USB_BUF_CTRL_FULL
    if r.random() < 0.4:
        value |= U.USB_BUF_CTRL_DATA1_PID
    if r.random() < 0.3:
        value |= U.USB_BUF_CTRL_LAST
    if first and r.random() < 0.3:
        value |= U.USB_BUF_CTRL_RESET_SELECT
    if first and r.random() < 0.06:
        value |= U.USB_BUF_CTRL_STALL
    return value


def _buffer_control(r: random.Random, out: bool) -> int:
    word = _half(r, out=out, first=True)
    if r.random() < 0.5:
        word |= _half(r, out=out, first=False) << U.USB_BUF1_SHIFT
        if r.random() < 0.3:
            word |= r.getrandbits(2) << U.USB_BUF_CTRL_ISO_OFFSET_SHIFT
    return word


def _setup_packet(r: random.Random) -> bytes:
    return bytes(
        [
            r.choice((0x00, 0x80, 0x21, 0xA1)),
            r.choice((5, 6, 9, 0x20, 0x22)),
            r.getrandbits(8),
            r.getrandbits(8),
            r.getrandbits(8),
            0,
            r.getrandbits(8),
            0,
        ]
    )


def _host_setup_packet(r: random.Random) -> bytes:
    """A request a host sends a device: SET_ADDRESS, GET_DESCRIPTOR, SET_CONFIGURATION, a class request."""
    return r.choice(
        (
            bytes([0x00, 5, r.randrange(1, 128), 0, 0, 0, 0, 0]),
            bytes([0x80, 6, 0, 1, 0, 0, 18, 0]),
            bytes([0x80, 6, 0, 2, 0, 0, 9, 0]),
            bytes([0x00, 9, 1, 0, 0, 0, 0, 0]),
            bytes([0x21, 0x22, 3, 0, 0, 0, 0, 0]),
            _setup_packet(r),
        )
    )


def _buffer_offset(endpoint: int, out: bool) -> int:
    return U.EP0_IN_BUFFER_CONTROL + endpoint * 8 + (4 if out else 0)


def _ep_control_offset(endpoint: int, out: bool) -> int:
    return U.EP1_IN_CONTROL + (endpoint - 1) * 8 + (4 if out else 0)


def _scenario_device(r: random.Random) -> list[tuple]:
    """A device-mode exchange as TinyUSB sets it up: endpoints configured, buffers handed over, the host answering, the CPU acknowledging."""
    ops: list[tuple] = [
        ("reg_write", U.MAIN_CTRL, 1, 0),
        ("reg_write", U.INTE, r.choice((0xFFFFF, 0x1F0, 0x10)), 0),
        ("reg_write", U.USB_PWR, _reg_value(r, U.USB_PWR), 0),
    ]
    if r.random() < 0.7:
        ops.append(
            (
                "reg_write",
                U.SIE_CTRL,
                r.choice(
                    (
                        U.SIE_CTRL_EP0_INT_1BUF,
                        U.SIE_CTRL_EP0_INT_2BUF | U.SIE_CTRL_EP0_DOUBLE_BUF,
                        U.SIE_CTRL_EP0_INT_1BUF | U.SIE_CTRL_EP0_DOUBLE_BUF,
                    )
                ),
                0,
            )
        )
    endpoints = r.sample([1, 2, 3, 15], r.randrange(1, 4))
    for endpoint in endpoints:
        double = r.random() < 0.5
        for out in (False, True):
            ops.append(("dpram_write", _ep_control_offset(endpoint, out), _ep_control(r, double=double), 4))
    for _ in range(r.randrange(4, 14)):
        endpoint = r.choice([0, *endpoints])
        out = r.random() < 0.45
        roll = r.random()
        if roll < 0.5:
            ops.append(
                (
                    "dpram_bytes",
                    r.choice(BUFFER_BASES) + r.randrange(0, 3) * 64,
                    bytes(r.getrandbits(8) for _ in range(r.choice((1, 8, 64)))),
                )
            )
            ops.append(
                ("dpram_write", _buffer_offset(endpoint, out), _buffer_control(r, out), r.choice((4, 4, 4, 2, 1)))
            )
        elif roll < 0.65:
            ops.append(
                (
                    "host_data",
                    endpoint,
                    bytes(r.getrandbits(8) for _ in range(r.choice((0, 1, 5, 64, 100)))),
                    r.choice((None, 0, 3, 50)),
                )
            )
        elif roll < 0.75:
            ops.append(("setup", _setup_packet(r)))
        else:
            ops.append(("tick", r.choice(TICKS)))
        if r.random() < 0.5:
            ops.append(
                (
                    "reg_write",
                    U.BUFF_STATUS,
                    r.choice((0xFFFFFFFF, 1 << (endpoint * 2 + out))),
                    r.choice((0, 0, 0, 0x3000)),
                )
            )
        if r.random() < 0.25:
            ops.append(("reg_read", r.choice((U.BUFF_STATUS, U.BUFF_CPU_SHOULD_HANDLE, U.SIE_STATUS, U.INTS)), 0))
    ops.append(("tick", 2_000_000))
    return ops


def _scenario_host(r: random.Random) -> list[tuple]:
    """Host mode as TinyUSB's host driver uses it: attach a device, SOFs, a control transfer through endpoint X, an interrupt endpoint polled."""
    ops: list[tuple] = [("reg_write", U.MAIN_CTRL, 3, 0), ("connect",)]
    ops.append(("reg_write", U.INTE, r.choice((0xFFFFF, 0x1F, 0x1)), 0))
    if r.random() < 0.8:
        ops.append(("reg_write", U.SIE_CTRL, U.SIE_CTRL_SOF_EN | U.SIE_CTRL_KEEP_ALIVE_EN | U.SIE_CTRL_PULLDOWN_EN, 0))
    ops.append(("reg_write", U.SIE_STATUS, r.choice((0x300, 0xFFFFFFFF)), 0))
    ops.append(("reg_write", U.ADDR_ENDP, r.choice((0, 1, 0x00010002)), 0))
    if r.random() < 0.6:  # enumeration starts with SET_ADDRESS
        ops.append(("dpram_bytes", 0, bytes([0x00, 5, r.randrange(1, 128), 0, 0, 0, 0, 0])))
        ops.append(("dpram_write", 0x80, U.USB_BUF_CTRL_AVAILABLE | 0, 4))
        ops.append(("reg_write", U.SIE_CTRL, U.SIE_CTRL_SEND_SETUP | U.SIE_CTRL_SOF_EN | U.SIE_CTRL_START_TRANS, 0))
        ops.append(("tick", 200_000))
    for _ in range(r.randrange(2, 7)):
        roll = r.random()
        if roll < 0.35:  # a control transfer: setup (+ data stage in or out)
            ops.append(("dpram_bytes", 0, _host_setup_packet(r)))
            ops.append(
                (
                    "dpram_write",
                    0x80,
                    r.choice(
                        (
                            U.USB_BUF_CTRL_AVAILABLE | 64,
                            U.USB_BUF_CTRL_AVAILABLE | 8,
                            U.USB_BUF_CTRL_AVAILABLE | U.USB_BUF_CTRL_FULL | 4,
                        )
                    ),
                    4,
                )
            )
            ops.append(("dpram_write", 0x100, U.EP_CTRL_ENABLE_BITS | U.USB_CTRL_INTERRUPT_PER_TRANSFER | 0x180, 4))
            ops.append(
                (
                    "reg_write",
                    U.SIE_CTRL,
                    r.choice(
                        (
                            U.SIE_CTRL_SEND_SETUP | U.SIE_CTRL_RECEIVE_DATA,
                            U.SIE_CTRL_SEND_SETUP | U.SIE_CTRL_SEND_DATA,
                            U.SIE_CTRL_RECEIVE_DATA,
                            U.SIE_CTRL_SEND_DATA,
                        )
                    )
                    | U.SIE_CTRL_SOF_EN
                    | U.SIE_CTRL_START_TRANS,
                    0,
                )
            )
        elif roll < 0.6:  # an interrupt endpoint
            slot = r.randrange(1, 4)
            ops.append(
                ("reg_write", U.ADDR_ENDP1 + (slot - 1) * 4, r.choice((0x00010000, 0x02010000, 0x01, 0x00020001)), 0)
            )
            ops.append(
                (
                    "dpram_write",
                    U.EP1_IN_CONTROL + (slot - 1) * 8,
                    U.EP_CTRL_ENABLE_BITS
                    | (3 << U.EP_CTRL_TYPE_SHIFT)
                    | (r.randrange(0, 4) << U.EP_CTRL_HOST_INTERRUPT_INTERVAL_LSB)
                    | 0x300,
                    4,
                )
            )
            ops.append(("dpram_write", 0x88 + (slot - 1) * 8, U.USB_BUF_CTRL_AVAILABLE | 16, 4))
            ops.append(("reg_write", U.INT_EP_CTRL, 1 << slot, 0))
        elif roll < 0.75:
            ops.append(("reg_write", U.SIE_CTRL, r.choice((U.SIE_CTRL_RESET_BUS, 0, U.SIE_CTRL_SOF_EN)), 0))
        elif roll < 0.85:
            ops.append(("disconnect",) if r.random() < 0.5 else ("connect",))
        ops.append(("tick", r.choice((6_000, 1_000_000, 3_500_000, 12_000_000))))
        if r.random() < 0.6:
            ops.append(("reg_write", U.BUFF_STATUS, r.choice((0xFFFFFFFF, 1, 4)), 0))
        if r.random() < 0.5:
            ops.append(("reg_write", U.SIE_STATUS, 0xFFFFFFFF, 0))
        if r.random() < 0.4:
            ops.append(("reg_read", U.SOF_RD, 0))
    ops.append(("reg_write", U.MAIN_CTRL, 0, 0))
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.045:
            ops.extend(_scenario_device(r))
        elif roll < 0.06:
            ops.extend(_scenario_host(r))
        elif roll < 0.38:
            offset = r.choice((*REGISTER_OFFSETS, *PAST_THE_BLOCK))
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("reg_write", offset, _reg_value(r, offset), alias))
        elif roll < 0.52:
            offset = r.choice((*REGISTER_OFFSETS, *PAST_THE_BLOCK))
            ops.append(("reg_read", offset, r.choice(ALIASES) if r.random() < 0.05 else ALIASES[0]))
        elif roll < 0.62:
            endpoint = r.choice(ENDPOINTS)
            out = r.random() < 0.5
            ops.append(("dpram_write", _buffer_offset(endpoint, out), _buffer_control(r, out), r.choice((4, 4, 2, 1))))
        elif roll < 0.68 and True:
            endpoint = r.choice((1, 2, 3, 15))
            ops.append(("dpram_write", _ep_control_offset(endpoint, r.random() < 0.5), _ep_control(r), 4))
        elif roll < 0.73:
            ops.append(
                (
                    "dpram_bytes",
                    r.randrange(0, 0x1000 - 64),
                    bytes(r.getrandbits(8) for _ in range(r.choice((1, 4, 64)))),
                )
            )
        elif roll < 0.76:
            ops.append(("dpram_read", r.randrange(0, 0x1000, 4)))
        elif roll < 0.80:
            ops.append(("setup", _setup_packet(r)))
        elif roll < 0.85:
            ops.append(
                (
                    "host_data",
                    r.choice(ENDPOINTS),
                    bytes(r.getrandbits(8) for _ in range(r.choice((0, 3, 64)))),
                    r.choice((None, 0, 7, 200)),
                )
            )
        elif roll < 0.95:
            ops.append(("tick", r.choice(TICKS)))
        elif roll < 0.965:
            ops.append(("reset_device",))
        elif roll < 0.975:
            ops.append(("reset",))
        elif roll < 0.98:
            ops.append(("disconnect",))
        elif roll < 0.985:
            ops.append(("connect",))
        elif roll < 0.99:
            ops.append(
                (
                    "poke_status",
                    r.choice(
                        (
                            U.SIE_SUSPENDED,
                            U.SIE_RESUME,
                            U.SIE_STALL_REC,
                            U.SIE_CRC_ERROR,
                            U.SIE_BIT_STUFF_ERROR,
                            U.SIE_RX_OVERFLOW,
                            U.SIE_RX_TIMEOUT,
                            U.SIE_DATA_SEQ_ERROR,
                            U.SIE_NAK_REC,
                        )
                    ),
                )
            )
        elif roll < 0.995:
            ops.append(("poke_stall_nak", 1 << r.randrange(32)))
        else:
            ops.append(("reg_write", U.USB_PWR, _reg_value(r, U.USB_PWR), ALIASES[0]))
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


def _snapshot(rig: Rig) -> dict[str, Any]:
    try:
        return rig.snapshot()
    except Exception as error:  # noqa: BLE001
        return {"raised": f"{type(error).__name__}: {error}"}


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b.get(key, "<missing>")
        if value_a == value_b:
            continue
        if isinstance(value_a, (tuple, bytes)) and isinstance(value_b, (tuple, bytes)) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:70] for i in where[:6]]} vs {[str(value_b[i])[:70] for i in where[:6]]}"
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
    try:
        return _lockstep(a, b, ops, perturb)
    finally:
        del a, b
        if IS32BIT:
            gc.collect()


def _lockstep(a: Rig, b: Rig, ops: list[tuple], perturb: "Callable[[Rig, int], None] | None") -> "Divergence | None":
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
        snapshot_a, snapshot_b = _snapshot(a), _snapshot(b)
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    usb = rig.chip.usb_ctrl
    for op in ops:
        before = len(rig.log)
        status = int(usb._sie_status)
        buff_before = [len(done) for done in usb._buff_done]
        host_before = bool(usb._host_mode)
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(f"log.{event[0]}")
            if event[0] == "write":
                count("device.in_delivered")
                count("device.in_zero_length" if not event[3] else "device.in_data")
            if event[0] == "read":
                count("device.out_asked")
        if op[0] == "dpram_write" and op[1] >= U.EP0_IN_BUFFER_CONTROL and op[1] <= U.EP15_OUT_BUFFER_CONTROL:
            count(f"bufctrl.width{op[3]}")
            count("bufctrl.out" if op[1] & 4 else "bufctrl.in")
            if (op[2] >> U.USB_BUF1_SHIFT) & U.USB_BUF_CTRL_AVAILABLE:
                count("bufctrl.double")
            if op[2] & U.USB_BUF_CTRL_STALL:
                count("bufctrl.stall")
            if op[2] & U.USB_BUF_CTRL_RESET_SELECT:
                count("bufctrl.reset_select")
            if op[2] & U.USB_BUF_CTRL_AVAILABLE and not op[2] & U.USB_BUF_CTRL_FULL and not op[1] & 4:
                count("bufctrl.in_without_full")
            if op[2] & U.USB_BUF_CTRL_AVAILABLE and op[2] & U.USB_BUF_CTRL_FULL and op[1] & 4:
                count("bufctrl.out_already_full")
        if op[0] == "dpram_write" and U.EP1_IN_CONTROL <= op[1] < U.EP0_IN_BUFFER_CONTROL:
            count("epctrl.write")
            if (op[2] >> U.EP_CTRL_TYPE_SHIFT) & 3 == U.EP_CTRL_TYPE_ISOCHRONOUS:
                count("epctrl.iso")
            if op[2] & U.EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER:
                count("epctrl.per_two")
        for index, done in enumerate(usb._buff_done):
            if len(done) > buff_before[index]:
                count("buff_status.raised")
                if done[-1]:
                    count("buff_status.buffer1")
            if len(done) > 1:
                count("buff_status.two_waiting")
        if int(usb._sie_status) & U.SIE_TRANS_COMPLETE and not status & U.SIE_TRANS_COMPLETE:
            count("sie.trans_complete")
        if int(usb._sie_status) & U.SIE_SETUP_REC and not status & U.SIE_SETUP_REC:
            count("sie.setup_rec")
        if int(usb._sie_status) & U.SIE_BUS_RESET and not status & U.SIE_BUS_RESET:
            count("sie.bus_reset")
        if usb._host_mode and not host_before:
            count("host.mode_entered")
        if (
            op[0] == "reg_write"
            and op[1] == U.SIE_CTRL
            and op[2] & U.SIE_CTRL_START_TRANS
            and usb._host_mode
            and usb.connected_device is not None
        ):
            count("host.transaction")
        if usb._host_mode and usb.connected_device is not None and int(usb._int_ep_ctrl):
            count("host.interrupt_endpoints_armed")
        if op[0] == "reg_write" and op[1] == U.BUFF_STATUS:
            count("buff_status.cleared")
        if op[0] == "reg_read" and op[1] == U.SOF_RD:
            count("sof_rd.read")
        if op[0] in ("reg_write", "reg_read") and op[1] in PAST_THE_BLOCK:
            count("past_the_block")
        if op[0] in ("reg_write", "reg_read") and len(op) > 3 and op[3] != 0:
            count("alias")
        if op[0] == "reg_write" and op[1] == U.USB_PWR:
            count("usb_pwr.write")
        if op[0] == "reset":
            count("reset")
        if op[0] in ("poke_status", "poke_stall_nak"):
            count(f"poke.{op[0]}")
    return counts
