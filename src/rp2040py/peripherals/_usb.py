from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from rp2040py.clock.clock import IAlarm
from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.usb.usb_device import StandardRequest, USBDevice, USBTransferResult, parse_setup_packet

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPUSBController",)

ENDPOINT_COUNT = 16
USB_HOST_INTERRUPT_ENDPOINTS = 15
FIFO_DEPTH = 64  # completed buffers per (endpoint, direction), and OUT starts per endpoint, the model remembers: a bound the C++ block needs (static storage); the hardware has one bit

# USB DPSRAM Registers - Device mode
EP1_IN_CONTROL = 0x8
EP1_OUT_CONTROL = 0xC
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

EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER = 1 << 28
EP_CTRL_TYPE_SHIFT = 26
EP_CTRL_TYPE_ISOCHRONOUS = 1
EP_CTRL_BUFFER_ADDRESS_MASK = 0xFFC0  # bits 5:0 are ignored (the buffer is 64-byte aligned)
EP0_IN_DATA_BUFFER = 0x100  # EP0 has no endpoint control register: its buffer(s) sit here, shared by both directions

# Buffer Control bits (one 16-bit half per buffer; buffer 1's are the same bits shifted by USB_BUF1_SHIFT)
USB_BUF_CTRL_AVAILABLE = 1 << 10
USB_BUF_CTRL_STALL = 1 << 11
USB_BUF_CTRL_RESET_SELECT = 1 << 12  # device only: buffer select back to buffer 0, cleared with the transfer
USB_BUF_CTRL_FULL = 1 << 15
USB_BUF_CTRL_LEN_MASK = 0x3FF
USB_BUF_CTRL_DATA1_PID = 1 << 13
USB_BUF_CTRL_LAST = 1 << 14
USB_BUF_CTRL_STATUS_KEEP = (
    USB_BUF_CTRL_LEN_MASK | USB_BUF_CTRL_DATA1_PID | USB_BUF_CTRL_LAST
)  # what the controller leaves in a half when it writes a transfer's status back
USB_BUF_CTRL_ISO_OFFSET_SHIFT = (
    27  # bits 28:27 of the word: buffer 1's offset in an isochronous endpoint (0..3 = 128, 256, 512, 1024)
)
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

# USB_PWR bits (datasheet table 414, pico-sdk hardware/regs/usb.h)
PWR_VBUS_EN = 1 << 0
PWR_VBUS_EN_OVERRIDE_EN = 1 << 1
PWR_VBUS_DETECT = 1 << 2
PWR_VBUS_DETECT_OVERRIDE_EN = 1 << 3
PWR_OVERCURR_DETECT = 1 << 4
PWR_OVERCURR_DETECT_EN = 1 << 5
PWR_MASK = 0x3F

# register write masks (the datasheet's register tables) and reset values
ADDR_ENDP_MASK = 0x000F007F
ADDR_ENDPN_MASK = 0x060F007F
SIE_CTRL_MASK = 0xFF07BF5F
INT_EP_CTRL_MASK = 0x0000FFFE
EP_STALL_ARM_MASK = 0x3
NAK_POLL_MASK = 0x03FF03FF
NAK_POLL_RESET = 0x00100010
USB_MUXING_MASK = 0xF
USBPHY_DIRECT_MASK = 0x0000FF77  # 22:16 (the RX / over-voltage / over-current pins) are read-only, 7 and 3 reserved
USBPHY_DIRECT_OVERRIDE_MASK = 0x00009FFF
USBPHY_TRIM_MASK = 0x00001F1F
USBPHY_TRIM_RESET = 0x00001F1F
INT_MASK = 0x000FFFFF
SOF_COUNT_MASK = 0x7FF

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
    | SIE_SUSPENDED
)
SIE_SPEED_MASK = (
    0x3 << 8
)  # WC as well, but a LEVEL (the speed of the attached device): writing it acknowledges the connect event, the speed stays (TinyUSB reads it after clearing)


class USBEndpointAlarm:
    """One endpoint direction's pending transfers: the buffers (and which half of the endpoint's buffer-control word each came from) that wait for the alarm."""

    def __init__(self, alarm: IAlarm):
        self.alarm = alarm
        self.buffers: list[bytes | bytearray] = []
        self.halves: list[int] = []

    def schedule(self, buffer: bytes | bytearray, delay_nanos: int, half: int = 0) -> None:
        self.buffers.append(buffer)
        self.halves.append(half)
        self.alarm.schedule(delay_nanos)

    def clear(self) -> None:
        self.buffers = []
        self.halves = []


def _get_uint32_le(buffer: bytearray, offset: int) -> int:
    return int.from_bytes(buffer[offset : offset + 4], "little")


def _set_uint32_le(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")


class RPUSBController(BasePeripheral):
    """The RP2040 USB controller (datasheet 4.1): the registers, the DPRAM semantics and the interrupts. The other end of the bus is abstracted (docs/tasks/usb-port.md, "What the model is"):
    in device mode `usb/cdc.py` acts as the host and a buffer is delivered the moment it is AVAILABLE; in host mode a Python `USBDevice` answers. Not modelled (each documented there): STALL / NAK
    handshakes and EP_STATUS_STALL_NAK, EP_ABORT (done at once), device SOF, suspend and resume, erratum RP2040-E4, the B0/B1 enumeration fix."""

    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)

        # Common registers
        self._addr_endp = 0
        self._main_ctrl = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._sie_status = 0
        self._connected = False
        self._connect_event = False
        self._host_connect_event = False

        # Buffer status: a bit of BUFF_STATUS is set while the endpoint direction has completed buffers the CPU has not handled; the FIFO holds which buffer (0/1) each is, oldest first, and
        # BUFF_CPU_SHOULD_HANDLE shows the oldest ("the cleared BUFF_STATUS bit will be set again, and BUFF_CPU_SHOULD_HANDLE will change", 4.1.2.8.2)
        self._buff_done: list[list[int]] = [[] for _ in range(ENDPOINT_COUNT * 2)]

        # Device mode: the controller's internal buffer select and the "every two buffers" counter, per endpoint and direction (index endpoint * 2 + out), and the halves of the OUT
        # buffers the host was asked to fill, oldest first
        self._buf_select = [0] * (ENDPOINT_COUNT * 2)
        self._two_buffer_count = [0] * (ENDPOINT_COUNT * 2)
        self._pending_reads: list[list[int]] = [[] for _ in range(ENDPOINT_COUNT)]

        # Host mode registers
        self._sie_ctrl = 0
        self._sof_frame_number = 0
        self._int_ep_addr_ctrl = [0] * USB_HOST_INTERRUPT_ENDPOINTS
        self._int_ep_ctrl = 0  # Interrupt endpoint control (enable bits)
        self._usb_pwr = 0
        self._usb_muxing = 0
        self._usbphy_direct = 0
        self._usbphy_direct_override = 0
        self._usbphy_trim = USBPHY_TRIM_RESET
        self._nak_poll = NAK_POLL_RESET
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
        # Fired by `RP2040.enter_reset()`, on every chip-level reset. It is what lets the *chip*
        # own a reset without knowing what is attached to its USB: `USBCDC` installs its own
        # state-clearing here (docs/records/0057-run-pin-reset-hook.md's option B), instead of the
        # device layer having to remember to reset both halves in the right order.
        self.on_reset: Callable[[], None] | None = None
        self.on_endpoint_write: Callable[[int, bytes | bytearray], None] | None = None
        self.on_endpoint_read: Callable[[int, int], None] | None = None

        self.read_delay_microseconds = 10
        self.write_delay_microseconds = 10  # Determined empirically
        # The host's reaction to an acknowledged IN buffer comes this long after the ACK, which is when the status is written back and BUFF_STATUS raised. On the wire the next thing the host does (the
        # OUT status stage of a control read, then the next SETUP) takes a transaction or more; the shortcut host (usb/cdc.py) reacts inside its callback, and without a gap the firmware sees SETUP_REQ and the
        # BUFF_STATUS of the transfer before it in one interrupt - which the TinyUSB of pico-sdk 1.2.0-1.4.0 handles in that order and corrupts EP0 (found by CI, record 0098, USB). Not independently
        # sourced: it is the gap the model had before, when BUFF_STATUS was raised at the start of the transfer.
        self.host_reaction_microseconds = 10
        self.host_transaction_delay_microseconds = 5  # Host transaction delay

        clock = rp2040.clock
        self.endpoint_read_alarms: list[USBEndpointAlarm] = []
        self.endpoint_write_alarms: list[USBEndpointAlarm] = []
        self.endpoint_reaction_alarms: list[USBEndpointAlarm] = []
        for i in range(ENDPOINT_COUNT):
            self.endpoint_read_alarms.append(USBEndpointAlarm(clock.create_alarm(self._make_read_alarm(i))))
            self.endpoint_write_alarms.append(USBEndpointAlarm(clock.create_alarm(self._make_write_alarm(i))))
            self.endpoint_reaction_alarms.append(USBEndpointAlarm(clock.create_alarm(self._make_reaction_alarm(i))))

        self.reset_alarm = clock.create_alarm(self._on_reset_alarm)

        # Host mode alarms
        self.sof_alarm = clock.create_alarm(self._generate_sof)
        self.host_transaction_alarm = clock.create_alarm(self._complete_host_transaction)

    # the device address and the host's target share one register
    @property
    def _dev_addr_ctrl(self) -> int:
        return self._addr_endp

    @_dev_addr_ctrl.setter
    def _dev_addr_ctrl(self, value: int) -> None:
        self._addr_endp = value & ADDR_ENDP_MASK

    @property
    def _buff_status(self) -> int:
        status = 0
        for index, done in enumerate(self._buff_done):
            if done:
                status |= 1 << index
        return status

    def _push_done(self, slot: int, half: int) -> None:
        """A buffer completed and the CPU has not handled it: remembered (at most FIFO_DEPTH of them) until BUFF_STATUS is written."""
        if len(self._buff_done[slot]) < FIFO_DEPTH:
            self._buff_done[slot].append(half)

    def _push_pending(self, endpoint: int, half: int) -> None:
        """An OUT buffer was handed to the controller and waits for the host's data (at most FIFO_DEPTH per endpoint)."""
        if len(self._pending_reads[endpoint]) < FIFO_DEPTH:
            self._pending_reads[endpoint].append(half)

    def _make_read_alarm(self, index: int) -> Callable[[], None]:
        def _on_alarm() -> None:
            alarm = self.endpoint_read_alarms[index]
            buffers = alarm.buffers
            alarm.clear()
            for buffer in buffers:  # every answer that arrived before the alarm ran
                self._finish_read(index, buffer)

        return _on_alarm

    def _make_write_alarm(self, index: int) -> Callable[[], None]:
        def _on_alarm() -> None:
            alarm = self.endpoint_write_alarms[index]
            buffers, halves = alarm.buffers, alarm.halves
            alarm.clear()
            for buffer, half in zip(buffers, halves, strict=True):
                self._finish_write(index, buffer, half)

        return _on_alarm

    def _make_reaction_alarm(self, index: int) -> Callable[[], None]:
        def _on_alarm() -> None:
            alarm = self.endpoint_reaction_alarms[index]
            buffers = alarm.buffers
            alarm.clear()
            for buffer in buffers:  # the host sees every buffer that was acknowledged before the alarm ran
                if self.on_endpoint_write:
                    self.on_endpoint_write(index, buffer)

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
            return self._addr_endp
        if offset == MAIN_CTRL:
            return self._main_ctrl
        if offset == SOF_WR:
            return 0  # Write-only
        if offset == SOF_RD:
            # reading it acknowledges the SOF interrupts (datasheet INTR.DEV_SOF / HOST_SOF)
            if self._int_raw & (INTR_DEV_SOF | INTR_HOST_SOF):
                self._int_raw &= ~(INTR_DEV_SOF | INTR_HOST_SOF)
                self.check_interrupts()
            return self._sof_frame_number & SOF_COUNT_MASK
        if offset == SIE_CTRL:
            return self._sie_ctrl
        if offset == SIE_STATUS:
            return self._sie_status
        if offset == INT_EP_CTRL:
            return self._int_ep_ctrl
        if offset == BUFF_STATUS:
            return self._buff_status
        if offset == BUFF_CPU_SHOULD_HANDLE:
            should_handle = 0
            for index, done in enumerate(self._buff_done):
                if done and done[0]:
                    should_handle |= 1 << index
            return should_handle
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
        if offset == USB_MUXING:
            return self._usb_muxing
        if offset == USB_PWR:
            return self._usb_pwr
        if offset == USBPHY_DIRECT:
            return self._usbphy_direct
        if offset == USBPHY_DIRECT_OVERRIDE:
            return self._usbphy_direct_override
        if offset == USBPHY_TRIM:
            return self._usbphy_trim
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
            self._int_ep_addr_ctrl[ep_index] = value & ADDR_ENDPN_MASK
            return

        if offset == ADDR_ENDP:
            self._addr_endp = value & ADDR_ENDP_MASK
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
            self._sof_frame_number = value & SOF_COUNT_MASK
        elif offset == SIE_CTRL:
            self._handle_sie_ctrl_write(value & SIE_CTRL_MASK)
        elif offset == INT_EP_CTRL:
            self._int_ep_ctrl = value & INT_EP_CTRL_MASK
        elif offset == BUFF_STATUS:
            # write 1 to clear: the oldest completed buffer of the endpoint is handled; the bit comes back at once if another one is waiting
            for index in range(ENDPOINT_COUNT * 2):
                if self.raw_write_value & (1 << index) and self._buff_done[index]:
                    self._buff_done[index].pop(0)
            self._buff_status_updated()
        elif offset == EP_ABORT:
            self._ep_abort = value
            # Immediately mark as done
            self._ep_abort_done |= value
            self._abort_done_updated()
        elif offset == EP_ABORT_DONE:
            self._ep_abort_done &= ~self.raw_write_value
            self._abort_done_updated()
        elif offset == EP_STALL_ARM:
            self._ep_stall_arm = value & EP_STALL_ARM_MASK
        elif offset == NAK_POLL:
            self._nak_poll = value & NAK_POLL_MASK
        elif offset == EP_STATUS_STALL_NAK:
            self._ep_status_stall_nak &= ~self.raw_write_value
            self._stall_nak_updated()
        elif offset == USB_MUXING:
            self._usb_muxing = value & USB_MUXING_MASK
            # Workaround for busy wait in hw_enumeration_fix_force_ls_j() / hw_enumeration_fix_finish():
            if value & TO_DIGITAL_PAD and not (value & TO_PHY):
                self._set_connected(True)
        elif offset == USB_PWR:
            self._usb_pwr = value & PWR_MASK
            # the override values stand in for the VBUS pins that are not wired
            if self._usb_pwr & PWR_VBUS_DETECT_OVERRIDE_EN and self._usb_pwr & PWR_VBUS_DETECT:
                self._sie_status |= SIE_VBUS_DETECTED
            else:
                self._sie_status &= ~SIE_VBUS_DETECTED
            if self._usb_pwr & PWR_OVERCURR_DETECT_EN and self._usb_pwr & PWR_OVERCURR_DETECT:
                self._sie_status |= SIE_VBUS_OVER_CURR
            else:
                self._sie_status &= ~SIE_VBUS_OVER_CURR
            self._sie_status_updated()
        elif offset == USBPHY_DIRECT:
            self._usbphy_direct = value & USBPHY_DIRECT_MASK
        elif offset == USBPHY_DIRECT_OVERRIDE:
            self._usbphy_direct_override = value & USBPHY_DIRECT_OVERRIDE_MASK
        elif offset == USBPHY_TRIM:
            self._usbphy_trim = value & USBPHY_TRIM_MASK
        elif offset == SIE_STATUS:
            self._sie_status &= ~(self.raw_write_value & SIE_WRITECLEAR_MASK)
            if self.raw_write_value & SIE_CONNECTED:
                # Write-1-to-clear acknowledges the connect/disconnect *event*. It does not
                # unplug anything - see `_set_connected()`.
                self._connect_event = False
            if self.raw_write_value & SIE_SPEED_MASK:
                # likewise the host's connect/disconnect event; SPEED itself is the attached device's speed
                self._host_connect_event = False
            if self.raw_write_value & SIE_BUS_RESET:
                if not self._host_mode and self.on_reset_received:
                    self.on_reset_received()
                self._sie_status &= ~(SIE_LINE_STATE_MASK << SIE_LINE_STATE_SHIFT)
                self._sie_status |= SIELineState.J << SIE_LINE_STATE_SHIFT
                self._set_connected(True)
            elif self._connected:
                self._sie_status |= SIE_CONNECTED
            self._sie_status_updated()
        elif offset == INTE:
            self._int_enable = value & INT_MASK
            self.check_interrupts()
        elif offset == INTF:
            self._int_force = value & INT_MASK
            self.check_interrupts()
        else:
            super().write_uint32(offset, value)

    # ============ Device mode: the DPRAM ============

    def _read_endpoint_control_reg(self, endpoint: int, out: bool) -> int:
        if endpoint == 0:
            return 0  # EP0 has no endpoint control register: SIE_CTRL carries its interrupt and double-buffer bits
        control_reg_offset = EP1_IN_CONTROL + 8 * (endpoint - 1) + (4 if out else 0)
        return _get_uint32_le(self.rp2040.usb_dpram, control_reg_offset)

    def _get_endpoint_buffer_offset(self, endpoint: int, out: bool) -> int:
        if endpoint == 0:
            return EP0_IN_DATA_BUFFER
        return self._read_endpoint_control_reg(endpoint, out) & EP_CTRL_BUFFER_ADDRESS_MASK

    def _is_double_buffered(self, endpoint: int, out: bool) -> bool:
        if endpoint == 0:
            return bool(self._sie_ctrl & SIE_CTRL_EP0_DOUBLE_BUF)
        return bool(self._read_endpoint_control_reg(endpoint, out) & USB_CTRL_DOUBLE_BUF)

    @staticmethod
    def _half_state(word: int, half: int) -> int:
        """The 16 bits of a buffer-control word that belong to buffer `half`. Buffer 1 has no STALL or reset-select bit: in its half those two positions are the isochronous offset (table 396)."""
        state = (word >> (USB_BUF1_SHIFT * half)) & 0xFFFF
        if half:
            state &= ~(USB_BUF_CTRL_STALL | USB_BUF_CTRL_RESET_SELECT)
        return state

    def _store(self, address: int, data: "bytes | bytearray") -> int:
        """Writes `data` into the DPRAM at `address`, cut off at the end of the 4 kB (a buffer base can be up to 64 kB - 64 in the endpoint control word, and a length is up to 1023): the hardware's
        behaviour past the end is not sourced; what matters is that the memory never changes size. Returns the number of bytes stored."""
        dpram = self.rp2040.usb_dpram
        count = max(0, min(len(data), len(dpram) - address))
        dpram[address : address + count] = data[:count]
        return count

    def _buffer_address(self, endpoint: int, out: bool, half: int, buffer_control: int) -> int:
        """Where buffer `half` of the endpoint sits in the DPRAM: 64 bytes after buffer 0, or - on an isochronous endpoint - the offset the buffer-control word names."""
        base = self._get_endpoint_buffer_offset(endpoint, out)
        if half == 0:
            return base
        control = self._read_endpoint_control_reg(endpoint, out)
        if endpoint != 0 and ((control >> EP_CTRL_TYPE_SHIFT) & 0x3) == EP_CTRL_TYPE_ISOCHRONOUS:
            return base + (128 << ((buffer_control >> USB_BUF_CTRL_ISO_OFFSET_SHIFT) & 0x3))
        return base + USB_BUF1_OFFSET

    def dpram_updated(self, offset: int, value: int) -> None:
        # Skip device-mode buffer control handling in host mode
        if self._host_mode:
            return
        if EP0_IN_BUFFER_CONTROL <= offset <= EP15_OUT_BUFFER_CONTROL:
            endpoint = (offset - EP0_IN_BUFFER_CONTROL) >> 3
            out = bool(offset & 4)
            if _get_uint32_le(self.rp2040.usb_dpram, offset) & USB_BUF_CTRL_RESET_SELECT:
                self._buf_select[endpoint * 2 + out] = 0
            self._service_endpoint(endpoint, out)

    def _service_endpoint(self, endpoint: int, out: bool) -> None:
        """Start the transfer of every buffer the firmware has made AVAILABLE, in the order the controller takes them (buffer 0, then buffer 1 of a double-buffered endpoint).

        A buffer moves only if the token would be answered with data: an IN needs FULL as well as AVAILABLE, an OUT needs FULL clear (4.1.2.8.2/3); otherwise the host would get a NAK and the
        buffer stays as the firmware left it. A STALL bit does the same (the STALL handshake itself is not modelled). The controller owns a buffer from here: AVAILABLE is cleared at once, the
        status is written back when the transfer is done (`_finish_write` / `_finish_read`)."""
        offset = EP0_IN_BUFFER_CONTROL + endpoint * 8 + (4 if out else 0)
        slot = endpoint * 2 + out
        double = self._is_double_buffered(endpoint, out)
        for _ in range(2 if double else 1):
            word = _get_uint32_le(self.rp2040.usb_dpram, offset)
            half = self._buf_select[slot] if double else 0
            state = self._half_state(word, half)
            if not state & USB_BUF_CTRL_AVAILABLE or state & USB_BUF_CTRL_STALL:
                return
            if bool(state & USB_BUF_CTRL_FULL) == out:
                return
            length = state & USB_BUF_CTRL_LEN_MASK
            address = self._buffer_address(endpoint, out, half, word)
            self.debug(
                f"Start USB transfer, endPoint={endpoint}, direction={'out' if out else 'in'} buffer={address:x} length={length}"
            )
            word &= ~(USB_BUF_CTRL_AVAILABLE << (USB_BUF1_SHIFT * half))
            _set_uint32_le(self.rp2040.usb_dpram, offset, word)
            if double:
                self._buf_select[slot] ^= 1
            if out:
                self._push_pending(endpoint, half)
                if self.on_endpoint_read:
                    self.on_endpoint_read(endpoint, length)
            else:
                buffer = bytes(self.rp2040.usb_dpram[address : address + length])
                self.endpoint_write_alarms[endpoint].schedule(buffer, self.write_delay_microseconds * 1000, half)

    def _finish_write(self, endpoint: int, buffer: bytes | bytearray, half: int) -> None:
        """An IN buffer reached the host and was acknowledged: the status phase (4.1.2.8.2) now, the host's reaction `host_reaction_microseconds` later."""
        self._buffer_done(endpoint, False, half, 0)
        self.endpoint_reaction_alarms[endpoint].schedule(buffer, self.host_reaction_microseconds * 1000)

    def endpoint_read_done(self, endpoint: int, buffer: bytes | bytearray, delay: int | None = None) -> None:
        if delay is None:
            delay = self.read_delay_microseconds
        self.endpoint_read_alarms[endpoint].schedule(buffer, delay * 1000)

    def _finish_read(self, endpoint: int, buffer: bytes | bytearray) -> None:
        """The host's data for an OUT buffer arrives: it is stored in the buffer the controller was waiting to fill, then the status phase (4.1.2.8.3)."""
        pending = self._pending_reads[endpoint]
        half = pending.pop(0) if pending else 0
        offset = EP0_OUT_BUFFER_CONTROL + endpoint * 8
        word = _get_uint32_le(self.rp2040.usb_dpram, offset)
        state = self._half_state(word, half)
        length = min(len(buffer), state & USB_BUF_CTRL_LEN_MASK)
        address = self._buffer_address(endpoint, True, half, word)
        length = self._store(address, buffer[:length])
        self._buffer_done(endpoint, True, half, length)

    def _buffer_done(self, endpoint: int, out: bool, half: int, received: int) -> None:
        """The status phase of a transfer: the controller writes the buffer's half of the buffer-control word back as length, pid and last-buffer, everything else zero (FULL set for an OUT, which
        has filled the buffer), raises TRANS_COMPLETE for a last buffer, and BUFF_STATUS if the endpoint's interrupt enable asks for it."""
        offset = EP0_IN_BUFFER_CONTROL + endpoint * 8 + (4 if out else 0)
        word = _get_uint32_le(self.rp2040.usb_dpram, offset)
        shift = USB_BUF1_SHIFT * half
        state = self._half_state(word, half)
        length = received if out else state & USB_BUF_CTRL_LEN_MASK
        done = (
            (length & USB_BUF_CTRL_LEN_MASK)
            | (state & (USB_BUF_CTRL_DATA1_PID | USB_BUF_CTRL_LAST))
            | (USB_BUF_CTRL_FULL if out else 0)
        )
        word = (word & ~(0xFFFF << shift)) | (done << shift)
        _set_uint32_le(self.rp2040.usb_dpram, offset, word)
        if state & USB_BUF_CTRL_LAST:
            self._sie_status |= SIE_TRANS_COMPLETE
        if not out:
            self._sie_status |= SIE_ACK_REC
        self._sie_status_updated()

        slot = endpoint * 2 + out
        if endpoint == 0:
            per_buffer = bool(self._sie_ctrl & SIE_CTRL_EP0_INT_1BUF)
            per_two = bool(self._sie_ctrl & SIE_CTRL_EP0_INT_2BUF)
        else:
            control = self._read_endpoint_control_reg(endpoint, out)
            per_buffer = bool(control & USB_CTRL_INTERRUPT_PER_TRANSFER)
            per_two = bool(control & EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER)
        if per_buffer:
            self._push_done(slot, half)
        elif per_two and self._is_double_buffered(endpoint, out):
            self._two_buffer_count[slot] += 1
            if self._two_buffer_count[slot] == 2:
                self._two_buffer_count[slot] = 0
                self._push_done(slot, half)
        self._buff_status_updated()
        # the other buffer of a double-buffered endpoint may already be waiting
        self._service_endpoint(endpoint, out)

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(IRQ.USBCTRL, bool(self.int_status))

    def reset(self) -> None:
        """Callbacks (on_usb_enabled etc.), the alarm objects themselves, and read/write_delay
        tuning are left alone - only the register/transaction state a real RESETS-block reset
        would clear. This controller object survives the reset in place rather than being
        reconstructed, unlike a fresh boot where a new one is simply constructed instead.

        Does **not** fire `on_reset` - `RP2040.enter_reset()` does, unconditionally. Deliberate:
        this block is only reset when `RESETS.WDSEL` selects it, but a chip reset drops the device
        off the host's bus either way, and that guarantee (0089's `hard_reset()`, which
        `ahard_reset()` waits on) must not become conditional on what a guest wrote to WDSEL."""
        self._addr_endp = 0
        self._main_ctrl = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._sie_status = 0
        self._connected = False
        self._connect_event = False
        self._host_connect_event = False
        for done in self._buff_done:
            done.clear()
        for slot in range(ENDPOINT_COUNT * 2):
            self._buf_select[slot] = 0
            self._two_buffer_count[slot] = 0
        for pending in self._pending_reads:
            pending.clear()

        self._sie_ctrl = 0
        self._sof_frame_number = 0
        for i in range(len(self._int_ep_addr_ctrl)):
            self._int_ep_addr_ctrl[i] = 0
        self._int_ep_ctrl = 0
        self._usb_pwr = 0
        self._usb_muxing = 0
        self._usbphy_direct = 0
        self._usbphy_direct_override = 0
        self._usbphy_trim = USBPHY_TRIM_RESET
        self._nak_poll = NAK_POLL_RESET
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
        for endpoint_alarm in (*self.endpoint_read_alarms, *self.endpoint_write_alarms, *self.endpoint_reaction_alarms):
            endpoint_alarm.alarm.cancel()
            endpoint_alarm.clear()

        self.check_interrupts()

    def reset_device(self) -> None:
        self.reset_alarm.schedule(10_000_000)  # USB reset takes ~10ms

    def send_setup_packet(self, setup_packet: bytes | bytearray) -> None:
        self.rp2040.usb_dpram[: len(setup_packet)] = setup_packet
        self._ep_stall_arm = 0  # a SETUP clears any STALL on EP0, so the arm bits go with it (4.1.2.8.1)
        self._sie_status |= SIE_SETUP_REC
        self._sie_status_updated()

    def _set_connected(self, connected: bool) -> None:
        """`SIE_STATUS.CONNECTED` is a **level**, not a latch: it says a host is on the other end
        of the bus, so it comes straight back after a write-1-to-clear for as long as that stays
        true. The *event* is what write-1-to-clear acknowledges, and that is what
        `INTR_DEV_CONN_DIS` reports.

        Modelling it as a pure latch deadlocked every second boot: pico-sdk's own
        `rp2040_usb_device_enumeration_fix()` busy-waits on this bit
        (`while (!(usb_hw->sie_status & USB_SIE_STATUS_CONNECTED_BITS));`, inside a timer alarm),
        and TinyUSB's ISR clears the connect status a moment earlier. On a first boot the two
        happen to race the right way round; after a chip reset they do not, and the firmware sat
        in that loop forever with the emulator's clock running - the shape
        docs/records/0089-one-reset-for-every-trigger.md's Phase 2 live check hit on MicroPython
        1.16/1.17.
        """
        changed = connected != self._connected
        if changed:
            self._connect_event = True
        self._connected = connected
        if connected:
            self._sie_status |= SIE_CONNECTED
        else:
            self._sie_status &= ~SIE_CONNECTED
        if changed:
            # Connecting *is* the event, so the interrupt is raised here rather than waiting for
            # whatever else happens to refresh the status next.
            self._sie_status_updated()

    def _buff_status_updated(self) -> None:
        if any(self._buff_done):
            self._int_raw |= INTR_BUFF_STATUS
        else:
            self._int_raw &= ~INTR_BUFF_STATUS
        self.check_interrupts()

    def _abort_done_updated(self) -> None:
        if self._ep_abort_done:
            self._int_raw |= INTR_ABORT_DONE
        else:
            self._int_raw &= ~INTR_ABORT_DONE
        self.check_interrupts()

    def _stall_nak_updated(self) -> None:
        if self._ep_status_stall_nak:
            self._int_raw |= INTR_EP_STALL_NAK
        else:
            self._int_raw &= ~INTR_EP_STALL_NAK
        self.check_interrupts()

    def _sie_status_updated(self) -> None:
        # What each interrupt is the raw state of: a SIE_STATUS bit (level: the interrupt follows it), or an event the register's write-1-to-clear acknowledges. The resume interrupt is the
        # device's or the host's, by mode.
        int_register_map = [
            (SIE_SETUP_REC, INTR_SETUP_REQ),
            (SIE_RESUME, INTR_HOST_RESUME if self._host_mode else INTR_DEV_RESUME_FROM_HOST),
            (SIE_SUSPENDED, INTR_DEV_SUSPEND),
            (SIE_BUS_RESET, INTR_BUS_RESET),
            (SIE_VBUS_DETECTED, INTR_VBUS_DETECT),
            (SIE_TRANS_COMPLETE, INTR_TRANS_COMPLETE),
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
        # The connect/disconnect interrupts are the *edge*, which is why they are not in the table above: the status bit is a level that survives being written to (`_set_connected()`),
        # so deriving the interrupt from it would re-raise forever.
        if self._connect_event:
            self._int_raw |= INTR_DEV_CONN_DIS
        else:
            self._int_raw &= ~INTR_DEV_CONN_DIS
        if self._host_connect_event:
            self._int_raw |= INTR_HOST_CONN_DIS
        else:
            self._int_raw &= ~INTR_HOST_CONN_DIS
        self.check_interrupts()

    # ============ Host Mode Methods ============

    def snapshot(self) -> dict[str, Any]:
        """The controller's state as plain values: the interrupt words, the SIE, the mode, the buffer bookkeeping (done, select, two-buffer counts, OUT requests in flight) and the host-mode control transfer in progress."""
        return {
            "int_raw": self._int_raw,
            "int_enable": self._int_enable,
            "int_force": self._int_force,
            "int_status": self.int_status,
            "sie_status": self._sie_status,
            "sie_ctrl": self._sie_ctrl,
            "connected": bool(self._connected),
            "connect_event": bool(self._connect_event),
            "host_connect_event": bool(self._host_connect_event),
            "host_mode": bool(self._host_mode),
            "sof_enabled": bool(self._sof_enabled),
            "device_attached": self.connected_device is not None,
            "sof_frame_number": self._sof_frame_number,
            "ep_status_stall_nak": self._ep_status_stall_nak,
            "buff_done": tuple(tuple(done) for done in self._buff_done),
            "buf_select": tuple(self._buf_select),
            "two_buffer_count": tuple(self._two_buffer_count),
            "pending_reads": tuple(tuple(pending) for pending in self._pending_reads),
            "expecting_status_phase": bool(self._expecting_status_phase),
            "control_data_pid": self._control_data_pid,
            "pending_setup_response": bytes(self._pending_setup_response or b""),
            "device_address": None if self.connected_device is None else self.connected_device.address,
        }

    def raise_sie_status(self, mask: int) -> None:
        """Raise SIE_STATUS bits no modelled event sets (suspend, resume, the error flags): what a token-level host would do."""
        self._sie_status |= mask
        self._sie_status_updated()

    def raise_stall_nak(self, mask: int) -> None:
        """Raise EP_STATUS_STALL_NAK bits, which only a handshake from the host sets."""
        self._ep_status_stall_nak |= mask
        self._stall_nak_updated()

    def raise_interrupt(self, mask: int) -> None:
        """Raise INTR bits for which the model has no event (the SOF interrupts need a host that sends start-of-frame tokens)."""
        self._int_raw |= mask
        self.check_interrupts()

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
            self._sie_status &= ~SIE_SPEED_MASK
            self._host_connect_event = True
            self._sie_status_updated()
        self.connected_device = None

    def _on_device_connected(self) -> None:
        # Set full-speed device connected (value 2 in speed field, bits 9:8)
        self._sie_status &= ~SIE_SPEED_MASK
        self._sie_status |= SIE_SPEED_FS_VALUE << 8
        self._host_connect_event = True
        self._sie_status_updated()
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
                    self._store(buffer_offset, result.data)

                    # Update buffer control
                    new_buf_ctrl = buf_ctrl & ~USB_BUF_CTRL_AVAILABLE
                    new_buf_ctrl |= USB_BUF_CTRL_FULL
                    new_buf_ctrl = (new_buf_ctrl & ~USB_BUF_CTRL_LEN_MASK) | (len(result.data) & USB_BUF_CTRL_LEN_MASK)
                    _set_uint32_le(self.rp2040.usb_dpram, buf_ctrl_offset, new_buf_ctrl)

                    # Set buffer status for this interrupt endpoint
                    # Interrupt EPs use bits 2+ in buff_status (bit 0 is epx IN, bit 1 is epx OUT)
                    self._push_done((i + 1) * 2, 0)
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
            self._store(HOST_EPX_DATA, result.data)

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
            self._push_done(0, 0)  # Bit 0 for EPX
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
            self._push_done(0, 0)  # Bit 0 for EPX
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
