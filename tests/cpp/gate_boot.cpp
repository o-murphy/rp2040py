// Gate G1 of docs/records/0096-cpp-mcu-core.md: the C++ core, with no Python and no Cython, boots a stock MicroPython and runs a program on it.
//
//   gate_boot ROM.bin FLASH.bin [max_million_instructions] [stop_text]
//
// ROM.bin is the 16 KiB boot ROM, FLASH.bin the flash image (the firmware and its littlefs filesystem with a main.py), both prepared by tests/test_gate_g1.py. The host gives the chip the
// world and nothing else: the buffers (`McuMemory`), the crystal, a logger that only counts (`warn`) and the UART0 byte sink (`uart_byte`); no pin, no ADC, no I2C, no USB device, no clock-tree
// hook. It prints the UART bytes it received to stdout, and - to stderr, one line each - how many times every callback of the host was called, by kind. The run ends when `stop_text` has been
// received, when the instruction budget is spent, or when the core stops.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "mcu.hpp"

using namespace rp2040core;

namespace {

struct World {
    std::string uart;
    std::map<uint32_t, long> warns;  // (block << 16 | kind) -> count
    std::map<uint32_t, uint32_t> first_offset;
    long uart_bytes = 0;
    int failed = 0;
};

bool read_file(const char* path, std::vector<uint8_t>* out) {
    std::FILE* f = std::fopen(path, "rb");
    if (f == nullptr) return false;
    std::fseek(f, 0, SEEK_END);
    const long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    out->resize(static_cast<size_t>(n));
    const size_t got = n > 0 ? std::fread(out->data(), 1, static_cast<size_t>(n), f) : 0;
    std::fclose(f);
    return got == static_cast<size_t>(n);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: gate_boot ROM.bin FLASH.bin [max_million_instructions] [stop_text]\n");
        return 2;
    }
    const long budget = (argc > 3 ? std::atol(argv[3]) : 200) * 1000000L;
    const std::string stop_text = argc > 4 ? argv[4] : "";

    std::vector<uint8_t> rom, image;
    if (!read_file(argv[1], &rom) || !read_file(argv[2], &image)) {
        std::fprintf(stderr, "cannot read the images\n");
        return 2;
    }
    static std::vector<uint8_t> flash(16 * 1024 * 1024, 0xFF);
    static std::vector<uint8_t> rom_words(16 * 1024, 0);
    static std::vector<uint8_t> sram(264 * 1024, 0);
    static std::vector<uint8_t> dpram(4 * 1024, 0);
    if (image.size() > flash.size() || rom.size() > rom_words.size()) {
        std::fprintf(stderr, "image too big\n");
        return 2;
    }
    std::memcpy(flash.data(), image.data(), image.size());
    std::memcpy(rom_words.data(), rom.data(), rom.size());

    World world;
    static Mcu mcu;  // (static: the chip is large)
    McuMemory memory;
    memory.rom = rom_words.data();
    memory.flash = flash.data();
    memory.sram = sram.data();
    memory.dpram = dpram.data();
    mcu.set_memory(memory);

    McuHost host;
    host.ctx = &world;
    host.failed = &world.failed;
    host.warn = [](void* ctx, uint32_t block, uint32_t kind, uint32_t offset, int64_t) {
        World* w = static_cast<World*>(ctx);
        const uint32_t key = block << 16 | kind;
        if (w->warns[key]++ == 0) w->first_offset[key] = offset;
    };
    host.uart_byte = [](void* ctx, uint32_t instance, uint32_t byte) {
        World* w = static_cast<World*>(ctx);
        ++w->uart_bytes;
        if (instance == 0) w->uart.push_back(static_cast<char>(byte));
        return true;
    };
    mcu.build(host);
    mcu.set_crystal_hz(12e6);  // the Pico's crystal: the board's, a parameter of the world
    if (!mcu.reset(false, true)) {  // a power-on reset: everything, the flash kept
        std::fprintf(stderr, "reset failed\n");
        return 1;
    }
    mcu.leave_reset();  // the core at the flash entry, where the boot ROM would have left it (the ROM image is optional)

    McuLimits limits;
    limits.instruction_ceiling = 1000000;
    limits.yield_budget_seconds = 1e9;  // no wall-clock budget: the harness has its own (instructions)
    long done = 0;
    int stop = 0;
    McuResult result;
    while (done < budget && stop == 0) {
        if (mcu.run_at_sys_clock(limits, &result) != 0) {
            std::fprintf(stderr, "the core faulted\n");
            return 1;
        }
        done += result.iterations;
        if (result.reason == kStopRequested) stop = 1;
        if (!stop_text.empty() && world.uart.find(stop_text) != std::string::npos) stop = 2;
        if (result.iterations == 0 && result.reason != kStopClockChanged) stop = 3;
    }
    std::fputs(world.uart.c_str(), stdout);
    std::fflush(stdout);
    std::fprintf(stderr, "iterations=%ld stop=%d sim_ns=%.0f clk_sys=%.0f\n", done, stop, mcu.clock().nanos(), mcu.clocks().clk_sys());
    std::fprintf(stderr, "callback uart_byte=%ld\n", world.uart_bytes);
    for (const auto& entry : world.warns) {
        std::fprintf(stderr, "callback warn block=%u kind=%u count=%ld first_offset=0x%x\n", entry.first >> 16, entry.first & 0xFFFF, entry.second, world.first_offset[entry.first]);
    }
    return stop == 2 ? 0 : 1;
}
