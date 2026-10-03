// Standalone checks of src/rp2040py/native/core/memory_map.hpp, built and run by tests/test_core_cpp.py with
// -std=c++17 -fno-exceptions -fno-rtti -Wall -Wextra -Werror (the discipline record 0096 sets for the core).
// No framework: a failed CHECK prints its line and the process exits non-zero.
#include <cstdio>
#include <cstdlib>

#include "memory_map.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                \
        }                                                              \
    } while (0)

static const uint32_t kBootRom = 0x00000000, kFlash = 0x10000000, kSram = 0x20000000, kDpram = 0x50100000;

int main() {
    static uint8_t rom[64], flash[0x1000], sram[0x400], dpram[0x100];
    MemoryMap map;
    const int rom_i = map.attach({kBootRom, sizeof rom, sizeof rom, 0xFFFFFFFFu, rom, kWordIndexed});
    // A 16x mirrored window over 4 KiB, like the XIP flash alias.
    const int flash_i = map.attach({kFlash, 0x10000, sizeof flash, sizeof flash - 1, flash, kSubWord});
    const int sram_i = map.attach({kSram, sizeof sram, sizeof sram, 0xFFFFFFFFu, sram, kSubWord});
    const int dpram_i = map.attach({kDpram, sizeof dpram, sizeof dpram, 0xFFFFFFFFu, dpram, kNotifyOnWrite});
    CHECK(rom_i == 0 && flash_i == 1 && sram_i == 2 && dpram_i == 3 && map.count() == 4);

    uint32_t v = 0;

    // Plain 32-bit round trip is little-endian and zero-copy: the caller's bytes are the memory.
    CHECK(map.write32(kSram + 8, 0xA1B2C3D4u) == sram_i);
    CHECK(sram[8] == 0xD4 && sram[11] == 0xA1);
    CHECK(map.read32(kSram + 8, &v) == sram_i && v == 0xA1B2C3D4u);
    sram[12] = 0x7F;  // the caller changes its own buffer; the next read sees it
    CHECK(map.read16(kSram + 12, &v) == sram_i && v == 0x7F);

    // Reads see the mirror, writes do not.
    flash[0x20] = 0x5A;
    CHECK(map.read32(kFlash + 0x20, &v) == sram_i - 1 && (v & 0xFF) == 0x5A);
    CHECK(map.read32(kFlash + 0x5000 + 0x20, &v) == flash_i && (v & 0xFF) == 0x5A);  // mirrored read
    CHECK(map.write32(kFlash + 0x5000, 1) == kNotHandled);                            // mirrored write: not ours
    CHECK(map.read16(kFlash + 0x5020, &v) == kNotHandled);                            // sub-word: no mirror either

    // Boot ROM is word-indexed: an unaligned 32-bit access touches the word it falls in.
    map.write32(kBootRom + 4, 0x11223344u);
    CHECK(map.read32(kBootRom + 6, &v) == rom_i && v == 0x11223344u);
    CHECK(map.read16(kBootRom + 4, &v) == kNotHandled);  // sub-word on the ROM goes through the caller

    // DPRAM: 32-bit writes are served and flagged for the hook; sub-word writes are left to the caller.
    CHECK(map.write32(kDpram + 4, 0xCAFEBABEu) == dpram_i);
    CHECK(map.region(dpram_i).flags & kNotifyOnWrite);
    CHECK(map.write8(kDpram + 4, 1) == kNotHandled && map.write16(kDpram + 4, 1) == kNotHandled);
    CHECK(map.read16(kDpram + 4, &v) == kNotHandled);

    // Edges: the last whole word, one past it, and a word that would straddle the end.
    CHECK(map.read32(kSram + sizeof sram - 4, &v) == sram_i);
    CHECK(map.read32(kSram + sizeof sram, &v) == kNotHandled);
    CHECK(map.read32(kSram + sizeof sram - 2, &v) == kNotHandled);
    CHECK(map.write32(kSram + sizeof sram - 2, 0) == kNotHandled);
    CHECK(map.read8(kSram + sizeof sram - 1, &v) == sram_i);
    CHECK(map.read16(kSram + sizeof sram - 1, &v) == kNotHandled);
    CHECK(map.read32(kSram - 4, &v) == kNotHandled);  // below a window: the unsigned wrap must not match
    CHECK(map.read32(0x40000000u, &v) == kNotHandled);

    // Sub-word writes land in the caller's bytes.
    CHECK(map.write8(kSram + 1, 0x1FF) == sram_i && sram[1] == 0xFF);
    CHECK(map.write16(kSram + 2, 0xBEEF) == sram_i && sram[2] == 0xEF && sram[3] == 0xBE);

    // attach() refuses what it cannot serve, and the table is bounded.
    MemoryMap small;
    CHECK(small.attach({0, 16, 16, 0xFFFFFFFFu, nullptr, 0}) == kNotHandled);      // no memory
    CHECK(small.attach({0, 16, 0, 0xFFFFFFFFu, rom, 0}) == kNotHandled);          // zero size
    CHECK(small.attach({0, 8, 16, 0xFFFFFFFFu, rom, 0}) == kNotHandled);          // window smaller than memory
    for (int i = 0; i < MemoryMap::kMaxRegions; ++i) CHECK(small.attach({0x1000u * i, 16, 16, 0xFFFFFFFFu, rom, 0}) == i);
    CHECK(small.attach({0x9000, 16, 16, 0xFFFFFFFFu, rom, 0}) == kNotHandled);     // full
    small.clear();
    CHECK(small.count() == 0 && small.read32(0, &v) == kNotHandled);

    std::printf(failures ? "FAILED: %d\n" : "memory_map: all checks passed\n", failures);
    return failures ? 1 : 0;
}
