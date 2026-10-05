"""A lockstep differential oracle for the PWM: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the PWM design note). The method is the ADC's, the I2C's, the SPI's and the others' (tests/utils/*_diff.py): one chip whose PWM is the
*pure-Python* reference (``peripherals/_pwm.py``, built explicitly because the facade would hand out the native one) and one whose PWM is whatever the facade gives (today the same
class, later the C++ one), fed one stream of operations:

* writes and reads of **every register offset** of the eight channels (CSR, DIV, CTR, CC, TOP) and of EN, INTR, INTE, INTF, INTS, through the bus and through its four aliases (normal, XOR,
  SET, CLR), unimplemented offsets included (so the warnings are compared);
* the **pins** the PWM drives, which are the chip's real pins: IO_BANK0's FUNCSEL is written so a pin follows the PWM (and sometimes something else), PADS_BANK0's input enable and pulls are written, the **B input** of a channel is driven
  from outside (``set_input_value`` / ``release_input``, which is what the gated and the edge-counting divider modes count), and every pin's level and output enable are read back;
* the block's own **direction word** poked from outside (after a reset it is never zero, and the B-input path is gated on it);
* **simulated time** advanced by ``clock.tick()`` from nothing to many wraps (the compare alarms and the wrap alarm of every enabled channel fire inside it, in order), ``reset()``,
  ``check_interrupts()``.

After **each** step: the register file read through the bus, the channels' private state (the registers as written, the double-buffer flags, the B-input bookkeeping, the counter's own timer
and the three alarms' targets), the block's pin words and interrupt registers, every pin's level / output enable / input level, whether an alarm is scheduled and when (seen through the
clock), the NVIC's pending bits, and the ordered, timestamped log of everything that left the block (IRQ line changes, DREQ sets, pin transitions, warnings); an exception on one side and
not the other is a difference like any other.

The rig *replaces* the chip's DMA with a recorder and wraps ``set_interrupt``. Logic mutants of the reference are made by changing its *source text* (``MUTATIONS``) and loading the
result as a module of its own, so a mutant is exactly the reference with one expression different.
"""

import inspect
import random
import types
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _pwm as P
from rp2040py.rp2040 import RP2040

PWM_BASE = 0x40050000
IO_BANK0 = 0x40014000
PADS_BANK0 = 0x4001C000
NVIC_ISPR = 0xE000E200
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
CHANNELS = 8
STRIDE = 0x14
PIN_COUNT = 30
FUNC_PWM = 4

CHANNEL_REGISTERS = (P.CHN_CSR, P.CHN_DIV, P.CHN_CTR, P.CHN_CC, P.CHN_TOP)
BLOCK_REGISTERS = (P.EN, P.INTR, P.INTE, P.INTF, P.INTS)
ALL_REGISTERS = tuple(c * STRIDE + r for c in range(CHANNELS) for r in CHANNEL_REGISTERS) + BLOCK_REGISTERS
UNIMPLEMENTED = (0xB4, 0xB8, 0xC0, 0x100, 0xFFC)
TICKS = (0, 1, 7, 8, 9, 50, 300, 1000, 5000, 20_000)
BIG_TICKS = (100_000,)
TOPS = (0, 1, 2, 3, 5, 10, 20, 100, 0xFFFF)
DIVS = (0x10, 0x10, 0x20, 0x18, 0x1F, 0x00, 0x100, 0x1000, 0x28)


def pins_of(channel: int) -> tuple[int, int, int, int]:
    """(A1, B1, A2, B2) of a channel; the second pair does not exist for channel 7 (-1)."""
    a2 = 16 + channel * 2 if channel < 7 else -1
    return channel * 2, channel * 2 + 1, a2, a2 + 1 if a2 >= 0 else -1


class Rig:
    """One chip plus the timestamped log of everything that left its PWM."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        # the chip's own PWM starts with its timers running (its reset comes with the chip's); stop it so that a replaced one leaves no alarm behind
        self.chip.pwm.reset()
        if kind == "pure":
            self._replace_the_pwm(P.RPPWM)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_pwm(factory)
        self._tap()

    def _replace_the_pwm(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(chip, "PWM_BASE")
        chip.pwm = new
        chip.peripherals[PWM_BASE >> 12] = new
        new.reset()

    def _tap(self) -> None:
        chip, log = self.chip, self.log

        class _Dma:
            def set_dreq(self, channel: int) -> None:
                log.append(("dreq", chip.clock.nanos, int(channel), 1))

            def clear_dreq(self, channel: int) -> None:
                log.append(("dreq", chip.clock.nanos, int(channel), 0))

        chip.dma = _Dma()  # type: ignore[assignment]  # the PWM looks `rp2040.dma` up on every wrap
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", chip.clock.nanos, int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", chip.clock.nanos, _m, str(name), str(message))),
            )
        for index in range(PIN_COUNT):
            chip.gpio[index].add_listener(
                lambda new, old, _i=index: log.append(("pin", chip.clock.nanos, _i, int(new), int(old)))
            )

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(PWM_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(PWM_BASE + op[2] + op[1]))
        elif kind == "func":  # IO_BANK0 GPIOn_CTRL: FUNCSEL, and an input override now and then
            chip.write_uint32(IO_BANK0 + 8 * op[1] + 4, op[2] & 0xFFFFFFFF)
        elif (
            kind == "pad"
        ):  # PADS_BANK0 GPIOn: the input enable the B input needs, the pulls that decide a released pin
            chip.write_uint32(PADS_BANK0 + 4 + 4 * op[1], op[2] & 0xFFFFFFFF)
        elif kind == "input":
            chip.gpio[op[1]].set_input_value(bool(op[2]))
        elif kind == "release":
            chip.gpio[op[1]].release_input()
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif (
            kind == "direction"
        ):  # a probe: the block's direction word set from outside. After a reset it is never zero (the A pins stay outputs), which the B-input path is gated on
            chip.pwm.gpio_direction = op[1]
        elif kind == "reset":
            chip.pwm.reset()
        elif kind == "check":
            chip.pwm.check_interrupts()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, pwm = self.chip, self.chip.pwm
        channels = []
        for channel in pwm.channels:
            timer = channel.timer
            channels.append(
                (
                    int(channel.csr),
                    int(channel.div),
                    int(channel.cc),
                    int(channel.top),
                    bool(channel.last_b_value),
                    bool(channel.counting_up),
                    bool(channel.cc_updated),
                    bool(channel.top_updated),
                    float(channel.tick_counter),
                    int(channel.div_mode),
                    bool(timer.enable),
                    int(timer.mode),
                    float(timer.prescaler),
                    int(timer.top),
                    int(timer.raw_counter),
                    int(timer.counter),
                    int(channel.alarm_a.target),
                    int(channel.alarm_b.target),
                    bool(channel.alarm_a.enable),
                    bool(channel.alarm_b.enable),
                    bool(channel.alarm_bottom.enable),
                )
            )
        return {
            "regs": tuple(int(chip.read_uint32(PWM_BASE + offset)) for offset in ALL_REGISTERS),
            "channels": tuple(channels),
            "block": (
                int(pwm.gpio_value),
                int(pwm.gpio_direction),
                int(pwm._int_raw),
                int(pwm._int_enable),
                int(pwm._int_force),
                int(pwm.int_status),
                int(pwm.clock_freq),
            ),
            "pins": tuple(
                (
                    int(chip.gpio[i].value),
                    bool(chip.gpio[i].output_enable),
                    bool(chip.gpio[i].input_value),
                    int(chip.gpio[i].function_select),
                )
                for i in range(PIN_COUNT)
            ),
            "alarms": (
                bool(chip.clock.has_scheduled_alarm),
                float(chip.clock.nanos_to_next_alarm),
            ),  # the chip has no other alarm in this rig
            "nvic": int(chip.read_uint32(NVIC_ISPR)),
            "time": float(chip.clock.nanos),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _pwm.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    # -- the wrap
    "wrap_no_interrupt": ("        self.pwm.channel_interrupt(self.index)\n", "        pass\n", 1),
    "wrap_no_double_buffer": (
        "self.pwm.channel_interrupt(self.index)\n        self._update_double_buffered()",
        "self.pwm.channel_interrupt(self.index)",
        1,
    ),
    "wrap_ignores_phase_correct": (
        "if not (self.csr & CSR_PH_CORRECT):\n            self.set_a(",
        "if True:\n            self.set_a(",
        1,
    ),
    "wrap_a_ge": ("self.set_a(self.alarm_a.target > 0)", "self.set_a(self.alarm_a.target >= 0)", 1),
    "wrap_b_ge": ("self.set_b(self.alarm_b.target > 0)", "self.set_b(self.alarm_b.target >= 0)", 1),
    "wrap_a_from_b": ("self.set_a(self.alarm_a.target > 0)", "self.set_a(self.alarm_b.target > 0)", 1),
    "alarm_a_sets": ("lambda: self.set_a(False))", "lambda: self.set_a(True))", 1),
    "alarm_b_drives_a": ("lambda: self.set_b(False))", "lambda: self.set_a(False))", 1),
    "alarm_bottom_off": ("self.alarm_bottom.enable = True", "self.alarm_bottom.enable = False", 1),
    # -- the double buffers
    "cc_swapped": (
        "self.alarm_b.target = self.cc >> 16\n            self.alarm_a.target = self.cc & 0xFFFF",
        "self.alarm_a.target = self.cc >> 16\n            self.alarm_b.target = self.cc & 0xFFFF",
        1,
    ),
    "cc_a_unmasked": ("self.alarm_a.target = self.cc & 0xFFFF", "self.alarm_a.target = self.cc", 1),
    "cc_not_buffered": (
        "self.cc = value\n            self.cc_updated = True",
        "self.cc = value\n            self.cc_updated = True\n            self.alarm_b.target = value >> 16\n            self.alarm_a.target = value & 0xFFFF",
        1,
    ),
    "top_not_buffered": (
        "self.top = value & 0xFFFF\n            self.top_updated = True",
        "self.top = value & 0xFFFF\n            self.top_updated = True\n            self.timer.top = self.top",
        1,
    ),
    "top_unmasked": ("self.top = value & 0xFFFF", "self.top = value", 1),
    "top_flag_kept": (
        "self.timer.top = self.top\n            self.top_updated = False",
        "self.timer.top = self.top",
        1,
    ),
    "cc_flag_kept": (
        "self.alarm_a.target = self.cc & 0xFFFF\n            self.cc_updated = False",
        "self.alarm_a.target = self.cc & 0xFFFF",
        1,
    ),
    # -- CSR
    "csr_en_always_updates": ("if value & CSR_EN and not (self.csr & CSR_EN):", "if value & CSR_EN:", 1),
    "csr_en_never_updates": ("if value & CSR_EN and not (self.csr & CSR_EN):", "if False:", 1),
    "csr_keeps_strobes": ("self.csr = value & ~(CSR_PH_ADV | CSR_PH_RET)", "self.csr = value", 1),
    "csr_strobes_tested_after_the_clear": ("if value & CSR_PH_ADV:", "if self.csr & CSR_PH_ADV:", 1),
    "csr_ret_tested_after_the_clear": ("if value & CSR_PH_RET:", "if self.csr & CSR_PH_RET:", 1),
    "csr_strobes_not_gated_on_en": ("            if value & CSR_EN:\n                if value & CSR_PH_ADV:", "            if True:\n                if value & CSR_PH_ADV:", 1),
    "csr_strobes_gated_on_the_counter_running": ("            if value & CSR_EN:\n                if value & CSR_PH_ADV:", "            if self.timer.enable:\n                if value & CSR_PH_ADV:", 1),
    "csr_adv_retards": ("self.timer.advance(1)\n                if value & CSR_PH_RET:", "self.timer.advance(-1)\n                if value & CSR_PH_RET:", 1),
    "csr_ret_advances": ("                    self.timer.advance(-1)\n            self.div_mode", "                    self.timer.advance(1)\n            self.div_mode", 1),
    "csr_ret_missing": ("                if value & CSR_PH_RET:\n                    self.timer.advance(-1)\n", "", 1),
    "csr_divmode_one_bit": (
        "(self.csr >> CSR_DIVMODE_SHIFT) & CSR_DIVMODE_MASK",
        "(self.csr >> CSR_DIVMODE_SHIFT) & 0x1",
        1,
    ),
    "csr_b_direction_inverted": (
        "self.set_b_direction(self.div_mode == PWMDivMode.FREE_RUNNING)",
        "self.set_b_direction(self.div_mode != PWMDivMode.FREE_RUNNING)",
        1,
    ),
    "csr_last_b_not_updated": (
        "            self.last_b_value = self.gpio_b_value\n            self.timer.mode",
        "            self.timer.mode",
        1,
    ),
    "csr_phase_correct_decrements": (
        "TimerMode.ZIGZAG if value & CSR_PH_CORRECT else TimerMode.INCREMENT",
        "TimerMode.DECREMENT if value & CSR_PH_CORRECT else TimerMode.INCREMENT",
        1,
    ),
    "csr_update_enable_missing": (
        "            self.set_b_direction(self.div_mode == PWMDivMode.FREE_RUNNING)\n            self.update_enable()",
        "            self.set_b_direction(self.div_mode == PWMDivMode.FREE_RUNNING)",
        1,
    ),
    # -- DIV, CTR
    "div_zero_is_zero": ("(int_value if int_value else 256)", "(int_value if int_value else 0)", 1),
    "div_frac_over_15": ("frac_value / 16", "frac_value / 15", 1),
    "div_int_seven_bits": ("int_value = (value >> 4) & 0xFF", "int_value = (value >> 4) & 0x7F", 1),
    "div_register_narrow": ("self.div = value & 0x000FFFFF", "self.div = value & 0x0000FFFF", 1),
    "ctr_unmasked": ("self.timer.set(value & 0xFFFF)", "self.timer.set(value)", 1),
    "ctr_reads_raw": ("            return self.timer.counter\n", "            return self.timer.raw_counter\n", 1),
    # -- the outputs
    "a_invert_uses_b": ("if self.csr & CSR_A_INV:", "if self.csr & CSR_B_INV:", 1),
    "b_invert_uses_a": ("if self.csr & CSR_B_INV:", "if self.csr & CSR_A_INV:", 1),
    "second_a_on_channel_7": (
        "16 + self.index * 2 if self.index < 7 else -1",
        "16 + self.index * 2 if self.index < 8 else -1",
        1,
    ),
    "second_b_stops_at_6": (
        "self.pin_b2 = 16 + self.index * 2 + 1 if self.index < 7 else -1",
        "self.pin_b2 = 16 + self.index * 2 + 1 if self.index < 6 else -1",
        1,
    ),
    "gpio_set_inverted": (
        "new_gpio_value = self.gpio_value | bit if value else self.gpio_value & ~bit",
        "new_gpio_value = self.gpio_value & ~bit if value else self.gpio_value | bit",
        1,
    ),
    "gpio_set_no_update": (
        "self.gpio_value = new_gpio_value\n            self.rp2040.gpio[index].check_for_updates()",
        "self.gpio_value = new_gpio_value",
        1,
    ),
    "gpio_dir_no_update": (
        "self.gpio_direction = new_gpio_direction\n            self.rp2040.gpio[index].check_for_updates()",
        "self.gpio_direction = new_gpio_direction",
        1,
    ),
    "gpio_dir_inverted": (
        "new_gpio_direction = self.gpio_direction | bit if output else self.gpio_direction & ~bit",
        "new_gpio_direction = self.gpio_direction & ~bit if output else self.gpio_direction | bit",
        1,
    ),
    # -- the B input
    "b_value_and": ("bool(self.pwm.gpio_read(self.pin_b1) or (", "bool(self.pwm.gpio_read(self.pin_b1) and (", 1),
    "b_reads_output": ("return self.rp2040.gpio[index].input_value", "return self.rp2040.gpio[index].output_value", 1),
    "b_same_value_processed": (
        "if value == self.last_b_value:\n            return",
        "if False:\n            return",
        1,
    ),
    "b_value_not_remembered": (
        "self.last_b_value = value\n\n        if self.div_mode == PWMDivMode.B_GATED",
        "if self.div_mode == PWMDivMode.B_GATED",
        1,
    ),
    "b_gated_no_update": (
        "            self.update_enable()\n        elif self.div_mode == PWMDivMode.B_RISING_EDGE:",
        "            pass\n        elif self.div_mode == PWMDivMode.B_RISING_EDGE:",
        1,
    ),
    "b_rising_counts_low": (
        "if value:\n                self.tick_counter += 1",
        "if not value:\n                self.tick_counter += 1",
        1,
    ),
    "b_falling_counts_high": (
        "if not value:\n                self.tick_counter += 1",
        "if value:\n                self.tick_counter += 1",
        1,
    ),
    "b_tick_gt": ("if self.tick_counter >= self.timer.prescaler:", "if self.tick_counter > self.timer.prescaler:", 1),
    "b_tick_counter_cleared": ("self.tick_counter -= self.timer.prescaler", "self.tick_counter = 0", 1),
    "b_advance_two": (
        "            self.timer.advance(1)\n            self.tick_counter",
        "            self.timer.advance(2)\n            self.tick_counter",
        1,
    ),
    "gated_enable_inverted": (
        "(div_mode == PWMDivMode.B_GATED and self.gpio_b_value)",
        "(div_mode == PWMDivMode.B_GATED and not self.gpio_b_value)",
        1,
    ),
    "enable_ignores_csr": ("enable = bool(csr & CSR_EN)", "enable = True", 1),
    "on_input_ignores_direction": (
        "if self.gpio_direction and 1 << index:\n            return",
        "if False:\n            return",
        1,
    ),
    "on_input_misses_second_b": (
        "if channel.pin_b1 == index or channel.pin_b2 == index:",
        "if channel.pin_b1 == index:",
        1,
    ),
    # -- EN and the interrupts
    "en_setter_no_buffer": (
        "if value and not (self.csr & CSR_EN):\n            self._update_double_buffered()\n        if value:",
        "if value:",
        1,
    ),
    "en_reads_zero": ("        return self.csr & CSR_EN\n", "        return 0\n", 1),
    "en_reads_inverted": ("        return self.csr & CSR_EN\n", "        return (self.csr & CSR_EN) ^ 1\n", 1),
    "en_reads_the_timer": ("        return self.csr & CSR_EN\n", "        return int(self.timer.enable)\n", 1),
    "en_wrong_bit": ("self.channels[3].en = value & (1 << 3)", "self.channels[3].en = value & (1 << 4)", 1),
    "int_raw_overwritten": ("self._int_raw |= 1 << index", "self._int_raw = 1 << index", 1),
    "intr_write_toggles": ("self._int_raw &= ~(value & INT_MASK)", "self._int_raw ^= value & INT_MASK", 1),
    "inte_unmasked": ("self._int_enable = value & INT_MASK", "self._int_enable = value", 1),
    "intf_unmasked": ("self._int_force = value & INT_MASK", "self._int_force = value", 1),
    "int_status_force_masked": (
        "(self._int_raw & self._int_enable) | self._int_force",
        "(self._int_raw | self._int_force) & self._int_enable",
        1,
    ),
    "int_status_no_force": (
        "(self._int_raw & self._int_enable) | self._int_force",
        "(self._int_raw & self._int_enable)",
        1,
    ),
    "check_interrupts_raw": ("bool(self.int_status)", "bool(self._int_raw)", 1),
    "dreq_off_by_one": ("DREQChannel.DREQ_PWM_WRAP0 + index", "DREQChannel.DREQ_PWM_WRAP0 + index + 1", 1),
    # -- the register map
    "stride_16": ("channel = math.floor(offset / 0x14)", "channel = math.floor(offset / 0x10)", 2),
    "seven_channels": ("for i in range(8)", "for i in range(7)", 1),
    # -- reset
    "reset_direction_zero": ("self.gpio_direction = 0xFFFFFFFF", "self.gpio_direction = 0", 1),
    "reset_top_zero": ("self.write_register(CHN_TOP, 0xFFFF)", "self.write_register(CHN_TOP, 0)", 1),
    "reset_div_two": ("self.write_register(CHN_DIV, 0x01 << 4)", "self.write_register(CHN_DIV, 0x02 << 4)", 1),
    "reset_timer_left_on": (
        "        self.timer.enable = False\n        self.timer.reset()",
        "        self.timer.reset()",
        1,
    ),
    "reset_cc_kept": ("        self.write_register(CHN_CC, 0)\n", "", 1),
}
MUTANTS = tuple(MUTATIONS)


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(P)
    assert source.count(old) == count, f"{name}: {old!r} occurs {source.count(old)} times in _pwm.py, expected {count}"
    module = types.ModuleType(f"rp2040py.peripherals._pwm_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_pwm mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def mutant_rig(name: str) -> Rig:
    """A rig whose PWM is the pure-Python one with one expression changed, to prove the comparison sees a change of *logic*."""
    return Rig("mutant", _mutant_module(name).RPPWM)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _channel_scenario(r: random.Random) -> list[tuple]:
    """A channel set up the way firmware does it - pins to the PWM function, TOP, CC, DIV, then CSR - followed by time and some input activity on its B pin."""
    ch = r.randrange(CHANNELS) if r.random() < 0.85 else r.choice((0, 0, 1, 7))
    pins = [p for p in pins_of(ch) if p >= 0]
    ops: list[tuple] = []
    for pin in pins:
        if r.random() < 0.85:
            ops.append(("func", pin, FUNC_PWM if r.random() < 0.9 else r.choice((0, 1, 2, 3, 5, 6, 7, 9, 31))))
        if r.random() < 0.85:
            ops.append(("pad", pin, 0x56 if r.random() < 0.8 else r.choice((0x16, 0x5A, 0x52, 0x5E, r.getrandbits(8)))))
    base = ch * STRIDE
    top = r.choice(TOPS)
    ops.append(("write", base + P.CHN_TOP, top if r.random() < 0.9 else r.getrandbits(32), ALIASES[0]))
    cc_a = r.choice((0, 1, top // 2, top, top + 1, r.randrange(top + 3)))
    cc_b = r.choice((0, 1, top // 2, top, top + 1, r.randrange(top + 3)))
    ops.append(("write", base + P.CHN_CC, (cc_b << 16) | cc_a if r.random() < 0.9 else r.getrandbits(32), ALIASES[0]))
    ops.append(("write", base + P.CHN_DIV, r.choice(DIVS) if r.random() < 0.9 else r.getrandbits(32), ALIASES[0]))
    if r.random() < 0.3:
        ops.append(("write", base + P.CHN_CTR, r.choice((0, 1, top, top + 1, r.getrandbits(32))), ALIASES[0]))
    mode = r.choice((0, 0, 0, 1, 2, 3))
    csr = (
        (mode << P.CSR_DIVMODE_SHIFT)
        | r.choice((0, 0, P.CSR_PH_CORRECT))
        | r.choice((0, 0, P.CSR_A_INV))
        | r.choice((0, 0, P.CSR_B_INV))
    )
    if r.random() < 0.08:
        csr |= r.choice((P.CSR_PH_ADV, P.CSR_PH_RET, P.CSR_PH_ADV | P.CSR_PH_RET))
    if r.random() < 0.75:
        ops.append(("write", base + P.CHN_CSR, csr | P.CSR_EN, ALIASES[0]))
    else:
        ops.append(("write", base + P.CHN_CSR, csr, ALIASES[0]))
        ops.append(("write", P.EN, r.choice((1 << ch, 0xFF, (1 << ch) | 1)), ALIASES[0]))
    if r.random() < 0.5:
        ops.append(("write", P.INTE, r.choice((1 << ch, 0xFF, 0)), ALIASES[0]))
    for _ in range(r.choice((0, 0, 1, 2, 4))):  # phase strobes on the running channel, the counter read after each
        ops.append(("write", base + P.CHN_CSR, csr | P.CSR_EN | r.choice((P.CSR_PH_ADV, P.CSR_PH_RET, P.CSR_PH_ADV | P.CSR_PH_RET, P.CSR_PH_ADV)), ALIASES[0]))
        ops.append(("read", base + P.CHN_CTR, ALIASES[0]))
    b = pins[1]
    if mode and r.random() < 0.6:
        ops.append(("direction", r.choice((0, 0, 0xFFFFFFFF, r.getrandbits(32)))))
    for _ in range(r.choice((2, 3, 4, 6))):
        if mode and r.random() < 0.8:
            ops.append(("input", b, r.random() < 0.5))
        elif r.random() < 0.1:
            ops.append(("release", b))
        ops.append(("tick", r.choice(TICKS) if r.random() < 0.98 else r.choice(BIG_TICKS)))
        if r.random() < 0.3:
            ops.append(("read", base + r.choice(CHANNEL_REGISTERS), ALIASES[0]))
    return ops


def _value(r: random.Random, offset: int) -> int:
    channel_reg = offset % STRIDE if offset < P.EN else None
    if offset == P.INTR:
        return r.choice((0xFF, 0, 1, r.getrandbits(32)))
    if offset in (P.INTE, P.INTF):
        return r.choice((0, 1, 0xFF, 0xFFFFFFFF, r.getrandbits(32)))
    if offset == P.EN:
        return r.choice((0, 0xFF, 1, r.getrandbits(8), r.getrandbits(32)))
    if channel_reg == P.CHN_TOP:
        return r.choice((*TOPS, r.getrandbits(32)))
    if channel_reg == P.CHN_DIV:
        return r.choice((*DIVS, r.getrandbits(32)))
    return r.getrandbits(32)


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.22:
            ops.extend(_channel_scenario(r))
        elif roll < 0.40:
            offset = r.choice(ALL_REGISTERS[:-1])
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.44:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.56:
            ops.append(
                ("read", r.choice(ALL_REGISTERS + UNIMPLEMENTED), r.choice(ALIASES) if r.random() < 0.1 else ALIASES[0])
            )
        elif roll < 0.74:
            ops.append(("tick", r.choice(TICKS)))
        elif roll < 0.82:
            ops.append(
                (
                    "input",
                    r.randrange(PIN_COUNT)
                    if r.random() < 0.4
                    else r.choice([p for c in range(CHANNELS) for p in pins_of(c)[1::2] if p >= 0]),
                    r.random() < 0.5,
                )
            )
        elif roll < 0.85:
            ops.append(("release", r.randrange(PIN_COUNT)))
        elif roll < 0.90:
            ops.append(
                ("func", r.randrange(PIN_COUNT), r.choice((FUNC_PWM, FUNC_PWM, FUNC_PWM, 5, 31, r.getrandbits(32))))
            )
        elif roll < 0.92:
            ops.append(("pad", r.randrange(PIN_COUNT), r.choice((0x56, 0x16, 0x5A, 0x52, r.getrandbits(8)))))
        elif roll < 0.93:
            ops.append(("direction", r.choice((0, 0xFFFFFFFF, r.getrandbits(32)))))
        elif roll < 0.95:
            ops.append(
                ("write", P.INTF if r.random() < 0.5 else P.INTE, r.choice((0, 1, 0xFF, r.getrandbits(32))), ALIASES[0])
            )
        elif roll < 0.975:
            ops.append(("reset",))
        else:
            ops.append(("check",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def _snapshot(rig: Rig) -> dict[str, Any]:
    """The rig's snapshot, or - when reading it raises, which a broken block can do - that fact as the snapshot (so it is a difference like any other)."""
    try:
        return rig.snapshot()
    except Exception as error:  # noqa: BLE001
        return {"raised": f"{type(error).__name__}: {error}"}


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b.get(key, "<missing>")
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and isinstance(value_b, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:70] for i in where[:6]]} vs {[str(value_b[i])[:70] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
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
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = _snapshot(a), _snapshot(b)
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    pwm = rig.chip.pwm
    for op in ops:
        before = len(rig.log)
        if op[0] == "tick":
            count("tick")
            if any(c.timer.enable for c in pwm.channels):
                count("tick.running")
        if op[0] == "reset":
            count("reset.running" if any(c.timer.enable for c in pwm.channels) else "reset")
        if op[0] == "input" and any(
            c.div_mode != P.PWMDivMode.FREE_RUNNING and op[1] in (c.pin_b1, c.pin_b2) for c in pwm.channels
        ):
            count("input.counted_mode")
        if op[0] == "write" and op[3] == 0 and op[1] < P.EN and op[1] % STRIDE == P.CHN_CSR:
            count("csr.phase_correct" if op[2] & P.CSR_PH_CORRECT else "csr")
            if op[2] & (P.CSR_PH_ADV | P.CSR_PH_RET):
                count("csr.strobe")
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        if rig.chip.clock.has_scheduled_alarm:
            count("alarm.scheduled")
        for c in pwm.channels:
            if c.timer.enable and c.div_mode == P.PWMDivMode.B_GATED:
                count("running.gated")
            if c.tick_counter:
                count("tick_counter.nonzero")
            if c.cc_updated:
                count("double_buffer.cc_pending")
            if c.top_updated:
                count("double_buffer.top_pending")
            if c.csr & (P.CSR_A_INV | P.CSR_B_INV) and c.timer.enable:
                count("running.inverted")
        for event in rig.log[before:]:
            count(f"log.{event[0]}" + (f".{event[3]}" if event[0] in ("irq", "dreq") else ""))
            if event[0] == "pin":
                count("pin.second" if event[2] >= 16 else "pin.first")
    return counts
