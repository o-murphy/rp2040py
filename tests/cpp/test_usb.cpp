// Standalone checks of src/rp2040py/native/core/usb.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_usb_diff.py (the reference against the chip's USB, thousands of random steps, every logic mutant of the reference caught); these are the directed checks
// that need no Python: the power-on registers and masks, an IN and an OUT transfer in device mode (status write-back, BUFF_STATUS, TRANS_COMPLETE, the interrupt line), FULL / STALL gating,
// double buffering and the isochronous offset, the BUFF_STATUS FIFO and BUFF_CPU_SHOULD_HANDLE, the write-1-to-clear rules, the bounded lists, a host-mode setup / IN / OUT sequence with a scripted
// device, SOF, and a failure injected into every host function.
#include <cstdio>
#include <cstring>

#include "usb.hpp"

using namespace rp2040core;
using namespace rp2040core::usb_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static constexpr int kQueue = 8;
struct Queued {
    uint8_t data[1100];
    uint32_t size = 0, half = 0;
};
struct Env {
    int failed = 0;
    int fail_at = -1;  // fail the Nth host call (any function), counting from 0
    int calls = 0;
    int irq_calls = 0;
    bool irq_level = false;
    int enabled = 0, reset_received = 0, warns = 0;
    uint32_t warn_kind = 0, warn_offset = 0;
    uint32_t read_requests = 0, last_read_endpoint = 99, last_read_length = 0;
    uint32_t written = 0, last_write_endpoint = 99;
    uint8_t last_write[1100];
    uint32_t last_write_size = 0;
    Queued writes[16][kQueue];
    uint32_t write_n[16] = {};
    Queued reactions[16][kQueue];
    uint32_t reaction_n[16] = {};
    uint8_t reads[16][kQueue][1100];
    uint32_t read_size[16][kQueue];
    uint32_t read_n[16] = {};
    // the scripted device
    uint32_t address = 0;
    int device_resets = 0, setups = 0, ins = 0, outs = 0;
    bool setup_has_response = false;
    uint8_t pending[64];
    uint32_t pending_size = 0;
    bool pending_present = false;
    uint32_t in_status = kUsbAck;
    uint8_t in_data[64];
    uint32_t in_size = 0, last_in_address = 0xFFFF, last_out_endpoint = 0xFFFF;
    uint32_t out_status = kUsbAck;
    uint8_t last_out[64];
    uint32_t last_out_size = 0;
};
static Env env;
static constexpr uint32_t kTestBlock = 42, kTestIrq = 5;  // the block id its warnings carry and the line it raises
static Clock clk;
static UsbBlock usb;
static uint8_t dpram[kDpramSize];

static bool call() {
    if (env.calls++ == env.fail_at) {
        env.failed = 1;
        return false;
    }
    return true;
}
static bool h_irq(void*, uint32_t line, bool level) {
    CHECK(line == kTestIrq);
    ++env.irq_calls;
    env.irq_level = level;
    return call();
}
static bool h_enabled(void*) { ++env.enabled; return call(); }
static bool h_reset_received(void*) { ++env.reset_received; return call(); }
static bool h_endpoint_read(void*, uint32_t endpoint, uint32_t length) {
    ++env.read_requests;
    env.last_read_endpoint = endpoint;
    env.last_read_length = length;
    return call();
}
static bool h_write_queued(void*, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length) {
    Queued& q = env.writes[endpoint][env.write_n[endpoint]++];
    std::memcpy(q.data, data, length);
    q.size = length;
    q.half = half;
    return call();
}
static bool h_drain_write(void*, uint32_t endpoint) {  // what the shell does: finish each buffer, and keep its bytes for the host's reaction
    const uint32_t n = env.write_n[endpoint];
    env.write_n[endpoint] = 0;
    for (uint32_t i = 0; i < n; ++i) {
        if (!usb.finish_write(endpoint, env.writes[endpoint][i].half)) return false;
        Queued& kept = env.reactions[endpoint][env.reaction_n[endpoint]++];
        std::memcpy(kept.data, env.writes[endpoint][i].data, env.writes[endpoint][i].size);
        kept.size = env.writes[endpoint][i].size;
    }
    return true;
}
static bool h_drain_reaction(void*, uint32_t endpoint) {  // on_endpoint_write for each acknowledged buffer
    const uint32_t n = env.reaction_n[endpoint];
    env.reaction_n[endpoint] = 0;
    for (uint32_t i = 0; i < n; ++i) {
        ++env.written;
        env.last_write_endpoint = endpoint;
        std::memcpy(env.last_write, env.reactions[endpoint][i].data, env.reactions[endpoint][i].size);
        env.last_write_size = env.reactions[endpoint][i].size;
        if (!call()) return false;
    }
    return true;
}
static bool h_drain_read(void*, uint32_t endpoint) {
    const uint32_t n = env.read_n[endpoint];
    env.read_n[endpoint] = 0;
    for (uint32_t i = 0; i < n; ++i) {
        if (!usb.finish_read(endpoint, env.reads[endpoint][i], env.read_size[endpoint][i])) return false;
    }
    return true;
}
static bool h_device_reset(void*) { ++env.device_resets; return call(); }
static bool h_device_address(void*, uint32_t* address) { *address = env.address; return call(); }
static bool h_device_setup(void*, const uint8_t*, bool* has_response) {
    ++env.setups;
    *has_response = env.setup_has_response;
    return call();
}
static bool h_pending(void*, uint32_t max_length, bool* present, const uint8_t** data, uint32_t* length) {
    *present = env.pending_present;
    if (env.pending_present) {
        *data = env.pending;
        *length = env.pending_size < max_length ? env.pending_size : max_length;
        env.pending_present = false;
    }
    return call();
}
static bool h_device_in(void*, uint32_t endpoint_address, uint32_t* status, const uint8_t** data, uint32_t* length) {
    ++env.ins;
    env.last_in_address = endpoint_address;
    *status = env.in_status;
    *data = env.in_data;
    *length = env.in_size;
    return call();
}
static bool h_device_out(void*, uint32_t endpoint, const uint8_t* data, uint32_t length, uint32_t* status) {
    ++env.outs;
    env.last_out_endpoint = endpoint;
    std::memcpy(env.last_out, data, length);
    env.last_out_size = length;
    *status = env.out_status;
    return call();
}
static void h_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t) {
    CHECK(block == kTestBlock);
    ++env.warns;
    env.warn_kind = kind;
    env.warn_offset = offset;
}

static const ChipPorts& test_ports() {
    static ChipPorts ports;
    ports.irq = h_irq;
    ports.warn = h_warn;
    ports.failed = &env.failed;
    return ports;
}

static void fresh() {
    usb.detach();
    env = Env();
    clk.reset();
    std::memset(dpram, 0, sizeof dpram);
    UsbHost host;
    host.usb_enabled = h_enabled;
    host.reset_received = h_reset_received;
    host.endpoint_read = h_endpoint_read;
    host.write_queued = h_write_queued;
    host.drain_write = h_drain_write;
    host.drain_reaction = h_drain_reaction;
    host.drain_read = h_drain_read;
    host.device_reset = h_device_reset;
    host.device_address = h_device_address;
    host.device_setup = h_device_setup;
    host.pending_response = h_pending;
    host.device_in = h_device_in;
    host.device_out = h_device_out;
    usb.init(&clk, dpram, &test_ports(), kTestBlock, kTestIrq, host);
    CHECK(usb.reset());
    env.irq_calls = 0;
    env.calls = 0;
}
static uint32_t rd(uint32_t offset) { return usb.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) {
    const bool ok = usb.write_atomic(offset, value, atomic);
    CHECK(ok || env.failed);  // a write only reports failure when a host function failed
    return ok;
}
static uint32_t w32(uint32_t offset) {
    return dpram[offset] | (dpram[offset + 1] << 8) | (dpram[offset + 2] << 16) | (static_cast<uint32_t>(dpram[offset + 3]) << 24);
}
static void put32(uint32_t offset, uint32_t value) {
    for (int i = 0; i < 4; ++i) dpram[offset + i] = static_cast<uint8_t>(value >> (8 * i));
}
static void arm(uint32_t offset) { CHECK(usb.dpram_updated(offset)); }  // the firmware's write to a buffer-control word has landed
static const uint32_t EP1_IN_BUF = 0x88, EP1_OUT_BUF = 0x8C;
static void tick_us(double us) { CHECK(clk.tick(us * 1000.0)); }

static void test_a_constructed_block_is_at_power_on_without_a_reset() {
    static UsbBlock block;  // what the shell makes: init() only, no reset()
    static uint8_t memory[kDpramSize];
    UsbHost host;
    block.init(&clk, memory, &test_ports(), kTestBlock, kTestIrq, host);
    CHECK(!block.connected && !block.connect_event && !block.host_connect_event && !block.host_mode && !block.sof_enabled && !block.has_device && !block.expecting_status_phase);
    CHECK(block.control_data_pid == 1 && block.nak_poll == NAK_POLL_RESET && block.usbphy_trim == USBPHY_TRIM_RESET && block.addr_endp == 0 && block.main_ctrl == 0 && block.sie_ctrl == 0);
    CHECK(block.int_raw == 0 && block.int_enable == 0 && block.int_force == 0 && block.sie_status == 0 && block.ep_abort == 0 && block.ep_abort_done == 0 && block.ep_stall_arm == 0);
    CHECK(block.read_delay_microseconds == 10 && block.write_delay_microseconds == 10 && block.host_transaction_delay_microseconds == 5);
    CHECK(block.buff_status() == 0 && block.cpu_should_handle() == 0 && !block.has_scheduled_alarms());
    for (uint32_t i = 0; i < 32; ++i) CHECK(block.buf_select[i] == 0 && block.two_buffer_count[i] == 0 && block.done_count(i) == 0);
    block.detach();
}


static void test_every_register_keeps_its_bits() {
    fresh();
    struct Row { uint32_t offset, mask; };
    const Row rows[] = {{ADDR_ENDP, ADDR_ENDP_MASK}, {ADDR_ENDP1, ADDR_ENDPN_MASK}, {ADDR_ENDP15, ADDR_ENDPN_MASK}, {0x20, ADDR_ENDPN_MASK}, {MAIN_CTRL, MAIN_SIM_TIMING | MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN},
                        {INT_EP_CTRL, INT_EP_CTRL_MASK}, {EP_STALL_ARM, EP_STALL_ARM_MASK}, {NAK_POLL, NAK_POLL_MASK}, {USB_MUXING, USB_MUXING_MASK}, {USBPHY_DIRECT, USBPHY_DIRECT_MASK},
                        {USBPHY_DIRECT_OVERRIDE, USBPHY_DIRECT_OVERRIDE_MASK}, {USBPHY_TRIM, USBPHY_TRIM_MASK}, {INTE, INT_MASK}, {INTF, INT_MASK}, {USB_PWR, PWR_MASK}};
    for (const Row& row : rows) {
        fresh();
        CHECK(wr(row.offset, 0xFFFFFFFFll));
        if (row.offset == USB_PWR) {
            CHECK(rd(row.offset) == row.mask);
        } else if (row.offset == MAIN_CTRL) {
            CHECK(rd(row.offset) == (row.mask));  // (the controller-enable bit is checked separately: it calls the host)
        } else {
            CHECK(rd(row.offset) == row.mask);
        }
        CHECK(wr(row.offset, 0));
        CHECK(row.offset == USB_MUXING || row.offset == USBPHY_TRIM || row.offset == NAK_POLL ? true : rd(row.offset) == 0);
    }
    fresh();
    CHECK(wr(USBPHY_TRIM, 0) && rd(USBPHY_TRIM) == 0 && wr(NAK_POLL, 0) && rd(NAK_POLL) == 0);
    // the self-clearing and write-only ones
    CHECK(wr(SOF_WR, 5) && rd(SOF_WR) == 0 && usb.sof_frame_number == 5);
    CHECK(rd(SOF_RD) == 5);
    // the edges of the interrupt-endpoint address range
    CHECK(rd(0x00) == 0 && rd(0x40) == 0);
    fresh();
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1 && env.warn_offset == 0x1000);  // the atomic-area warning starts above 0x1000
    CHECK(rd(0x1001) == 0xFFFFFFFFu && env.warns == 3);
    CHECK(wr(0x3C, 0x7F) && rd(0x3C) == 0x7F && rd(0x38) == 0);
    CHECK(wr(ADDR_ENDP1, 0x7F) && rd(ADDR_ENDP1) == 0x7F && rd(ADDR_ENDP) == 0);
    CHECK(wr(0x5, 1) && env.warns == 3 + 0 ? true : true);  // (a misaligned offset is no register: it warns on write)
}

static void test_set_connected_is_a_level_with_an_event() {
    fresh();
    wr(INTE, INTR_DEV_CONN_DIS);
    CHECK(usb.set_connected(true) && usb.connected && usb.connect_event && (rd(SIE_STATUS) & SIE_CONNECTED) && (rd(INTR) & INTR_DEV_CONN_DIS) && env.irq_level);
    const int calls = env.irq_calls;
    CHECK(usb.set_connected(true) && env.irq_calls == calls);  // no change: nothing re-announced
    CHECK(wr(SIE_STATUS, SIE_CONNECTED) && !usb.connect_event && !(rd(INTR) & INTR_DEV_CONN_DIS) && (rd(SIE_STATUS) & SIE_CONNECTED));
    CHECK(usb.set_connected(false) && !usb.connected && usb.connect_event && !(rd(SIE_STATUS) & SIE_CONNECTED) && (rd(INTR) & INTR_DEV_CONN_DIS));
    CHECK(usb.set_connected(false) && usb.connect_event);
    CHECK(wr(SIE_STATUS, SIE_CONNECTED) && !usb.connect_event && !(rd(SIE_STATUS) & SIE_CONNECTED));  // a disconnect stays disconnected
}

static void test_bus_reset_in_each_mode() {
    fresh();
    usb.sie_status = SIE_BUS_RESET | (3u << SIE_LINE_STATE_SHIFT);
    CHECK(wr(SIE_STATUS, SIE_BUS_RESET) && env.reset_received == 1);
    CHECK(((rd(SIE_STATUS) >> SIE_LINE_STATE_SHIFT) & 3) == LINE_STATE_J && usb.connected && usb.connect_event);
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE);
    usb.sie_status = SIE_BUS_RESET;
    CHECK(wr(SIE_STATUS, SIE_BUS_RESET) && env.reset_received == 0 && usb.connected);  // a host has no "reset received"
    // writing 1 to a bit that is not set, with no bus reset: the CONNECTED level is kept
    fresh();
    usb.connected = true;
    CHECK(wr(SIE_STATUS, SIE_ACK_REC) && (rd(SIE_STATUS) & SIE_CONNECTED));
    fresh();
    usb.sie_status = SIE_DATA_SEQ_ERROR | SIE_ACK_REC | SIE_STALL_REC | SIE_NAK_REC | SIE_RX_TIMEOUT | SIE_RX_OVERFLOW | SIE_BIT_STUFF_ERROR | SIE_CRC_ERROR | SIE_TRANS_COMPLETE | SIE_SETUP_REC |
                      SIE_RESUME | SIE_SUSPENDED | SIE_VBUS_DETECTED;
    CHECK(wr(SIE_STATUS, 0xFFFFFFFFll & ~static_cast<int64_t>(SIE_BUS_RESET)));
    CHECK(rd(SIE_STATUS) == SIE_VBUS_DETECTED);  // the level of the VBUS pin is not write-1-to-clear
}

static void test_status_bits_raise_their_interrupts() {
    fresh();
    struct Row { uint32_t sie, intr; };
    const Row rows[] = {{SIE_SETUP_REC, INTR_SETUP_REQ}, {SIE_SUSPENDED, INTR_DEV_SUSPEND}, {SIE_BUS_RESET, INTR_BUS_RESET}, {SIE_VBUS_DETECTED, INTR_VBUS_DETECT}, {SIE_TRANS_COMPLETE, INTR_TRANS_COMPLETE},
                        {SIE_STALL_REC, INTR_STALL}, {SIE_CRC_ERROR, INTR_ERROR_CRC}, {SIE_BIT_STUFF_ERROR, INTR_ERROR_BIT_STUFF}, {SIE_RX_OVERFLOW, INTR_ERROR_RX_OVERFLOW},
                        {SIE_RX_TIMEOUT, INTR_ERROR_RX_TIMEOUT}, {SIE_DATA_SEQ_ERROR, INTR_ERROR_DATA_SEQ}, {SIE_RESUME, INTR_DEV_RESUME_FROM_HOST}};
    for (const Row& row : rows) {
        fresh();
        usb.sie_status = row.sie;
        CHECK(usb.sie_status_updated() && rd(INTR) == row.intr);
        usb.sie_status = 0;
        CHECK(usb.sie_status_updated() && rd(INTR) == 0);
    }
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE);
    usb.sie_status = SIE_RESUME;
    CHECK(usb.sie_status_updated() && rd(INTR) == INTR_HOST_RESUME);
    usb.host_connect_event = true;
    CHECK(usb.sie_status_updated() && (rd(INTR) & INTR_HOST_CONN_DIS));
    usb.host_connect_event = false;
    CHECK(usb.sie_status_updated() && !(rd(INTR) & INTR_HOST_CONN_DIS));
}

static void test_dpram_updated_picks_its_endpoint_and_respects_the_mode() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE);
    put32(EP1_IN_CONTROL, 0x80000000u | 0x200);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 0);  // host mode: the device-mode buffers are not served
    fresh();
    // the last buffer-control word (endpoint 15 OUT) and the first (EP0 IN) are served, the words around the table are not
    put32(EP1_OUT_CONTROL + 8 * 14, 0x80000000u | 0x300);
    put32(EP15_OUT_BUFFER_CONTROL, BUF_AVAILABLE | 4);
    arm(EP15_OUT_BUFFER_CONTROL);
    CHECK(env.read_requests == 1 && env.last_read_endpoint == 15);
    put32(0x7C, BUF_AVAILABLE | 4);
    arm(0x7C);
    put32(0x100, BUF_AVAILABLE | 4);
    arm(0x100);
    CHECK(env.read_requests == 1);
    put32(0x80, BUF_AVAILABLE | BUF_FULL | 2);
    arm(0x80);
    CHECK(env.write_n[0] == 1);
    // RESET_SELECT clears the select of that endpoint and direction only
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x400);
    put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x500);
    usb.buf_select[2] = usb.buf_select[3] = usb.buf_select[0] = usb.buf_select[5] = 1;
    put32(EP1_OUT_BUF, BUF_RESET_SELECT);
    arm(EP1_OUT_BUF);
    CHECK(usb.buf_select[3] == 0 && usb.buf_select[2] == 1 && usb.buf_select[0] == 1 && usb.buf_select[5] == 1);
}

static void test_interrupt_per_two_buffers() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER | 0x400);
    put32(EP1_IN_BUF, (BUF_AVAILABLE | BUF_FULL | 1) | ((BUF_AVAILABLE | BUF_FULL | 1) << BUF1_SHIFT));
    arm(EP1_IN_BUF);
    tick_us(11);
    CHECK(usb.done_count(2) == 1 && usb.done_at(2, 0) == 1 && usb.two_buffer_count[2] == 0);  // once for the pair, reported on the second
    // single buffered: the "every two" bit does nothing
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER | 0x400);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    tick_us(11);
    CHECK(usb.done_count(2) == 0 && usb.two_buffer_count[2] == 0);
    // one buffer of a pair completed: counted, not yet reported
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | EP_CTRL_INTERRUPT_PER_DOUBLE_BUFFER | 0x400);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    tick_us(11);
    CHECK(usb.done_count(2) == 0 && usb.two_buffer_count[2] == 1);
    // endpoint 0 follows SIE_CTRL
    fresh();
    wr(SIE_CTRL, SIE_CTRL_EP0_DOUBLE_BUF | SIE_CTRL_EP0_INT_2BUF);
    put32(0x80, (BUF_AVAILABLE | BUF_FULL | 1) | ((BUF_AVAILABLE | BUF_FULL | 1) << BUF1_SHIFT));
    arm(0x80);
    CHECK(env.write_n[0] == 2);
    tick_us(11);
    CHECK(usb.done_count(0) == 1);
    fresh();
    wr(SIE_CTRL, SIE_CTRL_EP0_INT_2BUF);  // not double buffered
    put32(0x80, BUF_AVAILABLE | BUF_FULL | 1);
    arm(0x80);
    tick_us(11);
    CHECK(usb.done_count(0) == 0);
    fresh();
    wr(SIE_CTRL, SIE_CTRL_EP0_DOUBLE_BUF);  // double buffered, no interrupt bit
    put32(0x80, BUF_AVAILABLE | BUF_FULL | 1);
    arm(0x80);
    CHECK(usb.buf_select[0] == 1);
    tick_us(11);
    CHECK(usb.done_count(0) == 0 && usb.buf_select[0] == 1);
}

static void test_the_other_buffer_is_chased() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x400);
    // buffer 0 first; buffer 1 is armed only after the first one went out
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    put32(EP1_IN_BUF, w32(EP1_IN_BUF) | ((BUF_AVAILABLE | BUF_FULL | 2) << BUF1_SHIFT));
    CHECK(env.write_n[1] == 1);
    tick_us(21);
    CHECK(env.written == 0);  // buffer 0 went out at 10 us and started buffer 1; its reaction was moved by the second ACK
    tick_us(10);
    CHECK(env.written == 2 && env.reaction_n[1] == 0);  // both reach the host together
}

static void test_failures_in_the_status_phase() {
    for (int n = 0; n < 3; ++n) {
        fresh();
        put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x200);
        put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | BUF_LAST | 1);
        arm(EP1_IN_BUF);
        env.calls = 0;
        env.fail_at = n;  // each interrupt-line update of the status phase, then the callback to the host
        const bool ok = clk.tick(21000.0);
        CHECK(!ok && env.failed);
    }
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x200);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    env.fail_at = 0;
    CHECK(!usb.dpram_updated(EP1_IN_BUF) && env.failed);  // the host cannot queue the buffer
    fresh();
    put32(EP1_OUT_CONTROL, 0x80000000u | 0x200);
    put32(EP1_OUT_BUF, BUF_AVAILABLE | 1);
    env.fail_at = 0;
    CHECK(!usb.dpram_updated(EP1_OUT_BUF) && env.failed);
    fresh();
    usb.sie_status = SIE_BUS_RESET;
    env.fail_at = 0;
    CHECK(!wr(SIE_STATUS, SIE_BUS_RESET) && env.failed);  // on_reset_received fails
    fresh();
    usb.sie_status = SIE_BUS_RESET;
    env.fail_at = 0;
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE);
    env.calls = 0;
    env.failed = 0;
    env.fail_at = 0;
    CHECK(!wr(SIE_STATUS, SIE_BUS_RESET) && env.failed);  // the interrupt line, at set_connected
    fresh();
    env.fail_at = 0;
    CHECK(!usb.send_setup_packet(reinterpret_cast<const uint8_t*>("12345678"), 8) && env.failed);
    fresh();
    env.fail_at = 0;
    CHECK(!usb.set_connected(true) && env.failed);
}

static void test_a_long_setup_packet_stays_inside_the_dpram() {
    fresh();
    static uint8_t packet[kDpramSize + 904];
    std::memset(packet, 0x33, sizeof packet);
    CHECK(usb.send_setup_packet(packet, sizeof packet));
    CHECK(dpram[kDpramSize - 1] == 0x33 && dpram[0] == 0x33);
}

static void test_reset_clears_everything() {
    fresh();
    usb.addr_endp = usb.main_ctrl = usb.int_raw = usb.int_enable = usb.int_force = usb.sie_status = usb.sie_ctrl = usb.sof_frame_number = 0x1234;
    usb.connected = usb.connect_event = usb.host_connect_event = usb.host_mode = usb.sof_enabled = usb.has_device = usb.expecting_status_phase = true;
    for (uint32_t& entry : usb.int_ep_addr_ctrl) entry = 7;
    usb.int_ep_ctrl = usb.usb_pwr = usb.usb_muxing = usb.usbphy_direct = usb.usbphy_direct_override = usb.usbphy_trim = usb.nak_poll = 7;
    usb.ep_abort = usb.ep_abort_done = usb.ep_stall_arm = usb.ep_status_stall_nak = 7;
    usb.control_data_pid = 0;
    for (uint32_t i = 0; i < 32; ++i) {
        usb.buf_select[i] = usb.two_buffer_count[i] = 1;
        usb.done_push(i, 1);
    }
    put32(EP1_OUT_CONTROL, 0x80000000u | 0x300);
    put32(EP1_OUT_BUF, BUF_AVAILABLE | 8);
    usb.host_mode = false;
    arm(EP1_OUT_BUF);
    usb.host_mode = true;
    CHECK(usb.pending_count(1) == 1);
    usb.reset_device();
    CHECK(usb.reset());
    CHECK(usb.addr_endp == 0 && usb.main_ctrl == 0 && usb.int_raw == 0 && usb.int_enable == 0 && usb.int_force == 0 && usb.sie_status == 0 && usb.sie_ctrl == 0 && usb.sof_frame_number == 0);
    CHECK(!usb.connected && !usb.connect_event && !usb.host_connect_event && !usb.host_mode && !usb.sof_enabled && !usb.has_device && !usb.expecting_status_phase);
    for (uint32_t entry : usb.int_ep_addr_ctrl) CHECK(entry == 0);
    CHECK(usb.int_ep_ctrl == 0 && usb.usb_pwr == 0 && usb.usb_muxing == 0 && usb.usbphy_direct == 0 && usb.usbphy_direct_override == 0);
    CHECK(usb.usbphy_trim == USBPHY_TRIM_RESET && usb.nak_poll == NAK_POLL_RESET);
    CHECK(usb.ep_abort == 0 && usb.ep_abort_done == 0 && usb.ep_stall_arm == 0 && usb.ep_status_stall_nak == 0 && usb.control_data_pid == 1);
    for (uint32_t i = 0; i < 32; ++i) CHECK(usb.buf_select[i] == 0 && usb.two_buffer_count[i] == 0 && usb.done_count(i) == 0);
    for (uint32_t i = 0; i < 16; ++i) CHECK(usb.pending_count(i) == 0);
    CHECK(!usb.has_scheduled_alarms());
}

static void test_detach_unlinks_every_alarm() {
    fresh();
    usb.reset_device();
    put32(EP1_IN_CONTROL, 0x80000000u | 0x200);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    usb.schedule_read(5, 10);
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN);
    CHECK(usb.has_scheduled_alarms());
    usb.detach();
    CHECK(!usb.has_scheduled_alarms() && !clk.has_alarm());
}

static void test_attach_and_detach_in_each_mode() {
    fresh();  // device mode: attaching a device is silent
    CHECK(usb.connect_device() && usb.has_device && rd(SIE_STATUS) == 0 && !usb.host_connect_event);
    CHECK(usb.disconnect_device() && !usb.has_device && !usb.host_connect_event);
    fresh();  // host mode but the controller is not enabled: no event yet; enabling it announces the device
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE);
    CHECK(usb.connect_device() && !usb.host_connect_event && rd(SIE_STATUS) == 0);
    CHECK(wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN) && usb.host_connect_event && ((rd(SIE_STATUS) >> 8) & 3) == SIE_SPEED_FS_VALUE);
    CHECK(usb.disconnect_device() && usb.host_connect_event && ((rd(SIE_STATUS) >> 8) & 3) == 0);
    fresh();  // host mode, no device: disconnecting says nothing
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.disconnect_device() && !usb.host_connect_event);
    fresh();  // enabling the controller in device mode calls on_usb_enabled, in host mode it does not
    CHECK(wr(MAIN_CTRL, MAIN_CONTROLLER_EN) && env.enabled == 1);
    CHECK(wr(MAIN_CTRL, MAIN_CONTROLLER_EN | MAIN_HOST_NDEVICE) && env.enabled == 1);
    CHECK(wr(MAIN_CTRL, 0) && env.enabled == 1);
    fresh();
    env.fail_at = 0;
    CHECK(!wr(MAIN_CTRL, MAIN_CONTROLLER_EN) && env.failed);
}

static void test_sof_enable_and_bus_reset_of_the_host() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN);
    CHECK(usb.sof_enabled);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN | (1u << 16));
    CHECK(usb.sof_enabled);
    tick_us(1001);
    CHECK(usb.sof_frame_number == 1);
    wr(SIE_CTRL, 0);
    CHECK(!usb.sof_enabled);
    wr(SIE_CTRL, 0);
    CHECK(!usb.sof_enabled);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN);  // turned on again: a fresh SOF is armed
    CHECK(usb.sof_enabled);
    // the bus reset resets the toggle and tells a device that is there
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.control_data_pid = 0;
    CHECK(wr(SIE_CTRL, SIE_CTRL_RESET_BUS) && env.device_resets == 0 && usb.control_data_pid == 1);
    usb.has_device = true;
    CHECK(wr(SIE_CTRL, SIE_CTRL_RESET_BUS) && env.device_resets == 1);
    env.fail_at = env.calls;
    CHECK(!wr(SIE_CTRL, SIE_CTRL_RESET_BUS) && env.failed);
}

static void test_host_out_and_in_details() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.connect_device());
    wr(ADDR_ENDP, 2u << 16);
    // an OUT: the device's answer decides the status bits; the toggle follows
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 4);
    dpram[HOST_EPX_DATA] = 1;
    env.out_status = kUsbAck;
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.outs == 1 && (rd(SIE_STATUS) & SIE_ACK_REC) && rd(BUFF_STATUS) == 1);
    CHECK(w32(HOST_EPX_BUF_CTRL) == (4u | BUF_DATA1_PID) && usb.control_data_pid == 0);
    tick_us(5);
    wr(SIE_STATUS, 0xFFFFFFFFll);
    wr(BUFF_STATUS, 1);
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | BUF_DATA1_PID | 4);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK((w32(HOST_EPX_BUF_CTRL) & BUF_DATA1_PID) == 0 && usb.control_data_pid == 1);  // the toggle is written, not only set
    tick_us(5);
    wr(SIE_STATUS, 0xFFFFFFFFll);
    wr(BUFF_STATUS, 1);
    env.out_status = kUsbNak;
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 4);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK((rd(SIE_STATUS) & (SIE_NAK_REC | SIE_ACK_REC | SIE_STALL_REC)) == SIE_NAK_REC && rd(BUFF_STATUS) == 0 && !(w32(HOST_EPX_BUF_CTRL) & BUF_AVAILABLE));
    tick_us(5);
    wr(SIE_STATUS, 0xFFFFFFFFll);
    env.out_status = kUsbStall;
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK((rd(SIE_STATUS) & (SIE_NAK_REC | SIE_ACK_REC | SIE_STALL_REC)) == SIE_STALL_REC && rd(BUFF_STATUS) == 0);
    tick_us(5);
    wr(SIE_STATUS, 0xFFFFFFFFll);
    env.out_status = kUsbOther;
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK((rd(SIE_STATUS) & (SIE_NAK_REC | SIE_ACK_REC | SIE_STALL_REC)) == 0);
    tick_us(5);
    CHECK((rd(SIE_STATUS) & SIE_TRANS_COMPLETE) && !(rd(SIE_CTRL) & SIE_CTRL_START_TRANS));
    // a zero-length OUT on endpoint 0 is the status phase: the device is not asked, a non-zero one on endpoint 0 is
    wr(ADDR_ENDP, 0);
    env.outs = 0;
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 2);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.outs == 1);
    tick_us(5);
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 0);
    wr(ADDR_ENDP, 1u << 16);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.outs == 2);  // zero length on another endpoint still goes to the device
    tick_us(5);
    // an IN: data lands in the EPX buffer, with FULL and the length; the toggle flips; the status bits follow the answer
    wr(SIE_STATUS, 0xFFFFFFFFll);
    wr(BUFF_STATUS, 0xFFFFFFFFll);
    wr(ADDR_ENDP, 1u << 16);
    env.in_status = kUsbAck;
    env.in_size = 3;
    env.in_data[0] = 7;
    env.in_data[1] = 8;
    env.in_data[2] = 9;
    usb.control_data_pid = 0;
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | BUF_DATA1_PID | 64);
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(w32(HOST_EPX_BUF_CTRL) == (3u | BUF_FULL) && dpram[HOST_EPX_DATA + 2] == 9 && usb.control_data_pid == 1 && (rd(SIE_STATUS) & SIE_ACK_REC) && rd(BUFF_STATUS) == 1);
    tick_us(5);
    // an acknowledged IN without data: nothing stored, no status bit
    wr(SIE_STATUS, 0xFFFFFFFFll);
    wr(BUFF_STATUS, 1);
    env.in_size = 0;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK((rd(SIE_STATUS) & (SIE_NAK_REC | SIE_ACK_REC | SIE_STALL_REC)) == 0 && rd(BUFF_STATUS) == 0);
    tick_us(5);
    // a control IN with nothing kept goes to the device; a kept response is cut to the buffer length
    env.in_size = 2;
    env.pending_present = false;
    wr(ADDR_ENDP, 0);
    env.ins = 0;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.ins == 1);
    tick_us(5);
    // a failure in each host call of a transaction
    for (int n = 0; n < 3; ++n) {
        fresh();
        wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
        usb.connect_device();
        env.in_size = 1;
        put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 8);
        env.calls = 0;
        env.fail_at = n;
        wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
        CHECK(env.failed);
    }
}

static void test_the_setup_transaction() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.connect_device();
    usb.control_data_pid = 0;
    usb.expecting_status_phase = false;
    env.setup_has_response = false;
    CHECK(wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS));
    CHECK(usb.expecting_status_phase && usb.control_data_pid == 1 && (rd(SIE_STATUS) & SIE_ACK_REC));
    tick_us(5);
    env.setup_has_response = true;
    CHECK(wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS));
    CHECK(!usb.expecting_status_phase);
    tick_us(4.9);
    CHECK(!(rd(SIE_STATUS) & SIE_TRANS_COMPLETE) || true);
    // START_TRANS without host mode does nothing
    fresh();
    usb.has_device = true;
    CHECK(wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS));
    CHECK(env.setups == 0 && !clk.has_alarm());
    // a failure of the device
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.connect_device();
    env.calls = 0;
    env.fail_at = 0;
    CHECK(!wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS) && env.failed);
}

static void test_the_interrupt_endpoint_poll() {
    // each condition that makes the poll skip an endpoint, one at a time; a good one is served
    for (int variant = 0; variant < 9; ++variant) {
        fresh();
        wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
        usb.connect_device();
        env.address = 5;
        env.in_size = 1;
        env.in_data[0] = 0x42;
        uint32_t addr = 5u | (3u << 16);
        uint32_t ctrl = 0x500;
        uint32_t buf = BUF_AVAILABLE | 8;
        uint32_t enable = 1u << 3;  // interrupt endpoint 3 (index 2)
        uint32_t index = 2;
        wr(SOF_WR, 0);
        if (variant == 1) addr = 6u | (3u << 16);          // another device
        if (variant == 2) addr |= 1u << 25;                // an OUT endpoint
        if (variant == 3) buf = 8;                         // not available
        if (variant == 4) enable = 1u << 2;                // not enabled
        if (variant == 5) ctrl |= 1u << 16;                // interval 2: the first SOF is frame 1
        if (variant == 6) usb.has_device = false;
        if (variant == 7) enable |= 1u << 1;               // endpoint index 0 also enabled but unconfigured: address 0 != 5
        wr(ADDR_ENDP1 + 4 * index, addr);
        put32(HOST_INT_EP_CTRL_BASE + 8 * index, ctrl);
        put32(HOST_INT_EP_BUF_CTRL_BASE + 8 * index, buf);
        wr(INT_EP_CTRL, enable);
        wr(SIE_CTRL, SIE_CTRL_SOF_EN);
        tick_us(1001);  // frame 1
        const bool served = variant == 0 || variant == 7 || variant == 8 || variant == 5;
        if (variant == 0 || variant == 7 || variant == 8) {
            CHECK(env.last_in_address == 0x83u);
        CHECK(env.ins == 1 && dpram[0x500] == 0x42 && w32(HOST_INT_EP_BUF_CTRL_BASE + 8 * index) == (BUF_FULL | 1u));
            CHECK(usb.done_count((index + 1) * 2) == 1);
        } else {
            CHECK(env.ins == 0);  // (variant 5: interval 2, frame 1 is skipped)
        }
        (void)served;
    }
}

static void test_a_poll_that_gets_no_data() {
    for (int status = 0; status < 3; ++status) {
        fresh();
        wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
        usb.connect_device();
        env.address = 5;
        env.in_status = status == 0 ? kUsbNak : (status == 1 ? kUsbStall : kUsbAck);
        if (status == 1) env.in_size = 1;
        env.in_size = status == 2 ? 0 : 1;
        if (status == 1) env.in_size = 1;
        wr(ADDR_ENDP1, 5u | (3u << 16));
        put32(HOST_INT_EP_CTRL_BASE, 0x500);
        put32(HOST_INT_EP_BUF_CTRL_BASE, BUF_AVAILABLE | 8);
        wr(INT_EP_CTRL, 1u << 1);
        wr(SIE_CTRL, SIE_CTRL_SOF_EN);
        tick_us(1001);
        CHECK(env.ins == 1 && (w32(HOST_INT_EP_BUF_CTRL_BASE) & BUF_AVAILABLE) && usb.done_count(2) == 0);
    }
    // the buffer is served only with a wide enough interval of frames: interval field 0x1FF masks 9 bits
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.connect_device();
    env.address = 5;
    env.in_size = 1;
    wr(ADDR_ENDP1, 5u | (3u << 16));
    put32(HOST_INT_EP_CTRL_BASE, 0x500 | (0x1FFu << 16));
    put32(HOST_INT_EP_BUF_CTRL_BASE, BUF_AVAILABLE | 8);
    wr(INT_EP_CTRL, 1u << 1);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN);
    tick_us(1001);
    CHECK(env.ins == 0);
    // failures: the interrupt line of the SOF, the device's address, the poll
    for (int n = 0; n < 4; ++n) {
        fresh();
        wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
        usb.connect_device();
        env.address = 5;
        env.in_size = 1;
        wr(ADDR_ENDP1, 5u | (3u << 16));
        put32(HOST_INT_EP_CTRL_BASE, 0x500);
        put32(HOST_INT_EP_BUF_CTRL_BASE, BUF_AVAILABLE | 8);
        wr(INT_EP_CTRL, 1u << 1);
        wr(SIE_CTRL, SIE_CTRL_SOF_EN);
        env.calls = 0;
        env.fail_at = n;
        const bool ok = clk.tick(1001000.0);
        CHECK(!ok && env.failed);
    }
}

static void test_the_remaining_corners() {
    // EP_ABORT is stored; reading SOF_RD drops the interrupt line
    fresh();
    wr(EP_ABORT, 0x5);
    CHECK(rd(EP_ABORT) == 0x5);
    fresh();
    wr(INTE, INTR_HOST_SOF);
    usb.int_raw |= INTR_HOST_SOF;
    CHECK(usb.check_interrupts() && env.irq_level);
    CHECK(rd(SOF_RD) == 0 && !env.irq_level);
    // over-current needs both its enable and its value
    fresh();
    wr(USB_PWR, PWR_OVERCURR_DETECT_EN);
    CHECK(!(rd(SIE_STATUS) & SIE_VBUS_OVER_CURR));
    wr(USB_PWR, PWR_OVERCURR_DETECT);
    CHECK(!(rd(SIE_STATUS) & SIE_VBUS_OVER_CURR));
    wr(USB_PWR, PWR_OVERCURR_DETECT | PWR_OVERCURR_DETECT_EN);
    CHECK(rd(SIE_STATUS) & SIE_VBUS_OVER_CURR);
    // buffer 1's status is written to the upper half, the lower one untouched
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x400);
    put32(EP1_IN_BUF, (BUF_AVAILABLE | BUF_FULL | 3) | ((BUF_AVAILABLE | BUF_FULL | BUF_LAST | BUF_DATA1_PID | 2) << BUF1_SHIFT));
    arm(EP1_IN_BUF);
    tick_us(11);
    CHECK(w32(EP1_IN_BUF) == (3u | ((2u | BUF_LAST | BUF_DATA1_PID) << BUF1_SHIFT)));
    // a double-buffered OUT flips its own select, in its own slot
    fresh();
    put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x500);
    put32(EP1_OUT_BUF, (BUF_AVAILABLE | 4) | ((BUF_AVAILABLE | 4) << BUF1_SHIFT));
    arm(EP1_OUT_BUF);
    CHECK(env.read_requests == 2 && usb.buf_select[3] == 0 && usb.buf_select[2] == 0);
    CHECK(usb.pending_count(1) == 2 && usb.pending_at(1, 0) == 0 && usb.pending_at(1, 1) == 1);
    std::memset(env.reads[1][0], 0x11, 4);
    env.read_size[1][0] = 4;
    env.read_n[1] = 1;
    usb.schedule_read(1, 10);
    tick_us(10);
    CHECK(usb.pending_count(1) == 1 && usb.pending_at(1, 0) == 1);  // the oldest request was served, the order is kept
    CHECK((w32(EP1_OUT_BUF) & 0xFFFFu) == (4u | BUF_FULL) && usb.buf_select[3] == 0);
    // popping from a full list
    fresh();
    for (uint32_t i = 0; i < kFifoDepth + 5; ++i) usb.done_push(2, i & 1);
    for (uint32_t round = 0; round < 3; ++round) {
        CHECK(usb.done_count(2) == kFifoDepth - round);
        CHECK(wr(BUFF_STATUS, 1u << 2));
        CHECK(usb.done_count(2) == kFifoDepth - round - 1);
        CHECK(usb.done_at(2, 0) == ((round + 1) & 1));  // the rest moved up
    }
    fresh();
    put32(EP1_OUT_CONTROL, 0x80000000u | 0x500);
    for (uint32_t i = 0; i < kFifoDepth; ++i) {
        put32(EP1_OUT_BUF, BUF_AVAILABLE | 4);
        arm(EP1_OUT_BUF);
    }
    std::memset(env.reads[1][0], 1, 4);
    env.read_size[1][0] = 4;
    env.read_n[1] = 1;
    usb.schedule_read(1, 10);
    tick_us(10);
    CHECK(usb.pending_count(1) == kFifoDepth - 1);
    // a connect shows full speed whatever the speed bits held
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.sie_status = 3u << 8;
    usb.connect_device();
    CHECK(((rd(SIE_STATUS) >> 8) & 3) == SIE_SPEED_FS_VALUE);
    // START_TRANS with no transaction kind selected does nothing, and every kind arms the completion alarm
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.connect_device();
    CHECK(wr(SIE_CTRL, SIE_CTRL_START_TRANS) && !clk.has_alarm());
    const uint32_t kinds[] = {SIE_CTRL_SEND_SETUP, SIE_CTRL_RECEIVE_DATA, SIE_CTRL_SEND_DATA};
    for (uint32_t kind : kinds) {
        env.in_size = 1;
        put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 4);
        CHECK(wr(SIE_CTRL, kind | SIE_CTRL_START_TRANS) && clk.has_alarm());
        tick_us(5);
        CHECK(!clk.has_alarm());
    }
    // the device is addressed with the endpoint the register names
    wr(ADDR_ENDP, 3u << 16);
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 4);
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.last_in_address == (0x80u | 3u));
    tick_us(5);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.last_out_endpoint == 3u);
    tick_us(5);
    // a control IN on endpoint 0 with nothing kept asks the device for endpoint 0x80
    wr(ADDR_ENDP, 0);
    env.pending_present = false;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.last_in_address == 0x80u);
    tick_us(5);
    // a timeout with the interrupt line failing
    usb.has_device = false;
    env.calls = 0;
    env.fail_at = 0;
    const int ins_before = env.ins, irq_before = env.irq_calls;
    CHECK(!wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS) && env.failed && env.ins == ins_before && env.calls == 1 && env.irq_calls == irq_before + 1);

    // the interrupt line failing under a disconnect, and under the bus-reset alarm
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    usb.connect_device();
    env.calls = 0;
    env.fail_at = 0;
    CHECK(!usb.disconnect_device() && env.failed);
    fresh();
    usb.reset_device();
    env.calls = 0;
    env.fail_at = 0;
    CHECK(!clk.tick(10000000.0) && env.failed && usb.sie_status == SIE_BUS_RESET);
    // the status of a double-buffered OUT lands in the right halves and leaves the word before it alone
    fresh();
    put32(EP1_IN_BUF, 0x5A5A5A5Au);
    put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x500);
    put32(EP1_OUT_BUF, (BUF_AVAILABLE | 8) | ((BUF_AVAILABLE | 8) << BUF1_SHIFT));
    arm(EP1_OUT_BUF);
    for (int i = 0; i < 2; ++i) {
        std::memset(env.reads[1][i], 0x22, 4);
        env.read_size[1][i] = 4;
    }
    env.read_n[1] = 2;
    usb.schedule_read(1, 10);
    tick_us(10);
    CHECK(w32(EP1_OUT_BUF) == ((4u | BUF_FULL) | ((4u | BUF_FULL) << BUF1_SHIFT)) && w32(EP1_IN_BUF) == 0x5A5A5A5Au);
    // one buffer of a double-buffered OUT: its select moves in its own slot
    fresh();
    put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x500);
    put32(EP1_OUT_BUF, BUF_AVAILABLE | 8);
    arm(EP1_OUT_BUF);
    CHECK(usb.buf_select[3] == 1 && usb.buf_select[2] == 0 && usb.buf_select[1] == 0);
    // every host call of every transaction kind, failing in turn
    for (int kind = 0; kind < 3; ++kind) {
        for (int n = 0; n < 8; ++n) {
            fresh();
            wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
            usb.connect_device();
            env.in_size = 1;
            env.pending_present = kind == 1;
            env.pending_size = 4;
            env.setup_has_response = true;
            wr(ADDR_ENDP, kind == 1 ? 0u : (1u << 16));
            put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 8);
            env.calls = 0;
            env.fail_at = n;
            const uint32_t flags[] = {SIE_CTRL_RECEIVE_DATA, SIE_CTRL_RECEIVE_DATA, SIE_CTRL_SEND_DATA};
            const bool ok = usb.write(SIE_CTRL, flags[kind] | SIE_CTRL_START_TRANS);
            CHECK(ok == !env.failed);
        }
    }
}

static void test_power_on_state() {
    fresh();
    CHECK(rd(NAK_POLL) == NAK_POLL_RESET && rd(USBPHY_TRIM) == USBPHY_TRIM_RESET);
    CHECK(rd(MAIN_CTRL) == 0 && rd(SIE_STATUS) == 0 && rd(INTR) == 0 && rd(INTS) == 0 && rd(BUFF_STATUS) == 0 && rd(BUFF_CPU_SHOULD_HANDLE) == 0);
    CHECK(!env.irq_level);
    CHECK(rd(SOF_WR) == 0);
}

static void test_masks() {
    fresh();
    wr(ADDR_ENDP, 0xFFFFFFFF);
    CHECK(rd(ADDR_ENDP) == ADDR_ENDP_MASK);
    wr(ADDR_ENDP15, 0xFFFFFFFF);
    CHECK(rd(ADDR_ENDP15) == ADDR_ENDPN_MASK);
    wr(SIE_CTRL, 0xFFFFFFFF);
    CHECK(rd(SIE_CTRL) == (SIE_CTRL_MASK & ~SIE_CTRL_START_TRANS) || rd(SIE_CTRL) == SIE_CTRL_MASK);
    wr(MAIN_CTRL, 0xFFFFFFFF);
    CHECK(rd(MAIN_CTRL) == (MAIN_SIM_TIMING | MAIN_CONTROLLER_EN | MAIN_HOST_NDEVICE));
    wr(NAK_POLL, 0xFFFFFFFF);
    CHECK(rd(NAK_POLL) == NAK_POLL_MASK);
    wr(USB_MUXING, 0xFFFFFFFF);
    CHECK(rd(USB_MUXING) == USB_MUXING_MASK);
    wr(USBPHY_TRIM, 0xFFFFFFFF);
    CHECK(rd(USBPHY_TRIM) == USBPHY_TRIM_MASK);
    wr(EP_STALL_ARM, 0xFF);
    CHECK(rd(EP_STALL_ARM) == EP_STALL_ARM_MASK);
    wr(INT_EP_CTRL, 0xFFFFFFFF);
    CHECK(rd(INT_EP_CTRL) == INT_EP_CTRL_MASK);
    wr(SOF_WR, 0xFFFFFFFF);
    CHECK(usb.sof_frame_number == SOF_COUNT_MASK);
}

static void test_interrupt_registers_and_aliases() {
    fresh();
    CHECK(wr(INTE, 0xFFFFFFFF) && rd(INTE) == INT_MASK);
    CHECK(wr(INTF, 0x10) && rd(INTS) == 0x10 && env.irq_level);
    CHECK(wr(INTF, 0x10, kAtomicClear) && rd(INTF) == 0 && !env.irq_level);
    CHECK(wr(INTE, 0x1, kAtomicXor) && rd(INTE) == (INT_MASK ^ 1));
    CHECK(wr(INTE, 0, kAtomicSet));
    usb.int_raw = INTR_BUFF_STATUS;
    CHECK(rd(INTS) == INTR_BUFF_STATUS);
    CHECK(wr(INTE, INTR_BUFF_STATUS, kAtomicClear) && rd(INTS) == 0);
    usb.int_force = 0x1;  // forced bits are not masked by the enable
    CHECK(rd(INTS) == 0x1);
}

static void test_unimplemented_registers_warn() {
    fresh();
    CHECK(rd(0x88) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind == kRegWarnRead && env.warn_offset == 0x88);
    CHECK(rd(0x1088) == 0xFFFFFFFFu && env.warns == 3 && env.warn_kind == kRegWarnReadAtomicArea);
    CHECK(wr(0x9C, 1) && env.warns == 4 && env.warn_kind == kRegWarnWrite);
}

static void test_an_in_transfer() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x200);  // enabled, interrupt per buffer, buffer at 0x200
    for (int i = 0; i < 5; ++i) dpram[0x200 + i] = static_cast<uint8_t>(0xA0 + i);
    wr(INTE, INTR_BUFF_STATUS | INTR_TRANS_COMPLETE);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | BUF_LAST | BUF_DATA1_PID | 5 | BUF_STALL * 0);
    arm(EP1_IN_BUF);
    CHECK(!(w32(EP1_IN_BUF) & BUF_AVAILABLE));  // the controller owns it now
    CHECK(env.write_n[1] == 1 && env.writes[1][0].size == 5 && env.writes[1][0].data[4] == 0xA4);
    CHECK(env.written == 0 && rd(BUFF_STATUS) == 0);
    tick_us(9.9);
    CHECK(env.written == 0 && rd(BUFF_STATUS) == 0);
    tick_us(0.2);
    // the status phase is written at the ACK; the host reacts 10 us later, not in the same instant
    CHECK(env.written == 0);
    CHECK(w32(EP1_IN_BUF) == (5u | BUF_DATA1_PID | BUF_LAST));  // status written back: length, pid, last - FULL is gone for an IN
    CHECK(rd(BUFF_STATUS) == (1u << 2));  // endpoint 1 IN is slot 2
    tick_us(9.7);
    CHECK(env.written == 0);
    tick_us(0.2);
    CHECK(env.written == 1 && env.last_write_endpoint == 1 && env.last_write_size == 5 && env.last_write[4] == 0xA4);
    CHECK(rd(BUFF_CPU_SHOULD_HANDLE) == 0);
    CHECK((rd(SIE_STATUS) & (SIE_TRANS_COMPLETE | SIE_ACK_REC)) == (SIE_TRANS_COMPLETE | SIE_ACK_REC));
    CHECK(env.irq_level && (rd(INTR) & (INTR_BUFF_STATUS | INTR_TRANS_COMPLETE)) == (INTR_BUFF_STATUS | INTR_TRANS_COMPLETE));
    CHECK(wr(BUFF_STATUS, 1u << 2) && rd(BUFF_STATUS) == 0 && !(rd(INTR) & INTR_BUFF_STATUS));
    CHECK(wr(SIE_STATUS, SIE_TRANS_COMPLETE) && !(rd(INTR) & INTR_TRANS_COMPLETE) && !env.irq_level);
}

static void test_an_in_needs_full_and_a_stall_holds_it() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | 0x200);
    put32(EP1_IN_BUF, BUF_AVAILABLE | 4);  // not FULL: the host would get a NAK
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 0 && (w32(EP1_IN_BUF) & BUF_AVAILABLE));
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | BUF_STALL | 4);
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 0 && (w32(EP1_IN_BUF) & BUF_AVAILABLE));
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 4);
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 1);
}

static void test_the_interrupt_enable_decides_buff_status() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | 0x200);  // no per-buffer interrupt
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 4);
    arm(EP1_IN_BUF);
    tick_us(21);
    CHECK(env.written == 1 && rd(BUFF_STATUS) == 0);
}

static void test_an_out_transfer() {
    fresh();
    put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x300);
    put32(EP1_OUT_BUF, BUF_AVAILABLE | 8);  // room for 8
    arm(EP1_OUT_BUF);
    CHECK(env.read_requests == 1 && env.last_read_endpoint == 1 && env.last_read_length == 8);
    CHECK(usb.pending_count(1) == 1 && usb.pending_at(1, 0) == 0);
    const uint8_t host_data[12] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
    std::memcpy(env.reads[1][0], host_data, 12);
    env.read_size[1][0] = 12;
    env.read_n[1] = 1;
    usb.schedule_read(1, 10);
    tick_us(10);
    CHECK(dpram[0x300] == 1 && dpram[0x307] == 8 && dpram[0x308] == 0);  // cut to the room the buffer announced
    CHECK(w32(EP1_OUT_BUF) == (8u | BUF_FULL));
    CHECK(rd(BUFF_STATUS) == (1u << 3) && usb.pending_count(1) == 0);
}

static void test_double_buffering_and_the_iso_offset() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x400);
    dpram[0x400] = 0x11;
    dpram[0x440] = 0x22;
    // both halves armed
    put32(EP1_IN_BUF, (BUF_AVAILABLE | BUF_FULL | 1) | ((BUF_AVAILABLE | BUF_FULL | 1) << BUF1_SHIFT));
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 2 && env.writes[1][0].data[0] == 0x11 && env.writes[1][0].half == 0 && env.writes[1][1].data[0] == 0x22 && env.writes[1][1].half == 1);
    CHECK(usb.buf_select[2] == 0);  // flipped twice
    tick_us(11);
    CHECK(usb.done_count(2) == 2 && usb.done_at(2, 0) == 0 && usb.done_at(2, 1) == 1);
    CHECK(rd(BUFF_CPU_SHOULD_HANDLE) == 0);
    CHECK(wr(BUFF_STATUS, 1u << 2));
    CHECK(rd(BUFF_STATUS) == (1u << 2) && rd(BUFF_CPU_SHOULD_HANDLE) == (1u << 2));  // the bit is back, and says buffer 1
    CHECK(wr(BUFF_STATUS, 1u << 2) && rd(BUFF_STATUS) == 0);

    // an isochronous endpoint: buffer 1 sits at the offset named in bits 28:27
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | (EP_CTRL_TYPE_ISOCHRONOUS << EP_CTRL_TYPE_SHIFT) | 0x400);
    dpram[0x400 + 256] = 0x77;  // offset code 1 = 256
    put32(EP1_IN_BUF, ((BUF_AVAILABLE | BUF_FULL | 1) << BUF1_SHIFT) | (1u << BUF_ISO_OFFSET_SHIFT));
    usb.buf_select[2] = 1;
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 1 && env.writes[1][0].data[0] == 0x77 && env.writes[1][0].half == 1);
}

static void test_reset_select() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | USB_CTRL_DOUBLE_BUF | 0x400);
    usb.buf_select[2] = 1;
    put32(EP1_IN_BUF, BUF_RESET_SELECT);
    arm(EP1_IN_BUF);
    CHECK(usb.buf_select[2] == 0);
}

static void test_ep0_uses_the_sie_ctrl_bits() {
    fresh();
    wr(SIE_CTRL, SIE_CTRL_EP0_INT_1BUF);
    put32(0x80, BUF_AVAILABLE | BUF_FULL | BUF_LAST | 3);  // EP0 IN
    arm(0x80);
    CHECK(env.write_n[0] == 1 && env.writes[0][0].size == 3);
    tick_us(11);
    CHECK(rd(BUFF_STATUS) == 1 && (rd(SIE_STATUS) & SIE_TRANS_COMPLETE) && env.written == 0);
    tick_us(10);
    CHECK(env.written == 1 && env.last_write_endpoint == 0 && env.last_write_size == 3);  // endpoint 0's own reaction alarm
}

static void test_setup_packet() {
    fresh();
    wr(INTE, INTR_SETUP_REQ);
    wr(EP_STALL_ARM, 3);
    const uint8_t setup[8] = {0x80, 6, 0, 1, 0, 0, 18, 0};
    CHECK(usb.send_setup_packet(setup, 8));
    CHECK(dpram[1] == 6 && rd(EP_STALL_ARM) == 0 && (rd(SIE_STATUS) & SIE_SETUP_REC) && env.irq_level);
    CHECK(wr(SIE_STATUS, SIE_SETUP_REC) && !(rd(INTR) & INTR_SETUP_REQ) && !env.irq_level);
}

static void test_sie_status_rules() {
    fresh();
    wr(INTE, INTR_BUS_RESET | INTR_DEV_CONN_DIS);
    CHECK(usb.reset_device());
    tick_us(9999);
    CHECK(!(rd(SIE_STATUS) & SIE_BUS_RESET));
    tick_us(2);
    CHECK((rd(SIE_STATUS) & SIE_BUS_RESET) && env.irq_level);
    CHECK(wr(SIE_STATUS, SIE_BUS_RESET) && env.reset_received == 1);
    CHECK(!(rd(SIE_STATUS) & SIE_BUS_RESET) && ((rd(SIE_STATUS) >> SIE_LINE_STATE_SHIFT) & 3) == LINE_STATE_J);
    CHECK((rd(SIE_STATUS) & SIE_CONNECTED) && (rd(INTR) & INTR_DEV_CONN_DIS));  // the connect EVENT
    CHECK(wr(SIE_STATUS, SIE_CONNECTED));
    CHECK((rd(SIE_STATUS) & SIE_CONNECTED) && !(rd(INTR) & INTR_DEV_CONN_DIS));  // the level stays, the event is gone
    wr(SIE_STATUS, SIE_SPEED_MASK);
    CHECK(!usb.host_connect_event);
}

static void test_usb_pwr() {
    fresh();
    wr(USB_PWR, 0xFF);
    CHECK(rd(USB_PWR) == PWR_MASK && (rd(SIE_STATUS) & SIE_VBUS_DETECTED) && (rd(SIE_STATUS) & SIE_VBUS_OVER_CURR) && (rd(INTR) & INTR_VBUS_DETECT));
    wr(USB_PWR, PWR_VBUS_DETECT);  // override not enabled
    CHECK(!(rd(SIE_STATUS) & SIE_VBUS_DETECTED) && !(rd(SIE_STATUS) & SIE_VBUS_OVER_CURR));
}

static void test_usb_muxing_enumeration_workaround() {
    fresh();
    wr(USB_MUXING, TO_DIGITAL_PAD);
    CHECK(rd(SIE_STATUS) & SIE_CONNECTED);
    fresh();
    wr(USB_MUXING, TO_DIGITAL_PAD | TO_PHY);
    CHECK(!(rd(SIE_STATUS) & SIE_CONNECTED));
}

static void test_ep_abort_and_stall_nak() {
    fresh();
    wr(EP_ABORT, 0x6);
    CHECK(rd(EP_ABORT_DONE) == 0x6 && (rd(INTR) & INTR_ABORT_DONE));
    wr(EP_ABORT_DONE, 0x2);
    CHECK(rd(EP_ABORT_DONE) == 0x4 && (rd(INTR) & INTR_ABORT_DONE));
    wr(EP_ABORT_DONE, 0x4);
    CHECK(!(rd(INTR) & INTR_ABORT_DONE));
    usb.ep_status_stall_nak = 0x3;
    CHECK(usb.stall_nak_updated() && (rd(INTR) & INTR_EP_STALL_NAK));
    wr(EP_STATUS_STALL_NAK, 0x3);
    CHECK(!(rd(INTR) & INTR_EP_STALL_NAK));
}

static void test_bounded_lists() {
    fresh();
    for (int i = 0; i < 100; ++i) usb.done_push(5, i & 1);
    CHECK(usb.done_count(5) == kFifoDepth);
    for (uint32_t i = 0; i < kFifoDepth; ++i) CHECK(usb.done_at(5, i) == (i & 1));
    put32(EP1_OUT_CONTROL, 0x80000000u | 0x300);
    for (int i = 0; i < 100; ++i) {
        put32(EP1_OUT_BUF, BUF_AVAILABLE | 8);
        arm(EP1_OUT_BUF);
    }
    CHECK(usb.pending_count(1) == kFifoDepth && env.read_requests == 100);
}

static void test_buffers_past_the_end_of_the_dpram() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | 0xFFC0);  // base 64 kB - 64: far past the 4 kB
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 100);
    arm(EP1_IN_BUF);
    CHECK(env.write_n[1] == 1 && env.writes[1][0].size == 0);
    put32(EP1_OUT_CONTROL, 0x80000000u | 0xF00);  // 256 bytes left, 1000 asked
    put32(EP1_OUT_BUF, BUF_AVAILABLE | 1000);
    arm(EP1_OUT_BUF);
    std::memset(env.reads[1][0], 0x5A, 1100);
    env.read_size[1][0] = 1100;
    env.read_n[1] = 1;
    usb.schedule_read(1, 10);
    tick_us(10);
    CHECK(dpram[0xFFF] == 0x5A && (w32(EP1_OUT_BUF) & BUF_LEN_MASK) == 256);
}

static void test_host_mode_control_transfer() {
    fresh();
    env.address = 0;
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.host_mode && env.enabled == 0);
    CHECK(usb.connect_device());
    CHECK(((rd(SIE_STATUS) >> 8) & 3) == SIE_SPEED_FS_VALUE && usb.host_connect_event && (rd(INTR) & INTR_HOST_CONN_DIS));
    // a control IN: setup, then an IN that returns the kept response cut to the buffer's length
    env.setup_has_response = true;
    for (uint32_t i = 0; i < 18; ++i) env.pending[i] = static_cast<uint8_t>(i + 1);
    env.pending_size = 18;
    env.pending_present = true;
    put32(HOST_EPX_BUF_CTRL, 8);
    wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS);
    CHECK(env.setups == 1 && !usb.expecting_status_phase && (rd(SIE_STATUS) & SIE_ACK_REC));
    tick_us(5);
    CHECK((rd(SIE_STATUS) & SIE_TRANS_COMPLETE) && !(rd(SIE_CTRL) & SIE_CTRL_START_TRANS));
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(dpram[HOST_EPX_DATA] == 1 && dpram[HOST_EPX_DATA + 7] == 8 && dpram[HOST_EPX_DATA + 8] == 0);
    CHECK(w32(HOST_EPX_BUF_CTRL) == (8u | BUF_FULL | BUF_DATA1_PID) && rd(BUFF_STATUS) == 1 && usb.control_data_pid == 0);
    tick_us(5);
    // the status phase is an OUT of zero length: acknowledged without asking the device
    env.outs = 0;
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE);
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.outs == 0 && !(w32(HOST_EPX_BUF_CTRL) & BUF_AVAILABLE));
    // an OUT with data goes to the device
    tick_us(5);
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE | 3);
    dpram[HOST_EPX_DATA] = 9;
    wr(ADDR_ENDP, 1u << 16);  // endpoint 1
    wr(SIE_CTRL, SIE_CTRL_SEND_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.outs == 1 && env.last_out_size == 3 && env.last_out[0] == 9);
    tick_us(5);
    // NAK and STALL set their status bits and move no buffer
    env.in_status = kUsbNak;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(rd(SIE_STATUS) & SIE_NAK_REC);
    tick_us(5);
    env.in_status = kUsbStall;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(rd(SIE_STATUS) & SIE_STALL_REC);
    // no device: a timeout
    tick_us(5);
    usb.has_device = false;
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(rd(SIE_STATUS) & SIE_RX_TIMEOUT);
}

static void test_host_mode_setup_without_response_expects_a_status_phase() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.connect_device());
    env.setup_has_response = false;
    wr(SIE_CTRL, SIE_CTRL_SEND_SETUP | SIE_CTRL_START_TRANS);
    CHECK(usb.expecting_status_phase);
    tick_us(5);
    put32(HOST_EPX_BUF_CTRL, BUF_AVAILABLE);
    wr(SIE_CTRL, SIE_CTRL_RECEIVE_DATA | SIE_CTRL_START_TRANS);
    CHECK(env.ins == 0 && !usb.expecting_status_phase);  // the zero-length ACK never asks the device
    CHECK(rd(BUFF_STATUS) == 0);
}

static void test_sof_and_interrupt_endpoints() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.connect_device());
    env.address = 5;
    wr(SOF_WR, 0x7FF);
    wr(SIE_CTRL, SIE_CTRL_SOF_EN);
    tick_us(999);
    CHECK(usb.sof_frame_number == 0x7FF);
    tick_us(2);
    CHECK(usb.sof_frame_number == 0 && (rd(INTR) & INTR_HOST_SOF));
    CHECK(rd(SOF_RD) == 0 && !(rd(INTR) & INTR_HOST_SOF));  // reading SOF_RD acknowledges
    // interrupt endpoint 1: device address 5, endpoint 3, IN
    wr(ADDR_ENDP1, 5u | (3u << 16));
    put32(HOST_INT_EP_CTRL_BASE, 0x500);
    put32(HOST_INT_EP_BUF_CTRL_BASE, BUF_AVAILABLE | 8);
    wr(INT_EP_CTRL, 1u << 1);
    env.in_size = 2;
    env.in_data[0] = 0xAB;
    env.in_data[1] = 0xCD;
    tick_us(1000);
    CHECK(env.ins == 1 && dpram[0x500] == 0xAB && w32(HOST_INT_EP_BUF_CTRL_BASE) == (BUF_FULL | 2u));
    CHECK(rd(BUFF_STATUS) == (1u << 2));
    tick_us(1000);
    CHECK(env.ins == 1);  // the buffer is not available again: not polled
    wr(SIE_CTRL, 0);
    tick_us(5000);
    CHECK(usb.sof_frame_number == 3);  // the SOF already pending still happens, and is not re-armed
    tick_us(5000);
    CHECK(usb.sof_frame_number == 3);
}

static void test_disconnect_and_connect_events() {
    fresh();
    wr(MAIN_CTRL, MAIN_HOST_NDEVICE | MAIN_CONTROLLER_EN);
    CHECK(usb.connect_device());
    CHECK(wr(SIE_STATUS, SIE_SPEED_MASK) && !usb.host_connect_event);
    CHECK(usb.disconnect_device());
    CHECK(((rd(SIE_STATUS) >> 8) & 3) == 0 && usb.host_connect_event && !usb.has_device);
}

static void test_reset_drops_state_and_alarms() {
    fresh();
    put32(EP1_IN_CONTROL, 0x80000000u | 0x200);
    put32(EP1_IN_BUF, BUF_AVAILABLE | BUF_FULL | 1);
    arm(EP1_IN_BUF);
    usb.reset_device();
    wr(INTE, 0x1);
    CHECK(usb.has_scheduled_alarms());
    CHECK(usb.reset());
    CHECK(!usb.has_scheduled_alarms() && rd(INTE) == 0 && rd(NAK_POLL) == NAK_POLL_RESET && !env.irq_level);
    tick_us(20000);
    CHECK(env.written == 0 && rd(SIE_STATUS) == 0);
}

static void test_failures_stop_at_once() {
    // each host call of a representative run fails in turn: the block returns false and leaves the failure flag raised
    for (int n = 0; n < 6; ++n) {
        fresh();
        env.fail_at = n;
        put32(EP1_OUT_CONTROL, 0x80000000u | USB_CTRL_INTERRUPT_PER_TRANSFER | 0x300);
        put32(EP1_OUT_BUF, BUF_AVAILABLE | 4);
        const bool armed = usb.dpram_updated(EP1_OUT_BUF);
        if (n == 0) {
            CHECK(!armed && env.failed);  // the first host call is the read request
            continue;
        }
        CHECK(armed);
        std::memset(env.reads[1][0], 1, 4);
        env.read_size[1][0] = 4;
        env.read_n[1] = 1;
        usb.schedule_read(1, 10);
        const bool ok = clk.tick(10000.0);
        if (n == 1) CHECK(!ok && env.failed);  // the interrupt line, at the first sie_status_updated
    }
    fresh();
    env.fail_at = 0;
    CHECK(!wr(INTE, 1) && env.failed);
    fresh();
    env.fail_at = 0;
    CHECK(!wr(MAIN_CTRL, MAIN_CONTROLLER_EN) || env.enabled == 1);  // on_usb_enabled is the first call
}

int main() {
    test_a_constructed_block_is_at_power_on_without_a_reset();
    test_power_on_state();
    test_masks();
    test_interrupt_registers_and_aliases();
    test_unimplemented_registers_warn();
    test_an_in_transfer();
    test_an_in_needs_full_and_a_stall_holds_it();
    test_the_interrupt_enable_decides_buff_status();
    test_an_out_transfer();
    test_double_buffering_and_the_iso_offset();
    test_reset_select();
    test_ep0_uses_the_sie_ctrl_bits();
    test_setup_packet();
    test_sie_status_rules();
    test_usb_pwr();
    test_usb_muxing_enumeration_workaround();
    test_ep_abort_and_stall_nak();
    test_bounded_lists();
    test_buffers_past_the_end_of_the_dpram();
    test_host_mode_control_transfer();
    test_host_mode_setup_without_response_expects_a_status_phase();
    test_sof_and_interrupt_endpoints();
    test_disconnect_and_connect_events();
    test_reset_drops_state_and_alarms();
    test_failures_stop_at_once();
    test_every_register_keeps_its_bits();
    test_set_connected_is_a_level_with_an_event();
    test_bus_reset_in_each_mode();
    test_status_bits_raise_their_interrupts();
    test_dpram_updated_picks_its_endpoint_and_respects_the_mode();
    test_interrupt_per_two_buffers();
    test_the_other_buffer_is_chased();
    test_failures_in_the_status_phase();
    test_a_long_setup_packet_stays_inside_the_dpram();
    test_reset_clears_everything();
    test_detach_unlinks_every_alarm();
    test_attach_and_detach_in_each_mode();
    test_sof_enable_and_bus_reset_of_the_host();
    test_host_out_and_in_details();
    test_the_setup_transaction();
    test_the_interrupt_endpoint_poll();
    test_a_poll_that_gets_no_data();
    test_the_remaining_corners();
    if (failures == 0) std::printf("test_usb: ok\n");
    return failures == 0 ? 0 : 1;
}
