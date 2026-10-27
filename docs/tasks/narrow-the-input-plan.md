# Narrowing the border, the other direction: what the host calls INTO the core

Working note, 2026-10-26. **Nothing here is built; each step needs its own go-ahead** (CLAUDE.md, "Document" vs. "implement"). Companion to [narrow-the-border-plan.md](narrow-the-border-plan.md) (steps 0-7 done: `McuHost` is 16 slots, all the world's) and to [vm-world-border.md](vm-world-border.md). The maintainer's brief: **this is the same size of work as narrowing the slots** - an inventory, a classification, steps with gates, each checked by the twin oracles.

## Why

`McuHost` is the border *outwards* (the core calls the host). A host also calls *into* the core, and that half was never narrowed: the Cython shells hold pointers to C++ blocks and call their members directly. A new host (the wasm module under `wasmhost`, [0103](../records/0103-wasm-backend.md); a JS or Dart one, theoretical) cannot hold a C++ pointer: every member a shell calls must exist as an export of the C ABI ([0102](../records/0102-c-abi-for-the-core.md)). So the width of this half is the width of the first wasm host, and of every later one. The goal is the same as for the slots: **a short, language-neutral list, and the Cython shell a consumer of that list and nothing more.**

## What is measured (2026-10-26, from the shells' `.pyx` files)

6,737 lines of `.pyx` in `native/`; the biggest are `_rp2040.pyx` (1,315), `_gpio_pin.pyx` (493), `_cortex_m0_core.pyx` (453), `_usb.pyx` (436).

**The chip level (`Mcu`): about 40 different calls, nearly all made once** - to take the address of a block when the chip is assembled (`uart`, `spi`, `i2c`, `sio`, `ssi`, `xip`, `usb`, `pwm`, `ppb`, `watchdog`, `dma`, `clocks`, `pll`, `xosc`, `rosc`, `timer`, `adc`, `rtc`, `psm`, `resets`, `vreg`, `sysinfo`, `syscfg`, `tbman`, `io`, `pads`, `pins`, `busctrl`, `pio_block`) plus the controls: `build`, `run`, `reset`, `enter_reset_code`, `leave_reset`, `set_memory`, `set_core1_enabled`, `core1_running`, `set_irq_hook`, `set_interrupt_unhooked`, `use_clock`, `set_monotonic`, `set_clk_changed_flag`, `stop_address`, `real_io_address`.

**The block level (`_block.<member>`): the members each shell calls on its C++ block**, as counted:

| shell | n | members |
|---|---|---|
| `_uart` | 8 | check_interrupts ctrl feed_byte frac_divisor int_divisor line_ctrl reset update_dreq |
| `_spi` | 9 | check_interrupts clock_divisor complete_transmit control0 control1 data_bits enabled power_on reset |
| `_i2c` | 14 | arbitration_lost check_interrupts complete_connect/read/start/stop/write control fs_clock_high/low ss_clock_high/low master_bits reset |
| `_adc` | 7 | check_interrupts complete_adc_read default_adc_read num_channels reset sample_time start_adc_read |
| `_pio` | 16 | advance check_changed_pins check_interrupts irq_updated machines notify_due pin_directions pin_directions_changed pin_values pin_values_changed recompute_due reset stop stopped write32 write_atomic |
| `_pwm` | 14 | channels check_interrupts gpio_direction gpio_on_input gpio_read gpio_set gpio_set_dir gpio_value int_enable int_force int_raw int_status reset set_clock_freq |
| `_dma` | 11 | bind_clock channels check_interrupts clear_dreq dreq get_timer int_raw int_status0/1 set_dreq transfer_ns |
| `_usb` | 23 | abort_done_updated buff_status_updated check_interrupts connect_device disconnect_device dpram_updated ep_status_stall_nak finish_read finish_write has_device host_*_microseconds int_raw read_delay_microseconds reset reset_device schedule_read send_setup_packet set_connected sie_status sie_status_updated stall_nak_updated write_delay_microseconds |
| `_clocks` | 24 | add_{peri,ref,rtc,sys}_sink adc_freq bind_clock bind_frequency_listener bind_reference bind_rosc bind_stable_listener bind_xosc clear_sinks clk_peri clk_sys frequency_of peri_freq ref_freq reset reset_tree retune rtc_freq set_crystal_hz sys_freq usb_freq |
| `_pll` | 9 | bind_reference fbdiv frequency locked postdiv1/2 refdiv reference snapshot |
| `_xip_ctrl` | 8 | access aux_window_handler ctr_acc ctr_hit pop_stream_fifo reset write write_access |
| `_sio` | 9 | gpio_output_enable gpio_value qspi_* read_wide reset update_hardware_divider window_handler write32 |
| `_watchdog` | 7 | clk_ref_changed fire_timeout reset tick_hz tick_tap(+ctx) timer |
| `_ppb` | 6 | clk_sys clk_sys_changed reset tick_changed tick_hz timer |
| `_rosc` | 6 | bind_frequency_listener bind_random_source frequency hz read reset |
| `_rtc` | 6 | check_interrupts clk_rtc_changed reset running second_elapsed second_nanos |
| `_timer` | 5 | int_status raw_write_value tick_changed tick_hz write32 |
| the rest (`_io`, `_pads`, `_psm`, `_reset`, `_busctrl`, `_syscfg`, `_sysinfo`, `_tbman`, `_ssi`, `_xosc`, `_vreg_and_chip_reset`, `_gspi`, `_pin_wiring`) | 1-7 each | mostly `reset`, `snapshot`, `wdsel`, the pin images |

`_gpio_pin.pyx` (113 `def`s) and `_cortex_m0_core.pyx` are the Python views of the pin and of the core.

## A first reading of the table (hypotheses; step 0 has to confirm each)

1. **A large share is wiring left over from before the `Mcu` built the blocks in C++** (record 0096, step 3a): `bind_clock`, `bind_reference`, `bind_*_listener`, `add_*_sink`, `clear_sinks`, `retune`, `check_interrupts`, `tick_changed`, `clk_*_changed`, `notify_due`, `recompute_due`, `irq_updated`, `check_changed_pins`. The `Mcu` now wires blocks to each other itself; these calls are the shells doing it a second time, for the Python/`Mock` neighbours that no longer exist in the product (the same finding as the dead `_bound_tree` fallbacks of the border plan's handoff). Candidates for **deletion**.
2. **The world's inputs** are few and are the ones to keep as the list: bytes into a UART (`feed_byte`), the answers of a device on a bus (`complete_*` of SPI/I2C/ADC), a pin driven from outside (`pin_values`, `gpio_*`, `input` of the pin banks, PWM's `gpio_on_input`), the USB host's side (`connect_device`, `send_setup_packet`, `finish_*`, `set_connected`, `reset_device`), the clock source (`set_crystal_hz`), the memory images, and `run` / `request_stop` / `reset` / `set_core1_enabled`.
3. **Observation is `snapshot()` and the registers over the bus** (`RegisterOps`), already; getters such as `int_raw`, `int_status*`, `frequency_of`, `sys_freq`, `sie_status`, `channels`, `machines`, `locked`, `fbdiv` are candidates to be **read through `snapshot()`** and removed as calls.
4. **`reset`** is called on nearly every block by name: the `Mcu` cascade (`Mcu::reset`) already resets them, so the per-block call should be needed only by the oracles' operations (the border plan's "block-level `reset()` in the oracles").

## Where it ends

One list, written next to the 16 slots in [0102](../records/0102-c-abi-for-the-core.md): the **inputs** (what a host may call on the chip, in the same fixed-width form), at the size the inventory gives, probably tens of entries rather than hundreds. After it: the Cython shells call that list and nothing else, and `rp2040_wasm.cpp` exports exactly it ([narrow-the-border-plan.md](narrow-the-border-plan.md), step 8 is the *outputs* of the same module).

## The steps (same order idea as the slots: the leaves first, the chip last)

0. **Inventory and classification (read-only).** For every member in the table, one of: *world input* (stays, goes on the list), *wiring the `Mcu` already does* (delete), *observation* (becomes `snapshot()` / the bus), *internal plumbing between a shell and its own block* (stays inside the shell, never reaches the list). Done as a table in this file, checked by `grep` over `src/`, `tests/`, `scripts/` for every caller outside the shell.
1. **Delete the wiring** the `Mcu` already does (class 1 above), block by block, smallest first (`_timer`, `_rtc`, `_uart`, `_spi`, `_adc`), each with its twin oracle and the C++ tests.
2. **Move observation to `snapshot()`** where a getter is still called from outside.
3. **The clock tree and the PIO / PWM / DMA / USB** (the largest members): the same, with their oracles.
4. **The list itself**, written in 0102; the shells' remaining calls are checked against it (a test that fails when a shell calls a member that is not on the list).
5. **wasm exports** = the list (with the border plan's step 8).

## How each step is checked

As in the border plan: the twin oracles (a pure chip and a C++ chip, `tests/utils/twin.py`) must stay green with the same strength, the C++ tests, `uv run pre-commit run --all-files`, and CI on the push; a step that deletes a call must show that nothing outside the shell used it.

## Open

- Which side owns the "explicit inputs where the bus cannot" (`feed_byte`, `complete_*`, a pin level): these are the list; the question is only their exact signatures, to be fixed once with the 16 slots.
- Whether `_cortex_m0_core.pyx` (the core's registers and stepping) is on this list or a separate, smaller one (a debugger / gdb surface): to decide in step 0.
- The cost of a call across the wasm boundary for the hot ones (pins, UART): to measure when the first exports exist, not before.

## Step 0 - first pass of the inventory and classification (2026-10-26, read-only)

**Method.** For every `_block.<member>` in the 31 shells that use one (223 members, 242 member/wrapper pairs), the shell method that wraps it, and then every other place in the repository that calls a method of that name (`src/` Python, the other shells, `tests/`, the oracles in `tests/utils/`, scripts, boards), plus whether `core/mcu.hpp` already mentions the member. Raw data: a script in the scratchpad of the session, not kept.

**Limit of the method - read this before trusting a count.** The match is by *name*, not by type. `check_interrupts`, `reset`, `read_uint32`, `tick_hz` and the like exist on the pure-Python twin blocks too, so a count of "65 callers in `src/`" mostly counts the twin calling its own methods. A name that has **no** callers anywhere (not in `src/`, other shells, `tests/`, oracles) is therefore solid evidence of dead code; a name with callers is only "possibly used". The deletion experiment of step 1 (take the member out, build, run both suites; the same way `pio_started` was measured on 2026-10-26: three tests, nothing in production) is what turns "possibly" into a fact.

**The classes (members, with how sure the first pass is).**

| class | what | members | sure? |
|---|---|---|---|
| **D - dead** (shell wrapper with no caller by name anywhere) | delete | `PWM.set_clock_freq`; `RTC.second_elapsed`; `Watchdog._fire_timeout` (its public `fire_timeout` wrapper to be checked); `DMA.transfer_ns` and `DMA.bind_clock` (reached only through the `_rejoin_the_python_clock` fallback); `PLL.bind_reference` (`_rejoin_the_python_tree`), `ROSC.bind_frequency_listener`, `XOSC.bind_stable_listener` (same fallback); the `GSPI._bind_sources` and `_pin_wiring` calls (`pin_directions`, `pin_values`, `gpio_*`, `qspi_gpio_*`), which only re-bind what the `Mcu` bound already (the border plan's handoff names them) | high |
| **W - wiring the `Mcu` does itself** | delete with their callers | `Clocks.bind_clock`, `bind_reference`, `bind_xosc`, `bind_rosc`, `bind_stable_listener`, `bind_frequency_listener`, `add_{sys,peri,ref,rtc}_sink`, `clear_sinks`: all called **only** from `RPClocks.rebind_consumers()` (`_clocks.pyx:87,93`, and `_rp2040.pyx:1031` behind a `hasattr`), i.e. from the Python-tree path that no replaced block can reach any more; `Clocks.retune` (via `update`; `mcu.hpp` calls it itself) | high for the `bind_*`/sinks, medium for `retune` |
| **I - a world input** (stays; goes on the list) | keep | `UART.feed_byte`; `SPI.complete_transmit`; `I2C.complete_start/connect/write/read/stop`; `ADC.complete_adc_read` and `start_adc_read`; `DMA.set_dreq` / `clear_dreq`; `PWM.gpio_on_input`; the pin images that the chip *outputs* (`SIO.gpio_value/gpio_output_enable/qspi_*`, `PIO.pin_values/pin_directions`, `PWM.gpio_value/gpio_direction`); the USB host's side (`USB.connect_device`, `disconnect_device`, `send_setup_packet`, `schedule_read`, `finish_read`, `finish_write`, `set_connected`, `reset_device`, `dpram_updated`); `Clocks.set_crystal_hz`; `Mcu.run`, `request_stop`, `set_real_io`, `reset`, `enter_reset_code`, `leave_reset`, `set_core1_enabled`, `set_memory`, `set_flash`, `set_usb_dpram`; the registers, over the bus (`window_handler`, `read`, `write32`, `write_atomic`) | medium: the list's exact shape is for step 4 |
| **O - observation** (becomes `snapshot()` or stays a read-only property) | move | `Clocks.sys_freq/ref_freq/peri_freq/rtc_freq/usb_freq/adc_freq/frequency_of`, `PLL.refdiv/fbdiv/postdiv1/postdiv2/locked/frequency/reference`, `ROSC.frequency/hz`, `I2C.ss_clock_*/fs_clock_*/master_bits/arbitration_lost/control`, `SPI.data_bits/control0/control1/clock_divisor/enabled`, `UART.ctrl/line_ctrl/int_divisor/frac_divisor`, `ADC.num_channels/sample_time`, `USB.sie_status/ep_status_stall_nak/int_raw/has_device/*_microseconds`, `DMA.channels/int_raw/int_status0/1/dreq`, `PWM.channels/int_*`, `Timer.int_status/tick_hz`, `RTC.running/second_nanos`, `PPB.tick_hz/clk_sys`, `XIP.ctr_acc/ctr_hit`; the ones `snapshot()` already reads inside the shell stay inside it | medium: each has callers by name (some of them the shell's own property), the experiment decides |
| **P - plumbing between a shell and its own block** (stays in the shell, never on the list) | keep inside | `check_interrupts` (nine shells; called by the shell's own `check_interrupts` property, from tests and oracles), `update_dreq`, `notify_due`, `recompute_due`, `irq_updated`, `check_changed_pins`, `advance`, `stop`, `*_updated` of the USB, the `*_tap` / `*_tap_ctx` installers of `_rp2040.pyx` | medium |
| **R - `reset` by name** | keep for the oracles, remove from the product | `reset` on every block (26 shells): the `Mcu` cascade resets them; what still calls a block's own `reset()` are the oracles' operations and some tests | medium |

**What this says about the size of the work** (counted by hand from the lists in the table above, so approximate): classes D and W together are about **31** members (D: 8 named plus 4 in `_gspi` and 7 in `_pin_wiring`; W: 12), class I about **43** (the future list - the same order of size as the 16 slots, not hundreds), class O about **58**, class P about **16**, and `reset` on 26 shells (class R). That is about 175 of the 223; the other ~50 are the generic register and `snapshot` entries (`read`, `write32`, `snapshot`, `window_handler`, ...) that are one entry each on the bus or in `snapshot()`, not separate calls to list. So the surface is wide mostly because of *properties that read one field of a block* (class O) and the per-shell `reset` / `check_interrupts`, not because of calls the world needs; `snapshot()` replaces the first on the Python side, and the shells still carry both.

**Consequence for the order.** Step 1 (class D and W: delete) is the cheapest and the safest, and it needs no new design: start with `_clocks` (all of `rebind_consumers` and the `bind_*`), `_dma`, `_pll`, `_rosc`, `_xosc`, `_gspi` / `_pin_wiring`. Class O is the long middle (steps 2-3), class I is the list of step 4.

**Not done in this pass (needs the experiment or a type-aware search):** the per-member confirmation of class O and P (a count by name cannot separate the twin's own calls from the shell's callers); whether `_cortex_m0_core.pyx` and `_gpio_pin.pyx` (the core's and the pin's Python views, 453 and 493 lines) are on the list or a smaller debugger surface; the exact signatures of class I.

## Step 1a - done (2026-10-26): the dead part of classes D and W

Removed from the shells (the C++ members stay where the C++ tests use them): `RPPWM.set_clock_freq`, `RPRTC.second_elapsed`; in the DMA, both PLLs, the ROSC and the XOSC shells the "clock tree is going away" fallback - `_rejoin_the_python_clock` / `_rejoin_the_python_tree`, `_leave_the_python_clock`, `_bound_tree` (in the `.pxd` files too), `DMA._clk_sys_changed`, and the four Python trampolines those fallbacks re-installed (`_clk_sys_trampoline`, `_ref_trampoline`, `_frequency_trampoline`, `_stable_trampoline`). Evidence: no caller of any of them in `src/`, `tests/`, the oracles or the scripts (the `_rejoin_*` had no caller at all). `RPWatchdog.fire_timeout` stays: `tests/test_watchdog_reset.py` calls it. Both suites and the oracles (smoke) pass.

**A correction to the first pass.** `Clocks.rebind_consumers()` and its `bind_*` / `add_*_sink` / `clear_sinks` are **not** all duplicated wiring, and are not deleted by this step. The `Mcu` already registers the same C++ sinks (`bind_tree_sinks()`: SysTick of both cores, PWM, DMA, the watchdog's tick, the RTC) and binds the oscillators and the PLLs' reference (`mcu.hpp`, `build()`), but `rebind_consumers()` **clears that list and registers it again with three Python sinks interleaved**: `_sys_sink` (the chip's `clk_sys` attribute), `_listeners_sink` (the host's clock listeners) and `_peri_sink` (the chip's `clk_peri` and the UARTs' `clk_peri_changed`). Those three are host-visible state kept in Python, and they run in a fixed order relative to the C++ sinks (the reference's `update_clocks()` order, which `clocks_diff` checks). Deleting the rebind means giving those three consumers another home first - the chip's `clk_sys` / `clk_peri` read off the tree on demand, the listeners on `clock_tree_tap`, the UARTs told from the C++ side - which is a design step of its own and moves to step 3 (the clock tree), not step 1. The same goes for the `isinstance(xosc, RPXOSC)` branches and the `set_crystal_hz` call there (the crystal is the board's parameter, set by the shell).

## Step 1b - done (2026-10-26): the pin sources are bound by the `Mcu` only

Removed: `RP2040._bind_pin_sources()` and its two calls (the chip's build and `_adopt_sio`), the whole module `native/_pin_wiring` (`.pyx`, `.pyi`), and `GspiShifter._bind_sources` with the keep-alive references it added. `Mcu::build()` binds the sources of the pin banks (SIO, both PIOs, the PWM) for every board; the chip's blocks can no longer be replaced, so there is nothing to re-bind, and the gSPI shifter - an external device of the Pico W only - had no business re-wiring a chip's pins; it keeps what is the device's own, the direct listener on CLK and CS. Checked: both suites and the oracles (smoke) pass, and the Pico W Wi-Fi live boot - MicroPython 1.29.0 and 1.23.0 with `--board pico_w tests/micropython/main-cyw43-core.py` (scan, DHCP lease, disconnect: `MICROPYTHON CYW43 CORE OK`).

## Step 2a - done (2026-10-26): the first observation getters leave the native shells

The rule used (the three tiers of [0096](../records/0096-cpp-mcu-core.md)): an attribute-per-register view of a block is tier 3, free to differ between the backends; what a test or an oracle needs to *see* comes out of `snapshot()` on both chips; the pure-Python block keeps its own helpers. So for each getter: (a) is it read by the native shell itself (then it stays, as internal plumbing - `RPUART.baud_rate` / `baud_divider`, which the baud-rate announcement uses)? (b) if it is read only by tests and oracles, put the value in `snapshot()` on both chips if it is not there, switch the readers, delete the native getter.

Done: **RTC** (`running`, `second_nanos` - `second_nanos` is a new `snapshot()` entry, 0.0 while the counter is stopped, on both chips; `clocks_diff` reads both from the snapshot), **TIMER** (`int_status`, `tick_hz` - the TIMER had no `snapshot()` because its registers are all on the bus; it now has one with exactly these two values, which the registers do not show; `test_timer_parity`, `watchdog_diff`, `test_mcu_rtc` read it), **UART** (`enabled`, `tx_enabled`, `rx_enabled`, `fifos_enabled`, `word_length`: no reader but a test of the pure block, which reads `line_ctrl` from the snapshot now). The oracles run with `--oracle=full` for the RTC, TIMER, watchdog and clocks and `smoke` for the rest; both suites pass.

**What the first three blocks say about the rest of class O.** About half of the getters had a `snapshot()` entry already (`running`), the others need one new entry each (`second_nanos`, the TIMER pair) - one line in the pure block and one in the shell, compared by the existing snapshot oracles for free. A getter that the shell itself uses stays. Each block is a small separate change; the big ones (clock tree, PWM, DMA, USB, I2C, SPI) come next.

**Step 2a, the pure side (same day, the maintainer's decision "hide the getters of the pure Python too").** The getters that left the native shells are private on the pure-Python blocks as well (`_running`, `_second_nanos`, `_int_status`, `_enabled`, `_tx_enabled`, `_rx_enabled`, `_fifos_enabled`, `_word_length`; the TIMER's `tick_hz` property is dropped, its `_tick_hz` was the attribute behind it), so both chips show the same public surface for these three blocks: the bus, `snapshot()`, and what the shell itself uses. The oracles' mutants that overrode a getter (`rtc_diff`'s `running_ignores_clock` / `running_ignores_enable` / the `second_nanos` ones, `uart_diff`'s `self.enabled`...) were renamed with them; the first run caught that by failing - two mutants no longer changed anything - which is the oracle doing its job. Full oracle run: 3,536 tests.

## Step 2b - done (2026-10-26): PPB, watchdog, PLL and SPI getters, and the rules that fell out

Removed from the native shells (readers moved to `snapshot()`, which already had the values): `RPPPB.tick_hz`, `RPPPB.clk_sys`, `RPWatchdog.tick_hz`; `RPPLL.refdiv`, `fbdiv`, `postdiv1`, `postdiv2`, `reference`, `locked` (nobody read them but the oracle's mutants of the pure block); `RPSPI.enabled`, `RPSPI.master_mode` (`test_spi` reads the snapshot). Full oracle run: 3,536 tests, both suites.

**What stays, and why (the rules this block-by-block pass settled on):**
1. A getter **the shell itself uses** stays (the UART's `baud_rate` / `baud_divider`, the SPI's `spi_mode` / `clock_frequency` inside its snapshot).
2. A getter **a device or the product reads** stays: it is the device-facing contract (tier 1 of the 0096 note) - the SPI's `data_bits` and `clock_frequency` (the ST7735S and the e-paper display time their transfers by them), the I2C's `speed`, `scl_low_period`, `scl_high_period`, `master_bits` (the shell's own comment: "what a device on the bus reads of the configuration"), `frequency` of the PLLs and the ROSC (the clock tree and the oracles read it).
3. A **settable parameter** is an input, not an observation: the ADC's `num_channels` and `sample_time`.
4. **Plain attributes** of the pure blocks (`ppb.tick_hz`, `ppb.clk_sys`, `watchdog.tick_hz`) are not renamed: they are state, not getters, and the oracles' mutants patch the pure source by text; only the native shells lose the views. The pure getters that were properties (RTC, TIMER, UART) were made private in step 2a. Whether the pure PLL's and SPI's getters (`refdiv`... `enabled`, `master_mode`) are made private too is the same mechanical pass as 2a and is still to do.

**Step 2b, the pure side (same day).** Done at once, not left for later: the pure PLL's `refdiv`, `fbdiv`, `postdiv1`, `postdiv2`, `reference`, `locked` and the pure SPI's `enabled`, `master_mode` are private (`_refdiv`, ..., `_master_mode`), and the oracles' mutants that overrode them (`clocks_diff`'s `MutantPLL`, `spi_diff`'s `master_mode_inverted`) were renamed with them. The full oracle run (3,536 tests) is unchanged, so the mutants are still caught. The rule from here on: a getter leaves the native shell and goes private on the pure block in the same commit.

## Step 2c - done (2026-10-27): ADC (nothing to remove) and PWM

**ADC**: `num_channels` and `sample_time` are settable parameters, `start_adc_read` / `complete_adc_read` / `default_adc_read` are the world's inputs; there is no observation getter to remove. **PWM**: the native shell's `gpio_set`, `gpio_set_dir`, `gpio_read` and `clock_freq` had no caller in `src/`, `tests/`, the oracles or the scripts (the pure channels call the pure block's methods, and only the pure clock tree reads `clock_freq`), so they are removed, with their `.pxd` declarations; on the pure block the three methods are private (`_gpio_set`, `_gpio_set_dir`, `_gpio_read`; the one mutant that patches a call by text, `b_value_and`, was updated). Kept: `gpio_value` / `gpio_direction` (pin images: the pins read them, the tests and the pin oracle drive them) and `gpio_on_input`. Full oracle run: 3,536 tests.

## Step 2d - done (2026-10-27): DMA

Removed from the native shell: the `_Channel` view class and `RPDMA.channels` (about 40 lines; the only readers were `test_dma.py`, which now asks the snapshot - `cpu.dma.snapshot()["channels"][i][-1]` is `active` - and the oracle's own subclasses), and `RPDMA.get_timer` (its callers were the pure channel and the oracle's pure subclass). On the pure block `channels` / `get_timer` are `_channels` / `_get_timer`; the oracle's `Controller` / `Channel` subclasses follow. Kept: `set_dreq` / `clear_dreq` (the other blocks' requests, class I), `snapshot`, `check_interrupts`. Full oracle run: 3,536 tests.

## Step 2e - done (2026-10-27): the clock tree; and what is left of class O

**Clock tree.** The six derived frequencies were the one observation that was *not* in a snapshot (the snapshot had only the CTRL and DIV words); they are entries now (`ref_freq`, `sys_freq`, `peri_freq`, `rtc_freq`, `usb_freq`, `adc_freq`), computed by the same call on both chips; the native shell lost those six getters, `frequency_of()`, `clk_sys` and `clk_peri` (no reader); the pure block's six getters and `frequency_of()` are private, which also covers the module-level `update_clocks()` of the pure tree (it reads `rp2040.clocks._sys_freq` ...). The oracle reads them from the snapshot and its mutants of those getters were renamed; four tests that asserted a frequency (`test_clocks_xosc_pll`, `test_rosc`, `test_rtc_datasheet`) read the snapshot. Full oracle run: 3,536 tests. `Clocks.rebind_consumers()` and the three Python sinks are untouched, as written under step 1a.

**USB** needs no removal: the delays and `connected_device` are settable parameters (class I), `connect_device` / `disconnect_device` / `reset_device` / `send_setup_packet` / `endpoint_read_done` are the host side that `USBCDC`, the CLI and the tests drive (tier 1), `raise_sie_status` / `raise_stall_nak` / `raise_interrupt` are test inputs, and `_sie_status_updated` and the like are the plumbing between the shell and its block. **I2C**: nothing, per rule 2 (device-facing configuration).

**What is left of class O after this:** the `check_interrupts` of nine shells and `reset` of 26 (class P / R: the next question is whether the oracles can do without them), the chip-level surface (`_rp2040.pyx`: the pin views of `_gpio_pin.pyx`, the core's view `_cortex_m0_core.pyx`), and the three Python clock sinks (step 3).

## Step 3 - the clock tree's Python sinks: the approach (agreed 2026-10-27: "if approach 1 breaks nothing - ok") and 3a

**Approach.** `rebind_consumers()` clears the sink list the `Mcu` registered and registers it again with three Python sinks interleaved: `_sys_sink` (first: writes `chip.clk_sys`, raises the batch-end flag), `_listeners_sink` (last: the hosts' `add_clock_listener` callbacks, after SysTick, the PWM counters and the DMA have followed) and `_peri_sink` (writes `chip.clk_peri`, tells every UART `clk_peri_changed()`). The plan, in four commits each with the full oracle run and CI: **3a** `chip.clk_sys` becomes a property over the tree (as `clk_peri` already is); **3b** the listeners ride a new observer port of `ClocksBlock` that fires *after* the sinks (the existing `clock_tree_tap` fires before them, so it cannot carry the pure tree's order); **3c** the UART's re-announcement of its baud rate moves into C++ (the UART becomes a `clk_peri` consumer and calls the `uart_baud` slot when its divider is not zero); **3d** `rebind_consumers()`, the shell's `_consumers`, its direct `clocks_retune*` listeners and `_rebind_clock_sinks` go, and the board's crystal (`chip.xosc_freq`) is handed to the tree when the chip is built. `add_clock_listener` stays public.

**3a - done.** `ClocksBlock::force_clk_sys()` (the twin of `force_clk_peri()`), `RP2040.clk_sys` as a property with a setter over `Mcu::clocks()`, and `_sys_sink` no longer writes the attribute (it still raises `_sys_dirty`, which 3d hands to the C++ sink). The tests that assign `chip.clk_sys = X` (`test_simulator`, `test_batch_parity`, `ppb_diff`) are unchanged and pass. Full oracle run: 3,536 tests.

**3b - done (2026-10-27).** `ClocksBlock::after_tap` / `after_tap_ctx` (same signature as `tap`, called for all four domains once their sinks have all followed; `tap` still comes first and a failing observer parks the host's error as before). `RP2040.add_clock_listener()` keeps its contract; it sets `after_tap` to `_clock_listeners_hook` while the listener set is not empty (and back to NULL when the last one unsubscribes), and the hook calls the listeners for the `sys` domain. `_listeners_sink` and its registration in `rebind_consumers()` are removed. A directed C++ check (`test_the_after_tap_is_called_last_with_the_same_arguments`: tap, both sinks in order, then after_tap, same arguments; nothing called once it is NULL). Full oracle run: 3,536 tests, `clocks_diff` included.

