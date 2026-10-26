// The RP2040 CLOCKS block (datasheet 2.15) and the clock tree it drives, in C++ (docs/records/0096-cpp-mcu-core.md, docs/tasks/clocks-port.md). The pure-Python reference is
// `peripherals/_clocks.py`; its comments carry the sources.
//
// What it owns: every CLK_*_CTRL / DIV register, the frequency each generator delivers from its mux and divider (clk_ref, clk_sys, clk_peri, clk_usb, clk_adc, clk_rtc), the frequency counter FC0, the
// stored registers (WAKE_EN / SLEEP_EN, the resus control, INTE / INTF) and `clk_sys` / `clk_peri`, the two clocks the rest of the chip runs on (125 MHz until firmware configures the tree).
// What it reads through a `ClocksHost`: the crystal's and the ring oscillator's output (`xosc_hz` is 0 while the crystal is not running) and the two PLLs it points to.
//
//   - `retune()` is the reference's `update_clocks()`: clk_sys and clk_peri change only when the new value is non-zero and different (a stopped or unmodelled source keeps the last value); on a change
//     the sys sinks (SysTick, the PWM counters, the host's listeners: in registration order) or the peri sinks (the UARTs) are told `(new, old)`; the ref sinks (the watchdog tick) and the rtc sinks
//     (the RTC) are told on EVERY retune, whatever changed - they compare for themselves;
//   - a write to CLK_REF / SYS CTRL and DIV, CLK_PERI_CTRL and CLK_RTC CTRL / DIV retunes; CLK_PERI_DIV, CLK_USB, CLK_ADC and the GPOUT registers are stored (the PLLs and the ring oscillator retune
//     through `retune()` themselves);
//   - `reset()` puts the registers back to power-on; `reset_tree()` is the CLOCKS-domain reset's second half (both PLLs back to power-on, clk_sys / clk_peri to the default through the sinks, then the ref and
//     rtc sinks once);
//   - FC0 counts against the chip's C++ clock: writing FC0_SRC starts a count (RUNNING), one alarm ends it (DONE | PASS / FAIL, DIED, FAST, SLOW and the result in kHz with five fraction bits);
//   - an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write; an alias write decodes against a *read* of the register.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation (the sink lists are fixed arrays). Single-threaded by contract; the block must not move after `init()`.
#ifndef RP2040PY_CORE_CLOCKS_HPP
#define RP2040PY_CORE_CLOCKS_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "pll.hpp"
#include "rosc.hpp"
#include "xosc.hpp"

namespace rp2040core {


using ClockHzFn = double (*)(void* ctx);                          // an oscillator's output in Hz (0: not running)
using ClockSinkFn = bool (*)(void* ctx, double hz, double old);   // a clock changed (or, for the ref and rtc sinks, was re-derived); false: a failure is parked
constexpr uint32_t kClockSinkCapacity = 16;

struct ClocksHost {
    ClockHzFn xosc_hz = nullptr;
    ClockHzFn rosc_hz = nullptr;
    void* ctx = nullptr;
};

namespace clocks_regs {
constexpr uint32_t GPOUT0_CTRL = 0x00, GPOUT0_DIV = 0x04, GPOUT0_SELECTED = 0x08, GPOUT1_CTRL = 0x0C, GPOUT1_DIV = 0x10, GPOUT1_SELECTED = 0x14;
constexpr uint32_t GPOUT2_CTRL = 0x18, GPOUT2_DIV = 0x1C, GPOUT2_SELECTED = 0x20, GPOUT3_CTRL = 0x24, GPOUT3_DIV = 0x28, GPOUT3_SELECTED = 0x2C;
constexpr uint32_t REF_CTRL = 0x30, REF_DIV = 0x34, REF_SELECTED = 0x38, SYS_CTRL = 0x3C, SYS_DIV = 0x40, SYS_SELECTED = 0x44;
constexpr uint32_t PERI_CTRL = 0x48, PERI_DIV = 0x4C, PERI_SELECTED = 0x50, USB_CTRL = 0x54, USB_DIV = 0x58, USB_SELECTED = 0x5C;
constexpr uint32_t ADC_CTRL = 0x60, ADC_DIV = 0x64, ADC_SELECTED = 0x68, RTC_CTRL = 0x6C, RTC_DIV = 0x70, RTC_SELECTED = 0x74;
constexpr uint32_t SYS_RESUS_CTRL = 0x78, SYS_RESUS_STATUS = 0x7C;
constexpr uint32_t FC0_REF_KHZ = 0x80, FC0_MIN_KHZ = 0x84, FC0_MAX_KHZ = 0x88, FC0_DELAY = 0x8C, FC0_INTERVAL = 0x90, FC0_SRC = 0x94, FC0_STATUS = 0x98, FC0_RESULT = 0x9C;
constexpr uint32_t WAKE_EN0 = 0xA0, WAKE_EN1 = 0xA4, SLEEP_EN0 = 0xA8, SLEEP_EN1 = 0xAC, ENABLED0 = 0xB0, ENABLED1 = 0xB4, INTR = 0xB8, INTE = 0xBC, INTF = 0xC0, INTS = 0xC4;

constexpr uint32_t GPOUT_CTRL_READ = 0b100110001110111100000, AUX_CTRL_READ = 0b100110000110011100000, REF_CTRL_READ = 0b000001100011, SYS_CTRL_READ = 0b000011100001,
                   PERI_CTRL_READ = 0b110011100000, DIV_INT_ONLY = 0x300;
constexpr uint32_t REF_SRC_MASK = 0x3, REF_SRC_ROSC = 0x0, REF_SRC_AUX = 0x1, REF_SRC_XOSC = 0x2, AUXSRC_SHIFT = 5, REF_AUXSRC_MASK = 0x3, REF_AUXSRC_PLL_USB = 0x0;
constexpr uint32_t SYS_SRC_MASK = 0x1, SYS_SRC_REF = 0x0, SYS_AUXSRC_MASK = 0x7, SYS_AUXSRC_PLL_SYS = 0x0, SYS_AUXSRC_PLL_USB = 0x1, SYS_AUXSRC_ROSC = 0x2, SYS_AUXSRC_XOSC = 0x3;
constexpr uint32_t GEN_ENABLE = 1u << 11, GEN_KILL = 1u << 10, GEN_AUXSRC_MASK = 0x7;
constexpr uint32_t PERI_AUXSRC_CLK_SYS = 0x0, PERI_AUXSRC_PLL_SYS = 0x1, PERI_AUXSRC_PLL_USB = 0x2, PERI_AUXSRC_ROSC = 0x3, PERI_AUXSRC_XOSC = 0x4;
constexpr uint32_t AUX_PLL_USB = 0x0, AUX_PLL_SYS = 0x1, AUX_ROSC = 0x2, AUX_XOSC = 0x3;  // clk_usb / clk_adc / clk_rtc share this mux (tables 230, 232, 234)
constexpr uint32_t DIV_INT_SHIFT = 8, DIV_FRAC_MASK = 0xFF;

constexpr uint32_t FC0_REF_KHZ_MASK = 0xFFFFF, FC0_MIN_KHZ_MASK = 0x1FFFFFF, FC0_MAX_KHZ_MASK = 0x1FFFFFF, FC0_MAX_KHZ_RESET = 0x1FFFFFF, FC0_DELAY_MASK = 0x7, FC0_DELAY_RESET = 0x1;
constexpr uint32_t FC0_INTERVAL_MASK = 0xF, FC0_INTERVAL_RESET = 0x8, FC0_SRC_MASK = 0xFF;
constexpr uint32_t FC0_STATUS_DIED = 1u << 28, FC0_STATUS_FAST = 1u << 24, FC0_STATUS_SLOW = 1u << 20, FC0_STATUS_FAIL = 1u << 16, FC0_STATUS_RUNNING = 1u << 8, FC0_STATUS_DONE = 1u << 4,
                   FC0_STATUS_PASS = 1u;
constexpr uint32_t FC0_RESULT_MASK = 0x3FFFFFFF, FC0_RESULT_FRAC_BITS = 5;
constexpr double FC0_INTERVAL_NANOS = 980.0;  // "The test interval is 0.98us * 2**interval"
constexpr uint32_t FC0_SRC_PLL_SYS = 0x01, FC0_SRC_PLL_USB = 0x02, FC0_SRC_ROSC = 0x03, FC0_SRC_ROSC_PH = 0x04, FC0_SRC_XOSC = 0x05, FC0_SRC_CLK_REF = 0x08, FC0_SRC_CLK_SYS = 0x09,
                   FC0_SRC_CLK_PERI = 0x0A, FC0_SRC_CLK_USB = 0x0B, FC0_SRC_CLK_ADC = 0x0C, FC0_SRC_CLK_RTC = 0x0D;

constexpr uint32_t RESUS_CTRL_MASK = 0x111FF, RESUS_CTRL_RESET = 0xFF, WAKE_EN1_MASK = 0x7FFF;
constexpr double DEFAULT_HZ = 125e6;  // what clk_sys and clk_peri are until firmware configures the tree
}  // namespace clocks_regs

// A CLK_x_DIV value as a divisor: 24.8 fixed point, and an INT of 0 divides by 2**16.
inline double clock_divisor(uint32_t div) noexcept {
    const uint32_t integer = div >> clocks_regs::DIV_INT_SHIFT;
    return integer ? static_cast<double>(integer) + static_cast<double>(div & clocks_regs::DIV_FRAC_MASK) / 256.0 : 65536.0;
}

class ClocksBlock : public RegisterBlock<ClocksBlock> {
public:
    ClocksBlock() = default;
    ClocksBlock(const ClocksBlock&) = delete;  // the window handler and the alarm hold pointers to this object
    ClocksBlock& operator=(const ClocksBlock&) = delete;

    void init(Clock* clock, const ChipPorts* ports, uint32_t block, const ClocksHost& host, PllBlock* pll_sys, PllBlock* pll_usb) noexcept {
        port_.init(ports, block);
        clock_ = clock;
        host_ = host;
        pll_sys_ = pll_sys;
        pll_usb_ = pll_usb;
        fc_alarm_.fire = &ClocksBlock::on_fc_done;
        fc_alarm_.ctx = this;
        n_sys_ = n_peri_ = n_ref_ = n_rtc_ = 0;
        xosc_ = nullptr;
        rosc_ = nullptr;
        crystal_hz_ = 12e6;
        clk_sys_ = clk_peri_ = clocks_regs::DEFAULT_HZ;
        reset();
    }
    void detach() noexcept { cancel_alarm(&fc_alarm_); }


    // An observer of every announcement of the tree, called first, before the sinks of that domain, without taking anything away from them: the port a test or a debugger uses to see what the tree told its
    // consumers and in what order. `domain` is one of the `kTap...` values; `old` is the previous value for `sys` and `peri`, 0 for `ref` and `rtc`. False from the tap: the host has parked its error.
    static constexpr uint32_t kTapSys = 0, kTapPeri = 1, kTapRef = 2, kTapRtc = 3;
    using Tap = bool (*)(void* ctx, uint32_t domain, double hz, double old);
    Tap tap = nullptr;
    void* tap_ctx = nullptr;

    // The sinks, called in the order they were added. False: the list is full.
    // Forgets every sink (the host re-registers them when a consumer is replaced).
    void clear_sinks() noexcept { n_sys_ = n_peri_ = n_ref_ = n_rtc_ = 0; }
    bool add_sys_sink(ClockSinkFn fn, void* ctx) noexcept { return add(sys_sinks_, n_sys_, fn, ctx); }
    bool add_peri_sink(ClockSinkFn fn, void* ctx) noexcept { return add(peri_sinks_, n_peri_, fn, ctx); }
    bool add_ref_sink(ClockSinkFn fn, void* ctx) noexcept { return add(ref_sinks_, n_ref_, fn, ctx); }
    bool add_rtc_sink(ClockSinkFn fn, void* ctx) noexcept { return add(rtc_sinks_, n_rtc_, fn, ctx); }

    double clk_sys() const noexcept { return clk_sys_; }
    double clk_peri() const noexcept { return clk_peri_; }
    // The peripheral clock as the peripherals see it, set by a host that wants a rate the registers do not give (a test, a board's fixed peripheral clock); the next re-derivation overwrites it.
    void force_clk_peri(double hz) noexcept { clk_peri_ = hz; }

    // Every CLK_*_CTRL/DIV back to its power-on value (a divisor of 1.0 is 0x100); the stored registers and FC0 too. clk_sys / clk_peri are `reset_tree()`'s.
    void reset() noexcept {
        using namespace clocks_regs;
        gpout_ctrl[0] = gpout_ctrl[1] = gpout_ctrl[2] = gpout_ctrl[3] = 0;
        gpout_div[0] = gpout_div[1] = gpout_div[2] = gpout_div[3] = 0x100;
        ref_ctrl = peri_ctrl = usb_ctrl = sys_ctrl = adc_ctrl = rtc_ctrl = 0;
        ref_div = peri_div = usb_div = sys_div = adc_div = rtc_div = 0x100;
        resus_ctrl_ = RESUS_CTRL_RESET;
        wake_en0_ = sleep_en0_ = 0xFFFFFFFFu;
        wake_en1_ = sleep_en1_ = WAKE_EN1_MASK;
        inte_ = intf_ = 0;
        fc_reset();
    }

    // The CLOCKS domain was reset (after `reset()`): both PLLs to power-on, clk_sys / clk_peri back to the default through the sinks, then the ref and rtc sinks once.
    bool reset_tree() noexcept {
        pll_sys_->reset();
        pll_usb_->reset();
        if (clk_sys_ != clocks_regs::DEFAULT_HZ && !set_clk_sys(clocks_regs::DEFAULT_HZ)) return false;
        if (clk_peri_ != clocks_regs::DEFAULT_HZ && !set_clk_peri(clocks_regs::DEFAULT_HZ)) return false;
        return notify_ref_and_rtc();
    }

    // ---- the oscillators ----
    // The tree reads the crystal and the ring oscillator from their C++ blocks when it is given them: the crystal gives `crystal_hz` while its block is STABLE (enabled and not dormant) and nothing
    // otherwise (datasheet 2.16), the ring oscillator the frequency its block last announced. `nullptr` takes a block back: the host's `xosc_hz` / `rosc_hz` answer again. The crystal's frequency is
    // the board's (12 MHz on a Pico), a parameter of the world, not of the chip: `set_crystal_hz()`.
    void bind_xosc(const XoscBlock* block) noexcept { xosc_ = block; }
    void bind_rosc(const RoscBlock* block) noexcept { rosc_ = block; }
    void set_crystal_hz(double hz) noexcept { crystal_hz_ = hz; }
    double crystal_hz() const noexcept { return crystal_hz_; }

    // ---- the frequencies ----
    double xosc_hz() const noexcept {
        if (xosc_ != nullptr) return xosc_->stable ? crystal_hz_ : 0.0;
        return host_.xosc_hz ? host_.xosc_hz(host_.ctx) : 0.0;
    }
    double rosc_hz() const noexcept {
        if (rosc_ != nullptr) return rosc_->hz;
        return host_.rosc_hz ? host_.rosc_hz(host_.ctx) : 0.0;
    }

    double ref_freq() const noexcept { return ref_source_freq() / clock_divisor(ref_div & clocks_regs::DIV_INT_ONLY); }
    double sys_freq() const noexcept { return sys_source_freq() / clock_divisor(sys_div); }
    double peri_freq() const noexcept {
        using namespace clocks_regs;
        if (!(peri_ctrl & GEN_ENABLE) || (peri_ctrl & GEN_KILL)) return 0.0;
        switch ((peri_ctrl >> AUXSRC_SHIFT) & GEN_AUXSRC_MASK) {
            case PERI_AUXSRC_CLK_SYS: return sys_freq();
            case PERI_AUXSRC_PLL_SYS: return pll_sys_->frequency();
            case PERI_AUXSRC_PLL_USB: return pll_usb_->frequency();
            case PERI_AUXSRC_ROSC: return rosc_hz();
            case PERI_AUXSRC_XOSC: return xosc_hz();
            default: return 0.0;
        }
    }
    double rtc_freq() const noexcept { return aux_generator_freq(rtc_ctrl, rtc_div); }
    double usb_freq() const noexcept { return aux_generator_freq(usb_ctrl, usb_div); }
    double adc_freq() const noexcept { return aux_generator_freq(adc_ctrl, adc_div); }

    // What the frequency counter measures for an FC0_SRC value; 0 for NULL, a stopped source, GPIN0/GPIN1 (not modelled) and a value that selects nothing.
    double frequency_of(uint32_t source) const noexcept {
        using namespace clocks_regs;
        switch (source) {
            case FC0_SRC_PLL_SYS: return pll_sys_->frequency();
            case FC0_SRC_PLL_USB: return pll_usb_->frequency();
            case FC0_SRC_ROSC:
            case FC0_SRC_ROSC_PH: return rosc_hz();
            case FC0_SRC_XOSC: return xosc_hz();
            case FC0_SRC_CLK_REF: return ref_freq();
            case FC0_SRC_CLK_SYS: return clk_sys_;
            case FC0_SRC_CLK_PERI: return clk_peri_;
            case FC0_SRC_CLK_USB: return usb_freq();
            case FC0_SRC_CLK_ADC: return adc_freq();
            case FC0_SRC_CLK_RTC: return rtc_freq();
            default: return 0.0;
        }
    }

    // `update_clocks()`: re-derive clk_sys and clk_peri and tell what runs from them (see the header). False: a sink failed (the failure is parked with the host).
    bool retune() noexcept {
        const double sys = sys_freq();
        if (sys != 0.0 && sys != clk_sys_ && !set_clk_sys(sys)) return false;
        const double peri = peri_freq();
        if (peri != 0.0 && peri != clk_peri_ && !set_clk_peri(peri)) return false;
        return notify_ref_and_rtc();
    }

    // ---- registers ----
    uint32_t read(uint32_t offset) noexcept {
        using namespace clocks_regs;
        switch (offset) {
            case GPOUT0_CTRL: return gpout_ctrl[0] & GPOUT_CTRL_READ;
            case GPOUT0_DIV: return gpout_div[0];
            case GPOUT1_CTRL: return gpout_ctrl[1] & GPOUT_CTRL_READ;
            case GPOUT1_DIV: return gpout_div[1];
            case GPOUT2_CTRL: return gpout_ctrl[2] & GPOUT_CTRL_READ;
            case GPOUT2_DIV: return gpout_div[2];
            case GPOUT3_CTRL: return gpout_ctrl[3] & GPOUT_CTRL_READ;
            case GPOUT3_DIV: return gpout_div[3];
            case GPOUT0_SELECTED: case GPOUT1_SELECTED: case GPOUT2_SELECTED: case GPOUT3_SELECTED: return 1;
            case REF_CTRL: return ref_ctrl & REF_CTRL_READ;
            case REF_DIV: return ref_div & DIV_INT_ONLY;
            case REF_SELECTED: return 1u << (ref_ctrl & 0x03);
            case SYS_CTRL: return sys_ctrl & SYS_CTRL_READ;
            case SYS_DIV: return sys_div;
            case SYS_SELECTED: return 1u << (sys_ctrl & 0x01);
            case PERI_CTRL: return peri_ctrl & PERI_CTRL_READ;
            case PERI_DIV: return peri_div;
            case PERI_SELECTED: return 1;
            case USB_CTRL: return usb_ctrl & AUX_CTRL_READ;
            case USB_DIV: return usb_div & DIV_INT_ONLY;
            case USB_SELECTED: return 1;
            case ADC_CTRL: return adc_ctrl & AUX_CTRL_READ;
            case ADC_DIV: return adc_div & DIV_INT_ONLY;
            case ADC_SELECTED: return 1;
            case RTC_CTRL: return rtc_ctrl & AUX_CTRL_READ;
            case RTC_DIV: return rtc_div;
            case RTC_SELECTED: return 1;
            case FC0_REF_KHZ: return fc_ref_khz_;
            case FC0_MIN_KHZ: return fc_min_khz_;
            case FC0_MAX_KHZ: return fc_max_khz_;
            case FC0_DELAY: return fc_delay_;
            case FC0_INTERVAL: return fc_interval_;
            case FC0_SRC: return fc_src_;
            case FC0_STATUS: return fc_status_;
            case FC0_RESULT: return fc_result_;
            case SYS_RESUS_CTRL: return resus_ctrl_;
            case WAKE_EN0: return wake_en0_;
            case WAKE_EN1: return wake_en1_;
            case SLEEP_EN0: return sleep_en0_;
            case SLEEP_EN1: return sleep_en1_;
            case INTE: return inte_;
            case INTF: return intf_;
            case ENABLED0: case ENABLED1: return 0;  // read only; the per-destination clock enables are not modelled, so they read their reset value
            case SYS_RESUS_STATUS: return 0;          // clock resus not implemented
            case INTR: return 0;
            case INTS: return intf_;                  // INTS = (INTR & INTE) | INTF, and nothing raises INTR
            default: break;
        }
        port_.unimplemented_read(offset);
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace clocks_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        switch (offset) {
            case GPOUT0_CTRL: gpout_ctrl[0] = v; return true;
            case GPOUT0_DIV: gpout_div[0] = v; return true;
            case GPOUT1_CTRL: gpout_ctrl[1] = v; return true;
            case GPOUT1_DIV: gpout_div[1] = v; return true;
            case GPOUT2_CTRL: gpout_ctrl[2] = v; return true;
            case GPOUT2_DIV: gpout_div[2] = v; return true;
            case GPOUT3_CTRL: gpout_ctrl[3] = v; return true;
            case GPOUT3_DIV: gpout_div[3] = v; return true;
            case REF_CTRL: ref_ctrl = v; return retune();
            case REF_DIV: ref_div = v & DIV_INT_ONLY; return retune();
            case SYS_CTRL: sys_ctrl = v; return retune();
            case SYS_DIV: sys_div = v; return retune();
            case PERI_CTRL: peri_ctrl = v; return retune();
            case PERI_DIV: peri_div = v; return true;
            case USB_CTRL: usb_ctrl = v; return true;
            case USB_DIV: usb_div = v & DIV_INT_ONLY; return true;
            case ADC_CTRL: adc_ctrl = v; return true;
            case ADC_DIV: adc_div = v & DIV_INT_ONLY; return true;
            case RTC_CTRL: rtc_ctrl = v; return retune();  // clk_rtc is the RTC's reference
            case RTC_DIV: rtc_div = v; return retune();
            case FC0_REF_KHZ: fc_ref_khz_ = v & FC0_REF_KHZ_MASK; return true;
            case FC0_MIN_KHZ: fc_min_khz_ = v & FC0_MIN_KHZ_MASK; return true;
            case FC0_MAX_KHZ: fc_max_khz_ = v & FC0_MAX_KHZ_MASK; return true;
            case FC0_DELAY: fc_delay_ = v & FC0_DELAY_MASK; return true;
            case FC0_INTERVAL: fc_interval_ = v & FC0_INTERVAL_MASK; return true;
            case FC0_SRC: fc_start(v & FC0_SRC_MASK); return true;
            case FC0_STATUS: case FC0_RESULT: return true;  // read only
            case SYS_RESUS_CTRL: resus_ctrl_ = v & RESUS_CTRL_MASK; return true;
            case WAKE_EN0: wake_en0_ = v; return true;
            case WAKE_EN1: wake_en1_ = v & WAKE_EN1_MASK; return true;
            case SLEEP_EN0: sleep_en0_ = v; return true;
            case SLEEP_EN1: sleep_en1_ = v & WAKE_EN1_MASK; return true;
            case INTE: inte_ = v & 0x1; return true;
            case INTF: intf_ = v & 0x1; return true;
            case SYS_RESUS_STATUS: case ENABLED0: case ENABLED1: case INTR: case INTS: return true;  // read only
            default: break;
        }
        port_.unimplemented_write(offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.


    // The registers a test (and the shell's properties) reads straight; the stored raw values, as written.
    uint32_t gpout_ctrl[4] = {};
    uint32_t gpout_div[4] = {0x100, 0x100, 0x100, 0x100};
    uint32_t ref_ctrl = 0, ref_div = 0x100, peri_ctrl = 0, peri_div = 0x100, usb_ctrl = 0, usb_div = 0x100;
    uint32_t sys_ctrl = 0, sys_div = 0x100, adc_ctrl = 0, adc_div = 0x100, rtc_ctrl = 0, rtc_div = 0x100;

private:
    struct Sink {
        ClockSinkFn fn = nullptr;
        void* ctx = nullptr;
    };

    static bool add(Sink* list, uint32_t& count, ClockSinkFn fn, void* ctx) noexcept {
        if (count >= kClockSinkCapacity) return false;
        list[count].fn = fn;
        list[count].ctx = ctx;
        ++count;
        return true;
    }

    static bool tell(const Sink* list, uint32_t count, double hz, double old) noexcept {
        for (uint32_t i = 0; i < count; ++i) {
            if (!list[i].fn(list[i].ctx, hz, old)) return false;
        }
        return true;
    }

    bool set_clk_sys(double hz) noexcept {
        const double old = clk_sys_;
        clk_sys_ = hz;
        if (tap != nullptr && !tap(tap_ctx, kTapSys, hz, old)) return false;
        return tell(sys_sinks_, n_sys_, hz, old);
    }
    bool set_clk_peri(double hz) noexcept {
        const double old = clk_peri_;
        clk_peri_ = hz;
        if (tap != nullptr && !tap(tap_ctx, kTapPeri, hz, old)) return false;
        return tell(peri_sinks_, n_peri_, hz, old);
    }
    bool notify_ref_and_rtc() noexcept {
        if (tap != nullptr && !tap(tap_ctx, kTapRef, ref_freq(), 0.0)) return false;
        if (!tell(ref_sinks_, n_ref_, ref_freq(), 0.0)) return false;  // the watchdog's tick (the TIMER's and SysTick's reference) derives from clk_ref
        if (tap != nullptr && !tap(tap_ctx, kTapRtc, rtc_freq(), 0.0)) return false;
        return tell(rtc_sinks_, n_rtc_, rtc_freq(), 0.0);              // the RTC counts on clk_rtc / (CLKDIV_M1 + 1)
    }

    double ref_source_freq() const noexcept {
        using namespace clocks_regs;
        const uint32_t src = ref_ctrl & REF_SRC_MASK;
        if (src == REF_SRC_ROSC) return rosc_hz();
        if (src == REF_SRC_XOSC) return xosc_hz();
        if (src == REF_SRC_AUX) return ((ref_ctrl >> AUXSRC_SHIFT) & REF_AUXSRC_MASK) == REF_AUXSRC_PLL_USB ? pll_usb_->frequency() : 0.0;
        return 0.0;
    }
    double sys_source_freq() const noexcept {
        using namespace clocks_regs;
        if ((sys_ctrl & SYS_SRC_MASK) == SYS_SRC_REF) return ref_freq();
        switch ((sys_ctrl >> AUXSRC_SHIFT) & SYS_AUXSRC_MASK) {
            case SYS_AUXSRC_PLL_SYS: return pll_sys_->frequency();
            case SYS_AUXSRC_PLL_USB: return pll_usb_->frequency();
            case SYS_AUXSRC_ROSC: return rosc_hz();
            case SYS_AUXSRC_XOSC: return xosc_hz();
            default: return 0.0;
        }
    }
    // clk_usb, clk_adc and clk_rtc: the shared aux mux over the generator's divider; 0 while ENABLE is clear (the reset state) or KILL is set, and for GPIN0 / GPIN1.
    double aux_generator_freq(uint32_t ctrl, uint32_t div) const noexcept {
        using namespace clocks_regs;
        if (!(ctrl & GEN_ENABLE) || (ctrl & GEN_KILL)) return 0.0;
        double hz;
        switch ((ctrl >> AUXSRC_SHIFT) & GEN_AUXSRC_MASK) {
            case AUX_PLL_USB: hz = pll_usb_->frequency(); break;
            case AUX_PLL_SYS: hz = pll_sys_->frequency(); break;
            case AUX_ROSC: hz = rosc_hz(); break;
            case AUX_XOSC: hz = xosc_hz(); break;
            default: return 0.0;
        }
        return hz / clock_divisor(div);
    }

    // ---- the frequency counter (2.15.4, 2.15.6.2) ----
    void fc_reset() noexcept {
        using namespace clocks_regs;
        fc_ref_khz_ = fc_min_khz_ = 0;
        fc_max_khz_ = FC0_MAX_KHZ_RESET;
        fc_delay_ = FC0_DELAY_RESET;
        fc_interval_ = FC0_INTERVAL_RESET;
        fc_src_ = fc_status_ = fc_result_ = 0;
        fc_started_hz_ = 0.0;
        cancel_alarm(&fc_alarm_);
    }
    void fc_start(uint32_t src) noexcept {
        using namespace clocks_regs;
        fc_src_ = src;
        fc_started_hz_ = frequency_of(src);
        fc_status_ = FC0_STATUS_RUNNING;
        fc_result_ = 0;
        const double delay = fc_ref_khz_ ? static_cast<double>(fc_delay_) * 1e6 / static_cast<double>(fc_ref_khz_) : 0.0;
        if (clock_) clock_->schedule(&fc_alarm_, delay + FC0_INTERVAL_NANOS * static_cast<double>(1u << fc_interval_));
    }
    static bool on_fc_done(void* ctx) noexcept {
        static_cast<ClocksBlock*>(ctx)->fc_done();
        return true;
    }
    void fc_done() noexcept {
        using namespace clocks_regs;
        const double end_hz = frequency_of(fc_src_);
        const bool died = fc_started_hz_ == 0.0 || end_hz == 0.0;
        const double khz = died ? 0.0 : end_hz / 1000.0;
        uint32_t status = FC0_STATUS_DONE;
        if (died) {
            status |= FC0_STATUS_DIED;
        } else {
            if (khz > static_cast<double>(fc_max_khz_)) status |= FC0_STATUS_FAST;
            if (khz < static_cast<double>(fc_min_khz_)) status |= FC0_STATUS_SLOW;
        }
        status |= (status & (FC0_STATUS_DIED | FC0_STATUS_FAST | FC0_STATUS_SLOW)) ? FC0_STATUS_FAIL : FC0_STATUS_PASS;
        fc_status_ = status;
        fc_result_ = static_cast<uint32_t>(static_cast<int64_t>(khz * static_cast<double>(1u << FC0_RESULT_FRAC_BITS))) & FC0_RESULT_MASK;
    }

    Clock* clock_ = nullptr;
    ClocksHost host_;
    PllBlock* pll_sys_ = nullptr;
    PllBlock* pll_usb_ = nullptr;
    const XoscBlock* xosc_ = nullptr;
    const RoscBlock* rosc_ = nullptr;
    double crystal_hz_ = 12e6;
    Alarm fc_alarm_;

    double clk_sys_ = clocks_regs::DEFAULT_HZ;
    double clk_peri_ = clocks_regs::DEFAULT_HZ;

    Sink sys_sinks_[kClockSinkCapacity], peri_sinks_[kClockSinkCapacity], ref_sinks_[kClockSinkCapacity], rtc_sinks_[kClockSinkCapacity];
    uint32_t n_sys_ = 0, n_peri_ = 0, n_ref_ = 0, n_rtc_ = 0;

    uint32_t resus_ctrl_ = clocks_regs::RESUS_CTRL_RESET;
    uint32_t wake_en0_ = 0xFFFFFFFFu, wake_en1_ = clocks_regs::WAKE_EN1_MASK, sleep_en0_ = 0xFFFFFFFFu, sleep_en1_ = clocks_regs::WAKE_EN1_MASK;
    uint32_t inte_ = 0, intf_ = 0;

    uint32_t fc_ref_khz_ = 0, fc_min_khz_ = 0, fc_max_khz_ = clocks_regs::FC0_MAX_KHZ_RESET, fc_delay_ = clocks_regs::FC0_DELAY_RESET, fc_interval_ = clocks_regs::FC0_INTERVAL_RESET;
    uint32_t fc_src_ = 0, fc_status_ = 0, fc_result_ = 0;
    double fc_started_hz_ = 0.0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_CLOCKS_HPP
