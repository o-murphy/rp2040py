# 0099. When the WASM host is built: try a drop-in replacement for the original rp2040js

- Status: **Proposed - a note about what should eventually be tried; nothing designed, nothing built** (CLAUDE.md: documenting is not implementing). It belongs to Phase 6 of [0096](0096-cpp-mcu-core.md) and is not a reason to start Phase 6 earlier.
- Conceived: 2026-10-08
- Related: [0096](0096-cpp-mcu-core.md) (the C++ core and the WASM host, Phase 6; the "core owns all behaviour a firmware can observe, the host only supplies the world" gate of 2026-10-06), [0098](0098-datasheet-conformance-audit.md) (the references the core is held to), [0097](0097-pyodide-wasm-wheel.md) (a first WebAssembly artefact, for Python)

## The idea

rp2040py began as a port of [wokwi/rp2040js](https://github.com/wokwi/rp2040js) (TypeScript) and still tracks it: the 1.4.0 copy-list of 0096, the `clocks.spec.ts`-derived tests, the `package.json` pin. When the C++ core is built as a WASM reactor (0096, Phase 6), the same module could be wrapped in a **TypeScript layer that exposes rp2040js's own public API** - the classes and members a rp2040js user already writes against - so that it is dropped into rp2040js in place of its hand-written TypeScript peripherals and CPU, or, the other way round, a rp2040js project swaps its import and runs on the C++ core.

**Two ways to land it, to be chosen when it is real:** (a) the wrapper above, which leaves rp2040js untouched and only has to honour its API; or (b) **a fork of rp2040js with patches**, where the TypeScript peripherals and CPU are replaced by calls into the wasm module inside the fork itself, so the API stays whatever the fork's code says and nothing has to be imitated from outside. (b) is less work per member and lets the fork change what (a) could not (a data structure a project reads directly, a callback shape), at the price of a long-lived fork that has to follow upstream (1.x releases, the copy-list of 0096) and may never be accepted back. (a) can later turn into (b) if its list of unimitable members grows; the other direction is harder.

What it would buy, in the order it matters:

1. **A second, independent acceptance test.** rp2040js has its own spec suite. Running those specs, unchanged, against the wrapped core says whether the core still behaves as the code it descends from, from outside our test tree. Where a spec fails, either the core regressed or the spec encodes rp2040js behaviour that [0098](0098-datasheet-conformance-audit.md) found to be wrong against the datasheet - and which of the two it is gets written down, block by block, the way 0098 does.
2. **A real second host, early.** The 2026-10-06 gate asks that the core run with an *empty* host interface and only the world supplied. A drop-in wrapper is the strictest version of that: a host written in another language, by the API of someone else's project, that cannot reach into the core's internals.
3. **Speed for the existing rp2040js users** (Wokwi and others), which is the reason the original exists.

## Step zero: measure how far we are from the original (not done)

The maintainer's doubt (2026-10-08): it is not certain how far rp2040py has moved from the original emulator, so a drop-in may not be possible at all. Before anything above is worth designing, make two lists and read their overlap:

1. **The public surface of rp2040js** that real projects use (the exports of its package, the members of `RP2040`, `GPIOPin`, the peripherals and the simulator/clock that Wokwi and other embedders touch), against ours class by class and member by member. Class names were inherited (`RP2040RTC`, `RPWatchdog`...) but the members and the types have not been compared.
2. **The behavioural differences**, which are known in part: everything [0098](0098-datasheet-conformance-audit.md) corrected against the datasheet (the TIMER and the RTC count only once firmware has configured their clocks, the PWM phase-correct output, the DMA pacing timers, I2C, UART/SPI DREQs, the PIO rules, the interpolator), and what was added that the original does not have (per-block reset cascade with PSM/RESETS WDSEL, the reset of the clock tree, bootrom B0/B1/B2, the USB CDC host, CYW43), plus the architecture (bus window table, the C++ batch loop, the thread-free engine) wherever a user reads internal state.

The overlap says how many of rp2040js's own specs would pass unchanged. A large gap points to fork (b), or to an API of our own and no drop-in; a small one makes wrapper (a) realistic. Until this is done, the rest of this record is only a direction. **This is a lower priority than the C++ port and the audit of [0096](0096-cpp-mcu-core.md) and [0098](0098-datasheet-conformance-audit.md), which are the main work; nothing here is started.**

## What has to be checked before it is more than a wish (all unknown today)

- **The size of rp2040js's public surface** that projects really use (`RP2040`, `Simulator`/clock, `GPIOPin` and its listeners, `uart[n]`, `spi`/`i2c`/`adc`/`pio`/`usb` hooks, `loadBootrom`, `core` registers and the GDB server) and which of its members are internal state the core does not and will not have. A wrapper can only be a drop-in for the part that is an API and not a data structure.
- **Where rp2040js and the core deliberately differ** ([0098](0098-datasheet-conformance-audit.md) lists them: the RTC and the watchdog's tick need a configured clock before they count, the TIMER does not run from reset, and so on). A drop-in that makes those behave like the original again would undo corrections; the likely answer is to keep the core's behaviour and mark the specs that assume the old one.
- **The per-event price at the JS/WASM boundary.** Per-pin and per-byte callbacks are cheap in-process and unmeasured across a boundary (0096 estimates 4-200 us per call for Python hosts; JS is not measured). rp2040js's listeners are synchronous callbacks on every change; whether the wrapper can keep that contract, or needs the batched event ring that 0096's D2 planned and the GPIO path never got, decides how much of the API can stay the same.
- **Time and pacing.** rp2040js lets the embedder drive its clock; so does the core (the host calls it with how much simulated time to run). The wrapper has to map one onto the other without a Python-style event loop (see the host/controller split discussed in 0096: the core decides what to run and how long to sleep, the host sleeps).
- **Licence and packaging** of shipping a TypeScript layer next to a project that is MIT-licensed upstream: to be looked at when it is a real proposal, not now.

## What this does not decide

Wrapper (a) or fork (b); whether it lives in this repository or in a separate one; whether it targets rp2040js 1.x only; and whether it is done at all. Phase 6 first has to boot MicroPython to the REPL on the same wasm blob under wasmtime and node (0096's exit criterion); this note is only the next question once that holds.
