// Standalone checks of RegisterBlock in src/rp2040py/native/core/core_host.hpp (see tests/test_core_cpp.py for the flags).
//
// The alias-write rule every register block shares: a SET / CLR / XOR alias is decoded against a read of the register, and a read that failed (a host function parked an error) stops the write -
// the reference's exception would have. A plain write is not refused for a failure parked earlier.
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

static int failed_flag = 0;
static ChipPorts ports;

// A register whose read has a side effect that can fail (a FIFO pop that tells the interrupt line), and whose write is recorded.
class Probe : public RegisterBlock<Probe> {
public:
    uint32_t reads = 0, writes = 0;
    int64_t last_written = -1;
    bool read_fails = false;
    uint32_t value = 0xF0;

    void init() { port_.init(&ports, 7); }
    uint32_t read(uint32_t) noexcept {
        ++reads;
        if (read_fails) failed_flag = 1;
        return value;
    }
    bool write(uint32_t, int64_t v) noexcept {
        ++writes;
        last_written = v;
        return true;
    }
};

static void fresh(Probe& p) {
    failed_flag = 0;
    ports.failed = &failed_flag;
    p.init();
}

static void test_an_alias_write_decodes_against_a_read() {
    Probe p;
    fresh(p);
    CHECK(p.write_atomic(0, 0x0F, kAtomicSet) && p.last_written == 0xFF && p.reads == 1);
    CHECK(p.write_atomic(0, 0x30, kAtomicClear) && p.last_written == 0xC0);
    CHECK(p.write_atomic(0, 0xFF, kAtomicXor) && p.last_written == 0x0F);
    CHECK(p.raw_write_value() == 0xFF);  // what the bus passed, not the decoded value
}

static void test_a_plain_write_reads_nothing() {
    Probe p;
    fresh(p);
    CHECK(p.write_atomic(0, 0x1234, kAtomicNormal) && p.last_written == 0x1234 && p.reads == 0 && p.raw_write_value() == 0x1234);
}

static void test_a_failing_decode_read_stops_the_write() {
    const uint32_t aliases[3] = {kAtomicSet, kAtomicClear, kAtomicXor};
    for (uint32_t alias : aliases) {
        Probe p;
        fresh(p);
        p.read_fails = true;
        CHECK(!p.write_atomic(0, 0x0F, alias));
        CHECK(p.reads == 1 && p.writes == 0 && p.failed());
        CHECK(p.raw_write_value() == 0x0F);  // remembered before the read, as the reference's attribute is
    }
}

static void test_a_plain_write_is_not_refused_for_an_earlier_failure() {
    Probe p;
    fresh(p);
    failed_flag = 1;  // parked earlier, by something else
    CHECK(p.write_atomic(0, 5, kAtomicNormal) && p.writes == 1 && p.last_written == 5);
}

static void test_the_window_handler_is_the_bus_entry_point() {
    Probe p;
    fresh(p);
    WindowHandler handler = p.window_handler();
    CHECK(handler.read32(handler.ctx, 0) == 0xF0u);
    handler.write32(handler.ctx, 0, 0x0F, kAtomicSet);
    CHECK(p.last_written == 0xFF);
}

static void test_a_block_with_no_ports_cannot_fail() {
    Probe p;  // never initialised: no ports, no flag
    p.read_fails = true;
    CHECK(!p.failed());
    CHECK(p.write_atomic(0, 1, kAtomicSet) && p.writes == 1);
    failed_flag = 0;
}

int main() {
    test_an_alias_write_decodes_against_a_read();
    test_a_plain_write_reads_nothing();
    test_a_failing_decode_read_stops_the_write();
    test_a_plain_write_is_not_refused_for_an_earlier_failure();
    test_the_window_handler_is_the_bus_entry_point();
    test_a_block_with_no_ports_cannot_fail();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all register-block checks passed\n");
    return 0;
}
