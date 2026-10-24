"""The USB controller against the datasheet (4.1) and the SDK / TinyUSB driver that drives it (docs/tasks/usb-port.md has the findings and their sources).

Every test says which table or section it follows. They run on both builds: the register file and the DPRAM semantics are the same code either way until the C++ port, and after it the
oracle (`test_usb_diff.py`) holds the native block to this reference.
"""

import pytest

from rp2040py.peripherals import usb as U
from rp2040py.rp2040 import RP2040

USB_BASE = 0x50110000
DPRAM_BASE = 0x50100000
EP1_IN_BUFFER_CONTROL = 0x88
EP1_OUT_BUFFER_CONTROL = 0x8C
EP2_IN_BUFFER_CONTROL = 0x90
BUFFER = 0x180  # a 64-byte aligned buffer in the data area


@pytest.fixture
def mcu() -> RP2040:
    return RP2040()


def reg(mcu: RP2040, offset: int) -> int:
    return mcu.read_uint32(USB_BASE + offset)


def wreg(mcu: RP2040, offset: int, value: int) -> None:
    mcu.write_uint32(USB_BASE + offset, value)


def dpram_word(mcu: RP2040, offset: int) -> int:
    return mcu.read_uint32(DPRAM_BASE + offset)


def set_dpram(mcu: RP2040, offset: int, value: int) -> None:
    """A bus write to the DPRAM - what the firmware does; it reaches the controller's hook."""
    mcu.write_uint32(DPRAM_BASE + offset, value)


class Wire:
    """The host end of a device-mode controller, as `usb/cdc.py` is: collects what the device sends and supplies what it asks for."""

    def __init__(self, mcu: RP2040):
        self.usb = mcu.usb_ctrl
        self.sent: list[tuple[int, bytes]] = []
        self.asked: list[tuple[int, int]] = []
        self.usb.on_endpoint_write = lambda endpoint, buffer: self.sent.append((endpoint, bytes(buffer)))
        self.usb.on_endpoint_read = lambda endpoint, size: self.asked.append((endpoint, size))
        self.clock = mcu.clock

    def settle(self) -> None:
        self.clock.tick(1_000_000)  # long enough for every transfer alarm (10 us)


def configure_endpoint(
    mcu: RP2040, ctrl_offset: int, *, double=False, per_buffer=True, per_two=False, iso=False
) -> None:
    value = U.EP_CTRL_ENABLE_BITS | BUFFER
    if double:
        value |= U.USB_CTRL_DOUBLE_BUF
    if per_buffer:
        value |= U.USB_CTRL_INTERRUPT_PER_TRANSFER
    if per_two:
        value |= U.EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER
    if iso:
        value |= U.EP_CTRL_TYPE_ISOCHRONOUS << U.EP_CTRL_TYPE_SHIFT
    set_dpram(mcu, ctrl_offset, value)


def in_buffer(length: int, *, pid=0, last=False, stall=False, select=False, available=True, full=True) -> int:
    value = length
    value |= U.USB_BUF_CTRL_DATA1_PID if pid else 0
    value |= U.USB_BUF_CTRL_LAST if last else 0
    value |= U.USB_BUF_CTRL_STALL if stall else 0
    value |= U.USB_BUF_CTRL_RESET_SELECT if select else 0
    value |= U.USB_BUF_CTRL_AVAILABLE if available else 0
    value |= U.USB_BUF_CTRL_FULL if full else 0
    return value


# ---- registers: resets, masks, layouts (tables 397-421) ----


def test_nak_poll_resets_to_16_microseconds_and_masks_its_fields(mcu):
    """Table 411: DELAY_FS 25:16 and DELAY_LS 9:0, both 0x010."""
    assert reg(mcu, U.NAK_POLL) == 0x00100010
    wreg(mcu, U.NAK_POLL, 0xFFFFFFFF)
    assert reg(mcu, U.NAK_POLL) == 0x03FF03FF
    mcu.usb_ctrl.reset()
    assert reg(mcu, U.NAK_POLL) == 0x00100010


def test_usb_pwr_has_the_datasheet_layout(mcu):
    """Table 414 / hardware/regs/usb.h: VBUS_EN 0, VBUS_EN_OVERRIDE_EN 1, VBUS_DETECT 2, VBUS_DETECT_OVERRIDE_EN 3, OVERCURR_DETECT 4, OVERCURR_DETECT_EN 5. The reference had 2 and 3 swapped."""
    assert (
        U.PWR_VBUS_EN,
        U.PWR_VBUS_EN_OVERRIDE_EN,
        U.PWR_VBUS_DETECT,
        U.PWR_VBUS_DETECT_OVERRIDE_EN,
        U.PWR_OVERCURR_DETECT,
        U.PWR_OVERCURR_DETECT_EN,
    ) == (1, 2, 4, 8, 16, 32)
    wreg(mcu, U.USB_PWR, 0xFFFFFFFF)
    assert reg(mcu, U.USB_PWR) == 0x3F


def test_vbus_detect_follows_the_override_only_when_it_is_enabled(mcu):
    """4.1.2.10 and table 414: the override pair stands in for the VBUS pin. The value alone does nothing; the enable alone makes it read the (unwired) override value, 0."""
    wreg(mcu, U.USB_PWR, U.PWR_VBUS_DETECT)
    assert not reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_DETECTED
    wreg(mcu, U.USB_PWR, U.PWR_VBUS_DETECT_OVERRIDE_EN)
    assert not reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_DETECTED
    wreg(mcu, U.USB_PWR, U.PWR_VBUS_DETECT | U.PWR_VBUS_DETECT_OVERRIDE_EN)  # what dcd_init() writes
    assert reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_DETECTED
    wreg(mcu, U.USB_PWR, U.PWR_VBUS_DETECT_OVERRIDE_EN)
    assert not reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_DETECTED


def test_vbus_detect_raises_its_interrupt_when_it_changes(mcu):
    """INTR.VBUS_DETECT (bit 11) follows SIE_STATUS.VBUS_DETECTED (table 418)."""
    wreg(mcu, U.USB_PWR, U.PWR_VBUS_DETECT | U.PWR_VBUS_DETECT_OVERRIDE_EN)
    assert reg(mcu, U.INTR) & U.INTR_VBUS_DETECT
    wreg(mcu, U.USB_PWR, 0)
    assert not reg(mcu, U.INTR) & U.INTR_VBUS_DETECT


def test_overcurrent_override_drives_vbus_over_curr(mcu):
    """Table 404: SIE_STATUS.VBUS_OVER_CURR is read-only; table 414's OVERCURR_DETECT(_EN) override pair is where it comes from when no pin is wired."""
    wreg(mcu, U.USB_PWR, U.PWR_OVERCURR_DETECT)
    assert not reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_OVER_CURR
    wreg(mcu, U.USB_PWR, U.PWR_OVERCURR_DETECT | U.PWR_OVERCURR_DETECT_EN)
    assert reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_OVER_CURR
    wreg(mcu, U.SIE_STATUS, 0xFFFFFFFF)  # read-only: writing does not clear it
    assert reg(mcu, U.SIE_STATUS) & U.SIE_VBUS_OVER_CURR


def test_usb_muxing_is_stored(mcu):
    """Table 413: USB_MUXING is four RW bits (TO_PHY 0, TO_EXTPHY 1, TO_DIGITAL_PAD 2, SOFTCON 3)."""
    wreg(mcu, U.USB_MUXING, U.TO_PHY | U.SOFTCON)
    assert reg(mcu, U.USB_MUXING) == 0x9
    wreg(mcu, U.USB_MUXING, 0xFFFFFFFF)
    assert reg(mcu, U.USB_MUXING) == 0xF


def test_the_phy_registers_exist_with_their_writable_bits(mcu):
    """Tables 415-417: USBPHY_DIRECT (the RX / over-current bits 22:16 are read-only, 7 and 3 reserved), USBPHY_DIRECT_OVERRIDE (bit 15 and 12:0), USBPHY_TRIM (reset 0x1F1F)."""
    assert reg(mcu, U.USBPHY_DIRECT) == 0
    assert reg(mcu, U.USBPHY_DIRECT_OVERRIDE) == 0
    assert reg(mcu, U.USBPHY_TRIM) == 0x1F1F
    wreg(mcu, U.USBPHY_DIRECT, 0xFFFFFFFF)
    assert reg(mcu, U.USBPHY_DIRECT) == 0xFF77
    wreg(mcu, U.USBPHY_DIRECT_OVERRIDE, 0xFFFFFFFF)
    assert reg(mcu, U.USBPHY_DIRECT_OVERRIDE) == 0x9FFF
    wreg(mcu, U.USBPHY_TRIM, 0xFFFFFFFF)
    assert reg(mcu, U.USBPHY_TRIM) == 0x1F1F
    wreg(mcu, U.USBPHY_TRIM, 0)
    mcu.usb_ctrl.reset()
    assert reg(mcu, U.USBPHY_TRIM) == 0x1F1F


def test_registers_keep_only_their_defined_bits(mcu):
    """Tables 398, 399, 403, 405, 410: ADDR_ENDP 19:16 + 6:0; ADDR_ENDPn 26, 25, 19:16, 6:0; SIE_CTRL's defined bits; INT_EP_CTRL 15:1; EP_STALL_ARM 1:0."""
    wreg(mcu, U.ADDR_ENDP, 0xFFFFFFFF)
    assert reg(mcu, U.ADDR_ENDP) == 0x000F007F
    wreg(mcu, U.ADDR_ENDP1, 0xFFFFFFFF)
    assert reg(mcu, U.ADDR_ENDP1) == 0x060F007F
    wreg(mcu, U.ADDR_ENDP15, 0xFFFFFFFF)
    assert reg(mcu, U.ADDR_ENDP15) == 0x060F007F
    wreg(mcu, U.SIE_CTRL, 0xFFFFFFFF)
    assert reg(mcu, U.SIE_CTRL) & ~0xFF07BF5F == 0
    wreg(mcu, U.INT_EP_CTRL, 0xFFFFFFFF)
    assert reg(mcu, U.INT_EP_CTRL) == 0xFFFE
    wreg(mcu, U.EP_STALL_ARM, 0xFFFFFFFF)
    assert reg(mcu, U.EP_STALL_ARM) == 3


def test_sie_status_writes_clear_the_wc_bits_only(mcu):
    """Table 404: DATA_SEQ_ERROR..CRC_ERROR, BUS_RESET, TRANS_COMPLETE, SETUP_REC, CONNECTED, RESUME and SUSPENDED are WC; LINE_STATE, VBUS_DETECTED and VBUS_OVER_CURR are read-only; SPEED is WC
    but is the attached device's speed (the event is what the write acknowledges)."""
    usb = mcu.usb_ctrl
    usb.raise_sie_status(
        U.SIE_SUSPENDED | U.SIE_TRANS_COMPLETE | U.SIE_VBUS_DETECTED | (U.SIELineState.J << U.SIE_LINE_STATE_SHIFT)
    )
    wreg(mcu, U.SIE_STATUS, 0xFFFFFFFF)
    status = reg(mcu, U.SIE_STATUS)
    assert not status & (U.SIE_SUSPENDED | U.SIE_TRANS_COMPLETE)
    assert status & U.SIE_VBUS_DETECTED
    assert (status >> U.SIE_LINE_STATE_SHIFT) & 3 == U.SIELineState.J


def test_reading_sof_rd_acknowledges_the_sof_interrupts(mcu):
    """Table 418: INTR.DEV_SOF and HOST_SOF are "cleared by reading SOF_RD"."""
    usb = mcu.usb_ctrl
    usb.raise_interrupt(U.INTR_HOST_SOF | U.INTR_DEV_SOF)
    wreg(mcu, U.SOF_WR, 0x123)
    assert reg(mcu, U.SOF_RD) == 0x123
    assert not reg(mcu, U.INTR) & (U.INTR_HOST_SOF | U.INTR_DEV_SOF)
    wreg(mcu, U.SOF_WR, 0xFFFFFFFF)
    assert reg(mcu, U.SOF_RD) == 0x7FF  # table 401: COUNT is 10:0
    assert reg(mcu, U.SOF_WR) == 0


def test_a_setup_packet_clears_the_ep0_stall_arm_bits(mcu):
    """4.1.2.8.1: "The device controller clears these bits when a SETUP packet is received"."""
    wreg(mcu, U.EP_STALL_ARM, 3)
    mcu.usb_ctrl.send_setup_packet(bytes(8))
    assert reg(mcu, U.EP_STALL_ARM) == 0
    assert reg(mcu, U.SIE_STATUS) & U.SIE_SETUP_REC
    assert reg(mcu, U.INTR) & U.INTR_SETUP_REQ


def test_abort_done_and_stall_nak_status_raise_their_interrupts(mcu):
    """Table 418: INTR.ABORT_DONE (18) is raised while any EP_ABORT_DONE bit is set, INTR.EP_STALL_NAK (19) while any EP_STATUS_STALL_NAK bit is; both are cleared by clearing those bits."""
    wreg(mcu, U.EP_ABORT, 1 << 4)
    assert reg(mcu, U.EP_ABORT_DONE) == 1 << 4
    assert reg(mcu, U.INTR) & U.INTR_ABORT_DONE
    wreg(mcu, U.EP_ABORT_DONE, 1 << 4)
    assert not reg(mcu, U.INTR) & U.INTR_ABORT_DONE
    mcu.usb_ctrl.raise_stall_nak(1 << 2)
    assert reg(mcu, U.INTR) & U.INTR_EP_STALL_NAK
    wreg(mcu, U.EP_STATUS_STALL_NAK, 1 << 2)
    assert not reg(mcu, U.INTR) & U.INTR_EP_STALL_NAK


# ---- device mode: the buffers (4.1.2.7.4, 4.1.2.8.2, 4.1.2.8.3) ----


def test_an_in_buffer_writes_back_length_pid_and_last_buffer_only(mcu):
    """4.1.2.8.2 STATUS phase: "Update status in the appropriate half of the buffer control register: length, pid, and last_buff are set. Everything else is written to zero."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    mcu.usb_dpram[BUFFER : BUFFER + 5] = b"hello"
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(5, pid=1, select=True))
    wire.settle()
    assert wire.sent == [(1, b"hello")]
    assert (
        dpram_word(mcu, EP1_IN_BUFFER_CONTROL) == 5 | U.USB_BUF_CTRL_DATA1_PID
    )  # AVAILABLE, FULL and the reset-select bit are gone


def test_the_host_reacts_to_an_acknowledged_in_buffer_after_the_status_phase_not_with_it(mcu):
    """4.1.2.8.2: the status is written and BUFF_STATUS raised when the IN buffer is acknowledged; what the host does next (the status stage of a control read, then the next SETUP) takes a
    transaction or more. The shortcut host reacts inside its callback, so the callback comes `host_reaction_microseconds` later: otherwise the firmware sees SETUP_REQ and the BUFF_STATUS of the
    transfer before it in one interrupt, and the TinyUSB of pico-sdk 1.2.0-1.4.0 (SETUP first) corrupts EP0's length - found by CI as hello_usb never enumerating (record 0098, USB)."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    mcu.usb_dpram[BUFFER : BUFFER + 3] = b"abc"
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(3))
    mcu.clock.tick(9_999)
    assert reg(mcu, U.BUFF_STATUS) == 0 and wire.sent == []
    mcu.clock.tick(2)  # 10 us: the ACK
    assert reg(mcu, U.BUFF_STATUS) == 1 << 2 and dpram_word(mcu, EP1_IN_BUFFER_CONTROL) == 3
    assert wire.sent == []  # the host has not reacted yet
    mcu.clock.tick(9_000)
    assert wire.sent == []
    mcu.clock.tick(2_000)  # 20 us
    assert wire.sent == [(1, b"abc")]


def test_a_setup_the_host_sends_in_its_reaction_arrives_after_the_buffer_status(mcu):
    """The same, from the firmware's side: BUFF_STATUS is visible one reaction time before the SETUP_REQ that the host's callback causes."""
    wire = Wire(mcu)
    seen = []
    wire.usb.on_endpoint_write = lambda endpoint, buffer: (
        seen.append((int(mcu.clock.ticks), reg(mcu, U.BUFF_STATUS))),
        wire.usb.send_setup_packet(bytes(8)),
    )
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))
    wire.settle()
    assert len(seen) == 1
    assert seen[0][1] == 1 << 2  # when the SETUP comes the buffer status has been there for a while
    assert reg(mcu, U.INTR) & U.INTR_SETUP_REQ


def test_a_last_buffer_raises_trans_complete(mcu):
    """Table 404: TRANS_COMPLETE is "raised by device if an IN or OUT packet is sent with the LAST_BUFF bit set in the buffer control register"."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(4))
    wire.settle()
    assert not reg(mcu, U.SIE_STATUS) & U.SIE_TRANS_COMPLETE
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(4, last=True))
    wire.settle()
    assert reg(mcu, U.SIE_STATUS) & U.SIE_TRANS_COMPLETE
    assert reg(mcu, U.INTR) & U.INTR_TRANS_COMPLETE  # device mode too (it was host-only)


def test_an_in_buffer_without_full_is_not_sent(mcu):
    """4.1.2.8.2 TOKEN phase: "If AVAILABLE and FULL bits are set in buffer control move to the DATA phase. Otherwise send NAK" - the buffer stays as the firmware left it."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(3, full=False))
    wire.settle()
    assert wire.sent == []
    assert dpram_word(mcu, EP1_IN_BUFFER_CONTROL) & U.USB_BUF_CTRL_AVAILABLE
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(3))  # now FULL as well
    wire.settle()
    assert wire.sent == [(1, bytes(3))]


def test_a_stalled_buffer_is_not_sent(mcu):
    """Table 396 bit 11: STALL makes the endpoint answer STALL instead of moving data (the handshake itself is not modelled)."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(3, stall=True))
    wire.settle()
    assert wire.sent == []


def test_buff_status_needs_the_endpoints_interrupt_enable(mcu):
    """Table 395 bit 29: "Enable Interrupt for every transferred buffer". Without it a completed buffer sets nothing in BUFF_STATUS (the reference raised it for a single-buffered IN regardless)."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, per_buffer=False)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))
    wire.settle()
    assert reg(mcu, U.BUFF_STATUS) == 0
    configure_endpoint(mcu, U.EP1_IN_CONTROL, per_buffer=True)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))
    wire.settle()
    assert reg(mcu, U.BUFF_STATUS) == 1 << 2  # EP1_IN
    assert reg(mcu, U.INTR) & U.INTR_BUFF_STATUS


def test_ep0_takes_its_interrupt_enable_from_sie_ctrl(mcu):
    """4.1.2.7.3: "As EP0 has no endpoint control register, the interrupt enable controls for EP0 come from SIE_CTRL" (EP0_INT_1BUF)."""
    wire = Wire(mcu)
    set_dpram(mcu, U.EP0_IN_BUFFER_CONTROL, in_buffer(0))
    wire.settle()
    assert reg(mcu, U.BUFF_STATUS) == 0
    wreg(mcu, U.SIE_CTRL, U.SIE_CTRL_EP0_INT_1BUF)
    set_dpram(mcu, U.EP0_IN_BUFFER_CONTROL, in_buffer(0))
    wire.settle()
    assert reg(mcu, U.BUFF_STATUS) == 1  # EP0_IN
    assert wire.sent == [(0, b""), (0, b"")]


def test_a_buffer_status_bit_comes_back_for_the_second_buffer(mcu):
    """4.1.2.8.2: with a double-buffered endpoint "it is possible to have both buffers done. The cleared BUFF_STATUS bit will be set again, and BUFF_CPU_SHOULD_HANDLE will change in this
    instance." (TinyUSB's handle_hw_buff_status loops on exactly that.)"""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, double=True)
    both = in_buffer(2) | (in_buffer(3) << U.USB_BUF1_SHIFT)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, both)
    wire.settle()
    assert [len(b) for _, b in wire.sent] == [2, 3]
    bit = 1 << 2
    assert reg(mcu, U.BUFF_STATUS) & bit
    assert not reg(mcu, U.BUFF_CPU_SHOULD_HANDLE) & bit  # buffer 0 first
    wreg(mcu, U.BUFF_STATUS, bit)
    assert reg(mcu, U.BUFF_STATUS) & bit  # set again
    assert reg(mcu, U.BUFF_CPU_SHOULD_HANDLE) & bit  # now buffer 1
    wreg(mcu, U.BUFF_STATUS, bit)
    assert not reg(mcu, U.BUFF_STATUS) & bit
    assert not reg(mcu, U.INTR) & U.INTR_BUFF_STATUS


def test_the_every_two_buffers_interrupt_fires_on_the_second_buffer(mcu):
    """Table 395 bit 28: "Enable Interrupt for every 2 transferred buffers (valid for double buffered only)"."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, double=True, per_buffer=False, per_two=True)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))
    wire.settle()
    assert reg(mcu, U.BUFF_STATUS) == 0
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1) | (in_buffer(1) << U.USB_BUF1_SHIFT))
    wire.settle()
    assert len(wire.sent) == 3
    assert reg(mcu, U.BUFF_STATUS) == 1 << 2  # the second of the three completed buffers; the third has not made a pair


def test_a_double_buffered_in_endpoint_takes_buffer_1_after_buffer_0_even_across_writes(mcu):
    """4.1.2.7.4: "the buffer select starts at buffer 0. From then on, the buffer select flips between buffer 0 and 1 unless the 'reset buffer select' bit is set"."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, double=True)
    mcu.usb_dpram[BUFFER] = 0xA0
    mcu.usb_dpram[BUFFER + 64] = 0xB1
    # firmware starts buffer 1 first: nothing moves until buffer 0 is available
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1) << U.USB_BUF1_SHIFT)
    wire.settle()
    assert wire.sent == []
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, dpram_word(mcu, EP1_IN_BUFFER_CONTROL) | in_buffer(1))
    wire.settle()
    assert wire.sent == [(1, b"\xa0"), (1, b"\xb1")]
    # next transfer starts at buffer 0 again after the pair (select flipped twice)
    mcu.usb_dpram[BUFFER] = 0xC2
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))
    wire.settle()
    assert wire.sent[-1] == (1, b"\xc2")


def test_reset_buffer_select_returns_to_buffer_0(mcu):
    """Table 396 bit 12: "Reset buffer select to buffer 0" (TinyUSB sets it on the first buffer of a transfer)."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, double=True)
    mcu.usb_dpram[BUFFER] = 0x10
    mcu.usb_dpram[BUFFER + 64] = 0x20
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1))  # buffer 0 sent: the select is now 1
    wire.settle()
    set_dpram(
        mcu, EP1_IN_BUFFER_CONTROL, in_buffer(1, select=True)
    )  # without the reset the controller would wait at buffer 1
    wire.settle()
    assert wire.sent == [(1, b"\x10"), (1, b"\x10")]


def test_an_isochronous_second_buffer_sits_at_the_offset_the_word_names(mcu):
    """Table 396 bits 28:27: "Double buffer offset for Isochronous mode (0 = 128, 1 = 256, 2 = 512, 3 = 1024)"; a bulk endpoint's second buffer is 64 bytes on."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_IN_CONTROL, double=True, iso=True)
    mcu.usb_dpram[BUFFER + 256] = 0x77
    word = in_buffer(1) | (in_buffer(1) << U.USB_BUF1_SHIFT) | (1 << U.USB_BUF_CTRL_ISO_OFFSET_SHIFT)
    set_dpram(mcu, EP1_IN_BUFFER_CONTROL, word)
    wire.settle()
    assert wire.sent[1] == (1, b"\x77")


def test_an_out_buffer_is_filled_and_written_back_full(mcu):
    """4.1.2.8.3: the controller stores the data, then the status phase sets FULL and writes length / pid / last; the endpoint is asked for exactly the buffer's length."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_OUT_CONTROL)
    set_dpram(mcu, EP1_OUT_BUFFER_CONTROL, in_buffer(64, pid=1, last=True, full=False))
    assert wire.asked == [(1, 64)]
    mcu.usb_ctrl.endpoint_read_done(1, b"abc")
    wire.settle()
    assert bytes(mcu.usb_dpram[BUFFER : BUFFER + 3]) == b"abc"
    assert (
        dpram_word(mcu, EP1_OUT_BUFFER_CONTROL)
        == 3 | U.USB_BUF_CTRL_FULL | U.USB_BUF_CTRL_DATA1_PID | U.USB_BUF_CTRL_LAST
    )
    assert reg(mcu, U.BUFF_STATUS) == 1 << 3  # EP1_OUT
    assert reg(mcu, U.SIE_STATUS) & U.SIE_TRANS_COMPLETE


def test_an_out_buffer_that_is_already_full_is_not_accepted(mcu):
    """4.1.2.8.3 TOKEN phase: the buffer must be AVAILABLE and not FULL, otherwise the host is not given room."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_OUT_CONTROL)
    set_dpram(mcu, EP1_OUT_BUFFER_CONTROL, in_buffer(8, full=True))
    assert wire.asked == []


def test_data_for_the_second_out_buffer_lands_in_the_second_buffer(mcu):
    """A double-buffered OUT endpoint: the second request fills buffer 1, 64 bytes after buffer 0 (the reference put every answer in buffer 0)."""
    wire = Wire(mcu)
    configure_endpoint(mcu, U.EP1_OUT_CONTROL, double=True)
    set_dpram(mcu, EP1_OUT_BUFFER_CONTROL, in_buffer(64, full=False) | (in_buffer(64, full=False) << U.USB_BUF1_SHIFT))
    assert wire.asked == [(1, 64), (1, 64)]
    mcu.usb_ctrl.endpoint_read_done(1, b"first")
    mcu.usb_ctrl.endpoint_read_done(1, b"second")
    wire.settle()
    assert bytes(mcu.usb_dpram[BUFFER : BUFFER + 5]) == b"first"
    assert bytes(mcu.usb_dpram[BUFFER + 64 : BUFFER + 70]) == b"second"
    word = dpram_word(mcu, EP1_OUT_BUFFER_CONTROL)
    assert word & 0x3FF == 5
    assert (word >> U.USB_BUF1_SHIFT) & 0x3FF == 6
    assert word & U.USB_BUF_CTRL_FULL and word & (U.USB_BUF_CTRL_FULL << U.USB_BUF1_SHIFT)


def test_data_for_a_buffer_at_the_end_of_the_dpram_does_not_change_its_size(mcu):
    """The DPRAM is 4 kB (4.1.2.7). An endpoint control word can name a buffer base up to 64 kB and a length is up to 1023: the data is cut off at the end, the memory stays the size it is (a
    bytearray slice assignment past the end used to grow it, a memoryview one raised; the two chips disagreed)."""
    wire = Wire(mcu)
    set_dpram(
        mcu, U.EP1_OUT_CONTROL, U.EP_CTRL_ENABLE_BITS | U.USB_CTRL_INTERRUPT_PER_TRANSFER | 0xFFC0
    )  # a base far past the end
    set_dpram(mcu, EP1_OUT_BUFFER_CONTROL, in_buffer(1023, full=False))
    mcu.usb_ctrl.endpoint_read_done(1, bytes(range(200)) * 5)
    wire.settle()
    assert len(mcu.usb_dpram) == 4096
    assert dpram_word(mcu, EP1_OUT_BUFFER_CONTROL) & 0x3FF == 0  # nothing fitted
    set_dpram(
        mcu, U.EP1_OUT_CONTROL, U.EP_CTRL_ENABLE_BITS | U.USB_CTRL_INTERRUPT_PER_TRANSFER | 0xFFC0 - 0xFC0
    )  # 0x1000 - 0x40 - 0x... a base 64 bytes from the end
    set_dpram(mcu, EP1_OUT_BUFFER_CONTROL, in_buffer(1023, full=False))
    mcu.usb_ctrl.endpoint_read_done(1, bytes([7]) * 200)
    wire.settle()
    assert len(mcu.usb_dpram) == 4096
