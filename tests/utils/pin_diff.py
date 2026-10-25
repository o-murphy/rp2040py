"""A lockstep differential oracle for the pins (IO_BANK0 / IO_QSPI / PADS windows, the GPIO pin layer): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md (#29, the shared pin bank), docs/records/0098-datasheet-conformance-audit.md ("IO_BANK0, IO_QSPI, PADS_BANK0, PADS_QSPI"). The method is the PWM's and the PPB's
(tests/utils/*_diff.py): one chip whose pins are the *pure-Python* reference (``_gpio_pin.GPIOPin``, built explicitly because the facade would hand out the native ones) and one whose pins are whatever
the chip builds (today the Cython shells over ``core/pin.hpp``, later the shared C++ bank), fed one stream of operations:

* writes and reads of the **IO_BANK0, IO_QSPI, PADS_BANK0 and PADS_QSPI** register files through the bus **and its XOR/SET/CLR aliases** (every GPIO's STATUS / CTRL, the interrupt groups of all three
  destinations, the pad words, SWCLK / SWD, VOLTAGE_SELECT), unimplemented offsets included (so the warnings are compared);
* the outside world: a level **driven** onto a pin or **released** (``set_input_value`` / ``release_input``), and ``refresh_input``;
* the **sources** of an output: the real SIO's GPIO and QSPI output registers (written through the bus, so SIO's own pin updates are exercised) and probes for the PWM and the two PIOs;
* ``reset()`` of a pin's IO half, PAD half or both, and of the register windows.

After **each** step: every pin's registers (CTRL, pad, the three masks of proc 0, the IRQ status, the seen level), its derived values (STATUS, state, input / output values and enables, IRQ value), the proc 1 / dormant
masks the window stores, the register files read back through the bus, and the ordered log of everything that left the pins (listener calls with their states, ``update_io_interrupt()``, the PWM's and
the PIOs' input hooks, the warnings); an exception on one side and not the other is a difference like any other.

Logic mutants of the reference are made by changing the *source text* of ``_gpio_pin.py`` (``MUTATIONS``) and loading the result as a module of its own.
"""

import gc
import inspect
import random
import types
from collections.abc import Callable
from typing import Any

from rp2040py import _gpio_pin as G
from rp2040py import _pin_bank as G_bank
from rp2040py.irq import IRQ
from rp2040py.rp2040 import RP2040
from utils.is32bit import IS32BIT

IO_BANK0, IO_QSPI, PADS_BANK0, PADS_QSPI, SIO = 0x40014000, 0x40018000, 0x4001C000, 0x40020000, 0xD0000000
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
GPIO_PINS, QSPI_PINS = 30, 6

SIO_GPIO_HI_OUT, SIO_GPIO_HI_OE = 0x30, 0x40
SIO_GPIO_OUT, SIO_GPIO_OE = 0x10, 0x20

# register files: (base, [offsets worth poking])
IO0_OFFSETS = tuple(range(0, 0x190, 4))
IOQ_OFFSETS = tuple(range(0, 0x58, 4))
PAD0_OFFSETS = tuple(range(0, 0x84, 4))
PADQ_OFFSETS = tuple(range(0, 0x1C, 4))
UNIMPLEMENTED = (
    (IO_BANK0, 0x190),
    (IO_BANK0, 0x1F0),
    (IO_BANK0, 0xF1),
    (IO_QSPI, 0x58),
    (IO_QSPI, 0x80),
    (PADS_BANK0, 0x84),
    (PADS_BANK0, 0x100),
    (PADS_QSPI, 0x1C),
    (PADS_QSPI, 0x7C),
    (PADS_QSPI, 0x80),
)  # (PADS_QSPI, 0x7C) and 0x80 are bank0's SWCLK and SWD: not QSPI pads
BLOCKS = (
    (IO_BANK0, IO0_OFFSETS, GPIO_PINS),
    (IO_QSPI, IOQ_OFFSETS, QSPI_PINS),
    (PADS_BANK0, PAD0_OFFSETS, GPIO_PINS),
    (PADS_QSPI, PADQ_OFFSETS, QSPI_PINS),
)


class _PwmProbe:
    """Stands in for the PWM: the two source registers pins read, and the hook an input change calls."""

    def __init__(self, log: list) -> None:
        self.gpio_direction = 0
        self.gpio_value = 0
        self._log = log

    def gpio_on_input(self, index: int) -> None:
        self._log.append(("pwm.input", index))


class _PioProbe:
    """Stands in for one PIO: the two source registers pins read; no state machines, so no pin waits."""

    def __init__(self) -> None:
        self.pin_directions = 0
        self.pin_values = 0

    def check_pin_waits(self, pin: int) -> None:
        pass


class Rig:
    """One chip plus the timestamped log of everything that left its pins."""

    def __init__(
        self,
        kind: str,
        factory: "Callable[..., Any] | None" = None,
        windows: "tuple[Any, Any] | None" = None,
        bank_module: Any = None,
    ) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        chip = self.chip
        chip.pwm = _PwmProbe(self.log)
        chip.pio = [_PioProbe(), _PioProbe()]
        if kind in ("pure", "mutant"):
            pin_class = G.GPIOPin if kind == "pure" else factory
            assert pin_class is not None
            self._replace_the_pins(pin_class, windows, bank_module or G_bank)
        self._tap()

    def _replace_the_pins(
        self, pin_class: Callable[..., Any], windows: "tuple[Any, Any] | None" = None, bank_module: Any = G_bank
    ) -> None:
        """Both banks' pins, as the chip builds them (the QSPI pins always output-enabled, with the pad resets of record 0050)."""
        from rp2040py.qspi_pads import QSPI_PAD_RESET_VALUES

        chip = self.chip
        # ... in banks of the reference's own (the chip asks its banks for GPIO_IN and the IO_BANK0 interrupt line, and the chip's native banks would answer for the native pins)
        gpio_bank = bank_module.GPIOPinBank(chip, GPIO_PINS)
        qspi_bank = bank_module.GPIOPinBank(chip, QSPI_PINS, True)
        names = [pin.name for pin in chip.qspi]
        chip._gpio_bank, chip._qspi_bank = gpio_bank, qspi_bank
        chip.gpio, chip.qspi = gpio_bank.shells, qspi_bank.shells
        for index in range(GPIO_PINS):
            pin_class(chip, index, bank=gpio_bank)
        for index in range(QSPI_PINS):
            pin = pin_class(chip, index, name=names[index], bank=qspi_bank)
            pin.reset(io=False, pad_reset=QSPI_PAD_RESET_VALUES[index])
        # ... and the pure-Python register windows over them: the facade's would be the native ones, which work on the native pin bank
        from rp2040py.peripherals import _io, _pads

        if windows is not None:  # the modules of a mutant of the reference windows
            _io, _pads = windows

        for attribute, base, window in (
            ("io_bank0", 0x40014000, lambda: _io.RPIO(chip, "IO_BANK0_BASE")),
            ("io_qspi", 0x40018000, lambda: _io.RPIO(chip, "IO_QSPI_BASE", pins=chip.qspi)),
            ("pads_bank0", 0x4001C000, lambda: _pads.RPPADS(chip, "PADS_BANK0_BASE", "bank0")),
            ("pads_qspi", 0x40020000, lambda: _pads.RPPADS(chip, "PADS_QSPI_BASE", "qspi")),
        ):
            new = window()
            setattr(chip, attribute, new)
            chip.peripherals[base >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        # Every rig starts from pins whose announced state is reconciled with their power-on registers
        for pins in (chip.gpio, chip.qspi):
            for pin in pins:
                pin.check_for_updates()
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger, method, lambda name, message, _m=method: log.append(("log", _m, str(name), str(message)))
            )
        # The IO_BANK0 line is seen where it leaves the pins: at `set_interrupt` (on the native chip it goes from the C++ pins straight to `Mcu::set_interrupt`, which a replaced
        # `set_interrupt` still receives), one entry per time the pins re-evaluate it.
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            if irq == IRQ.IO_BANK0:
                log.append(("io_irq",))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for bank, pins in (("gpio", chip.gpio), ("qspi", chip.qspi)):
            for pin in pins:
                pin.add_listener(
                    lambda new, old, _b=bank, _i=pin.index: log.append(("pin", _b, _i, int(new), int(old)))
                )

    # --- the operations --------------------------------------------------------------------------------------------------

    def _pins(self, bank: int) -> list:
        return self.chip.gpio if bank == 0 else self.chip.qspi

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(op[1] + op[4] + op[2], op[3] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(op[1] + op[3] + op[2]))
        elif kind == "drive":
            self._pins(op[1])[op[2]].set_input_value(bool(op[3]))
        elif kind == "release":
            self._pins(op[1])[op[2]].release_input()
        elif kind == "refresh":
            self._pins(op[1])[op[2]].refresh_input()
        elif kind == "sio":
            chip.write_uint32(SIO + op[1] + op[3], op[2] & 0xFFFFFFFF)
        elif kind == "pwm":
            chip.pwm.gpio_direction, chip.pwm.gpio_value = op[1] & 0x3FFFFFFF, op[2] & 0x3FFFFFFF
        elif kind == "pio":
            chip.pio[op[1]].pin_directions, chip.pio[op[1]].pin_values = op[2] & 0x3FFFFFFF, op[3] & 0x3FFFFFFF
        elif kind == "pin_reset":
            self._pins(op[1])[op[2]].reset(io=bool(op[3]), pads=bool(op[4]))
        elif kind == "io_reset":
            (chip.io_bank0 if op[1] == 0 else chip.io_qspi).reset()
        elif kind == "pads_reset":
            (chip.pads_bank0 if op[1] == 0 else chip.pads_qspi).reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def _pin_state(self, pin: Any) -> tuple:
        return tuple(sorted(pin.snapshot().items()))

    def snapshot(self) -> dict[str, Any]:
        chip = self.chip
        out: dict[str, Any] = {}
        for bank, pins in (("gpio", chip.gpio), ("qspi", chip.qspi)):
            out[bank] = tuple(self._pin_state(pin) for pin in pins)
        for pads in (chip.pads_bank0, chip.pads_qspi):
            out[f"pads.{pads.name}"] = tuple(pads.snapshot().values())
        for block in (chip.io_bank0, chip.io_qspi):
            out[f"extra.{block.name}"] = block.snapshot()["extra"]
        for base, offsets, _count in BLOCKS:
            out[f"regs.{base:x}"] = tuple(int(chip.read_uint32(base + offset)) for offset in offsets)
        # what a property of all the pins at once is (a chip asks its bank for these)
        out["banks"] = (
            int(chip.gpio_values),
            int(chip._gpio_bank.input_values()),
            bool(chip._gpio_bank.any_irq_value()),
            int(chip._qspi_bank.input_values()),
            bool(chip._qspi_bank.any_irq_value()),
        )
        out["sio"] = (
            int(chip.sio.gpio_value),
            int(chip.sio.gpio_output_enable),
            int(chip.sio.qspi_gpio_value),
            int(chip.sio.qspi_gpio_output_enable),
        )
        return out


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _gpio_pin.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    "pad_reset_old": ("PAD_RESET = 0x56", "PAD_RESET = 0x36", 1),
    "pad_reset_ie_off": ("PAD_RESET = 0x56", "PAD_RESET = 0x16", 1),
    "ctrl_default": ("        self.ctrl = 0x1F\n        self.pad_value = PAD_RESET", "        self.ctrl = 0x0\n        self.pad_value = PAD_RESET", 1),
    "edge_on_every_apply": ("        if edges and seen != self._seen:\n", "        if edges:\n", 1),
    "edge_high_swapped": ("self.irq_status |= IRQ_EDGE_HIGH if seen else IRQ_EDGE_LOW", "self.irq_status |= IRQ_EDGE_LOW if seen else IRQ_EDGE_HIGH", 1),
    "levels_never_follow": ("        self.irq_status = (self.irq_status & ~(IRQ_LEVEL_HIGH | IRQ_LEVEL_LOW)) | (\n            IRQ_LEVEL_HIGH if seen else IRQ_LEVEL_LOW\n        )\n", "        pass\n", 1),
    "level_swapped": ("IRQ_LEVEL_HIGH if seen else IRQ_LEVEL_LOW", "IRQ_LEVEL_LOW if seen else IRQ_LEVEL_HIGH", 1),
    "seen_ignores_ie": ("        return self._effective_raw_input_value and self.input_enable\n", "        return self._effective_raw_input_value\n", 1),
    "seen_not_stored": ("        self._seen = seen\n", "", 1),
    "reset_edges": ("        self._sync_irq_levels(edges=False)  # a reset raises no edge; the level bits follow the pin\n", "        self._sync_irq_levels(edges=True)\n", 1),
    "reset_keeps_levels": ("        self._sync_irq_levels(edges=False)  # a reset raises no edge; the level bits follow the pin\n", "", 1),
    "reset_pad_ignores_value": ("            self.pad_value = pad_reset\n", "            self.pad_value = PAD_RESET\n", 1),
    "reset_io_keeps_masks": ("            self.irq_enable_mask = 0\n            self.irq_force_mask = 0\n            self.irq_status = 0\n        if pads:", "            self.irq_status = 0\n        if pads:", 1),
    "pad_changed_missing": ("        if self._seen_level() != self._seen:\n            self.refresh_input()\n", "        pass\n", 1),
    "pad_changed_always": ("        if self._seen_level() != self._seen:\n            self.refresh_input()\n", "        self.refresh_input()\n", 1),
    "irq_to_io_not_told": ("        if self.irq_value != prev_irq_value:\n            self.rp2040.update_io_interrupt()\n        if self.function_select == FUNCTION_PWM:", "        if self.function_select == FUNCTION_PWM:", 1),
    "irq_told_always": ("        if self.irq_value != prev_irq_value:\n            self.rp2040.update_io_interrupt()\n        if self.function_select == FUNCTION_PWM:", "        self.rp2040.update_io_interrupt()\n        if self.function_select == FUNCTION_PWM:", 1),
    "pwm_not_told": ("        if self.function_select == FUNCTION_PWM:\n            self.rp2040.pwm.gpio_on_input(self.index)\n", "", 1),
    "pwm_told_always": ("        if self.function_select == FUNCTION_PWM:\n            self.rp2040.pwm.gpio_on_input(self.index)\n", "        self.rp2040.pwm.gpio_on_input(self.index)\n", 1),
    "release_sets_high": ("        self._apply_input_value(self._effective_raw_input_value)", "        self._apply_input_value(True)", 1),
    "release_keeps_driven": ("        self._driven = False\n        self._apply_input_value", "        self._apply_input_value", 1),
    "set_input_not_driven": ("        self._driven = True\n        self._apply_input_value(value)", "        self._apply_input_value(value)", 1),
    "pull_both_wins_up": ("        if self.pullup_enabled and not self.pulldown_enabled:\n            return True", "        if self.pullup_enabled:\n            return True", 1),
    "pull_down_ignored": ("        if self.pulldown_enabled and not self.pullup_enabled:\n            return False\n", "", 1),
    "driven_ignored": ("        if self._driven:\n            return self._raw_input_value\n", "", 1),
    "irq_override_ignored": ("        return _apply_override(self.raw_interrupt, self.irq_override)", "        return self.raw_interrupt", 1),
    "raw_interrupt_ignores_force": ("return bool((self.irq_status & self.irq_enable_mask) | self.irq_force_mask)", "return bool(self.irq_status & self.irq_enable_mask)", 1),
    "input_override_ignored": ("        return _apply_override(self._effective_raw_input_value and self.input_enable, self.input_override)", "        return self._effective_raw_input_value and self.input_enable", 1),
    "output_override_ignored": ("        return _apply_override(self.raw_output_value, self.output_override)", "        return self.raw_output_value", 1),
    "oe_override_ignored": ("        return _apply_override(self.raw_output_enable, self.output_enable_override)", "        return self.raw_output_enable", 1),
    "override_invert_as_low": ("    if override_type == 1:\n        return not value\n", "    if override_type == 1:\n        return False\n", 1),
    "override_high_as_low": ("    if override_type == 3:\n        return True\n", "    if override_type == 3:\n        return False\n", 1),
    "sio_oe_wrong_source": ("            return bool(rp2040.sio.gpio_output_enable & bitmask)", "            return bool(rp2040.sio.gpio_value & bitmask)", 1),
    "sio_value_wrong_source": ("            return bool(rp2040.sio.gpio_value & bitmask)\n\n        if function_select == FUNCTION_PIO0:\n            return bool(rp2040.pio[0].pin_values", "            return bool(rp2040.sio.gpio_output_enable & bitmask)\n\n        if function_select == FUNCTION_PIO0:\n            return bool(rp2040.pio[0].pin_values", 1),
    "pio0_oe_uses_pio1": ("            return bool(rp2040.pio[0].pin_directions & bitmask)", "            return bool(rp2040.pio[1].pin_directions & bitmask)", 1),
    "pio1_value_uses_pio0": ("            return bool(rp2040.pio[1].pin_values & bitmask)", "            return bool(rp2040.pio[0].pin_values & bitmask)", 1),
    "pwm_oe_uses_value": ("            return bool(rp2040.pwm.gpio_direction & bitmask)", "            return bool(rp2040.pwm.gpio_value & bitmask)", 1),
    "qspi_sio_oe_uses_gpio": ("return bool(rp2040.sio.qspi_gpio_output_enable & bitmask) if function_select == FUNCTION_SIO else True", "return bool(rp2040.sio.gpio_output_enable & bitmask) if function_select == FUNCTION_SIO else True", 1),
    "qspi_sio_value_uses_gpio": ("return bool(rp2040.sio.qspi_gpio_value & bitmask) if function_select == FUNCTION_SIO else False", "return bool(rp2040.sio.gpio_value & bitmask) if function_select == FUNCTION_SIO else False", 1),
    "qspi_always_enabled_lost": ("if function_select == FUNCTION_SIO else True", "if function_select == FUNCTION_SIO else False", 1),
    "qspi_xip_value_high": ("if function_select == FUNCTION_SIO else False\n\n        if function_select == FUNCTION_PWM:", "if function_select == FUNCTION_SIO else True\n\n        if function_select == FUNCTION_PWM:", 1),
    "status_irq_to_proc_raw": ("irq_to_proc = 1 << 26 if self.irq_value else 0", "irq_to_proc = 1 << 26 if self.raw_interrupt else 0", 1),
    "status_in_from_pad_gated": ("in_from_pad = 1 << 17 if self._effective_raw_input_value else 0", "in_from_pad = 1 << 17 if self.input_value else 0", 1),
    "status_oe_from_peri_after": ("oe_from_peri = 1 << 12 if self.raw_output_enable else 0", "oe_from_peri = 1 << 12 if self.output_enable else 0", 1),
    "status_out_to_pad_raw": ("out_to_pad = 1 << 9 if self.output_value else 0", "out_to_pad = 1 << 9 if self.raw_output_value else 0", 1),
    "value_ignores_oe": ("        if self.output_enable and not self.output_disable:\n            return GPIOPinState.HIGH if self.output_value else GPIOPinState.LOW", "        if self.raw_output_enable and not self.output_disable:\n            return GPIOPinState.HIGH if self.output_value else GPIOPinState.LOW", 1),
    "value_ignores_od": ("        if self.output_enable and not self.output_disable:", "        if self.output_enable:", 1),
    "pad_ignores_the_chips_own_drive": ("        driven_by_chip = self._chip_drive_level()\n        if driven_by_chip is not None:\n            return driven_by_chip\n", "", 1),
    "chips_drive_beats_an_external_one": ("        if self._driven:\n            return self._raw_input_value\n        driven_by_chip = self._chip_drive_level()\n        if driven_by_chip is not None:\n            return driven_by_chip\n", "        driven_by_chip = self._chip_drive_level()\n        if driven_by_chip is not None:\n            return driven_by_chip\n        if self._driven:\n            return self._raw_input_value\n", 1),
    "pad_reads_inverted_own_drive": ("            return self._last_value == GPIOPinState.HIGH\n        return None", "            return self._last_value != GPIOPinState.HIGH\n        return None", 1),
    "qspi_xip_side_counts_as_a_drive": ("            if self._always_output_enabled and self.function_select != FUNCTION_SIO:\n                return None\n", "", 1),
    "own_drive_change_is_not_an_input_change": ("            if self._seen_level() != self._seen or self.input_value != input_before:\n                self.refresh_input()\n", "", 1),
    "own_drive_change_only_moves_the_seen_level": ("            if self._seen_level() != self._seen or self.input_value != input_before:\n", "            if self._seen_level() != self._seen:\n", 1),
    "own_drive_change_only_moves_the_input": ("            if self._seen_level() != self._seen or self.input_value != input_before:\n", "            if self.input_value != input_before:\n", 1),
    "bus_keeper_as_pull_up": ("        if self.pulldown_enabled and self.pullup_enabled:\n            # Pull high when high, pull low when low:\n            return GPIOPinState.INPUT_BUS_KEEPER", "        if self.pulldown_enabled and self.pullup_enabled:\n            return GPIOPinState.INPUT_PULL_UP", 1),
    "check_for_updates_every_time": ("        if value != last_value:\n            input_before = self.input_value\n", "        if True:\n            input_before = self.input_value\n", 1),
    "check_for_updates_forgets": ("            input_before = self.input_value\n            self._last_value = value\n", "            input_before = self.input_value\n", 1),
    "update_irq_clears_level": ("        if value & IRQ_EDGE_LOW and self.irq_status & IRQ_EDGE_LOW:\n            self.irq_status &= ~IRQ_EDGE_LOW\n", "        if value & IRQ_EDGE_LOW and self.irq_status & IRQ_EDGE_LOW:\n            self.irq_status &= ~(IRQ_EDGE_LOW | IRQ_LEVEL_LOW)\n", 1),
    "update_irq_edge_high_not_cleared": ("        if value & IRQ_EDGE_HIGH and self.irq_status & IRQ_EDGE_HIGH:\n            self.irq_status &= ~IRQ_EDGE_HIGH\n            self.rp2040.update_io_interrupt()\n", "", 1),
    "update_irq_edge_low_silent": ("            self.irq_status &= ~IRQ_EDGE_LOW\n            self.rp2040.update_io_interrupt()\n", "            self.irq_status &= ~IRQ_EDGE_LOW\n", 1),
}  # fmt: skip

# The reference's register windows (`peripherals/_io.py`, `_pads.py`) the same way: name -> (module, exact text, replacement, count).
WINDOW_MUTATIONS: dict[str, tuple[str, str, str, int]] = {
    "io_ctrl_mask_wider": ("io", "CTRL_MASK = 0x3003331F", "CTRL_MASK = 0x3003337F", 1),
    "io_ctrl_write_silent": ("io", "                gpio.ctrl = value & CTRL_MASK\n                gpio.check_for_updates()\n", "                gpio.ctrl = value & CTRL_MASK\n", 1),
    "io_status_write_reaches_ctrl": ("io", "            if is_ctrl:\n                gpio.ctrl = value & CTRL_MASK\n                gpio.check_for_updates()\n            return", "            gpio.ctrl = value & CTRL_MASK\n            gpio.check_for_updates()\n            return", 1),
    "io_ctrl_status_swapped": ("io", "return gpio.ctrl if is_ctrl else gpio.status", "return gpio.status if is_ctrl else gpio.ctrl", 1),
    "io_ctrl_flag_inverted": ("io", "return self.pins[gpio_index], bool(offset & 0x4)", "return self.pins[gpio_index], not offset & 0x4", 1),
    "io_bank0_intr_moved": ("io", '("intr", None, 0x0F0)', '("intr", None, 0x0F4)', 1),
    "io_bank0_ints_moved": ("io", '("ints", 0, 0x120)', '("ints", 0, 0x124)', 1),
    "io_bank0_dormant_inte_moved": ("io", '("inte", 2, 0x160)', '("inte", 2, 0x168)', 1),
    "io_qspi_intr_moved": ("io", '("intr", None, 0x30)', '("intr", None, 0x34)', 1),
    "io_qspi_proc1_intf_moved": ("io", '("intf", 1, 0x44)', '("intf", 1, 0x48)', 1),
    "io_qspi_dormant_ints_moved": ("io", '("ints", 2, 0x54)', '("ints", 2, 0x50)', 1),
    "io_words_rounded_down": ("io", "(len(self.pins) + PINS_PER_WORD - 1) // PINS_PER_WORD", "len(self.pins) // PINS_PER_WORD", 1),
    "io_ints_without_force": ("io", "(pin.irq_status & enable) | force", "pin.irq_status & enable", 1),
    "io_ints_without_enable": ("io", "(pin.irq_status & enable) | force", "pin.irq_status | force", 1),
    "io_intr_written_by_the_nibble": ("io", "(self.raw_write_value >> (slot * 4)) & 0xF", "nibble", 1),
    "io_ints_writable": ("io", '            elif kind == "ints":\n                continue  # read only\n', "", 1),
    "io_inte_silent": ("io", "                    pin.irq_enable_mask = nibble\n                    self.rp2040.update_io_interrupt()\n", "                    pin.irq_enable_mask = nibble\n", 1),
    "io_intf_silent": ("io", "                    pin.irq_force_mask = nibble\n                    self.rp2040.update_io_interrupt()\n", "                    pin.irq_force_mask = nibble\n", 1),
    "io_inte_always_announces": ("io", 'if kind == "inte" and pin.irq_enable_mask != nibble:', 'if kind == "inte":', 1),
    "io_intf_always_announces": ("io", 'elif kind == "intf" and pin.irq_force_mask != nibble:', 'elif kind == "intf":', 1),
    "io_extra_enable_force_swapped": ("io", '[0 if kind == "inte" else 1][index] = nibble', '[1 if kind == "inte" else 0][index] = nibble', 1),
    "io_extra_dormant_is_proc1": ("io", "self._extra[dest][", "self._extra[1][", 1),
    "io_reset_keeps_force": ("io", "enable[index] = force[index] = 0", "enable[index] = 0", 1),
    "io_reset_keeps_enable": ("io", "enable[index] = force[index] = 0", "force[index] = 0", 1),
    "io_word_overruns": ("io", "if index >= len(self.pins):\n                break\n            pin = self.pins[index]\n            if kind", "if index >= len(self.pins) + 1:\n                break\n            pin = self.pins[index % len(self.pins)]\n            if kind", 1),
    "io_unaligned_group_access": ("io", "located = self._locate(offset) if not offset & 3 else None\n        if located is None:\n            return super().read_uint32(offset)", "located = self._locate(offset)\n        if located is None:\n            return super().read_uint32(offset)", 1),
    "pads_mask_wider": ("pads", "PAD_MASK = 0xFF", "PAD_MASK = 0x1FF", 1),
    "pads_write_silent": ("pads", "            gpio.check_for_updates()\n            gpio.pad_changed()", "            gpio.pad_changed()", 1),
    "pads_write_never_moves_the_level": ("pads", "            gpio.check_for_updates()\n            gpio.pad_changed()", "            gpio.check_for_updates()", 1),
    "pads_qspi_in_pin_order": ("pads", "_QSPI_PAD_TO_PIN = (0, 2, 3, 4, 5, 1)", "_QSPI_PAD_TO_PIN = (0, 1, 2, 3, 4, 5)", 1),
    "pads_voltage_select_unmasked": ("pads", "self.voltage_select = value & 1", "self.voltage_select = value", 1),
    "pads_swclk_reset_wrong": ("pads", "SWCLK_RESET = 0xDA", "SWCLK_RESET = 0x5A", 1),
    "pads_swd_reset_wrong": ("pads", "SWD_RESET = 0x5A", "SWD_RESET = 0xDA", 1),
    "pads_reset_keeps_swd": ("pads", "        self.swd = SWD_RESET\n\n    def get_pin", "\n    def get_pin", 1),
    "pads_reset_keeps_swclk": ("pads", "        self.swclk = SWCLK_RESET\n        self.swd = SWD_RESET\n\n    def get_pin", "        self.swd = SWD_RESET\n\n    def get_pin", 1),
    "pads_reset_keeps_voltage_select": ("pads", "        self.voltage_select = 0\n        self.swclk = SWCLK_RESET\n        self.swd = SWD_RESET\n\n    def get_pin", "        self.swclk = SWCLK_RESET\n        self.swd = SWD_RESET\n\n    def get_pin", 1),
    "pads_last_gpio_pad_lost": ("pads", "GPIO_LAST = 0x78", "GPIO_LAST = 0x74", 1),
    "pads_last_qspi_pad_lost": ("pads", "QSPI_LAST = 0x18", "QSPI_LAST = 0x14", 1),
    "pads_debug_pads_in_qspi": ("pads", 'self.bank == "bank0" and offset == SWCLK', "offset == SWCLK", 2),
    "pads_debug_pad_swd_in_qspi": ("pads", 'self.bank == "bank0" and offset == SWD', "offset == SWD", 2),
    "pads_read_gives_another_pad": ("pads", "            gpio = self.get_pin_from_offset(offset)\n            return gpio.pad_value", "            gpio = self.get_pin_from_offset(offset + 4)\n            return gpio.pad_value", 1),
}  # fmt: skip

# name -> (the exact text in _pin_bank.py, what replaces it, how many times it occurs): the reference of what a property of all the pins at once is.
BANK_MUTATIONS: dict[str, tuple[str, str, int]] = {
    "bank_input_bit_shifted": ("                result |= 1 << index\n", "                result |= 1 << (index + 1)\n", 1),
    "bank_input_inverted": ("            if pin.input_value:\n", "            if not pin.input_value:\n", 1),
    "bank_input_stops_at_first": ("                result |= 1 << index\n", "                return 1 << index\n", 1),
    "bank_irq_inverted": ("            if pin.irq_value:\n", "            if not pin.irq_value:\n", 1),
    "bank_irq_raw": ("            if pin.irq_value:\n", "            if pin.raw_interrupt:\n", 1),
    "bank_irq_last_pin_only": ("        for pin in self.shells:\n            if pin.irq_value:\n                return True\n        return False\n", "        return bool(self.shells[-1].irq_value)\n", 1),
}  # fmt: skip

MUTANTS = tuple(MUTATIONS) + tuple(WINDOW_MUTATIONS) + tuple(BANK_MUTATIONS)


def _bank_module(name: str) -> types.ModuleType:
    old, new, count = BANK_MUTATIONS[name]
    source = inspect.getsource(G_bank)
    assert source.count(old) == count, (
        f"{name}: {old!r} occurs {source.count(old)} times in _pin_bank.py, expected {count}"
    )
    module = types.ModuleType(f"rp2040py._pin_bank_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_pin_bank mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(G)
    assert source.count(old) == count, (
        f"{name}: {old!r} occurs {source.count(old)} times in _gpio_pin.py, expected {count}"
    )
    module = types.ModuleType(f"rp2040py._gpio_pin_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_gpio_pin mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def _window_modules(name: str) -> "tuple[types.ModuleType, types.ModuleType]":
    """`_io` and `_pads` with one expression of the named mutant changed (the other module as it is)."""
    from rp2040py.peripherals import _io, _pads

    which, old, new, count = WINDOW_MUTATIONS[name]
    original = _io if which == "io" else _pads
    source = inspect.getsource(original)
    assert source.count(old) == count, (
        f"{name}: {old!r} occurs {source.count(old)} times in _{which}.py, expected {count}"
    )
    module = types.ModuleType(f"rp2040py.peripherals._{which}_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_{which} mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return (module, _pads) if which == "io" else (_io, module)


def mutant_rig(name: str) -> Rig:
    """A rig whose pins (or register windows) are the pure-Python ones with one expression changed, to prove the comparison sees a change of *logic*."""
    if name in WINDOW_MUTATIONS:
        return Rig("mutant", G.GPIOPin, windows=_window_modules(name))
    if name in BANK_MUTATIONS:
        return Rig("mutant", G.GPIOPin, bank_module=_bank_module(name))
    return Rig("mutant", _mutant_module(name).GPIOPin)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _pad_word(r: random.Random) -> int:
    base = r.choice((0x56, 0x52, 0x5A, 0xDA, 0x0, 0x40, 0x48, 0x4C, 0x44, 0xFF, r.getrandbits(8)))
    return base | (r.getrandbits(24) << 8 if r.random() < 0.1 else 0)


def _ctrl_word(r: random.Random) -> int:
    fsel = r.choice((0x1F, 0x1F, 0, 4, 5, 5, 6, 7, 1, 8, 9, r.getrandbits(5)))
    return (
        fsel
        | (r.randrange(4) << 8)
        | (r.randrange(4) << 12)
        | (r.randrange(4) << 16)
        | (r.randrange(4) << 28)
        | (r.getrandbits(32) if r.random() < 0.05 else 0)
    )


def _register_value(r: random.Random, base: int, offset: int) -> int:
    if base == PADS_BANK0 or base == PADS_QSPI:
        return _pad_word(r) if offset != 0 else r.getrandbits(32)
    if offset < 0x100 and base == IO_BANK0 and offset < 0xF0:
        return _ctrl_word(r) if offset & 4 else r.getrandbits(32)
    if base == IO_QSPI and offset < 0x30:
        return _ctrl_word(r) if offset & 4 else r.getrandbits(32)
    return r.choice(
        (0, 0xFFFFFFFF, 0x11111111, 0x88888888, 0x44444444, 0x22222222, r.getrandbits(32), r.getrandbits(32))
    )


def _scenario(r: random.Random) -> list[tuple]:
    """A pin configured and then poked: function and overrides, a pad word, an interrupt enabled, an input driven up and down, the sources moved."""
    bank = r.randrange(2)
    count = GPIO_PINS if bank == 0 else QSPI_PINS
    pin = r.randrange(count)
    io, pads = (IO_BANK0, PADS_BANK0) if bank == 0 else (IO_QSPI, PADS_QSPI)
    pad_offset = 4 + 4 * pin if bank == 0 else 4 + 4 * (0, 5, 1, 2, 3, 4)[pin]
    ops: list[tuple] = [
        ("write", io, 8 * pin + 4, _ctrl_word(r), 0),
        ("write", pads, pad_offset, _pad_word(r), 0),
    ]
    word, slot = (pin // 8, pin % 8) if bank == 0 else (0, pin)
    inte = 0x100 + 4 * word if bank == 0 else 0x34
    intf = 0x110 + 4 * word if bank == 0 else 0x38
    ops.append(("write", io, inte, r.randrange(16) << (4 * slot), 0))
    if r.random() < 0.3:
        ops.append(("write", io, intf, r.randrange(16) << (4 * slot), 0))
    for _ in range(r.randrange(2, 7)):
        roll = r.random()
        if roll < 0.5:
            ops.append(("drive", bank, pin, r.getrandbits(1)))
        elif roll < 0.6:
            ops.append(("release", bank, pin))
        elif roll < 0.8:
            ops.append(("write", pads, pad_offset, _pad_word(r), 0))
        else:
            ops.append(("write", io, 0xF0 + 4 * word if bank == 0 else 0x30, 0xFFFFFFFF, 0))
        ops.append(("read", io, 0xF0 + 4 * word if bank == 0 else 0x30, 0))
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.25:
            ops.extend(_scenario(r))
        elif roll < 0.50:
            base, offsets, _count = r.choice(BLOCKS)
            offset = r.choice(offsets)
            alias = ALIASES[0] if r.random() < 0.75 else r.choice(ALIASES[1:])
            ops.append(("write", base, offset, _register_value(r, base, offset), alias))
        elif roll < 0.54:
            base, offset = r.choice(UNIMPLEMENTED)
            ops.append(("write", base, offset, r.getrandbits(32), ALIASES[0]))
        elif roll < 0.68:
            base, offsets, _count = r.choice(BLOCKS)
            ops.append(("read", base, r.choice(offsets), ALIASES[0] if r.random() < 0.9 else r.choice(ALIASES)))
        elif roll < 0.71:
            base, offset = r.choice(UNIMPLEMENTED)
            ops.append(("read", base, offset, ALIASES[0]))
        elif roll < 0.80:
            bank = r.randrange(2)
            ops.append(("drive", bank, r.randrange(GPIO_PINS if bank == 0 else QSPI_PINS), r.getrandbits(1)))
        elif roll < 0.83:
            bank = r.randrange(2)
            ops.append((r.choice(("release", "refresh")), bank, r.randrange(GPIO_PINS if bank == 0 else QSPI_PINS)))
        elif roll < 0.89:
            reg = r.choice((SIO_GPIO_OUT, SIO_GPIO_OE, SIO_GPIO_HI_OUT, SIO_GPIO_HI_OE))
            ops.append(("sio", reg, r.choice((0, r.getrandbits(32), 1 << r.randrange(30))), r.choice((0, 4, 8, 0xC))))
        elif roll < 0.92:
            ops.append(("pwm", r.getrandbits(30), r.getrandbits(30)))
        elif roll < 0.95:
            ops.append(("pio", r.randrange(2), r.getrandbits(30), r.getrandbits(30)))
        elif roll < 0.98:
            bank = r.randrange(2)
            ops.append(
                (
                    "pin_reset",
                    bank,
                    r.randrange(GPIO_PINS if bank == 0 else QSPI_PINS),
                    r.getrandbits(1),
                    r.getrandbits(1),
                )
            )
        else:
            ops.append((r.choice(("io_reset", "pads_reset")), r.randrange(2)))
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
                f"{key}[{where[:4]}] {[str(value_a[i])[:90] for i in where[:2]]} vs {[str(value_b[i])[:90] for i in where[:2]]}"
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
    try:
        return _lockstep(a, b, ops, perturb)
    finally:
        del a, b
        if IS32BIT:
            gc.collect()


def _lockstep(a: Rig, b: Rig, ops: list[tuple], perturb: "Callable[[Rig, int], None] | None") -> "Divergence | None":
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

    chip = rig.chip
    for op in ops:
        before = len(rig.log)
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(f"log.{event[0]}")
        for pins in (chip.gpio, chip.qspi):
            for pin in pins:
                if pin.irq_status & (G.IRQ_EDGE_HIGH | G.IRQ_EDGE_LOW):
                    count("pin.edge_latched")
                if pin.raw_interrupt:
                    count("pin.interrupt_raw")
                if pin.irq_value:
                    count("pin.irq_value")
                if pin.output_enable:
                    count("pin.output_enabled")
                if pin.function_select in (4, 5, 6, 7):
                    count(f"pin.func{pin.function_select}")
                if pin._driven:
                    count("pin.driven")
                if pin.output_override or pin.output_enable_override or pin.input_override or pin.irq_override:
                    count("pin.overridden")
    return counts
