# The border between the VM and the world: where it is crossed today, where it should be

Working note, 2026-10-25. **Documentation only - nothing here is built, and each step needs its own go-ahead** (CLAUDE.md, "Document" vs. "implement"). It follows from the
snapshot series closing ([0096](../records/0096-cpp-mcu-core.md), progress log 2026-10-25) and sharpens two things from 0096's "three tiers" note: *the emulator is a sandbox with a cheap, narrow
border*, and *one* interface for GPIO / external devices. It is the input to the C ABI of [0102](../records/0102-c-abi-for-the-core.md): whatever is in this border is what a wasm host, a cffi binding and
the Cython shell all have to provide.

## The principle (stated by the maintainer, 2026-10-26)

The C++ core is **the** implementation; every host - Cython, wasm (JS), Dart, Rust, ... - is a thin binding that provides the same border, and **external devices are written in the host's own
language**. The pure-Python backend is its twin and reference, not a second product: no block of one backend runs inside the other, in production or in tests. The border therefore has to be
language-neutral (function pointers + `ctx`, numbers instead of strings, parked errors, no wall clock in the core), and the same list for every host.

**Order: first the testing model, then the narrowing.** The twin backends are compared as whole chips ([twin-backend-testing.md](twin-backend-testing.md)); only when the oracles stand on that
model is it safe to remove the slots below. Group 2 is then *removed from the product* (it exists only because the oracles swap blocks), not moved into a separate test interface.

## What the border is today

From the C++ side the border is one struct, `McuHost` in `native/core/mcu.hpp`: 28 function pointers, the `usb_wire` struct (`UsbHost`), a `ctx` and the parked-error flag. Every slot is optional.
`native/_rp2040.pyx` fills all of them with Python trampolines (`_w_*`). Read by *who calls it and why*, they are three different things, not one:

### Group 1 - the world proper: devices, the log, the wire (about 15 slots)

What an embedder who only wants to attach devices to the chip needs, and nothing else.

| Slot | What the world does |
|---|---|
| `warn` | the logger (an unimplemented register, an invalid access) |
| `uart_byte`, `uart_baud` | takes a transmitted byte; is told the baud rate changed (the Python side calls `on_baud_rate_change`) |
| `spi_transmit` | a word is on the wire; the device answers with `complete_transmit()` |
| `i2c_start`, `i2c_connect`, `i2c_write`, `i2c_read`, `i2c_stop` | the device on the bus; each answers with a `complete_*()` |
| `adc_read`, `adc_channel_value` | a conversion was asked for; the analog value of a channel |
| `pin_changed` | a pin changed state: the listeners of `gpio[i]` (this is the GPIO border: see below) |
| `usb_wire` (+ `usb_reset`, `bus_reset`) | the device at the other end of the USB cable |

### Group 2 - "a Python block stands in for a C++ one" (about 10 slots)

Not needed by any device. They exist so that a block the host *replaced* (an oracle's pure-Python reference, the pure chip mixed with native blocks) can still be asked / told what the
C++ blocks would otherwise handle themselves:

`pin_source`, `pin_input_changed`, `pin_inputs`, `pin_update` (the pin banks / PIO / PWM replaced by the host), `xosc_hz`, `rosc_hz`, `clk_sys`, `ssi_baudr` (a host's own crystal, ring oscillator,
clock tree, SSI), `reset_block`, `record_cause` (a host's own block reset by the cascade), and `watchdog_tick` (its C++ half tells the C++ TIMER and SysTick itself;
the Python half, `_world_tick`, tells a *replaced* TIMER / PPB, and then the user's `add_tick_listener` callbacks - so one slot is both a replacement seam and a small world hook).

This is where the border is wider than the world needs. It is a testing facility (the oracles swap pure-Python blocks into a native chip; no product mixes backends) that became part of the contract; it is also what makes a C ABI large. It goes away once the oracles compare whole twin chips.

### Group 3 - the engine's own policy that is routed through Python (4 slots)

`system_reset_request` (AIRCR.SYSRESETREQ), `watchdog_trigger`, `pio_started`, and the default of the first two. `PPB._world_system_reset` and `Watchdog._world_trigger` call a *user-overridable
hook* (`on_system_reset_request`, `on_watchdog_trigger`); when nothing is installed, the default is **Python**: `rp2040.enter_reset(...)` then `leave_reset()`. So the cascade is in the C++ core
(0096 step 3b) but the *decision to run it* and the call into it take a round trip through the chip's Python object. `pio_started` is the fallback for a chip without an owning `Simulator`
(it creates an asyncio task): it is a host concern, but today it is called from inside a register write.

## The other direction: what the world pushes in

Not in `McuHost`, but it is the same border, and it is the other half of the contract:

- **memory the host owns:** `chip.flash` (a caller-owned buffer), the bootrom (`load_bootrom`), SRAM / DPRAM (`McuMemory`);
- **pins:** `gpio[i].set_input_value` / `release_input`, `add_listener`; the bus-side `pad_value |= 0x40` still used by some tests;
- **devices answering:** `feed_byte`, `complete_transmit`, `complete_start/connect/write/read/stop`, the ADC sample push, the USB endpoint calls;
- **time:** `clock.create_alarm`, `Simulator.schedule_threadsafe`, `request_stop`;
- **running:** `Simulator`, `_execute_batch`; **looking:** `snapshot()`, memory reads, the narrow core view (`pc`, registers, `step`, breakpoints).

## The proposed line (a proposal, to be agreed before any code)

1. **The border is Group 1 plus the push-in list above.** That is the public contract, the same on the native chip, the pure-Python chip and under wasm, and it is what the C ABI of 0102 exports.
   About 15 callbacks instead of 28.
2. **Group 2 leaves the product.** No block is substituted at run time and the oracles stop substituting (they compare whole twin chips), so these slots, the `set_*_world` switches and the
   `_native_*_address` seams in the shell have no user left; they are deleted, not relocated. `watchdog_tick` is split: the C++ chip tells its own consumers, and a world-facing
   `add_tick_listener`-style event stays in Group 1 only if a device really uses it (to be checked - nothing in `external/` does, as far as grepped on 2026-10-25).
3. **Group 3 moves inside.** A watchdog or SYSRESETREQ reset is the core's decision: it runs the cascade itself (it is already in C++) and tells the world *that it happened* (an event, like
   `bus_reset`), instead of calling a Python function that calls back into C++. A user who wants to intercept a reset keeps a hook (`on_watchdog_trigger` /
   `on_system_reset_request` as today), but the default no longer takes the Python round trip. `pio_started` stays a host concern but leaves the register write (the owner is asked once,
   when it attaches, not on every CTRL write).
4. **GPIO is the one door for devices.** `pin_changed` + `gpio[i]` is the border for external devices; anything a device does with a pin goes through `gpio[i]` (value, state,
   `add_listener`, `set_input_value`, `release_input`) and a bus register write, never through `pad_value` / `ctrl` / `driven` mirrors (those stay the IO and PADS blocks' coupling).
5. **Firmware has one primitive:** the flash buffer and `load_bootrom` (0096, "Firmware is several things on purpose"). Everything else (UF2, HEX, littlefs images, the Kaluma program) is
   backend-independent Python on top of those two.

## Open questions (to answer before a plan is written)

- Does anything outside tests and `boards/` use `add_tick_listener` / `on_baud_rate_change`? (`on_baud_rate_change` is a device-facing hook of the UART - Group 1; the tick listener
  is the doubtful one.)
- Group 2's `pin_*` slots carry the replaced-banks case that `tests/utils/pin_diff.py` and the pure chip rely on: can `McuHostTest` be a second struct handed to `Mcu::build`, or does the
  replacement belong in the shell (the C++ banks never learn about it)?
- Are the user hooks `on_system_reset_request` / `on_watchdog_trigger` used by anyone (boards, tests, the CLI), or only the defaults? If only the defaults, Group 3 can lose the hook itself.
- Where do the *wasm* host's slots stop (0103 already answers the device-mode USB wire itself and imports nothing): is the Group-1 list above what a browser host would provide, or a subset?
- Measured, not designed: the cost of `pin_changed` and the clock alarms of Python devices per change (the hot crossing; 0096, "Not decided").

## Not in scope

No change of `McuHost`, of the shells, of the C ABI or of the Python API follows from this note. What is decided here, once agreed, is a list; the plan (which slots move when, what an oracle
needs to keep working) is a second document.
