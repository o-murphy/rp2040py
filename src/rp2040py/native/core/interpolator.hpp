// One RP2040 SIO interpolator in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2): a line-for-line translation of
// interpolator.py, which stays as the pure-Python reference and the oracle.
//
// The Python class computes with unbounded integers, so what must be preserved is not "the same C types" but the same
// values modulo the places Python wraps them: the raw registers (ACCUMn, BASEn, CTRLn as written) are kept as the
// 64-bit integer the caller passed, every intermediate is an int64_t (a product of an 8-bit alpha and a 33-bit
// difference fits easily), and every result goes through the same u32()/s32() the Python does. Where Python's `//`
// floors (the blend divides a possibly negative product by 256) this floors too - C++'s `/` truncates.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation.
#ifndef RP2040PY_CORE_INTERPOLATOR_HPP
#define RP2040PY_CORE_INTERPOLATOR_HPP

#include <cstdint>

namespace rp2040core {

namespace interp_detail {
inline int64_t u32(int64_t n) noexcept { return n & 0xFFFFFFFFLL; }
inline int64_t s32(int64_t n) noexcept {
    n &= 0xFFFFFFFFLL;
    return (n & 0x80000000LL) ? n - 0x100000000LL : n;
}
inline int64_t urshift(int64_t n, uint32_t shift) noexcept { return u32(n) >> (shift & 31u); }
// Python's `a // b` for b > 0: rounds toward negative infinity.
inline int64_t floor_div(int64_t a, int64_t b) noexcept { return a >= 0 ? a / b : -((-a + b - 1) / b); }
}  // namespace interp_detail

// The fields of a CTRL_LANEn register (interpolator.py: InterpolatorConfig).
struct InterpConfig {
    uint32_t shift, mask_lsb, mask_msb, force_msb;
    bool signed_, cross_input, cross_result, add_raw, blend, clamp, overf0, overf1, overf;

    static InterpConfig parse(int64_t value) noexcept {
        const uint32_t u = static_cast<uint32_t>(value & 0xFFFFFFFFLL);
        InterpConfig c;
        c.shift = u & 0x1F;
        c.mask_lsb = (u >> 5) & 0x1F;
        c.mask_msb = (u >> 10) & 0x1F;
        c.signed_ = (u >> 15) & 1;
        c.cross_input = (u >> 16) & 1;
        c.cross_result = (u >> 17) & 1;
        c.add_raw = (u >> 18) & 1;
        c.force_msb = (u >> 19) & 0x3;
        c.blend = (u >> 21) & 1;
        c.clamp = (u >> 22) & 1;
        c.overf0 = (u >> 23) & 1;
        c.overf1 = (u >> 24) & 1;
        c.overf = (u >> 25) & 1;
        return c;
    }

    uint32_t to_uint32() const noexcept {
        return ((shift & 0x1F) << 0) | ((mask_lsb & 0x1F) << 5) | ((mask_msb & 0x1F) << 10) |
               (static_cast<uint32_t>(signed_) << 15) | (static_cast<uint32_t>(cross_input) << 16) |
               (static_cast<uint32_t>(cross_result) << 17) | (static_cast<uint32_t>(add_raw) << 18) |
               ((force_msb & 0x3) << 19) | (static_cast<uint32_t>(blend) << 21) | (static_cast<uint32_t>(clamp) << 22) |
               (static_cast<uint32_t>(overf0) << 23) | (static_cast<uint32_t>(overf1) << 24) |
               (static_cast<uint32_t>(overf) << 25);
    }
};

class Interpolator {
public:
    // Registers as the caller last wrote them (raw 64-bit), results as the 32-bit values the block computes.
    int64_t accum0 = 0, accum1 = 0, base0 = 0, base1 = 0, base2 = 0;
    int64_t ctrl0 = 0, ctrl1 = 0;  // raw until update() rewrites them in canonical form
    uint32_t result0 = 0, result1 = 0, result2 = 0, smresult0 = 0, smresult1 = 0;
    // The lane results on the internal 32-bit datapath, i.e. without FORCE_MSB ("No effect on the internal 32-bit datapath"): what a POP writes back.
    uint32_t lane0 = 0, lane1 = 0;

    explicit Interpolator(int index = 0) noexcept : index_(index) { update(); }

    void set_index(int index) noexcept {
        index_ = index;
        update();
    }

    void reset() noexcept {
        accum0 = accum1 = base0 = base1 = base2 = 0;
        ctrl0 = ctrl1 = 0;
        result0 = result1 = result2 = smresult0 = smresult1 = 0;
        lane0 = lane1 = 0;
        update();
    }

    void update() noexcept {
        using namespace interp_detail;
        const int n = index_;
        InterpConfig c0 = InterpConfig::parse(ctrl0);
        InterpConfig c1 = InterpConfig::parse(ctrl1);

        const bool do_clamp = c0.clamp && n == 1;
        const bool do_blend = c0.blend && n == 0;

        c0.clamp = do_clamp;
        c0.blend = do_blend;
        c1.clamp = false;
        c1.blend = false;
        c1.overf0 = false;
        c1.overf1 = false;
        c1.overf = false;

        const int64_t input0 = s32(c0.cross_input ? accum1 : accum0);
        const int64_t input1 = s32(c1.cross_input ? accum0 : accum1);

        const int64_t msbmask0 = c0.mask_msb == 31 ? 0xFFFFFFFFLL : (int64_t{1} << (c0.mask_msb + 1)) - 1;
        const int64_t msbmask1 = c1.mask_msb == 31 ? 0xFFFFFFFFLL : (int64_t{1} << (c1.mask_msb + 1)) - 1;
        const int64_t mask0 = msbmask0 & ~((int64_t{1} << c0.mask_lsb) - 1) & 0xFFFFFFFFLL;
        const int64_t mask1 = msbmask1 & ~((int64_t{1} << c1.mask_lsb) - 1) & 0xFFFFFFFFLL;

        const int64_t uresult0 = urshift(input0, c0.shift) & mask0;
        const int64_t uresult1 = urshift(input1, c1.shift) & mask1;

        const bool overf0 = (urshift(input0, c0.shift) & (~msbmask0 & 0xFFFFFFFFLL)) != 0;
        const bool overf1 = (urshift(input1, c1.shift) & (~msbmask1 & 0xFFFFFFFFLL)) != 0;
        const bool overf = overf0 || overf1;

        // Python: (-1 << msb) - a negative number whose low `msb` bits are clear.
        const int64_t sextmask0 = (uresult0 & (int64_t{1} << c0.mask_msb)) ? -(int64_t{1} << c0.mask_msb) : 0;
        const int64_t sextmask1 = (uresult1 & (int64_t{1} << c1.mask_msb)) ? -(int64_t{1} << c1.mask_msb) : 0;

        const int64_t sresult0 = uresult0 | sextmask0;
        const int64_t sresult1 = uresult1 | sextmask1;

        const int64_t r0 = c0.signed_ ? sresult0 : uresult0;
        const int64_t r1 = c1.signed_ ? sresult1 : uresult1;

        const int64_t addresult0 = base0 + (c0.add_raw ? input0 : r0);
        const int64_t addresult1 = base1 + (c1.add_raw ? input1 : r1);
        const int64_t addresult2 = base2 + r0 + (do_blend ? 0 : r1);

        const int64_t uclamp0 = u32(r0) < u32(base0) ? base0 : (u32(r0) > u32(base1) ? base1 : r0);
        const int64_t sclamp0 = s32(r0) < s32(base0) ? base0 : (s32(r0) > s32(base1) ? base1 : r0);
        const int64_t clamp0 = c0.signed_ ? sclamp0 : uclamp0;

        const int64_t alpha1 = r1 & 0xFF;
        const int64_t ublend1 = u32(base0) + s32(floor_div(alpha1 * (u32(base1) - u32(base0)), 256));
        const int64_t sblend1 = s32(base0) + s32(floor_div(alpha1 * (s32(base1) - s32(base0)), 256));
        const int64_t blend1 = c1.signed_ ? sblend1 : ublend1;

        // FORCE_MSB is a field of each lane's own CTRL register: "ORed into bits 29:28 of the lane result presented to the processor on the bus"
        const int64_t force0 = static_cast<int64_t>(c0.force_msb) << 28;
        const int64_t force1 = static_cast<int64_t>(c1.force_msb) << 28;
        smresult0 = static_cast<uint32_t>(u32(r0));
        smresult1 = static_cast<uint32_t>(u32(r1));
        // `alpha1 if do_blend else (clamp0 if do_clamp else addresult0) | (force_msb << 28)`: the OR binds to the
        // else-branch only, so a blend result carries no forced MSBs.
        lane0 = static_cast<uint32_t>(u32(do_blend ? alpha1 : (do_clamp ? clamp0 : addresult0)));
        lane1 = static_cast<uint32_t>(u32(do_blend ? blend1 : addresult1));
        result0 = static_cast<uint32_t>(u32(do_blend ? alpha1 : ((do_clamp ? clamp0 : addresult0) | force0)));
        result1 = static_cast<uint32_t>(u32((do_blend ? blend1 : addresult1) | force1));
        result2 = static_cast<uint32_t>(u32(addresult2));

        c0.overf0 = overf0;
        c0.overf1 = overf1;
        c0.overf = overf;
        ctrl0 = c0.to_uint32();
        ctrl1 = c1.to_uint32();
    }

    // POP: the accumulators take the lane results (crossed if configured), then everything is recomputed.
    void writeback() noexcept {
        const InterpConfig c0 = InterpConfig::parse(ctrl0);
        const InterpConfig c1 = InterpConfig::parse(ctrl1);
        accum0 = interp_detail::u32(c0.cross_result ? lane1 : lane0);
        accum1 = interp_detail::u32(c1.cross_result ? lane0 : lane1);
        update();
    }

    // Write to BASE_1AND0: the low 16 bits go to BASE0, the high 16 to BASE1, sign-extended per the lane's mode.
    void set_base01(int64_t value) noexcept {
        using namespace interp_detail;
        const InterpConfig c0 = InterpConfig::parse(ctrl0);
        const InterpConfig c1 = InterpConfig::parse(ctrl1);
        const bool do_blend = c0.blend && index_ == 0;

        const int64_t input0 = value & 0xFFFF;
        const int64_t input1 = urshift(value, 16) & 0xFFFF;
        const int64_t sext0 = (input0 & (int64_t{1} << 15)) ? -(int64_t{1} << 15) : 0;
        const int64_t sext1 = (input1 & (int64_t{1} << 15)) ? -(int64_t{1} << 15) : 0;

        const bool signed0 = do_blend ? c1.signed_ : c0.signed_;
        base0 = u32(signed0 ? (input0 | sext0) : input0);
        base1 = u32(c1.signed_ ? (input1 | sext1) : input1);
        update();
    }

private:
    int index_;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_INTERPOLATOR_HPP
