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
| `pio_started` | **open**: the "no owning `Simulator`" fallback is a host concern called from inside a CTRL register write; it should leave the write (see "Open") |

Result: 16 slots (15 + `reset_requested`), down from 28, plus `pio_started` if it survives.

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
2. **PWM / PIO / SSI "world" flags**: delete `set_pwm_world`, `set_pio_world`, `set_ssi_world`, the host branches they guard, `ssi_baudr`, and the `pwm` / `pio` / `ssi` setters' re-pointing. *Gate: the pwm, pio, ssi twin oracles with all mutants; boot CI.*
3. **Pin banks**: delete `set_pin_bank_world`, `pin_source`, `pin_inputs`, `pin_update`, `pin_input_changed`, the `_gpio_bank` / `_qspi_bank` setters; `pin_changed` stays. `test_pin_bank` swap test goes; the pin oracle's `chip.pio` / `chip.pwm` probes (symmetric neighbour doubles, assigned on the C++ chip) need a replacement first - **either real PIO/PWM there, or the probes only on the pure chip's side of a pin-bank unit test** (to decide at this step). *Gate: pin oracle; boot CI; the CYW43 scan (the pin hot path).*
4. **Clock tree**: delete `set_tree_hook`, `xosc_hz`, `rosc_hz`, `clk_sys`, the `xosc` / `rosc` / `clocks` / `clk_peri` / `xosc_freq` / `rosc_freq` re-pointing, `bind_tree_sinks` host branches. *Gate: clocks, rosc, psm_xosc_resets oracles; `test_clocks*`; boot CI.*
5. **Reset cascade**: delete the `world` mask, `reset_step`'s host branch, `reset_block`, `record_cause`, `watchdog_tick`; then merge `system_reset_request` + `watchdog_trigger` into `reset_requested(cause)` with the core's default and `BaseDevice`'s handler (keep the `on_watchdog_trigger` / `on_system_reset_request` hook names on the Python side as thin wrappers for one release). *Gate: `chip_diff`, `test_chip_reset`, `test_watchdog_reset`, `test_ppb`, the live `machine.reset()` / `bootloader()` path of mpremote.*
6. **DMA hook**: with no recorders left, delete `set_dreq_hook`, `attach_dma` indirection and the `dma` setter's branch. *Gate: dma, adc, spi, uart, pio oracles; boot CI.*
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
