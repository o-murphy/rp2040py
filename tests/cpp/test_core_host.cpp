// Standalone checks of src/rp2040py/native/core/core_host.hpp (see tests/test_core_cpp.py for the flags): what every block shares - the atomic-alias decode, the failure
// flag and the bus window adapter - pinned once instead of through each block.
#include <cstdio>

#include "core_host.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static void test_the_warning_kinds_are_the_three_the_python_blocks_log() {
    CHECK(kRegWarnRead == 0 && kRegWarnReadAtomicArea == 1 && kRegWarnWrite == 2);
}

static void test_decode_atomic_is_the_reference_decode() {
    CHECK(decode_atomic(kAtomicNormal, 0xF0, 0x0F) == 0x0F);       // a normal write is the raw value
    CHECK(decode_atomic(kAtomicXor, 0xF0, 0xFF) == 0x0F);
    CHECK(decode_atomic(kAtomicSet, 0xF0, 0x0F) == 0xFF);
    CHECK(decode_atomic(kAtomicClear, 0xFF, 0x0F) == 0xF0);
    CHECK(decode_atomic(99, 0xF0, 0x0F) == 0x0F);                  // an alias that is none of the three writes raw
    CHECK(decode_atomic(kAtomicClear, 0xFFFFFFFFLL, 0xFFFFFFFFLL) == 0);
    CHECK(decode_atomic(kAtomicSet, 0, -1) == -1);                 // 64-bit: the callers truncate what they store
}

static void test_the_failure_flag() {
    int flag = 0;
    CHECK(!host_failed(nullptr));  // a host that cannot fail
    CHECK(!host_failed(&flag));
    flag = 1;
    CHECK(host_failed(&flag));
    flag = -3;
    CHECK(host_failed(&flag));  // any nonzero
}

// Two blocks, one whose write_atomic returns a bool (the failure model) and one that returns nothing: the adapter serves both.
struct BoolBlock {
    uint32_t last_read_offset = 0, last_write_offset = 0, last_type = 99;
    int64_t last_raw = 0;
    uint32_t read(uint32_t offset) { last_read_offset = offset; return 0xA5A5A5A5u; }
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t type) { last_write_offset = offset; last_raw = raw; last_type = type; return false; }
};
struct VoidBlock {
    uint32_t writes = 0;
    uint32_t read(uint32_t offset) { return offset + 1; }
    void write_atomic(uint32_t, int64_t, uint32_t) { ++writes; }
};

static void test_the_window_adapter_forwards_reads_and_atomic_writes() {
    BoolBlock block;
    WindowHandler h = BlockWindow<BoolBlock>::handler(&block);
    CHECK(h.ctx == &block && h.read32 != nullptr && h.write32 != nullptr);
    CHECK(h.read32(h.ctx, 0x18) == 0xA5A5A5A5u && block.last_read_offset == 0x18);
    h.write32(h.ctx, 0x44, 0x1234, kAtomicSet);
    CHECK(block.last_write_offset == 0x44 && block.last_raw == 0x1234 && block.last_type == kAtomicSet);  // the bool is dropped, the write happened

    VoidBlock quiet;
    WindowHandler v = BlockWindow<VoidBlock>::handler(&quiet);
    CHECK(v.read32(v.ctx, 7) == 8);
    v.write32(v.ctx, 0, 0, kAtomicNormal);
    v.write32(v.ctx, 0, 0, kAtomicXor);
    CHECK(quiet.writes == 2);
}

int main() {
    test_the_warning_kinds_are_the_three_the_python_blocks_log();
    test_decode_atomic_is_the_reference_decode();
    test_the_failure_flag();
    test_the_window_adapter_forwards_reads_and_atomic_writes();
    if (failures == 0) std::printf("test_core_host: ok\n");
    return failures == 0 ? 0 : 1;
}
