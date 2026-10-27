// Standalone checks of src/rp2040py/native/core/mcu.hpp and of the result `run_batch` gives (see tests/test_core_cpp.py for the flags).
#include <cstdio>
#include <cstring>

#include "mcu.hpp"

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
static void put16(uint32_t addr, uint16_t v) { std::memcpy(sram + (addr - 0x20000000), &v, 2); }

static double fake_now = 0.0, fake_step = 0.0;
static double monotonic(void*) noexcept { return fake_now += fake_step; }

static bool alarm_fail(void*) noexcept { return false; }

static uint8_t flash_image[65536];
static uint8_t usb_dpram[4096];

int main() {
    Mcu mcu;
    mcu.bus.mem.attach({0x20000000, 4096, 4096, 0xFFFFFFFF, sram, kSubWord});
    for (uint32_t a = 0; a < 6; a += 2) put16(0x20000000 + a, 0xBF00);  // nop, nop, nop
    put16(0x20000006, 0xE7FB);                                          // b .-6
    mcu.cpu.init(&mcu.bus, {}, 25);
    mcu.cpu.registers[15] = 0x20000000;
    Cpu& cpu = mcu.cpu;
    Clock& clock = mcu.clock();

    mcu.set_monotonic(monotonic);
    BatchParams limits;
    limits.instruction_ceiling = 1000;

    McuResult r;

    // Built with no clock the chip runs on its own; a clock handed to it is used where it is, and `use_clock(nullptr)` goes back to the owned one.
    CHECK(mcu.owns_clock() && &mcu.clock() == &clock);
    {
        Clock given;
        mcu.use_clock(&given);
        CHECK(!mcu.owns_clock() && &mcu.clock() == &given);
        mcu.use_clock(nullptr);
        CHECK(mcu.owns_clock() && &mcu.clock() == &clock);
    }

    // The ceiling ends it; the result says how many iterations and how much simulated time (ticks), which is what the clock advanced by.
    const int64_t t0 = clock.ticks();
    CHECK(mcu.run(limits, &r) == 0);
    CHECK(r.reason == kStopCeiling && r.iterations == 1000);
    CHECK(r.ticks_advanced == clock.ticks() - t0 && r.ticks_advanced > 0);
    CHECK(clock.nanos() == static_cast<double>(cpu.cycles) * limits.cycle_nanos);

    // The result is optional, and a run without one does the same thing.
    const uint64_t cycles_before = cpu.cycles;
    CHECK(mcu.run(limits) == 0 && cpu.cycles > cycles_before);

    // A stop request: no iteration, no time.
    CHECK(!mcu.stop_requested());
    mcu.request_stop();
    CHECK(mcu.stop_requested() && *mcu.stop_address() == 1);
    const uint64_t at_stop = cpu.cycles;
    CHECK(mcu.run(limits, &r) == 0 && r.reason == kStopRequested && r.iterations == 0 && r.ticks_advanced == 0 && cpu.cycles == at_stop);
    mcu.request_stop(false);
    CHECK(!mcu.stop_requested() && *mcu.stop_address() == 0);

    // The clock tree moved clk_sys: the batch ends before an instruction and says why.
    uint8_t clk_changed = 1;
    mcu.set_clk_changed_flag(&clk_changed);
    CHECK(mcu.run(limits, &r) == 0 && r.reason == kStopClockChanged && r.iterations == 0 && cpu.cycles == at_stop);
    clk_changed = 0;
    mcu.set_clk_changed_flag(nullptr);

    // The real-time budget: the monotonic clock jumps past it at the first check (every `check_interval` iterations).
    {
        BatchParams budget = limits;
        budget.check_interval = 8;
        budget.yield_budget_seconds = 0.005;
        fake_now = 0.0;
        fake_step = 1.0;  // each reading is a second later than the last
        CHECK(mcu.run(budget, &r) == 0 && r.reason == kStopBudget && r.iterations > 0 && r.iterations < 1000);
        fake_step = 0.0;
    }

    // An idle core jumps to the next alarm; while a device waits on the real world the jump is capped and the batch ends there (the host sleeps for the time it covered).
    {
        Mcu m;
        m.set_monotonic(monotonic);
        m.cpu.init(&m.bus, {}, 25);
        m.cpu.waiting = true;
        Alarm far;
        far.fire = [](void*) noexcept { return true; };
        m.clock().link(&far, 50e6);  // 50 ms away
        m.set_real_io(true);
        CHECK(m.real_io() && *m.real_io_address() == 1);
        CHECK(m.run(limits, &r) == 0 && r.reason == kStopPaced && r.iterations == 1);
        CHECK(r.ticks_advanced == nanos_to_ticks(limits.paced_idle_nanos) && m.clock().nanos() == limits.paced_idle_nanos);
        // without the wait the same jump goes the whole way to the alarm
        m.set_real_io(false);
        CHECK(m.run(limits, &r) == 0 && r.reason == kStopCeiling && m.clock().nanos() >= 50e6);  // (then the idle iterations with no alarm left run out the ceiling)
    }

    // A failing clock alarm is a fault: the result says so and the batch ends.
    {
        Mcu m;
        m.bus.mem.attach({0x20000000, 4096, 4096, 0xFFFFFFFF, sram, kSubWord});
        m.set_monotonic(monotonic);
        m.cpu.init(&m.bus, {}, 25);
        m.cpu.registers[15] = 0x20000000;
        Alarm bad;
        bad.fire = alarm_fail;
        m.clock().link(&bad, 8.0);  // due after the first instruction
        CHECK(m.run(limits, &r) == kBatchFault && r.reason == kStopFault);
    }

    // The interrupt lines: `set_interrupt` reaches the CPU, a hook takes the line instead of the CPU, and taking the hook away gives it back.
    {
        struct Seen {
            int n = 0;
            uint32_t irq = 0;
            bool level = false;
            bool result = true;
        } seen;
        Mcu m;
        m.cpu.init(&m.bus, {}, 25);
        CHECK(m.set_interrupt(5, true) && (m.cpu.pending_interrupts & (1u << 5)) != 0);
        CHECK(m.set_interrupt(5, false) && (m.cpu.pending_interrupts & (1u << 5)) == 0);
        m.set_irq_hook(
            [](void* ctx, uint32_t irq, bool level) noexcept {
                Seen* s = static_cast<Seen*>(ctx);
                ++s->n;
                s->irq = irq;
                s->level = level;
                return s->result;
            },
            &seen);
        CHECK(m.set_interrupt(7, true) && seen.n == 1 && seen.irq == 7 && seen.level);
        CHECK((m.cpu.pending_interrupts & (1u << 7)) == 0);  // the hook got the line instead of the CPU
        seen.result = false;
        CHECK(!m.set_interrupt(8, true));  // its failure is the caller's
        m.set_irq_hook(nullptr, nullptr);
        CHECK(m.set_interrupt(7, true) && seen.n == 2 && (m.cpu.pending_interrupts & (1u << 7)) != 0);

        // The adapters a shell hands a block: a fixed line, a line offset from a base, and IO_BANK0's, which is the pin bank's "any IRQ value".
        McuIrqLine fixed;
        fixed.mcu = &m;
        fixed.line = 20;
        CHECK(mcu_irq_line(&fixed, true) && (m.cpu.pending_interrupts & (1u << 20)) != 0);
        CHECK(mcu_irq_line(&fixed, false) && (m.cpu.pending_interrupts & (1u << 20)) == 0);
        McuIrqLines based;
        based.mcu = &m;
        based.base = 4;
        CHECK(mcu_irq_lines(&based, 3, true) && (m.cpu.pending_interrupts & (1u << 7)) != 0);
        PinBank bank;
        PinHost no_host;
        CHECK(bank.init(30, no_host));
        McuIoIrq io;
        io.mcu = &m;
        io.bank = &bank;
        io.line = 13;
        CHECK(mcu_io_interrupt(&io) && (m.cpu.pending_interrupts & (1u << 13)) == 0);  // nothing raises it
        bank.pin(3).irq_force_mask = 1;
        CHECK(mcu_io_interrupt(&io) && (m.cpu.pending_interrupts & (1u << 13)) != 0);  // one pin does
        bank.pin(3).irq_force_mask = 0;
        CHECK(mcu_io_interrupt(&io) && (m.cpu.pending_interrupts & (1u << 13)) == 0);  // and the line follows it down
    }

    // The DMA requests: `set_dreq` reaches the attached DMA block, a hook takes the request instead of it, none attached goes nowhere, and the adapters map a block's own shape onto
    // the absolute DREQ number.
    {
        Mcu m;
        DmaBlock& dma = m.dma();  // the requests go to the chip's own DMA block
        CHECK(m.set_dreq(5, true) && dma.dreq(5));
        CHECK(m.set_dreq(5, false) && !dma.dreq(5));

        McuDreqPair pair;
        pair.mcu = &m;
        pair.if_true = 20;
        pair.if_false = 21;
        CHECK(mcu_dreq_pair(&pair, true, true) && dma.dreq(20) && !dma.dreq(21));
        CHECK(mcu_dreq_pair(&pair, false, true) && dma.dreq(21));
        CHECK(mcu_dreq_pair(&pair, true, false) && !dma.dreq(20) && dma.dreq(21));

        // The observer (`DmaBlock::dreq_tap`): called first for every request, the DMA still gets it; a failing tap fails the request.
        struct Seen {
            int n = 0;
            uint32_t number = 0;
            bool asserted = false;
            bool result = true;
        } seen;
        dma.dreq_tap = [](void* ctx, uint32_t number, bool asserted) noexcept {
            Seen* x = static_cast<Seen*>(ctx);
            ++x->n;
            x->number = number;
            x->asserted = asserted;
            return x->result;
        };
        dma.dreq_tap_ctx = &seen;
        CHECK(m.set_dreq(12, true) && seen.n == 1 && seen.number == 12 && seen.asserted && dma.dreq(12));  // the observer saw it and the DMA got it
        seen.result = false;
        CHECK(!m.set_dreq(13, true));  // its failure is the caller's
        dma.dreq_tap = nullptr;
        CHECK(m.set_dreq(14, true) && seen.n == 2 && dma.dreq(14));
    }

    // The RTC, built by the chip (record 0096, 3a): `build()` initialises it on the chip's clock, maps its window and binds its line - all without a shell. The host only answers for
    // the warning. A write to INTF forces the line, a read of a register that does not exist is a warning, and the second counts on the chip's clock.
    {
        struct World {
            int warns = 0;
            uint32_t block = 99, kind = 99, offset = 0;
            int transmits = 0;
            int uart_bytes = 0;
            int resets = 0, triggers = 0, ticks = 0;
            int pin_changes = 0;
            int bus_resets = 0, usb_resets = 0, usb_enabled = 0;
            uint32_t pin_bank = 99, pin_number = 99;
            int pin_new = -1, pin_old = -1;
            double last_tick = -1.0;
            uint32_t uart_instance = 99, uart_value = 0;
            uint32_t transmit_instance = 99, transmit_value = 0;
        } world;
        Mcu m;
        m.cpu.init(&m.bus, {}, 25);
        McuHost host;
        host.ctx = &world;
        host.warn = [](void* ctx, uint32_t block, uint32_t kind, uint32_t offset, int64_t) {
            World* w = static_cast<World*>(ctx);
            ++w->warns;
            w->block = block;
            w->kind = kind;
            w->offset = offset;
        };
        host.uart_byte = [](void* ctx, uint32_t instance, uint32_t byte) {
            World* w = static_cast<World*>(ctx);
            ++w->uart_bytes;
            w->uart_instance = instance;
            w->uart_value = byte;
            return true;
        };
        host.bus_reset = [](void* ctx) {
            ++static_cast<World*>(ctx)->bus_resets;
            return true;
        };
        host.usb_reset = [](void* ctx) {
            ++static_cast<World*>(ctx)->usb_resets;
            return true;
        };
        host.pin_changed = [](void* ctx, uint32_t bank, uint32_t pin, int new_state, int old_state) {
            World* w = static_cast<World*>(ctx);
            ++w->pin_changes;
            w->pin_bank = bank;
            w->pin_number = pin;
            w->pin_new = new_state;
            w->pin_old = old_state;
            return true;
        };
        host.reset_requested = [](void* ctx, uint32_t cause) {
            World* w = static_cast<World*>(ctx);
            if (cause == McuHost::kResetRequestSysResetReq) {
                ++w->resets;
            } else {
                ++w->triggers;
            }
            return true;
        };
        host.spi_transmit = [](void* ctx, uint32_t instance, uint32_t value) {
            World* w = static_cast<World*>(ctx);
            ++w->transmits;
            w->transmit_instance = instance;
            w->transmit_value = value;
            return true;
        };
        for (uint32_t i = 0; i < sizeof flash_image; ++i) flash_image[i] = static_cast<uint8_t>(i * 7 + 3);
        m.set_flash(flash_image, sizeof flash_image);
        UsbHost usb_wire;  // the host's end of the USB wire, given in the host and read by `build()`
        usb_wire.ctx = &world;
        usb_wire.usb_enabled = [](void* ctx) {
            ++static_cast<World*>(ctx)->usb_enabled;
            return true;
        };
        host.usb_wire = &usb_wire;
        m.set_usb_dpram(usb_dpram);
        m.build(host);
        const uint32_t rtc = 0x4005C000;  // (literal: the constant under test must not vouch for itself)
        CHECK((m.cpu.pending_interrupts & (1u << 25)) == 0);
        m.bus.write32(rtc + 0x28, 1);  // INTF: force the interrupt
        CHECK((m.cpu.pending_interrupts & (1u << 25)) != 0);
        m.bus.write32(rtc + 0x28, 0);
        CHECK(m.bus.read32(rtc + 0x20) == 0 && m.bus.read32(rtc + 0x2C) == 0);  // INTR, INTS: nothing pending

        CHECK(world.warns == 0);
        (void)m.bus.read32(rtc + 0x3C);  // no such register
        CHECK(world.warns == 1 && world.block == kMcuBlockRtc && world.kind == kRegWarnRead && world.offset == 0x3C);

        // 1 Hz clk_rtc, 2024-02-29 (a leap year) 23:59:58: two seconds later it is 2024-03-01 00:00:00.
        CHECK(m.rtc().clk_rtc_changed(1.0));
        m.bus.write32(rtc + 0x00, 0);  // CLKDIV_M1: one clk_rtc tick per second
        m.bus.write32(rtc + 0x04, (2024u << 12) | (2u << 8) | 29u);
        m.bus.write32(rtc + 0x08, (4u << 24) | (23u << 16) | (59u << 8) | 58u);
        m.bus.write32(rtc + 0x0C, 1u << 4);  // LOAD
        m.bus.write32(rtc + 0x0C, 1u);       // ENABLE
        CHECK(m.rtc().running());
        CHECK(m.clock().tick(2e9));
        CHECK(m.bus.read32(rtc + 0x1C) == ((5u << 24) | 0u));  // RTC_0: Friday, 00:00:00 (dotw 4 -> 5)
        CHECK(m.bus.read32(rtc + 0x18) == ((2024u << 12) | (3u << 8) | 1u));  // RTC_1: 2024-03-01

        // TBMAN, built by the same call: PLATFORM reads ASIC and ignores a write, a register that does not exist warns with the TBMAN's block id.
        const uint32_t tbman = 0x4006C000;
        const int warns = world.warns;
        CHECK(m.bus.read32(tbman) == 1);
        m.bus.write32(tbman, 0xFFFFFFFFu);
        CHECK(m.bus.read32(tbman) == 1 && world.warns == warns);
        (void)m.bus.read32(tbman + 0x10);
        CHECK(world.warns == warns + 1 && world.block == kMcuBlockTbman && world.kind == kRegWarnRead && world.offset == 0x10);

        // BUSCTRL, VREG, SYSINFO and SYSCFG, built by the same call (group 1 of step 3a). Addresses and values are literals, so a wrong constant fails here.
        static uint8_t rom[0x40];
        m.bus.mem.attach({0x00000000, sizeof rom, sizeof rom, 0xFFFFFFFF, rom, kSubWord});
        m.bus.write32(0x40030000, 0xFFFFFFFFu);  // BUSCTRL.BUS_PRIORITY: four 1-bit fields
        CHECK(m.bus.read32(0x40030000) == 0x1111);
        CHECK(m.bus.read32(0x40064000) == 0xB1);  // VREG reset value
        m.bus.write32(0x40004000, 0x5);           // SYSCFG.PROC0_NMI_MASK is the chip's own CPU
        CHECK(m.cpu.interrupt_nmi_mask == 0x5 && m.bus.read32(0x40004000) == 0x5);
        rom[0x13] = 1;  // the bootrom version byte, read off the chip's own bus
        CHECK(m.bus.read32(0x40000000) == 0x10002927);
        rom[0x13] = 3;
        CHECK(m.bus.read32(0x40000000) == 0x20002927);  // B2: revision 2
        const int before = world.warns;
        (void)m.bus.read32(0x40030000 + 0x3C);
        CHECK(world.block == kMcuBlockBusctrl && world.warns == before + 1);
        (void)m.bus.read32(0x40064000 + 0x3C);
        CHECK(world.block == kMcuBlockVreg && world.warns == before + 2);
        (void)m.bus.read32(0x40000000 + 0x3C);
        CHECK(world.block == kMcuBlockSysinfo && world.warns == before + 3);
        (void)m.bus.read32(0x40004000 + 0x3C);
        CHECK(world.block == kMcuBlockSyscfg && world.warns == before + 4);

        // Group 2 (step 3a): TIMER, ADC, UART x2, SPI x2, I2C x2. Literals only.
        const uint32_t bases[8] = {0x40054000, 0x4004C000, 0x40034000, 0x40038000, 0x4003C000, 0x40040000, 0x40044000, 0x40048000};
        const uint32_t ids[8] = {kMcuBlockTimer, kMcuBlockAdc, kMcuBlockUart0, kMcuBlockUart1, kMcuBlockSpi0, kMcuBlockSpi1, kMcuBlockI2c0, kMcuBlockI2c1};
        for (int k = 0; k < 8; ++k) {  // each block warns under its own id (the instance ones through their McuInstance)
            const int w = world.warns;
            (void)m.bus.read32(bases[k] + 0x200);
            CHECK(world.warns == w + 1 && world.block == ids[k] && world.kind == kRegWarnRead && world.offset == 0x200);
        }
        m.bus.write32(0x40054000 + 0x3C, 1);  // TIMER.INTF: forces TIMER_IRQ_0
        CHECK((m.cpu.pending_interrupts & (1u << 0)) != 0);
        for (uint32_t u = 0; u < 2; ++u) {  // UART: the TX interrupt, and the TX request on the DMA
            const uint32_t base = bases[2 + u];
            m.bus.write32(base + 0x30, 0x301);  // UARTCR: UARTEN | TXE | RXE
            m.bus.write32(base + 0x38, 1u << 5);  // UARTIMSC: TX interrupt
            m.bus.write32(base + 0x00, 0x41);    // UARTDR
            CHECK((m.cpu.pending_interrupts & (1u << (20 + u))) != 0);
            m.bus.write32(base + 0x48, 1u << 1);  // UARTDMACR: TXDMAE
        }
        DmaBlock& dma_probe = m.dma();  // the requests go to the chip's own DMA block
        CHECK(m.set_dreq(40, true));
        for (uint32_t u = 0; u < 2; ++u) m.bus.write32(bases[2 + u] + 0x48, 0), m.bus.write32(bases[2 + u] + 0x48, 1u << 1);
        CHECK(dma_probe.dreq(20) && dma_probe.dreq(22) && !dma_probe.dreq(21));
        for (uint32_t i = 0; i < 2; ++i) {  // SPI: the TX request
            m.bus.write32(bases[4 + i] + 0x04, 1u << 1);  // SSPCR1: SSE
            m.bus.write32(bases[4 + i] + 0x24, 1u << 1);  // SSPDMACR: TXDMAE
        }
        CHECK(dma_probe.dreq(16) && dma_probe.dreq(18) && !dma_probe.dreq(17));
        m.bus.write32(bases[5] + 0x00, 0x7);  // SSPCR0: DSS = 8-bit frames
        m.bus.write32(bases[5] + 0x08, 0x5A);  // SSPDR of SPI1: a word on the wire, which the host (not the block) sees, with the instance
        CHECK(m.clock().tick(1e6));
        CHECK(world.transmits == 1 && world.transmit_instance == 1 && world.transmit_value == 0x5A);
        m.bus.write32(bases[4] + 0x14, 1u << 3);  // SSPIMSC: TXIM - the TX FIFO is under half full, so SPI0's line rises
        CHECK((m.cpu.pending_interrupts & (1u << 18)) != 0);
        m.bus.write32(bases[1] + 0x1C, 1);  // ADC.INTF: forces ADC_IRQ_FIFO
        CHECK((m.cpu.pending_interrupts & (1u << 22)) != 0);

        // The clock tree and the oscillators (group 3): built by the same call, and the tree is the chip's own - the UART's bit time comes from its clk_peri, not from the host.
        const uint32_t tree[5] = {0x40008000, 0x40028000, 0x4002C000, 0x40024000, 0x40060000};
        const uint32_t tree_ids[5] = {kMcuBlockClocks, kMcuBlockPllSys, kMcuBlockPllUsb, kMcuBlockXosc, kMcuBlockRosc};
        for (int k = 0; k < 5; ++k) {
            const int w = world.warns;
            (void)m.bus.read32(tree[k] + 0x200);
            CHECK(world.warns == w + 1 && world.block == tree_ids[k] && world.offset == 0x200);
        }
        CHECK(m.clocks().clk_sys() == 125e6 && m.clocks().clk_peri() == 125e6);  // the tree's power-on rate
        int ones = 0;
        for (int k = 0; k < 64; ++k) ones += static_cast<int>(m.bus.read32(0x40060000 + 0x1C) & 1);  // ROSC.RANDOMBIT: the block's own generator, with no host source
        CHECK(ones > 0 && ones < 64);
        // 115200 baud on a 125 MHz clk_peri: IBRD 67, FBRD 52, 8N1 -> a frame is 10 bits of 8.68 us; the byte reaches the host after the frame, not before.
        const int bytes_before = world.uart_bytes;
        m.bus.write32(0x40038000 + 0x24, 67);    // UART1 UARTIBRD
        m.bus.write32(0x40038000 + 0x28, 52);    // UARTFBRD
        m.bus.write32(0x40038000 + 0x2C, 0x60);  // UARTLCR_H: 8 data bits
        m.bus.write32(0x40038000 + 0x30, 0x301); // UARTCR
        m.bus.write32(0x40038000 + 0x00, 0x42);
        CHECK(m.clock().tick(50e3));
        CHECK(world.uart_bytes == bytes_before);
        CHECK(m.clock().tick(50e3));
        CHECK(world.uart_bytes == bytes_before + 1 && world.uart_instance == 1 && world.uart_value == 0x42);

        // PPB, the watchdog and the DMA (group 4): built by the same call; the watchdog's tick reaches the TIMER and SysTick inside, then the host.
        {
            const int w = world.warns;
            (void)m.bus.read32(0x40058000 + 0x200);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockWatchdog);
            (void)m.bus.read32(0x50000000 + 0x600);
            CHECK(world.warns == w + 2 && world.block == kMcuBlockDma);
        }
        m.bus.write32(0x50000000 + 0x408, 1);  // DMA.INTF0: forces DMA_IRQ_0 (11)
        CHECK((m.cpu.pending_interrupts & (1u << 11)) != 0);
        m.bus.write32(0x50000000 + 0x418, 1);  // DMA.INTF1: forces DMA_IRQ_1 (12)
        CHECK((m.cpu.pending_interrupts & (1u << 12)) != 0);
        CHECK(m.set_dreq(3, true) && m.dma().dreq(3));  // the chip's own DMA is the one the requests go to
        m.bus.write32(0x40058000 + 0x00, 1u << 31);  // WATCHDOG.CTRL.TRIGGER: the chip's reset is the host's
        CHECK(world.triggers == 1);
        m.watchdog().tick_tap = [](void* ctx, double hz) {  // an observer of the tick (the port a test uses to hear it)
            World* w = static_cast<World*>(ctx);
            ++w->ticks;
            w->last_tick = hz;
            return true;
        };
        m.watchdog().tick_tap_ctx = &world;
        m.watchdog().clk_ref_changed(12e6);
        m.bus.write32(0x40058000 + 0x2C, 12 | (1u << 9));  // WATCHDOG.TICK: 12 cycles of clk_ref, enabled -> 1 MHz
        CHECK(m.timer().tick_hz() == 1e6 && m.ppb().tick_hz() == 1e6);  // the TIMER and SysTick follow it in C++ ...
        CHECK(world.ticks >= 1 && world.last_tick == 1e6);              // ... and the observer heard of it
        CHECK(m.ppb(1).tick_hz() == 1e6);  // core 1's SysTick is fed the tick as well (the watchdog tells each core's PPB)

        // The second core's seams (record 0104, P0): present, inactive, and reached only through the bus's `initiator`.
        {
            CHECK(m.cpu1.initialised() && &m.core(0) == &m.cpu && &m.core(1) == &m.cpu1 && m.bus.initiator == 0);
            // CPUID is the initiator; the divider and the interpolators are per core.
            CHECK(m.bus.read32(0xD0000000 + 0x000) == 0);
            m.bus.initiator = 1;
            CHECK(m.bus.read32(0xD0000000 + 0x000) == 1);
            m.bus.write32(0xD0000000 + 0x60, 100);  // DIV_UDIVIDEND
            m.bus.write32(0xD0000000 + 0x64, 7);    // DIV_UDIVISOR
            CHECK(m.bus.read32(0xD0000000 + 0x70) == 14);  // DIV_QUOTIENT: core 1's own result
            const uint64_t cycles0 = m.cpu.cycles, cycles1 = m.cpu1.cycles;
            m.bus.write32(0xD0000000 + 0x64, 9);  // another divide: its cycles go to the initiating core
            CHECK(m.cpu1.cycles > cycles1 && m.cpu.cycles == cycles0);
            m.bus.initiator = 0;
            CHECK(m.bus.read32(0xD0000000 + 0x60) == 0 && m.bus.read32(0xD0000000 + 0x70) == 0);  // core 0's divider was never written
            // The PPB window is banked: core 1's SysTick reload is not core 0's.
            m.bus.write32(0xE000E014, 0x1234);  // SYST_RVR on core 0
            m.bus.initiator = 1;
            CHECK(m.bus.read32(0xE000E014) != 0x1234);
            m.bus.write32(0xE000E014, 0x4321);
            CHECK(m.bus.read32(0xE000E014) == 0x4321);
            m.bus.initiator = 0;
            CHECK(m.bus.read32(0xE000E014) == 0x1234);
            // A system interrupt reaches both NVICs; a GPIO bank one reaches the core it is for.
            CHECK((m.cpu1.pending_interrupts & (1u << 5)) == 0 && !m.core1_running());
            CHECK(m.set_interrupt(5, true) && (m.cpu.pending_interrupts & (1u << 5)) && !(m.cpu1.pending_interrupts & (1u << 5)));  // (not delivered while core 1 is not running ...)
            m.start_core1_interrupts();
            CHECK(m.core1_running() && (m.cpu1.pending_interrupts & (1u << 5)));  // ... but it starts with the levels the lines have
            CHECK(m.set_interrupt(5, false) && !(m.cpu.pending_interrupts & (1u << 5)) && !(m.cpu1.pending_interrupts & (1u << 5)));
            CHECK(m.set_interrupt(5, true) && (m.cpu1.pending_interrupts & (1u << 5)));
            CHECK(m.set_interrupt(5, false) && !(m.cpu1.pending_interrupts & (1u << 5)));
            CHECK(m.set_interrupt(kIrqIoBank0, true) && (m.cpu.pending_interrupts & (1u << kIrqIoBank0)) && !(m.cpu1.pending_interrupts & (1u << kIrqIoBank0)));
            CHECK(m.set_interrupt_core(1, kIrqIoBank0, true) && (m.cpu1.pending_interrupts & (1u << kIrqIoBank0)));
            CHECK(m.set_interrupt(kIrqIoBank0, false) && m.set_interrupt_core(1, kIrqIoBank0, false));
            // SEV on one core sets the other's event register (datasheet 2.3.3).
            m.cpu1.event_registered = false;
            static uint8_t sev_ram[16] = {0x40, 0xBF};  // sev
            m.bus.mem.attach({0x30000000, 16, 16, 0xFFFFFFFF, sev_ram, kSubWord});
            m.cpu.registers[15] = 0x30000000;
            const int sev_cycles = m.cpu.execute();
            CHECK(sev_cycles >= 0 && m.cpu1.event_registered);
            m.cpu1.event_registered = false;
        }
        m.bus.write32(0xE000ED0C, 0x05FA0004);  // AIRCR: VECTKEY | SYSRESETREQ
        CHECK(world.resets == 1);

        // The pins (group 5a): two banks and the IO / PADS blocks on them, held by the Mcu. Windows, warnings, the IO_BANK0 line and the listeners' notice.
        {
            const uint32_t pin_blocks[4] = {0x40014000, 0x40018000, 0x4001C000, 0x40020000};
            const uint32_t pin_ids[4] = {kMcuBlockIoBank0, kMcuBlockIoQspi, kMcuBlockPadsBank0, kMcuBlockPadsQspi};
            for (int k = 0; k < 4; ++k) {
                const int w = world.warns;
                (void)m.bus.read32(pin_blocks[k] + 0x600);
                CHECK(world.warns == w + 1 && world.block == pin_ids[k]);
            }
        }
        CHECK(m.bus.read32(0x4001C000 + 0x04) == 0x56 && m.bus.read32(0x40020000 + 0x04) == 0x56);  // PADS GPIO0 / SCLK power-on value (the chip's QSPI resets are the host's)
        CHECK(m.bus.read32(0x40014000 + 0x04) == 0x1F);                                          // IO_BANK0.GPIO0_CTRL: FUNCSEL null
        // IO_BANK0's line is "any GPIO pin's IRQ value": forcing one (PROC0_INTF0) raises it on the CPU, and clearing the force lowers it.
        CHECK((m.cpu.pending_interrupts & (1u << 13)) == 0);
        m.bus.write32(0x40014000 + 0x110, 1);
        CHECK((m.cpu.pending_interrupts & (1u << 13)) != 0);
        m.bus.write32(0x40014000 + 0x110, 0);
        CHECK((m.cpu.pending_interrupts & (1u << 13)) == 0);
        // A pin whose output the firmware drives (override: output high, enabled): the state change reaches the host's listeners with the bank and the pin.
        const int changes = world.pin_changes;
        m.bus.write32(0x40014000 + 0x04 + 8 * 5, 5 | (3u << 8) | (3u << 12));  // GPIO5_CTRL: SIO, OUTOVER = high, OEOVER = enable
        CHECK(world.pin_changes == changes + 1 && world.pin_bank == 0 && world.pin_number == 5 && world.pin_new == 1);  // HIGH

        // SIO (group 5b): its GPIO registers drive the pins with the sources bound inside, its GPIO_IN reads the bank's inputs, and an invalid address warns under its own id.
        {
            const int w = world.warns;
            (void)m.bus.read32(0xD0000000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockSio);
        }
        CHECK(m.pins(0).set_input_value(3, true));
        CHECK((m.bus.read32(0xD0000004) & (1u << 3)) != 0);  // GPIO_IN: pin 3's effective input
        m.bus.write32(0x40014000 + 0x04 + 8 * 7, 5);          // GPIO7_CTRL: FUNCSEL = SIO
        const int sio_changes = world.pin_changes;
        m.bus.write32(0xD0000000 + 0x24, 1u << 7);            // GPIO_OE_SET: pin 7 is an output
        m.bus.write32(0xD0000000 + 0x14, 1u << 7);            // GPIO_OUT_SET: ... driven high
        CHECK(world.pin_changes > sio_changes && world.pin_bank == 0 && world.pin_number == 7 && world.pin_new == 1);  // HIGH, told to the host's listeners
        m.bus.write32(0xD0000000 + 0x18, 1u << 7);            // GPIO_OUT_CLR
        CHECK(world.pin_new == 0);                            // LOW

        // PWM (group 5c): the Mcu builds it, binds its direction / value words to the pin banks' sources and takes its line (PWM_IRQ_WRAP, 4) and its DREQs.
        {
            const int w = world.warns;
            (void)m.bus.read32(0x40050000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockPwm);
        }
        CHECK((m.cpu.pending_interrupts & (1u << 4)) == 0);
        m.bus.write32(0x40050000 + 0xA8, 1);  // INTE: slice 0
        m.bus.write32(0x40050000 + 0xAC, 1);  // INTF: forces slice 0's wrap
        CHECK((m.cpu.pending_interrupts & (1u << 4)) != 0);
        m.bus.write32(0x40050000 + 0xAC, 0);
        CHECK((m.cpu.pending_interrupts & (1u << 4)) == 0);
        // Slice 3 on pin 6 (GPIO6 is PWM3 A): TOP 3, CC A 3 -> the level is high once enabled; the change reaches the host's listeners and the pin's status reads it.
        m.bus.write32(0x40014000 + 0x04 + 8 * 6, 4);  // GPIO6_CTRL: FUNCSEL = PWM
        m.bus.write32(0x40050000 + 3 * 0x14 + 0x10, 3);    // CH3_TOP
        m.bus.write32(0x40050000 + 3 * 0x14 + 0x0C, 3);    // CH3_CC: A = 3 (>= TOP: always high)
        const int pwm_changes = world.pin_changes;
        m.bus.write32(0x40050000 + 3 * 0x14 + 0x00, 1);    // CH3_CSR.EN
        CHECK(m.clock().tick(1e3));
        CHECK(world.pin_changes > pwm_changes);
        CHECK((m.bus.read32(0x40014000 + 8 * 6) & (1u << 8)) != 0);  // GPIO6_STATUS.OUTFROMPERI: the PWM's level, read through the bound source

        // The PIOs (group 5d): both built by the Mcu - the warning under each block's id, an invalid state machine register reported as such, the interrupt lines (PIO0_IRQ_0 = 7, PIO1_IRQ_0 = 9),
        // the TX request to the DMA, and the pins: a state machine's `set pindirs` / `set pins` reaches a pin whose function is PIO.
        {
            const int w = world.warns;
            (void)m.bus.read32(0x50200000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockPio0);
            (void)m.bus.read32(0x50300000 + 0x300);
            CHECK(world.warns == w + 2 && world.block == kMcuBlockPio1);
        }
        CHECK((m.cpu.pending_interrupts & (1u << 7)) == 0 && (m.cpu.pending_interrupts & (1u << 9)) == 0);
        m.bus.write32(0x50200000 + 0x12C, 1);  // PIO0 IRQ0_INTE: bit 0
        m.bus.write32(0x50200000 + 0x130, 1);  // IRQ0_INTF: forced
        CHECK((m.cpu.pending_interrupts & (1u << 7)) != 0 && (m.cpu.pending_interrupts & (1u << 9)) == 0);
        m.bus.write32(0x50300000 + 0x138, 1);  // PIO1 IRQ1_INTE
        m.bus.write32(0x50300000 + 0x13C, 1);  // IRQ1_INTF: PIO1's second line is 10
        CHECK((m.cpu.pending_interrupts & (1u << 10)) != 0);
        CHECK(m.dma().dreq(0) && !m.dma().dreq(8 + 4));  // PIO0 TX0 asks for data (its FIFO is empty); PIO1 RX0 does not (nothing was received)
        m.bus.write32(0x40014000 + 0x04 + 8 * 9, 6);  // GPIO9_CTRL: FUNCSEL = PIO0
        m.bus.write32(0x50200000 + 0x0DC, (1u << 26) | (9u << 5));    // SM0_PINCTRL: SET_COUNT 1, SET_BASE 9
        const int pio_changes = world.pin_changes;
        m.bus.write32(0x50200000 + 0x0D8, 0xE081);  // SM0_INSTR: set pindirs, 1
        m.bus.write32(0x50200000 + 0x0D8, 0xE001);  // SM0_INSTR: set pins, 1
        CHECK(m.pio_block(0)->check_changed_pins());  // (a forced instruction outside a cycle: the changed pins are checked on request)
        CHECK(world.pin_changes > pio_changes && world.pin_number == 9 && world.pin_new == 1);
        CHECK((m.bus.read32(0x40014000 + 8 * 9) & (1u << 8)) != 0);  // GPIO9_STATUS.OUTFROMPERI

        // The flash path (group 6): the SSI and XIP_CTRL are built by the Mcu on the chip's flash image.
        {
            const int w = world.warns;
            (void)m.bus.read32(0x18000000 + 0x100);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockSsi);
            (void)m.bus.read32(0x15000000);  // the cache-as-SRAM segment while the cache is enabled: the memory map's XIP hook reports it
            CHECK(world.warns == w + 2 && world.block == kMcuBlockXip);
        }
        CHECK(m.bus.read32(0x14000000 + 0x00) == 0x3);   // XIP_CTRL.CTRL: EN | ERR_BADWRITE
        CHECK(m.bus.read32(0x18000000 + 0x5C) != 0);     // SSI VERSION_ID: the window is the SSI's own
        // XIP_IRQ (6): the SSI's TX-empty interrupt, unmasked on an enabled SSI; and its TX request to the DMA (38).
        CHECK((m.cpu.pending_interrupts & (1u << 6)) == 0);
        m.bus.write32(0x18000000 + 0x08, 1);   // SSIENR
        m.bus.write32(0x18000000 + 0x2C, 1);   // IMR: TXEIM
        CHECK((m.cpu.pending_interrupts & (1u << 6)) != 0);
        m.bus.write32(0x18000000 + 0x2C, 0);
        CHECK((m.cpu.pending_interrupts & (1u << 6)) == 0);
        m.bus.write32(0x18000000 + 0x4C, 2);   // DMACR: TDMAE
        CHECK(m.dma().dreq(38));
        // The XIP stream reads the flash image by itself at the pace of clk_sys and SSI.BAUDR: DREQ_XIP_STREAM (37) when a word is ready, and XIP_AUX hands it over.
        m.bus.write32(0x18000000 + 0x14, 4);                 // SSI.BAUDR
        m.bus.write32(0x14000000 + 0x14, 0x100);             // STREAM_ADDR
        m.bus.write32(0x14000000 + 0x18, 1);                 // STREAM_CTR: one word
        CHECK(m.clock().tick(1e6));
        CHECK(m.dma().dreq(37));
        CHECK(m.bus.read32(0x50400000) == (flash_image[0x100] | flash_image[0x101] << 8 | flash_image[0x102] << 16 | static_cast<uint32_t>(flash_image[0x103]) << 24));

        // USB (group 7): the Mcu builds the controller on the chip's DPRAM - its window, its warning under its own id, and its line (USBCTRL_IRQ, 5); the host's end of the wire is given to the block afterwards
        // and must not take the line or the warning with it.
        {
            const int w = world.warns;
            (void)m.bus.read32(0x50110000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockUsb);
        }
        // the wire the host gave reaches the block: a controller enabled in device mode tells the host (and the line and the warning, which are the chip's, still work)
        CHECK(world.usb_enabled == 0);
        m.bus.write32(0x50110000 + 0x40, 1);  // MAIN_CTRL.CONTROLLER_EN
        CHECK(world.usb_enabled == 1);
        CHECK((m.cpu.pending_interrupts & (1u << 5)) == 0);
        m.bus.write32(0x50110000 + 0x90, 1);  // INTE
        m.bus.write32(0x50110000 + 0x94, 1);  // INTF: forced
        CHECK((m.cpu.pending_interrupts & (1u << 5)) != 0);
        m.bus.write32(0x50110000 + 0x94, 0);
        CHECK((m.cpu.pending_interrupts & (1u << 5)) == 0);
        {
            const int w = world.warns;
            (void)m.bus.read32(0x50110000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockUsb);  // still the chip's warning after the wire
        }

        // a host that gives no wire: nothing is connected, and nothing the firmware does calls a null pointer
        {
            static uint8_t bare_dpram[4096];
            Mcu bare;
            bare.set_usb_dpram(bare_dpram);
            McuHost none;
            bare.build(none);
            bare.bus.write32(0x50110000 + 0x40, 1);   // MAIN_CTRL.CONTROLLER_EN, device mode: the host is not asked
            bare.bus.write32(0x50110000 + 0x4C, 0x20);  // SIE_CTRL: some control bit
            CHECK(bare.bus.read32(0x50110000 + 0x40) == 1);
        }

        // RESETS and PSM (group 8): built by the Mcu - their windows, the warnings under their ids, the registers the cascade will read (WDSEL).
        {
            const int w = world.warns;
            (void)m.bus.read32(0x4000C000 + 0x300);
            CHECK(world.warns == w + 1 && world.block == kMcuBlockResets);
            (void)m.bus.read32(0x40010000 + 0x300);
            CHECK(world.warns == w + 2 && world.block == kMcuBlockPsm);
        }
        CHECK(m.bus.read32(0x4000C000 + 0x8) == 0x01FFFFFF);  // RESETS.RESET_DONE: every block out of reset
        m.bus.write32(0x4000C000 + 0x4, 0x1234);               // RESETS.WDSEL
        CHECK(m.resets().wdsel == 0x1234);
        m.bus.write32(0x40010000 + 0x8, 0x0155);               // PSM.WDSEL
        CHECK(m.psm().wdsel == 0x0155 && m.bus.read32(0x40010000 + 0xC) == 0x1FFFF);

        // The reset cascade (step 3b): what a reset covers, by cause and by PSM.WDSEL / RESETS.WDSEL (datasheet 2.13.5, 2.14.3; record 0089), the cause recorded after it, the flash, the hand-off to the host.
        {
            constexpr uint32_t kUart0Ibrd = 0x40034000 + 0x24, kPwmCh0Top = 0x40050000 + 0x10, kBusPriority = 0x40030000, kWdScratch0 = 0x40058000 + 0x0C, kChipReset = 0x40064000 + 0x8, kUsbAddrEndp = 0x50110000;
            const auto dirty = [&] {
                m.bus.write32(kUart0Ibrd, 67);
                m.bus.write32(kPwmCh0Top, 7);
                m.bus.write32(kBusPriority, 0x11);
                m.bus.write32(0x14000000, 0x1);  // XIP_CTRL.CTRL: EN only (reset: EN | ERR_BADWRITE)
                m.bus.write32(kWdScratch0, 0xCAFE);
                m.bus.write32(kUsbAddrEndp, 5);
            };
            // a watchdog reset that selects nothing: the core and the PPB only
            dirty();
            m.bus.write32(0x40010000 + 0x8, 0);   // PSM.WDSEL
            m.bus.write32(0x4000C000 + 0x4, 0);   // RESETS.WDSEL
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(kUart0Ibrd) == 67 && m.bus.read32(kPwmCh0Top) == 7 && m.bus.read32(kBusPriority) == 0x11 && m.bus.read32(0x14000000) == 1 && m.bus.read32(kUsbAddrEndp) == 5);
            // RESETS.WDSEL picks single blocks (UART0 bit 22, PWM bit 14)
            m.bus.write32(0x4000C000 + 0x4, (1u << 22) | (1u << 14));
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(kUart0Ibrd) == 0 && m.bus.read32(kPwmCh0Top) == 0xFFFF && m.bus.read32(kBusPriority) == 0x11 && m.bus.read32(kUsbAddrEndp) == 5);
            dirty();
            m.bus.write32(0x4000C000 + 0x4, 1u << 24);  // USBCTRL alone
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(kUsbAddrEndp) == 0 && m.bus.read32(kUart0Ibrd) == 67 && m.bus.read32(kPwmCh0Top) == 7);
            // PSM.WDSEL naming the RESETS controller itself resets every peripheral whatever RESETS.WDSEL says - and, the power-on sequence being what it is (datasheet 2.13.4: a selected block takes every
            // block after it with it; the bits are in sequence order), the XIP domain and the SIO behind it
            dirty();
            m.bus.write32(0x4000C000 + 0x4, 0);
            m.bus.write32(0x40010000 + 0x8, 0x8);
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(kUart0Ibrd) == 0 && m.bus.read32(kPwmCh0Top) == 0xFFFF && m.bus.read32(kBusPriority) == 0 && m.bus.read32(0x14000000) == 3 && m.bus.read32(kUsbAddrEndp) == 0);
            // the XIP domain (bit 12) takes the SIO and the blocks above, but not the peripherals before it
            dirty();
            m.bus.write32(0x40010000 + 0x8, 0x1000);
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(0x14000000) == 3 && m.bus.read32(kUart0Ibrd) == 67 && m.bus.read32(kPwmCh0Top) == 7);
            // the SIO alone (bit 14) resets only itself and what is above it: the peripherals and the XIP are left
            dirty();
            m.bus.write32(0x40010000 + 0x8, 0x4000);
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(0x14000000) == 1 && m.bus.read32(kUart0Ibrd) == 67 && m.bus.read32(kBusPriority) == 0x11);
            // the CLOCKS (bit 2) take the RESETS controller - so every peripheral - and the XIP behind them
            dirty();
            m.bus.write32(0x40010000 + 0x8, 0x4);
            CHECK(m.reset(true, true));
            CHECK(m.bus.read32(kUart0Ibrd) == 0 && m.bus.read32(0x14000000) == 3 && m.bus.read32(kUsbAddrEndp) == 0);
            // any other reset covers everything, whatever the WDSEL registers hold
            dirty();
            m.bus.write32(0x40010000 + 0x8, 0);
            const int usb_before = world.usb_resets;
            CHECK(m.reset(false, true));
            CHECK(world.usb_resets == usb_before + 1);  // the host drops its device just before the USB block's own reset
            CHECK(m.bus.read32(kUart0Ibrd) == 0 && m.bus.read32(kPwmCh0Top) == 0xFFFF && m.bus.read32(kBusPriority) == 0 && m.bus.read32(0x14000000) == 3);
            // the flash survives a live reset and is erased by a first one
            CHECK(flash_image[5] == static_cast<uint8_t>(5 * 7 + 3));
            CHECK(m.reset(false, false));
            CHECK(flash_image[5] == 0xFF && flash_image[sizeof flash_image - 1] == 0xFF);
            // the cause: a watchdog reset leaves REASON / SCRATCH and CHIP_RESET alone, anything else clears the watchdog's and records HAD_RUN / HAD_POR - after the reset - and tells the USB link either way
            dirty();
            const int buses = world.bus_resets;
            const uint32_t chip_reset = m.bus.read32(kChipReset);
            CHECK(m.enter_reset(kMcuResetWatchdog));
            CHECK(m.bus.read32(kWdScratch0) == 0xCAFE && m.bus.read32(kChipReset) == chip_reset && world.bus_resets == buses + 1);
            CHECK(m.enter_reset(kMcuResetRunPin));
            CHECK(m.bus.read32(kWdScratch0) == 0 && (m.bus.read32(kChipReset) & (1u << 16)) != 0 && world.bus_resets == buses + 2);
            CHECK(m.enter_reset(kMcuResetPowerOn));
            CHECK((m.bus.read32(kChipReset) & (1u << 8)) != 0 && (m.bus.read32(kChipReset) & (1u << 16)) == 0 && world.bus_resets == buses + 3);
            m.leave_reset();
            CHECK(m.cpu.registers[15] == 0x10000000);
        }

        // With no hook of the host's the chip resets itself (the default): the watchdog's TRIGGER is `enter_reset(WATCHDOG)` + `leave_reset()`, AIRCR.SYSRESETREQ the core and the PPB; the host is not asked.
        {
            m.set_reset_requested(nullptr);
            const int asked = world.resets + world.triggers, buses = world.bus_resets;
            m.cpu.registers[15] = 0x20000000;
            m.bus.write32(0x40058000 + 0x00, 1u << 31);  // WATCHDOG.CTRL.TRIGGER
            CHECK(m.cpu.registers[15] == 0x10000000 && world.bus_resets == buses + 1);
            m.cpu.registers[15] = 0x20000000;
            m.bus.write32(0xE000ED0C, 0x05FA0004);  // AIRCR: VECTKEY | SYSRESETREQ
            CHECK(m.cpu.registers[15] == 0x10000000);
            CHECK(world.resets + world.triggers == asked);
        }
    }

    if (failures == 0) std::printf("test_mcu: ok\n");
    return failures == 0 ? 0 : 1;
}
