from rp2040py.utils.bit import s32, u32, urshift

__all__ = (
    "Interpolator",
    "InterpolatorConfig",
)


class InterpolatorConfig:
    def __init__(self, value: int):
        uvalue = u32(value)
        self.shift = uvalue & 0b11111
        self.mask_lsb = (uvalue >> 5) & 0b11111
        self.mask_msb = (uvalue >> 10) & 0b11111
        self.signed = bool((uvalue >> 15) & 1)
        self.cross_input = bool((uvalue >> 16) & 1)
        self.cross_result = bool((uvalue >> 17) & 1)
        self.add_raw = bool((uvalue >> 18) & 1)
        self.force_msb = (uvalue >> 19) & 0b11
        self.blend = bool((uvalue >> 21) & 1)
        self.clamp = bool((uvalue >> 22) & 1)
        self.overf0 = bool((uvalue >> 23) & 1)
        self.overf1 = bool((uvalue >> 24) & 1)
        self.overf = bool((uvalue >> 25) & 1)

    def to_uint32(self) -> int:
        return u32(
            ((self.shift & 0b11111) << 0)
            | ((self.mask_lsb & 0b11111) << 5)
            | ((self.mask_msb & 0b11111) << 10)
            | (int(self.signed) << 15)
            | (int(self.cross_input) << 16)
            | (int(self.cross_result) << 17)
            | (int(self.add_raw) << 18)
            | ((self.force_msb & 0b11) << 19)
            | (int(self.blend) << 21)
            | (int(self.clamp) << 22)
            | (int(self.overf0) << 23)
            | (int(self.overf1) << 24)
            | (int(self.overf) << 25)
        )


class Interpolator:
    def __init__(self, index: int):
        self._index = index

        self.accum0 = 0
        self.accum1 = 0
        self.base0 = 0
        self.base1 = 0
        self.base2 = 0
        self.ctrl0 = 0
        self.ctrl1 = 0
        self.result0 = 0
        self.result1 = 0
        self.result2 = 0
        self.smresult0 = 0
        self.smresult1 = 0

        self.update()

    def update(self) -> None:
        n = self._index
        ctrl0 = InterpolatorConfig(self.ctrl0)
        ctrl1 = InterpolatorConfig(self.ctrl1)

        do_clamp = ctrl0.clamp and n == 1
        do_blend = ctrl0.blend and n == 0

        ctrl0.clamp = do_clamp
        ctrl0.blend = do_blend
        ctrl1.clamp = False
        ctrl1.blend = False
        ctrl1.overf0 = False
        ctrl1.overf1 = False
        ctrl1.overf = False

        input0 = s32(self.accum1 if ctrl0.cross_input else self.accum0)
        input1 = s32(self.accum0 if ctrl1.cross_input else self.accum1)

        msbmask0 = 0xFFFFFFFF if ctrl0.mask_msb == 31 else (1 << (ctrl0.mask_msb + 1)) - 1
        msbmask1 = 0xFFFFFFFF if ctrl1.mask_msb == 31 else (1 << (ctrl1.mask_msb + 1)) - 1
        mask0 = msbmask0 & ~((1 << ctrl0.mask_lsb) - 1) & 0xFFFFFFFF
        mask1 = msbmask1 & ~((1 << ctrl1.mask_lsb) - 1) & 0xFFFFFFFF

        uresult0 = urshift(input0, ctrl0.shift) & mask0
        uresult1 = urshift(input1, ctrl1.shift) & mask1

        overf0 = bool(urshift(input0, ctrl0.shift) & (~msbmask0 & 0xFFFFFFFF))
        overf1 = bool(urshift(input1, ctrl1.shift) & (~msbmask1 & 0xFFFFFFFF))
        overf = overf0 or overf1

        sextmask0 = (-1 << ctrl0.mask_msb) if (uresult0 & (1 << ctrl0.mask_msb)) else 0
        sextmask1 = (-1 << ctrl1.mask_msb) if (uresult1 & (1 << ctrl1.mask_msb)) else 0

        sresult0 = uresult0 | sextmask0
        sresult1 = uresult1 | sextmask1

        result0 = sresult0 if ctrl0.signed else uresult0
        result1 = sresult1 if ctrl1.signed else uresult1

        addresult0 = self.base0 + (input0 if ctrl0.add_raw else result0)
        addresult1 = self.base1 + (input1 if ctrl1.add_raw else result1)
        addresult2 = self.base2 + result0 + (0 if do_blend else result1)

        uclamp0 = (
            self.base0 if u32(result0) < u32(self.base0) else self.base1 if u32(result0) > u32(self.base1) else result0
        )
        sclamp0 = (
            self.base0 if s32(result0) < s32(self.base0) else self.base1 if s32(result0) > s32(self.base1) else result0
        )
        clamp0 = sclamp0 if ctrl0.signed else uclamp0

        alpha1 = result1 & 0xFF
        ublend1 = u32(self.base0) + s32((alpha1 * (u32(self.base1) - u32(self.base0))) // 256)
        sblend1 = s32(self.base0) + s32((alpha1 * (s32(self.base1) - s32(self.base0))) // 256)
        blend1 = sblend1 if ctrl1.signed else ublend1

        self.smresult0 = u32(result0)
        self.smresult1 = u32(result1)
        self.result0 = u32(alpha1 if do_blend else (clamp0 if do_clamp else addresult0) | (ctrl0.force_msb << 28))
        self.result1 = u32((blend1 if do_blend else addresult1) | (ctrl0.force_msb << 28))
        self.result2 = u32(addresult2)

        ctrl0.overf0 = overf0
        ctrl0.overf1 = overf1
        ctrl0.overf = overf
        self.ctrl0 = ctrl0.to_uint32()
        self.ctrl1 = ctrl1.to_uint32()

    def writeback(self) -> None:
        ctrl0 = InterpolatorConfig(self.ctrl0)
        ctrl1 = InterpolatorConfig(self.ctrl1)

        self.accum0 = u32(self.result1 if ctrl0.cross_result else self.result0)
        self.accum1 = u32(self.result0 if ctrl1.cross_result else self.result1)

        self.update()

    def set_base01(self, value: int) -> None:
        n = self._index
        ctrl0 = InterpolatorConfig(self.ctrl0)
        ctrl1 = InterpolatorConfig(self.ctrl1)

        do_blend = ctrl0.blend and n == 0

        input0 = value & 0xFFFF
        input1 = urshift(value, 16) & 0xFFFF

        sextmask0 = (-1 << 15) if (input0 & (1 << 15)) else 0
        sextmask1 = (-1 << 15) if (input1 & (1 << 15)) else 0

        signed0 = ctrl1.signed if do_blend else ctrl0.signed
        base0 = (input0 | sextmask0) if signed0 else input0
        base1 = (input1 | sextmask1) if ctrl1.signed else input1

        self.base0 = u32(base0)
        self.base1 = u32(base1)

        self.update()
