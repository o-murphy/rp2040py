# Plan: narrowing the VM / world border (`McuHost`) once the oracles stand on twin chips

Working note, 2026-10-26. **A plan, not an authorisation: every step needs its own go-ahead.** It follows from [vm-world-border.md](vm-world-border.md) (the line, the three groups, the answers read from
the code) and [twin-backend-testing.md](twin-backend-testing.md) (the oracles compare a pure chip and a C++ chip; 17 are moved, `chip.dreq_tap` exists). Goal: `McuHost` goes from 28 slots plus the USB
struct to the slots a host that only attaches devices needs, the same list for the Cython shell, wasm and any other binding.

## Where it ends

| Slot | Fate |
|---|---|
| `warn`, `uart_byte`, `uart_baud`, `spi_transmit`, `i2c_start/connect/write/read/stop`, `adc_read`, `adc_channel_value`, `pin_changed`, `usb_wire`, `usb_reset`, `bus_reset` | **stay** (the world, 15 slots) |
| `system_reset_request` + `watchdog_trigger` | **merge** into one slot, `reset_requested(cause)`; the default runs the cascade in the core; `BaseDevice` installs its own (USB device + console side effects) |
| `pin_source`, `pin_inputs`, `pin_update`, `pin_input_changed`, `xosc_hz`, `rosc_hz`, `clk_sys`, `ssi_baudr`, `reset_block`, `record_cause`, `watchdog_tick` | **delete** (block swapping; 11 slots) |
| `pio_started` | **deleted (2026-10-26)**: not a world slot but a request to the host to run a fallback; a start by CTRL now only marks the block as running, and whoever advances the clock steps it |

Result: 16 slots (15 + `reset_requested`), down from 28; `pio_started` is deleted too.

## What the swapping looks like in the code (what gets deleted)

- C++ (`core/mcu.hpp`): the flags `pins_world_[2]`, `pwm_world_`, `ssi_world_`, `pio_world_[2]` (set by `set_pin_bank_world`, `set_pwm_world`, `set_ssi_world`, `set_pio_world`); the `world` mask argument of `reset()` /
  `enter_reset()` and `reset_step()`'s host branch; `set_tree_hook` and the host-clock branches of the tree; `attach_dma` / `set_dreq_hook`; the branches in the pin, PWM, PIO and SSI adapters that
  ask the host instead of the C++ block.
- Cython (`native/_rp2040.pyx`): the property setters that re-point the core at a replacement - `pwm`, `pio`, `sio`, `ppb`, `watchdog`, `clocks`, `xosc`, `rosc`, `rtc`, `ssi`, `dma`, `_gpio_bank`,
  `_qspi_bank`, `clk_peri`, `xosc_freq`, `rosc_freq` (about 16 of the 20 setters in the file) and the `_w_*` trampolines of the deleted slots.
- **Not** deleted: `chip.peripherals[address] = block` for an address the chip does not use (a user's own register block: `attach_peripheral` of tier 1), `chip.set_interrupt =` (the IRQ tap, tier 2),
  `chip.dreq_tap`, the device-side callbacks.

## What still swaps blocks in `tests/` (must go first, measured 2026-10-26)

| File | What it does | Fate |
|---|---|---|
| `test_pin_bank.py` (`test_replacing_a_block_re_points_the_pins_...`) | stand-ins for `pio`, `pwm` to see that the bank re-points | **delete** with the seam |
| `test_dreq_direct.py` | assigns recorders / a failing DMA to test the DREQ hook seam | **reshape** to `dreq_tap` (observer, failure parking); the replace-the-DMA cases go |
| `test_adc.py`, `test_uart.py`, `test_ssi.py`, `test_pio_fjoin.py` | `chip.dma = <recorder>` | **convert** to `dreq_tap` (as the six oracles were) |
| `test_sio_bus.py` | puts a pure or a native SIO into one chip | **move** to a twin parity over the bus |
| `test_dma_time.py` | pure PLLs and clocks in a chip | **move** to a twin (or to the pure chip, which has them) |
| `test_bus_host.py` | a Python `Ppb` stand-in behind the C++ bus: a test of the core's host contract ("the block cannot tell") | **keep as a core-level test** (C++ `tests/cpp` or `Mcu` driven directly), not through the chip's setters |
| `utils/mmio_trace.py`, `utils/pin_trace.py` | record every SIO / PPB access by standing a recorder in for the block | **rebuild on a trace port** (tier 2: one tap on the bus window, same on both chips) or restrict to the pure chip - decide when the first user needs it |
| `test_mcu_rtc.py`, `test_timer_parity.py`, `test_batch_parity.py`, `test_peripheral_windows.py`, `test_rp2040.py`, `utils/*_diff.py` (mutants) | `peripherals[...] = ...` | each to be told apart: a user block at a free address (**stays**), a mutant inside the pure chip (**stays**), a replacement of a built-in on the C++ chip (**goes**) |

## Inventory (step 0, done 2026-10-26): every `peripherals[...] = ...` and block assignment outside the two chip files

**Replacing a built-in block on the C++ chip - goes with step 2-6** (each file named with the step that frees it):
- `test_mcu_rtc.py:67` (a stand-in RTC at 0x4005C, to see the C++ chip hand work to a Python RTC) - step 4/5: becomes a test of the *core's host contract* or is deleted with the RTC seam;
- `test_batch_parity.py:159` (`Angry()` at the TIMER's address, a failing peripheral inside the batch loop) - **keep the intent, change the address**: a failing block at a free address that the program touches tests the same
  failure parking without replacing a built-in;
- `test_timer_parity.py:55,214,248` (a pure or native TIMER swapped into a chip, and a wrapper of it) - step 4: a twin parity (both chips' own TIMER) replaces it; `tick_changed` is the one input it needs and stays;
- `test_peripheral_windows.py:109,113,130` (`TIMER_KEY` swapped for a stand-in and back) - the other 15 uses are a block at a **free** address (`FREE_KEY`, `OTHER_KEY`), which is `attach_peripheral` and **stays**; the three TIMER ones go;
- `test_dma_time.py:55-60` (pure PLLs and clocks built into a chip) - step 4;
- `test_sio_bus.py:104,119`, `test_bus_host.py:77,100` (`chip.sio` / `chip.ppb` swapped) - step 0 verdict: these test the *bus-to-host contract* of the C++ core; move them to a core-level test that drives `Mcu` directly (or `tests/cpp`), not through the chip setters;
- the oracles' own `utils/*_diff.py` lines (`chip.peripherals[... >> 12] = new`) are **mutants inside the pure chip** and stay.

**Recorders standing in for the DMA - step 1, done 2026-10-26:** `test_adc.py`, `test_uart.py`, `test_ssi.py`, `test_pio_fjoin.py` now observe through `chip.dreq_tap`; `test_dreq_direct.py` was reshaped (the replace-the-DMA and give-it-back cases are gone, the observer cases - sees the request, sees the clear, can be taken away, a failing one surfaces - are in). 65 tests green on both builds. The only `chip.dma = ...` left in `tests/` is `dma_diff.py`'s own DMA under test (a mutant inside the pure chip) and `pwm_cpp_vectors.py`'s stand-in chip.

**Tracing tools - decide at step 3/4:** `utils/mmio_trace.py:154-158`, `utils/pin_trace.py:153-154` put a recorder in front of a block (SIO, PPB, any `peripherals[key]`); users: `test_mmio_trace.py`, `test_pin_trace.py`. They are "a tap on the bus window", which the core can offer once.

**Stays:** user blocks at free addresses (`attach_peripheral`), `chip.set_interrupt = ...` (the IRQ tap; 10 test files), `chip.dreq_tap`, every mutant built into a pure chip.

## The steps (leaf first; the reset last)

0. *(done)* Inventory above.
1. *(done)* **Convert the recorders** (`test_adc`, `test_uart`, `test_ssi`, `test_pio_fjoin`, `test_dreq_direct`) to `dreq_tap`. *Gate: those files green on both builds.*
2. *(SSI done 2026-10-26)* **The SSI "world" flag**: `set_ssi_world`, `ssi_world_`, the `ssi_baudr` slot and its trampoline, the `ssi` setter's re-pointing are deleted (the SSI is the `Mcu`'s own; `chip.ssi` is read-only; `McuHost` is 27 slots). Gate met: ssi, xip, chip oracles with all mutants (262 tests), `test_chip_reset`, both builds green. **PWM and PIO flags (`set_pwm_world`, `set_pio_world`, the `pwm` / `pio` setters' re-pointing, `pin_input_changed`) moved to step 3**: their last users are the pin oracle's `chip.pio` / `chip.pwm` probes and `test_pin_bank`, so they can only go together with the pin banks' world.
3. *(first half done 2026-10-26: the PWM / PIO flags)* **The trial** with real PIO and PWM in the pin oracle first failed 6 of 118 tests (`pwm_not_told`, `pwm_told_always`, `pad_changed_always`, `own_drive_change_only_moves_the_seen_level`, the `drive_a_pin` perturbation and the coverage test): the probe also *recorded* every call a pin made to `gpio_on_input`, which a real PWM does not show. The fix is an observer, `chip.pwm_input_tap` (on both chips, the `dreq_tap` pattern: `PwmBlock::input_tap` in C++, the same call in the Python `RPPWM`); with it the pin oracle runs on the **real PIO and PWM** and all 118 tests pass, mutants and perturbations as before. Deleted: `set_pwm_world`, `set_pio_world`, `pwm_world_`, `pio_world_`, the `pin_input_changed` slot and its trampoline, the host branches of `pin_input_changed` (`McuHost`: 26 slots). **Still to do in this step:** `test_pin_bank`'s stand-in test and the `pwm` / `pio` setters' re-pointing (nothing sets them any more), then the pin banks' world below.

   *(second half done 2026-10-26)* Deleted: `test_pin_bank`'s stand-in test; the `pwm` and `pio` setters of the C++ chip (both are read-only now; `_PinSourceList` is gone) and the `_gpio_bank` / `_qspi_bank` setters; in C++ `set_pin_bank_world`, `pins_world_`, the `pin_inputs` and `pin_update` slots with their trampolines and every host branch they guarded (SIO's inputs and updates, the PIO pin binding, the reset of a pin bank) - `McuHost` is **24 slots**. Full pre-commit green on both builds. **`pin_source` stays for now**: SIO can still be swapped (`test_sio_bus`, `utils/pin_trace.py`, `utils/mmio_trace.py` assign `chip.sio` / `chip.ppb`); it goes with the SIO setter, after those three are moved (the table above), as step 3b. **3b, done 2026-10-26 (first part):** `test_sio_bus` compares two whole chips (the wrapper test is gone, the raising-pin test uses a real listener), `test_bus_host`'s two replaced-PPB tests are gone (a Python block behind a window is `test_peripheral_windows`), `test_mmio_trace` / `test_pin_trace` record on the pure chip and replay on the C++ one (twin parity: it agreed at once), and the C++ chip's `sio` is read-only. `ppb` stays assignable until step 4 (the clocks oracle spies on what the tree tells the PPB). **3b finished (same day):** the `pin_source` slot, its C++ adapter and Python trampoline are deleted (the `Mcu` binds every pin source - SIO, both PIOs, the PWM - to the blocks' own fields itself, so the host fallback could not be reached); `test_pin_bank`'s "unwire and rewire the sources" test, which tested exactly that fallback, went with it. `McuHost` is **23 slots**. `RP2040._bind_pin_sources` and `_pin_wiring.bind_pin_sources` now only re-bind what the `Mcu` bound already - a cleanup left for later.

   **Pin banks (original text)**: delete `set_pin_bank_world`, `pin_source`, `pin_inputs`, `pin_update`, `pin_input_changed`, the `_gpio_bank` / `_qspi_bank` setters; `pin_changed` stays. `test_pin_bank` swap test goes; the pin oracle's `chip.pio` / `chip.pwm` probes (symmetric neighbour doubles, assigned on the C++ chip) need a replacement first - **either real PIO/PWM there, or the probes only on the pure chip's side of a pin-bank unit test** (to decide at this step). *Gate: pin oracle; boot CI; the CYW43 scan (the pin hot path).*
4. *(done 2026-10-26)* **Clock tree**: a new observer, `chip.clock_tree_tap(domain, hz, old)` (domain "sys", "peri", "ref", "rtc"; `ClocksBlock::tap` in C++, `_tap` calls in the pure `update_clocks`), replaced the `_Spy` proxies of `clocks_diff`; the oracle is **stronger** for it (it used to drop the calls of the C++ consumers - SysTick, the watchdog, the RTC - from its log because there was no Python call to spy on; now every announcement is compared). Deleted: `set_tree_hook`, `TreeHook`, the `xosc_hz`, `rosc_hz` and `clk_sys` slots with their trampolines, `_native_tree`, the setters of `watchdog`, `rtc`, `ppb`, `clocks`, `xosc`, `rosc` on the C++ chip (read-only now; `xosc_freq`, `rosc_freq`, `clk_peri` stay: they are values, not blocks), `RPClocks.release_consumers`, the four Python fallback sinks and the `isinstance` branches of `rebind_consumers`; `test_dma_time`'s replaced-tree test. `McuHost` is **20 slots**. Full suite green with the oracles in full. **Left for a cleanup pass:** the `_bound_tree` / `_rejoin_the_python_tree` / `_leave_the_python_clock` fallbacks in the XOSC, ROSC, PLL and DMA shells (Python paths for a block that is not bound to the native tree - none exists any more).
5. *(done 2026-10-26, except the core's own default)* **Reset cascade**: deleted the `world` mask and every host branch of `Mcu::reset` / `enter_reset` / `reset_step` (the two WDSEL words are the `Mcu`'s own PSM and RESETS now), the `reset_block` and `record_cause` slots and trampolines, `RP2040._reset_world_mask` / `_reset_block_python` / `_RESET_BLOCKS`, and the `watchdog_tick` slot; the tick listeners (`watchdog.add_tick_listener`) are now served by `WatchdogBlock::tick_tap` (installed only while a listener exists), the third observer of this kind after `dreq_tap` and `clock_tree_tap`. `system_reset_request` + `watchdog_trigger` are **one slot, `reset_requested(cause)`** (0: SYSRESETREQ, 1: the watchdog); the Python side keeps `ppb.on_system_reset_request` and `watchdog.on_watchdog_trigger` as the hooks the slot calls, so `BaseDevice`'s handler is untouched. `McuHost` is **17 slots** (the 16 of the end list plus `pio_started`). Full suite green with the oracles in full; the C++ tests of `Mcu` follow the new shape. **Not done:** the default of `reset_requested` running the cascade in the core without the Python hop (today the default is still the Python `_default_watchdog_trigger`); it needs C++ to know whether a handler is installed, and is a separate small step.
6. *(done 2026-10-26)* **DMA hook**: deleted `Mcu::set_dreq_hook`, `DreqHook`, `attach_dma` (the requests go to the chip's own DMA block, always), `_dreq_hook` and the `dma` setter of the C++ chip (read-only now); the C++ test of `Mcu` checks the observer (`DmaBlock::dreq_tap`) where it used to check the hook. Full suite green with the oracles in full. `McuHost` stays at 17 slots (these hooks were on `Mcu`, not on `McuHost`).
7. **The final `McuHost`**: the 16 slots written down, [0102](../records/0102-c-abi-for-the-core.md)'s C ABI sketch updated to exactly them, `core-block-recipe.md` and the record's progress log, a CHANGELOG entry (the chip's block setters are gone: a **breaking change** for code that replaced a built-in block).
8. **Only then wasm**: extend `rp2040_wasm.cpp` to the 16 slots (the SPI, I2C, ADC and pin ones are what it lacks), with the same twin oracle as the check where the module's behaviour can be driven.

## How each step is checked

`uv run pre-commit run --all-files` (both builds, the oracles in full); the seven CI workflows; the live boots (MicroPython, CircuitPython, Kaluma, Pico SDK) that CI already runs; the C++ tests in `tests/cpp`
for the touched block; for steps 3-5 an A/B of CoreMark and the CYW43 scan against the commit before (the host branches are on hot paths: removing them should not cost, and may gain). One step is
one commit, and a step that makes any mutant or perturbation of any twin oracle escape is not done.

## Open

- **`pio_started`**: with no owning `Simulator`, a CTRL write starts an asyncio task from inside the register write. Proposal: the owner is asked once, at attach time ("who steps the PIO?"), not per CTRL write;
  the batch loop is the owner when there is a `Simulator`. To design before step 5, as it is the one slot that is not obviously a world event.
- **Trace port for `mmio_trace` / `pin_trace`** (step 3/4): a single tap on the bus window is the neutral form; is it worth a public name, or are those tools pure-chip-only?
- **Breaking change policy**: the block setters are public in practice (`chip.dma = ...` is documented in the 1.0.0b1 CHANGELOG as a feature). Removing the replace-a-built-in part needs a CHANGELOG line and,
  given the project is in a beta line, probably no deprecation cycle - to confirm.
- **`attach_peripheral`** (tier-1 requirement 3) is the supported form of "my own register block"; today it is `peripherals[address] = block`. Naming it is a separate, small step that can come before step 7.

## Where it stands (2026-10-26, after step 7 (documents) - the handoff below was written after step 6; the handoff for the next session)

Steps 0-6 are done and pushed on `refactor/native-shell-boilerplate` (last commit `64b7f50`; CI green on every commit up to and including step 5 `75a458a`, step 6 was still running when this was written - check it first). `McuHost` is **17 slots**: the 16 of the end list in "Where it ends" (`warn`, `uart_byte`, `uart_baud`, `spi_transmit`, `i2c_start/connect/write/read/stop`, `adc_read`, `adc_channel_value`, `pin_changed`, `usb_wire`, `usb_reset`, `bus_reset`, and the merged `reset_requested`) plus `pio_started`.

**Next, in order:**
1. Check CI on `64b7f50`.
2. **Step 7 - the final `McuHost`:** write the list down (the 16, and what `pio_started` becomes), update the C ABI sketch of [0102](../records/0102-c-abi-for-the-core.md) to exactly that list, add a progress-log entry in [0096](../records/0096-cpp-mcu-core.md), the tracker row, `core-block-recipe.md` (no host seams for replacing blocks), and a CHANGELOG line that names the whole set of removed setters as one breaking change.
3. **`pio_started`:** the "no owning `Simulator`" fallback (an asyncio task started from inside a CTRL register write). Proposal in "Open" above: ask the owner once, at attach time. To be designed and agreed before it is touched.
4. **Step 8 - wasm** to the 16 slots (what `rp2040_wasm.cpp` lacks: SPI, I2C, ADC, pin events), with the twin oracle as the check where the module can be driven.

**Small cleanups left on the way (none blocks a step):**
- the `_bound_tree` / `_rejoin_the_python_tree` / `_leave_the_python_clock` fallbacks in the XOSC, ROSC, PLL and DMA shells, and the `isinstance(dma, RPDMA)` branch in `RPClocks.rebind_consumers` (no replaced block exists any more);
- `RP2040._bind_pin_sources` and `_pin_wiring.bind_pin_sources`, which only re-bind what the `Mcu` bound already;
- the default of `reset_requested` (`_default_watchdog_trigger`) still runs in Python; running the cascade in the core needs C++ to know whether a handler is installed;
- the operations of the twin oracles that still call a block (`peripheral.reset()`) and the neighbour doubles that remain (the DMA oracle's bus probe); see "Migration status" in [twin-backend-testing.md](twin-backend-testing.md).

**Not started, and waiting for the maintainer:** opening the pull request from `refactor/native-shell-boilerplate` into `main` (the branch has been rebased onto the merged PR #66 by the maintainer; the series is large: ~25 blocks on `snapshot()`, the shared `NativeBlock`, the border plan steps 0-6).

**Observers added on this road** (all on both chips, all leave the consumer working): `chip.dreq_tap`, `chip.pwm_input_tap`, `chip.clock_tree_tap`, `watchdog.add_tick_listener` (on `WatchdogBlock::tick_tap`), next to the existing `chip.set_interrupt` assignment.

## Update (2026-10-26, after step 7): `pio_started` is deleted - `McuHost` is 16 slots

Measured first: with the fallback switched off on both backends, only three tests of `tests/test_pio.py` failed (a state machine started by CTRL, its address read at once); nothing in production depended on it (a `Simulator` steps the running PIOs itself). Removed: `McuHost::pio_started`, `PioHost::started`, `RPPIO._world_started`, `RPPIO.run()`, `RPPIO._step_batch()`, `RPPIO._run_task` on both backends; the three tests step PIO0 with `run_pio(cpu)`. The "Open" and "Next" items about `pio_started` above are therefore closed; what is left is **step 8 (wasm to the 16 slots)** and the small cleanups.
