"""The inward half of the border (docs/tasks/narrow-the-input-plan.md, step 4; the list is in docs/records/0102-c-abi-for-the-core.md): what the Cython shells call INTO the C++ core.

A shell reaches the core through a pointer to its block (`self._block.x`), the chip's `Mcu` (`self._mcu.x`) or, for a few, the block the shell owns (`_own`, `_core`). A new host (the wasm module under `wasmhost`, later a JS or Dart one) cannot
hold a C++ pointer: every member listed here must exist as an export of the C ABI. So the set is **pinned**: this test reads the shells and fails when one calls something that is not in `SURFACE` (the list grew: decide whether it is an input of
the world, and add it with its class and to 0102) and when `SURFACE` names something no shell calls any more (the list shrank: delete the line). `snapshot()` is one call per block and is not scanned.

Classes - what a host would have to be given:
  control    drive the chip: build / run / reset / memory / the interrupt hook                           (an export)
  accessor   find a block of the chip from its number                                                       (a selector argument, not an export)
  port       an observer a host may install (`tap`, `after_tap`, `dreq_tap`, `input_tap`, `tick_tap`)       (an export and an import)
  input      the world tells the chip something: a byte, a device's answer, a pin, the USB host            (an export)
  config     a parameter the world sets or a device reads of the configuration                              (an export)
  register   the bus view of a block (`read` / `write32` / `write_atomic` / `window_handler`)               (the bus, one export)
  construct  creating / detaching the block                                                                 (the chip builds its blocks)
  test       what the oracles and the tests apply to or read off one block (the clock tree's notices, the USB's `raise_*`, the XIP counters, the pin images, a block's own `reset()`): not a host input; kept in the shell
             because the oracles need them, and not on the host's list (to go when an oracle can drive the same thing through the chip)
"""

import re
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1] / "src" / "rp2040py" / "native"
CLASSES = {"control", "accessor", "port", "input", "config", "register", "construct", "test"}

_ROOT = r"(?:self\._block|self\._mcu|rp2040\._mcu|chip\._mcu|self\._own|self\._core|\(<\w+> \w+\)\._block|\(<\w+> self\.\w+\)\._block|\w+\._block)"


def _c(text: str, cls: str) -> dict[str, str]:
    return dict.fromkeys(text.split(), cls)


def _merge(*parts: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for part in parts:
        merged.update(part)
    return merged


SURFACE: dict[str, dict[str, str]] = {
    "_adc": _merge(
        _c("complete_adc_read default_adc_read start_adc_read", "input"),
        _c("num_channels sample_time", "config"),
        _c("reset", "test"),
    ),
    "_busctrl": _c("set_perf_counter", "input"),
    "_clocks": _merge(_c("reset_tree retune", "control"), _c("set_crystal_hz", "config"), _c("reset", "test")),
    "_cortex_m0_core": _c("bus", "accessor"),
    "_dma": _c("clear_dreq set_dreq", "input"),
    "_gpio_pin": _c("init", "construct"),
    "_gspi": _merge(
        _c("attach detach", "construct"),
        _c("reset", "test"),
    ),
    "_i2c": _merge(
        _c("arbitration_lost complete_connect complete_read complete_start complete_stop complete_write", "input"),
        _c("control fs_clock_high fs_clock_low master_bits ss_clock_high ss_clock_low", "config"),
        _c("reset", "test"),
    ),
    "_io": _merge(_c("read", "register"), _c("reset", "test")),
    "_pads": _merge(_c("read", "register"), _c("reset", "test")),
    "_pio": _merge(
        _c("pin_directions_changed pin_values_changed", "input"),
        _c("advance stop", "control"),
        _c("write32 write_atomic", "register"),
        _c("machines pin_directions pin_values stopped", "test"),
        _c("reset", "test"),
    ),
    "_pll": _c("frequency", "config"),
    "_ppb": _merge(_c("timer", "test"), _c("clk_sys_changed tick_changed", "plumbing"), _c("reset", "test")),
    "_pwm": _merge(
        _c("gpio_on_input", "input"),
        _c("channels gpio_direction gpio_value", "test"),
        _c("reset", "test"),
    ),
    "_rosc": _merge(
        _c("bind_random_source", "input"), _c("frequency hz", "config"), _c("read", "register"), _c("reset", "test")
    ),
    "_rp2040": _merge(
        _c(
            "adc bus busctrl clocks dma i2c io pads pins pio_block pll ppb psm pwm resets rosc rtc sio spi ssi syscfg sysinfo tbman timer uart usb vreg watchdog xip xosc",
            "accessor",
        ),
        _c(
            "bind_uart_peri_sinks build core1_running enter_reset_code leave_reset reset set_core1_enabled set_interrupt_unhooked set_irq_hook set_memory use_clock",
            "control",
        ),
        _c("after_tap after_tap_ctx dreq_tap dreq_tap_ctx input_tap input_tap_ctx tap tap_ctx", "port"),
    ),
    "_rtc": _merge(_c("clk_rtc_changed", "plumbing"), _c("reset", "test")),
    "_simulator": _merge(_c("pio_block", "accessor"), _c("begin_batch run set_monotonic use_clock", "control")),
    "_sio": _merge(
        _c("gpio_output_enable gpio_value qspi_gpio_output_enable qspi_gpio_value", "test"),
        _c("read_wide window_handler write32", "register"),
        _c("update_hardware_divider", "plumbing"),
        _c("init", "construct"),
        _c("reset", "test"),
    ),
    "_spi": _merge(
        _c("complete_transmit", "input"),
        _c("clock_divisor control0 data_bits", "config"),
        _c("power_on", "construct"),
        _c("reset", "test"),
    ),
    "_ssi": _merge(_c("detach", "construct"), _c("reset", "test")),
    "_syscfg": _c("reset", "test"),
    "_sysinfo": _c("reset", "test"),
    "_tbman": _c("reset", "test"),
    "_timer": _merge(
        _c("raw_write_value write32", "register"), _c("tick_changed", "plumbing"), _c("detach init", "construct")
    ),
    "_uart": _merge(
        _c("feed_byte", "input"),
        _c("frac_divisor int_divisor", "config"),
        _c("detach init", "construct"),
        _c("reset", "test"),
    ),
    "_usb": _merge(
        _c(
            "connect_device disconnect_device dpram_updated finish_read finish_write reset_device schedule_read send_setup_packet set_connected has_device ep_status_stall_nak int_raw sie_status",
            "input",
        ),
        _c(
            "host_reaction_microseconds host_transaction_delay_microseconds read_delay_microseconds write_delay_microseconds",
            "config",
        ),
        _c("check_interrupts sie_status_updated stall_nak_updated", "plumbing"),
        _c("reset", "test"),
    ),
    "_vreg_and_chip_reset": _c("record_reset_cause set_psm_restart_flag", "control"),
    "_watchdog": _merge(
        _c("fire_timeout", "control"),
        _c("tick_tap tick_tap_ctx", "port"),
        _c("timer", "test"),
        _c("clk_ref_changed", "plumbing"),
        _c("reset", "test"),
    ),
    "_xip_ctrl": _merge(
        _c("ctr_acc ctr_hit", "input"),
        _c("aux_window_handler write", "register"),
        _c("reset", "test"),
    ),
    "_xosc": _c("reset", "test"),
}


# What only the oracles and the tests apply to a block directly, whatever the shell's own code makes of it (the clock tree's notices to its consumers, the USB's `raise_*` hooks, the XIP counters, the
# watchdog's timeout): class "test", not on the host's list.
_TEST_STIMULI = {
    "_busctrl": "set_perf_counter",
    "_ppb": "clk_sys_changed tick_changed timer",
    "_timer": "tick_changed",
    "_watchdog": "clk_ref_changed fire_timeout timer",
    "_rtc": "clk_rtc_changed",
    "_sio": "update_hardware_divider",
    "_usb": "check_interrupts sie_status_updated stall_nak_updated int_raw sie_status ep_status_stall_nak",
    "_xip_ctrl": "ctr_acc ctr_hit",
}
for _shell, _names in _TEST_STIMULI.items():
    for _name in _names.split():
        assert _name in SURFACE[_shell], (_shell, _name)
        SURFACE[_shell][_name] = "test"


def _without_snapshot(text: str) -> str:
    kept, skipping, indent = [], False, 0
    for line in text.split("\n"):
        match = re.match(r"(\s*)def snapshot\(self", line)
        if match:
            skipping, indent = True, len(match.group(1))
            continue
        if skipping:
            if line.strip() == "" or len(line) - len(line.lstrip()) > indent:
                continue
            skipping = False
        kept.append(line)
    return "\n".join(kept)


def scan() -> dict[str, set[str]]:
    """The members each shell uses on the core, outside `snapshot()`, as found in the source of the shells."""
    found: dict[str, set[str]] = {}
    for path in sorted(NATIVE.glob("*.pyx")):
        text = _without_snapshot(path.read_text())
        text = re.sub(r"#[^\n]*", "", text)
        text = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        names = {match.group(1) for match in re.finditer(_ROOT + r"\.(\w+)", text)}
        if names:
            found[path.stem] = names
    return found


def test_the_table_only_uses_the_known_classes():
    for shell, members in SURFACE.items():
        for member, cls in members.items():
            assert cls in CLASSES, f"{shell}.{member}: unknown class {cls!r}"


def test_no_shell_calls_the_core_outside_the_pinned_surface():
    found = scan()
    unknown = {
        shell: sorted(names - set(SURFACE.get(shell, {})))
        for shell, names in found.items()
        if names - set(SURFACE.get(shell, {}))
    }
    assert not unknown, (
        "a shell calls into the core with something that is not on the list (tests/test_native_input_surface.py, docs/records/0102): "
        f"{unknown}. Is it an input of the world, a control or a parameter? Then add it with its class; if it is the shell talking to its own block, "
        "do it in the core instead."
    )


def test_the_pinned_surface_has_no_stale_entries():
    found = scan()
    stale = {
        shell: sorted(set(members) - found.get(shell, set()))
        for shell, members in SURFACE.items()
        if set(members) - found.get(shell, set())
    }
    assert not stale, f"the table names members no shell uses any more (delete them): {stale}"


@pytest.mark.parametrize("cls", sorted(CLASSES))
def test_the_class_counts_are_printable(cls):
    # the counts of the table are what docs/records/0102 quotes; this only keeps the table from being empty in a class by mistake
    assert any(c == cls for members in SURFACE.values() for c in members.values())
