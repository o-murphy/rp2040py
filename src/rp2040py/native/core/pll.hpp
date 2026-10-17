// One RP2040 PLL (datasheet 2.18.4: PLL_SYS and PLL_USB are two instances of this block) in C++ (docs/records/0096-cpp-mcu-core.md, docs/tasks/clocks-port.md). The pure-Python reference is
// `peripherals/_pll.py`; its comments carry the sources.
//
//   - CS keeps REFDIV (5:0) and BYPASS (8) and reads LOCK (31) on top: LOCK is the VCO running (PWR.PD and PWR.VCOPD clear) off a reference that is there; there is no lock time.
//     PWR keeps PD (0), DSMPD (2), POSTDIVPD (3) and VCOPD (5) and resets to 0x2D; FBDIV_INT keeps 11:0; PRIM keeps POSTDIV1 (18:16) and POSTDIV2 (14:12) and resets to 0x77000;
//   - the output is 0 while PD is set; the reference over REFDIV alone with BYPASS; else it needs VCOPD and POSTDIVPD clear and is `ref / REFDIV * FBDIV / (POSTDIV1 * POSTDIV2)` (a divider of 0
//     counts as 1). The reference is what the host's `ref_hz` says (the crystal oscillator's output: 0 until XOSC is enabled);
//   - every register write tells the host (`changed`), which re-derives the clock tree; an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write;
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_PLL_HPP
#define RP2040PY_CORE_PLL_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {


using PllRefFn = double (*)(void* ctx);      // the crystal oscillator's output in Hz, 0 while it is not running
using PllChangedFn = bool (*)(void* ctx);    // a register changed: the clock tree is re-derived; false: a failure is parked

struct PllHost {
    PllRefFn ref_hz = nullptr;
    void* ref_ctx = nullptr;  // the context `ref_hz` is called with; null: `ctx` (see `bind_reference`)
    PllChangedFn changed = nullptr;
    void* ctx = nullptr;
};

namespace pll_regs {
constexpr uint32_t REG_CS = 0x00, REG_PWR = 0x04, REG_FBDIV_INT = 0x08, REG_PRIM = 0x0C;
constexpr uint32_t CS_LOCK = 0x80000000u, CS_BYPASS = 0x100, CS_REFDIV_MASK = 0x3F, CS_WRITE_MASK = CS_BYPASS | CS_REFDIV_MASK, CS_RESET = 0x1;
[[maybe_unused]] constexpr uint32_t PWR_DSMPD = 1u << 2;  // (stored, no effect on the output: only the checks name it)
constexpr uint32_t PWR_PD = 1u << 0, PWR_POSTDIVPD = 1u << 3, PWR_VCOPD = 1u << 5, PWR_WRITE_MASK = 0x2D, PWR_RESET = 0x2D;
constexpr uint32_t FBDIV_MASK = 0xFFF;
constexpr uint32_t PRIM_WRITE_MASK = 0x77000, PRIM_RESET = 0x77000, PRIM_POSTDIV1_SHIFT = 16, PRIM_POSTDIV2_SHIFT = 12, PRIM_POSTDIV_MASK = 0x7;
}  // namespace pll_regs

class PllBlock : public RegisterBlock<PllBlock> {
public:
    PllBlock() = default;
    PllBlock(const PllBlock&) = delete;  // the window handler holds a pointer to this object
    PllBlock& operator=(const PllBlock&) = delete;

    void init(const ChipPorts* ports, uint32_t block, const PllHost& host) noexcept {
        port_.init(ports, block);
        host_ = host;
    }

    // Whoever owns the clock tree takes over the reference (core/clock_sinks.hpp `clocks_xosc_hz`) in place of the host's `ref_hz`; `fn == nullptr` takes it back.
    void bind_reference(PllRefFn fn, void* ctx) noexcept {
        host_.ref_hz = fn;
        host_.ref_ctx = ctx;
    }


    uint32_t cs = pll_regs::CS_RESET;
    uint32_t pwr = pll_regs::PWR_RESET;
    uint32_t fbdiv_int = 0;
    uint32_t prim = pll_regs::PRIM_RESET;

    // Back to power-on (the CLOCKS domain reset does it; the tree re-derives itself afterwards, so nothing is announced here).
    void reset() noexcept {
        using namespace pll_regs;
        cs = CS_RESET;
        pwr = PWR_RESET;
        fbdiv_int = 0;
        prim = PRIM_RESET;
    }

    uint32_t refdiv() const noexcept { return cs & pll_regs::CS_REFDIV_MASK; }
    uint32_t fbdiv() const noexcept { return fbdiv_int & pll_regs::FBDIV_MASK; }
    uint32_t postdiv1() const noexcept { return (prim >> pll_regs::PRIM_POSTDIV1_SHIFT) & pll_regs::PRIM_POSTDIV_MASK; }
    uint32_t postdiv2() const noexcept { return (prim >> pll_regs::PRIM_POSTDIV2_SHIFT) & pll_regs::PRIM_POSTDIV_MASK; }

    // FREF / REFDIV in Hz (0 until the crystal runs).
    double reference() const noexcept {
        const double fref = host_.ref_hz ? host_.ref_hz(host_.ref_ctx != nullptr ? host_.ref_ctx : host_.ctx) : 0.0;
        const uint32_t div = refdiv();
        return fref / static_cast<double>(div ? div : 1);
    }

    bool locked() const noexcept { return !(pwr & (pll_regs::PWR_PD | pll_regs::PWR_VCOPD)) && reference() > 0.0; }

    // The output in Hz; 0 while it is not driven.
    double frequency() const noexcept {
        using namespace pll_regs;
        if (pwr & PWR_PD) return 0.0;
        const double ref = reference();
        if (cs & CS_BYPASS) return ref;
        if (pwr & (PWR_VCOPD | PWR_POSTDIVPD)) return 0.0;
        const uint32_t d1 = postdiv1(), d2 = postdiv2();
        const double postdiv = static_cast<double>(d1 ? d1 : 1) * static_cast<double>(d2 ? d2 : 1);
        return ref * static_cast<double>(fbdiv()) / postdiv;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace pll_regs;
        if (offset == REG_CS) return cs | (locked() ? CS_LOCK : 0u);
        if (offset == REG_PWR) return pwr;
        if (offset == REG_FBDIV_INT) return fbdiv_int;
        if (offset == REG_PRIM) return prim;
        port_.unimplemented_read(offset);
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace pll_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (offset == REG_CS) {
            cs = v & CS_WRITE_MASK;
        } else if (offset == REG_PWR) {
            pwr = v & PWR_WRITE_MASK;
        } else if (offset == REG_FBDIV_INT) {
            fbdiv_int = v & FBDIV_MASK;
        } else if (offset == REG_PRIM) {
            prim = v & PRIM_WRITE_MASK;
        } else {
            port_.unimplemented_write(offset, value);
            return true;
        }
        return host_.changed ? host_.changed(host_.ctx) : true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.


private:
    PllHost host_;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PLL_HPP
