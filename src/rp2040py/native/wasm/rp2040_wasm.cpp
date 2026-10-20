// The proof of concept of docs/records/0103-wasm-backend.md: the C++ core behind a flat C ABI (the reduced form of docs/records/0102-c-abi-for-the-core.md), built for wasm32-wasi as a reactor
// module. The module imports nothing: its buffers (boot ROM, flash, SRAM, USB DPRAM, the UART0 bytes) live in its own linear memory and the host reads and writes them through the pointers
// below; the only host callback of the core, `uart_byte`, is answered inside the module (appended to `g_uart`).
//
// The host calls `_initialize` once (a reactor's constructors), then: write the images through `rp2040_rom()` / `rp2040_flash()`, `rp2040_boot()`, and `rp2040_run()` until what it waits for
// has come out of `rp2040_uart()` (`rp2040_uart_length()` bytes). Exports are named with `export_name`, so no linker flag lists them.
#include <cstddef>
#include <cstdint>

#include "rp2040.hpp"

using namespace rp2040core;

#ifdef __wasm__
#define RP2040_EXPORT(name) extern "C" __attribute__((export_name(#name)))
#else
#define RP2040_EXPORT(name) extern "C"  // (built natively, e.g. scripts/bench/wasm_core.py: every extern "C" symbol is exported anyway)
#endif

namespace {

constexpr uint32_t kRomBytes = 16 * 1024;
constexpr uint32_t kFlashBytes = 16 * 1024 * 1024;
constexpr uint32_t kSramBytes = 264 * 1024;
constexpr uint32_t kDpramBytes = 4 * 1024;
constexpr uint32_t kUartBytes = 64 * 1024;

uint8_t g_rom[kRomBytes];
uint8_t g_flash[kFlashBytes];  // (zero-initialised memory costs nothing in the .wasm file; `rp2040_erase_flash` sets the erased state, 0xFF)
uint8_t g_sram[kSramBytes];
uint8_t g_dpram[kDpramBytes];
uint8_t g_uart[kUartBytes];
uint32_t g_uart_length = 0;
uint32_t g_uart_dropped = 0;
uint32_t g_warnings = 0;
int g_failed = 0;
Mcu g_mcu;
McuLimits g_limits;

// --- USB, device mode (docs/records/0103-wasm-backend.md, 2026-10-20): the host's end of the wire, answered inside the module so that it still imports nothing. What the Cython shell (`native/_usb.pyx`)
// keeps in Python queues is kept here; what the Python host (`usb/cdc.py`, an external device) receives there as callbacks becomes records in an event buffer that the host reads after a `rp2040_run`,
// and what it calls becomes exports. The host's answers therefore reach the chip when a slice ends, not in the same simulated microsecond as in the Cython build.
constexpr uint32_t kUsbEndpoints = 16;
constexpr uint32_t kUsbSlot = 64;       // one full-speed packet
constexpr uint32_t kUsbQueueDepth = 8;  // buffers in flight per endpoint and queue
constexpr uint32_t kUsbEventBytes = 64 * 1024;

struct UsbBuffer {
    uint8_t data[kUsbSlot];
    uint32_t length;
    uint32_t half;
};
struct UsbQueue {
    UsbBuffer items[kUsbQueueDepth];
    uint32_t count = 0;
};
UsbQueue g_usb_write[kUsbEndpoints];     // IN buffers the firmware started (the shell's _write_queue)
UsbQueue g_usb_reaction[kUsbEndpoints];  // acknowledged, waiting for the host's reaction (_reaction_queue)
UsbQueue g_usb_read[kUsbEndpoints];      // OUT data the host answered with (_read_queue)
uint8_t g_usb_events[kUsbEventBytes];    // records: type(1) endpoint(1) length(2, LE) data(length)
uint32_t g_usb_events_length = 0;
uint32_t g_usb_events_dropped = 0;
uint32_t g_usb_overflow = 0;             // a queue was full / a buffer longer than a packet
uint8_t g_usb_scratch[1024];             // setup packets and OUT data from the host come through here
enum : uint8_t { kEvtEnabled = 1, kEvtResetReceived = 2, kEvtReadRequest = 3, kEvtInData = 4 };

void usb_event(uint8_t type, uint32_t endpoint, const uint8_t* data, uint32_t length) {
    if (g_usb_events_length + 4 + length > kUsbEventBytes) {
        ++g_usb_events_dropped;
        return;
    }
    uint8_t* at = g_usb_events + g_usb_events_length;
    at[0] = type;
    at[1] = static_cast<uint8_t>(endpoint);
    at[2] = static_cast<uint8_t>(length & 0xFF);
    at[3] = static_cast<uint8_t>(length >> 8);
    for (uint32_t i = 0; i < length; ++i) at[4 + i] = data[i];
    g_usb_events_length += 4 + length;
}

bool usb_push(UsbQueue& queue, const uint8_t* data, uint32_t length, uint32_t half) {
    if (queue.count >= kUsbQueueDepth) {
        ++g_usb_overflow;
        return true;
    }
    UsbBuffer& item = queue.items[queue.count++];
    if (length > kUsbSlot) {
        ++g_usb_overflow;
        length = kUsbSlot;
    }
    for (uint32_t i = 0; i < length; ++i) item.data[i] = data[i];
    item.length = length;
    item.half = half;
    return true;
}

UsbHost make_usb_wire() {
    UsbHost wire;
    wire.usb_enabled = [](void*) { usb_event(kEvtEnabled, 0, nullptr, 0); return true; };
    wire.reset_received = [](void*) { usb_event(kEvtResetReceived, 0, nullptr, 0); return true; };
    wire.endpoint_read = [](void*, uint32_t endpoint, uint32_t length) {
        uint8_t size[2] = {static_cast<uint8_t>(length & 0xFF), static_cast<uint8_t>(length >> 8)};
        usb_event(kEvtReadRequest, endpoint, size, 2);
        return true;
    };
    wire.write_queued = [](void*, uint32_t endpoint, uint32_t half, const uint8_t* data, uint32_t length) {
        return endpoint < kUsbEndpoints && usb_push(g_usb_write[endpoint], data, length, half);
    };
    wire.drain_write = [](void*, uint32_t endpoint) {
        UsbQueue& queue = g_usb_write[endpoint];
        const uint32_t count = queue.count;
        queue.count = 0;
        for (uint32_t i = 0; i < count; ++i) {
            if (!g_mcu.usb().finish_write(endpoint, queue.items[i].half)) return false;
            usb_push(g_usb_reaction[endpoint], queue.items[i].data, queue.items[i].length, 0);
        }
        return true;
    };
    wire.drain_reaction = [](void*, uint32_t endpoint) {
        UsbQueue& queue = g_usb_reaction[endpoint];
        const uint32_t count = queue.count;
        queue.count = 0;
        for (uint32_t i = 0; i < count; ++i) usb_event(kEvtInData, endpoint, queue.items[i].data, queue.items[i].length);
        return true;
    };
    wire.drain_read = [](void*, uint32_t endpoint) {
        UsbQueue& queue = g_usb_read[endpoint];
        const uint32_t count = queue.count;
        queue.count = 0;
        for (uint32_t i = 0; i < count; ++i) {
            if (!g_mcu.usb().finish_read(endpoint, queue.items[i].data, queue.items[i].length)) return false;
        }
        return true;
    };
    return wire;
}
UsbHost g_usb_wire;

}  // namespace

RP2040_EXPORT(rp2040_rom) uint8_t* rp2040_rom() { return g_rom; }
RP2040_EXPORT(rp2040_rom_size) uint32_t rp2040_rom_size() { return kRomBytes; }
RP2040_EXPORT(rp2040_flash) uint8_t* rp2040_flash() { return g_flash; }
RP2040_EXPORT(rp2040_flash_size) uint32_t rp2040_flash_size() { return kFlashBytes; }
RP2040_EXPORT(rp2040_sram) uint8_t* rp2040_sram() { return g_sram; }
RP2040_EXPORT(rp2040_uart) const uint8_t* rp2040_uart() { return g_uart; }
RP2040_EXPORT(rp2040_uart_length) uint32_t rp2040_uart_length() { return g_uart_length; }
RP2040_EXPORT(rp2040_uart_dropped) uint32_t rp2040_uart_dropped() { return g_uart_dropped; }
RP2040_EXPORT(rp2040_warnings) uint32_t rp2040_warnings() { return g_warnings; }

// The flash as a blank chip has it: every byte 0xFF. The host calls it before it writes the image, so only the image crosses the boundary, not 16 MiB of erased flash.
RP2040_EXPORT(rp2040_erase_flash) void rp2040_erase_flash() {
    for (uint32_t i = 0; i < kFlashBytes; ++i) g_flash[i] = 0xFF;
}

// Brings the chip to the flash entry (where the boot ROM would have left the core): the first call builds it over the module's buffers, every later call is a power-on reset (`Mcu::build` is
// for once). Either way the UART0 bytes and the counters start again. Returns 0 on success.
RP2040_EXPORT(rp2040_boot) int32_t rp2040_boot() {
    static bool built = false;
    g_uart_length = g_uart_dropped = g_warnings = 0;
    g_failed = 0;
    if (!built) {
        McuMemory memory;
        memory.rom = g_rom;
        memory.flash = g_flash;
        memory.sram = g_sram;
        memory.dpram = g_dpram;
        g_mcu.set_memory(memory);

        // The bus tells the host about a word written to the DPRAM (a firmware arming a buffer); the host passes it to the USB block, which starts the transfer from exactly that notification
        // (the Cython chip's `_bus_dpram_written`). Without it the device is silent after its first SETUP.
        BusHost bus_host;
        bus_host.warn = [](void*, uint32_t, uint32_t) { ++g_warnings; };
        bus_host.dpram_written = [](void*, uint32_t offset, int64_t) {
            if (!g_mcu.usb().dpram_updated(offset)) g_failed = 1;
        };
        g_mcu.bus.init(bus_host);

        McuHost host;
        g_usb_wire = make_usb_wire();
        host.usb_wire = &g_usb_wire;
        host.failed = &g_failed;
        host.warn = [](void*, uint32_t, uint32_t, uint32_t, int64_t) { ++g_warnings; };
        host.uart_byte = [](void*, uint32_t instance, uint32_t byte) {
            if (instance != 0) return true;
            if (g_uart_length < kUartBytes) {
                g_uart[g_uart_length++] = static_cast<uint8_t>(byte);
            } else {
                ++g_uart_dropped;
            }
            return true;
        };
        g_mcu.build(host);
        g_mcu.set_crystal_hz(12e6);  // the Pico's crystal
        g_limits.instruction_ceiling = 1000000;
        g_limits.yield_budget_seconds = 1e9;  // the host paces itself: it calls `rp2040_run` as often as it wants
        built = true;
    }
    if (!g_mcu.reset(false, true)) return 1;
    g_mcu.leave_reset();
    return 0;
}

// The core's registers (0-12, 13 = SP, 14 = LR, 15 = PC): a host that starts a program somewhere other than the flash entry (a benchmark in SRAM, a test) sets PC and SP, and reads the result back.
// The registers of core `core` (0 or 1; record 0104). `rp2040_register` / `rp2040_set_register` are core 0's, the ones every existing host uses; core 1 exists from the start and stays inactive until a launch.
RP2040_EXPORT(rp2040_core_register) uint32_t rp2040_core_register(uint32_t core, uint32_t index) {
    return core < 2 && index < 16 ? g_mcu.core(static_cast<uint8_t>(core)).registers[index] : 0;
}
RP2040_EXPORT(rp2040_core_set_register) void rp2040_core_set_register(uint32_t core, uint32_t index, uint32_t value) {
    if (core < 2 && index < 16) g_mcu.core(static_cast<uint8_t>(core)).registers[index] = value;
}
RP2040_EXPORT(rp2040_register) uint32_t rp2040_register(uint32_t index) { return rp2040_core_register(0, index); }
RP2040_EXPORT(rp2040_set_register) void rp2040_set_register(uint32_t index, uint32_t value) { rp2040_core_set_register(0, index, value); }

// Exactly `count` instructions at most (a host that knows how long its program is runs no more: the rest of a slice would execute whatever follows the program), capped at a million. Returns the
// instructions run, or -1 when the core faulted.
RP2040_EXPORT(rp2040_run_n) int64_t rp2040_run_n(uint32_t count) {
    McuLimits limits = g_limits;
    const long wanted = static_cast<long>(count);
    limits.instruction_ceiling = wanted < g_limits.instruction_ceiling ? wanted : g_limits.instruction_ceiling;
    McuResult result;
    if (g_mcu.run_at_sys_clock(limits, &result) != 0) return -1;
    return static_cast<int64_t>(result.iterations);
}

// One slice of at most a million instructions. Returns the instructions run, or -1 when the core faulted.
RP2040_EXPORT(rp2040_run) int64_t rp2040_run() { return rp2040_run_n(1000000); }

// --- USB, device mode: events out (read after a run), actions in.
RP2040_EXPORT(rp2040_usb_events) const uint8_t* rp2040_usb_events() { return g_usb_events; }
RP2040_EXPORT(rp2040_usb_events_length) uint32_t rp2040_usb_events_length() { return g_usb_events_length; }
RP2040_EXPORT(rp2040_usb_events_clear) void rp2040_usb_events_clear() { g_usb_events_length = 0; }
RP2040_EXPORT(rp2040_usb_events_dropped) uint32_t rp2040_usb_events_dropped() { return g_usb_events_dropped; }
RP2040_EXPORT(rp2040_usb_overflow) uint32_t rp2040_usb_overflow() { return g_usb_overflow; }
RP2040_EXPORT(rp2040_usb_scratch) uint8_t* rp2040_usb_scratch() { return g_usb_scratch; }
RP2040_EXPORT(rp2040_usb_reset_device) int32_t rp2040_usb_reset_device() { return g_mcu.usb().reset_device() ? 0 : 1; }
RP2040_EXPORT(rp2040_usb_send_setup) int32_t rp2040_usb_send_setup(uint32_t length) {
    return length <= sizeof g_usb_scratch && g_mcu.usb().send_setup_packet(g_usb_scratch, length) ? 0 : 1;
}
// The host's answer to an OUT read request: `length` bytes from the scratch buffer, delivered `read_delay_microseconds` later (the shell's `endpoint_read_done`).
RP2040_EXPORT(rp2040_usb_endpoint_read_done) int32_t rp2040_usb_endpoint_read_done(uint32_t endpoint, uint32_t length) {
    if (endpoint >= kUsbEndpoints || length > sizeof g_usb_scratch) return 1;
    usb_push(g_usb_read[endpoint], g_usb_scratch, length, 0);
    g_mcu.usb().schedule_read(endpoint, static_cast<double>(g_mcu.usb().read_delay_microseconds));
    return 0;
}
RP2040_EXPORT(rp2040_dpram) uint8_t* rp2040_dpram() { return g_dpram; }
