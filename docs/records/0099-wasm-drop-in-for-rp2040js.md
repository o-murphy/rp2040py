# 0099. When the WASM host is built: try a drop-in replacement for the original rp2040js

- Status: **Proposed - a note about what should eventually be tried; nothing designed, nothing built** (CLAUDE.md: documenting is not implementing). It belongs to Phase 6 of [0096](0096-cpp-mcu-core.md) and is not a reason to start Phase 6 earlier.
- Conceived: 2026-10-08
- Related: [0096](0096-cpp-mcu-core.md) (the C++ core and the WASM host, Phase 6; the "core owns all behaviour a firmware can observe, the host only supplies the world" gate of 2026-10-06), [0098](0098-datasheet-conformance-audit.md) (the references the core is held to), [0097](0097-pyodide-wasm-wheel.md) (a first WebAssembly artefact, for Python)

## The idea

rp2040py began as a port of [wokwi/rp2040js](https://github.com/wokwi/rp2040js) (TypeScript) and still tracks it: the 1.4.0 copy-list of 0096, the `clocks.spec.ts`-derived tests, the `package.json` pin. When the C++ core is built as a WASM reactor (0096, Phase 6), the same module could be wrapped in a **TypeScript layer that exposes rp2040js's own public API** - the classes and members a rp2040js user already writes against - so that it is dropped into rp2040js in place of its hand-written TypeScript peripherals and CPU, or, the other way round, a rp2040js project swaps its import and runs on the C++ core.

What it would buy, in the order it matters:

1. **A second, independent acceptance test.** rp2040js has its own spec suite. Running those specs, unchanged, against the wrapped core says whether the core still behaves as the code it descends from, from outside our test tree. Where a spec fails, either the core regressed or the spec encodes rp2040js behaviour that [0098](0098-datasheet-conformance-audit.md) found to be wrong against the datasheet - and which of the two it is gets written down, block by block, the way 0098 does.
2. **A real second host, early.** The 2026-10-06 gate asks that the core run with an *empty* host interface and only the world supplied. A drop-in wrapper is the strictest version of that: a host written in another language, by the API of someone else's project, that cannot reach into the core's internals.
3. **Speed for the existing rp2040js users** (Wokwi and others), which is the reason the original exists.

## What has to be checked before it is more than a wish (all unknown today)

- **The size of rp2040js's public surface** that projects really use (`RP2040`, `Simulator`/clock, `GPIOPin` and its listeners, `uart[n]`, `spi`/`i2c`/`adc`/`pio`/`usb` hooks, `loadBootrom`, `core` registers and the GDB server) and which of its members are internal state the core does not and will not have. A wrapper can only be a drop-in for the part that is an API and not a data structure.
- **Where rp2040js and the core deliberately differ** ([0098](0098-datasheet-conformance-audit.md) lists them: the RTC and the watchdog's tick need a configured clock before they count, the TIMER does not run from reset, and so on). A drop-in that makes those behave like the original again would undo corrections; the likely answer is to keep the core's behaviour and mark the specs that assume the old one.
- **The per-event price at the JS/WASM boundary.** Per-pin and per-byte callbacks are cheap in-process and unmeasured across a boundary (0096 estimates 4-200 us per call for Python hosts; JS is not measured). rp2040js's listeners are synchronous callbacks on every change; whether the wrapper can keep that contract, or needs the batched event ring that 0096's D2 planned and the GPIO path never got, decides how much of the API can stay the same.
- **Time and pacing.** rp2040js lets the embedder drive its clock; so does the core (the host calls it with how much simulated time to run). The wrapper has to map one onto the other without a Python-style event loop (see the host/controller split discussed in 0096: the core decides what to run and how long to sleep, the host sleeps).
- **Licence and packaging** of shipping a TypeScript layer next to a project that is MIT-licensed upstream: to be looked at when it is a real proposal, not now.

## What this does not decide

Whether the wrapper lives in this repository or in a separate one; whether it targets rp2040js 1.x only; and whether it is done at all. Phase 6 first has to boot MicroPython to the REPL on the same wasm blob under wasmtime and node (0096's exit criterion); this note is only the next question once that holds.
