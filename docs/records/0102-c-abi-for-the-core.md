# 0102 - a C ABI around the core, for wasm (and, later, cffi)

Status: proposed (2026-10-16; 2026-10-18: the exports carry a core index for the registers (`rp2040_core_register` / `_set_register`), the module builds with the second core in it, but nothing in the wasm host enables or tests core 1 yet - 0104's "Next steps"); **a first slice is built inside [0103](0103-wasm-backend.md) (2026-10-17/18): `native/wasm/rp2040_wasm.cpp`, the headless subset. The rest - the host callbacks - is documentation only, nothing designed in detail.** Companion to [0096](0096-cpp-mcu-core.md): this is the "host interface" of its gate G6 and the entry point of its Phase 6 (wasm). Written down on the maintainer's call ("for wasm"); per `CLAUDE.md` a note about what should eventually happen is not a go-ahead to implement it.

## Why

The core is C++17 (classes, templates, no exceptions/RTTI/STL). A wasm module can export only C-ABI functions, and any binding that is not Cython (a wasm host in JS/Rust/Go/C#, cffi, a plain C program) needs the same: a flat `extern "C"` surface. Cython binds C++ directly and does not need it, so today nothing forces it; Phase 6 does. The same surface also is the answer to G6's "the host interface and the threading contract are documented" - it is that contract in code.

## What it would be

- **An opaque handle.** `McuHandle*` from `mcu_create()`, destroyed by `mcu_destroy()`; the C++ `Mcu` stays an implementation detail (it is large and non-movable; the handle owns it).
- **Calls, not classes.** `mcu_build(h, const McuHostC*)`, `mcu_set_memory(h, const McuMemoryC*)`, `mcu_reset`, `mcu_run`/`mcu_run_at_sys_clock` (returning the result struct by pointer), `mcu_read32/write32` for a host that pokes registers (a debugger, a test), pin/ADC/UART entry points for the world's inputs, `mcu_request_stop`. Every failure returns a code; nothing throws (D4).
- **The host as a C struct.** `McuHost` is already one optional struct of function pointers plus a `ctx`; the C twin is the same fields with fixed-width types. Layout is part of the ABI: a version field first, fields appended only.
- **The memory buffers are the host's.** `McuMemory` (boot ROM, flash, SRAM, USB DPRAM) points into memory the host allocated; in wasm that is the module's linear memory, which JS fills through the exported pointer.

## What wasm adds to the picture

- **No `externref` / `funcref` is needed at the boundary.** A callback is a function pointer + `ctx`: in wasm32 both are `i32` (an index into the module's own indirect table, and an address). The module calls the host through ordinary imports; a thin C++ trampoline per callback turns the import into the `McuHost` pointer. Host objects (a WebSerial port, a canvas) stay on the JS side behind an integer handle the JS maps back to the object.
- **`i64` in `warn`.** The value argument is `int64_t`; JS sees it as a BigInt - either that is accepted or the callback is split into two `i32`.
- **The stop byte across threads** (`request_stop`, the only cross-thread write in the contract) needs shared memory and atomics in wasm; without them stopping works from the thread that runs the core only. This is a threading-contract line to write down, not a design problem.
- **No wall clock in the core.** The host sleeps for what `ticks_advanced` says (already so in `McuResult`); in a browser that is `setTimeout`/`requestAnimationFrame` around `mcu_run`.
- **Gate G1/G2 on wasm** (Phase 6's exit): the same MicroPython and pico-sdk UART programs, run through this surface under wasmtime/node, with the same "the host was called for the UART bytes only" count.

## What a host does with the core: the run loop (2026-10-26)

The core is a state machine with a virtual clock; it never runs by itself and never waits. Everything a host has to do to "make the emulator go" is this, the same on every host:

1. **Call `run` in a loop.** One call executes a bounded batch (up to ~1,000,000 instructions) and returns why it stopped: the batch is done, `stop` was requested, `on_break`, or a device that waits on the real world capped an idle jump. It also returns `ticks_advanced`, the simulated time that passed.
2. **Hand the control back between calls.** On a host with one thread and one event loop (a browser tab, asyncio) a `run` call keeps that thread busy for its whole length, so the host yields between calls (`await asyncio.sleep(0)`, `setTimeout(0)`, `requestAnimationFrame`) - only so that the rest of the host (devices, terminal, UI, gdb) gets a turn. Nothing in the core needs it.
3. **Pace it, or not.** Sleeping for `ticks_advanced` makes the chip run in real time (a blinking LED, a UART at its baud rate, a firmware's `sleep_ms`: an idle jump moves simulated time ahead at once, so without the sleep a second of firmware sleep passes in microseconds). Not sleeping runs as fast as possible (tests, CI, batch jobs). That is the host's choice; the core has no wall clock.
4. **Everything else in the chip is stepped inside `run`** - the clock's alarms, the PIOs, the DMA - there is no second thing for a host to drive (the PIO's own "fallback task" for a chip with no `Simulator` was removed with `pio_started`: a chip nobody runs does not advance, like any other block).

**The loop on its own thread / in a worker.** Then the thread that runs steps 1-3 *is* the core thread and the host's main thread never blocks, so step 2 is not needed for it (the loop may call `run` back to back). The contract of [0096](0096-cpp-mcu-core.md), "Threads", is unchanged: every core call and every host callback is on the core thread; the only things another thread may touch directly are the stop byte (`request_stop`) and the real-world flag; anything else (a pin driven from the GUI, a socket's reply, a typed byte) is put in a queue by the other thread and applied by the loop between `run` calls (in Python: `Simulator.schedule_threadsafe`). A worker therefore needs no "run in thread" function in the core - the host supplies the thread. In wasm, `request_stop` from another thread needs shared memory and atomics; without them a worker can only be stopped between its own `run` calls, so the loop checks its message queue after each one.

## What this does not decide

- **cffi** (or any other Python binding on top of the C ABI) is a *consumer* of this surface, not part of it. It would pay only for PyPy support (the Cython extension runs on PyPy through `cpyext`, slowly) or for a single `abi3` wheel in place of the per-interpreter matrix of `publish.yml`. Neither is a goal today. Two measurements to take before deciding: CoreMark (`demo/coremark_single_thread.py`) on PyPy with the current build, and what the interpreter matrix costs in CI time. The Cython shells (the `_from_mcu` views) would stay for CPython; a cffi shell would be a second set of views over the same handle, which is the real cost.
- **Whether the C++ API is frozen.** The C surface exposes less than the C++ one; the C++ classes stay free to change behind it.
- **How much of the Python-facing API (block objects with properties) the C ABI mirrors.** Likely none: a flat register-and-world interface is enough for wasm; Python's richer views live above it.

## Open questions

1. One `McuHostC` struct or one registration function per callback (a wasm module imports functions by name; a struct of pointers is awkward to fill from JS)?
2. Versioning of the struct layout and the symbol names; who may append.
3. Is the C surface generated (from the C++ headers or a small description) or written by hand? Hand-written is simplest and is checked by the existing bare-core harness.
4. Where it lives: `native/core/` (header-only) next to `mcu.hpp`, or a sibling top-level module (`CLAUDE.md`'s layout rule: distinct pieces in sibling modules) - to be asked, not guessed.

## Progress log

- 2026-10-16: proposed (documentation only).
- 2026-10-18: **A first slice is built, inside 0103.** `src/rp2040py/native/wasm/rp2040_wasm.cpp` exports `rp2040_rom` / `rp2040_flash` / `rp2040_sram` / `rp2040_uart` (pointers into the module's own memory), `rp2040_erase_flash`, `rp2040_boot` (a build on the first call, a power-on reset after), `rp2040_run` / `rp2040_run_n`, `rp2040_register` / `rp2040_set_register`, and the UART0 bytes, the dropped count and the warning count out; the module imports nothing. Of the open questions: (1) none yet - the only host callback, `uart_byte`, is answered inside the module, so the struct-or-functions choice is not forced; (4) the file sits in `native/wasm/`, a sibling of `core/`, by the layout rule, awaiting the maintainer's word. Still undesigned: pins, UART input, I2C / SPI devices, ADC, USB, the clock tree hooks - every callback of `McuHost` the module does not answer itself.
- 2026-10-18: **A note from the second core ([0104](0104-second-core-seams-and-architecture.md)).** The register accessors of the first slice have no core argument (`rp2040_register(index)`); widening this ABI waits for 0104's seams (P0), and the accessors become `(core, index)`, with `rp2040_core_active(core)` and `rp2040_run*` running the chip, not "the core".
- 2026-10-26: **The host half of the surface is now the narrowed `McuHost` (documentation only; no code of this record changed).** [0096](0096-cpp-mcu-core.md)'s border plan cut the host struct to 16 target slots plus the still-open `pio_started`; `McuHostC` is to carry exactly these, and nothing that lets a host replace a chip block. Fixed-width form: `warn(ctx, block, kind, offset, i64 value)`; `uart_byte(ctx, instance, byte)`, `uart_baud(ctx, instance)`; `spi_transmit(ctx, instance, value)`; `i2c_start(ctx, instance, repeated)`, `i2c_connect(ctx, instance, address, mode)`, `i2c_write(ctx, instance, value)`, `i2c_read(ctx, instance, ack)`, `i2c_stop(ctx, instance)`; `adc_read(ctx, channel)`, `adc_channel_value(ctx, channel, i64* value)`; `pin_changed(ctx, bank, pin, new_state, old_state)`; `reset_requested(ctx, cause)` with cause 0 = SYSRESETREQ, 1 = watchdog; `usb_wire` (the `UsbHost` table), `usb_reset(ctx)`, `bus_reset(ctx)`; every callback returns a bool (false = the host parked an error), and `ctx` plus the `failed` pointer come first in the struct. There is no `pio_started`: it was removed (see [0096](0096-cpp-mcu-core.md), 2026-10-26 "`pio_started` is gone"), the 16 slots above are the whole host struct. What a wasm host lacks today (SPI, I2C, ADC, pin events) is step 8 of the plan.
