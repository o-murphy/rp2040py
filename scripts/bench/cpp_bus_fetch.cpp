// Micro-benchmark of the bus's read path: the fixed chain of range compares of the pre-0096 Cython bus (`_rp2040.pyx`, read_uint16 / read_uint32:
// flash, then SRAM, then boot ROM / DPRAM for 32-bit) against the C++ `Bus` (core/bus.hpp, a block-indexed memory map). Same addresses, same
// memory, the sums are compared so nothing is optimised away. Build and run:
//     c++ -O3 -std=c++17 -fno-exceptions -fno-rtti -I src/rp2040py/native/core scripts/bench/cpp_bus_fetch.cpp -o /tmp/bus_bench && /tmp/bus_bench
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "bus.hpp"

using namespace rp2040core;

static constexpr uint32_t kFlash = 0x10000000, kFlashSize = 16u << 20, kRam = 0x20000000, kRamSize = 264u << 10;
static constexpr uint32_t kRom = 0, kRomSize = 16u << 10, kDpram = 0x50100000, kDpramSize = 4096;

static uint8_t flash[kFlashSize], ram[kRamSize], rom[kRomSize], dpram[kDpramSize];

// What the Cython bus did, transcribed: direct compares in a fixed order.
struct CythonStyle {
    uint32_t read16(uint32_t addr) const noexcept {
        if (kFlash <= addr && addr < kFlash + kFlashSize) return ld16(flash + (addr - kFlash));
        if (kRam <= addr && addr < kRam + kRamSize) return ld16(ram + (addr - kRam));
        const uint32_t v = read32(addr & ~3u);
        return (addr & 2) ? v >> 16 : v & 0xFFFF;
    }
    uint32_t read32(uint32_t addr) const noexcept {
        if (addr < kRomSize) return ld32(rom + (addr & ~3u));
        if (kFlash <= addr && addr < kFlash + 0x04000000u) return ld32(flash + (addr & 0x00FFFFFFu));
        if (kRam <= addr && addr < kRam + kRamSize) return ld32(ram + (addr - kRam));
        if (kDpram <= addr && addr < kDpram + kDpramSize) return ld32(dpram + (addr - kDpram));
        return 0xFFFFFFFFu;
    }
    static uint32_t ld16(const uint8_t* p) noexcept { uint16_t v; std::memcpy(&v, p, 2); return v; }
    static uint32_t ld32(const uint8_t* p) noexcept { uint32_t v; std::memcpy(&v, p, 4); return v; }
};

struct Access {
    uint32_t addr;
    bool wide;  // 32-bit
};

template <class F>
static double best_ns(const std::vector<Access>& trace, F&& read, uint64_t* sum_out) {
    double best = 1e30;
    uint64_t sum = 0;
    for (int run = 0; run < 7; ++run) {
        sum = 0;
        const auto t0 = std::chrono::steady_clock::now();
        for (int pass = 0; pass < 40; ++pass)
            for (const Access& a : trace) sum += read(a);
        const auto t1 = std::chrono::steady_clock::now();
        const double ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / (40.0 * trace.size());
        if (ns < best) best = ns;
    }
    *sum_out = sum;
    return best;
}

int main(int argc, char** argv) {
    // `small`: a 256 KiB flash / 64 KiB SRAM working set that stays in the cache, so the compare logic and not the memory latency is measured.
    const bool small = argc > 1 && std::strcmp(argv[1], "small") == 0;
    const uint32_t flash_span = small ? (256u << 10) : (1u << 20) * 2, ram_span = small ? (64u << 10) : kRamSize;
    uint32_t s = 0x12345678;
    auto rnd = [&s]() { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; };
    for (uint32_t i = 0; i < kFlashSize; i += 4) { const uint32_t v = rnd(); std::memcpy(flash + i, &v, 4); }
    for (uint32_t i = 0; i < kRamSize; i += 4) { const uint32_t v = rnd(); std::memcpy(ram + i, &v, 4); }

    Bus bus;
    bus.mem.attach({kRom, kRomSize, kRomSize, 0xFFFFFFFFu, rom, kWordIndexed});
    bus.mem.attach({kFlash, 0x04000000u, kFlashSize, 0x00FFFFFFu, flash, kSubWord});
    bus.mem.attach({kRam, kRamSize, kRamSize, 0xFFFFFFFFu, ram, kSubWord});
    bus.mem.attach({kDpram, kDpramSize, kDpramSize, 0xFFFFFFFFu, dpram, kNotifyOnWrite});
    CythonStyle cy;

    // A MicroPython-like mix: mostly instruction fetches (16-bit, flash, sequential runs with jumps), then SRAM data, then literal-pool/flash data.
    std::vector<Access> trace;
    uint32_t pc = kFlash + 0x2000;
    std::printf("working set: %s\n", small ? "small (cache-resident)" : "large (random over 2 MiB flash, 264 KiB SRAM)");
    for (int i = 0; i < 1 << 20; ++i) {
        const uint32_t r = rnd() % 100;
        if (r < 62) {
            if (rnd() % 8 == 0) pc = kFlash + (rnd() % (flash_span / 2)) * 2; else pc += 2;
            trace.push_back({pc, false});
        } else if (r < 90) {
            trace.push_back({kRam + (rnd() % (ram_span / 4)) * 4, true});
        } else {
            trace.push_back({kFlash + (rnd() % (flash_span / 4)) * 4, true});
        }
    }

    uint64_t s_cy, s_bus;
    const double ns_cy = best_ns(trace, [&](const Access& a) { return a.wide ? cy.read32(a.addr) : cy.read16(a.addr); }, &s_cy);
    const double ns_bus = best_ns(trace, [&](const Access& a) { return a.wide ? bus.read32(a.addr) : bus.read16(a.addr); }, &s_bus);
    std::printf("Cython-style fixed compare chain: %.2f ns/access\n", ns_cy);
    std::printf("C++ Bus (block-indexed map)     : %.2f ns/access\n", ns_bus);
    std::printf("sums %s\n", s_cy == s_bus ? "match" : "DIFFER");
    return s_cy == s_bus ? 0 : 1;
}
