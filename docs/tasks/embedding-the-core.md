# Embedding the core: what a host must do, what must not stick out, and a `boot` in the core (working note, 2026-10-27 - **nothing here is built**)

Written at the maintainer's request after the narrowing of both halves of the border ([narrow-the-border-plan.md](narrow-the-border-plan.md), [narrow-the-input-plan.md](narrow-the-input-plan.md)). The question: *if someone takes the C++ code and writes their own emulator around it, what does that take?* The chip is self-contained; the emulator around it is not yet - a wrapper written without the author's knowledge of the order and the rules is a wrapper that breaks. Facts below are from the code and from measurements of this session; what is a proposal is marked as one.

## What a C++ host does today (the minimum)

[tests/cpp/gate_boot.cpp](../../tests/cpp/gate_boot.cpp) is a 125-line host with no Python and no Cython that boots a stock MicroPython and runs a program on it. It does five things:

1. **Memory**: four buffers (`McuMemory`: boot ROM, flash, SRAM, the USB DPRAM), allocated and kept by the host; the flash image copied in at the right place.
2. **The world** (`McuHost`): `ctx`, the `failed` flag, and the slots it has something to say to - here only `warn` and `uart_byte`. Every other slot is optional: with none, that block is "not connected".
3. **The start-up, in order**: `set_memory`, `build(host)`, `set_crystal_hz(12e6)`, `reset(...)`, `leave_reset()` (and `set_core1_enabled` if wanted).
4. **The loop**: `run_at_sys_clock(limits, &result)` until `result.reason` says to stop; the host decides the budget, when to yield, whether to pace.
5. **Reading the result**: UART bytes out of the slot.

So the minimum is small. The trouble is everything around it that is not written down as a contract.

## What an embedder is missing (the gaps)

- **No contract.** The order of the calls, who owns the buffers and for how long, what `failed` means and what the host must do when a callback returns `false` live in header comments and in one test file. A wrong order gives a silently dead chip.
- **No build target.** `rp2040.hpp` pulls 47 headers; the tests compile with `-std=c++17` (and, for the wasm target, `-fno-exceptions -fno-rtti`) by hand. There is no CMake target or package to link.
- **No USB/CDC "batteries".** To talk to a REPL instead of the UART the embedder would have to write the USB host side: the ten `UsbBlock` inputs (`connect_device`, `send_setup_packet`, `schedule_read`, `finish_*`, ...), the enumeration, the CDC channel, the re-enumeration after a reset. Today that is Python (`device/base_device.py`, `usb/cdc.py`).
- **No image loading in the core.** UF2 is parsed in Python (`load_flash.load_uf2`); a raw `.bin` has no address in it, so the host has to give one. Board knowledge (a flash offset of a filesystem, CircuitPython's drive start) stays the host's.
- **No C ABI beyond the wasm glue** (`native/wasm/rp2040_wasm.cpp`, 247 lines). [0102](../records/0102-c-abi-for-the-core.md) describes the ABI; a language that is not C++ has no layer yet.
- **The reset policy was the host's** until the experiment on `experiment/core-owned-reset` (not merged, not decided): without a hook installed, the core resets the chip itself on `WATCHDOG.TRIGGER` and `AIRCR.SYSRESETREQ`.

## What must not stick out (a proposal - the classification is not yet agreed)

The line the note draws: everything below is *not* part of what an embedder sees. A new member that appears outside has to explain why.

1. **Wiring between blocks** - the clock tree's notices to its consumers (`clk_sys_changed`, `tick_changed`, ...), `check_interrupts`, `recompute_due`, `*_updated`. The core connects them. Mostly gone from the shells already (pin classes `plumbing`, `own`).
2. **A single block's `reset()`** - an instrument of the oracles, not an API. The host resets the chip as a whole.
3. **State images for reading** (pin images, `stopped`, `machines`, `timer`) - `snapshot()` or the bus; the pin classes them `test`.
4. **What a reset means** - the host must not need to know what to do on `machine.reset()` or a watchdog timeout.
5. **Order and pacing** - no guessing of when to yield or how much to run: one call, "run and say why you stopped".
6. **The USB host protocol** (ten calls) - an embedder gives and receives CDC bytes and a connected/disconnected event.
7. **Image formats** - parsing UF2 is not the embedder's problem.

**What stays outside by design**: the memory buffers and the image, the crystal frequency, the UART/CDC as a byte stream, the pins and the external devices (SPI/I2C/ADC answers), the logger. That is the "which firmware, which interfaces, which devices on GPIO" that the maintainer described.

The gap between this list and the pin table of [0102](../records/0102-c-abi-for-the-core.md) is the work: the table says what the Cython shells *do* call (75 names on the host's list, about 63 as exports), this note says what a host *should* have to call. For example the ten USB-host calls are on the table and have no place in this note.

## A `boot` in the core (a proposal for later - not started)

One call with a configuration instead of four calls in the right order:

```
Mcu::boot(const McuConfig& cfg) -> Status
  cfg.memory      // rom, flash, sram, dpram
  cfg.host        // McuHost: the slots and `failed`
  cfg.crystal_hz  // 12e6
  cfg.core1       // on or off
  cfg.clock       // the host's clock, or the chip's own
```

Inside it does what the host writes today: `set_memory`, `build`, the crystal, `reset`, `leave_reset`. The host cannot get the order wrong because it has no order.

**What decides the shape - the order the Python shell uses** (`native/_rp2040.pyx`, read 2026-10-27; an earlier statement in the conversation had the clock's place wrong):

| step | why it sits there |
|---|---|
| `use_clock(clock)` | first: several blocks create alarms on the clock in their constructors, so it must be set before anything is built. A **real** dependency, and the same for a C++ host. |
| `build(host)` | builds the blocks on that clock: windows on the bus, lines to the CPU. |
| default frequencies, `xosc_freq = 12 MHz` | parameters the clock tree reads, so they come after the tree exists. |
| the Python shells of the blocks | views over blocks that already exist. |
| `set_crystal_hz` | after the clocks shell is constructed (its comment: after the PLLs and the blocks it drives, because the native tree points at their C++ blocks). Looks like history: `gate_boot.cpp` also sets it after `build`; whether the crystal could go into `build` with the rest of the configuration is **not verified**, but is the likely shape of `boot`. |
| `reset()` | last: it needs every block to exist. |

So the clock must be a field of the configuration, not a separate call; the crystal, the frequencies and the reset fold into one call without loss.

**Open (decide when it is built):**
1. *A host that needs to step in between.* The Python shell sets the crystal after the tree is built and points the chip at the simulator's clock again on every run. `boot(cfg)` for a simple host, and the stages (or hooks between them) for one that needs more? Or all of it in the configuration?
2. *Cold start and warm reset are different things.* `boot` does the full set-up once; `reset` stays a separate call (no rebuild, flash kept). They are easy to confuse.
3. *No exceptions.* The core is `-fno-exceptions`, so `boot` returns a status and the host never sees a half-built chip. What to return - a cause code or a yes/no - is open ([chip-protocol-sketch.md](chip-protocol-sketch.md) has the ABI-versus-glue split).
4. *The image and `leave_reset`.* If `boot` points the core at the flash's entry before the image is loaded, the chip starts on empty memory. Either the image is part of the configuration or `boot` leaves the chip in reset until an explicit "go".

**With a reset the core does itself and the image loading it would be three actions for an embedder**: `boot(cfg)`, `run(limits)`, and bytes and pins in and out. Everything else the core does.

## What would come first (a suggestion, not a plan)

1. A page "Embedding the core": the minimal host (from `gate_boot.cpp`), the order, the `failed` / `ctx` rules, and the two lists above. Costs nothing, breaks nothing, shows what is still missing.
2. The reset policy in the core (the experiment) - small, isolated, already measured.
3. Image loading (UF2 and a raw image with an address) - a pure data transformation; the format and the address are a decision to make first, and board knowledge stays outside.
4. USB/CDC "batteries" in C++ - the largest gain and the largest risk (live-firmware CI behind every step).
5. `boot(cfg)` - after 2-4, when its shape is clearer.

Each needs its own go-ahead (CLAUDE.md, "Document" vs. "implement").
