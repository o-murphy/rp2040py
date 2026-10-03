// Standalone checks of src/rp2040py/native/core/bus.hpp (see tests/test_core_cpp.py for the flags).
#include <cstdio>
#include <cstring>

#include "bus.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warns[16][2];
    int warn_count = 0;
    uint32_t dpram[8][2];
    int64_t dpram_value[8];
    int dpram_count = 0;
};

static void host_warn(void* ctx, uint32_t kind, uint32_t address) noexcept {
    Env* e = static_cast<Env*>(ctx);
    if (e->warn_count < 16) { e->warns[e->warn_count][0] = kind; e->warns[e->warn_count][1] = address; }
    ++e->warn_count;
}
static void host_dpram(void* ctx, uint32_t offset, int64_t value) noexcept {
    Env* e = static_cast<Env*>(ctx);
    if (e->dpram_count < 8) { e->dpram[e->dpram_count][0] = offset; e->dpram_value[e->dpram_count] = value; }
    ++e->dpram_count;
}

struct Block {
    uint32_t last_offset = 0, last_atomic = 99, reads = 0, writes = 0;
    int64_t last_value = 0;
    uint32_t read_value = 0;
};
static uint32_t block_read(void* ctx, uint32_t offset) noexcept {
    Block* b = static_cast<Block*>(ctx);
    ++b->reads;
    b->last_offset = offset;
    return b->read_value;
}
static void block_write(void* ctx, uint32_t offset, int64_t value, uint32_t atomic) noexcept {
    Block* b = static_cast<Block*>(ctx);
    ++b->writes;
    b->last_offset = offset;
    b->last_value = value;
    b->last_atomic = atomic;
}

static uint8_t sram[1024], dpram[256];

int main() {
    Env env;
    Bus bus;
    bus.init({host_warn, host_dpram, &env});
    bus.mem.attach({0x20000000, 1024, 1024, 0xFFFFFFFF, sram, kSubWord});
    bus.mem.attach({0x50100000, 256, 256, 0xFFFFFFFF, dpram, kSubWord | kNotifyOnWrite});
    Block sio, ppb, timer;
    bus.set_sio({block_read, block_write, &sio});
    bus.set_ppb({block_read, block_write, &ppb});
    bus.windows.attach(0x40054000, {block_read, block_write, &timer});

    // Memory regions first; sub-word accesses compose the word little-endian.
    bus.write32(0x20000010, 0x11223344);
    CHECK(bus.read32(0x20000010) == 0x11223344u && bus.read16(0x20000012) == 0x1122u && bus.read8(0x20000011) == 0x33u);
    bus.write8(0x20000013, 0xAB);
    bus.write16(0x20000010, 0xCDEF);
    CHECK(bus.read32(0x20000010) == 0xAB22CDEFu);
    CHECK(env.warn_count == 0);

    // A DPRAM write is announced with its offset and the caller's unmasked value; the data lands too.
    bus.write32(0x50100020, -2);
    CHECK(env.dpram_count == 1 && env.dpram[0][0] == 0x20 && env.dpram_value[0] == -2 && bus.read32(0x50100020) == 0xFFFFFFFEu);

    // SIO gets address - 0xD0000000 and the unmasked value; the PPB gets address & 0xFFF.
    sio.read_value = 0x5150;
    CHECK(bus.read32(0xD0000010) == 0x5150u && sio.last_offset == 0x10);
    bus.write32(0xD0000064, -16);
    CHECK(sio.last_offset == 0x64 && sio.last_value == -16 && sio.last_atomic == 0);
    CHECK(bus.read32(0xDFFFFFFC) == 0x5150u && sio.last_offset == 0x0FFFFFFC);  // the whole 256 MiB is SIO's
    ppb.read_value = 0xAA;
    CHECK(bus.read32(0xE000E100) == 0xAAu && ppb.last_offset == 0x100);
    bus.write32(0xE000ED04, 7);
    CHECK(ppb.last_offset == 0xD04 && ppb.last_value == 7);

    // A peripheral window: reads see the alias bits, writes the register offset; sub-word writes go to the window replicated.
    timer.read_value = 0x77;
    CHECK(bus.read32(0x40054000 + 0x2008) == 0x77u && timer.last_offset == 0x2008);
    bus.write8(0x40054039, 0xAB);
    CHECK(timer.last_offset == 0x38 && timer.last_value == 0xABABABAB);
    bus.write16(0x4005403A, 0xCDEF);
    CHECK(timer.last_value == 0xCDEFCDEF);
    CHECK(env.warn_count == 0);

    // Sub-word accesses to a block no window claims are a read-modify-write of the aligned word through the SIO handler.
    sio.read_value = 0x11223344;
    bus.write8(0xD0000001, 0xAA);
    CHECK(sio.last_offset == 0 && sio.last_value == 0x1122AA44);
    CHECK(bus.read8(0xD0000002) == 0x22u && bus.read16(0xD0000002) == 0x1122u);

    // Warnings: unaligned 32-bit read, unmapped read (all ones), unmapped write.
    bus.read32(0x20000011);
    CHECK(env.warn_count == 1 && env.warns[0][0] == kBusWarnUnalignedRead && env.warns[0][1] == 0x20000011);
    CHECK(bus.read32(0x60000000) == 0xFFFFFFFFu && env.warns[1][0] == kBusWarnInvalidRead && env.warns[1][1] == 0x60000000);
    bus.write32(0x60000004, 1);
    CHECK(env.warn_count == 3 && env.warns[2][0] == kBusWarnUndefinedWrite && env.warns[2][1] == 0x60000004);

    // No SIO/PPB handler set: those ranges are just unmapped.
    Bus bare;
    bare.init({host_warn, host_dpram, &env});
    CHECK(bare.read32(0xD0000000) == 0xFFFFFFFFu && env.warn_count == 4);

    if (failures == 0) std::printf("bus: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
