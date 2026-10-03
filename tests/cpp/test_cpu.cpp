// Standalone checks of src/rp2040py/native/core/cpu.hpp (see tests/test_core_cpp.py for the flags). The thorough proof is
// tests/test_cpu_parity.py (random instruction streams against the pure-Python core); this pins the C++ API itself.
#include <cstdio>
#include <cstring>

#include "cpu.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static uint8_t sram[4096];
static int failed_flag = 0;
static uint32_t breaks[4];
static int break_count = 0;
static uint32_t logs[8][4];
static int log_count = 0;
static int bl_hooks = 0;

static void on_break(void*, uint32_t imm) noexcept { breaks[break_count++ & 3] = imm; }
static void bl_taken(void*, bool) noexcept { ++bl_hooks; }
static void log_fn(void*, uint32_t kind, uint32_t a, uint32_t b, uint32_t c) noexcept {
    if (log_count < 8) { logs[log_count][0] = kind; logs[log_count][1] = a; logs[log_count][2] = b; logs[log_count][3] = c; }
    ++log_count;
}
// A window that fails every read, like a Python peripheral that raised.
static uint32_t angry_read(void*, uint32_t) noexcept { failed_flag = 1; return 0; }
static void angry_write(void*, uint32_t, int64_t, uint32_t) noexcept { failed_flag = 1; }

static void put16(uint32_t addr, uint16_t v) { std::memcpy(sram + (addr - 0x20000000), &v, 2); }

int main() {
    Bus bus;
    bus.mem.attach({0x20000000, 4096, 4096, 0xFFFFFFFF, sram, kSubWord});
    bus.windows.attach(0x40000000, {angry_read, angry_write, nullptr});
    Cpu cpu;
    cpu.init(&bus, {on_break, bl_taken, log_fn, &failed_flag, nullptr}, 25);

    // movs r0,#5 ; movs r1,#3 ; adds r2,r0,r1 ; subs r3,r0,r1 ; cmp r0,#5
    put16(0x20000000, 0x2005);
    put16(0x20000002, 0x2103);
    put16(0x20000004, 0x1842);
    put16(0x20000006, 0x1a43);
    put16(0x20000008, 0x2805);
    cpu.registers[15] = 0x20000000;
    int total = 0;
    for (int i = 0; i < 5; ++i) total += cpu.execute();
    CHECK(cpu.registers[2] == 8 && cpu.registers[3] == 2 && cpu.z && cpu.c && total == 5 && cpu.cycles == 5);
    CHECK(cpu.registers[15] == 0x2000000A);

    // str r0,[r4,#0] ; ldr r5,[r4,#0] with r4 pointing into SRAM: the data round-trips and loads cost 1 + 1 cycles.
    cpu.registers[4] = 0x20000100;
    cpu.registers[0] = 0xCAFEBABE;
    put16(0x2000000A, 0x6020);
    put16(0x2000000C, 0x6825);
    CHECK(cpu.execute() == 2 && cpu.execute() == 2 && cpu.registers[5] == 0xCAFEBABE);

    // A load from a window that fails: the instruction reports a fault, the destination is untouched, cycles do not advance.
    cpu.registers[4] = 0x40000000;
    cpu.registers[5] = 0x1234;
    put16(0x2000000E, 0x6825);
    const uint64_t before = cpu.cycles;
    CHECK(cpu.execute() == kCpuFault && cpu.registers[5] == 0x1234 && cpu.cycles == before && cpu.registers[15] == 0x20000010);
    failed_flag = 0;

    // svc: sets the pending flag; the next instruction takes the exception (frame pushed, handler mode, vector read).
    cpu.registers[13] = 0x20000800;
    put16(0x20000010, 0xDF01);
    CHECK(cpu.execute() == 1 && cpu.pending_svcall && cpu.interrupts_updated);

    // bkpt reports the immediate; an undefined opcode logs the two warnings.
    cpu.pending_svcall = false;
    cpu.interrupts_updated = false;
    cpu.registers[15] = 0x20000020;
    put16(0x20000020, 0xBE07);
    CHECK(cpu.execute() == 1 && break_count == 1 && breaks[0] == 7 && cpu.break_rewind == 2);
    put16(0x20000022, 0xB6FF);  // no such instruction
    CHECK(cpu.execute() == 1 && log_count == 1 && logs[0][0] == kCpuLogInstrUnimplemented && logs[0][2] == 0xB6FF);

    // bl calls the hook only once one is installed.
    cpu.registers[15] = 0x20000030;
    put16(0x20000030, 0xF000);
    put16(0x20000032, 0xF800);
    CHECK(cpu.execute() == 3 && bl_hooks == 0);
    cpu.registers[15] = 0x20000030;
    cpu.bl_hook_enabled = true;
    CHECK(cpu.execute() == 3 && bl_hooks == 1);

    // wfi parks the core; an interrupt line wakes it only when it is enabled and takes the exception.
    cpu.registers[15] = 0x20000040;
    put16(0x20000040, 0xBF30);
    CHECK(cpu.execute() == 2 && cpu.waiting);
    CHECK(cpu.set_interrupt(3, true) && cpu.waiting && (cpu.pending_interrupts & 8));  // not enabled: stays asleep

    if (failures == 0) std::printf("cpu: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
