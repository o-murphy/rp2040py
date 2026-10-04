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

    // The 256 MiB-block index: regions that share a block still resolve in attach order, a region whose window spans a block boundary is found
    // from both blocks, a block no region touches serves nothing, and clear() forgets everything.
    {
        static uint8_t a[0x100], b[0x100], big[0x40];
        MemoryMap m;
        CHECK(m.attach({0x20000000u, 0x100, 0x100, 0xFFFFFFFFu, a, kSubWord}) == 0);
        CHECK(m.attach({0x20001000u, 0x100, 0x100, 0xFFFFFFFFu, b, kSubWord}) == 1);
        a[0] = 0x11;
        b[0] = 0x22;
        uint32_t w = 0;
        CHECK(m.read8(0x20000000u, &w) == 0 && w == 0x11);
        CHECK(m.read8(0x20001000u, &w) == 1 && w == 0x22);
        CHECK(m.read8(0x20000800u, &w) == kNotHandled);  // between them, same block
        CHECK(m.read8(0x30000000u, &w) == kNotHandled && m.write32(0x40000000u, 1) == kNotHandled);
        // Overlap inside one block: the first attached wins, as the ordered walk always decided.
        CHECK(m.attach({0x20000000u, 0x100, 0x100, 0xFFFFFFFFu, b, kSubWord}) == 2);
        a[4] = 0x5A;
        CHECK(m.read8(0x20000004u, &w) == 0 && w == 0x5A);
        // A window across the block boundary.
        MemoryMap n;
        CHECK(n.attach({0x0FFFFFF0u, 0x20, 0x20, 0xFFFFFFFFu, big, kSubWord}) == 0);
        big[0] = 0x77;
        big[0x10] = 0x88;
        CHECK(n.read8(0x0FFFFFF0u, &w) == 0 && w == 0x77);
        CHECK(n.read8(0x10000000u, &w) == 0 && w == 0x88);
        n.clear();
        CHECK(n.read8(0x10000000u, &w) == kNotHandled);
    }

    // The constant-base fast paths for flash and SRAM against the generic walk: same regions, same bytes, every width, addresses on and around
    // the edges of both (inside, at the end, straddling it, in the mirror, below the base, in the gap) - the answers and the memory must match.
    {
        static uint8_t fl_a[0x2000], fl_b[0x2000], ra_a[0x800], ra_b[0x800];
        for (uint32_t i = 0; i < sizeof fl_a; ++i) fl_a[i] = fl_b[i] = static_cast<uint8_t>(i * 7 + 3);
        for (uint32_t i = 0; i < sizeof ra_a; ++i) ra_a[i] = ra_b[i] = static_cast<uint8_t>(i * 13 + 5);
        MemoryMap fast, slow;
        fast.attach({0x10000000u, 0x40000u, sizeof fl_a, 0x1FFFu, fl_a, kSubWord});
        fast.attach({0x20000000u, sizeof ra_a, sizeof ra_a, 0xFFFFFFFFu, ra_a, kSubWord});
        slow.attach({0x10000000u, 0x40000u, sizeof fl_b, 0x1FFFu, fl_b, kSubWord});
        slow.attach({0x20000000u, sizeof ra_b, sizeof ra_b, 0xFFFFFFFFu, ra_b, kSubWord});
        slow.set_fast_paths(false);
        uint32_t seed = 12345;
        const uint32_t bases[] = {0x10000000u, 0x20000000u};
        const uint32_t spans[] = {0x2000, 0x800};
        for (int n = 0; n < 200000; ++n) {
            seed = seed * 1664525u + 1013904223u;
            const int which = (seed >> 24) & 1;
            uint32_t address = bases[which] + ((seed >> 3) % (spans[which] + 0x40)) - 0x20;  // 32 bytes either side of the ends
            if ((seed >> 20) % 7 == 0) address = 0x10000000u + 0x2000 + ((seed >> 5) % 0x4000);  // the mirror
            const uint32_t value = seed * 2654435761u;
            uint32_t a = 0xAAAAAAAAu, b = 0xBBBBBBBBu;
            int ra, rb;
            switch ((seed >> 16) % 6) {
                case 0: ra = fast.read32(address, &a); rb = slow.read32(address, &b); break;
                case 1: ra = fast.read16(address, &a); rb = slow.read16(address, &b); break;
                case 2: ra = fast.read8(address, &a); rb = slow.read8(address, &b); break;
                case 3: ra = fast.write32(address, value); rb = slow.write32(address, value); a = b = 0; break;
                case 4: ra = fast.write16(address, value); rb = slow.write16(address, value); a = b = 0; break;
                default: ra = fast.write8(address, value); rb = slow.write8(address, value); a = b = 0; break;
            }
            CHECK(ra == rb && (ra == kNotHandled || a == b));
            if (failures) break;
        }
        CHECK(std::memcmp(fl_a, fl_b, sizeof fl_a) == 0 && std::memcmp(ra_a, ra_b, sizeof ra_a) == 0);
    }

    std::printf(failures ? "FAILED: %d\n" : "memory_map: all checks passed\n", failures);
    return failures ? 1 : 0;
}
