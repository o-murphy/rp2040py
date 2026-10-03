// Standalone checks of src/rp2040py/native/core/window_map.hpp (see tests/test_core_cpp.py for the flags).
#include <cstdio>

#include "window_map.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Block {
    uint32_t reads = 0, writes = 0;
    uint32_t last_offset = 0, last_atomic = 99;
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

static const uint32_t kTimer = 0x40054000, kUart = 0x40034000, kDma = 0x50000000;

int main() {
    Block timer, uart, replacement;
    timer.read_value = 0x11111111;
    uart.read_value = 0x22222222;
    replacement.read_value = 0x33333333;
    WindowMap map;
    uint32_t v = 0;

    const int t = map.attach(kTimer, {block_read, block_write, &timer});
    const int u = map.attach(kUart, {block_read, block_write, &uart});
    CHECK(t == 0 && u == 1 && map.count() == 2);

    // A read is routed by `address >> 14` and gets the offset inside the 16 KiB window, alias bits included.
    CHECK(map.read32(kTimer + 0x0C, &v) == t && v == 0x11111111 && timer.last_offset == 0x0C);
    CHECK(map.read32(kTimer + 0x2000 + 0x0C, &v) == t && timer.last_offset == 0x200C);
    CHECK(map.read32(kTimer + 0x3FFC, &v) == t && timer.last_offset == 0x3FFC);
    CHECK(map.read32(kUart + 8, &v) == u && v == 0x22222222);

    // A write gets the register offset, the alias as a separate type, and the caller's full-width value.
    CHECK(map.write32(kTimer + 0x38, 1) == t);
    CHECK(timer.last_offset == 0x38 && timer.last_atomic == kAtomicNormal && timer.last_value == 1);
    CHECK(map.write32(kTimer + 0x1000 + 0x38, 2) == t && timer.last_offset == 0x38 && timer.last_atomic == kAtomicXor);
    CHECK(map.write32(kTimer + 0x2000 + 0x38, 3) == t && timer.last_atomic == kAtomicSet);
    CHECK(map.write32(kTimer + 0x3000 + 0x38, 4) == t && timer.last_atomic == kAtomicClear);
    CHECK(map.write32(kTimer + 0x38, -2) == t && timer.last_value == -2);              // the sign survives
    CHECK(map.write32(kTimer + 0x38, 0x123456789ALL) == t && timer.last_value == 0x123456789ALL);  // so do the high bits

    // Neighbouring windows are different windows; unmapped addresses are not handled.
    CHECK(map.read32(kTimer + 0x4000, &v) == kNoWindow);
    CHECK(map.read32(kTimer - 4, &v) == kNoWindow);
    CHECK(map.write32(kDma, 0) == kNoWindow);
    CHECK(!map.has(kDma) && map.has(kTimer) && map.has(kTimer + 0x3FFF));

    // Attaching over an occupied window replaces its handler and keeps its slot (the override rule).
    CHECK(map.attach(kTimer + 0x100, {block_read, block_write, &replacement}) == t);
    CHECK(map.count() == 2);
    const uint32_t timer_reads = timer.reads;
    CHECK(map.read32(kTimer + 4, &v) == t && v == 0x33333333);
    CHECK(replacement.reads == 1 && timer.reads == timer_reads);  // the new handler served it, the old one did not

    // Detach: the window disappears, the others keep working (the table stays dense).
    CHECK(map.detach(kTimer) && !map.detach(kTimer));
    CHECK(map.count() == 1 && map.read32(kTimer, &v) == kNoWindow);
    CHECK(map.read32(kUart, &v) != kNoWindow && v == 0x22222222);

    // Unusable handlers and a full table are refused.
    CHECK(map.attach(kDma, {nullptr, block_write, &timer}) == kNoWindow);
    CHECK(map.attach(kDma, {block_read, nullptr, &timer}) == kNoWindow);
    WindowMap full;
    for (int i = 0; i < WindowMap::kMaxWindows; ++i) CHECK(full.attach(0x40000000u + (uint32_t(i) << kWindowShift), {block_read, block_write, &timer}) == i);
    CHECK(full.attach(0x60000000u, {block_read, block_write, &timer}) == kNoWindow);
    CHECK(full.attach(0x40000000u, {block_read, block_write, &uart}) == 0);  // replacing still works when full
    full.clear();
    CHECK(full.count() == 0 && !full.has(0x40000000u));

    std::printf(failures ? "FAILED: %d\n" : "window_map: all checks passed\n", failures);
    return failures ? 1 : 0;
}
