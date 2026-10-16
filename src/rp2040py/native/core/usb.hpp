// The RP2040 USB controller in C++ (docs/records/0096-cpp-mcu-core.md; docs/tasks/usb-port.md): a faithful translation of peripherals/_usb.py, which stays as the pure-Python reference and
// the oracle (tests/test_usb_diff.py).
//
// What it owns: the register file, the DPRAM *semantics* (the 4 kB itself is the bus's memory region - the block is handed a pointer to it), the interrupts, the transfer / SOF / bus-reset
// alarms and the host-mode transaction machinery. What it does not own, reached through a `UsbHost` of plain function pointers (core_host.hpp):
//   - the other end of the wire: in device mode the host shortcut `usb/cdc.py` (callbacks `endpoint_write` / `endpoint_read`, and the OUT data it hands back), in host mode a Python `USBDevice`
//     (`device_setup` / `device_in` / `device_out`). The data an IN buffer carried and the data an OUT buffer waits for are Python objects that live in the shell's per-endpoint queues; the
//     block only owns the alarm that delivers them (`drain_write` / `drain_read` run on it and call `finish_write` / `finish_read` back for each);
//   - the interrupt line and the logger.
//
// Failures. A host function may fail (a Python callback may raise): the callee parks the error, raises `*host.failed` and returns false; the block returns false at once, leaving exactly the
// state the reference's exception would have left.
//
// Kept from the reference, each pinned by tests/test_usb_diff.py or tests/cpp/test_usb.cpp:
//   - BUFF_STATUS is not a latch but the state of per-(endpoint, direction) FIFOs of completed buffers: writing 1 pops the oldest, the bit comes back while another is waiting, and
//     BUFF_CPU_SHOULD_HANDLE shows whether the oldest was buffer 1;
//   - a buffer moves only when the token would be answered with data (AVAILABLE and, for IN, FULL; an OUT needs FULL clear; STALL holds it back); the controller then owns it: AVAILABLE is
//     cleared at once and the status is written back (length | pid | last, FULL for an OUT) when the transfer is done;
//   - the write-1-to-clear semantics read `raw_write_value` (the bus's value before an alias decode), and a direct write keeps whatever the last atomic write left;
//   - SIE_STATUS.CONNECTED and SPEED are levels, the connect events are what a write-1-to-clear acknowledges;
//   - INTS is `(raw & enable) | force` (cross-block convention, unsourced).
//
// BOUNDED where the reference's Python lists were not (the one deliberate difference, mirrored in the reference with the same constant so the oracle holds them equal): at most
// `kFifoDepth` completed buffers per (endpoint, direction) wait for the CPU, and at most `kFifoDepth` OUT starts per endpoint wait for their data. The hardware has a single bit per
// endpoint; the lists are the model's own device for "the bit comes back", and a firmware that never services BUFF_STATUS for 64 buffers is not a case worth unbounded memory. The
// `debug` narration of the reference is not ported (nothing observes it).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_USB_HPP
#define RP2040PY_CORE_USB_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"

namespace rp2040core {

// Result codes of the device end in host mode (the reference's USBTransferResult.status strings).
[[maybe_unused]] constexpr uint32_t kUsbOther = 3;  // (read by the shells and the checks; the block itself only tells the other answers apart from ACK / NAK / STALL)
constexpr uint32_t kUsbAck = 0, kUsbNak = 1, kUsbStall = 2;

constexpr uint32_t kUsbWarnRead = kRegWarnRead, kUsbWarnReadAtomicArea = kRegWarnReadAtomicArea, kUsbWarnWrite = kRegWarnWrite;

struct UsbHost {
    bool (*irq)(void* ctx, bool level) = nullptr;
    void* irq_ctx = nullptr;  // the context of `irq` alone, when it is not `ctx` (a line bound straight to the chip, core/mcu.hpp)
    bool (*usb_enabled)(void* ctx) = nullptr;                           // on_usb_enabled (the shell checks it is set)
    bool (*reset_received)(void* ctx) = nullptr;                        // on_reset_received
    bool (*endpoint_read)(void* ctx, uint32_t endpoint, uint32_t length) = nullptr;  // on_endpoint_read: the host is asked for `length` bytes
    bool (*write_queued)(void* ctx, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length) = nullptr;  // an IN buffer has started: the shell queues a copy
    bool (*drain_write)(void* ctx, uint32_t endpoint) = nullptr;        // the IN alarm fired: the shell finishes every queued buffer (`finish_write`)
    bool (*drain_reaction)(void* ctx, uint32_t endpoint) = nullptr;     // the reaction alarm fired: the shell hands every acknowledged buffer to on_endpoint_write
    bool (*drain_read)(void* ctx, uint32_t endpoint) = nullptr;         // the OUT alarm fired: the shell hands the queued data over (`finish_read`)
    bool (*device_reset)(void* ctx) = nullptr;                          // connected_device.on_reset()
    bool (*device_address)(void* ctx, uint32_t* address) = nullptr;     // connected_device.address
    bool (*device_setup)(void* ctx, const uint8_t* setup, bool* has_response) = nullptr;  // handle_setup_packet + SET_ADDRESS; has_response: an IN request with data (kept by the shell)
    bool (*pending_response)(void* ctx, uint32_t max_length, bool* present, const uint8_t** data, uint32_t* length) = nullptr;  // the kept data of a control IN, cut to max_length and forgotten
    bool (*device_in)(void* ctx, uint32_t endpoint_address, uint32_t* status, const uint8_t** data, uint32_t* length) = nullptr;
    bool (*device_out)(void* ctx, uint32_t endpoint, const uint8_t* data, uint32_t length, uint32_t* status) = nullptr;
    RegWarnFn warn = nullptr;
    void* warn_ctx = nullptr;  // the context of `warn` alone, when it is not `ctx` (the chip's warning, core/mcu.hpp)
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace usb_regs {
constexpr uint32_t kEndpoints = 16;
constexpr uint32_t kHostInterruptEndpoints = 15;
constexpr uint32_t kDpramSize = 4096;
constexpr uint32_t kFifoDepth = 64;

[[maybe_unused]] constexpr uint32_t EP1_OUT_CONTROL = 0xC;  // (the shells' and checks' view of the endpoint control words)
constexpr uint32_t EP1_IN_CONTROL = 0x8, EP0_IN_BUFFER_CONTROL = 0x80, EP0_OUT_BUFFER_CONTROL = 0x84, EP15_OUT_BUFFER_CONTROL = 0xFC;
constexpr uint32_t HOST_SETUP_PACKET = 0x00, HOST_INT_EP_CTRL_BASE = 0x08, HOST_INT_EP_BUF_CTRL_BASE = 0x88, HOST_EPX_BUF_CTRL = 0x80, HOST_EPX_DATA = 0x180;

constexpr uint32_t USB_CTRL_DOUBLE_BUF = 1u << 30, USB_CTRL_INTERRUPT_PER_TRANSFER = 1u << 29, EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER = 1u << 28;
constexpr uint32_t EP_CTRL_TYPE_SHIFT = 26, EP_CTRL_TYPE_ISOCHRONOUS = 1, EP_CTRL_BUFFER_ADDRESS_MASK = 0xFFC0, EP_CTRL_HOST_INTERRUPT_INTERVAL_LSB = 16;
constexpr uint32_t EP0_IN_DATA_BUFFER = 0x100;

constexpr uint32_t BUF_AVAILABLE = 1u << 10, BUF_STALL = 1u << 11, BUF_RESET_SELECT = 1u << 12, BUF_FULL = 1u << 15, BUF_LEN_MASK = 0x3FF, BUF_DATA1_PID = 1u << 13, BUF_LAST = 1u << 14;
constexpr uint32_t BUF_ISO_OFFSET_SHIFT = 27, BUF1_SHIFT = 16, BUF1_OFFSET = 64;

constexpr uint32_t ADDR_ENDP = 0x0, ADDR_ENDP1 = 0x04, ADDR_ENDP15 = 0x3C, MAIN_CTRL = 0x40, SOF_WR = 0x44, SOF_RD = 0x48, SIE_CTRL = 0x4C, SIE_STATUS = 0x50, INT_EP_CTRL = 0x54;
constexpr uint32_t BUFF_STATUS = 0x58, BUFF_CPU_SHOULD_HANDLE = 0x5C, EP_ABORT = 0x60, EP_ABORT_DONE = 0x64, EP_STALL_ARM = 0x68, NAK_POLL = 0x6C, EP_STATUS_STALL_NAK = 0x70;
constexpr uint32_t USB_MUXING = 0x74, USB_PWR = 0x78, USBPHY_DIRECT = 0x7C, USBPHY_DIRECT_OVERRIDE = 0x80, USBPHY_TRIM = 0x84, INTR = 0x8C, INTE = 0x90, INTF = 0x94, INTS = 0x98;

constexpr uint32_t MAIN_SIM_TIMING = 1u << 31, MAIN_HOST_NDEVICE = 1u << 1, MAIN_CONTROLLER_EN = 1u << 0;

constexpr uint32_t SIE_CTRL_EP0_DOUBLE_BUF = 1u << 30, SIE_CTRL_EP0_INT_1BUF = 1u << 29, SIE_CTRL_EP0_INT_2BUF = 1u << 28, SIE_CTRL_RESET_BUS = 1u << 13, SIE_CTRL_SOF_EN = 1u << 9;
constexpr uint32_t SIE_CTRL_RECEIVE_DATA = 1u << 3, SIE_CTRL_SEND_DATA = 1u << 2, SIE_CTRL_SEND_SETUP = 1u << 1, SIE_CTRL_START_TRANS = 1u << 0;

constexpr uint32_t SIE_DATA_SEQ_ERROR = 1u << 31, SIE_ACK_REC = 1u << 30, SIE_STALL_REC = 1u << 29, SIE_NAK_REC = 1u << 28, SIE_RX_TIMEOUT = 1u << 27, SIE_RX_OVERFLOW = 1u << 26;
constexpr uint32_t SIE_BIT_STUFF_ERROR = 1u << 25, SIE_CRC_ERROR = 1u << 24, SIE_BUS_RESET = 1u << 19, SIE_TRANS_COMPLETE = 1u << 18, SIE_SETUP_REC = 1u << 17, SIE_CONNECTED = 1u << 16;
constexpr uint32_t SIE_RESUME = 1u << 11, SIE_VBUS_OVER_CURR = 1u << 10, SIE_SPEED_FS_VALUE = 2, SIE_SUSPENDED = 1u << 4, SIE_LINE_STATE_MASK = 0x3, SIE_LINE_STATE_SHIFT = 2;
constexpr uint32_t SIE_VBUS_DETECTED = 1u << 0, SIE_SPEED_MASK = 0x3u << 8, LINE_STATE_J = 1;
constexpr uint32_t SIE_WRITECLEAR_MASK = SIE_DATA_SEQ_ERROR | SIE_ACK_REC | SIE_STALL_REC | SIE_NAK_REC | SIE_RX_TIMEOUT | SIE_RX_OVERFLOW | SIE_BIT_STUFF_ERROR | SIE_CONNECTED | SIE_CRC_ERROR |
                                          SIE_BUS_RESET | SIE_TRANS_COMPLETE | SIE_SETUP_REC | SIE_RESUME | SIE_SUSPENDED;

constexpr uint32_t TO_PHY = 1u << 0, TO_DIGITAL_PAD = 1u << 2;
constexpr uint32_t PWR_VBUS_DETECT = 1u << 2, PWR_VBUS_DETECT_OVERRIDE_EN = 1u << 3, PWR_OVERCURR_DETECT = 1u << 4, PWR_OVERCURR_DETECT_EN = 1u << 5, PWR_MASK = 0x3F;

constexpr uint32_t ADDR_ENDP_MASK = 0x000F007F, ADDR_ENDPN_MASK = 0x060F007F, SIE_CTRL_MASK = 0xFF07BF5F, INT_EP_CTRL_MASK = 0x0000FFFE, EP_STALL_ARM_MASK = 0x3, NAK_POLL_MASK = 0x03FF03FF;
constexpr uint32_t NAK_POLL_RESET = 0x00100010, USB_MUXING_MASK = 0xF, USBPHY_DIRECT_MASK = 0x0000FF77, USBPHY_DIRECT_OVERRIDE_MASK = 0x00009FFF, USBPHY_TRIM_MASK = 0x00001F1F;
constexpr uint32_t USBPHY_TRIM_RESET = 0x00001F1F, INT_MASK = 0x000FFFFF, SOF_COUNT_MASK = 0x7FF;

constexpr uint32_t INTR_EP_STALL_NAK = 1u << 19, INTR_ABORT_DONE = 1u << 18, INTR_DEV_SOF = 1u << 17, INTR_SETUP_REQ = 1u << 16, INTR_DEV_RESUME_FROM_HOST = 1u << 15, INTR_DEV_SUSPEND = 1u << 14;
constexpr uint32_t INTR_DEV_CONN_DIS = 1u << 13, INTR_BUS_RESET = 1u << 12, INTR_VBUS_DETECT = 1u << 11, INTR_STALL = 1u << 10, INTR_ERROR_CRC = 1u << 9, INTR_ERROR_BIT_STUFF = 1u << 8;
constexpr uint32_t INTR_ERROR_RX_OVERFLOW = 1u << 7, INTR_ERROR_RX_TIMEOUT = 1u << 6, INTR_ERROR_DATA_SEQ = 1u << 5, INTR_BUFF_STATUS = 1u << 4, INTR_TRANS_COMPLETE = 1u << 3;
constexpr uint32_t INTR_HOST_SOF = 1u << 2, INTR_HOST_RESUME = 1u << 1, INTR_HOST_CONN_DIS = 1u << 0;
}  // namespace usb_regs

class UsbBlock {
public:
    static constexpr uint32_t kSlots = usb_regs::kEndpoints * 2;

    UsbBlock() = default;
    UsbBlock(const UsbBlock&) = delete;  // the window handler and the alarms hold a pointer to this object
    UsbBlock& operator=(const UsbBlock&) = delete;

    // ---- the registers, public for the shell (the reference's private attributes) ---------------------------------------------------
    uint32_t addr_endp = 0, main_ctrl = 0, int_raw = 0, int_enable = 0, int_force = 0, sie_status = 0, sie_ctrl = 0, sof_frame_number = 0;
    bool connected = false, connect_event = false, host_connect_event = false;
    uint32_t int_ep_addr_ctrl[usb_regs::kHostInterruptEndpoints] = {};
    uint32_t int_ep_ctrl = 0, usb_pwr = 0, usb_muxing = 0, usbphy_direct = 0, usbphy_direct_override = 0, usbphy_trim = usb_regs::USBPHY_TRIM_RESET, nak_poll = usb_regs::NAK_POLL_RESET;
    uint32_t ep_abort = 0, ep_abort_done = 0, ep_stall_arm = 0, ep_status_stall_nak = 0;
    bool host_mode = false, sof_enabled = false, has_device = false, expecting_status_phase = false;
    uint32_t control_data_pid = 1;
    uint32_t buf_select[kSlots] = {}, two_buffer_count[kSlots] = {};
    int64_t read_delay_microseconds = 10, write_delay_microseconds = 10, host_transaction_delay_microseconds = 5, host_reaction_microseconds = 10;

    void init(Clock* clock, uint8_t* dpram, const UsbHost& host) noexcept {
        clock_ = clock;
        dpram_ = dpram;
        host_ = host;
        fill_unconnected();
        for (uint32_t i = 0; i < kAlarms; ++i) {
            slots_[i].owner = this;
            slots_[i].index = i;
            slots_[i].alarm.fire = &UsbBlock::on_alarm;
            slots_[i].alarm.ctx = &slots_[i];
        }
    }

    // The other end of the wire: the host's callbacks and their context (`wire.ctx`). The line, the warning and the failure flag stay the ones the block was initialised with (the chip's).
    void set_wire(const UsbHost& wire) noexcept {
        const UsbHost keep = host_;
        host_ = wire;
        host_.irq = keep.irq;
        host_.irq_ctx = keep.irq_ctx;
        host_.warn = keep.warn;
        host_.warn_ctx = keep.warn_ctx;
        host_.failed = keep.failed;
        fill_unconnected();
    }

    // Unlinks every alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        for (uint32_t i = 0; i < kAlarms; ++i) cancel_alarm(&slots_[i].alarm);
    }

    bool failed() const noexcept { return host_failed(host_.failed); }
    void* warn_ctx() const noexcept { return host_.warn_ctx != nullptr ? host_.warn_ctx : host_.ctx; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    bool has_scheduled_alarms() const noexcept {
        for (uint32_t i = 0; i < kAlarms; ++i) {
            if (slots_[i].alarm.scheduled) return true;
        }
        return false;
    }

    // ---- the lists the reference keeps as Python lists --------------------------------------------------------------------------------
    uint32_t done_count(uint32_t slot) const noexcept { return done_n_[slot]; }
    uint32_t done_at(uint32_t slot, uint32_t index) const noexcept { return done_[slot][index]; }
    uint32_t pending_count(uint32_t endpoint) const noexcept { return pend_n_[endpoint]; }
    uint32_t pending_at(uint32_t endpoint, uint32_t index) const noexcept { return pend_[endpoint][index]; }
    // test seams: what the reference's `_buff_done[slot].append(half)` does (bounded)
    void done_push(uint32_t slot, uint32_t half) noexcept {
        if (done_n_[slot] < usb_regs::kFifoDepth) done_[slot][done_n_[slot]++] = static_cast<uint8_t>(half);
    }

    uint32_t buff_status() const noexcept {
        uint32_t status = 0;
        for (uint32_t i = 0; i < kSlots; ++i) {
            if (done_n_[i]) status |= 1u << i;
        }
        return status;
    }
    uint32_t cpu_should_handle() const noexcept {
        uint32_t status = 0;
        for (uint32_t i = 0; i < kSlots; ++i) {
            if (done_n_[i] && done_[i][0]) status |= 1u << i;
        }
        return status;
    }
    uint32_t int_status() const noexcept { return (int_raw & int_enable) | int_force; }

    // ---- interrupts ---------------------------------------------------------------------------------------------------------------------------
    bool check_interrupts() noexcept { return raise_irq(int_status() != 0); }

    bool buff_status_updated() noexcept {
        if (buff_status()) {
            int_raw |= usb_regs::INTR_BUFF_STATUS;
        } else {
            int_raw &= ~usb_regs::INTR_BUFF_STATUS;
        }
        return check_interrupts();
    }
    bool abort_done_updated() noexcept {
        if (ep_abort_done) {
            int_raw |= usb_regs::INTR_ABORT_DONE;
        } else {
            int_raw &= ~usb_regs::INTR_ABORT_DONE;
        }
        return check_interrupts();
    }
    bool stall_nak_updated() noexcept {
        if (ep_status_stall_nak) {
            int_raw |= usb_regs::INTR_EP_STALL_NAK;
        } else {
            int_raw &= ~usb_regs::INTR_EP_STALL_NAK;
        }
        return check_interrupts();
    }

    // What each interrupt is the raw state of: a SIE_STATUS bit (a level: the interrupt follows it), or an event the register's write-1-to-clear acknowledges. The resume interrupt is the
    // device's or the host's, by mode.
    bool sie_status_updated() noexcept {
        using namespace usb_regs;
        const uint32_t map[][2] = {
            {SIE_SETUP_REC, INTR_SETUP_REQ},
            {SIE_RESUME, host_mode ? INTR_HOST_RESUME : INTR_DEV_RESUME_FROM_HOST},
            {SIE_SUSPENDED, INTR_DEV_SUSPEND},
            {SIE_BUS_RESET, INTR_BUS_RESET},
            {SIE_VBUS_DETECTED, INTR_VBUS_DETECT},
            {SIE_TRANS_COMPLETE, INTR_TRANS_COMPLETE},
            {SIE_STALL_REC, INTR_STALL},
            {SIE_CRC_ERROR, INTR_ERROR_CRC},
            {SIE_BIT_STUFF_ERROR, INTR_ERROR_BIT_STUFF},
            {SIE_RX_OVERFLOW, INTR_ERROR_RX_OVERFLOW},
            {SIE_RX_TIMEOUT, INTR_ERROR_RX_TIMEOUT},
            {SIE_DATA_SEQ_ERROR, INTR_ERROR_DATA_SEQ},
        };
        for (const auto& entry : map) {
            if (sie_status & entry[0]) {
                int_raw |= entry[1];
            } else {
                int_raw &= ~entry[1];
            }
        }
        // The connect/disconnect interrupts are the *edge*: the status bit is a level that survives being written to (`set_connected`), so deriving the interrupt from it would re-raise forever.
        if (connect_event) {
            int_raw |= INTR_DEV_CONN_DIS;
        } else {
            int_raw &= ~INTR_DEV_CONN_DIS;
        }
        if (host_connect_event) {
            int_raw |= INTR_HOST_CONN_DIS;
        } else {
            int_raw &= ~INTR_HOST_CONN_DIS;
        }
        return check_interrupts();
    }

    // SIE_STATUS.CONNECTED is a LEVEL (a host is on the other end), not a latch; the event is what a write-1-to-clear acknowledges (see the reference's docstring for the boot deadlock a latch caused).
    bool set_connected(bool value) noexcept {
        const bool changed = value != connected;
        if (changed) connect_event = true;
        connected = value;
        if (value) {
            sie_status |= usb_regs::SIE_CONNECTED;
        } else {
            sie_status &= ~usb_regs::SIE_CONNECTED;
        }
        return changed ? sie_status_updated() : true;
    }

    // ---- registers ----------------------------------------------------------------------------------------------------------------------------
    uint32_t read(uint32_t offset) noexcept {
        using namespace usb_regs;
        if (offset >= ADDR_ENDP1 && offset <= ADDR_ENDP15 && (offset & 3u) == 0) return int_ep_addr_ctrl[(offset - ADDR_ENDP1) >> 2];
        switch (offset) {
            case ADDR_ENDP: return addr_endp;
            case MAIN_CTRL: return main_ctrl;
            case SOF_WR: return 0;  // write-only
            case SOF_RD:
                // reading it acknowledges the SOF interrupts (datasheet INTR.DEV_SOF / HOST_SOF)
                if (int_raw & (INTR_DEV_SOF | INTR_HOST_SOF)) {
                    int_raw &= ~(INTR_DEV_SOF | INTR_HOST_SOF);
                    (void)check_interrupts();
                }
                return sof_frame_number & SOF_COUNT_MASK;
            case SIE_CTRL: return sie_ctrl;
            case SIE_STATUS: return sie_status;
            case INT_EP_CTRL: return int_ep_ctrl;
            case BUFF_STATUS: return buff_status();
            case BUFF_CPU_SHOULD_HANDLE: return cpu_should_handle();
            case EP_ABORT: return ep_abort;
            case EP_ABORT_DONE: return ep_abort_done;
            case EP_STALL_ARM: return ep_stall_arm;
            case NAK_POLL: return nak_poll;
            case EP_STATUS_STALL_NAK: return ep_status_stall_nak;
            case USB_MUXING: return usb_muxing;
            case USB_PWR: return usb_pwr;
            case USBPHY_DIRECT: return usbphy_direct;
            case USBPHY_DIRECT_OVERRIDE: return usbphy_direct_override;
            case USBPHY_TRIM: return usbphy_trim;
            case INTR: return int_raw;
            case INTE: return int_enable;
            case INTF: return int_force;
            case INTS: return int_status();
            default: break;
        }
        if (host_.warn) {
            host_.warn(warn_ctx(), kUsbWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(warn_ctx(), kUsbWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode; the raw value is `raw_write_value()`. False: a failure is pending.
    bool write(uint32_t offset, int64_t value64) noexcept {
        using namespace usb_regs;
        const uint32_t value = static_cast<uint32_t>(value64);
        const uint32_t raw = static_cast<uint32_t>(raw_write_value_);
        if (offset >= ADDR_ENDP1 && offset <= ADDR_ENDP15 && (offset & 3u) == 0) {
            int_ep_addr_ctrl[(offset - ADDR_ENDP1) >> 2] = value & ADDR_ENDPN_MASK;
            return true;
        }
        switch (offset) {
            case ADDR_ENDP: addr_endp = value & ADDR_ENDP_MASK; return true;
            case MAIN_CTRL:
                main_ctrl = value & (MAIN_SIM_TIMING | MAIN_CONTROLLER_EN | MAIN_HOST_NDEVICE);
                host_mode = (value & MAIN_HOST_NDEVICE) != 0;
                if (value & MAIN_CONTROLLER_EN) {
                    if (host_mode) {
                        if (has_device) return on_device_connected();  // a device is already connected
                    } else {
                        return host_.usb_enabled(host_.ctx);
                    }
                }
                return true;
            case SOF_WR: sof_frame_number = value & SOF_COUNT_MASK; return true;
            case SIE_CTRL: return handle_sie_ctrl_write(value & SIE_CTRL_MASK);
            case INT_EP_CTRL: int_ep_ctrl = value & INT_EP_CTRL_MASK; return true;
            case BUFF_STATUS:
                // write 1 to clear: the oldest completed buffer of the endpoint is handled; the bit comes back at once if another one is waiting
                for (uint32_t index = 0; index < kSlots; ++index) {
                    if ((raw & (1u << index)) && done_n_[index]) done_pop(index);
                }
                return buff_status_updated();
            case EP_ABORT:
                ep_abort = value;
                ep_abort_done |= value;  // immediately marked as done
                return abort_done_updated();
            case EP_ABORT_DONE:
                ep_abort_done &= ~raw;
                return abort_done_updated();
            case EP_STALL_ARM: ep_stall_arm = value & EP_STALL_ARM_MASK; return true;
            case NAK_POLL: nak_poll = value & NAK_POLL_MASK; return true;
            case EP_STATUS_STALL_NAK:
                ep_status_stall_nak &= ~raw;
                return stall_nak_updated();
            case USB_MUXING:
                usb_muxing = value & USB_MUXING_MASK;
                // Workaround for the busy wait in hw_enumeration_fix_force_ls_j() / hw_enumeration_fix_finish()
                if ((value & TO_DIGITAL_PAD) && !(value & TO_PHY)) return set_connected(true);
                return true;
            case USB_PWR:
                usb_pwr = value & PWR_MASK;
                // the override values stand in for the VBUS pins that are not wired
                if ((usb_pwr & PWR_VBUS_DETECT_OVERRIDE_EN) && (usb_pwr & PWR_VBUS_DETECT)) {
                    sie_status |= SIE_VBUS_DETECTED;
                } else {
                    sie_status &= ~SIE_VBUS_DETECTED;
                }
                if ((usb_pwr & PWR_OVERCURR_DETECT_EN) && (usb_pwr & PWR_OVERCURR_DETECT)) {
                    sie_status |= SIE_VBUS_OVER_CURR;
                } else {
                    sie_status &= ~SIE_VBUS_OVER_CURR;
                }
                return sie_status_updated();
            case USBPHY_DIRECT: usbphy_direct = value & USBPHY_DIRECT_MASK; return true;
            case USBPHY_DIRECT_OVERRIDE: usbphy_direct_override = value & USBPHY_DIRECT_OVERRIDE_MASK; return true;
            case USBPHY_TRIM: usbphy_trim = value & USBPHY_TRIM_MASK; return true;
            case SIE_STATUS:
                sie_status &= ~(raw & SIE_WRITECLEAR_MASK);
                // Write-1-to-clear acknowledges the connect/disconnect *event*. It does not unplug anything - see `set_connected`.
                if (raw & SIE_CONNECTED) connect_event = false;
                // likewise the host's connect/disconnect event; SPEED itself is the attached device's speed
                if (raw & SIE_SPEED_MASK) host_connect_event = false;
                if (raw & SIE_BUS_RESET) {
                    if (!host_mode && !host_.reset_received(host_.ctx)) return false;
                    sie_status &= ~(SIE_LINE_STATE_MASK << SIE_LINE_STATE_SHIFT);
                    sie_status |= LINE_STATE_J << SIE_LINE_STATE_SHIFT;
                    if (!set_connected(true)) return false;
                } else if (connected) {
                    sie_status |= SIE_CONNECTED;
                }
                return sie_status_updated();
            case INTE:
                int_enable = value & INT_MASK;
                return check_interrupts();
            case INTF:
                int_force = value & INT_MASK;
                return check_interrupts();
            default: break;
        }
        if (host_.warn) host_.warn(warn_ctx(), kUsbWarnWrite, offset, value64);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        return write(offset, value);
    }

    // ---- device mode: the DPRAM -----------------------------------------------------------------------------------------------------------

    // A completed 32-bit write to the DPRAM (the bus's hook).
    bool dpram_updated(uint32_t offset) noexcept {
        using namespace usb_regs;
        if (host_mode) return true;
        if (offset >= EP0_IN_BUFFER_CONTROL && offset <= EP15_OUT_BUFFER_CONTROL) {
            const uint32_t endpoint = (offset - EP0_IN_BUFFER_CONTROL) >> 3;
            const bool out = (offset & 4u) != 0;
            if (get32(offset) & BUF_RESET_SELECT) buf_select[endpoint * 2 + (out ? 1u : 0u)] = 0;
            return service_endpoint(endpoint, out);
        }
        return true;
    }

    // The host's data for an OUT buffer arrives (the alarm of `schedule_read` ran, the shell drains its queue): it is stored in the buffer the controller was waiting to fill, then the status phase.
    bool finish_read(uint32_t endpoint, const uint8_t* data, uint32_t size) noexcept {
        using namespace usb_regs;
        uint32_t half = 0;
        if (pend_n_[endpoint]) half = pend_pop(endpoint);
        const uint32_t offset = EP0_OUT_BUFFER_CONTROL + endpoint * 8;
        const uint32_t word = get32(offset);
        const uint32_t state = half_state(word, half);
        uint32_t length = state & BUF_LEN_MASK;
        if (size < length) length = size;
        const uint32_t stored = store(buffer_address(endpoint, true, half, word), data, length);
        return buffer_done(endpoint, true, half, stored);
    }

    // An IN buffer reached the host and was acknowledged: the status phase now (the shell has the data), the host's reaction `host_reaction_microseconds` later (`drain_reaction`).
    bool finish_write(uint32_t endpoint, uint32_t half) noexcept {
        if (!buffer_done(endpoint, false, half, 0)) return false;
        clock_->schedule(&slots_[2 * usb_regs::kEndpoints + endpoint].alarm, static_cast<double>(host_reaction_microseconds * 1000));
        return true;
    }

    // The shell queued OUT data / an IN buffer: (re)start the alarm that delivers it.
    void schedule_read(uint32_t endpoint, double delay_microseconds) noexcept { clock_->schedule(&slots_[endpoint].alarm, delay_microseconds * 1000.0); }
    void schedule_write(uint32_t endpoint) noexcept { clock_->schedule(&slots_[usb_regs::kEndpoints + endpoint].alarm, static_cast<double>(write_delay_microseconds * 1000)); }

    // The host sends a setup packet (device mode).
    bool send_setup_packet(const uint8_t* packet, uint32_t size) noexcept {
        using namespace usb_regs;
        for (uint32_t i = 0; i < size && i < kDpramSize; ++i) dpram_[i] = packet[i];
        ep_stall_arm = 0;  // a SETUP clears any STALL on EP0, so the arm bits go with it (4.1.2.8.1)
        sie_status |= SIE_SETUP_REC;
        return sie_status_updated();
    }

    bool reset_device() noexcept {
        clock_->schedule(&slots_[kResetAlarm].alarm, 10'000'000.0);  // USB reset takes ~10ms
        return true;
    }

    // Registers and the transaction state back to power-on. Callbacks, the delay tuning and the alarm objects are left alone; the shell clears the Python-side queues and the attached device.
    bool reset() noexcept {
        using namespace usb_regs;
        addr_endp = main_ctrl = int_raw = int_enable = int_force = sie_status = 0;
        connected = connect_event = host_connect_event = false;
        for (uint32_t i = 0; i < kSlots; ++i) {
            done_n_[i] = 0;
            buf_select[i] = 0;
            two_buffer_count[i] = 0;
        }
        for (uint32_t i = 0; i < kEndpoints; ++i) pend_n_[i] = 0;
        sie_ctrl = sof_frame_number = 0;
        for (uint32_t& entry : int_ep_addr_ctrl) entry = 0;
        int_ep_ctrl = usb_pwr = usb_muxing = usbphy_direct = usbphy_direct_override = 0;
        usbphy_trim = USBPHY_TRIM_RESET;
        nak_poll = NAK_POLL_RESET;
        ep_abort = ep_abort_done = ep_stall_arm = ep_status_stall_nak = 0;
        host_mode = sof_enabled = has_device = expecting_status_phase = false;
        control_data_pid = 1;
        for (uint32_t i = 0; i < kAlarms; ++i) clock_->cancel(&slots_[i].alarm);
        return check_interrupts();
    }

    // ---- host mode ----------------------------------------------------------------------------------------------------------------------------

    // The shell has set/cleared its device object; these are the controller's reactions.
    bool connect_device() noexcept {
        has_device = true;
        if (host_mode && (main_ctrl & usb_regs::MAIN_CONTROLLER_EN)) return on_device_connected();
        return true;
    }
    bool disconnect_device() noexcept {
        const bool had = has_device;
        has_device = false;
        if (had && host_mode) {
            sie_status &= ~usb_regs::SIE_SPEED_MASK;  // clear speed bits to indicate disconnection
            host_connect_event = true;
            return sie_status_updated();
        }
        return true;
    }

    WindowHandler window_handler() noexcept { return BlockWindow<UsbBlock>::handler(this); }

private:
    // A wire the host left out: nothing is connected at the other end. Every callback the block may call answers as an absent device would (no device, nothing queued, a NAK), so a host that gives
    // no wire - or only some of it - never makes the block call a null pointer.
    void fill_unconnected() noexcept {
        if (host_.irq == nullptr) host_.irq = [](void*, bool) { return true; };
        if (host_.usb_enabled == nullptr) host_.usb_enabled = [](void*) { return true; };
        if (host_.reset_received == nullptr) host_.reset_received = [](void*) { return true; };
        if (host_.endpoint_read == nullptr) host_.endpoint_read = [](void*, uint32_t, uint32_t) { return true; };
        if (host_.write_queued == nullptr) host_.write_queued = [](void*, uint32_t, uint32_t, const uint8_t*, uint32_t) { return true; };
        if (host_.drain_write == nullptr) host_.drain_write = [](void*, uint32_t) { return true; };
        if (host_.drain_reaction == nullptr) host_.drain_reaction = [](void*, uint32_t) { return true; };
        if (host_.drain_read == nullptr) host_.drain_read = [](void*, uint32_t) { return true; };
        if (host_.device_reset == nullptr) host_.device_reset = [](void*) { return true; };
        if (host_.device_address == nullptr) host_.device_address = [](void*, uint32_t* address) { *address = 0; return true; };
        if (host_.device_setup == nullptr) host_.device_setup = [](void*, const uint8_t*, bool* has_response) { *has_response = false; return true; };
        if (host_.pending_response == nullptr) host_.pending_response = [](void*, uint32_t, bool* present, const uint8_t**, uint32_t*) { *present = false; return true; };
        if (host_.device_in == nullptr) {
            host_.device_in = [](void*, uint32_t, uint32_t* status, const uint8_t** data, uint32_t* length) {
                *status = kUsbNak;
                *data = nullptr;
                *length = 0;
                return true;
            };
        }
        if (host_.device_out == nullptr) host_.device_out = [](void*, uint32_t, const uint8_t*, uint32_t, uint32_t* status) { *status = kUsbNak; return true; };
    }

    static constexpr uint32_t kAlarms = usb_regs::kEndpoints * 3 + 3;  // 16 OUT, 16 IN, 16 host reactions, bus reset, host SOF, host transaction
    static constexpr uint32_t kReactionAlarm = usb_regs::kEndpoints * 2, kResetAlarm = usb_regs::kEndpoints * 3, kSofAlarm = kResetAlarm + 1, kHostAlarm = kResetAlarm + 2;

    struct AlarmSlot {
        Alarm alarm;
        UsbBlock* owner = nullptr;
        uint32_t index = 0;
    };

    static bool on_alarm(void* ctx) noexcept {
        AlarmSlot* slot = static_cast<AlarmSlot*>(ctx);
        UsbBlock* block = slot->owner;
        const uint32_t index = slot->index;
        if (index < usb_regs::kEndpoints) return block->host_.drain_read(block->host_.ctx, index);
        if (index < kReactionAlarm) return block->host_.drain_write(block->host_.ctx, index - usb_regs::kEndpoints);
        if (index < kResetAlarm) return block->host_.drain_reaction(block->host_.ctx, index - kReactionAlarm);
        if (index == kResetAlarm) {
            block->sie_status |= usb_regs::SIE_BUS_RESET;
            return block->sie_status_updated();
        }
        if (index == kSofAlarm) return block->generate_sof();
        return block->complete_host_transaction();
    }

    // ---- memory ---------------------------------------------------------------------------------------------------------------------------------
    uint32_t get32(uint32_t offset) const noexcept {
        return static_cast<uint32_t>(dpram_[offset]) | (static_cast<uint32_t>(dpram_[offset + 1]) << 8) | (static_cast<uint32_t>(dpram_[offset + 2]) << 16) | (static_cast<uint32_t>(dpram_[offset + 3]) << 24);
    }
    void set32(uint32_t offset, uint32_t value) noexcept {
        dpram_[offset] = static_cast<uint8_t>(value);
        dpram_[offset + 1] = static_cast<uint8_t>(value >> 8);
        dpram_[offset + 2] = static_cast<uint8_t>(value >> 16);
        dpram_[offset + 3] = static_cast<uint8_t>(value >> 24);
    }
    // Writes `size` bytes at `address`, cut off at the end of the 4 kB (the memory never changes size; the hardware's behaviour past the end is not sourced). Returns the bytes stored.
    uint32_t store(uint32_t address, const uint8_t* data, uint32_t size) noexcept {
        if (address >= usb_regs::kDpramSize) return 0;
        uint32_t count = usb_regs::kDpramSize - address;
        if (size < count) count = size;
        for (uint32_t i = 0; i < count; ++i) dpram_[address + i] = data[i];
        return count;
    }

    // ---- the bounded lists ----------------------------------------------------------------------------------------------------------------
    void done_pop(uint32_t slot) noexcept {
        for (uint32_t i = 1; i < done_n_[slot]; ++i) done_[slot][i - 1] = done_[slot][i];
        --done_n_[slot];
    }
    void pend_push(uint32_t endpoint, uint32_t half) noexcept {
        if (pend_n_[endpoint] < usb_regs::kFifoDepth) pend_[endpoint][pend_n_[endpoint]++] = static_cast<uint8_t>(half);
    }
    uint32_t pend_pop(uint32_t endpoint) noexcept {
        const uint32_t half = pend_[endpoint][0];
        for (uint32_t i = 1; i < pend_n_[endpoint]; ++i) pend_[endpoint][i - 1] = pend_[endpoint][i];
        --pend_n_[endpoint];
        return half;
    }

    // ---- device mode: buffers ---------------------------------------------------------------------------------------------------------------
    uint32_t read_endpoint_control(uint32_t endpoint, bool out) const noexcept {
        if (endpoint == 0) return 0;  // EP0 has no endpoint control register: SIE_CTRL carries its interrupt and double-buffer bits
        return get32(usb_regs::EP1_IN_CONTROL + 8 * (endpoint - 1) + (out ? 4u : 0u));
    }
    uint32_t buffer_offset(uint32_t endpoint, bool out) const noexcept {
        if (endpoint == 0) return usb_regs::EP0_IN_DATA_BUFFER;
        return read_endpoint_control(endpoint, out) & usb_regs::EP_CTRL_BUFFER_ADDRESS_MASK;
    }
    bool is_double_buffered(uint32_t endpoint, bool out) const noexcept {
        if (endpoint == 0) return (sie_ctrl & usb_regs::SIE_CTRL_EP0_DOUBLE_BUF) != 0;
        return (read_endpoint_control(endpoint, out) & usb_regs::USB_CTRL_DOUBLE_BUF) != 0;
    }
    // The 16 bits of a buffer-control word that belong to buffer `half`. Buffer 1 has no STALL or reset-select bit: in its half those two positions are the isochronous offset (table 396).
    static uint32_t half_state(uint32_t word, uint32_t half) noexcept {
        using namespace usb_regs;
        uint32_t state = (word >> (BUF1_SHIFT * half)) & 0xFFFFu;
        if (half) state &= ~(BUF_STALL | BUF_RESET_SELECT);
        return state;
    }
    // Where buffer `half` sits: 64 bytes after buffer 0, or - on an isochronous endpoint - the offset the buffer-control word names.
    uint32_t buffer_address(uint32_t endpoint, bool out, uint32_t half, uint32_t buffer_control) const noexcept {
        using namespace usb_regs;
        const uint32_t base = buffer_offset(endpoint, out);
        if (half == 0) return base;
        const uint32_t control = read_endpoint_control(endpoint, out);
        if (endpoint != 0 && ((control >> EP_CTRL_TYPE_SHIFT) & 0x3u) == EP_CTRL_TYPE_ISOCHRONOUS) return base + (128u << ((buffer_control >> BUF_ISO_OFFSET_SHIFT) & 0x3u));
        return base + BUF1_OFFSET;
    }

    // Starts the transfer of every buffer the firmware has made AVAILABLE, in the order the controller takes them (buffer 0, then buffer 1 of a double-buffered endpoint). A buffer moves only if the
    // token would be answered with data; the controller owns it from here: AVAILABLE is cleared at once, the status is written back when the transfer is done.
    bool service_endpoint(uint32_t endpoint, bool out) noexcept {
        using namespace usb_regs;
        const uint32_t offset = EP0_IN_BUFFER_CONTROL + endpoint * 8 + (out ? 4u : 0u);
        const uint32_t slot = endpoint * 2 + (out ? 1u : 0u);
        const bool dbl = is_double_buffered(endpoint, out);
        for (uint32_t pass = 0; pass < (dbl ? 2u : 1u); ++pass) {
            uint32_t word = get32(offset);
            const uint32_t half = dbl ? buf_select[slot] : 0u;
            const uint32_t state = half_state(word, half);
            if (!(state & BUF_AVAILABLE) || (state & BUF_STALL)) return true;
            if (((state & BUF_FULL) != 0) == out) return true;
            const uint32_t length = state & BUF_LEN_MASK;
            const uint32_t address = buffer_address(endpoint, out, half, word);
            word &= ~(BUF_AVAILABLE << (BUF1_SHIFT * half));
            set32(offset, word);
            if (dbl) buf_select[slot] ^= 1u;
            if (out) {
                pend_push(endpoint, half);
                if (!host_.endpoint_read(host_.ctx, endpoint, length)) return false;
            } else {
                uint32_t count = 0;
                if (address < kDpramSize) {
                    count = kDpramSize - address;
                    if (length < count) count = length;
                }
                if (!host_.write_queued(host_.ctx, endpoint, half, dpram_ + (address < kDpramSize ? address : 0), count)) return false;
                schedule_write(endpoint);
            }
        }
        return true;
    }

    // The status phase of a transfer: the buffer's half of the buffer-control word is written back as length, pid and last-buffer, everything else zero (FULL for an OUT, which has filled the buffer);
    // TRANS_COMPLETE for a last buffer; BUFF_STATUS if the endpoint's interrupt enable asks for it.
    bool buffer_done(uint32_t endpoint, bool out, uint32_t half, uint32_t received) noexcept {
        using namespace usb_regs;
        const uint32_t offset = EP0_IN_BUFFER_CONTROL + endpoint * 8 + (out ? 4u : 0u);
        uint32_t word = get32(offset);
        const uint32_t shift = BUF1_SHIFT * half;
        const uint32_t state = half_state(word, half);
        const uint32_t length = out ? received : (state & BUF_LEN_MASK);
        const uint32_t done = (length & BUF_LEN_MASK) | (state & (BUF_DATA1_PID | BUF_LAST)) | (out ? BUF_FULL : 0u);
        word = (word & ~(0xFFFFu << shift)) | (done << shift);
        set32(offset, word);
        if (state & BUF_LAST) sie_status |= SIE_TRANS_COMPLETE;
        if (!out) sie_status |= SIE_ACK_REC;
        if (!sie_status_updated()) return false;

        const uint32_t slot = endpoint * 2 + (out ? 1u : 0u);
        bool per_buffer, per_two;
        if (endpoint == 0) {
            per_buffer = (sie_ctrl & SIE_CTRL_EP0_INT_1BUF) != 0;
            per_two = (sie_ctrl & SIE_CTRL_EP0_INT_2BUF) != 0;
        } else {
            const uint32_t control = read_endpoint_control(endpoint, out);
            per_buffer = (control & USB_CTRL_INTERRUPT_PER_TRANSFER) != 0;
            per_two = (control & EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER) != 0;
        }
        if (per_buffer) {
            done_push(slot, half);
        } else if (per_two && is_double_buffered(endpoint, out)) {
            ++two_buffer_count[slot];
            if (two_buffer_count[slot] == 2) {
                two_buffer_count[slot] = 0;
                done_push(slot, half);
            }
        }
        if (!buff_status_updated()) return false;
        return service_endpoint(endpoint, out);  // the other buffer of a double-buffered endpoint may already be waiting
    }

    // ---- host mode --------------------------------------------------------------------------------------------------------------------------
    bool on_device_connected() noexcept {
        // full-speed device connected (value 2 in the speed field, bits 9:8)
        sie_status &= ~usb_regs::SIE_SPEED_MASK;
        sie_status |= usb_regs::SIE_SPEED_FS_VALUE << 8;
        host_connect_event = true;
        return sie_status_updated();
    }

    bool handle_sie_ctrl_write(uint32_t value) noexcept {
        using namespace usb_regs;
        sie_ctrl = value;
        if ((value & SIE_CTRL_SOF_EN) && !sof_enabled) {
            sof_enabled = true;
            schedule_sof();
        } else if (!(value & SIE_CTRL_SOF_EN) && sof_enabled) {
            sof_enabled = false;
        }
        if (value & SIE_CTRL_RESET_BUS) {
            if (has_device && !host_.device_reset(host_.ctx)) return false;
            control_data_pid = 1;  // reset data toggle
        }
        if (value & SIE_CTRL_START_TRANS) return start_host_transaction();
        return true;
    }

    void schedule_sof() noexcept {
        if (sof_enabled) clock_->schedule(&slots_[kSofAlarm].alarm, 1'000'000.0);  // SOF every 1 ms
    }

    bool generate_sof() noexcept {
        sof_frame_number = (sof_frame_number + 1) & 0x7FF;
        int_raw |= usb_regs::INTR_HOST_SOF;
        if (!check_interrupts()) return false;
        if (!poll_interrupt_endpoints()) return false;
        schedule_sof();
        return true;
    }

    void schedule_host_transaction() noexcept { clock_->schedule(&slots_[kHostAlarm].alarm, static_cast<double>(host_transaction_delay_microseconds * 1000)); }

    bool poll_interrupt_endpoints() noexcept {
        using namespace usb_regs;
        for (uint32_t i = 0; i < kHostInterruptEndpoints; ++i) {
            if (!(int_ep_ctrl & (1u << (i + 1)))) continue;
            const uint32_t addr_endp_i = int_ep_addr_ctrl[i];
            const uint32_t dev_addr = addr_endp_i & 0x7Fu;
            const uint32_t ep_num = (addr_endp_i >> 16) & 0xFu;
            const bool is_out = (addr_endp_i & (1u << 25)) != 0;  // INTEP_DIR
            if (!has_device) continue;
            uint32_t address = 0;
            if (!host_.device_address(host_.ctx, &address)) return false;
            if (address != dev_addr) continue;

            const uint32_t ep_ctrl = get32(HOST_INT_EP_CTRL_BASE + i * 8);
            const uint32_t interval = ((ep_ctrl >> EP_CTRL_HOST_INTERRUPT_INTERVAL_LSB) & 0x1FFu) + 1;
            if (sof_frame_number % interval != 0) continue;  // (simplified: every SOF the interval allows)

            const uint32_t buf_ctrl_offset = HOST_INT_EP_BUF_CTRL_BASE + i * 8;
            const uint32_t buf_ctrl = get32(buf_ctrl_offset);
            if (!(buf_ctrl & BUF_AVAILABLE)) continue;  // only poll if the buffer is available
            if (is_out) continue;

            uint32_t status = 0, length = 0;
            const uint8_t* data = nullptr;
            if (!host_.device_in(host_.ctx, 0x80u | ep_num, &status, &data, &length)) return false;
            if (status == kUsbAck && length > 0) {
                store(ep_ctrl & 0xFFC0u, data, length);
                uint32_t updated = (buf_ctrl & ~BUF_AVAILABLE) | BUF_FULL;
                updated = (updated & ~BUF_LEN_MASK) | (length & BUF_LEN_MASK);
                set32(buf_ctrl_offset, updated);
                // interrupt endpoints use bits 2+ of buff_status (bit 0 is epx IN, bit 1 is epx OUT)
                done_push((i + 1) * 2, 0);
                if (!buff_status_updated()) return false;
            }
        }
        return true;
    }

    bool start_host_transaction() noexcept {
        using namespace usb_regs;
        if (!host_mode) return true;
        const uint32_t ep_num = (addr_endp >> 16) & 0xFu;
        if (!has_device) {  // no device connected - timeout
            sie_status |= SIE_RX_TIMEOUT;
            return sie_status_updated();
        }
        if (sie_ctrl & SIE_CTRL_SEND_SETUP) return handle_setup_transaction();
        if (sie_ctrl & SIE_CTRL_RECEIVE_DATA) return handle_in_transaction(ep_num);
        if (sie_ctrl & SIE_CTRL_SEND_DATA) return handle_out_transaction(ep_num);
        return true;
    }

    bool handle_setup_transaction() noexcept {
        using namespace usb_regs;
        bool has_response = false;
        if (!host_.device_setup(host_.ctx, dpram_ + HOST_SETUP_PACKET, &has_response)) return false;
        // the data of an IN request is kept (by the shell) for the IN transaction that follows; any other request is followed by a zero-length IN status phase
        expecting_status_phase = !has_response;
        control_data_pid = 1;  // reset data toggle for the data phase
        sie_status |= SIE_ACK_REC;  // SETUP always gets ACK
        schedule_host_transaction();
        return true;
    }

    bool handle_in_transaction(uint32_t ep_num) noexcept {
        using namespace usb_regs;
        uint32_t status = kUsbAck, length = 0;
        const uint8_t* data = nullptr;
        bool answered = false;
        if (ep_num == 0 && expecting_status_phase) {
            expecting_status_phase = false;  // control OUT status phase - a zero-length ACK
            answered = true;
        } else if (ep_num == 0) {
            bool present = false;
            const uint32_t max_length = get32(HOST_EPX_BUF_CTRL) & BUF_LEN_MASK;
            if (!host_.pending_response(host_.ctx, max_length, &present, &data, &length)) return false;
            answered = present;
        }
        if (!answered && !host_.device_in(host_.ctx, 0x80u | ep_num, &status, &data, &length)) return false;

        if (status == kUsbAck && length > 0) {
            store(HOST_EPX_DATA, data, length);
            uint32_t buf_ctrl = get32(HOST_EPX_BUF_CTRL);
            buf_ctrl &= ~BUF_LEN_MASK;
            buf_ctrl |= length & BUF_LEN_MASK;
            buf_ctrl |= BUF_FULL;
            buf_ctrl &= ~BUF_AVAILABLE;
            if (control_data_pid) {
                buf_ctrl |= BUF_DATA1_PID;
            } else {
                buf_ctrl &= ~BUF_DATA1_PID;
            }
            control_data_pid ^= 1u;
            set32(HOST_EPX_BUF_CTRL, buf_ctrl);
            done_push(0, 0);  // bit 0 for EPX
            if (!buff_status_updated()) return false;
            sie_status |= SIE_ACK_REC;
        } else if (status == kUsbNak) {
            sie_status |= SIE_NAK_REC;
        } else if (status == kUsbStall) {
            sie_status |= SIE_STALL_REC;
        }
        schedule_host_transaction();
        return true;
    }

    bool handle_out_transaction(uint32_t ep_num) noexcept {
        using namespace usb_regs;
        const uint32_t buf_ctrl = get32(HOST_EPX_BUF_CTRL);
        const uint32_t data_len = buf_ctrl & BUF_LEN_MASK;
        uint32_t status = kUsbAck;
        if (!(ep_num == 0 && data_len == 0)) {  // a zero-length control status phase is acknowledged without asking the device
            uint32_t available = kDpramSize - HOST_EPX_DATA;
            if (data_len < available) available = data_len;
            if (!host_.device_out(host_.ctx, ep_num, dpram_ + HOST_EPX_DATA, available, &status)) return false;
        }
        uint32_t updated = buf_ctrl & ~BUF_AVAILABLE;
        if (control_data_pid) {
            updated |= BUF_DATA1_PID;
        } else {
            updated &= ~BUF_DATA1_PID;
        }
        control_data_pid ^= 1u;
        set32(HOST_EPX_BUF_CTRL, updated);
        if (status == kUsbAck) {
            sie_status |= SIE_ACK_REC;
            done_push(0, 0);
            if (!buff_status_updated()) return false;
        } else if (status == kUsbNak) {
            sie_status |= SIE_NAK_REC;
        } else if (status == kUsbStall) {
            sie_status |= SIE_STALL_REC;
        }
        schedule_host_transaction();
        return true;
    }

    bool complete_host_transaction() noexcept {
        sie_ctrl &= ~usb_regs::SIE_CTRL_START_TRANS;
        sie_status |= usb_regs::SIE_TRANS_COMPLETE;
        return sie_status_updated();
    }

    bool raise_irq(bool level) noexcept { return host_.irq(host_.irq_ctx != nullptr ? host_.irq_ctx : host_.ctx, level); }

    UsbHost host_;
    Clock* clock_ = nullptr;
    uint8_t* dpram_ = nullptr;
    AlarmSlot slots_[kAlarms];
    uint8_t done_[kSlots][usb_regs::kFifoDepth] = {};
    uint32_t done_n_[kSlots] = {};
    uint8_t pend_[usb_regs::kEndpoints][usb_regs::kFifoDepth] = {};
    uint32_t pend_n_[usb_regs::kEndpoints] = {};
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_USB_HPP
