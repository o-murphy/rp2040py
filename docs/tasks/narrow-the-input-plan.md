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

