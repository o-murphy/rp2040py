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

        McuHost host;
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
RP2040_EXPORT(rp2040_register) uint32_t rp2040_register(uint32_t index) { return index < 16 ? g_mcu.cpu.registers[index] : 0; }
RP2040_EXPORT(rp2040_set_register) void rp2040_set_register(uint32_t index, uint32_t value) {
    if (index < 16) g_mcu.cpu.registers[index] = value;
}

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
