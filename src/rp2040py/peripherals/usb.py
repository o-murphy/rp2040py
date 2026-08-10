from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.clock.clock import IAlarm
from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.usb.usb_device import StandardRequest, USBDevice, USBTransferResult, parse_setup_packet

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPUSBController",)

ENDPOINT_COUNT = 16
USB_HOST_INTERRUPT_ENDPOINTS = 15

# USB DPSRAM Registers - Device mode
EP1_IN_CONTROL = 0x8
EP0_IN_BUFFER_CONTROL = 0x80
EP0_OUT_BUFFER_CONTROL = 0x84
EP15_OUT_BUFFER_CONTROL = 0xFC

# USB DPRAM Registers - Host mode
# Host DPRAM layout (from pico-sdk hardware/structs/usb.h):
# 0x00-0x07: setup_packet (8 bytes)
# 0x08-0x7f: int_ep_ctrl[15] (15 x 8 bytes, ctrl + spare per entry)
# 0x80: epx_buf_ctrl (4 bytes)
# 0x84: _spare0 (4 bytes)
# 0x88-0xff: int_ep_buffer_ctrl[15] (15 x 8 bytes, ctrl + spare per entry)
# 0x100: epx_ctrl (4 bytes)
# 0x104-0x17f: _spare1 (124 bytes)
# 0x180+: epx_data buffer (up to end of DPRAM)
HOST_SETUP_PACKET = 0x00
HOST_INT_EP_CTRL_BASE = 0x08  # int_ep_ctrl[0] at 0x08, stride 8
HOST_INT_EP_BUF_CTRL_BASE = 0x88  # int_ep_buffer_ctrl[0] at 0x88, stride 8
HOST_EPX_BUF_CTRL = 0x80
HOST_EPX_DATA = 0x180

# Endpoint Control bits
USB_CTRL_DOUBLE_BUF = 1 << 30
USB_CTRL_INTERRUPT_PER_TRANSFER = 1 << 29
EP_CTRL_ENABLE_BITS = 1 << 31
EP_CTRL_BUFFER_TYPE_LSB = 26
EP_CTRL_HOST_INTERRUPT_INTERVAL_LSB = 16

# Buffer Control bits
USB_BUF_CTRL_AVAILABLE = 1 << 10
USB_BUF_CTRL_FULL = 1 << 15
USB_BUF_CTRL_LEN_MASK = 0x3FF
USB_BUF_CTRL_DATA1_PID = 1 << 13
USB_BUF_CTRL_LAST = 1 << 14
# Buffer1
USB_BUF1_SHIFT = 16
USB_BUF1_OFFSET = 64

# USB Peripheral Registers
ADDR_ENDP = 0x0
ADDR_ENDP1 = 0x04
ADDR_ENDP15 = 0x3C
MAIN_CTRL = 0x40
SOF_WR = 0x44
SOF_RD = 0x48
SIE_CTRL = 0x4C
SIE_STATUS = 0x50
INT_EP_CTRL = 0x54
BUFF_STATUS = 0x58
BUFF_CPU_SHOULD_HANDLE = 0x5C
EP_ABORT = 0x60
EP_ABORT_DONE = 0x64
EP_STALL_ARM = 0x68
NAK_POLL = 0x6C
EP_STATUS_STALL_NAK = 0x70
USB_MUXING = 0x74
USB_PWR = 0x78
USBPHY_DIRECT = 0x7C
USBPHY_DIRECT_OVERRIDE = 0x80
USBPHY_TRIM = 0x84
INTR = 0x8C
INTE = 0x90
INTF = 0x94
INTS = 0x98

# MAIN_CTRL bits
SIM_TIMING = 1 << 31
HOST_NDEVICE = 1 << 1
CONTROLLER_EN = 1 << 0

# SIE_CTRL bits (host mode)
SIE_CTRL_EP0_INT_STALL = 1 << 31
SIE_CTRL_EP0_DOUBLE_BUF = 1 << 30
SIE_CTRL_EP0_INT_1BUF = 1 << 29
SIE_CTRL_EP0_INT_2BUF = 1 << 28
SIE_CTRL_EP0_INT_NAK = 1 << 27
SIE_CTRL_DIRECT_EN = 1 << 26
SIE_CTRL_DIRECT_DP = 1 << 25
SIE_CTRL_DIRECT_DM = 1 << 24
SIE_CTRL_TRANSCEIVER_PD = 1 << 18
SIE_CTRL_RPU_OPT = 1 << 17
SIE_CTRL_PULLUP_EN = 1 << 16
SIE_CTRL_PULLDOWN_EN = 1 << 15
SIE_CTRL_RESET_BUS = 1 << 13
SIE_CTRL_RESUME = 1 << 12
SIE_CTRL_VBUS_EN = 1 << 11
SIE_CTRL_KEEP_ALIVE_EN = 1 << 10
SIE_CTRL_SOF_EN = 1 << 9
SIE_CTRL_SOF_SYNC = 1 << 8
SIE_CTRL_PREAMBLE_EN = 1 << 6
SIE_CTRL_STOP_TRANS = 1 << 4
SIE_CTRL_RECEIVE_DATA = 1 << 3
SIE_CTRL_SEND_DATA = 1 << 2
SIE_CTRL_SEND_SETUP = 1 << 1
SIE_CTRL_START_TRANS = 1 << 0

# SIE_STATUS bits
SIE_DATA_SEQ_ERROR = 1 << 31
SIE_ACK_REC = 1 << 30
SIE_STALL_REC = 1 << 29
SIE_NAK_REC = 1 << 28
SIE_RX_TIMEOUT = 1 << 27
SIE_RX_OVERFLOW = 1 << 26
SIE_BIT_STUFF_ERROR = 1 << 25
SIE_CRC_ERROR = 1 << 24
SIE_BUS_RESET = 1 << 19
SIE_TRANS_COMPLETE = 1 << 18
SIE_SETUP_REC = 1 << 17
SIE_CONNECTED = 1 << 16
SIE_RESUME = 1 << 11
SIE_VBUS_OVER_CURR = 1 << 10
SIE_SPEED = 1 << 9
SIE_SPEED_LS_VALUE = 1  # Low speed
SIE_SPEED_FS_VALUE = 2  # Full speed
SIE_SUSPENDED = 1 << 4
SIE_LINE_STATE_MASK = 0x3
SIE_LINE_STATE_SHIFT = 2
SIE_VBUS_DETECTED = 1 << 0

# USB_MUXING bits
SOFTCON = 1 << 3
TO_DIGITAL_PAD = 1 << 2
TO_EXTPHY = 1 << 1
TO_PHY = 1 << 0

# USB_PWR bits
PWR_VBUS_DETECT = 1 << 3
PWR_VBUS_DETECT_OVERRIDE_EN = 1 << 2
PWR_OVERCURR_DETECT = 1 << 1
PWR_OVERCURR_DETECT_EN = 1 << 0

# INTR bits (directly from RP2040 datasheet)
INTR_EP_STALL_NAK = 1 << 19
INTR_ABORT_DONE = 1 << 18
INTR_DEV_SOF = 1 << 17
INTR_SETUP_REQ = 1 << 16
INTR_DEV_RESUME_FROM_HOST = 1 << 15
INTR_DEV_SUSPEND = 1 << 14
INTR_DEV_CONN_DIS = 1 << 13
INTR_BUS_RESET = 1 << 12
INTR_VBUS_DETECT = 1 << 11
INTR_STALL = 1 << 10
INTR_ERROR_CRC = 1 << 9
INTR_ERROR_BIT_STUFF = 1 << 8
INTR_ERROR_RX_OVERFLOW = 1 << 7
INTR_ERROR_RX_TIMEOUT = 1 << 6
INTR_ERROR_DATA_SEQ = 1 << 5
INTR_BUFF_STATUS = 1 << 4
INTR_TRANS_COMPLETE = 1 << 3
INTR_HOST_SOF = 1 << 2
INTR_HOST_RESUME = 1 << 1
INTR_HOST_CONN_DIS = 1 << 0


# SIE Line states
class SIELineState(IntEnum):
    SE0 = 0b00
    J = 0b01
    K = 0b10
    SE1 = 0b11


SIE_WRITECLEAR_MASK = (
    SIE_DATA_SEQ_ERROR
    | SIE_ACK_REC
    | SIE_STALL_REC
    | SIE_NAK_REC
    | SIE_RX_TIMEOUT
    | SIE_RX_OVERFLOW
    | SIE_BIT_STUFF_ERROR
    | SIE_CONNECTED
    | SIE_CRC_ERROR
    | SIE_BUS_RESET
    | SIE_TRANS_COMPLETE
    | SIE_SETUP_REC
    | SIE_RESUME
)


class USBEndpointAlarm:
    def __init__(self, alarm: IAlarm):
        self.alarm = alarm
        self.buffers: list[bytes | bytearray] = []

    def schedule(self, buffer: bytes | bytearray, delay_nanos: int) -> None:
        self.buffers.append(buffer)
        self.alarm.schedule(delay_nanos)


def _get_uint32_le(buffer: bytearray, offset: int) -> int:
    return int.from_bytes(buffer[offset : offset + 4], "little")


def _set_uint32_le(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")


class RPUSBController(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)

        # Common registers
        self._addr_endp = 0
        self._main_ctrl = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._sie_status = 0
        self._buff_status = 0

        # Host mode registers
        self._sie_ctrl = 0
        self._sof_frame_number = 0
        self._dev_addr_ctrl = 0  # Device address and endpoint for non-interrupt transfers
        self._int_ep_addr_ctrl = [0] * USB_HOST_INTERRUPT_ENDPOINTS
        self._int_ep_ctrl = 0  # Interrupt endpoint control (enable bits)
        self._usb_pwr = 0
        self._nak_poll = 0
        self._ep_abort = 0
        self._ep_abort_done = 0
        self._ep_stall_arm = 0
        self._ep_status_stall_nak = 0

        # Host mode state
        self._host_mode = False
        self._sof_enabled = False
        self.connected_device: USBDevice | None = None
        self._pending_setup_response: bytes | bytearray | None = None
        self._control_data_pid = 1  # DATA0/DATA1 toggle for control transfers
        self._expecting_status_phase = False  # True when expecting IN status for control OUT

        # Device mode callbacks
        self.on_usb_enabled: Callable[[], None] | None = None
        self.on_reset_received: Callable[[], None] | None = None
        self.on_endpoint_write: Callable[[int, bytes | bytearray], None] | None = None
        self.on_endpoint_read: Callable[[int, int], None] | None = None

        self.read_delay_microseconds = 10
        self.write_delay_microseconds = 10  # Determined empirically
        self.host_transaction_delay_microseconds = 5  # Host transaction delay

        clock = rp2040.clock
        self.endpoint_read_alarms: list[USBEndpointAlarm] = []
        self.endpoint_write_alarms: list[USBEndpointAlarm] = []
        for i in range(ENDPOINT_COUNT):
            self.endpoint_read_alarms.append(USBEndpointAlarm(clock.create_alarm(self._make_read_alarm(i))))
            self.endpoint_write_alarms.append(USBEndpointAlarm(clock.create_alarm(self._make_write_alarm(i))))

        self.reset_alarm = clock.create_alarm(self._on_reset_alarm)

        # Host mode alarms
        self.sof_alarm = clock.create_alarm(self._generate_sof)
        self.host_transaction_alarm = clock.create_alarm(self._complete_host_transaction)

    def _make_read_alarm(self, index: int) -> Callable[[], None]:
        def _on_alarm() -> None:
            alarm = self.endpoint_read_alarms[index]
            if alarm.buffers:
                buffer = alarm.buffers.pop(0)
                self._finish_read(index, buffer)

        return _on_alarm

    def _make_write_alarm(self, index: int) -> Callable[[], None]:
        def _on_alarm() -> None:
            alarm = self.endpoint_write_alarms[index]
            for buffer in alarm.buffers:
                if self.on_endpoint_write:
                    self.on_endpoint_write(index, buffer)
            alarm.buffers = []

        return _on_alarm

    def _on_reset_alarm(self) -> None:
        self._sie_status |= SIE_BUS_RESET
        self._sie_status_updated()

    @property
    def int_status(self) -> int:
        return (self._int_raw & self._int_enable) | self._int_force

    def read_uint32(self, offset: int) -> int:
        # Handle interrupt endpoint address registers (ADDR_ENDP1 through ADDR_ENDP15)
        if ADDR_ENDP1 <= offset <= ADDR_ENDP15 and (offset & 0x3) == 0:
            ep_index = (offset - ADDR_ENDP1) >> 2
            return self._int_ep_addr_ctrl[ep_index]

        if offset == ADDR_ENDP:
            return self._dev_addr_ctrl if self._host_mode else self._addr_endp & 0b1111000000001111111
        if offset == MAIN_CTRL:
            return self._main_ctrl
        if offset == SOF_WR:
            return 0  # Write-only
        if offset == SOF_RD:
            return self._sof_frame_number & 0x7FF
        if offset == SIE_CTRL:
            return self._sie_ctrl
        if offset == SIE_STATUS:
            # In host mode, reading SIE_STATUS acknowledges the connection event
            # Clear HOST_CONN_DIS interrupt once firmware reads the status
            if self._host_mode and self._int_raw & INTR_HOST_CONN_DIS:
                self._int_raw &= ~INTR_HOST_CONN_DIS
                self.check_interrupts()
            return self._sie_status
        if offset == INT_EP_CTRL:
            return self._int_ep_ctrl
        if offset == BUFF_STATUS:
            return self._buff_status
        if offset == BUFF_CPU_SHOULD_HANDLE:
            return 0
        if offset == EP_ABORT:
            return self._ep_abort
        if offset == EP_ABORT_DONE:
            return self._ep_abort_done
        if offset == EP_STALL_ARM:
            return self._ep_stall_arm
        if offset == NAK_POLL:
            return self._nak_poll
        if offset == EP_STATUS_STALL_NAK:
            return self._ep_status_stall_nak
        if offset == USB_PWR:
            return self._usb_pwr
        if offset == INTR:
            return self._int_raw
        if offset == INTE:
            return self._int_enable
        if offset == INTF:
            return self._int_force
        if offset == INTS:
            return self.int_status
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        # Handle interrupt endpoint address registers (ADDR_ENDP1 through ADDR_ENDP15)
        if ADDR_ENDP1 <= offset <= ADDR_ENDP15 and (offset & 0x3) == 0:
            ep_index = (offset - ADDR_ENDP1) >> 2
            self._int_ep_addr_ctrl[ep_index] = value
            return

        if offset == ADDR_ENDP:
            if self._host_mode:
                self._dev_addr_ctrl = value
            else:
                self._addr_endp = value
        elif offset == MAIN_CTRL:
            self._main_ctrl = value & (SIM_TIMING | CONTROLLER_EN | HOST_NDEVICE)
            self._host_mode = bool(value & HOST_NDEVICE)
            if value & CONTROLLER_EN:
                if self._host_mode:
                    self.debug("USB Host mode enabled")
                    # In host mode, check if a device is already connected
                    if self.connected_device:
                        self._on_device_connected()
                elif self.on_usb_enabled:
                    self.on_usb_enabled()
        elif offset == SOF_WR:
            self._sof_frame_number = value & 0x7FF
        elif offset == SIE_CTRL:
            self._handle_sie_ctrl_write(value)
        elif offset == INT_EP_CTRL:
            self._int_ep_ctrl = value
        elif offset == BUFF_STATUS:
            self._buff_status &= ~self.raw_write_value
            self._buff_status_updated()
        elif offset == EP_ABORT:
            self._ep_abort = value
            # Immediately mark as done
            self._ep_abort_done |= value
        elif offset == EP_ABORT_DONE:
            self._ep_abort_done &= ~self.raw_write_value
        elif offset == EP_STALL_ARM:
            self._ep_stall_arm = value
        elif offset == NAK_POLL:
            self._nak_poll = value
        elif offset == EP_STATUS_STALL_NAK:
            self._ep_status_stall_nak &= ~self.raw_write_value
        elif offset == USB_MUXING:
            # Workaround for busy wait in hw_enumeration_fix_force_ls_j() / hw_enumeration_fix_finish():
            if value & TO_DIGITAL_PAD and not (value & TO_PHY):
                self._sie_status |= SIE_CONNECTED
        elif offset == USB_PWR:
            self._usb_pwr = value
            # VBUS detect override - set VBUS detected in SIE_STATUS
            if value & PWR_VBUS_DETECT_OVERRIDE_EN:
                if value & PWR_VBUS_DETECT:
                    self._sie_status |= SIE_VBUS_DETECTED
                else:
                    self._sie_status &= ~SIE_VBUS_DETECTED
        elif offset == SIE_STATUS:
            self._sie_status &= ~(self.raw_write_value & SIE_WRITECLEAR_MASK)
            if self.raw_write_value & SIE_BUS_RESET:
                if not self._host_mode and self.on_reset_received:
                    self.on_reset_received()
                self._sie_status &= ~(SIE_LINE_STATE_MASK << SIE_LINE_STATE_SHIFT)
                self._sie_status |= (SIELineState.J << SIE_LINE_STATE_SHIFT) | SIE_CONNECTED
            self._sie_status_updated()
        elif offset == INTE:
            self._int_enable = value & 0xFFFFF
            self.check_interrupts()
        elif offset == INTF:
            self._int_force = value & 0xFFFFF
            self.check_interrupts()
        else:
            super().write_uint32(offset, value)

    def _read_endpoint_control_reg(self, endpoint: int, out: bool) -> int:
        control_reg_offset = EP1_IN_CONTROL + 8 * (endpoint - 1) + (4 if out else 0)
        return _get_uint32_le(self.rp2040.usb_dpram, control_reg_offset)

    def _get_endpoint_buffer_offset(self, endpoint: int, out: bool) -> int:
        if endpoint == 0:
            return 0x100
        return self._read_endpoint_control_reg(endpoint, out) & 0xFFC0

    def dpram_updated(self, offset: int, value: int) -> None:
        # Skip device-mode buffer control handling in host mode
        if self._host_mode:
            return
        if value & USB_BUF_CTRL_AVAILABLE and EP0_IN_BUFFER_CONTROL <= offset <= EP15_OUT_BUFFER_CONTROL:
            endpoint = (offset - EP0_IN_BUFFER_CONTROL) >> 3
            buffer_out = bool(offset & 4)
            double_buffer = False
            interrupt = True
            if endpoint != 0:
                control = self._read_endpoint_control_reg(endpoint, buffer_out)
                double_buffer = bool(control & USB_CTRL_DOUBLE_BUF)
                interrupt = bool(control & USB_CTRL_INTERRUPT_PER_TRANSFER)

            if double_buffer and (value >> USB_BUF1_SHIFT) & USB_BUF_CTRL_AVAILABLE:
                buffer_length = (value >> USB_BUF1_SHIFT) & USB_BUF_CTRL_LEN_MASK
                buffer_offset = self._get_endpoint_buffer_offset(endpoint, buffer_out) + USB_BUF1_OFFSET
                self.debug(
                    f"Start USB transfer, endPoint={endpoint}, "
                    f"direction={'out' if buffer_out else 'in'} "
                    f"buffer={buffer_offset:x} length={buffer_length}"
                )
                value &= ~(USB_BUF_CTRL_AVAILABLE << USB_BUF1_SHIFT)
                _set_uint32_le(self.rp2040.usb_dpram, offset, value)
                if buffer_out:
                    if self.on_endpoint_read:
                        self.on_endpoint_read(endpoint, buffer_length)
                else:
                    value &= ~(USB_BUF_CTRL_FULL << USB_BUF1_SHIFT)
                    _set_uint32_le(self.rp2040.usb_dpram, offset, value)
                    buffer = bytes(self.rp2040.usb_dpram[buffer_offset : buffer_offset + buffer_length])
                    self._indicate_buffer_ready(endpoint, False)
                    self.endpoint_write_alarms[endpoint].schedule(buffer, self.write_delay_microseconds * 1000)

            buffer_length = value & USB_BUF_CTRL_LEN_MASK
            buffer_offset = self._get_endpoint_buffer_offset(endpoint, buffer_out)
            self.debug(
                f"Start USB transfer, endPoint={endpoint}, "
                f"direction={'out' if buffer_out else 'in'} "
                f"buffer={buffer_offset:x} length={buffer_length}"
            )
            value &= ~USB_BUF_CTRL_AVAILABLE
            _set_uint32_le(self.rp2040.usb_dpram, offset, value)
            if buffer_out:
                if self.on_endpoint_read:
                    self.on_endpoint_read(endpoint, buffer_length)
            else:
                value &= ~USB_BUF_CTRL_FULL
                _set_uint32_le(self.rp2040.usb_dpram, offset, value)
                buffer = bytes(self.rp2040.usb_dpram[buffer_offset : buffer_offset + buffer_length])
                if interrupt or not double_buffer:
                    self._indicate_buffer_ready(endpoint, False)
                self.endpoint_write_alarms[endpoint].schedule(buffer, self.write_delay_microseconds * 1000)

    def endpoint_read_done(self, endpoint: int, buffer: bytes | bytearray, delay: int | None = None) -> None:
        if delay is None:
            delay = self.read_delay_microseconds
        self.endpoint_read_alarms[endpoint].schedule(buffer, delay * 1000)

    def _finish_read(self, endpoint: int, buffer: bytes | bytearray) -> None:
        buffer_offset = self._get_endpoint_buffer_offset(endpoint, True)
        buf_control_reg = EP0_OUT_BUFFER_CONTROL + endpoint * 8
        buf_control = _get_uint32_le(self.rp2040.usb_dpram, buf_control_reg)
        requested_length = buf_control & USB_BUF_CTRL_LEN_MASK
        new_length = min(len(buffer), requested_length)
        buf_control |= USB_BUF_CTRL_FULL
        buf_control = (buf_control & ~USB_BUF_CTRL_LEN_MASK) | (new_length & USB_BUF_CTRL_LEN_MASK)
        _set_uint32_le(self.rp2040.usb_dpram, buf_control_reg, buf_control)
        self.rp2040.usb_dpram[buffer_offset : buffer_offset + new_length] = buffer[:new_length]
        self._indicate_buffer_ready(endpoint, True)

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(IRQ.USBCTRL, bool(self.int_status))

    def reset(self) -> None:
        """Callbacks (on_usb_enabled etc.), the alarm objects themselves, and read/write_delay
        tuning are left alone - only the register/transaction state a real RESETS-block reset
        would clear. Used by USBCDC.reset() (RPWatchdog.on_watchdog_trigger's live device reset -
        see base_device.py), where this controller object survives the reset in place rather than
        being reconstructed, unlike a fresh boot where a new one is simply constructed instead."""
        self._addr_endp = 0
        self._main_ctrl = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._sie_status = 0
        self._buff_status = 0

        self._sie_ctrl = 0
        self._sof_frame_number = 0
        self._dev_addr_ctrl = 0
        for i in range(len(self._int_ep_addr_ctrl)):
            self._int_ep_addr_ctrl[i] = 0
        self._int_ep_ctrl = 0
        self._usb_pwr = 0
        self._nak_poll = 0
        self._ep_abort = 0
        self._ep_abort_done = 0
        self._ep_stall_arm = 0
        self._ep_status_stall_nak = 0

        self._host_mode = False
        self._sof_enabled = False
        self.connected_device = None
        self._pending_setup_response = None
        self._control_data_pid = 1
        self._expecting_status_phase = False

        for alarm in (self.reset_alarm, self.sof_alarm, self.host_transaction_alarm):
            alarm.cancel()
        for endpoint_alarm in (*self.endpoint_read_alarms, *self.endpoint_write_alarms):
            endpoint_alarm.alarm.cancel()
            endpoint_alarm.buffers = []

        self.check_interrupts()

    def reset_device(self) -> None:
        self.reset_alarm.schedule(10_000_000)  # USB reset takes ~10ms

    def send_setup_packet(self, setup_packet: bytes | bytearray) -> None:
        self.rp2040.usb_dpram[: len(setup_packet)] = setup_packet
        self._sie_status |= SIE_SETUP_REC
        self._sie_status_updated()

    def _indicate_buffer_ready(self, endpoint: int, out: bool) -> None:
        self._buff_status |= 1 << (endpoint * 2 + (1 if out else 0))
        self._buff_status_updated()

    def _buff_status_updated(self) -> None:
        if self._buff_status:
            self._int_raw |= INTR_BUFF_STATUS
        else:
            self._int_raw &= ~INTR_BUFF_STATUS
        self.check_interrupts()

    def _sie_status_updated(self) -> None:
        if self._host_mode:
            # Host mode interrupt mapping
            int_register_map = [
                (SIE_TRANS_COMPLETE, INTR_TRANS_COMPLETE),
                (SIE_STALL_REC, INTR_STALL),
                (SIE_CRC_ERROR, INTR_ERROR_CRC),
                (SIE_BIT_STUFF_ERROR, INTR_ERROR_BIT_STUFF),
                (SIE_RX_OVERFLOW, INTR_ERROR_RX_OVERFLOW),
                (SIE_RX_TIMEOUT, INTR_ERROR_RX_TIMEOUT),
                (SIE_DATA_SEQ_ERROR, INTR_ERROR_DATA_SEQ),
            ]
        else:
            # Device mode interrupt mapping
            int_register_map = [
                (SIE_SETUP_REC, INTR_SETUP_REQ),
                (SIE_RESUME, INTR_DEV_RESUME_FROM_HOST),
                (SIE_SUSPENDED, INTR_DEV_SUSPEND),
                (SIE_CONNECTED, INTR_DEV_CONN_DIS),
                (SIE_BUS_RESET, INTR_BUS_RESET),
                (SIE_VBUS_DETECTED, INTR_VBUS_DETECT),
                (SIE_STALL_REC, INTR_STALL),
                (SIE_CRC_ERROR, INTR_ERROR_CRC),
                (SIE_BIT_STUFF_ERROR, INTR_ERROR_BIT_STUFF),
                (SIE_RX_OVERFLOW, INTR_ERROR_RX_OVERFLOW),
                (SIE_RX_TIMEOUT, INTR_ERROR_RX_TIMEOUT),
                (SIE_DATA_SEQ_ERROR, INTR_ERROR_DATA_SEQ),
            ]
        for sie_bit, int_raw_bit in int_register_map:
            if self._sie_status & sie_bit:
                self._int_raw |= int_raw_bit
            else:
                self._int_raw &= ~int_raw_bit
        self.check_interrupts()

    # ============ Host Mode Methods ============

    def connect_device(self, device: USBDevice) -> None:
        """Connect a simulated USB device to the host controller."""
        self.connected_device = device
        if self._host_mode and self._main_ctrl & CONTROLLER_EN:
            self._on_device_connected()

    def disconnect_device(self) -> None:
        """Disconnect the simulated USB device from the host controller."""
        if self.connected_device and self._host_mode:
            self.connected_device = None
            # Clear speed bits to indicate disconnection
            self._sie_status &= ~(0x3 << 8)  # Clear speed bits
            self._int_raw |= INTR_HOST_CONN_DIS
            self.check_interrupts()
        self.connected_device = None

    def _on_device_connected(self) -> None:
        # Set full-speed device connected (value 2 in speed field, bits 9:8)
        self._sie_status &= ~(0x3 << 8)
        self._sie_status |= SIE_SPEED_FS_VALUE << 8
        self._int_raw |= INTR_HOST_CONN_DIS
        self.check_interrupts()
        self.debug("USB device connected (full-speed)")

    def _handle_sie_ctrl_write(self, value: int) -> None:
        self._sie_ctrl = value

        # Handle SOF enable/disable
        if value & SIE_CTRL_SOF_EN and not self._sof_enabled:
            self._sof_enabled = True
            self._schedule_sof_packet()
            self.debug("SOF generation enabled")
        elif not (value & SIE_CTRL_SOF_EN) and self._sof_enabled:
            self._sof_enabled = False
            self.debug("SOF generation disabled")

        # Handle bus reset
        if value & SIE_CTRL_RESET_BUS:
            self.debug("USB bus reset initiated")
            if self.connected_device:
                self.connected_device.on_reset()
            self._control_data_pid = 1  # Reset data toggle

        # Handle start transaction
        if value & SIE_CTRL_START_TRANS:
            self._start_host_transaction()

    def _schedule_sof_packet(self) -> None:
        if self._sof_enabled:
            # SOF every 1ms = 1,000,000 ns
            self.sof_alarm.schedule(1_000_000)

    def _generate_sof(self) -> None:
        self._sof_frame_number = (self._sof_frame_number + 1) & 0x7FF
        self._int_raw |= INTR_HOST_SOF
        self.check_interrupts()

        # Poll interrupt endpoints
        self._poll_interrupt_endpoints()

        # Schedule next SOF
        self._schedule_sof_packet()

    def _poll_interrupt_endpoints(self) -> None:
        # Debug: log if any interrupt endpoints are enabled
        if self._int_ep_ctrl and self._sof_frame_number % 100 == 0:
            self.debug(f"INT_EP poll: intEpCtrl=0x{self._int_ep_ctrl:x} sofFrame={self._sof_frame_number}")
        # Check each enabled interrupt endpoint
        for i in range(USB_HOST_INTERRUPT_ENDPOINTS):
            ep_ctrl_bit = 1 << (i + 1)
            if not (self._int_ep_ctrl & ep_ctrl_bit):
                continue

            addr_endp = self._int_ep_addr_ctrl[i]
            dev_addr = addr_endp & 0x7F
            ep_num = (addr_endp >> 16) & 0xF
            is_out = bool(addr_endp & (1 << 25))  # INTEP_DIR bit

            # Debug: log endpoint config
            if self._sof_frame_number % 500 == 0:
                connected_addr = self.connected_device.address if self.connected_device else None
                self.debug(
                    f"INT_EP[{i}]: addrEndp=0x{addr_endp:x} devAddr={dev_addr} "
                    f"epNum={ep_num} connectedAddr={connected_addr}"
                )

            if not self.connected_device or self.connected_device.address != dev_addr:
                continue

            # Get the endpoint control register for interval checking
            ep_ctrl_offset = HOST_INT_EP_CTRL_BASE + i * 8
            ep_ctrl = _get_uint32_le(self.rp2040.usb_dpram, ep_ctrl_offset)
            interval = ((ep_ctrl >> EP_CTRL_HOST_INTERRUPT_INTERVAL_LSB) & 0x1FF) + 1

            # Check if it's time to poll (simplified: poll every SOF for now)
            if self._sof_frame_number % interval != 0:
                continue

            # Get buffer control
            buf_ctrl_offset = HOST_INT_EP_BUF_CTRL_BASE + i * 8
            buf_ctrl = _get_uint32_le(self.rp2040.usb_dpram, buf_ctrl_offset)

            # Debug: log buffer status
            if self._sof_frame_number % 500 == 0:
                self.debug(f"INT_EP[{i}]: bufCtrl=0x{buf_ctrl:x} available={bool(buf_ctrl & USB_BUF_CTRL_AVAILABLE)}")

            # Only poll if buffer is available
            if not (buf_ctrl & USB_BUF_CTRL_AVAILABLE):
                continue

            # For IN endpoints, request data from device
            if not is_out:
                ep_addr = 0x80 | ep_num  # IN endpoint
                result = self.connected_device.handle_data_in(ep_addr)

                if result.status == "ack" and result.data:
                    # Write data to the interrupt endpoint buffer
                    buffer_offset = ep_ctrl & 0xFFC0
                    self.rp2040.usb_dpram[buffer_offset : buffer_offset + len(result.data)] = result.data

                    # Update buffer control
                    new_buf_ctrl = buf_ctrl & ~USB_BUF_CTRL_AVAILABLE
                    new_buf_ctrl |= USB_BUF_CTRL_FULL
                    new_buf_ctrl = (new_buf_ctrl & ~USB_BUF_CTRL_LEN_MASK) | (len(result.data) & USB_BUF_CTRL_LEN_MASK)
                    _set_uint32_le(self.rp2040.usb_dpram, buf_ctrl_offset, new_buf_ctrl)

                    # Set buffer status for this interrupt endpoint
                    # Interrupt EPs use bits 2+ in buff_status (bit 0 is epx IN, bit 1 is epx OUT)
                    self._buff_status |= 1 << ((i + 1) * 2)
                    self._buff_status_updated()

    def _start_host_transaction(self) -> None:
        if not self._host_mode:
            return

        dev_addr = self._dev_addr_ctrl & 0x7F
        ep_num = (self._dev_addr_ctrl >> 16) & 0xF

        self.debug(f"Host transaction: dev={dev_addr} ep={ep_num} sieCtrl=0x{self._sie_ctrl:x}")

        if not self.connected_device:
            # No device connected - timeout
            self._sie_status |= SIE_RX_TIMEOUT
            self._sie_status_updated()
            return

        # Determine transaction type
        if self._sie_ctrl & SIE_CTRL_SEND_SETUP:
            self._handle_setup_transaction(dev_addr)
        elif self._sie_ctrl & SIE_CTRL_RECEIVE_DATA:
            self._handle_in_transaction(dev_addr, ep_num)
        elif self._sie_ctrl & SIE_CTRL_SEND_DATA:
            self._handle_out_transaction(dev_addr, ep_num)

    def _handle_setup_transaction(self, dev_addr: int) -> None:
        # Read setup packet from DPRAM
        setup_packet = bytes(self.rp2040.usb_dpram[HOST_SETUP_PACKET : HOST_SETUP_PACKET + 8])
        setup = parse_setup_packet(setup_packet)

        self.debug(
            f"SETUP: bmRequestType=0x{setup.bm_request_type:x} bRequest={setup.b_request} "
            f"wValue=0x{setup.w_value:x} wIndex={setup.w_index} wLength={setup.w_length}"
        )

        assert self.connected_device is not None
        # Forward to device
        result = self.connected_device.handle_setup_packet(setup_packet)

        # Handle SET_ADDRESS specially
        if setup.b_request == StandardRequest.SET_ADDRESS and setup.type == 0:
            new_addr = setup.w_value & 0x7F
            self.connected_device.address = new_addr
            on_address_assigned = getattr(self.connected_device, "on_address_assigned", None)
            if on_address_assigned:
                on_address_assigned(new_addr)
            self.debug(f"Device address set to {new_addr}")

        # Store response data for subsequent IN transaction
        if setup.direction == "in" and result.data:
            self._pending_setup_response = result.data
            self._expecting_status_phase = False
        else:
            self._pending_setup_response = None
            # Control OUT transfer - next IN will be status phase (zero-length ACK)
            self._expecting_status_phase = True

        # Reset data toggle for data phase
        self._control_data_pid = 1

        # SETUP always gets ACK (or STALL if error, but we handle that later)
        self._sie_status |= SIE_ACK_REC

        # Schedule transaction completion
        self.host_transaction_alarm.schedule(self.host_transaction_delay_microseconds * 1000)

    def _handle_in_transaction(self, dev_addr: int, ep_num: int) -> None:
        ep_addr = 0x80 | ep_num
        assert self.connected_device is not None

        if ep_num == 0 and self._expecting_status_phase:
            # Control OUT status phase - return zero-length ACK
            result = USBTransferResult(status="ack", data=b"")
            self._expecting_status_phase = False
            self.debug("Control OUT status phase (ZLP)")
        elif ep_num == 0 and self._pending_setup_response:
            # Control IN - return pending setup response
            result = USBTransferResult(status="ack", data=self._pending_setup_response)

            # Get requested length from buffer control
            buf_ctrl = _get_uint32_le(self.rp2040.usb_dpram, HOST_EPX_BUF_CTRL)
            max_len = buf_ctrl & USB_BUF_CTRL_LEN_MASK

            # Trim data to requested length
            if result.data and len(result.data) > max_len:
                result.data = result.data[:max_len]

            # Clear pending response if all data sent
            if not result.data or len(result.data) <= max_len:
                self._pending_setup_response = None
        else:
            result = self.connected_device.handle_data_in(ep_addr)

        if result.status == "ack" and result.data:
            # Write data to EPX data buffer
            self.rp2040.usb_dpram[HOST_EPX_DATA : HOST_EPX_DATA + len(result.data)] = result.data

            # Update buffer control with actual length and FULL flag
            buf_ctrl = _get_uint32_le(self.rp2040.usb_dpram, HOST_EPX_BUF_CTRL)
            buf_ctrl &= ~USB_BUF_CTRL_LEN_MASK
            buf_ctrl |= len(result.data) & USB_BUF_CTRL_LEN_MASK
            buf_ctrl |= USB_BUF_CTRL_FULL
            buf_ctrl &= ~USB_BUF_CTRL_AVAILABLE

            # Set DATA1 PID for control transfers
            if self._control_data_pid:
                buf_ctrl |= USB_BUF_CTRL_DATA1_PID
            else:
                buf_ctrl &= ~USB_BUF_CTRL_DATA1_PID
            self._control_data_pid ^= 1

            _set_uint32_le(self.rp2040.usb_dpram, HOST_EPX_BUF_CTRL, buf_ctrl)

            # Set buffer status
            self._buff_status |= 1  # Bit 0 for EPX
            self._buff_status_updated()

            self._sie_status |= SIE_ACK_REC
        elif result.status == "nak":
            self._sie_status |= SIE_NAK_REC
        elif result.status == "stall":
            self._sie_status |= SIE_STALL_REC

        self.host_transaction_alarm.schedule(self.host_transaction_delay_microseconds * 1000)

    def _handle_out_transaction(self, dev_addr: int, ep_num: int) -> None:
        ep_addr = ep_num  # OUT endpoint
        assert self.connected_device is not None

        # Read data from EPX data buffer
        buf_ctrl = _get_uint32_le(self.rp2040.usb_dpram, HOST_EPX_BUF_CTRL)
        data_len = buf_ctrl & USB_BUF_CTRL_LEN_MASK
        data = bytes(self.rp2040.usb_dpram[HOST_EPX_DATA : HOST_EPX_DATA + data_len])

        if ep_num == 0 and data_len == 0:
            # Zero-length status phase for control transfer
            result = USBTransferResult(status="ack")
        else:
            result = self.connected_device.handle_data_out(ep_addr, data)

        # Update buffer control
        new_buf_ctrl = buf_ctrl
        new_buf_ctrl &= ~USB_BUF_CTRL_AVAILABLE

        # Toggle DATA PID
        if self._control_data_pid:
            new_buf_ctrl |= USB_BUF_CTRL_DATA1_PID
        else:
            new_buf_ctrl &= ~USB_BUF_CTRL_DATA1_PID
        self._control_data_pid ^= 1

        _set_uint32_le(self.rp2040.usb_dpram, HOST_EPX_BUF_CTRL, new_buf_ctrl)

        if result.status == "ack":
            self._sie_status |= SIE_ACK_REC
            self._buff_status |= 1  # Bit 0 for EPX
            self._buff_status_updated()
        elif result.status == "nak":
            self._sie_status |= SIE_NAK_REC
        elif result.status == "stall":
            self._sie_status |= SIE_STALL_REC

        self.host_transaction_alarm.schedule(self.host_transaction_delay_microseconds * 1000)

    def _complete_host_transaction(self) -> None:
        # Clear START_TRANS bit
        self._sie_ctrl &= ~SIE_CTRL_START_TRANS

        # Set transaction complete
        self._sie_status |= SIE_TRANS_COMPLETE
        self._sie_status_updated()

        self.debug("Host transaction complete")
