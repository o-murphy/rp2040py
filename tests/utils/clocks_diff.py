"""A lockstep differential oracle for CLOCKS and the PLLs (docs/tasks/clocks-port.md): the same generated stimulus drives two chips and everything observable is compared after every step.

One chip's CLOCKS and PLL blocks are the *pure-Python* reference (``peripherals/_clocks.py`` and ``_pll.py``, built explicitly because the facade would hand out the native ones); the other's are whatever
the facade gives (the C++ tree in a native build). The operations are reads and writes of **every CLOCKS and PLL register through the bus and its XOR/SET/CLR aliases** (and of offsets that are no register, so the warnings are compared), the
SDK's own sequences (``pll_init()``, ``clocks_init()``-style ``clock_configure()`` of clk_ref / clk_sys / clk_peri / clk_rtc), the **oscillators** (the crystal enabled and disabled, the ring
oscillator's divider), the frequency counter (started, and then **simulated time** passes), a **chip reset**, and a **host listener** of ``clk_sys`` that can be made to raise (the contract of
``core_host.hpp``: a failing sink stops the retune where the reference's exception would have). Every consumer the tree tells of a change is a recording spy - SysTick (``ppb``), the watchdog tick,
the RTC and every UART - so the log holds the **ORDER** of the calls, not only the end state. After each step the registers (read through the bus), ``clk_sys`` / ``clk_peri``, the frequency each generator
and PLL delivers, the PWM counters' rate, the clock, and the ordered, timestamped log of the warnings and sink calls are compared; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _clocks as C
from rp2040py.peripherals import _pll as P
from utils.twin import new_chip

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
CLOCKS_BASE = 0x40008000
PLL_BASES = (0x40028000, 0x4002C000)
XOSC_CTRL = 0x40024000
ROSC_DIV = 0x40060010
XOSC_ENABLE, XOSC_DISABLE = (0xFAB << 12) | 0xAA0, (0xD1E << 12) | 0xAA0

CLOCKS_OFFSETS = tuple(range(0x00, 0xC8, 4))  # every register of the datasheet
CLOCKS_WRITABLE = tuple(
    o
    for o in CLOCKS_OFFSETS
    if o not in (0x08, 0x14, 0x20, 0x2C, 0x38, 0x44, 0x50, 0x5C, 0x68, 0x74, 0x7C, 0x98, 0x9C, 0xB0, 0xB4, 0xB8, 0xC4)
)
PLL_OFFSETS = (0x00, 0x04, 0x08, 0x0C)
CTRL_OFFSETS = (
    0x00,
    0x0C,
    0x18,
    0x24,
    C.CLK_REF_CTRL,
    C.CLK_SYS_CTRL,
    C.CLK_PERI_CTRL,
    C.CLK_USB_CTRL,
    C.CLK_ADC_CTRL,
    C.CLK_RTC_CTRL,
)
DIV_OFFSETS = (
    0x04,
    0x10,
    0x1C,
    0x28,
    C.CLK_REF_DIV,
    C.CLK_SYS_DIV,
    C.CLK_PERI_DIV,
    C.CLK_USB_DIV,
    C.CLK_ADC_DIV,
    C.CLK_RTC_DIV,
)
FC_OFFSETS = (C.FC0_REF_KHZ, C.FC0_MIN_KHZ, C.FC0_MAX_KHZ, C.FC0_DELAY, C.FC0_INTERVAL, C.FC0_SRC)
CLK_RTC_CTRL, CLK_RTC_DIV = CLOCKS_BASE + C.CLK_RTC_CTRL, CLOCKS_BASE + C.CLK_RTC_DIV


class Boom(Exception):
    """What a failing host listener raises."""


class _Spy:
    """Forwards everything to the real consumer and records the call the clock tree makes of it."""

    def __init__(self, target: Any, rig: "Rig", name: str) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_rig", rig)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._target, attribute)

    def __setattr__(self, attribute: str, value: Any) -> None:
        setattr(self._target, attribute, value)

    def _record(self, *args: Any) -> None:
        self._rig.log.append(("sink", float(self._rig.chip.clock.nanos), self._name, *args))

    def clk_ref_changed(self, hz: float) -> Any:
        self._record(float(hz))
        return self._target.clk_ref_changed(hz)

    def clk_rtc_changed(self, hz: float) -> Any:
        self._record(float(hz))
        return self._target.clk_rtc_changed(hz)

    def clk_sys_changed(self, hz: float) -> Any:
        self._record(float(hz))
        return self._target.clk_sys_changed(hz)

    def clk_peri_changed(self) -> Any:
        self._record()
        return self._target.clk_peri_changed()


class Rig:
    """One chip plus the log of every warning and every call the clock tree makes."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = new_chip(kind)
        self.log: list[tuple] = []
        self.raising = False
        if kind == "mutant":
            assert factory is not None
            self._replace_the_blocks(*factory())
        self._tap()

    def _replace_the_blocks(self, clocks_factory: Callable[..., Any], pll_factory: Callable[..., Any]) -> None:
        chip = self.chip
        chip.pll_sys = pll_factory(chip, "PLL_SYS_BASE")
        chip.pll_usb = pll_factory(chip, "PLL_USB_BASE")
        chip.clocks = clocks_factory(chip, "CLOCKS_BASE")
        chip.peripherals[PLL_BASES[0] >> 12] = chip.pll_sys
        chip.peripherals[PLL_BASES[1] >> 12] = chip.pll_usb
        chip.peripherals[CLOCKS_BASE >> 12] = chip.clocks

    def _tap(self) -> None:
        log, chip = self.log, self.chip
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(
                    ("log", float(chip.clock.nanos), _m, str(name), str(message))
                ),
            )
        chip.watchdog = _Spy(chip.watchdog, self, "watchdog")
        chip.rtc = _Spy(chip.rtc, self, "rtc")
        chip.ppb = _Spy(chip.ppb, self, "ppb")
        chip.uart = [_Spy(uart, self, f"uart{index}") for index, uart in enumerate(chip.uart)]

        def listener(clk_sys: float, old: float) -> None:
            log.append(("listener", float(chip.clock.nanos), float(clk_sys), float(old), self.raising))
            if self.raising:
                raise Boom("the listener failed")

        chip.add_clock_listener(listener)

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(op[1] + op[2] + op[3], op[4] & 0xFFFFFFFF)  # base, alias, offset, value
        elif kind == "read":
            return int(chip.read_uint32(op[1] + op[2] + op[3]))
        elif kind == "seq":
            for step in op[1]:
                chip.write_uint32(step[0], step[1])
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "xosc":
            chip.write_uint32(XOSC_CTRL, XOSC_ENABLE if op[1] else XOSC_DISABLE)
        elif kind == "rosc_div":
            chip.write_uint32(ROSC_DIV, op[1])
        elif kind == "reset":
            chip.reset(preserve_flash=True)
        elif kind == "raising":
            self.raising = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    def snapshot(self) -> dict[str, Any]:
        chip = self.chip
        clocks = chip.clocks
        reads = []
        for base, offsets in ((CLOCKS_BASE, CLOCKS_OFFSETS), (PLL_BASES[0], PLL_OFFSETS), (PLL_BASES[1], PLL_OFFSETS)):
            for offset in offsets:
                reads.append(int(chip.read_uint32(base + offset)))
        return {
            "regs": tuple(reads),
            "clk": (float(chip.clk_sys), float(chip.clk_peri)),
            "freq": tuple(
                float(value)
                for value in (
                    clocks.ref_freq,
                    clocks.sys_freq,
                    clocks.peri_freq,
                    clocks.rtc_freq,
                    clocks.usb_freq,
                    clocks.adc_freq,
                    chip.pll_sys.frequency,
                    chip.pll_usb.frequency,
                )
            ),
            "pwm": tuple(float(channel.timer.frequency) for channel in chip.pwm.channels),
            # what the C++ consumers were told (the default tree calls them directly, so no spy sees it): SysTick's clk_sys, the watchdog's tick, the RTC's second
            "consumers": (
                float(chip.ppb.clk_sys),
                float(chip.watchdog.tick_hz),
                bool(chip.rtc.running),
                float(chip.rtc.second_nanos()) if chip.rtc.running else 0.0,
            ),
            "raw": (int(clocks.raw_write_value), int(chip.pll_sys.raw_write_value), int(chip.pll_usb.raw_write_value)),
            "time": float(chip.clock.nanos),
        }


_C_CONSUMERS = ("ppb", "watchdog", "rtc")


class Divergence:
    def __init__(self, step: int, op: tuple, detail: str) -> None:
        self.step, self.op, self.detail = step, op, detail

    def __repr__(self) -> str:
        return f"step {self.step} {self.op!r}: {self.detail}"


def _step(rig: Rig, op: tuple) -> tuple[Any, "str | None"]:
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, type(error).__name__


# --- the stimulus ---------------------------------------------------------------------------------------------------------


def _ctrl_value(rng: random.Random) -> int:
    value = rng.randrange(4) | (rng.randrange(8) << 5)
    if rng.random() < 0.6:
        value |= 1 << 11  # ENABLE
    if rng.random() < 0.1:
        value |= 1 << 10  # KILL
    if rng.random() < 0.1:
        value |= rng.randrange(1 << 32)  # junk, so the reserved bits are exercised
    return value


def _div_value(rng: random.Random) -> int:
    return rng.choice(
        (
            0x100,
            0x100,
            0x200,
            0x180,
            0x300,
            0,
            0x1_0000,
            (rng.randrange(1, 40) << 8) | rng.randrange(256),
            rng.randrange(1 << 32),
        )
    )


def sdk_pll_sequence(base: int, refdiv: int, fbdiv: int, post1: int, post2: int) -> list[tuple[int, int]]:
    """pico-sdk `pll_init()`: REFDIV and FBDIV, power up the VCO, the post dividers, power up the post dividers."""
    return [
        (base + P.PLL_CS, refdiv),
        (base + P.PLL_FBDIV_INT, fbdiv),
        (base + P.PLL_PWR, P.PLL_PWR_POSTDIVPD),
        (base + P.PLL_PRIM, (post1 << 16) | (post2 << 12)),
        (base + P.PLL_PWR, 0),
    ]


def sdk_clocks_sequence(rng: random.Random) -> list[tuple[int, int]]:
    """`clocks_init()` after the PLLs: clk_ref on the crystal, clk_sys on an aux source, clk_peri, clk_rtc."""
    return [
        (
            CLOCKS_BASE + C.CLK_REF_CTRL,
            rng.choice((C.CLK_REF_CTRL_SRC_XOSC, C.CLK_REF_CTRL_SRC_ROSC, C.CLK_REF_CTRL_SRC_AUX)),
        ),
        (CLOCKS_BASE + C.CLK_SYS_CTRL, rng.choice((0, 1)) | (rng.randrange(4) << 5)),
        (CLOCKS_BASE + C.CLK_SYS_DIV, _div_value(rng)),
        (CLOCKS_BASE + C.CLK_PERI_CTRL, C.CLK_PERI_CTRL_ENABLE | (rng.randrange(5) << 5)),
        (CLOCKS_BASE + C.CLK_RTC_DIV, rng.choice((256 << 8, 1024 << 8, 0x100))),
        (CLOCKS_BASE + C.CLK_RTC_CTRL, C.CLK_RTC_CTRL_ENABLE | (rng.randrange(4) << 5)),
    ]


def generate(seed: int, steps: int) -> list[tuple]:
    rng = random.Random(seed)
    ops: list[tuple] = [
        ("xosc", True),
        ("seq", sdk_pll_sequence(PLL_BASES[0], 1, 125, 6, 2)),
        ("seq", sdk_clocks_sequence(rng)),
    ]
    for _ in range(steps):
        roll = rng.random()
        alias = rng.choice(ALIAS_BASES) if rng.random() < 0.2 else 0
        if roll < 0.18:
            ops.append(("write", CLOCKS_BASE, alias, rng.choice(CTRL_OFFSETS), _ctrl_value(rng)))
        elif roll < 0.30:
            ops.append(("write", CLOCKS_BASE, alias, rng.choice(DIV_OFFSETS), _div_value(rng)))
        elif roll < 0.38:
            ops.append(("write", CLOCKS_BASE, alias, rng.choice(CLOCKS_WRITABLE), rng.randrange(1 << 32)))
        elif roll < 0.47:
            ops.append(
                (
                    "write",
                    CLOCKS_BASE,
                    alias,
                    rng.choice(FC_OFFSETS),
                    rng.choice((0, 1, 8, 12000, 48000, 6500, 0x7F, rng.randrange(0x20))),
                )
            )
        elif roll < 0.55:
            offset = rng.choice(PLL_OFFSETS)
            value = {
                0x00: (rng.randrange(66) | (rng.random() < 0.3) << 8),
                0x04: rng.randrange(64),
                0x08: rng.choice((125, 100, 16, 320, rng.randrange(4096))),
                0x0C: rng.randrange(1 << 20),
            }[offset]
            ops.append(("write", rng.choice(PLL_BASES), alias, offset, value))
        elif roll < 0.60:
            ops.append(
                (
                    "seq",
                    sdk_pll_sequence(
                        rng.choice(PLL_BASES),
                        rng.choice((1, 1, 2, 0, 63)),
                        rng.choice((125, 100, 16, 320, 40)),
                        rng.randrange(1, 8),
                        rng.randrange(1, 8),
                    ),
                )
            )
        elif roll < 0.62:
            ops.append(("seq", sdk_clocks_sequence(rng)))
        elif roll < 0.64:
            # a whole count: the SDK's frequency_count_khz() setup, then time for it to finish
            ops.append(
                (
                    "seq",
                    [
                        (CLOCKS_BASE + C.FC0_REF_KHZ, 12000),
                        (CLOCKS_BASE + C.FC0_MIN_KHZ, rng.choice((0, 5000, 6500, 12000, 48000, 125000))),
                        (CLOCKS_BASE + C.FC0_MAX_KHZ, rng.choice((0x1FFFFFF, 6500, 10000, 12000, 48000, 125000))),
                        (CLOCKS_BASE + C.FC0_INTERVAL, rng.randrange(0, 6)),
                        (CLOCKS_BASE + C.FC0_SRC, rng.randrange(0, 0x0F)),
                    ],
                )
            )
            ops.append(("tick", 400_000))
        elif roll < 0.67:
            ops.append(("xosc", rng.random() < 0.6))
        elif roll < 0.70:
            ops.append(("rosc_div", 0xAA0 + rng.randrange(32) if rng.random() < 0.9 else rng.randrange(1 << 16)))
        elif roll < 0.73:
            ops.append(("reset",))
        elif roll < 0.76:
            ops.append(("raising", rng.random() < 0.5))
        elif roll < 0.84:
            ops.append(("tick", rng.choice((1, 100, 1000, 250_000, 1_000_000, 5_000_000))))
        elif roll < 0.92:
            base = rng.choice((CLOCKS_BASE, *PLL_BASES))
            offsets = CLOCKS_OFFSETS + (0xC8, 0xCC, 0x1004) if base == CLOCKS_BASE else PLL_OFFSETS + (0x10, 0x14)
            ops.append(("read", base, alias, rng.choice(offsets)))
        else:
            base = rng.choice((CLOCKS_BASE, *PLL_BASES))
            ops.append(("write", base, alias, rng.choice((0xC8, 0xCC, 0x10, 0x14)), rng.randrange(1 << 32)))
    return ops


def run_pair(
    ops: list[tuple],
    *,
    reference: "Callable[[], Rig] | None" = None,
    candidate: "Callable[[], Rig] | None" = None,
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a = reference() if reference else Rig("pure")
    b = candidate() if candidate else Rig("default")
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if a.kind == "default" or b.kind == "default":
            # The default tree tells SysTick, the watchdog and the RTC from C++ without a Python call to spy on: their calls are compared by what they did ("consumers" in the snapshot), not as a log
            new_a = [entry for entry in new_a if not (entry[0] == "sink" and entry[2] in _C_CONSUMERS)]
            new_b = [entry for entry in new_b if not (entry[0] == "sink" and entry[2] in _C_CONSUMERS)]
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:6]} vs {new_b[:6]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, f"{snapshot_a} vs {snapshot_b}")
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    for op in ops:
        before = len(rig.log)
        clk = (rig.chip.clk_sys, rig.chip.clk_peri)
        count(op[0])
        if op[0] in ("read", "write") and op[2]:
            count("alias")
        _, error = _step(rig, op)
        if error:
            count("exception")
        for entry in rig.log[before:]:
            count(entry[0] if entry[0] != "sink" else f"sink.{entry[2]}")
        if (rig.chip.clk_sys, rig.chip.clk_peri) != clk:
            count("clock changed")
        if rig.chip.pll_sys.frequency and rig.chip.pll_usb.frequency:
            count("both plls running")
        if rig.chip.clocks.rtc_freq:
            count("rtc clock running")
        if rig.chip.clocks.read_uint32(C.FC0_STATUS) & C.FC0_STATUS_DONE:
            count("fc done")
        if rig.chip.clocks.read_uint32(C.FC0_STATUS) & C.FC0_STATUS_DIED:
            count("fc died")
    return counts


# --- logic mutants of the reference: what the oracle must see -----------------------------------------------------------------

MUTANTS = (
    # the generators
    "sys_div_zero_is_one",
    "sys_div_ignores_fraction",
    "ref_div_ignores_int",
    "ref_xosc_is_rosc",
    "ref_aux_is_pll_sys",
    "sys_aux_xosc_is_rosc",
    "peri_ignores_kill",
    "peri_ignores_enable",
    "peri_xosc_is_rosc",
    "rtc_ignores_enable",
    "rtc_ignores_kill",
    "rtc_div_ignored",
    "usb_aux_is_sys_pll",
    "adc_div_ignored",
    # the retune
    "update_peri_before_sys",
    "update_zero_updates_clk_sys",
    "update_zero_updates_clk_peri",
    "update_notifies_unchanged_sys",
    "update_ref_only_on_change",
    "update_skips_ref",
    "update_skips_rtc",
    "update_skips_peri",
    "update_rtc_before_ref",
    # the registers
    "ref_ctrl_read_unmasked",
    "sys_selected_constant",
    "peri_div_write_retunes",
    "usb_ctrl_write_retunes",
    "enabled_reads_ones",
    # the reset
    "reset_tree_keeps_plls",
    "reset_tree_keeps_clk_peri",
    "reset_tree_skips_rtc",
    "reset_tree_skips_ref",
    "reset_keeps_sys_ctrl",
    "reset_keeps_rtc_div",
    "reset_keeps_wake_en",
    # the frequency counter
    "fc_ignores_interval",
    "fc_never_died",
    "fc_max_exclusive",
    "fc_min_exclusive",
    "fc_result_not_scaled",
    "fc_ignores_delay",
    # the PLLs
    "pll_lock_always",
    "pll_lock_ignores_vcopd",
    "pll_lock_ignores_reference",
    "pll_ignores_pd",
    "pll_ignores_vcopd",
    "pll_ignores_postdivpd",
    "pll_bypass_divides_by_postdiv",
    "pll_bypass_not_divided_by_refdiv",
    "pll_bypass_needs_vco",
    "pll_postdiv_zero_is_zero",
    "pll_refdiv_zero_is_zero",
    "pll_write_does_not_retune",
    "pll_pwr_mask_wrong",
    "pll_prim_mask_wrong",
    "pll_reset_keeps_pwr",
    "pll_reset_keeps_fbdiv",
)


class MutantClocks(C.RPClocks):
    """The reference's CLOCKS block with one change of logic."""

    def __init__(self, rp2040: Any, name: str, mutant: str) -> None:
        self._mutant = mutant
        self._last_ref = None
        super().__init__(rp2040, name)

    @property
    def sys_freq(self) -> float:
        mutant = self._mutant
        if mutant == "sys_div_zero_is_one" and self.sys_div >> 8 == 0:
            return self._sys_source_freq
        if mutant == "sys_div_ignores_fraction":
            return self._sys_source_freq / C.clock_divisor(self.sys_div & ~C.CLK_DIV_FRAC_MASK)
        return super().sys_freq

    @property
    def ref_freq(self) -> float:
        if self._mutant == "ref_div_ignores_int":
            return self._ref_source_freq
        return super().ref_freq

    @property
    def _ref_source_freq(self) -> float:
        mutant, rp2040 = self._mutant, self.rp2040
        src = self.ref_ctrl & C.CLK_REF_CTRL_SRC_MASK
        if mutant == "ref_xosc_is_rosc" and src == C.CLK_REF_CTRL_SRC_XOSC:
            return rp2040.rosc_freq
        if mutant == "ref_aux_is_pll_sys" and src == C.CLK_REF_CTRL_SRC_AUX:
            return rp2040.pll_sys.frequency
        return C.RPClocks._ref_source_freq.fget(self)  # type: ignore[attr-defined]

    @property
    def _sys_source_freq(self) -> float:
        rp2040 = self.rp2040
        if (
            self._mutant == "sys_aux_xosc_is_rosc"
            and (self.sys_ctrl & C.CLK_SYS_CTRL_SRC_MASK) != C.CLK_SYS_CTRL_SRC_REF
            and (self.sys_ctrl >> C.CLK_SYS_CTRL_AUXSRC_SHIFT) & C.CLK_SYS_CTRL_AUXSRC_MASK
            == C.CLK_SYS_CTRL_AUXSRC_XOSC
        ):
            return rp2040.rosc_freq
        return C.RPClocks._sys_source_freq.fget(self)  # type: ignore[attr-defined]

    @property
    def peri_freq(self) -> float:
        mutant, rp2040, ctrl = self._mutant, self.rp2040, self.peri_ctrl
        if mutant == "peri_ignores_kill":
            self.peri_ctrl = ctrl & ~C.CLK_PERI_CTRL_KILL
        elif mutant == "peri_ignores_enable":
            self.peri_ctrl = ctrl | C.CLK_PERI_CTRL_ENABLE
        elif (
            mutant == "peri_xosc_is_rosc"
            and (ctrl >> C.CLK_PERI_CTRL_AUXSRC_SHIFT) & C.CLK_PERI_CTRL_AUXSRC_MASK == C.CLK_PERI_CTRL_AUXSRC_XOSC
            and ctrl & C.CLK_PERI_CTRL_ENABLE
            and not ctrl & C.CLK_PERI_CTRL_KILL
        ):
            return rp2040.rosc_freq
        try:
            return super().peri_freq
        finally:
            self.peri_ctrl = ctrl

    def _aux_generator_freq(self, ctrl: int, div: int) -> float:
        mutant = self._mutant
        if mutant == "rtc_ignores_enable" and ctrl is self.rtc_ctrl:
            ctrl |= C.CLK_RTC_CTRL_ENABLE
        if mutant == "rtc_ignores_kill" and ctrl is self.rtc_ctrl:
            ctrl &= ~C.CLK_RTC_CTRL_KILL
        if mutant == "rtc_div_ignored" and ctrl is self.rtc_ctrl:
            div = 0x100
        if mutant == "adc_div_ignored" and ctrl is self.adc_ctrl:
            div = 0x100
        if (
            mutant == "usb_aux_is_sys_pll"
            and ctrl is self.usb_ctrl
            and (ctrl >> 5) & 7 == C.CLK_RTC_CTRL_AUXSRC_PLL_USB
        ):
            ctrl = (ctrl & ~(7 << 5)) | (C.CLK_RTC_CTRL_AUXSRC_PLL_SYS << 5)
        return super()._aux_generator_freq(ctrl, div)

    def update(self) -> None:
        mutant, rp2040 = self._mutant, self.rp2040
        if not mutant.startswith("update_"):
            super().update()
            return

        def sys() -> None:
            clk_sys = self.sys_freq
            if mutant == "update_zero_updates_clk_sys":
                changed = clk_sys != rp2040.clk_sys
            elif mutant == "update_notifies_unchanged_sys":
                changed = bool(clk_sys)
            else:
                changed = bool(clk_sys) and clk_sys != rp2040.clk_sys
            if changed:
                C._set_clk_sys(rp2040, clk_sys)

        def peri() -> None:
            clk_peri = self.peri_freq
            changed = (
                clk_peri != rp2040.clk_peri
                if mutant == "update_zero_updates_clk_peri"
                else bool(clk_peri) and clk_peri != rp2040.clk_peri
            )
            if changed and mutant != "update_skips_peri":
                C._set_clk_peri(rp2040, clk_peri)

        def ref() -> None:
            if mutant == "update_skips_ref":
                return
            hz = self.ref_freq
            if mutant == "update_ref_only_on_change" and hz == self._last_ref:
                return
            self._last_ref = hz
            rp2040.watchdog.clk_ref_changed(hz)

        def rtc() -> None:
            if mutant != "update_skips_rtc":
                rp2040.rtc.clk_rtc_changed(self.rtc_freq)

        if mutant == "update_peri_before_sys":
            peri()
            sys()
        else:
            sys()
            peri()
        if mutant == "update_rtc_before_ref":
            rtc()
            ref()
        else:
            ref()
            rtc()

    def reset_tree(self) -> None:
        mutant, rp2040 = self._mutant, self.rp2040
        if not mutant.startswith("reset_tree_"):
            super().reset_tree()
            return
        if mutant != "reset_tree_keeps_plls":
            rp2040.pll_sys.reset()
            rp2040.pll_usb.reset()
        if rp2040.clk_sys != C.DEFAULT_CLK:
            C._set_clk_sys(rp2040, C.DEFAULT_CLK)
        if rp2040.clk_peri != C.DEFAULT_CLK and mutant != "reset_tree_keeps_clk_peri":
            C._set_clk_peri(rp2040, C.DEFAULT_CLK)
        if mutant != "reset_tree_skips_ref":
            rp2040.watchdog.clk_ref_changed(self.ref_freq)
        if mutant != "reset_tree_skips_rtc":
            rp2040.rtc.clk_rtc_changed(self.rtc_freq)

    def reset(self) -> None:
        mutant, sys_ctrl, rtc_div, wake = (
            self._mutant,
            getattr(self, "sys_ctrl", 0),
            getattr(self, "rtc_div", 0x100),
            getattr(self, "_stored", {}).get(C.WAKE_EN0),
        )
        super().reset()
        if mutant == "reset_keeps_sys_ctrl":
            self.sys_ctrl = sys_ctrl
        elif mutant == "reset_keeps_rtc_div":
            self.rtc_div = rtc_div
        elif mutant == "reset_keeps_wake_en" and wake is not None:
            self._stored[C.WAKE_EN0] = wake

    def read_uint32(self, offset: int) -> int:
        mutant = self._mutant
        if mutant == "ref_ctrl_read_unmasked" and offset == C.CLK_REF_CTRL:
            return self.ref_ctrl
        if mutant == "sys_selected_constant" and offset == C.CLK_SYS_SELECTED:
            return 1
        if mutant == "enabled_reads_ones" and offset in (C.ENABLED0, C.ENABLED1):
            return 0xFFFFFFFF
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        mutant = self._mutant
        if mutant == "ref_div_unmasked" and offset == C.CLK_REF_DIV:
            self.ref_div = value
            self.rp2040.update_clocks()
            return
        super().write_uint32(offset, value)
        if (mutant == "peri_div_write_retunes" and offset == C.CLK_PERI_DIV) or (
            mutant == "usb_ctrl_write_retunes" and offset == C.CLK_USB_CTRL
        ):
            self.rp2040.update_clocks()

    def _fc_start(self, src: int) -> None:
        mutant = self._mutant
        if mutant == "fc_ignores_interval":
            interval, self._fc_interval = self._fc_interval, 0
            super()._fc_start(src)
            self._fc_interval = interval
        elif mutant == "fc_ignores_delay":
            delay, self._fc_delay = self._fc_delay, 0
            super()._fc_start(src)
            self._fc_delay = delay
        else:
            super()._fc_start(src)

    def _fc_done(self) -> None:
        mutant = self._mutant
        if mutant in ("fc_max_exclusive", "fc_min_exclusive"):
            end_hz = self.frequency_of(self._fc_src)
            died = self._fc_started_hz == 0 or end_hz == 0
            khz = 0.0 if died else end_hz / 1000.0
            status = C.FC0_STATUS_DONE | (C.FC0_STATUS_DIED if died else 0)
            if not died:
                if khz >= self._fc_max_khz and mutant == "fc_max_exclusive" or khz > self._fc_max_khz:
                    status |= C.FC0_STATUS_FAST
                if khz <= self._fc_min_khz and mutant == "fc_min_exclusive" or khz < self._fc_min_khz:
                    status |= C.FC0_STATUS_SLOW
            status |= (
                C.FC0_STATUS_FAIL
                if status & (C.FC0_STATUS_DIED | C.FC0_STATUS_FAST | C.FC0_STATUS_SLOW)
                else C.FC0_STATUS_PASS
            )
            self._fc_status = status
            self._fc_result = int(khz * (1 << C.FC0_RESULT_FRAC_BITS)) & C.FC0_RESULT_MASK
            return
        super()._fc_done()
        if mutant == "fc_never_died":
            self._fc_status &= ~(C.FC0_STATUS_DIED | C.FC0_STATUS_FAIL)
            self._fc_status |= C.FC0_STATUS_PASS
        elif mutant == "fc_result_not_scaled":
            self._fc_result >>= C.FC0_RESULT_FRAC_BITS


class MutantPLL(P.RPPLL):
    """The reference's PLL with one change of logic."""

    def __init__(self, rp2040: Any, name: str, mutant: str) -> None:
        self._mutant = mutant
        super().__init__(rp2040, name)

    @property
    def refdiv(self) -> int:
        if self._mutant == "pll_refdiv_zero_is_zero" and not self.cs & P.PLL_CS_REFDIV_MASK:
            return 0
        return super().refdiv

    @property
    def reference(self) -> float:
        if self._mutant == "pll_refdiv_zero_is_zero" and not self.cs & P.PLL_CS_REFDIV_MASK:
            return 0.0
        return super().reference

    @property
    def locked(self) -> bool:
        mutant = self._mutant
        if mutant == "pll_lock_always":
            return True
        if mutant == "pll_lock_ignores_vcopd":
            return not self.pwr & P.PLL_PWR_PD and self.reference > 0
        if mutant == "pll_lock_ignores_reference":
            return not self.pwr & (P.PLL_PWR_PD | P.PLL_PWR_VCOPD)
        return super().locked

    @property
    def frequency(self) -> float:
        mutant = self._mutant
        pwr, cs = self.pwr, self.cs
        if mutant == "pll_ignores_pd":
            self.pwr = pwr & ~P.PLL_PWR_PD
        elif mutant == "pll_ignores_vcopd":
            self.pwr = pwr & ~P.PLL_PWR_VCOPD
        elif mutant == "pll_ignores_postdivpd":
            self.pwr = pwr & ~P.PLL_PWR_POSTDIVPD
        try:
            if mutant == "pll_bypass_divides_by_postdiv" and cs & P.PLL_CS_BYPASS and not pwr & P.PLL_PWR_PD:
                return self.reference / ((self.postdiv1 or 1) * (self.postdiv2 or 1))
            if mutant == "pll_bypass_not_divided_by_refdiv" and cs & P.PLL_CS_BYPASS and not pwr & P.PLL_PWR_PD:
                return self.reference * (self.refdiv or 1)
            if (
                mutant == "pll_bypass_needs_vco"
                and cs & P.PLL_CS_BYPASS
                and pwr & (P.PLL_PWR_VCOPD | P.PLL_PWR_POSTDIVPD)
            ):
                return 0.0
            if (
                mutant == "pll_postdiv_zero_is_zero"
                and not cs & P.PLL_CS_BYPASS
                and (self.postdiv1 == 0 or self.postdiv2 == 0)
            ):
                return 0.0
            return super().frequency
        finally:
            self.pwr = pwr

    def write_uint32(self, offset: int, value: int) -> None:
        mutant = self._mutant
        if mutant == "pll_write_does_not_retune":
            update, self.rp2040.update_clocks = self.rp2040.update_clocks, lambda: None
            try:
                super().write_uint32(offset, value)
            finally:
                self.rp2040.update_clocks = update
            return
        if mutant == "pll_pwr_mask_wrong" and offset == P.PLL_PWR:
            value &= 0x0D
        if mutant == "pll_prim_mask_wrong" and offset == P.PLL_PRIM:
            value &= 0x73000
        super().write_uint32(offset, value)

    def reset(self) -> None:
        mutant, pwr, fbdiv = self._mutant, getattr(self, "pwr", 0x2D), getattr(self, "fbdiv_int", 0)
        super().reset()
        if mutant == "pll_reset_keeps_pwr":
            self.pwr = pwr
        elif mutant == "pll_reset_keeps_fbdiv":
            self.fbdiv_int = fbdiv


def mutant_rig(mutant: str) -> Rig:
    """A chip whose CLOCKS and PLL blocks carry one change of the reference's logic ("control": none, which must agree)."""
    return Rig(
        "mutant",
        lambda: (
            (lambda chip, name: MutantClocks(chip, name, mutant)),
            (lambda chip, name: MutantPLL(chip, name, mutant)),
        ),
    )
