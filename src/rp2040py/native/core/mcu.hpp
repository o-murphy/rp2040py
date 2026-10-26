// The chip as one object (docs/records/0096-cpp-mcu-core.md, step 2a of the plan of 2026-10-14): what a host asks of the core, instead of a host reaching for the CPU, the bus,
// the clock and the batch loop one by one. `Mcu` owns the CPU, the bus, the two PIO blocks (the loop steps them), the run flags and the host of the batch loop, and has the
// step API, `run()`; the other blocks, the reset cascade, the wiring and the host interface proper (the world in, the callbacks out) come in the steps after it, each its own
// go-ahead (3a: one block per commit; the PIO goes first because the loop depends on it).
//
// **The clock is the one thing `Mcu` owns only when nobody gives it one.** The chip can be handed a clock from outside (`RP2040(clock)`, a `Simulator` built around one), and
// that clock may already hold time and alarms, so it cannot be adopted into the chip; it is used where it is (`use_clock()`), and the host that replaces it points the chip at the new
// one. Built with no clock - a wasm host, a C++ test - the chip runs on the one it owns.
//
// **The run flags are the chip's.** `request_stop()` (any thread) and the "a device waits on the real world" byte are bytes of this object, read by the loop on every iteration, so
// a host that wants the loop stopped or paced writes the chip, not a buffer of its own that the chip has to be told about.
//
// `run()` is the batch loop of `batch.hpp` with a result: why it ended, how many iterations it did, and how much simulated time it covered - the three things a host that
// paces the core against a wall clock needs and `Simulator` does not read. Simulated time is the core's own; the host sleeps for what `ticks_advanced` says.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract, except that the stop byte may be written from another thread. Not movable or copyable:
// the CPU holds the address of the bus, the loop's host holds the address of the flags, and blocks will hold the address of the chip.
#ifndef RP2040PY_CORE_MCU_HPP
#define RP2040PY_CORE_MCU_HPP

#include <cstdint>
#include <cstring>

#include "adc.hpp"
#include "batch.hpp"
#include "bus.hpp"
#include "clock.hpp"
#include "batch_dual.hpp"
#include "cpu.hpp"
#include "dma.hpp"
#include "i2c.hpp"
#include "io.hpp"
#include "pads.hpp"
#include "pin.hpp"
#include "pll.hpp"
#include "ppb.hpp"
#include "pwm.hpp"
#include "pin.hpp"
#include "pio.hpp"
#include "psm.hpp"
#include "resets.hpp"
#include "ssi.hpp"
#include "usb.hpp"
#include "xip_ctrl.hpp"
#include "busctrl.hpp"
#include "clock_sinks.hpp"
#include "clocks.hpp"
#include "rosc.hpp"
#include "rtc.hpp"
#include "sio.hpp"
#include "spi.hpp"
#include "syscfg.hpp"
#include "sysinfo.hpp"
#include "tbman.hpp"
#include "timer.hpp"
#include "uart.hpp"
#include "vreg.hpp"
#include "watchdog.hpp"
#include "xosc.hpp"

namespace rp2040core {

// The limits of one `run()`: the batch's parameters (the cycle period, the instruction ceiling, the real-time budget, ...). Named for what a host calls it.
using McuLimits = BatchParams;
using McuResult = BatchResult;

// What the world supplies to a chip that builds itself (step 3a of the revised plan, record 0096): the one interface the blocks go through for what the core cannot answer alone.
// For now it is the warning (a register access the block does not implement: the logger of the host); the pins, the console and the rest come with the blocks that need them.
// `warn(ctx, block, kind, offset, value)`: `block` is a `McuBlock`, `kind` the `kRegWarn*` code of core_host.hpp. `failed` is the host's parked-failure flag, shared by every block.
enum McuResetCause : uint32_t { kMcuResetPowerOn = 0, kMcuResetRunPin = 1, kMcuResetWatchdog = 2 };  // why a chip-level reset happened (`ResetCause`)
enum McuBlock : uint32_t { kMcuBlockRtc = 0, kMcuBlockTbman = 1, kMcuBlockBusctrl = 2, kMcuBlockVreg = 3, kMcuBlockSysinfo = 4, kMcuBlockSyscfg = 5, kMcuBlockTimer = 6, kMcuBlockAdc = 7, kMcuBlockUart0 = 8, kMcuBlockUart1 = 9, kMcuBlockSpi0 = 10, kMcuBlockSpi1 = 11, kMcuBlockI2c0 = 12, kMcuBlockI2c1 = 13, kMcuBlockClocks = 14, kMcuBlockPllSys = 15, kMcuBlockPllUsb = 16, kMcuBlockXosc = 17, kMcuBlockRosc = 18, kMcuBlockPpb = 19, kMcuBlockWatchdog = 20, kMcuBlockDma = 21, kMcuBlockIoBank0 = 22, kMcuBlockIoQspi = 23, kMcuBlockPadsBank0 = 24, kMcuBlockPadsQspi = 25, kMcuBlockSio = 26, kMcuBlockPwm = 27, kMcuBlockPio0 = 28, kMcuBlockPio1 = 29, kMcuBlockSsi = 30, kMcuBlockXip = 31, kMcuBlockUsb = 32, kMcuBlockPsm = 33, kMcuBlockResets = 34 };
struct McuHost {
    void (*warn)(void* ctx, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;

    // The devices the world attaches to the peripherals, Every one is optional: with none, a bus has no device on it (an I2C that NACKs every
    // address, an SPI that reads back 0, an ADC that converts what the default device gives), a UART's bytes go nowhere. `instance` is the UART / SPI / I2C number.
    // False from any of them: the host has parked its error (as everywhere), and the block's access fails with it.
    bool (*uart_byte)(void* ctx, uint32_t instance, uint32_t byte) = nullptr;           // a transmitted byte
    bool (*uart_baud)(void* ctx, uint32_t instance) = nullptr;                          // IBRD/FBRD changed: the host reads the new baud rate off the block
    bool (*spi_transmit)(void* ctx, uint32_t instance, uint32_t value) = nullptr;       // a word is on the wire; the device answers with the block's `complete_transmit()`
    bool (*i2c_start)(void* ctx, uint32_t instance, bool repeated) = nullptr;           // the device is asked to produce a (repeated) START ...
    bool (*i2c_connect)(void* ctx, uint32_t instance, uint32_t address, uint32_t mode) = nullptr;  // ... to address a target (mode 0 = write, 1 = read) ...
    bool (*i2c_write)(void* ctx, uint32_t instance, uint32_t value) = nullptr;          // ... to take a byte ...
    bool (*i2c_read)(void* ctx, uint32_t instance, bool ack) = nullptr;                 // ... to give a byte ...
    bool (*i2c_stop)(void* ctx, uint32_t instance) = nullptr;                           // ... to produce a STOP; each answers with the block's `complete_*()`
    // The pins. `bank` is 0 for the 30 GPIO pins, 1 for the 6 QSPI pins. A pin's level comes from the registers of other blocks (SIO's, a PIO's, the PWM's), which the `Mcu` reads itself; a pin that changed state is announced to whoever listens on it (`pin_changed`, with the pin's number and the old and the new state code: the devices the host attached);
    // an input level that moved reaches the PWM and the PIOs' waits inside.
    bool (*pin_changed)(void* ctx, uint32_t bank, uint32_t pin, int new_state, int old_state) = nullptr;
    // A reset was asked for, and why: `kResetRequestSysResetReq` (AIRCR.SYSRESETREQ; without a host the core alone is reset) or `kResetRequestWatchdog` (the watchdog fired or was triggered; without a host
    // nothing happens). The host decides what a reset is - the chip's cascade (`enter_reset` / `leave_reset`) and whatever side effects it has on its own end (a USB device, a console). False: the host has parked its error.
    static constexpr uint32_t kResetRequestSysResetReq = 0, kResetRequestWatchdog = 1;
    bool (*reset_requested)(void* ctx, uint32_t cause) = nullptr;
    // The host's end of the USB wire (the device at the other end of the link: its setup / IN / OUT handling, the endpoint transfers and the notifications of `core/usb.hpp`'s `UsbHost`). Optional: with
    // none, the controller has nothing connected. Read once, by `build()`; the line, the warning and the failure flag of that struct are the chip's, not the host's.
    const UsbHost* usb_wire = nullptr;
    // The reset cascade (`Mcu::reset`): `usb_reset` comes before the USB block's own reset (the host drops the device and the transfers it queued), `bus_reset` is the USB link seeing the chip reset (the
    // device's `on_reset`). Both optional.
    bool (*usb_reset)(void* ctx) = nullptr;
    bool (*bus_reset)(void* ctx) = nullptr;
    bool (*pio_started)(void* ctx, uint32_t index) = nullptr;                           // CTRL started a stopped PIO: the host runs its "no owning Simulator" fallback (the asyncio task of `run()`)
    bool (*adc_read)(void* ctx, uint32_t channel) = nullptr;                            // a conversion was requested; the device answers by pushing a sample
    bool (*adc_channel_value)(void* ctx, uint32_t channel, int64_t* value) = nullptr;   // the analog value of a channel
};

constexpr uint32_t kRtcBase = 0x4005C000;    // RTC_BASE
constexpr uint32_t kPwmBase = 0x40050000, kIrqPwm = 4, kDreqPwm0 = 24;  // PWM_BASE, PWM_IRQ_WRAP, DREQ_PWM_WRAP0
constexpr uint32_t kUsbBase = 0x50110000, kIrqUsb = 5;  // USBCTRL_REGS_BASE, USBCTRL_IRQ
constexpr uint32_t kXipCtrlBase = 0x14000000, kSsiBase = 0x18000000, kXipAuxBase = 0x50400000, kIrqXip = 6, kDreqXipStream = 37, kDreqXipSsiTx = 38;  // XIP_CTRL_BASE, XIP_SSI_BASE, XIP_AUX_BASE, XIP_IRQ, DREQ_XIP_STREAM / _SSITX (DREQ_XIP_SSIRX, 39, follows it)
constexpr uint32_t kPio0Base = 0x50200000, kPio1Base = 0x50300000, kIrqPio0 = 7, kIrqPio1 = 9;  // PIO0_BASE, PIO1_BASE, PIO0_IRQ_0, PIO1_IRQ_0
constexpr uint32_t kIoBank0Base = 0x40014000, kIoQspiBase = 0x40018000, kPadsBank0Base = 0x4001C000, kPadsQspiBase = 0x40020000;
constexpr uint32_t kIrqIoBank0 = 13, kIrqIoQspi = 14;  // IO_IRQ_BANK0, IO_IRQ_QSPI
constexpr uint32_t kWatchdogBase = 0x40058000, kDmaBase = 0x50000000;
constexpr uint32_t kIrqDma[2] = {11, 12};
constexpr uint32_t kMaxHardwareIrq = 25;  // the highest NVIC line the chip wires (RTC_IRQ)
constexpr uint32_t kClocksBase = 0x40008000, kPllSysBase = 0x40028000, kPllUsbBase = 0x4002C000, kXoscBase = 0x40024000, kRoscBase = 0x40060000;
constexpr uint32_t kTimerBase = 0x40054000;  // TIMER_BASE (the lines are 0..3)
constexpr uint32_t kAdcBase = 0x4004C000;    // ADC_BASE
constexpr uint32_t kIrqAdc = 22;             // ADC_IRQ_FIFO
constexpr uint32_t kDreqAdc = 36;            // DREQ_ADC
constexpr uint32_t kUartBase[2] = {0x40034000, 0x40038000}, kIrqUart[2] = {20, 21}, kDreqUartTx[2] = {20, 22};  // (the RX request is the TX one + 1)
constexpr uint32_t kSpiBase[2] = {0x4003C000, 0x40040000}, kIrqSpi[2] = {18, 19}, kDreqSpiTx[2] = {16, 18};
constexpr uint32_t kI2cBase[2] = {0x40044000, 0x40048000}, kIrqI2c[2] = {23, 24};
constexpr uint32_t kTbmanBase = 0x4006C000;  // TBMAN_BASE
constexpr uint32_t kResetsBase = 0x4000C000, kPsmBase = 0x40010000;  // RESETS_BASE, PSM_BASE
constexpr uint32_t kBusctrlBase = 0x40030000;  // BUSCTRL_BASE
constexpr uint32_t kVregBase = 0x40064000;     // VREG_AND_CHIP_RESET_BASE
constexpr uint32_t kSysinfoBase = 0x40000000;  // SYSINFO_BASE
constexpr uint32_t kSyscfgBase = 0x40004000;   // SYSCFG_BASE
constexpr uint32_t kRomVersionAddress = 0x13;  // the bootrom's version byte (pico-sdk rp2040_rom_version(): the ROM header at 0x10, the fourth byte)
constexpr uint32_t kIrqRtc = 25;           // RTC_IRQ

class Mcu;
struct McuInstance {  // the context of one UART / SPI / I2C: the chip and which of the two it is
    Mcu* mcu = nullptr;
    uint32_t index = 0;
};

// The chip's memory: the host's buffers (see `Mcu::set_memory()`).
struct McuMemory {
    uint8_t* rom = nullptr;
    uint32_t rom_size = 16 * 1024;
    uint8_t* flash = nullptr;
    uint32_t flash_size = 16 * 1024 * 1024;
    uint8_t* sram = nullptr;
    uint32_t sram_size = 264 * 1024;
    uint8_t* dpram = nullptr;
    uint32_t dpram_size = 4 * 1024;
};

class Mcu {
public:
    static constexpr int kPioBlocks = 2;

    Mcu() noexcept {
        host_.stopped = &stop_;
        host_.real_io = &real_io_;
        host_.pio_count = kPioBlocks;
        for (int i = 0; i < kPioBlocks; ++i) host_.pio_stopped[i] = &pio_[i].stopped;
        host_.pio_advance = step_pio;
        host_.monotonic = no_time;
        host_.ctx = this;
    }
    Mcu(const Mcu&) = delete;
    Mcu& operator=(const Mcu&) = delete;
    ~Mcu() {  // (unlinks every alarm from the clock, which may be gone already: safe)
        rtc_.detach();
        clocks_.detach();
        watchdog_.detach();
        ppb_.detach();
        ppb1_.detach();
        own_dma_.detach();
        rosc_.detach();
        timer_.detach();
        pwm_.detach();
        ssi_.detach();
        xip_.detach();
        usb_.detach();
        adc_.detach();
        for (int i = 0; i < 2; ++i) {
            uart_[i].detach();
            spi_[i].detach();
        }
    }

    // Builds the blocks that live in this class: initialises each on the chip's clock, maps its window on the bus and binds its interrupt line, all here and not in a shell. Call once,
    // after `use_clock()` (the RTC's second is an alarm of that clock) and with the host that answers for what the chip cannot. The blocks come over one at a time (record 0096, 3a): the RTC first.
    void build(const McuHost& host) noexcept {
        host_world_ = host;
        ports_.warn = ports_warn;
        ports_.irq = ports_irq;
        ports_.dreq = ports_dreq;
        ports_.chip = this;
        ports_.failed = host.failed;
        if (!cpu.initialised()) {  // the core on the bus; a shell that wants the core's break / BL / log notices has initialised it with its own host already
            CpuHost cpu_host;
            cpu_host.failed = host.failed;
            cpu.init(&bus, cpu_host, kMaxHardwareIrq);
        }
        if (!cpu1.initialised()) {  // core 1: present from the start, never run until it is launched (record 0104, P0); it shares the bus and the opcode table
            CpuHost cpu_host;
            cpu_host.failed = host.failed;
            cpu1.init(&bus, cpu_host, kMaxHardwareIrq);
            cpu1.reset_state();
        }
        cpu.peer = &cpu1;
        cpu1.peer = &cpu;
        // The clock tree (CLOCKS, both PLLs, the crystal and the ring oscillator): built before the blocks that run from it. The PLLs take the crystal from the tree, the oscillators and the PLL
        // registers tell the tree when they change, and the tree reads both oscillators off their blocks. The crystal's frequency is the board's: `set_crystal_hz()`.
        PllHost ps;
        ps.changed = tree_changed;
        ps.ctx = this;
        pll_[0].init(&ports_, kMcuBlockPllSys, ps);
        pll_[1].init(&ports_, kMcuBlockPllUsb, ps);
        XoscHost xo;
        xo.stable_changed = tree_changed;
        xo.ctx = this;
        xosc_.init(&ports_, kMcuBlockXosc, xo);
        RoscHost ro;
        ro.frequency_changed = rosc_changed;  // (no random source: the block has its own)
        ro.ctx = this;
        rosc_.init(clock_, &ports_, kMcuBlockRosc, ro);
        ClocksHost ch;
        ch.ctx = this;
        clocks_.init(clock_, &ports_, kMcuBlockClocks, ch, &pll_[0], &pll_[1]);
        for (int i = 0; i < 2; ++i) pll_[i].bind_reference(clocks_xosc_hz, &clocks_);
        clocks_.bind_xosc(&xosc_);
        clocks_.bind_rosc(&rosc_);
        bus.windows.attach(kClocksBase, clocks_.window_handler());
        bus.windows.attach(kPllSysBase, pll_[0].window_handler());
        bus.windows.attach(kPllUsbBase, pll_[1].window_handler());
        bus.windows.attach(kXoscBase, xosc_.window_handler());
        bus.windows.attach(kRoscBase, rosc_.window_handler());

        // The pins: two banks (30 GPIO, 6 QSPI) and the four blocks that work on them. A bank asks the host only for what the chip does not hold (a replaced SIO, PIO or PWM) and tells it only of
        // the changes the host's listeners want; the IO_BANK0 line is the GPIO bank's "any pin's IRQ value", and the QSPI bank's changes re-evaluate the same line.
        for (uint32_t b = 0; b < 2; ++b) {
            bank_ctx_[b] = {this, b};
            PinHost ph;
            ph.on_change = pin_change;
            ph.io_interrupt = pin_io_interrupt;
            ph.input_changed = pin_input_changed;
            ph.ctx = &bank_ctx_[b];
            bool always[PinBank::kMaxPins];
            for (uint32_t k = 0; k < PinBank::kMaxPins; ++k) always[k] = b == 1;
            (void)(b == 0 ? gpio_bank_ : qspi_bank_).init(b == 0 ? 30 : 6, ph, always, 0);
        }
        io_bank0_.init(&gpio_bank_, false, &ports_, kMcuBlockIoBank0);
        io_qspi_.init(&qspi_bank_, true, &ports_, kMcuBlockIoQspi);
        pads_bank0_.init(&gpio_bank_, false, &ports_, kMcuBlockPadsBank0);
        pads_qspi_.init(&qspi_bank_, true, &ports_, kMcuBlockPadsQspi);
        bus.windows.attach(kIoBank0Base, io_bank0_.window_handler());
        bus.windows.attach(kIoQspiBase, io_qspi_.window_handler());
        bus.windows.attach(kPadsBank0Base, pads_bank0_.window_handler());
        bus.windows.attach(kPadsQspiBase, pads_qspi_.window_handler());

        // SIO: the single-cycle IO block (it has no window of the table: the bus reaches it on its own). Its GPIO registers are the pins' sources, and the pins' sources are bound to them (and to both
        // PIOs' pin images) by address, so a level evaluation never leaves C++. A replaced block unbinds its source, which goes back to the host.
        SioHost sh;
        sh.gpio_in = sio_gpio_in;
        sh.qspi_in = sio_qspi_in;
        sh.update_pins = sio_update_pins;
        sh.add_cycles = sio_add_cycles;
        sh.launch_core1 = sio_launch_core1;
        sh.send_event = sio_send_event;
        sh.ctx = this;
        sio_.init(&ports_, kMcuBlockSio, sh);
        sio_.bind_initiator(&bus.initiator);  // the divider, interpolators and CPUID are per core: the bus says which
        bus.set_sio(sio_.window_handler());
        for (uint32_t b = 0; b < 2; ++b) {
            PinBank& bank = pins(b);
            bank.bind_source(kSrcSioOe, &sio_.gpio_output_enable);
            bank.bind_source(kSrcSioValue, &sio_.gpio_value);
            bank.bind_source(kSrcSioQspiOe, &sio_.qspi_gpio_output_enable);
            bank.bind_source(kSrcSioQspiValue, &sio_.qspi_gpio_value);
            bank.bind_source(kSrcPio0Oe, &pio_[0].pin_directions);
            bank.bind_source(kSrcPio0Value, &pio_[0].pin_values);
            bank.bind_source(kSrcPio1Oe, &pio_[1].pin_directions);
            bank.bind_source(kSrcPio1Value, &pio_[1].pin_values);
        }

        // The PWM: its line, its requests and its pins are the chip's own; the pins read its direction and value words by address. Its pin reads and changes go to the GPIO bank (or the host's, for a bank
        // the host holds).
        PwmHost pw;
        pw.pin_changed = pwm_pin_changed;
        pw.pin_read = pwm_pin_read;
        pw.ctx = this;
        pwm_.init(clock_, &ports_, kMcuBlockPwm, kIrqPwm, kDreqPwm0, pw, clocks_.clk_sys());
        bus.windows.attach(kPwmBase, pwm_.window_handler());
        for (uint32_t b = 0; b < 2; ++b) {
            pins(b).bind_source(kSrcPwmDirection, &pwm_.gpio_direction);
            pins(b).bind_source(kSrcPwmValue, &pwm_.gpio_value);
        }

        // The core's own peripherals and the DMA: PPB (SysTick, NVIC, SCB on the CPU), the watchdog (it tells the TIMER and SysTick their tick) and the DMA (on the bus, reading clk_sys from the tree).
        PpbHost pp;
        pp.system_reset_request = ppb_reset_request;
        pp.ctx = this;
        ppb_.init(&cpu, clock_, &ports_, kMcuBlockPpb, pp, clocks_.clk_sys(), kMaxHardwareIrq);
        bus.set_ppb(ppb_.window_handler());
        ppb1_.init(&cpu1, clock_, &ports_, kMcuBlockPpb, pp, clocks_.clk_sys(), kMaxHardwareIrq);  // core 1's own SysTick / SCR / MPU / NVIC window, reached when the bus's initiator is 1
        bus.set_ppb_core(1, ppb1_.window_handler());

        WatchdogHost wd;
        wd.trigger = watchdog_trigger;
        wd.tick_changed = watchdog_tick;
        wd.ctx = this;
        watchdog_.init(clock_, &ports_, kMcuBlockWatchdog, wd);
        bus.windows.attach(kWatchdogBase, watchdog_.window_handler());

        DmaHost dm;
        dm.ctx = this;
        own_dma_.init(&bus, clock_, &ports_, kMcuBlockDma, kIrqDma[0], dm);  // (DMA_IRQ_1 is the next line)
        own_dma_.bind_clock(clocks_clk_sys, &clocks_, clocks_.clk_sys());
        bus.windows.attach(kDmaBase, own_dma_.window_handler());
        attach_dma(&own_dma_);

        // The two PIOs: their lines (IRQ0 and the next one), their requests and their pins are the chip's own; the pins are the GPIO bank's (or the host's, for a bank the host holds), and a warning
        // goes to the host under the block's id. The batch loop steps them (`host_.pio_advance`). Built after the DMA: a block publishes its FIFOs' requests when it starts.
        for (uint32_t i = 0; i < kPioBlocks; ++i) {
            pio_ctx_[i].mcu = this;
            pio_ctx_[i].index = i;
            PioHost ph;
            ph.pin_update = pio_pin_update;
            ph.pin_input = pio_pin_input;
            ph.started = pio_started;
            ph.ctx = &pio_ctx_[i];
            (void)pio_[i].init(&ports_, i == 0 ? kMcuBlockPio0 : kMcuBlockPio1, ph, i == 0 ? kIrqPio0 : kIrqPio1, i);
            bus.windows.attach(i == 0 ? kPio0Base : kPio1Base, pio_[i].window_handler());
        }
        bind_pio_pins();

        // The flash path: the SSI (the bootrom's flash commands, its chip select the QSPI_SS pin) and XIP_CTRL (the cache, the flash window and the stream) work on the chip's flash buffer
        // (`set_flash`). The XIP stream's pace derives from `clk_sys` (the tree) and SSI.BAUDR.
        if (flash_ != nullptr) {  // (an embedder with no flash image has neither block)
            SsiHost sd;  // (no Python chip-select here)
            ssi_.init(flash_, flash_size_, &ports_, kMcuBlockSsi, kIrqXip, kDreqXipSsiTx, sd);  // (the chip-select is a pin of the bank: `bind_ssi_cs`, no host edge)
            bus.windows.attach(kSsiBase, ssi_.window_handler());
            bind_ssi_cs();
            XipHost xh;
            xh.clk_sys = xip_clk_sys;
            xh.baudr = xip_baudr;
            xh.ctx = this;
            xip_.init(clock_, flash_, flash_size_, &ports_, kMcuBlockXip, kDreqXipStream, xh);
            bus.windows.attach(kXipCtrlBase, xip_.window_handler());
            bus.windows.attach(kXipAuxBase, xip_.aux_window_handler());
            bus.mem.set_xip(xip_.hooks());
            (void)xip_.reset();
        }

        // The USB controller: its line is the chip's own; the other end of the wire (the host's device, endpoints and callbacks) is the host's, given to the block by `usb().set_wire()`. The DPRAM is the
        // bus's memory region, handed over by `set_usb_dpram`.
        if (usb_dpram_ != nullptr) {
            usb_.init(clock_, usb_dpram_, &ports_, kMcuBlockUsb, kIrqUsb, host.usb_wire != nullptr ? *host.usb_wire : UsbHost());
            bus.windows.attach(kUsbBase, usb_.window_handler());
        }

        rtc_.init(clock_, &ports_, kMcuBlockRtc, kIrqRtc);
        bus.windows.attach(kRtcBase, rtc_.window_handler());

        // The blocks that only report (group 1 of the clean-up): the chip's one `ChipPorts` and their own id, no per-block host.
        tbman_.init(&ports_, kMcuBlockTbman);
        bus.windows.attach(kTbmanBase, tbman_.window_handler());

        busctrl_.init(&ports_, kMcuBlockBusctrl);
        bus.windows.attach(kBusctrlBase, busctrl_.window_handler());

        resets_.init(&ports_, kMcuBlockResets);
        bus.windows.attach(kResetsBase, resets_.window_handler());

        psm_.init(&ports_, kMcuBlockPsm);
        psm_.bind_proc1(psm_proc1, this);
        bus.windows.attach(kPsmBase, psm_.window_handler());

        vreg_.init(&ports_, kMcuBlockVreg);
        bus.windows.attach(kVregBase, vreg_.window_handler());

        SysInfoHost i;
        i.rom_version = sysinfo_rom_version;  // read off the chip's own bus: the bootrom the chip holds
        i.ctx = this;
        sysinfo_.init(&ports_, kMcuBlockSysinfo, i);
        bus.windows.attach(kSysinfoBase, sysinfo_.window_handler());

        syscfg_.init(&cpu, &ports_, kMcuBlockSyscfg);  // (PROC0_NMI_MASK is the chip's own CPU)
        syscfg_.bind_core1(&cpu1);
        bus.windows.attach(kSyscfgBase, syscfg_.window_handler());

        // The blocks with lines, requests and devices (group 2). Each instance's callbacks get its own `McuInstance` as their context.
        timer_.init(clock_, &ports_, kMcuBlockTimer, 0);  // (TIMER_0..3 are NVIC lines 0..3)
        bus.windows.attach(kTimerBase, timer_.window_handler());

        AdcHost ad;
        ad.read = adc_read;
        ad.channel_value = adc_channel_value;
        ad.ctx = this;
        adc_.init(clock_, &ports_, kMcuBlockAdc, kIrqAdc, kDreqAdc, ad);
        bus.windows.attach(kAdcBase, adc_.window_handler());

        for (uint32_t i = 0; i < 2; ++i) {
            instance_[i] = {this, i};
            UartHost u;
            u.on_byte = uart_byte;
            u.baud_changed = uart_baud;
            u.bit_ns = uart_bit_ns;
            u.ctx = &instance_[i];
            uart_[i].init(clock_, &ports_, i == 0 ? kMcuBlockUart0 : kMcuBlockUart1, kIrqUart[i], kDreqUartTx[i], u);
            bus.windows.attach(kUartBase[i], uart_[i].window_handler());

            SpiHost sp;
            sp.transmit = spi_transmit;
            sp.bit_ns = spi_bit_ns;
            sp.ctx = &instance_[i];
            spi_[i].init(clock_, &ports_, i == 0 ? kMcuBlockSpi0 : kMcuBlockSpi1, kIrqSpi[i], kDreqSpiTx[i], sp);
            bus.windows.attach(kSpiBase[i], spi_[i].window_handler());

            I2cHost ic;
            ic.start = i2c_start;
            ic.connect = i2c_connect;
            ic.write_byte = i2c_write;
            ic.read_byte = i2c_read;
            ic.stop = i2c_stop;
            ic.ctx = &instance_[i];
            i2c_[i].init(&ports_, i == 0 ? kMcuBlockI2c0 : kMcuBlockI2c1, kIrqI2c[i], ic);
            bus.windows.attach(kI2cBase[i], i2c_[i].window_handler());
        }
        bind_tree_sinks();
    }
    RtcBlock& rtc() noexcept { return rtc_; }
    TbmanBlock& tbman() noexcept { return tbman_; }
    BusctrlBlock& busctrl() noexcept { return busctrl_; }
    // ---- the reset cascade (record 0096, step 3b; the pure-Python chip's `reset()` / `enter_reset()` / `leave_reset()` are its reference) ----
    // `RESETS` and `PSM` decide what a watchdog reset covers (datasheet 2.13.5, 2.14.3; record 0089): `from_watchdog` reads PSM.WDSEL and RESETS.WDSEL (RESETS.WDSEL is all ones when PSM.WDSEL selects the
    // RESETS controller itself, which holds every peripheral in reset); any other reset covers everything. SRAM and the ROM survive every path, the flash everywhere but a first reset (`preserve_flash` false).
    // The core and the PPB are reset on every path (the one deliberate broadening, record 0096 F3).
    // The two WDSEL words are the ones the `Mcu`'s PSM and RESETS blocks hold.
    static constexpr uint32_t kPsmWdselSio = 0x4000, kPsmWdselXip = 0x1000, kPsmWdselResets = 0x8, kPsmWdselClocks = 0x4, kPsmWdselXosc = 0x2, kPsmWdselRosc = 0x1;
    static constexpr uint32_t kPsmBitsMask = 0x1FFFF, kResetsBitsMask = 0x01FFFFFF;
    bool reset(bool from_watchdog, bool preserve_flash) noexcept {
        uint32_t psm_wdsel = psm_.wdsel, resets_wdsel = resets_.wdsel;
        if (from_watchdog) {
            psm_wdsel &= kPsmBitsMask;
            // "if a peripheral in the power-on state machine has the WDSEL bit set, every peripheral after it in the power-on sequence will also be reset" (datasheet 2.13.4): the bits of PSM.WDSEL
            // are in sequence order (ROSC 0 .. PROC1 16), so the lowest bit set selects itself and everything above it.
            if (psm_wdsel != 0) psm_wdsel = kPsmBitsMask & ~((psm_wdsel & (~psm_wdsel + 1u)) - 1u);
            if ((psm_wdsel & kPsmWdselResets) != 0) resets_wdsel = kResetsBitsMask;
        } else {
            psm_wdsel = kPsmBitsMask;
            resets_wdsel = kResetsBitsMask;
        }
        if (!cpu.reset()) return false;
        cpu1_running_ = false;
        irq_slow_ = irq_hook_ != nullptr ? 1 : 0;
        cpu1.reset_state();  // (core 1 is not running: its state goes back to power-on without a vector fetch)
        if (!reset_step(kMcuBlockPpb)) return false;
        if ((psm_wdsel & kPsmWdselSio) != 0 && !reset_step(kMcuBlockSio)) return false;
        if ((psm_wdsel & kPsmWdselClocks) != 0 && !reset_step(kMcuBlockClocks)) return false;
        // the pins' registers go with their IO / PADS block (a pin bank the host holds is reset by the host with it)
        static constexpr struct { uint32_t bit; McuBlock block; } kGated[] = {
            {1u << 5, kMcuBlockIoBank0}, {1u << 8, kMcuBlockPadsBank0}, {1u << 6, kMcuBlockIoQspi}, {1u << 9, kMcuBlockPadsQspi}, {1u << 14, kMcuBlockPwm}, {1u << 2, kMcuBlockDma},
            {1u << 0, kMcuBlockAdc}, {1u << 21, kMcuBlockTimer}, {1u << 22, kMcuBlockUart0}, {1u << 23, kMcuBlockUart1}, {1u << 16, kMcuBlockSpi0}, {1u << 17, kMcuBlockSpi1},
            {1u << 3, kMcuBlockI2c0}, {1u << 4, kMcuBlockI2c1}, {1u << 10, kMcuBlockPio0}, {1u << 11, kMcuBlockPio1}, {1u << 24, kMcuBlockUsb}, {1u << 15, kMcuBlockRtc},
            {1u << 1, kMcuBlockBusctrl}, {1u << 18, kMcuBlockSyscfg},
        };  // RESETS bit -> block, in the order the reference resets them (SYSINFO and TBMAN hold no instance state)
        for (const auto& gate : kGated) {
            if ((resets_wdsel & gate.bit) != 0 && !reset_step(gate.block)) return false;
        }
        if ((psm_wdsel & kPsmWdselXosc) != 0 && !reset_step(kMcuBlockXosc)) return false;
        if ((psm_wdsel & kPsmWdselRosc) != 0 && !reset_step(kMcuBlockRosc)) return false;
        if ((psm_wdsel & kPsmWdselXip) != 0) {  // the XIP domain is a PSM bit: both halves reset together
            if (!reset_step(kMcuBlockXip) || !reset_step(kMcuBlockSsi)) return false;
        }
        if (!preserve_flash && flash_ != nullptr) {
            std::memset(flash_, 0xFF, flash_size_);  // erased flash reads 0xFF; 16 MB, so not a byte loop
        }
        return true;
    }
    // Into reset: the reset (flash kept), the USB link told (the device is off the bus either way, whatever WDSEL selected), then the cause recorded - after, so the wider reset cannot wipe it. A watchdog
    // reset is the absence of bookkeeping (REASON and SCRATCH survive, CHIP_RESET is left alone); anything else resets the watchdog and records HAD_RUN / HAD_POR.
    bool enter_reset(McuResetCause cause) noexcept {
        if (!reset(cause == kMcuResetWatchdog, true)) return false;
        if (host_world_.bus_reset != nullptr && !host_world_.bus_reset(host_world_.ctx)) return false;
        if (cause == kMcuResetWatchdog) return true;
        if (!reset_step(kMcuBlockWatchdog)) return false;
        const uint32_t flag = cause == kMcuResetRunPin ? vreg_regs::HAD_RUN : vreg_regs::HAD_POR;
        vreg_.record_reset_cause(flag);
        return true;
    }
    bool enter_reset_code(uint32_t cause) noexcept { return enter_reset(static_cast<McuResetCause>(cause)); }
    // Out of reset: the core at the flash's entry point, where the boot ROM would have left it (the ROM image is optional for a host, record 0096 F2).
    void leave_reset() noexcept { cpu.registers[15] = kFlashStart; }
    static constexpr uint32_t kFlashStart = 0x10000000, kFlashEnd = 0x14000000, kRamStart = 0x20000000, kDpramStart = 0x50100000;

    ResetsBlock& resets() noexcept { return resets_; }
    PsmBlock& psm() noexcept { return psm_; }
    VregBlock& vreg() noexcept { return vreg_; }
    SysInfoBlock& sysinfo() noexcept { return sysinfo_; }
    SyscfgBlock& syscfg() noexcept { return syscfg_; }
    PinBank& pins(uint32_t bank) noexcept { return bank == 0 ? gpio_bank_ : qspi_bank_; }
    IoBlock& io(uint32_t bank) noexcept { return bank == 0 ? io_bank0_ : io_qspi_; }
    PadsBlock& pads(uint32_t bank) noexcept { return bank == 0 ? pads_bank0_ : pads_qspi_; }
    SioBlock& sio() noexcept { return sio_; }
    PwmBlock& pwm() noexcept { return pwm_; }
    PpbBlock& ppb() noexcept { return ppb_; }
    PpbBlock& ppb(uint8_t core) noexcept { return core != 0 ? ppb1_ : ppb_; }
    WatchdogBlock& watchdog() noexcept { return watchdog_; }
    DmaBlock& dma() noexcept { return own_dma_; }
    ClocksBlock& clocks() noexcept { return clocks_; }
    PllBlock& pll(uint32_t i) noexcept { return pll_[i & 1]; }
    XoscBlock& xosc() noexcept { return xosc_; }
    RoscBlock& rosc() noexcept { return rosc_; }
    TimerBlock& timer() noexcept { return timer_; }
    AdcBlock& adc() noexcept { return adc_; }
    UartBlock& uart(uint32_t i) noexcept { return uart_[i & 1]; }
    SpiBlock& spi(uint32_t i) noexcept { return spi_[i & 1]; }
    I2cBlock& i2c(uint32_t i) noexcept { return i2c_[i & 1]; }

    // The parts the chip owns. A host fills the bus's memory map and window table and calls `cpu.init(&bus, host, max_irq)` itself until the host interface does it.
    Cpu cpu;
    Cpu cpu1;  // core 1: initialised by build(), inactive (nothing steps it) until a launch makes it run
    Bus bus;
    // The core by number (0 or 1).
    Cpu& core(uint8_t index) noexcept { return index != 0 ? cpu1 : cpu; }

    // The clock the chip runs on: its own, or the one the host gave it. Re-point whenever the host replaces it (a stale pointer is the only way to misuse this class).
    Clock& clock() noexcept { return *clock_; }
    void use_clock(Clock* clock) noexcept { clock_ = clock != nullptr ? clock : &own_clock_; }
    bool owns_clock() const noexcept { return clock_ == &own_clock_; }

    // The interrupt lines. Every block that raises one calls this (directly, through `mcu_irq_line` below, once its shell has bound it), and it reaches the CPU - unless the host has
    // put a hook over it (a test that spies on the lines, a host that wants to see them): the hook then gets the line *instead of* the CPU, as a replaced `set_interrupt` always did.
    // False: waking the core took an exception and its bus access failed (the host has parked its error).
    using IrqHook = bool (*)(void* ctx, uint32_t irq, bool level);
    bool set_interrupt(uint32_t irq, bool level) noexcept {
        if (irq_slow_ == 0) return cpu.set_interrupt(irq, level);  // (the one test the line has always paid: a hook, or - new - a running core 1, takes the longer way)
        return set_interrupt_slow(irq, level);
    }
    RP2040PY_NOINLINE bool set_interrupt_slow(uint32_t irq, bool level) noexcept {
        if (irq_hook_ != nullptr) return irq_hook_(irq_hook_ctx_, irq, level);
        return set_interrupt_unhooked(irq, level);
    }
    // The line to the cores, whatever hook is installed (what a hook that spies on the lines calls to let one through). Every NVIC has the same system interrupts routed to it (datasheet 2.3.2) -
    // except the two GPIO ones, which are one per bank PER CORE and are delivered by `set_interrupt_core()`; core 1 is reached once it runs.
    bool set_interrupt_unhooked(uint32_t irq, bool level) noexcept {
        if (!cpu.set_interrupt(irq, level)) return false;
        return !cpu1_running_ || irq == kIrqIoBank0 || irq == kIrqIoQspi || cpu1.set_interrupt(irq, level);
    }
    // Core 1 starts to take the system lines. Until then nothing reaches it (the RTC and friends re-announce their lines a half-million times through an idle boot, and any extra work per
    // announcement shows): its pending bits are, at the start, those core 0 has now - a line's pending bit is its level here - without the two per-core GPIO lines.
    void start_core1_interrupts() noexcept {
        cpu1.pending_interrupts = cpu.pending_interrupts & ~((1u << kIrqIoBank0) | (1u << kIrqIoQspi));
        cpu1.interrupts_updated = true;
        cpu1_running_ = true;
        irq_slow_ = 1;
    }
    bool core1_running() const noexcept { return cpu1_running_; }
    // One core's own line (IO_IRQ_BANK0 / IO_IRQ_QSPI carry a different level on each core).
    bool set_interrupt_core(uint8_t index, uint32_t irq, bool level) noexcept {
        if (irq_hook_ != nullptr && index == 0) return irq_hook_(irq_hook_ctx_, irq, level);
        return core(index).set_interrupt(irq, level);
    }
    void set_irq_hook(IrqHook hook, void* ctx) noexcept {
        irq_hook_ = hook;
        irq_hook_ctx_ = ctx;
        irq_slow_ = (hook != nullptr || cpu1_running_) ? 1 : 0;
    }

    // The DMA requests (step 2c). Every block with a DREQ calls this (directly, through the adapters below, once its shell has bound it) and it reaches the DMA block the host
    // attached - unless a hook is over it (a test double that records the requests, a DMA that is not the native one): the hook then gets the request *instead of* the DMA, as a
    // replaced `rp2040.dma` always did. With neither, a request goes nowhere (a chip without a DMA). False: waking a channel failed (the host has parked its error).
    // The attached block must outlive its attachment: the host re-attaches (or detaches, with null) whenever it replaces the DMA. Until the DMA is a member of this class (3a) it is the host's.
    using DreqHook = bool (*)(void* ctx, uint32_t number, bool asserted);
    bool set_dreq(uint32_t number, bool asserted) noexcept {
        if (dreq_hook_ != nullptr) return dreq_hook_(dreq_hook_ctx_, number, asserted);
        if (dma_ == nullptr) return true;
        if (asserted) return dma_->set_dreq(number);
        dma_->clear_dreq(number);
        return true;
    }
    // The chip's memory, the host's buffers (they outlive the chip and are never moved): the boot ROM, the flash image, the SRAM and the USB DPRAM, mapped on the bus as the RP2040 has them (the ROM
    // word-indexed at 0; the flash as four 16 MiB read aliases - XIP, NOALLOC, NOCACHE, NOCACHE_NOALLOC - read-only to the bus, writes and sub-word accesses only below the real size, every access
    // reported to the XIP block once it is built; the SRAM at 0x20000000; the DPRAM at 0x50100000, a completed word write reported to the host's `dpram_written`). Call once, before `build()`;
    // it hands the flash and the DPRAM to the blocks that work on them (`set_flash`, `set_usb_dpram`).
    void set_memory(const McuMemory& m) noexcept {
        Region region;
        region.base = 0;
        region.window = m.rom_size;
        region.size = m.rom_size;
        region.mask = 0xFFFFFFFFu;
        region.data = m.rom;
        region.flags = kWordIndexed;
        bus.mem.attach(region);
        region.base = kFlashStart;
        region.window = kFlashEnd - kFlashStart;
        region.size = m.flash_size;
        region.mask = 0x00FFFFFFu;
        region.data = m.flash;
        region.flags = kSubWord | kXip;
        bus.mem.attach(region);
        region.base = kRamStart;
        region.window = m.sram_size;
        region.size = m.sram_size;
        region.mask = 0xFFFFFFFFu;
        region.data = m.sram;
        region.flags = kSubWord;
        bus.mem.attach(region);
        region.base = kDpramStart;
        region.window = m.dpram_size;
        region.size = m.dpram_size;
        region.mask = 0xFFFFFFFFu;
        region.data = m.dpram;
        region.flags = kNotifyOnWrite;
        bus.mem.attach(region);
        set_flash(m.flash, m.flash_size);
        set_usb_dpram(m.dpram);
    }
    // The chip's flash image: the host's buffer, which outlives the chip. Set before `build()` (`set_memory()` does it).
    void set_flash(uint8_t* data, uint32_t size) noexcept {
        flash_ = data;
        flash_size_ = size;
    }
    // The USB DPRAM: the host's buffer (4 KiB), which outlives the chip. Set before `build()`.
    void set_usb_dpram(uint8_t* data) noexcept { usb_dpram_ = data; }
    UsbBlock& usb() noexcept { return usb_; }
    SsiBlock& ssi() noexcept { return ssi_; }
    XipBlock& xip() noexcept { return xip_; }
    void attach_dma(DmaBlock* dma) noexcept { dma_ = dma; }
    void set_dreq_hook(DreqHook hook, void* ctx) noexcept {
        dreq_hook_ = hook;
        dreq_hook_ctx_ = ctx;
    }

    // The run flags. `request_stop()` may be called from any thread; the loop leaves at its next iteration and `run()` then reports `kStopRequested`. `real_io` is nonzero while
    // some device waits on the *real* world (a relayed DNS reply, a TCP connect in flight): an idle jump is then capped and ends the batch, so simulated time cannot outrun the
    // wall clock while the reply is on its way. The addresses are for a host whose own object is a view of the byte (the Cython shell hands them to Python as buffers).
    void request_stop(bool stop = true) noexcept { stop_ = stop ? 1 : 0; }
    bool stop_requested() const noexcept { return stop_ != 0; }
    void set_real_io(bool waiting) noexcept { real_io_ = waiting ? 1 : 0; }
    bool real_io() const noexcept { return real_io_ != 0; }
    uint8_t* stop_address() noexcept { return const_cast<uint8_t*>(&stop_); }
    uint8_t* real_io_address() noexcept { return const_cast<uint8_t*>(&real_io_); }

    // The PIO blocks. The host initialises one (`init(host, first_irq, index)`) when it builds the block's shell; the loop steps whichever is running (`stopped == 0`).
    PioBlock* pio_block(int index) noexcept { return index >= 0 && index < kPioBlocks ? &pio_[index] : nullptr; }

    // What the loop reads between instructions besides what the chip owns: the "clk_sys changed" byte of the clock tree (null: the cycle period is fixed for the batch) and a
    // monotonic clock, in seconds, for the real-time budget (the default never runs the budget out). Both are the host's: the tree's byte is the tree's, and wall time is the
    // host's to give. The function receives the chip as its context.
    void set_clk_changed_flag(const volatile uint8_t* flag) noexcept { host_.clk_changed = flag; }
    void set_monotonic(double (*monotonic)(void* chip)) noexcept { host_.monotonic = monotonic != nullptr ? monotonic : no_time; }

    // The clock tree. The crystal is the board's (12 MHz on a Pico): a parameter of the world. A change of an oscillator or of a PLL re-derives the tree.
    void set_crystal_hz(double hz) noexcept { clocks_.set_crystal_hz(hz); }
    // The clock tree tells its consumers when a rate changes: SysTick, the PWM counters and the DMA (`clk_sys`), the watchdog's tick (`clk_ref`) and the RTC (`clk_rtc`), then the flag below. Registered by
    // `build()` in the reference's order (`update_clocks()`); a shell that puts its own consumer over one of them (an oracle's reference) clears the lists and registers its own.
    void bind_tree_sinks() noexcept {
        clocks_.clear_sinks();
        (void)clocks_.add_sys_sink(ppb_sys_sink, &ppb_);
        (void)clocks_.add_sys_sink(ppb_sys_sink, &ppb1_);  // the tree feeds each core's SysTick itself
        (void)clocks_.add_sys_sink(pwm_sys_sink, &pwm_);
        (void)clocks_.add_sys_sink(dma_sys_sink, &own_dma_);
        (void)clocks_.add_sys_sink(sys_changed_sink, this);
        (void)clocks_.add_ref_sink(watchdog_ref_sink, &watchdog_);
        (void)clocks_.add_rtc_sink(rtc_rtc_sink, &rtc_);
    }
    // Runs at the rate the clock tree has now: one instruction costs one `clk_sys` cycle, and a batch ends when `clk_sys` changes (so the next call starts at the new rate). What a wrapper calls in a loop
    // when it has no clock tree of its own to follow (the Python `Simulator` does it itself, with its own flag, and calls `run()`).
    int run_at_sys_clock(McuLimits limits, McuResult* result = nullptr) noexcept {
        clk_changed_ = 0;
        host_.clk_changed = &clk_changed_;
        limits.cycle_nanos = 1e9 / clocks_.clk_sys();
        return run(limits, result);
    }

    // Runs until the limits or a request end it. Returns 0, or `kBatchFault` when a clock alarm, the CPU or a host call failed - the host has parked its own error, and
    // `result->reason` is `kStopFault`.
    int run(const McuLimits& limits, McuResult* result = nullptr) noexcept {
        if (cpu1_running_) {  // (the loop for two cores; the single-core one is not touched)
            const int status = run_batch_dual(cpu, cpu1, bus, *clock_, host_, limits, result);
            if ((cpu1.registers[15] & ~1u) == kCore1RomReturn && cpu1.waiting) hold_core1();  // core 1 returned to the ROM: it waits to be launched again
            return status;
        }
        return run_batch(cpu, *clock_, host_, limits, result);
    }

    // ---- core 1 (record 0104, P1) ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    // On by default: the inter-core FIFOs and the launch handshake work. `set_core1_enabled(false)` gives back the old stub (the registers warn and read as 0xFFFFFFFF, nothing can launch core 1).
    void set_core1_enabled(bool on) noexcept { sio_.set_multicore(on); }
    // Core 1 starts at the vector table, stack and entry core 0 sent it (the handshake of datasheet 2.8.2, answered by the SIO). The batch ends at its next iteration (`clk_changed_`, the flag the loop
    // already tests) so that `run()` picks the loop for two cores for the next one.
    bool launch_core1(uint32_t vtor, uint32_t sp, uint32_t pc) noexcept {
        cpu1.reset_state();
        cpu1.vtor = vtor;
        cpu1.registers[13] = sp & 0xFFFFFFFCu;
        cpu1.registers[15] = pc & 0xFFFFFFFEu;
        cpu1.registers[14] = kCore1RomReturn | 1u;  // (an entry function that returns lands back in the ROM: see batch_dual.hpp)
        start_core1_interrupts();
        end_batch();
        return true;
    }
    // Core 1 goes back to the boot ROM (PSM.FRCE_OFF PROC1, `multicore_reset_core1()`): it stops, its FIFOs are emptied and a new handshake is needed.
    void hold_core1() noexcept {
        cpu1_running_ = false;
        irq_slow_ = irq_hook_ != nullptr ? 1 : 0;
        cpu1.reset_state();
        (void)sio_.reset_core1();
        end_batch();
    }
    // Ends the batch in progress at its next iteration, so that `run()` can pick the other loop for the next one: the flag the loop already tests - the chip's own, and the host's if it gave one
    // (`set_clk_changed_flag`; the host's must be writable, as the Simulator's byte is).
    void end_batch() noexcept {
        clk_changed_ = 1;
        if (host_.clk_changed != nullptr) *const_cast<volatile uint8_t*>(host_.clk_changed) = 1;
    }

private:
    // One block of the cascade.
    bool reset_step(McuBlock block) noexcept {
        switch (block) {
            case kMcuBlockPpb: ppb_.reset(); ppb1_.reset(); return true;
            case kMcuBlockSio: sio_.reset(); return true;
            case kMcuBlockClocks: clocks_.reset(); return clocks_.reset_tree();
            case kMcuBlockIoBank0:
            case kMcuBlockPadsBank0:
            case kMcuBlockIoQspi:
            case kMcuBlockPadsQspi: {
                const uint32_t bank = (block == kMcuBlockIoQspi || block == kMcuBlockPadsQspi) ? 1 : 0;
                const bool pads = block == kMcuBlockPadsBank0 || block == kMcuBlockPadsQspi;
                // PADS_QSPI does not share BANK0's reset value: GPIO_QSPI_SS's pull-up is what holds the flash deselected and what makes BOOTSEL readable (record 0050); SCLK, SS, SD0-SD3.
                static constexpr uint32_t kQspiPad[6] = {0x56, 0x5A, 0x52, 0x52, 0x52, 0x52};
                PinBank& b = pins(bank);
                for (uint32_t i = 0; i < b.count(); ++i) {
                    if (!b.reset(i, !pads, pads, (pads && bank == 1) ? kQspiPad[i] : pin_bits::kDefaultPad)) return false;
                }
                if (bank == 0) {
                    if (pads) pads_bank0_.reset(); else io_bank0_.reset();
                } else {
                    if (pads) pads_qspi_.reset(); else io_qspi_.reset();
                }
                return true;
            }
            case kMcuBlockPwm: return pwm_.reset();
            case kMcuBlockDma: return own_dma_.reset();
            case kMcuBlockAdc: return adc_.reset();
            case kMcuBlockTimer: return timer_.reset();
            case kMcuBlockUart0: return uart_[0].reset();
            case kMcuBlockUart1: return uart_[1].reset();
            case kMcuBlockSpi0: return spi_[0].reset();
            case kMcuBlockSpi1: return spi_[1].reset();
            case kMcuBlockI2c0: return i2c_[0].reset();
            case kMcuBlockI2c1: return i2c_[1].reset();
            case kMcuBlockPio0: return pio_[0].reset();
            case kMcuBlockPio1: return pio_[1].reset();
            case kMcuBlockUsb:
                if (host_world_.usb_reset != nullptr && !host_world_.usb_reset(host_world_.ctx)) return false;
                return usb_.reset();
            case kMcuBlockRtc: return rtc_.reset();
            case kMcuBlockBusctrl: return busctrl_.reset();
            case kMcuBlockSyscfg: return syscfg_.reset();
            case kMcuBlockXosc: return xosc_.reset();
            case kMcuBlockRosc: return rosc_.reset();
            case kMcuBlockXip: return xip_.reset();
            case kMcuBlockSsi: return ssi_.reset();
            case kMcuBlockWatchdog: return watchdog_.reset();
            default: return true;
        }
    }
    static bool sys_changed_sink(void* chip, double, double) noexcept {
        static_cast<Mcu*>(chip)->clk_changed_ = 1;
        return true;
    }
    static bool step_pio(void* chip, int index, int64_t cycles) noexcept { return static_cast<Mcu*>(chip)->pio_[index].advance(cycles); }
    static double no_time(void*) noexcept { return 0.0; }
    // The chip's `ChipPorts::warn`: the block's own id and the numbers onto the host's `warn(ctx, block, kind, offset, value)`.
    static void ports_warn(void* chip, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept {
        Mcu* mcu = static_cast<Mcu*>(chip);
        if (mcu->host_world_.warn != nullptr) mcu->host_world_.warn(mcu->host_world_.ctx, block, kind, offset, value);
    }
    static bool ports_irq(void* chip, uint32_t line, bool level) noexcept { return static_cast<Mcu*>(chip)->set_interrupt(line, level); }
    static bool ports_dreq(void* chip, uint32_t number, bool asserted) noexcept { return static_cast<Mcu*>(chip)->set_dreq(number, asserted); }
    static bool tree_changed(void* chip) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        return m->clocks_.retune();
    }
    // SIO's host: the pins' effective inputs, the pins whose SIO-driven state moved, and the cycles SIO charges the CPU.
    // The PIOs' host: `chip` is the `Mcu` for the line and the requests (the PIO passes absolute numbers), a `McuInstance` for the rest.
    // (reached only for a pin of a bank the host holds: the others the block reads and updates directly)
    static bool pio_pin_update(void* ctx, uint32_t pin) noexcept { return sio_update_pins(static_cast<const McuInstance*>(ctx)->mcu, 1u << pin, false); }
    static bool pio_pin_input(void* ctx, uint32_t pin, bool* level) noexcept {
        *level = (sio_inputs(static_cast<const McuInstance*>(ctx)->mcu, 0) >> pin & 1u) != 0;
        return true;
    }
    static bool pio_started(void* ctx) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.pio_started == nullptr || w.pio_started(w.ctx, i->index);
    }
    // The PIOs read and update the GPIO bank's pins directly.
    void bind_pio_pins() noexcept {
        for (int p = 0; p < kPioBlocks; ++p) {
            for (uint32_t pin = 0; pin < 30; ++pin) pio_[p].bind_pin(pin, &gpio_bank_, pin);
        }
    }
    // The SSI's and XIP_CTRL's hosts.
    static bool xip_clk_sys(void* chip, double* hz) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        *hz = m->clocks_.clk_sys();
        return true;
    }
    static bool xip_baudr(void* chip, double* value) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        *value = static_cast<double>(m->ssi_.baudr() & 0xFFFFu);
        return true;
    }
    // The SSI listens to QSPI_SS (slot 1 of the QSPI bank) directly.
    void bind_ssi_cs() noexcept {
        ssi_.detach();
        (void)ssi_.bind_cs(&qspi_bank_, 1);
    }
    // The PWM's host: its line and requests, and the pins it drives and reads (the GPIO bank).
    static bool pwm_pin_changed(void* chip, uint32_t pin) noexcept { return sio_update_pins(chip, 1u << pin, false); }
    static bool pwm_pin_read(void* chip, uint32_t pin, bool* level) noexcept {
        *level = (sio_inputs(static_cast<Mcu*>(chip), 0) >> pin & 1u) != 0;
        return true;
    }
    static uint32_t sio_inputs(Mcu* m, uint32_t bank) noexcept {
        return m->pins(bank).input_values();
    }
    static uint32_t sio_gpio_in(void* chip) noexcept { return sio_inputs(static_cast<Mcu*>(chip), 0); }
    static uint32_t sio_qspi_in(void* chip) noexcept { return sio_inputs(static_cast<Mcu*>(chip), 1); }
    static bool sio_update_pins(void* chip, uint32_t pin_mask, bool qspi) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        const uint32_t b = qspi ? 1 : 0;
        PinBank& bank = m->pins(b);
        for (uint32_t i = 0; i < bank.count() && i < 32; ++i) {
            if ((pin_mask & (1u << i)) != 0 && !bank.check_for_updates(i)) return false;
        }
        return true;
    }
    static void sio_send_event(void* chip, uint32_t core) noexcept {
        Cpu& target = static_cast<Mcu*>(chip)->core(static_cast<uint8_t>(core));
        if (target.waiting && target.event_wait) {
            target.waiting = target.event_wait = false;
        } else {
            target.event_registered = true;
        }
    }
    static bool sio_launch_core1(void* chip, uint32_t vtor, uint32_t sp, uint32_t pc) noexcept { return static_cast<Mcu*>(chip)->launch_core1(vtor, sp, pc); }
    static void psm_proc1(void* chip, bool held) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        if (held) {
            m->hold_core1();
        } else {
            (void)m->sio_.core1_boot_done();  // out of reset: the boot ROM drains its FIFO and tells core 0 with a 0
        }
    }
    static void sio_add_cycles(void* chip, uint32_t cycles) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        m->core(m->bus.initiator).cycles += cycles;  // the initiating core pays (always core 0 until core 1 runs)
    }
    // The pin banks' host: `ctx` is a `McuInstance` (the bank).
    static bool pin_change(void* ctx, uint32_t pin, int new_state, int old_state) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.pin_changed == nullptr || w.pin_changed(w.ctx, i->index, pin, new_state, old_state);
    }
    // An input level moved: the PWM (if the pin's function is PWM) and the PIO machines waiting on this pin react.
    static bool pin_input_changed(void* ctx, uint32_t pin, bool function_is_pwm) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        Mcu* m = i->mcu;
        if (function_is_pwm && !m->pwm_.gpio_on_input(pin)) return false;
        for (uint32_t p = 0; p < kPioBlocks; ++p) {
            for (uint32_t k = 0; k < kPioMachines; ++k) {
                PioMachine& machine = m->pio_[p].machines[k];
                if (machine.enabled && machine.waiting && machine.wait_type == kPioWaitPin && machine.wait_index == pin && !machine.check_wait()) return false;
            }
        }
        return true;
    }
    // A pin's IRQ value changed (either bank): IO_BANK0's line follows "any GPIO pin's IRQ value" - the QSPI bank's changes re-evaluate the same line, as they always did.
    static bool pin_io_interrupt(void* ctx) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        return i->mcu->set_interrupt(kIrqIoBank0, i->mcu->gpio_bank_.any_irq_value());
    }
    static void ppb_reset_request(void* chip) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        if (m->host_world_.reset_requested != nullptr) {
            (void)m->host_world_.reset_requested(m->host_world_.ctx, McuHost::kResetRequestSysResetReq);
        } else {
            (void)m->cpu.reset();  // "Reset the core only" (datasheet 2.4.2.9)
        }
    }
    static bool watchdog_trigger(void* chip) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        return m->host_world_.reset_requested == nullptr || m->host_world_.reset_requested(m->host_world_.ctx, McuHost::kResetRequestWatchdog);
    }
    // The watchdog's tick moved: the TIMER and SysTick count on it.
    static bool watchdog_tick(void* chip, double tick_hz) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        m->timer_.tick_changed(tick_hz);
        m->ppb_.tick_changed(tick_hz);
        m->ppb1_.tick_changed(tick_hz);
        return true;
    }
    static bool rosc_changed(void* chip, double) noexcept { return tree_changed(chip); }
    static bool sysinfo_rom_version(void* chip, uint32_t* version) noexcept {
        *version = static_cast<Mcu*>(chip)->bus.read8(kRomVersionAddress);
        return true;
    }
    // The blocks of group 2 call these; each raises its line / request on the chip, or hands the world the part it answers for. `ctx` is the Mcu (TIMER, ADC) or a `McuInstance`.
    static bool adc_read(void* chip, uint32_t channel) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        if (m->host_world_.adc_read != nullptr) return m->host_world_.adc_read(m->host_world_.ctx, channel);
        m->adc_.default_adc_read(channel);
        return true;
    }
    static bool adc_channel_value(void* chip, uint32_t channel, int64_t* value) noexcept {
        Mcu* m = static_cast<Mcu*>(chip);
        if (m->host_world_.adc_channel_value != nullptr) return m->host_world_.adc_channel_value(m->host_world_.ctx, channel, value);
        *value = 0;
        return true;
    }
    static bool uart_byte(void* ctx, uint32_t byte) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.uart_byte == nullptr || w.uart_byte(w.ctx, i->index, byte);
    }
    static bool uart_baud(void* ctx) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.uart_baud == nullptr || w.uart_baud(w.ctx, i->index);
    }
    static bool uart_bit_ns(void* ctx, double* nanos) noexcept {  // 16 x divisor bit-clocks of clk_peri per bit; 0 while the divisor or the clock is zero
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const UartBlock& u = i->mcu->uart_[i->index];
        const double divider = u.int_divisor + u.frac_divisor / 64.0;
        const double clk_peri = i->mcu->clocks_.clk_peri();
        *nanos = (divider <= 0 || clk_peri <= 0) ? 0.0 : 16 * divider * 1e9 / clk_peri;
        return true;
    }
    static bool spi_transmit(void* ctx, uint32_t value) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        if (w.spi_transmit != nullptr) return w.spi_transmit(w.ctx, i->index, value);
        return i->mcu->spi_[i->index].complete_transmit(0);  // no device: the byte comes straight back as 0
    }
    static bool spi_bit_ns(void* ctx, double* nanos) noexcept {  // one SSPCLKOUT period: clk_peri / (CPSR x (1 + SCR)); 0 while the prescale divisor is 0
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const SpiBlock& sp = i->mcu->spi_[i->index];
        const uint32_t scr = (sp.control0 >> spi_regs::CR0_SCR_SHIFT) & spi_regs::CR0_SCR_MASK;
        const double frequency = sp.clock_divisor == 0 ? 0.0 : i->mcu->clocks_.clk_peri() / (static_cast<double>(sp.clock_divisor) * (1 + scr));
        *nanos = frequency != 0.0 ? 1e9 / frequency : 0.0;
        return true;
    }
    static bool i2c_start(void* ctx, bool repeated) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.i2c_start != nullptr ? w.i2c_start(w.ctx, i->index, repeated) : i->mcu->i2c_[i->index].complete_start();
    }
    static bool i2c_connect(void* ctx, uint32_t address, uint32_t mode) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.i2c_connect != nullptr ? w.i2c_connect(w.ctx, i->index, address, mode) : i->mcu->i2c_[i->index].complete_connect(false, 0);  // no device: every address is NACKed
    }
    static bool i2c_write(void* ctx, uint32_t value) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.i2c_write != nullptr ? w.i2c_write(w.ctx, i->index, value) : i->mcu->i2c_[i->index].complete_write(false);
    }
    static bool i2c_read(void* ctx, bool ack) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.i2c_read != nullptr ? w.i2c_read(w.ctx, i->index, ack) : i->mcu->i2c_[i->index].complete_read(0xFF);
    }
    static bool i2c_stop(void* ctx) noexcept {
        const McuInstance* i = static_cast<const McuInstance*>(ctx);
        const McuHost& w = i->mcu->host_world_;
        return w.i2c_stop != nullptr ? w.i2c_stop(w.ctx, i->index) : i->mcu->i2c_[i->index].complete_stop();
    }

    PioBlock pio_[kPioBlocks];
    ClocksBlock clocks_;
    PllBlock pll_[2];
    XoscBlock xosc_;
    RoscBlock rosc_;
    RtcBlock rtc_;
    ChipPorts ports_;  // the lines every block shares (core_host.hpp): the host's `warn` with the block's id, and the failure flag
    TbmanBlock tbman_;
    BusctrlBlock busctrl_;
    ResetsBlock resets_;
    PsmBlock psm_;
    VregBlock vreg_;
    SysInfoBlock sysinfo_;
    SyscfgBlock syscfg_;
    PinBank gpio_bank_, qspi_bank_;
    IoBlock io_bank0_, io_qspi_;
    PadsBlock pads_bank0_, pads_qspi_;
    McuInstance bank_ctx_[2];
    TimerBlock timer_;
    PwmBlock pwm_;
    McuInstance pio_ctx_[2];
    SsiBlock ssi_;
    bool cpu1_running_ = false;
    uint8_t irq_slow_ = 0;  // nonzero: an IRQ hook is installed or core 1 is running, so `set_interrupt` does more than hand the line to core 0
    volatile uint8_t clk_changed_ = 0;  // raised by the tree when clk_sys changes (`run_at_sys_clock()`)
    UsbBlock usb_;
    uint8_t* usb_dpram_ = nullptr;
    XipBlock xip_;
    uint8_t* flash_ = nullptr;
    uint32_t flash_size_ = 0;
    SioBlock sio_;
    PpbBlock ppb_;
    PpbBlock ppb1_;  // core 1's
    WatchdogBlock watchdog_;
    DmaBlock own_dma_;  // (`dma_` is the block the requests go to: this one, unless the host attached another)
    AdcBlock adc_;
    UartBlock uart_[2];
    SpiBlock spi_[2];
    I2cBlock i2c_[2];
    McuInstance instance_[2];
    McuHost host_world_;
    IrqHook irq_hook_ = nullptr;
    void* irq_hook_ctx_ = nullptr;
    DmaBlock* dma_ = nullptr;
    DreqHook dreq_hook_ = nullptr;
    void* dreq_hook_ctx_ = nullptr;
    volatile uint8_t stop_ = 0;
    volatile uint8_t real_io_ = 0;
    BatchHost host_;
    Clock own_clock_;
    Clock* clock_ = &own_clock_;
};

// A block's interrupt line, bound to the chip: the context a shell hands the block as its `irq_ctx`, and the function that goes with it. The block's `irq(ctx, level)` signature is
// the common one (UART, SPI, I2C, ADC, PWM, RTC, USB, SSI: a line the shell knows); blocks with several lines use `McuIrqLines` (`irq(ctx, line, level)`, the line offset from `base`).
struct McuIrqLine {
    Mcu* mcu = nullptr;
    uint32_t line = 0;
};
inline bool mcu_irq_line(void* ctx, bool level) noexcept {
    const McuIrqLine* l = static_cast<const McuIrqLine*>(ctx);
    return l->mcu->set_interrupt(l->line, level);
}
struct McuIrqLines {
    Mcu* mcu = nullptr;
    uint32_t base = 0;
};
inline bool mcu_irq_lines(void* ctx, uint32_t line, bool level) noexcept {
    const McuIrqLines* l = static_cast<const McuIrqLines*>(ctx);
    return l->mcu->set_interrupt(l->base + line, level);
}

// A block's DMA request, bound to the chip: the context a shell hands the block as its `dreq_ctx`, and the function that goes with it, by the shape of the block's own `dreq`.
// `McuDreqPair`: a TX and an RX request, `dreq(ctx, which, asserted)` - `if_true` is the request `which == true` selects (the UART passes `rx`). Only the UART's stand-in path still hands this to a
// block; the blocks the `Mcu` builds are wired inside it (`Mcu::set_dreq`).
struct McuDreqPair {
    Mcu* mcu = nullptr;
    uint32_t if_true = 0, if_false = 0;
};
inline bool mcu_dreq_pair(void* ctx, bool which, bool asserted) noexcept {
    const McuDreqPair* l = static_cast<const McuDreqPair*>(ctx);
    return l->mcu->set_dreq(which ? l->if_true : l->if_false, asserted);
}

// The IO_BANK0 interrupt line: the level of it is "any pin's IRQ value is set", re-evaluated on the pin bank whenever a pin says its own value may have changed.
struct McuIoIrq {
    Mcu* mcu = nullptr;
    const PinBank* bank = nullptr;
    uint32_t line = 0;
};
inline bool mcu_io_interrupt(void* ctx) noexcept {
    const McuIoIrq* l = static_cast<const McuIoIrq*>(ctx);
    return l->mcu->set_interrupt(l->line, l->bank->any_irq_value());
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_MCU_HPP
