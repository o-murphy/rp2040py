# 0096. A C++ MCU core, statically linked through Cython, with a WASM host last

- Status: **Proposed - a phased implementation plan, nothing built (2026-10-03).** Written per
  CLAUDE.md's "document vs. implement" rule: this record is the plan and the evidence behind it,
  and authorizes no code. Each phase below needs its own explicit go-ahead.
- Conceived: 2026-10-03
- Related: [0013](0013-cython-core.md) (the Cython core this replaces the *implementation* of, not
  the API of), [0031](0031-pio-cython-tick-batching.md) / [0034](0034-execute-batch-native-port.md) /
  [0039](0039-simulation-clock-native-port.md) / [0047](0047-cyw43-pio-gpio-hotpath.md) (the
  ports that already put the CPU, batch loop, clock, `GPIOPin` and `RPPIO` into Cython - what this
  starts from), [0016](0016-jit-fusion.md) (rejected: why "make the interpreter faster" is a
  rewrite of the core, not a trick on top of it), [0040](0040-time-monotonic-vs-cimport.md) (the
  `Py_LIMITED_API`/abi3 constraints this has to respect), [0027](0027-cyw43-wifi.md) /
  [0048](0048-cyw43-nat-reflector.md) (the CYW43 stack - the "maybe later" part), [0030](0030-external-device-concurrency.md)
  (the concurrency model the callbacks must not break), [0049](0049-external-device-authoring-docs.md) /
  [0067](0067-external-devices-and-boards-skill.md) (what "boards and external devices stay in Python" protects),
  [0089](0089-one-reset-for-every-trigger.md) (reset semantics every ported block has to keep)

## What was asked for

Seven requirements, in the user's own order of importance, kept as the acceptance list for the
whole plan (every phase is checked against them):

1. **C++ is the MCU.** Everything the *chip* provides - CPU, bus, GPIO, PIO, XIP, QSPI/SSI, SIO,
   timers, comms blocks, DMA, USB controller - to make emulation faster.
2. **Python is the board.** Board descriptions (`boards/`), `ExternalDevice`s and everything built
   on top of the MCU stay Python. Extensibility for developers is the point of the Python layer.
3. **Users can still write a peripheral in pure Python.** Not just boards and devices: a
   memory-mapped block of their own, or a replacement for one of the MCU's.
4. **Zero-copy, caller-owned memory.** The C++ never allocates buffers. The caller - Python - hands
   it pointers: firmware/flash, bootrom, SRAM, DPRAM, rings.
5. **No FFI.** C++ is **statically linked into the Cython extension**; Cython remains the layer
   between Python and C++. No `ctypes`/`cffi` bindings to a shared library.
6. **Incremental, testable at every step, Python API unchanged.** Parts of what exists are rewritten
   in C++ and plugged into the library so each step is testable immediately; nothing that works
   through the Python API today (`rp2040.timer`, `rp2040.pio[0]`, `ExternalDevice`, `schedule_threadsafe`, ...)
   changes shape.
7. **The same C++ also runs through a WASM host - last.** Only once the MCU is fully ported does
   the WASM build get done, so that the code can be used in environments with no CPython
   extension toolchain (or no Python at all).

Also stated: parts of what already exists - the CYW43 / Pico W module was the example - may move to
C++ too, but *the rest should not*, and a user must always be able to write peripherals in plain
Python.

## What was measured (and what it does and does not prove)

Everything below was measured on 2026-10-03 in a throwaway scratch directory; none of the harness is
in the tree (Phase 0 turns it into one). Machine: 4 shared cores, so **±10-15% noise** - the one
outlier (a 153 Minstr/s native run against 310-336 on every other) was discarded and the run
repeated. CPython 3.10/3.11, PyPy 3.10.16 (v7.3.19, supplied by the user - the sandbox could not
download one), `ziglang` from PyPI as the wasm toolchain, `wasmhost` with the `wasmtime` and `node`
backends. **Not run:** `wasm3` (`pywasm3` would not install), JavaScriptCore, any Rust or Zig
*language* port, and any firmware other than MicroPython 1.21.0 on a Pico.

### 1. One synthetic program, every implementation

A shared 8-instruction Thumb-1 loop - `adds`, `eors`, `lsls`, `str` + `ldr` to SRAM, `adds`, `subs`,
`bne` - `2^N` iterations, ending in `bkpt`. Every implementation ended in identical registers
(`r0`, `r2`, `r5` compared), so these are numbers for the *same work*.

| Implementation | Minstr/s |
|---|---|
| CPython 3.11, pure Python | 0.74 - 0.94 |
| CPython 3.11, Cython (abi3 build) | 5.3 - 6.8 |
| CPython 3.10, Cython (normal build) | 15.8 - 16.7 |
| **PyPy 3.10, the same pure-Python code** | 15.7 (8.4M instr, JIT warm-up included) - 20.9 (33.5M); 38.7 CPU-only |
| C++ proxy, native (gcc / clang / zig c++) | ~315-336 / ~310-318 / ~363-374 |
| C++ proxy -> wasm32, `wasmtime` (via `wasmhost`) | ~310-313 |
| C++ proxy -> wasm32, `node` (via `wasmhost`) | ~234-284 |

- The C++ "proxy" is ~150 lines: ~25 of ~100 opcodes, a two-region bus, a cycle clock with a
  next-alarm deadline, no STL/exceptions/RTTI, flat C ABI; the wasm module is 13 KB with **zero
  imports**. It is a **speed ceiling for this shape of code, not a port**: no PIO, no peripherals,
  no interrupts. The real core will be slower; by how much is *not* measured. This record
  therefore treats "~20x" as an upper bound and the *order* of the gap as the finding.
- The abi3 penalty already noted in `setup.py` (13.3 vs 5.2 there) reproduced: ~16 vs ~5-7. Cython's
  limited-API build costs ~2.5-3x on this code, which a core with no Python in its inner loop does not pay.
- The Cython figure is the same with or without `Simulator._execute_batch()` (16.5 CPU-only vs 16.7), so the cost is in the
  core and bus themselves (memoryviews, boxed values, Python-level peripheral dispatch), not in the batch loop.
- PyPy matches or beats the Cython build on this loop. **Caveat that matters:** the loop is the
  best case for a tracing JIT (hot, monomorphic). It has not been run against a real firmware.

### 2. The cost of crossing into a Python peripheral

The same loop with the `ldr` reading a *Python* peripheral (`TIMER.TIMELR`, `0x4005400C`) every iteration,
instead of SRAM:

| | per iteration (8 instr) SRAM -> TIMER | per Python-peripheral read |
|---|---|---|
| Cython (3.10) | 477 ns -> 1005 ns | **~0.5 us** |
| CPython pure | 9.8 us -> 10.2 us | ~0.45 us |
| PyPy | 708 ns -> 766 ns | ~60 ns (short run, JIT warm-up dominates - indicative only) |

At ~3 ns per instruction in C++, one Python-peripheral access is worth ~150 instructions. A register that firmware polls
every few instructions cannot stay in Python in a C++ core - that is the whole speed argument for item 1.

### 3. Which blocks firmware actually touches

Wrapping every entry of `RP2040.peripherals` (plus `sio` and `ppb`) in a counting proxy and booting
`RPI_PICO-20231005-v1.21.0.uf2` with the built-in `BOOTROM_B1`, Cython build, stopped by *simulated* time:

| Block | first 3000 ms simulated | share |
|---|---|---|
| TIMER | 112,166 accesses | 52.6% |
| SIO | 97,042 | 45.5% |
| SSI (XIP bring-up) | 3,678 | 1.7% |
| the other 13 blocks together | 542 | 0.25% |

Total 213,428. Idle REPL afterwards (a 120 s wall run covering ~525 s of simulated idle): TIMER ~19.5M and
SIO ~15.7M accesses, every other block below ~4k. Time spent *inside* peripheral code was ~25% of wall
(the proxy's own `perf_counter` calls inflate that). **Caveats:** this is MicroPython's idle REPL (which is on USB CDC, not UART,
so no banner was ever seen on UART0 - the run was bounded by simulated time instead); PIO-heavy, DMA-heavy,
USB-heavy, CircuitPython and Pico W boots will weight differently. Phase 0 repeats it before the order of phases 2-4 is fixed.

### 4. What a WASM host costs per call

From the same C++ proxy through `wasmhost`, one call to `core_run(budget)`; effective Minstr/s by batch size:

| instructions per call | native (ctypes) | wasmtime | node |
|---|---|---|---|
| 100 | 158 | 3.4 | 1.4 |
| 1,000 | 284 | 33 | 13 |
| 10,000 | 294 | 169 | 89 |
| 100,000 | 320 | 282 | 232 |

That is ~30 us per call on wasmtime, ~70 us on node, ~0.6 us native; `wasmhost`'s own README gives a host
function call as ~4 us (wasm3), ~50 us (wasmtime, jsc), ~200 us (node). `wasmhost` memory is **copied**
(`memory.read/write`), not shared. Three consequences are baked into the design below: batches of >=10k
instructions per call; MMIO into Python must not be the normal path in a WASM host; and "the caller owns the
memory" means something weaker in a WASM host than in Cython.

## Decisions

**D1. The split.** *C++:* CPU, bus and memory map, SIO (+ interpolator), PPB (NVIC/SysTick), the clock and alarm
scheduler, TIMER, GPIO/IO/pads, PIO and its state machines, XIP/SSI/QSPI, UART, SPI, I2C, DMA, ADC, PWM, the USB
*controller* (+ DPRAM), watchdog, RTC, clocks/xosc/resets/psm/vreg/syscfg/sysinfo/tbman/busctrl.
*Python, permanently:* `boards.py` and `boards/`, `external/` (every `ExternalDevice`), the USB *host side* a
device talks to (`usb/cdc.py`, `usb_device.py`), the gdb stub, the CLI, the REPL runners, firmware retrieval, littlefs
tooling, and **any peripheral a user writes**. *CYW43:* protocol layer (`external/cyw43/bus.py`, `chip.py`) is a *candidate* for
C++ after Phase 5's gate; `nat.py` (host sockets) stays Python regardless - it is host I/O, not chip logic.

Why everything that is the chip, not just the hot blocks: Section 3 says two blocks carry ~98% of accesses, which fixes the *order*;
but a WASM host has no Python in many environments (JS, Dart, browser), so a register that is cold in Cython is still
unreachable there unless it is in the module. Cold blocks are "last", not "never".

**D2. The contract (what Python hands to C++).** A flat, allocation-free C++ API; no C++ type crosses it.
- *Memory regions:* a table of `{base, size, mirror_mask, ptr, flags(RO/RW/XIP)}` supplied by the caller. The C++ never
  `new`s a buffer. On Cython the pointers come from typed memoryviews / the buffer protocol (what `RP2040._sram`/`_flash`
  already are), and the Cython object keeps the buffer alive; a board adds PSRAM or a second flash by adding a region.
- *Rings:* UART TX/RX bytes and GPIO/pin-change events go through caller-supplied ring buffers; the caller drains them
  after `run()` returns. No per-event callback unless a window or alarm below demands it.
- *Python windows:* `attach_window(base, size, handler)` with `handler = {read32, write32, reset, ctx}` as C function pointers.
  A Python window **overrides** a native block at the same address (that is how a user replaces the ADC). The set/xor/clear alias
  decode (`atomic_update`, today in `BasePeripheral`) moves into the C++ bus as the *default*, so a simple Python block writes only
  `read32`/`write32` - **amended 2026-10-03 (see the progress log): the write entry point also receives the raw pre-alias value and
  the atomic type**, because TIMER/IO/UART/DMA/USB read `raw_write_value` today.
  Cython supplies trampolines to Python methods (~0.5 us, Section 2).
- *Time and IRQ:* `SimulationClock` alarms stay creatable from Python (the C++ clock calls back at the deadline); IRQ lines are a
  bitmap in the core with `set_irq(n, level)` callable from a window handler, so the contract must be **re-entrant for `set_irq`,
  `set_pin` and alarm scheduling called from inside a callback** (see risks).
- *Run:* `run(budget) -> exit_reason` (budget, deadline, breakpoint, fault, ...). Exit reasons, not exceptions.

**D3. Static linking through Cython, no FFI.** The C++ core is plain sources compiled into the existing extension
(`setup.py`'s `Extension` gains `language="c++"`, the core's `.cpp` files, include dirs). Cython declares it with
`cdef extern from "..."` and wraps it in `cdef class`es that keep today's public names and signatures. No `Python.h` inside
the core, so the core is independent of abi3/free-threaded. Pure-Python builds (`RP2040PY_SKIP_NATIVE_BUILD=1`) remain.

**D4. Language: C++17, restrained - not C, Rust or Zig.** The standard is **C++17** (`-std=c++17` / `/std:c++17`; nothing newer is
assumed, so MSVC, the Emscripten/Android/iOS toolchains and wasi-sdk/zig all build it). `-fno-exceptions -fno-rtti`, no allocating STL, templates (and C++17's `constexpr` tables, `if constexpr`, `std::array`/`std::string_view` only where they do not allocate) only for
register-block boilerplate (read/write/set/clr/xor aliasing, field tables), `static_assert` on layouts - the same discipline,
and the same CI guards (`check_no_exceptions`, `check_no_imports`), as `ballistics-lab/bclibc`, which already ships a C++
core native and as a 0-import bare wasm module through `zig c++` (~85 KB) and wasi-sdk. In this scratch measurement zig's
LLVM build was the fastest native (~365 vs ~315) - a property of the toolchain, available to C++ as it is. Rust: credible,
but the borrow checker fights a bus/IRQ/peripheral graph, bounds checks sit in the hot loop, and it leaves bclibc's existing
build/parity infrastructure behind. Zig-the-language: pre-1.0 churn, no Cython-like binding story. Not measured as languages.

**D5. Python API stays the contract.** Every ported block keeps a Python facade (a Cython `cdef class` or a thin Python
class over one) exposing the same attributes and methods the tests, devices, boards and CLI use today. The existing
`tests/` - already run on both the pure and native builds - are the oracle for every step; no step is accepted by
"the new tests pass" alone.

**D6. Rollback is built in, by the pattern that already exists.** The facades gated by `_native_gate.py`
(`RP2040PY_SKIP_CYTHON=1`) are the model: each ported block keeps its Python implementation selectable, so a C++ block can
be A/B-compared against the Python one on the same access trace and switched off without a rebuild while it settles.

**D7. WASM last.** After Phase 5. The module is the same sources, built as a wasm32 reactor with no WASI imports
(bclibc's recipe); its imports are *only* the optional Python-window/alarm trampolines. See Phase 6.

**D8. The pure-Python implementation is frozen, not extended.** It stays the reference/oracle and the PyPy/no-toolchain fallback
(PyPy ~20 Minstr/s on the synthetic loop). New MCU behaviour lands in C++ and, where the block is still Python, in Python;
a pure-Python twin of a *newly ported* block is not written unless the Phase 7 decision says otherwise.

## Non-goals

- Not a change to the Python API for peripherals, boards or devices (requirement 6).
- Not moving `boards/`, `ExternalDevice`s, the gdb stub, the CLI or REPL runners.
- Not moving CYW43's NAT/socket layer (host I/O).
- Not a JIT (0016 was rejected) and not a second emulator written in parallel and swapped in at once.
- Not a decision about Rust/Zig as the *language* (D4 records why not, from reasoning, not measurement).

## The phased plan

Each phase ends with something releasable (a wheel that still passes everything), has an **exit criterion** that is a
measurement or a test, and says what *stays in Python* afterwards. A phase is not started without a go-ahead.

### Phase 0 - guardrails before any C++ (no behaviour change)
- Commit the benchmark harness (the 8-instruction loop with its three variants; a boot-to-N-ms-simulated runner; the
  access-counting proxy) under `scripts/`, with the Section 1-4 numbers re-taken on the CI-comparable machine.
- Re-run Section 3 on **at least three more workloads**: CircuitPython boot, Pico W + CYW43 boot to a scan, a PIO + DMA
  program, and an idle USB CDC session. Fix the *order* of Phases 2-4 from the totals, not from MicroPython's idle REPL.
- Build the **differential harness**: record an MMIO access trace (address, size, value, simulated time) from the Python
  implementation of a block and replay it against another implementation, comparing every read value and the pending
  IRQ/pin state. This is the per-block parity oracle used in every later phase.
- Exit: harness merged, the workload table recorded in this record's progress log, parity-harness self-tested on a
  Python block against itself.

### Phase 1 - the skeleton: build, memory regions, the C++ bus
- Add the C++ core directory, `Extension(language="c++")` wiring, MSVC/clang/gcc compile in `cibuildwheel`, Emscripten
  and Android builds still configure (they exist today), `-fno-exceptions -fno-rtti` and the guards in CI.
- C++ **memory-region table + bus**, replacing the Cython `RP2040.read/write_uint{8,16,32}` fast path for flash/bootrom/SRAM/DPRAM
  with caller-owned pointers (zero-copy). Everything else still goes to the existing Python/Cython peripherals through the
  *window* mechanism - i.e. this phase already implements D2's windows, so every later block moves out of Python one at a time.
- The existing Cython `CortexM0Core` keeps running, calling the C++ bus.
- Exit: full `pytest` on pure and native green; boot MicroPython to the REPL; instr/s on the Section 1 loop not worse than today.
  (An improvement is likely, not required, in this phase.)

### Phase 2 - the CPU, clock and batch loop in C++
- Port `CortexM0Core` (~1.7k lines of Cython; `tests/test_instructions.py` is the oracle), `SimulationClock`/alarms and
  `_execute_batch()` (the loop and its time-budget logic) into C++; the Cython classes become thin wrappers with the same API.
- SIO (+ interpolator), PPB (NVIC, SysTick, SCB) and **TIMER** move here too: with the CPU they are ~98% of the accesses (Section 3), and
  moving the CPU without them would leave the hot path still crossing into Python.
- Exit: no speed target is promised (the proxy's ceiling is not a forecast) - the criterion is a measured table
  (synthetic loop and MicroPython-boot wall time) recorded here, with the full `pytest` suite green on both builds and
  the differential harness clean for SIO/PPB/TIMER traces.

### Phase 3 - pins, PIO and the flash path
> **Order amended 2026-10-04, then amended again the same day:** the first amendment put USB before this phase; the second
> (see the newest progress-log entry) defers USB instead. This phase is next. The content below is unchanged.

- GPIO/IO/pads and PIO + state machines (already Cython: 0031, 0047) to C++. Pin changes go to the caller's event ring,
  with a direct-callback mode for an `ExternalDevice` that must answer combinationally (SPI bit-bang) - *measured*, not assumed, which one
  each shipped device needs.
- XIP/SSI/QSPI (the flash bring-up the 3.7k SSI accesses are).
- Exit: `tests/test_pio*.py`, `test_gpio_pin.py`, `test_ssi.py`, `test_qspi_pads.py` green unchanged; PIO benchmark recorded;
  the CYW43 live boot (0027) still reaches a scan.

### Phase 4 - the communication blocks and DMA
> **Order amended 2026-10-04 (twice):** USB (controller + CDC host) is deferred to the end of this phase, and DMA is pulled
> forward to follow Phase 3 (its DREQ coupling to PIO is the reason). See the newest progress-log entry.

- UART, SPI, I2C, DMA (including its DREQ coupling to PIO/SPI/UART/ADC - the fragile part, see 0044), ADC, PWM, USB controller,
  watchdog, RTC and the small blocks (clocks, xosc, resets, psm, vreg, syscfg, sysinfo, tbman, busctrl). Order by Phase 0's profile; blocks nothing
  touches in any workload may be last.
- Python windows must still let a user replace any of these (D2): a test replaces one block with a Python one and boots.
- Exit: the whole of `tests/` green on both builds; no MCU register access reaches a Python handler in a boot unless a user window was attached.

### Phase 5 - gate: what of the existing Python moves too (CYW43 and friends)
- Decide from a profile, not from taste: is the CYW43 gSPI transfer path (PIO-clocked, a very high event rate per bit) the new
  bottleneck? If so port `bus.py`/`chip.py`; keep `nat.py` in Python. Re-check other `external/` devices the same way.
- Exit: a written decision per component, with the measurement behind it.

### Phase 6 - the WASM host
- Same sources built as a wasm32 reactor with no WASI imports (zig and wasi-sdk, as bclibc does), `_initialize` + a flat exported
  API, `get_layout()` for struct offsets, host `alloc`/fixed arena for the memory regions (the host reserves them *inside* the
  module's linear memory - this is the weaker zero-copy: a Python host copies via `memory.read/write`; a JS host can view it).
- Imports only for Python-window / alarm trampolines; a **headless profile** (none) for JS/Dart/browser.
- Parity test native<->wasm like bclibc's `tests/wasm_parity/parity.py`: identical register/memory/pin-event traces for the same firmware.
- Integrate with `wasmhost` as a fallback tier below the Cython extension (the Python peripherals/boards keep working through imports,
  at 4-200 us per call - fine for rare windows, not for hot blocks, which by now are all in the module).
- Exit: MicroPython boots to the REPL on wasmtime and node from the same wasm blob; per-backend instr/s and the batch-size
  requirement (>=10k instr per call) recorded; Pyodide/Emscripten Cython build (already configured in `setup.py`) re-checked, since that
  path keeps the same-process zero-copy.

### Phase 7 - decide the pure-Python implementation's fate
- With C++ complete and the WASM tier measured, either keep the frozen pure-Python as the PyPy/no-toolchain fallback, or retire it
  and rely on wasm + a wasm runtime. Decided from (a) PyPy on a **real boot** (not measured yet), (b) which platforms have neither
  a wheel nor a wasm runtime, (c) the maintenance cost of the frozen copy.

## The per-block recipe (every block in Phases 2-4)

1. Record an access trace of the Python block from a real boot (Phase 0 tooling).
2. Write the C++ block behind the D2 window interface, with its register table as data.
3. Replay the trace against both; diff every read and the IRQ/pin side effects.
4. Put a Cython facade in front with the *same* Python attributes/methods; keep the Python implementation selectable (D6).
5. Run `uv run pre-commit run --all-files` (both builds) and the live-boot CI for the affected family.
6. Measure (Section 1/3 tooling) and append the numbers to this record's progress log.
7. Leave the Python block in place: it stays as the oracle until Phase 7.

## Risks and open questions

- **Re-entrancy.** A Python window handler may call back into the core (`set_irq`, pin writes, scheduling an alarm) mid-`run()`.
  The core's state must stay consistent across such a call; design and test this in Phase 1, before blocks pile on.
- **Buffer lifetime.** A `bytearray` resized while a pointer is held is a use-after-free. The Cython wrapper must hold the buffer
  (and for `bytearray`, a memoryview export, which blocks resizing) for the core's life; board code must not swap the region mid-run.
- **Callbacks and threads.** `schedule_threadsafe()` and the engine-room thread (0026, 0030) stay in the Python/Cython layer; the C++
  core is single-threaded by contract. Free-threaded builds need an explicit look.
- **Toolchains.** C++17 is the language floor (decided 2026-10-03); it must compile on MSVC, the macOS/Android/iOS/Emscripten wheel builds, and the compiler flags differing per toolchain.
  `-ffp-contract=off` for any float path (SIO divider note in `sio.py`: it can yield fractional values today - pin the semantics in Phase 2).
- **Alignment and endianness.** Use `memcpy` loads; assume little-endian hosts and say so.
- **Type stubs.** The `.pyi` files under `native/` and mypy coverage must follow every facade.
- **The measurements are a ceiling.** Phase 2's exit criterion is a measured table because the C++ proxy's ~20x is not a promise.
- **PyPy on real firmware is unmeasured.** It decides Phase 7, and could go either way.
- **Wasm interpreters.** `wasm3` and JavaScriptCore backends were not measured; an interpreter backend will be far slower than wasmtime/node.
- **Open (raised 2026-10-04): should the core own its small fixed buffers instead of taking them from Python?** Today every region is caller-owned (a Python `bytearray`, pointer handed to C++; decision 4). The sizes are in fact fixed - SRAM 264 KiB, USB DPRAM 4 KiB,
  boot ROM 4 KiB, flash 16 MiB in the emulator today - so members/statics of the core are an option. Not the stack: 16 MiB and 264 KiB do not fit one (1-8 MiB thread stacks, 1 MiB on Windows, 64 KiB linear stack in wasm); "inside C++" would mean static/member storage.
  For: no buffer-lifetime hazard (the `bytearray`-resized-under-a-pointer use-after-free above), no export lock to hold, constant sizes the compiler can fold, and in wasm plain static data instead of host-reserved regions. Against: `mcu.sram`/`mcu.flash`/`usb_dpram` are
  `memoryview`s over Python buffers that board code, `load_flash`, tests and filesystem images write to (they could become views of C++ memory, still zero-copy, but it touches all of them); 16 MiB of flash per chip instance in a process that builds hundreds of chips in tests;
  boards that want a different flash size. The expected speed difference is small (the buffer pointer is loaded once per batch and the constant bases are already folded) - a hypothesis, not a measurement.
  Measurement plan, before deciding: in `scripts/bench/cpp_bus_fetch.cpp`, the same access mix (62% 16-bit flash fetches, 28% SRAM words, 10% flash words, cache-resident and 2 MiB/264 KiB working sets) through caller-owned pointers vs static arrays of the same size; if the gain is under a few percent
  the answer is "no". Leading option if it is not: SRAM, DPRAM and boot ROM as core members, flash stays caller-owned (large, board-dependent). Not done now; revisit after Phase 3, since it reaches most of the Python-facing surface.

## Progress log

- 2026-10-05: **The SSI (the flash command path) is C++ (`core/ssi.hpp`, shell `native/_ssi.pyx`): CircuitPython boot 1.16-1.23 s -> 0.92-0.97 s (~1.25x); the unfinished item of Phase 3 is done except the QSPI pads' IO/PADS windows (#29).**
  - Built as in the design note: `SsiBlock` holds the register file, the command state and the whole JEDEC subset; it listens to QSPI_SS as a **direct listener** of the native `GPIOPin` (a chip-select edge never enters Python; a non-native pin gets a Python listener that forwards the level) and works on the chip's flash buffer **by pointer** (`RP2040._native_flash_address()`; the buffer is allocated once and only refilled in place). The bounded replacements for the reference's growing containers: the command keeps its first 260 bytes and a count of the bytes shifted, the RX queue is a ring of 4096 (drop-oldest above it - undefined in the reference, kept out of the oracle's domain). `peripherals/ssi.py` is now the facade over `_ssi.py` (the reference, with the register/command constants re-exported); the pure chip builds `_ssi.RPSSI` directly, as it does the pure DMA.
  - Verification: (1) the lockstep differential `tests/utils/ssi_diff.py` (built and self-proven first: 5 perturbations of the candidate, 14 logic mutants of the reference, coverage of every opcode, erase/program with and without the write-enable latch, reads past the end, CS toggles with an empty command, resets mid-command) - 100 seeds x 8000 steps on top of the CI set, 0 differences; (2) `tests/cpp/test_ssi.cpp`, directed checks on a real C++ `PinBank` for QSPI_SS, 93 mutants of `ssi.hpp`, 88 killed, the 5 survivors equivalent (two chip-select edge forms and the pin-state test that differ only for pin states an always-output-enabled pad cannot have, `apply_command` of an empty command, a `<` that is `<=` in disguise) or an invalid mutant; (3) `test_ssi.py`, `test_chip_reset.py`, `test_xip_ctrl.py` and the rest on both builds; (4) the live boots of every cached family - MicroPython 1.21 and 1.23, CircuitPython 8.0.2 (hard reset and RESET button fail with or without this change, CI skips them), 9.2.9, 10.2.1, Kaluma 1.2.1 (flash-rw), Pico SDK `hello_usb`; (5) the pin-event order hashes unchanged (`mp-idle`, `pio-dma`, `picow-scan`; `cp-boot` differs from `main`'s from event 51 exactly as it did before this change, see the USB entry); (6) `pre-commit` on both builds.
  - Measured, A/B interleaved against the commit before the SSI, three rounds: CircuitPython boot 1.157 / 1.158 / 1.227 s -> 0.924 / 0.966 / 0.951 s; MicroPython boot (a few ms of SSI) and the sleeps unchanged.
  - Not in this step, on purpose: XIP_CTRL (three registers, zero accesses in every workload), the QSPI pads' IO_QSPI/PADS_QSPI windows (with the shared pin banks, #29), and any timing of the flash (the SSI still answers instantly).

- 2026-10-05: **Phase 3's unfinished item, "XIP/SSI/QSPI (the flash path)": inventory and design note - nothing in C++ yet.** The plan listed it under Phase 3 but `core/` has none of it; the profile of the same day (SSI = 408k reads + 137k writes in a CircuitPython boot, ~8% of its profiled time; XIP_CTRL and the QSPI pads' IO/PADS windows do not appear at all) is why it is next. Order of the whole remainder is in the entry above.
  - Three pieces, only one is hot: (1) **SSI** (`peripherals/ssi.py`, 307 lines) - the flash command path: boot2 and the bootrom's `flash_*` helpers drive it, it frames a command by the **QSPI_SS pin** (not SER/SSIENR - the Pico SDK bit-bangs CS through IO_QSPI), shifts bytes through DR0 and answers a small JEDEC subset (write enable/disable, status 1/2, write status, page program, 4K/64K erase, read data, JEDEC ID); erase/program are applied to the flash bytes when CS deasserts. (2) **XIP_CTRL** (`xip_ctrl.py`, 95 lines): three registers and constants, no cache modelled, zero accesses in any workload - stays Python. (3) **IO_QSPI / PADS_QSPI** windows: part of the deferred shared-bank item (#29), not of this step. XIP *reads* are already the C++ memory region.
  - Shape of `core/ssi.hpp` (`SsiBlock`): the register file (CTRLR0/1, SSIENR, BAUDR, TXFLR as a plain stored word, SR's constant TFE|TFNF + RFNE from the RX queue, IDR/VERSION constants, DR0, RX_SAMPLE_DLY and TXD_DRIVE_EDGE masked to 8 bits, SPI_CTRL_R0), the command state (`write_enabled`, `cs_asserted`, the bytes of the current command, the RX queue) and the whole flash command set. Bound to the **native `GPIOPin` of QSPI_SS by a direct listener** (the gSPI shifter's mechanism: the CS edge never enters Python) and to the **flash buffer by pointer + size** (caller-owned like every region; `RP2040._flash` is allocated once and only ever refilled in place, so the pointer is stable for the chip's life - a new `_native_flash_address()` on the chip, like `_native_bus_address()`). Host: warnings only (unimplemented register read/write, as every block). A non-native QSPI pin or chip gets the Python listener, as the gSPI shifter does.
  - What the reference does that the C++ must keep: DR0 written with SSIENR set and CS asserted pushes `_shift_byte(value & 0xFF)` onto the RX queue; with CS *de*asserted it still pushes 0xFF (firmware's TXFLR/RXFLR flow control would spin forever otherwise); with SSIENR clear it is ignored. CS asserting starts a fresh command **and empties the RX queue**; CS deasserting applies the command (`_apply_command`) - erase aligns the address down to the sector/block and fills 0xFF only if write-enable was set (WEL is cleared either way); program ANDs the data into the flash (NOR can only clear bits) for at most 256 data bytes; READ_DATA returns `flash[address + pos - 4]` (0xFF past the end), READ_STATUS_1 the WEL bit, READ_STATUS_2 a permanently set QE, JEDEC_ID (0xEF, 0x40, 0x15) then 0, any other byte 0xFF. `reset()` clears the registers and the command but **re-syncs `cs_asserted` from the pin** (an always-output-enabled pad with nothing driving it resolves LOW = already asserted; hard-coding False once hung the bootrom, see the reference's own comments) and never re-adds the listener.
  - The reference's two unbounded containers (`_rx_queue` deque, `_tx_buffer` bytearray) cannot be unbounded in C++ (no allocation). What is actually used of them is bounded: the command keeps its first 4 bytes (opcode + address) and at most 256 data bytes, plus a *count* of bytes shifted so far (READ_DATA and JEDEC_ID index by position, a read of any length needs only the count); the RX queue is a ring. Capacity chosen 4096 entries (the hardware FIFO is 16; the reference lets a test push without reading, `tests/test_ssi.py` pushes a few) and the behaviour above it - **undefined in the reference, defined here** - is to drop the oldest, documented and kept out of the oracle's domain like the other two such cases (SIDESET_COUNT > 5, a DREQ >= 64).
  - Oracle (before any C++): the PIO/DMA method - a lockstep differential `tests/utils/ssi_diff.py` of the pure `RPSSI` against the facade's, one generated stream over both chips: register writes/reads of every offset (so the unimplemented ones' warnings are compared), DR0 traffic made of valid commands of every kind with random addresses and data and of random bytes, CS toggled the way firmware does it (the QSPI_SS pin's `ctrl` override forced low/high through the pin API, plus an externally driven level), SSIENR on/off, `reset()`, writes of 0xFF-filled and patterned flash around the addresses used. After each step it compares the register file, `rx` level and contents, the command-state fields, the whole flash (hashed) and the warning log. Self-proof as before: perturbations caught, logic mutants of the reference caught, coverage asserted (every opcode, erase with and without WEL, program over programmed bytes, reads past the end, CS toggles with an empty command, reset mid-command). Plus a replay of the SSI traffic of real boots (MicroPython flash-rw, CircuitPython boot) recorded with the existing MMIO tooling.
  - The rig has one trap to design out: both chips' SSIs listen to their own QSPI_SS, and the *pure* rig replaces `chip.ssi` after construction - the replaced SSI's listener must be removed (it would apply every command to the same flash a second time). The shell gets a `_detach()` for that, and the pure reference's equivalent is `remove_listener`.
  - Not in this step: XIP_CTRL (no accesses), IO_QSPI/PADS_QSPI (with #29), and anything about *timing* - the SSI answers instantly, as now.

- 2026-10-05: **USB ping-pong decided and changed (variant B): the CDC host leaves an OUT arm waiting until it has data. 1 s of idle sleep 2.5 s -> 0.05 s of wall time; Pico W scan 2.0 s -> 1.0 s. It was tried on its own branch first; nothing but one test depended on the old behaviour.**
  - The question (record 2026-10-04): the CDC host answered every OUT-endpoint arm after 10 us, with an empty buffer when it had nothing to send, so an idle firmware and the host ping-ponged ~35k times per simulated second (USB was ~30% of an idle run, 11-16% of the others). A real device stays armed until the host has something. The user asked that B be shown not to break anything *before* it is taken.
  - How it was checked (experiment branch `experiment/usb-park-empty-reads`, a switch to run both from one tree, runs interleaved twice): a local battery of the CI's live boots on every cached family - MicroPython 1.21 and 1.23 (hello, SPI, flash read/write, reset cause, hard reset, RESET button, chip reset, control lines), CircuitPython 8.0.2 / 9.2.9 / 10.2.1 (banner, hard reset, RESET button, control lines), the WS2812 boot decode, Kaluma 1.2.1 (hello, flash-rw), and a Pico SDK 1.3.1 `hello_usb` (plain TinyUSB, no REPL) built locally - plus the CI of the branch. Everything passed identically except **`tests/control_lines_run.py` on every CircuitPython version** (also red in CI): "DTR never changed as the guest saw it (60 connected, 0 not)". (CircuitPython 8.0.2's hard-reset and RESET-button runs fail with and without B; CI skips those two for it.)
  - Cause: a test, not the emulation. The test dropped and raised DTR at 0.5 s and 1.5 s of *guest* time but watched the guest clock by polling it from the host's event loop (`asyncio.sleep(0.001)`). That only works while the guest is slower than the poll. With B an idle guest jumps from timer to timer and the whole 3 s sampler ran in 0.077 s of wall time; the first poll saw a clock already at 2.17 s, past both windows. With the same toggles armed as clock alarms in guest time the samples are identical with and without B on all three versions (40 connected, 20 not). The fix is its own commit and is independent of B (a faster host will break the polling version on `main` too): the toggles are now alarms created in the engine room through `schedule_threadsafe`.
  - Taken as its own commit, no environment switch: `usb/cdc.py` parks the arm (`_parked_out_reads`), the first byte the host sends completes the oldest arm after one controller read delay (so a burst is one packet, as the ping-pong made it), a reset forgets the parked arms; four tests in `tests/test_cdc.py` (three fail on the old code).
  - **Consequence for the pin-event order hashes (a property of the measurement, not a divergence):** the run stops on wall time, and an idle guest now covers far more simulated time per wall second (`cp-boot`: 68-95 s simulated in 1.3 s wall, against 3.35 s before), so the tail of the stream after the last script is longer and, for `cp-boot`, differs from run to run (the guest blinks its LED while idle). The streams still agree event for event over the common prefix - 4601 events of `pio-dma`, 50 of `cp-boot` - and `mp-idle` and `picow-scan` hash exactly as before. From now on the comparison is of the common prefix (the new baselines: `pio-dma` da505563e66d0d4664d928a31cf59805 over the whole run, the others as listed above); `scripts/bench/pin_events.py --compare` still reports the first divergence and the lengths.
  - Measured, A/B interleaved, two rounds: `mp-idle` `time.sleep_ms()` 1 s 2.52-2.59 s -> 0.051 s (~50x), `cp-boot` sleep 2.51-2.59 s -> 0.046 s (~55x), Pico W scan 1.99 s -> 1.01-1.06 s, PIO+DMA 0.143-0.147 s -> 0.113-0.121 s; Kaluma flash-rw 3.3-3.7 s -> 1.6-1.8 s.
  - Not covered here, left to CI: Kaluma and Pico SDK on pypy/3.14 runtimes, MicroPython 1.29, CircuitPython on Pico W (`chip_reset --cyw43`).
  - The USB *controller* and CDC host port to C++ stays deferred; this change makes it a smaller question (the ping-pong was most of the USB cost on an idle run).

- 2026-10-05: **Zero-count DMA trigger decided and fixed (reference first, then the C++): a trigger with `TRANS_COUNT` 0 starts nothing.**
  - The open question of the DMA entry (what a count of 0 should do) was looked up instead of guessed. pico-sdk (`hardware/regs/dma.h`, `hardware_dma/dma.h`, master): a *nonzero* write to a trigger register reloads the counter and starts the channel, a *zero* write is the "null trigger" (raises the IRQ in IRQ_QUIET mode - already what the reference does); nothing about a zero reload value at a trigger, and BUSY is "high when the channel starts a new transfer sequence, low when the last transfer of that sequence completes", which a sequence of no transfers does not define. `rp2040-emu` (`0x4D44/picoem`, `crates/rp2040-emu/src/dma.rs`, differentially checked against QEMU and real silicon in general, though nothing there says this case was): `trigger_channel()` returns without doing anything when the count is 0 (no BUSY, no IRQ, no chain), and a chain only reloads a target whose reload value is above 0. `rp2040-pio-emulator` has no DMA.
  - What was there: the reference set BUSY and scheduled nothing, so the channel stayed BUSY for good (it ignored every later trigger), and a later CTRL rewrite or DREQ edge ran one transfer that took the count to -1. No source supports that; the datasheet (not the SDK) or a board would settle silicon, so this follows the one implementation that decided. **Behaviour change, in its own commit** (`peripherals/_dma.py` + a regression test in `tests/test_dma.py` that fails on the old code), then the C++ (`DmaChannel::start`) and the oracle: zero counts are back in the differential's generator (also through the xor/clear aliases), and a mutant that keeps the old behaviour is caught.
  - Unchanged on purpose: a CTRL rewrite while a channel runs re-arms its alarm, so on its *last* transfer one more transfer runs on the finished channel and the count goes to -1 - that is the reference's (and now the C++'s) behaviour, pinned by a C++ check; only the representation differs (Python -1, register word 0xFFFFFFFF), which the oracle's `BusView` normalises.
  - Verification: differential 120 seeds x 8000 steps on the new domain, 0 differences; 129 mutants of `dma.hpp`, 123 killed (the same six equivalent survivors).

- 2026-10-05: **The DMA is C++ (`core/dma.hpp`, shell `native/_dma.pyx`): Pico W scan 2.09 s -> 1.71 s (1.22x), PIO+DMA 0.143 s -> 0.124 s (1.15x) over the Python DMA; all pin-event order hashes equal `main`'s.**
  - Built as designed: `DmaBlock` + 12 `DmaChannel`s hold the C++ `Bus` and `Clock` directly (a transfer is a bus read and write, the channel's alarm a node of the C++ clock); host callbacks only for the two interrupt lines, `clk_sys` and warnings; the shared parked-failure flag is checked after every bus access and host call, so a failure stops a transfer where the reference's exception would. `clk_sys` is not asked when the period is zero anyway (a stalled DREQ channel is rescheduled after every transfer). The shell gives `channels` (views with the reference's attribute names), `dreq` (read-only view of the asserted ones), `int_raw`, the window, `set_dreq/clear_dreq`; the chip lends its bus through `_native_bus_address()`. The PIO's DREQ trampoline now calls the C++ DMA directly when the chip's DMA is the native one (still looked up on every call, so a test double works). `pio_diff` replaces `chip.dma` with a recorder instead of patching its methods (a native object has none to patch).
  - Verification: (1) the lockstep differential - 120 seeds x 8000 steps on top of the CI set, 0 differences (a step that never ends - a channel that rewrites its own registers into a zero-delay loop, which the reference does too - is declared out of the domain by a log-size guard); (2) `tests/cpp/test_dma.cpp`, directed checks of every behaviour on a real C++ Bus+Clock, 128 mutants of `dma.hpp`, 122 killed, the 6 survivors equivalent (the write-clear error bits and the DBG counter are never set by anything, ring size 0, BSWAP on bytes, a redundant cancel in reset, an out-of-range chain read the sanitizers do not see); (3) `test_dma.py`, `test_pio*.py` and the rest on both builds; (4) order hashes of the pin events equal `main`'s on `mp-idle`, `pio-dma`, `cp-boot`, `picow-scan`; (5) `pre-commit` on both builds.
  - Quirks of the reference kept (and pinned): a channel started with count 0 stays BUSY and a later transfer takes the count to -1; a CTRL rewrite while a channel runs re-arms its alarm, so a stale transfer can run once more on a finished channel; reset leaves BUSY. Defined differently on purpose: addresses wrap at 32 bits (the reference's ints grow), a DREQ number of 64 or more is ignored. (Settled afterwards: a trigger with a count of 0 starts nothing - see the next entry in this log, above.)
  - Measured, A/B interleaved against the commit before the DMA (three rounds each, best of two runs): Pico W scan 2.028 / 2.094 / 2.135 s vs 1.775 / 1.700 / 1.654 s; PIO+DMA 0.136 / 0.141 / 0.153 s vs 0.126 / 0.126 / 0.121 s. The gain is smaller than the ~20% profile share suggested because the DMA's remaining cost is mostly the bus accesses and the alarm traffic it always had; what is left on the scan is USB (~14%) and `GSPIBus._on_word`.

- 2026-10-05: **DMA oracle built and self-proven: the lockstep differential (`tests/utils/dma_diff.py`, `tests/test_dma_diff.py`), before any C++.** Method of the PIO oracle: two chips, one with the pure `_dma.RPDMA` and one with whatever the facade gives, one generated stream of operations, everything observable compared after each step - the whole register file (every channel's addresses/count/control/DBG registers and the controller's), the clock's current time and next alarm (a transfer scheduled at a different instant is a difference at once), the asserted DREQs, the memory the channels work in, the probe's state, and the ordered logs of interrupt-line changes, bus warnings and probe accesses; an exception on one side only is a difference.
  - The **probe** is a peripheral in an unused window (0x50400000) a channel can read from and write to; writing it raises or clears a DREQ, so the re-entrancy of a transfer that writes a PIO FIFO (the PIO answers with a DREQ change while the DMA is still inside `transfer()`) is exercised without a PIO in the harness.
  - Self-proof: (1) 8 seeds x 3000 steps agree (reference vs the facade's, which is the same class today); (2) six perturbations of the candidate through public API (a moved read pointer, a spurious DREQ, a flipped raw-interrupt bit, an extra clock tick, a changed INTE, a poked word of a channel's memory) are each caught at the step they were made; (3) seven *logic* mutants of the reference (chains ignored, IRQ_QUIET ignored, rings ignored, BSWAP ignored, `set_dreq` not waking channels, the TIMER3 shift "fixed" to 16, `reset` clearing the DREQs) are each caught within three seeds; (4) coverage is asserted - every transfer function incl. both byte-swaps, rings on read and write, fixed addresses, quiet and interrupting completion, valid and invalid chains, aborts, starts of running/disabled channels, permanent / DREQ-asserted / paced / stalled scheduling, thousands of bus warnings, hundreds of probe writes.
  - What the generator keeps out of the domain, and why (both found by running it): (a) a **chain back to a lower channel** (A chains to B chains to A on a PERMANENT TREQ) is a cycle of zero-delay transfers that never lets a clock tick return - a loop the hardware has too, so chains go to the same or a higher channel (12..15, which do not exist, included); (b) a **TRANS_COUNT of 0 at a trigger** - the reference starts such a channel (BUSY set, nothing scheduled) and leaves it BUSY for good, and the next CTRL rewrite or DREQ edge runs a transfer that takes the count to -1 (a register read the Cython bus then rejects with `OverflowError`). This is an existing quirk, **not fixed here** (what the silicon does with a zero count is a decision, and it changes behaviour): the C++ keeps a signed count so the state evolves as in the reference, reads it back as 0xFFFFFFFF, and the oracle keeps counts at 1 or more. **Open question for the user:** what a count of 0 should do (hardware: the channel completes at once and raises its interrupt - to be confirmed against the datasheet before anyone changes it).
  - Also found: a fresh pure `RPDMA` starts with no asserted DREQs, whereas the chip's own DMA already holds the levels its peripherals asserted at construction (TX FIFOs ready) - the harness copies them, as a DMA `reset()` would leave them.

- 2026-10-05: **Phase 4 starts with the DMA: design note (nothing in C++ yet).** `peripherals/dma.py` is split into the pure reference `_dma.py` (unchanged, stays the oracle) and a facade `dma.py` (re-exports for now; it gains the native import with the shell, like `pio.py`).
  - Inventory of what the reference does (this is what the C++ must reproduce, quirks included): 12 channels, each with `read/write addr`, `trans_count` + `reload`, `ctrl`, `dreq_counter`, `treq_value`, `data_size`, `chain_to`, `ring_mask`, a transfer function (8/16/32 bit, with BSWAP variants) and one clock alarm. `transfer()` order: perform the transfer -> advance read/write address (ring wrap) -> `trans_count -= 1` -> either reschedule, or finish (clear BUSY; unless IRQ_QUIET raise the `int_raw` bit and re-evaluate the two IRQ lines; trigger `chain_to` if it is another channel).
    `schedule_transfer()`: DREQ asserted (or PERMANENT) -> alarm at +0; otherwise a pacing timer's period (x1000 ns) if non-zero. The controller: `int_raw`, INTE0/1 and INTF0/1 (16 bit), four pacing timers, a `dreq` set that `reset()` deliberately leaves untouched, `set_dreq` waking only channels with that TREQ and only on a rising edge, and a quirk in TIMER3's period (`urshift(x, 36)` is `>> (36 & 31)`).
  - Shape of the C++ (`core/dma.hpp`): like the TIMER it holds `Bus*` and `Clock*` directly - a transfer is a bus read plus a bus write and must not cross into Python; the bus parks a failure, the DMA checks the pending flag after each access and stops the channel the same way the reference does on a raised error. Host callbacks only for what leaves the chip: the two IRQ lines and (rarely read) `clk_sys`. `dreq` becomes a 40-bit mask; the public `dreq` attribute stays a dict-like view for compatibility. The PIO's DREQ host callback is then pointed *directly* at the C++ DMA (the FIFO level -> `set_dreq/clear_dreq` path never touches Python), which is where the PIO+DMA workload should gain.
  - Re-entrancy to watch: a DMA transfer can write a PIO window (TX FIFO), which can synchronously assert/clear a DREQ and so call back into the same DMA while it is inside `transfer()`. The reference tolerates this because everything is read back from the channel afterwards; the C++ must not hold references across the bus call, and the harness must contain such a case.
  - Oracle first (task order): `tests/utils/dma_diff.py` - the PIO method again: one generated stream (register writes/reads with every alias, triggers, `CHAN_ABORT`, `MULTI_CHAN_TRIGGER`, DREQ set/clear from a probe peripheral at an unused window that logs the writes it receives, clock advances of random size, `reset()`), two chips (pure `_dma.RPDMA` vs the facade's), comparing after every step the readable register file, per-channel state, and the ordered logs of IRQ lines and probe writes. It must prove itself before C++: self-replay clean, perturbations of the reference caught, coverage asserted (every data size, ring, chain, quiet, abort, error paths, paced and DREQ-driven). Real PIO is deliberately not in the harness (the rebinding problem seen with the PIO oracle); the probe covers the coupling.
  - Verification chain after the port: differential (CI set + a long run), `test_dma.py`/`test_pio*.py` on both builds, the pin-event order hashes equal `main`'s (mp-idle 279c81ed..., pio-dma ea5acc8e..., cp-boot 099e7bf4..., picow-scan f45f9c11...), the scan still finds `RP2040PY-GUEST`, pio-dma prints `dma-done`, an interleaved A/B benchmark against the previous commit, mutation testing of `dma.hpp`.

- 2026-10-05: **Phase 3 finished: the PIO is C++ (`core/pio.hpp`) - Pico W scan 3.7 s -> 2.1 s (1.7x), PIO+DMA 0.172 s -> 0.134 s (1.3x) over the Cython PIO; ~4.6x over `main` on the scan.**
  - What was built (as in the 2026-10-05 design note): `core/pio.hpp` - `PioBlock` (instruction memory, `irq`, FDEBUG/stall words, pin images, IRQ0/1 enable/force, the pacing of record 0063) with four `PioMachine`s (all instruction semantics, the wait kinds, EXEC, autopush/autopull, the divider) and `PioFifo` reproducing `utils/fifo.FIFO(4)`
    exactly (reset clears only the count); the register file with the set/xor/clear alias decode and `raw_write_value`; `PioHost` for what leaves the block (IRQ lines, DREQs, logger, the "no Simulator" fallback, and any non-native pin). `native/_pio.pyx` is now a shell: `RPPIO`, `StateMachine`, the FIFOs and the instruction memory are *views* of the C++ state with today's attribute names,
    the block registers as a native bus window like TIMER and SIO, the batch loop steps it with one C++ call (`_pio_advance` calls `PioBlock::advance` directly), and the 30 native pins are read and updated through their C++ banks. `native/_state_machine.pyx/.pxd/.pyi` are gone (the facade `peripherals/state_machine.py` now takes `StateMachine` from `native/_pio`);
    the gSPI shifter's direct sources point into the C++ block.
  - Two things the C++ defines that were undefined before: a `SIDESET_COUNT` above 5 (not a hardware configuration; the pure code raised, the Cython shifted by a platform-dependent amount) is treated as 5, and a runaway chain of EXEC instructions each executing the next is cut after 1024 (the Python would have overflowed its stack, the Cython crashed).
  - Verification, in the order of the design note: (1) the lockstep differential of `tests/utils/pio_diff.py` - found a real bug in my first C++ within seconds (an opcode wider than 16 bits, which `MOV EXEC, PINS` produces, must match no instruction, not SET) - then clean on 150 seeds x 8000 steps (~300 s) and on the CI set; (2) `tests/cpp/test_pio.cpp`, 14 directed tests, 35 mutants of `pio.hpp` killed
    (the first round let 11 survive: a drained FIFO's start index, OUT shifting left, the wake-up reschedule, the arrears threshold at 8/9 cycles, CTRL enable re-arming, FDEBUG through an alias, the INTE mask, the side-set clamp, pins 30/31, the interrupt re-announce on RX empty - each now has a test); (3) 165 PIO/CYW43/pin tests on both builds; (4) the order hash of the pin events equals `main`'s on
    `mp-idle`, `pio-dma`, `cp-boot` and `picow-scan` (4,798,482 events), the scan finds `RP2040PY-GUEST` and the DMA workload prints `dma-done` as before; (5) `uv run pre-commit run --all-files` green on both builds.
  - Measured, A/B interleaved against the commit just before (Cython PIO + C++ gSPI shifter), three rounds of best-of-two: Pico W scan 3.675 / 3.638 / 3.887 s vs 2.076 / 2.235 / 2.152 s; PIO+DMA 0.172 / 0.177 / 0.167 s vs 0.136 / 0.137 / 0.131 s.
  - Where this leaves the project's headline numbers on the Pico W scan: `main` 9.83 s -> pin shell (neutral) -> gSPI shifter 3.4 s -> C++ PIO 2.1 s. The remaining Python on that path is the DMA (`transfer`, `set_dreq`, `schedule_transfer`, the FIFO class: ~25% of the earlier profile), the USB host side, and `GSPIBus._on_word` (the protocol, once per 32-bit word). Phase 4 starts with the DMA (its DREQ coupling to the PIO is now a host callback on a C++ block).

- 2026-10-05: **PIO oracle built and clean: the lockstep differential (`tests/utils/pio_diff.py`, `tests/test_pio_diff.py`). It found two real bugs in the pure-Python reference before a line of C++ exists.**
  - What it is: the same generated stream of operations - bus writes/reads of every PIO register (with the set/xor/clear aliases), random programs of valid and fully random 16-bit opcodes, state-machine configuration, CTRL enable/restart/clock-divider restart, FIFO traffic, IRQ and IRQ_FORCE, `SM_INSTR` execution, `advance(n)` with small and huge `n` (the arrears path), `reset()`, pin FUNCSELs and external pin drives - on two chips,
    one with the pure-Python PIO (`_pio.py` + `_state_machine.py`, built explicitly, because the facade would give it the native machines) and one with whatever the facade gives. After every step it compares the whole readable register file, every machine's state and FIFO contents, the block's pacing state, and the ordered logs of what left the block (interrupt lines, DREQ calls, pin-listener events, warnings), reporting the first difference.
  - Found: (1) the pure-Python `OUT` and `IN` left shifts did not mask to 32 bits (`output_shift_reg <<= n`, `input_shift_reg <<= n`): the registers grew past 32 bits, which the native machine (C `unsigned int`) never did - fixed in its own commit with regression tests that fail on the old code; (2) a `SIDESET_COUNT` above 5 (not a configuration on the hardware): the pure code raises `ValueError: negative shift count` where the Cython behaves per the platform's shift semantics. (2) is not a behaviour to preserve - the generator stays inside the valid domain (0..5), and the C++ will define it (it will be pinned and documented when the machine is written).
  - Result: pure vs the current Cython PIO, 0 differences on 150 seeds x 8000 steps (one-off, 335 s) and on the CI set (6 seeds x 3500 steps, ~8 s native / ~18 s pure). Self-proofs, as for every oracle here: five damaged candidates (a flipped `x`, a changed pin image, a changed FIFO, an extra `advance`, a flipped IRQ flag) are each caught at the exact step the damage is done; and the run's coverage is asserted
    (every instruction kind >= 150 executions over 4 seeds, every JMP condition >= 15, all three WAIT sources >= 30, PUSH and PULL, ~1000 DREQ calls, ~3000 interrupt-line changes, ~160 pin events, the unimplemented-register warnings, and machines parked on each kind of wait).
  - Next: `core/pio.hpp` - the machine semantics, the FIFO exactly as `utils/fifo.FIFO` behaves (including that `reset()` clears only the count), and the block - with standalone C++ checks, then the Cython views and the bus window, always against this oracle.

- 2026-10-05: **Design note: the PIO (`RPPIO` + four `StateMachine`s) in C++ (`core/pio.hpp`). Not implemented; read from `native/_pio.pyx`, `native/_state_machine.pyx`, `peripherals/_pio.py`, `utils/fifo.py`, the batch hook in `native/_simulator.pyx` / `core/batch.hpp` and what else touches a PIO.**
  - What exists. `RPPIO` (480 lines of Cython) owns the block: 32 words of instruction memory, `irq`, `fdebug` + the two stall words, `input_sync_bypass`, the pin images `pin_values`/`pin_directions` (+ the `old_*` copies `check_changed_pins` diffs), the IRQ0/IRQ1 enable/force words, and the pacing of record 0063 (`cycle_fp`, `next_due_fp`, `backlog_drops`,
    `NEVER_DUE`, `MAX_ARREARS_FP`). Each `StateMachine` (831 lines) owns x, y, pc, the two shift registers and counts, the three config words (`exec_ctrl`/`shift_ctrl`/`pin_ctrl`), the divider (`div_fp`, `next_due_fp`), the wait state (`waiting`, `wait_type/index/polarity/delay`), the pending `EXEC` opcode and two 4-entry FIFOs
    (the plain-Python `utils/fifo.FIFO`). Both are mechanical transcriptions of the pure-Python `_pio.py` / `_state_machine.py`, kept in lockstep by the existing tests.
  - Why it costs what it costs (the callgrind finding): inside the Cython the instruction semantics are cheap, but every crossing is a *Python call*: `self.pio.pin_values_changed(...)`, `self.pio.check_interrupts()`, `self.rx_fifo.push(...)`/`.full` (FIFO is a Python class), `self.rp2040.dma.set_dreq(...)`, `self.rp2040.gpio[i].input_value`, `self.rp2040.gpio_values` (a property that loops over 30 pins),
    `gpio[i].check_for_updates()`. Moving the *block* and its machines into one C++ object turns the first three into field accesses and leaves only the genuinely external calls.
  - The calls that leave the block, and what each becomes: (1) IRQ lines (`rp2040.set_interrupt(first_irq, ...)`, `check_interrupts`) -> a host callback; (2) DREQ (`dma.set_dreq`/`clear_dreq`) -> a host callback, Python DMA until Phase 4; (3) pin reads (`input_value` for `jmp pin`/`wait gpio`/`wait pin`, `gpio_values` for `in pins`) and `check_for_updates` after a pin-image change ->
    direct calls on the pins' C++ banks (`PinBank*` per GPIO, bound at construction from the native `GPIOPin`s; a pin that is not native keeps going through a host callback); (4) `pio.error(...)`/warnings -> a host log callback; (5) CTRL starting a machine when no `Simulator` owns the chip (the asyncio `run()` fallback) -> a host `started` callback, the task stays in the shell.
  - Shape. `core/pio.hpp`: `PioBlock` with four `PioMachine`s, 32 instruction words, fixed 4-entry FIFOs that reproduce `utils/fifo.FIFO` exactly (note: `reset()` clears only the count, the start index survives, `push` masks to 32 bits and is ignored when full, `pull` on empty returns 0), the register file (`read32`/`write32` incl. the set/xor/clear alias decode of `write_uint32_atomic` and `raw_write_value`),
    `advance(cycles)`/`_run_due`/`recompute_due`/`notify_due`/`check_changed_pins`, `reset`, `stop`, and the instruction semantics. A `PioHost` of function pointers as above. No allocation, no exceptions; failures of a host call stop the work and are reported as `false` (parked by the Cython shell, as in every other block).
    Python: `native/_pio.pyx` keeps `RPPIO` and `StateMachine` as `cdef class` *views* with today's attribute names (`machines`, `instructions`, `pin_values`, `pin_directions`, `irq`, `fdebug`, `stopped`, `cycle_fp`, `next_due_fp`, `waiting`, `enabled`, `pc`, `x`, `y`, `tx_fifo`/`rx_fifo` as small FIFO views with `push`/`pull`/`peek`/`reset`/`empty`/`full`/`item_count`/`items`/`size`, `execute_instruction`, `check_wait`, ...),
    the block registers itself as a native bus window like TIMER (so a register access, including DMA's FIFO traffic, never enters Python), and the batch loop (`core/batch.hpp`) gets a direct `PioBlock*` per PIO in place of the `pio_advance` trampoline (the trampoline stays for a non-native PIO). The pin layer's and the gSPI shifter's direct sources, bound today to `&pio.pin_values`, are rebound to the C++ block's fields.
  - Oracle (decided: a *lockstep differential*, not a recorded trace). A trace of a PIO would need every `advance(cycles)` call (the pacing model steps at most one instruction per machine per call; ~93M calls on the Pico W scan) and every external pin level, so it is neither small nor exact. Instead `tests/utils/pio_diff.py` drives two chips with the *same* generated stimulus stream - random programs (valid and random 16-bit opcodes, so every instruction type
    and the reserved encodings), config writes, CTRL enable/restart/clkdiv-restart, FIFO pushes and pulls, IRQ and IRQ_FORCE, INTE/INTF, `SM_INSTR` exec, external pin drives and releases, `advance(n)` with small and huge `n` (arrears), `reset()` - one chip with the pure-Python PIO (the reference), one with the implementation under test, and compares after *every* operation everything observable: the whole readable register file,
    every machine's state and FIFO contents, the block's pacing state, and the ordered logs of interrupt-line changes, DREQ calls, pin-listener events and warnings. Precondition, as for every oracle here: the harness must first show the pure PIO against the *current* Cython one with 0 differences (a difference there is a harness bug or an existing divergence to be found), and a mutant-injected implementation must be caught.
  - Verification chain: the differential (long random runs) -> C++ checks of the instruction semantics vs a transcription -> `tests/test_pio*.py` (44 FIFO/machine touches) and `test_pio_pin_wait.py` on both builds -> the CYW43 tests -> `pio-dma` and the Pico W scan with the order hash of the pin events equal to `main`'s -> A/B benchmark on both workloads -> a new callgrind.
  - Order of work: (1) `pio_diff.py` and its clean pure-vs-Cython run; (2) the machine semantics + FIFO + block in `core/pio.hpp` with standalone C++ checks; (3) the Cython views and the bus window; (4) the batch loop calling the block directly and the pin-source rebinding; (5) measurements.
  - Risks, stated: re-entrancy (a DMA transfer, itself inside a DMA alarm, writes a FIFO through the PIO window, which calls `dma.set_dreq`, which may schedule another transfer: the same call chain as today, but now through C++ frames, so every host call must leave the block consistent before it is made); `wait()`/`check_wait()` ordering around `next_due_fp` (0063) is delicate and is exactly what the differential must hammer; the Python attributes that are plain fields today become properties over C++ memory (`machines[i].x = ...` from a test must still work);
    `instructions` is a Python `list` today (a test or a board could index it) - the view must behave like one for the operations used (checked by grep before the port).

- 2026-10-05: **callgrind of the Pico W scan after the gSPI shifter: the PIO is now the largest cost, ~59% of all instructions; the CPU is ~18%.** Decides the next step: the PIO (state machines + `RPPIO`) to C++, the rest of Phase 3, before DMA.
  - Method: `valgrind --tool=callgrind` on the real flow (boot, then `WLAN.scan()` through the REPL) with a build that keeps symbols (`RP2040PY_DISABLE_STRIP=1`), 30.84 G instructions in total; inclusive shares of the total (instructions, not time - cache behaviour differs, so read them as proportions):
    | | share |
    |---|---|
    | `execute_batch` (everything the engine runs) | 94.5% |
    | of which `_pio_advance` -> `RPPIO.advance` (the PIO, with everything it calls) | **59.0%** |
    | `RPPIO._run_due` -> `StateMachine.step` -> `execute_instruction` | 50.2% -> 29.3% -> 25.7% |
    | `RPPIO.check_changed_pins` (pin updates the PIO causes, incl. the direct gSPI listener) | 16.7% |
    | CPU instruction execution (`cpu.hpp`) | 18.1% |
    | everything outside the batch (asyncio, REPL host) | ~5.5% |
    - `PyObject_VectorcallMethod` is 29% inclusive: the Cython PIO talks to its own `RPPIO` and to the pins through *Python method calls* (`pio.pin_values_changed(...)`, `pin_directions_changed(...)`, attribute reads through `object`-typed fields), i.e. the per-instruction cost is
      CPython dispatch, not the instruction semantics. Flat: `_PyEval_EvalFrameDefault` 11.8% and `_PyObject_GenericGetAttrWithDict` 6.7% (attribute reads), `PyLong_FromLong` 3%, `_pio_advance` self 4.2%, `RPPIO.advance` self 2.5%, `pin_values_changed` 2.1%, and the pin shell's own
      `_change_trampoline` 2.6% (it iterates an empty Python listener set on every CLK change: a cheap skip exists, not done - it disappears with the port).
  - Reading: the Python listener (38% before) is gone; what is left of the PIO's cost is its *interface* to Python, which is exactly what moving it into C++ removes. DMA (~25% of the wall in the earlier cProfile) stays the next after that: its DREQ coupling is with the PIO FIFOs, so it is simpler once the PIO is in C++.
  - Plan for the PIO (design note to be written before code, per the recipe): (1) a trace/differential oracle for the PIO block (register traffic + pin-event order + FIFO levels) recorded on `main`; (2) the state-machine instruction semantics in `core/pio.hpp`, checked against a transcription of `_state_machine.py` on random programs; (3) `RPPIO` (registers, FIFOs, pin mapping, due-time pacing of record 0063, IRQs) behind a Cython shell with
      the same Python attributes; (4) `_pio_advance` calling C++ directly instead of a trampoline; (5) DREQ left as the host callback until Phase 4. Existing tests `test_pio*.py`, the CYW43 tests, the `pio-dma` workload and the pin-event hashes are the judges.

- 2026-10-05: **Phase 3: the CYW43 gSPI bit shifter is C++ - the Pico W scan goes from 7.1 s to 3.4 s (2.1x) over the Python listener.**
  - What was built (as designed in the 2026-10-05 note): `core/gspi.hpp` (`GspiShifter`: CS, 32-bit shift register, response buffer; direct listeners of the CLK and CS pins; samples DATA with the pin's `state_code()`, drives it with `set_input_value()`), a Cython shell `native/_gspi.pyx`, and in
    `external/cyw43/bus.py` the code after the 32nd bit of `_on_clock_rising` split out as `GSPIBus._on_word()` (the pure path is unchanged and is still the oracle and the fallback) plus `attach_gpio()` choosing the native shifter when the three pins are native `GPIOPin`s. The level sources of the three pins are bound as
    pointers into the live native PIO/SIO objects, so reading CLK/DATA/CS never makes a Python attribute read; the pins keep the shifter and those objects alive.
  - Verified before measuring: standalone C++ checks against an independent transcription of the three edge methods (30 x 6000 random CS/CLK/word/response operations; 13 mutants killed, one equivalent); the 111 CYW43/chip-reset/boards tests pass on both the shifter and the Python listeners; the order hash of the Pico W scan's
    4,798,482 pin events equals `main`'s (`f45f9c116bcf8dab81f25792d2c1eff6`); the scan result is unchanged (`RP2040PY-GUEST` found, same tuple); `uv run pre-commit run --all-files` green on both builds.
  - Measured, A/B interleaved against the commit just before, three rounds of best-of-two, Pico W `WLAN.scan()` (MicroPython 1.23.0): Python listener 7.154 / 7.146 / 7.057 s, C++ shifter 3.342 / 3.597 / 3.457 s. The ~4M Python edge listener calls (38% of the run in the profile) became ~125k word callbacks. Against `main` (9.83 s on the same
    workload, measured earlier in the session): about 2.8x.
  - What this teaches about the plan: the profile pointed at exactly this (a Python listener per clock edge, not the pin layer's own machinery - two earlier experiments, the pin shell and the PIO wait mask, were performance-neutral), and the gain only came from a consumer that no longer crosses into Python.
    The same recipe - a C++ consumer behind the direct-listener registry, the protocol left in Python and called once per word - is what Phase 5's other `external/` devices (displays, LED strips on PIO) would get if a profile ever points at them.
  - Not done: `nat.py`/`chip.py` stay Python by decision; the cost of the DMA path (13% of the scan) is Phase 4's.

- 2026-10-05: **Design note: the CYW43 gSPI bit shifter in C++ (`core/gspi.hpp`), the protocol in Python. Not implemented; read from `external/cyw43/bus.py` (`_on_cs_change`, `_on_clock_rising`, `_start_response`, `_on_clock_falling`, `attach_gpio`).**
  - What the per-edge path is. Three pins (CLK 29, DATA 24, CS 25) and a tiny state machine in `GSPIBus`: `_selected`, a 32-bit `_shift_reg` + `_bits_in_word`, and while answering a read a `_response_bytes` buffer with a bit index. CS (active low) resets everything and, on deassert, puts the chip's IRQ level on DATA;
    a rising CLK edge samples DATA (`data_pin.value == HIGH`: the level the host drives) unless idle or answering, and on the 32nd bit hands the word to the protocol; a falling edge, while answering, drives the next response bit with `data_pin.set_input_value(bit)`. Everything above one 32-bit word (`_word()` wire transform, header decode, write accumulation,
    `read_register`/`write_register`, SDPCM framing, scan/join events, the NAT bridge) is protocol and stays Python. The pure-Python listeners (`_cs_listener`/`_clk_listener`) stay as they are: they are the oracle and the path when no native pins are present.
  - The split. A C++ `GspiShifter` owns the *bit-level* state only - `selected`, `shift_reg`, `bits_in_word`, the response buffer and bit index - and is registered as **direct listeners** on the CLK and CS pins (the registry added in Phase 3), so an edge never enters Python. It holds the data pin's C++ bank and calls `state_code()` to sample and `set_input_value()` to drive. Two host calls out, both
    Python and both rare: `on_word(word)` once per completed 32-bit word (~4.05M edges become ~125k calls on the Pico W scan), and `on_cs(selected)` on a CS change. `bus.py` gets one refactor that does not change the pure path: the code after the 32nd bit of `_on_clock_rising` becomes `_on_word(word)`, called by both the pure listener and the native trampoline.
    A response reaches the shifter as the bytes `_start_response` produced (the trampoline moves `_response_bytes` into the C++ buffer and clears the Python copy); capacity 4096 bytes (a read is at most 2047 bytes + the backplane pad), a longer one parks an error instead of truncating.
  - Equivalence to keep, edge by edge: CS reset clears shift register, bit count and response *before* the Python `_on_cs_change` runs (which clears the protocol's own pending command/words and drives DATA with the IRQ level); a rising edge while idle or answering does nothing; a falling edge with nothing to say does nothing; the response buffer is released when its last bit has been driven;
    failures of the Python callbacks or of a pin call stop the rest at once, as an exception would. The listener *order* on a pin is unchanged for everything else: direct listeners run first, then the Python set (`_power_listener` etc.).
  - Making the edge itself cheap (otherwise the saved Python call is replaced by Python source reads): for the three pins the shifter binds the level *sources* directly - `RPPIO.pin_values`/`pin_directions` of both PIOs and SIO's `gpio_value`/`gpio_output_enable` - as pointers (the `bind_source` hook of the pin bank), keeping the objects alive for the shifter's life. Only when those objects are the native ones;
    otherwise that source keeps calling the host. Same pre-run contract as every `ExternalDevice.attach()`: the PIO/SIO objects of a chip are not replaced after attach.
  - Fallback: the native shifter is used only if the three pins are native `GPIOPin`s and the extension is importable (`native_disabled()` is false); otherwise `attach_gpio` installs the Python listeners exactly as today.
  - Verification, before any benchmark: (1) standalone C++ checks of the shifter against a transcription of the three Python methods, on random CS/CLK/word/response sequences, mutation-checked; (2) the existing CYW43 tests (`test_cyw43_bus.py`, `test_cyw43_nat.py`, `test_chip_reset.py`, `test_boards.py`) run on both paths - their fake gSPI master wiggles real pins, so they exercise the native shifter when it is on;
    (3) the order hash of the pin-event stream on `picow-scan` equal to `main`'s (`f45f9c116bcf8dab81f25792d2c1eff6`), and the live MicroPython Pico W scan result unchanged; (4) A/B against the Python listener on the Pico W scan.
  - Risks, stated: the sources are read through pointers into PIO/SIO objects (stale if a test replaces them after attach: it must not); `state_code()` on the data pin used to be a Python property read and is now a C++ call - the same level, but the failure path differs only in where an exception from a source would surface; a response larger than the buffer (not producible by the protocol today).

- 2026-10-04: **Open question recorded, nothing changed: who owns the small fixed buffers (core members vs caller-owned)?** The user asked whether, the sizes being known, arrays inside the C++ would be better than buffers provided from Python. Written into "Risks and open questions" with the
  arguments both ways, the point that the stack is not an option (and that flash, 16 MiB, would stay caller-owned in any compromise), and a measurement plan on `scripts/bench/cpp_bus_fetch.cpp`. Not implemented, not measured yet.

- 2026-10-04: **Tried and dropped: a `pin_wait_mask` to skip the per-input-change walk of the PIO state machines. No gain; the walk is not the cost.**
  - Idea: every input change (`set_input_value`, 740k on the Pico W scan's data pin) makes the pin layer walk both PIOs' eight machines looking for one parked on `wait gpio`/`wait pin` for that index. A conservative mask on each `RPPIO` (bit set by
    `StateMachine.wait()`, recomputed from the machines' real states after a delivery, a PIO without it always walked) lets a PIO that nobody waits on for the pin be skipped. Implemented, with tests of the wake-up behaviour on both builds and of the mask's contract
    on the native one; a mutant that never set the mask failed 9 of the 10 tests; the order hash of the pin-event stream stayed identical to `main`'s on all four workloads and the three pin traces still replayed with 0 mismatches.
  - Measured, A/B interleaved against the commit just before, three rounds of best-of-two: Pico W scan without the mask 7.24 / 7.28 / 7.49 s, with it 7.34 / 7.41 / 7.07 s (means 7.34 vs 7.27 s, inside the round-to-round spread), PIO+DMA 0.162 / 0.183 / 0.167 s vs 0.162 / 0.163 / 0.162 s.
    So the walk is cheap (`machine.enabled and machine.waiting` fails fast for an idle machine) and the 38% of the Pico W scan is the Python *listener*, not what the pin layer does around it - as the profile said.
  - Decision: not kept (complexity without a measured gain). The wake-up tests are kept as regression tests (`tests/test_pio_pin_wait.py`, 7 tests, both builds): they fail if a waiting machine is ever missed, whatever changes how input changes reach the machines later (for instance once PIO state is in C++).
  - So the order stays: (1) the CYW43 gSPI split designed from `bus.py`'s per-edge path - the bit shifter on CLK/CS in C++, `read_register`/`write_register`/SDPCM in Python once per completed 32-bit word - written into this record before code; (2) the C++ shifter on the direct-listener registry;
    (3) benchmark on the Pico W scan. Expected from the profile: the per-edge Python listener (4.05M calls, 38%) becomes ~125k word callbacks.

- 2026-10-04: **Phase 3 re-planned from a profile: the rest of the pin layer is not where the time is; the CYW43 gSPI listener is.** Decision (the user's: "as you think necessary").
  - Measured with `cProfile` on the native build (no counting proxies): the paths the planned step 3 would have moved - SIO's two Python trampolines (`rp2040.gpio_values`, `update_pins`), the IO_BANK0/PADS_BANK0 windows, `update_io_interrupt`,
    `check_for_updates`, `set_input_value` - stay below 2% of the run in every workload tried: a MicroPython loop of 20,000 `Pin.value()` writes and 20,000 reads (1.2 s profiled), and the Pico W scan (11.5 s profiled). The only function over that
    threshold in the Pico W scan is the CYW43 clock listener, `external/cyw43/bus.py` `_clk_listener`: **38%** (4.4 s cumulative, 4,048,855 calls).
  - Why it is expensive and not the pin layer: each CLK edge calls into Python, compares `GPIOPinState` IntEnum members, reads `data_pin.value` (a property that evaluates the level sources) and calls `set_input_value`. A faster pin makes that crossing cheaper, not absent;
    only a consumer that does not leave C++ removes it.
  - Done (committed with this entry): a **direct-listener registry in the C++ pin** (`PinBank::add_direct_listener`/`remove_direct_listener`, up to 4 per pin): function pointers called from `check_for_updates()` in registration order, *before* the Python listener set,
    each able to stop the rest by failing (as a raising Python listener does); the pin's state is already recorded when they run. Exposed to Cython consumers as `GPIOPin.add_direct_listener()` / `bank_ptr()`. Standalone C++ checks, mutation-checked (5 mutants killed, one initially survived a removal test that only removed the last listener).
    Existing Python listeners keep their order; only a new C++ consumer is ahead of them.
  - Next, in this order: (1) read the per-edge path of `external/cyw43/bus.py` (`_on_clock_rising`, `_on_clock_falling`, the CS listener, how the data pin is driven) and design the split - the bit shifter on the CLK/CS edges in C++, the command/word decode and `chip.py` in Python called once per completed word or
    transaction (so ~4M callbacks become a number of the order of the transactions); written into this record before any code; (2) the C++ shifter behind that interface, judged by the pin-event order hash (`scripts/bench/pin_events.py`), the pin trace oracle and the live CYW43 boot test (0027); (3) benchmark against the Python listener on the Pico W scan.
  - Deferred, not dropped: the shared 30+6 pin banks with SIO calling them directly, IO_BANK0/PADS_BANK0 as native windows and `update_io_interrupt` in C++. No speed-up on any measured workload, but they are what the headless WASM profile needs (no register access reaching Python), so they belong before Phase 4's exit.

- 2026-10-04: **Phase 3, step 2 done: the native `GPIOPin` is a shell over the C++ pin (`core/pin.hpp`).** Performance-neutral by design; the gain is step 3's.
  - What changed: each `GPIOPin` owns a `PinBank` of one pin (the C++ pin holds its number, so a source register's bit and what the host is told are the pin's own); the Cython class keeps the whole attribute surface as properties over the C++ fields
    (`ctrl`, `pad_value`, the three IRQ words, `index`, `_raw_input_value`, `_driven`, `_always_output_enabled`) and implements only the host: one trampoline for the level *sources* (`rp.sio`/`rp.pio[i]`/`rp.pwm` attributes, read exactly as before), the listener `set`, `update_io_interrupt()`, and
    the PWM/PIO reactions to an input change. A failing callback parks its exception in the shared slot and is re-raised at the same call. `core/pin.hpp` gained `Pin::index` / `init(..., first_index)` for this; `tests/cpp/test_pin.cpp` covers it.
  - Verified against the old code, not only against itself: three pin traces (`mp-idle`, `cp-boot`, `pins-mp`) *recorded on `main`* replay on this build with 0 mismatches (6.6k / 66k / 20k events, the last with 14 IO interrupt changes and 5 external-drive samples), and the order hash of the pin-event stream equals `main`'s on `mp-idle`, `pio-dma`,
    `cp-boot` and `picow-scan` (4,798,482 events). `test_gpio_pin.py`, `test_qspi_pads.py`, `test_ssi.py`, the pure-Python build and the whole suite are green.
  - Measured, A/B interleaved against the commit just before (Cython pin), three rounds of best-of-two: Pico W scan 6.92 / 6.89 / 7.32 s vs 7.14 / 7.64 / 7.01 s (best 6.89 vs 7.01, about +2%, round-to-round spread up to 5%), PIO+DMA 0.167 / 0.157 / 0.167 s vs 0.172 / 0.162 / 0.161 s. So: no regression beyond noise, and no gain - the callbacks
    the Python version made are the same callbacks. The sources are still Python attribute reads and the per-input-change PIO wait loop is still Python.
  - Deliberately left for step 3: shared banks (30 GPIO + 6 QSPI) so SIO's two Python trampolines (`gpio_in` -> `rp2040.gpio_values`, `update_pins` -> `check_for_updates`) become direct calls; direct pointers for SIO's `gpio_output_enable`/`gpio_value` (rebound by the `sio` property setter, since `rp2040.sio` can be replaced);
    IO_BANK0/PADS_BANK0 as `_native_window` blocks; `update_io_interrupt` in C++. Kept as is, not changed: an input change on QSPI pin *n* reaches the GPIO-*n* PWM/PIO reactions (the host is given only an index) - looks like a bug in the existing code; changing it needs a decision.

- 2026-10-04: **Phase 3 design note: the C++ pin layer (`GPIOPin` x 36, IO_BANK0, PADS_BANK0). Not implemented; read from `_gpio_pin.pyx` and its consumers.**
  - What the pin is today. A pin is ten words of state (`ctrl`, `pad_value`, `irq_enable_mask`, `irq_force_mask`, `irq_status`, `_last_state`, `_raw_input_value`, `_driven`, `_always_output_enabled`, `index`) plus a Python
    `set` of listeners. Its level is a pure function of that state and of *other blocks' registers*: `rp.sio.gpio_output_enable/gpio_value` (SIO, already C++), `rp.pio[0|1].pin_directions/pin_values` (RPPIO, still Cython) and
    `rp.pwm.gpio_direction/gpio_value` (Python). 30 GPIO pins in `rp2040.gpio`, 6 QSPI pins in `rp2040.qspi` (`always_output_enabled=True`, their own pad reset values, record 0050).
  - Who calls the pin and what it calls back (the real coupling, which is what the port must keep):
    - *In*: IO_BANK0/PADS_BANK0 windows (`check_for_updates`, `update_irq_value`, `refresh_input`, `ctrl`/`pad_value` writes); SIO's C++ block through two Python trampolines (`gpio_in` -> `rp2040.gpio_values`, a Python loop over the 30 pins; `update_pins` -> `gpio[i].check_for_updates()` per changed pin);
      `RPPIO.check_changed_pins` (a direct C call into `GPIOPin.check_for_updates`, via the `.pxd`); any device through `set_input_value`/`release_input`; `reset(io, pads)` from the chip.
    - *Out*: the listeners (a Python `set`, iterated in set order), `rp2040.update_io_interrupt()` (a Python loop over the pins), `rp.pwm.gpio_on_input(index)` when FUNCSEL is PWM, and - on **every** input-level change - a loop over both PIOs and all their state machines calling `check_wait()` for one that is `WAIT PIN` on this index.
  - Proposed shape (step 1, behaviour identical, Python listeners kept):
    1. `core/pin.hpp`: a `PinBank` of N pins (a plain struct array, caller-owned like the memory regions) owning all of the state above except the listeners. The level function takes its sources as `const uint32_t*` (SIO's `gpio_output_enable`/`gpio_value`, each PIO's `pin_directions`/`pin_values`) plus a PWM
       callback, so nothing in it calls Python on the common path. `check_for_updates()` computes the state code as `_state_code()` does and, on a change, calls one host callback `on_change(pin, new, old)`; the Cython shell iterates the listener `set` there, so listener order is unchanged by construction.
    2. `GPIOPin` stays a `cdef class` with the same attribute surface (`ctrl`, `pad_value`, `irq_*`, `_raw_input_value`, `_driven`, `_listeners`, `_last_state` as properties over the bank's struct), and keeps `check_for_updates` callable as a C method for `_pio.pyx` until PIO itself moves. The pure `_gpio_pin.py` stays as the oracle (D6).
    3. SIO's two trampolines become direct calls into the bank (no Python on a `GPIO_OUT_SET`), and `gpio_values` becomes one C++ loop. IO_BANK0/PADS_BANK0 stay Python windows in step 1 and move onto the bank as `_native_window` blocks in step 2, like TIMER and SIO.
    4. The IO interrupt (`update_io_interrupt`) is computed in C++ from the 30 `irq_value`s; `set_interrupt` is the one call out.
  - Open design points, decided from measurement and not assumed:
    - **Direct-callback mode** for a consumer that must answer in the same cycle (the CYW43 gSPI listener on the falling CLK edge): a registry in the bank (`pin -> {function pointer, ctx}`) consulted in the same `on_change` call, ahead of or instead of the Python set. Its place in the listener *order* has to be fixed first: today everything is one `set`.
    - **The per-input-change PIO wait loop** runs in Python for every `set_input_value` (740k/s on the Pico W scan's data pin); it can only be removed once PIO state is in C++ - a per-pin "someone is waiting" bit kept by the state machines would make it one test. Left as is in step 1.
    - Thread-safety is unchanged: pins are touched on the engine-room thread (or between batches via `schedule_threadsafe`), single-threaded by contract.
  - Verification (all already in place): `tests/test_pin_trace.py` oracle on `mp-idle`/`cp-boot`/`pins-mp`, `scripts/bench/pin_events.py` order hash identical to `main` on the four workloads, `tests/test_gpio_pin.py`, `test_qspi_pads.py`, `test_ssi.py`, the pure-Python build, a C++ access-by-access test of the level function against a transcription of `_state_code`, then the micro/real benchmark against the current Cython pins.

- 2026-10-04: **Phase 3 baseline, step 2: an IO_BANK0 + PADS_BANK0 trace oracle (`tests/utils/pin_trace.py`).** Decision (the user's): Phase 3 starts with the pin layer
  (`GPIOPin`, IO_BANK0, PADS_BANK0), PIO second; the CYW43 gSPI question stays open until the pin layer's direct-callback mode is designed.
  - Why a new module and not `mmio_trace.py`: IO_BANK0/PADS_BANK0 are thin windows over the state of 30 `GPIOPin` objects, and that state is also written by other things.
    So a pin trace records, besides the two blocks' reads/writes/resets and the IO interrupt line: (1) SIO *writes* as a driver (a pad's output level and direction are what
    `STATUS` reports; replayed, not compared, SIO has its own oracle), and (2) who drives each pin from outside (`_raw_input_value`/`_driven`, sampled before every register access and logged
    as a `q` event when it changed - a button changes no register). Interrupts compare by `(irq, value)` in order, not by time, because an external edge raises its line when the drive happened in the
    original run but when its sample is applied in the replay. Not modelled: PIO, PWM and the CYW43 as pin drivers (their traffic is covered by `scripts/bench/pin_events.py`).
  - Self-test `tests/test_pin_trace.py` (6 tests, both builds): a scripted bare-chip session replays exactly, and the oracle reports a wrong `STATUS` read, a missing SIO driver write, a missing external
    drive and a missing interrupt. `scripts/bench/trace_block.py pins [--workload mp-idle|cp-boot|pins-mp]` records a real boot and replays it.
  - Measured (current Cython pins against themselves, 0 mismatches each): `mp-idle` 13.5k events, `cp-boot` 68k, and `pins-mp` (a MicroPython script with an output pin, both pulls, a rising/falling pin interrupt, and the host playing a
    button) 44k events including 14 IO interrupt changes and 5 external-drive samples. A clean self-replay is the precondition for judging a C++ pin layer with it.
  - Next: the C++ pin layer itself, ported from `_gpio_pin.pyx` keeping its attribute surface and its `check_for_updates` semantics and listener order, first with Python listeners only; then the direct-callback mode.

- 2026-10-04: **Phase 3 baseline, step 1: the pin-event stream, and an open question about the order.**
  - New tool `scripts/bench/pin_events.py`: hooks a listener on every `RP2040.gpio[n]` before anything runs and records `(nanos, pin, new, old)` in the order listeners are told -
    exactly what an `ExternalDevice` (the CYW43 gSPI listener included) sees. `--save`/`--compare` keep and diff a stream.
  - What can be compared exactly: the **order** of `(pin, new, old)`. Measured: the order hash is identical between `main` (`6aeef40`) and this branch on all four workloads
    (`mp-idle` 2 events, `pio-dma` 4607, `cp-boot` 59, `picow-scan` 4,798,482), so Phase 2 did not change what the pins do.
  - What cannot: the **spacing**. Two runs of the very same build already differ (`pio-dma`: 97.8% of 4604 gaps within 64 ns of a saved run, worst 504 ns), because the asynchronous host injects a
    script at a wall-clock-dependent simulated time (a 2 MHz PIO period is 62.5 core cycles, so it is quantised differently) and the guest code between DMA transfers takes a host-dependent time.
    Absolute timestamps differ by tens of ms between runs. So `--compare` fails only on the order and *reports* the spacing; cycle-exact timing is covered by the instruction-level golden traces and `tests/test_pio*.py`.
  - Event volumes (native build, per simulated second): Pico W scan 6.5M (4.05M of them on the CYW43 clock pin GPIO 29, 0.74M on its data pin GPIO 24, all to one Python listener), `pio-dma` 49k (GPIO 2 only), MicroPython idle 2.
  - Consequence for the plan, **not yet decided**: the CYW43 listener must answer on the falling clock edge in the same cycle (`data_pin.set_input_value(bit)`), so its events cannot be deferred into a ring - they need a direct callback. If only GPIO/PIO move to C++ the Python listener stays and
    the 4M callbacks per scan remain; the Pico W gain needs the gSPI decode in C++ too (Phase 5's item). Proposed order if that is accepted: Phase 3, then the CYW43 gSPI port (pulled forward), then DMA. Waiting on the user's decision; nothing implemented.
  - Also measured: MicroPython 1.29.0 (the stable release, not in this record's earlier tables) is ~1.6-2x heavier for the emulator than 1.21 on both builds, and the speed-up over `main` holds (Pico `compute` 20.5 s -> 5.7 s = 3.6x, sleep 3.7x, ticks 3.0x, gpio 3.1x, Pico W scan 13.4 s -> 8.5 s = 1.6x).

- 2026-10-04: **Measured speed-up against `main` (`6aeef40`, the pre-0096 Cython build) at the end of Phase 2.** Same machine, both builds Cython, best of two runs, outputs identical
  (`scripts/bench/profile_access.py` workloads; MicroPython rows from the per-phase script of the earlier MicroPython-vs-Cython entry).

  | Workload / phase | main | branch | speed-up |
  |---|---|---|---|
  | MicroPython 1.21 Pico: boot + enumerate | 0.076 s | 0.041 s | 1.9x |
  | MicroPython: `sum(i*i for i in range(60000))` | 12.56 s | 2.92 s | 4.3x |
  | MicroPython: `time.ticks_us()` x3000 | 0.363 s | 0.106 s | 3.4x |
  | MicroPython: `Pin.value()` x3000 | 0.428 s | 0.126 s | 3.4x |
  | MicroPython: `time.sleep_ms(1000)` | 8.66 s | 2.17 s | 4.0x |
  | MicroPython: `print(1+1)` | 0.051 s | 0.046 s | 1.1x |
  | CircuitPython Pico: boot | 15.86 s | 1.17 s | 13.5x |
  | CircuitPython: `print` | 0.515 s | 0.061 s | 8.4x |
  | CircuitPython: `time.sleep(1)` | 8.11 s | 2.19 s | 3.7x |
  | Pico W: boot | 0.071 s | 0.041 s | 1.7x |
  | Pico W: `WLAN.scan()` | 9.83 s | 6.59 s | 1.5x |
  | PIO+DMA (MicroPython 1.23): boot | 0.066 s | 0.041 s | 1.6x |
  | PIO+DMA: 4 x 256-word transfers | 0.383 s | 0.167 s | 2.3x |

  - Short/cold phases gain least because the asynchronous REPL round trip dominates them (`print`: 1.1x).
  - Pico W gains least of the long workloads (1.5x) because its cost is the CYW43 gSPI listener and DMA, still Python (see the profile in the previous entry): that is what Phases 3-5 are for.
  - One machine, one platform; run-to-run variance a few percent.

- 2026-10-04: **Phase order amended again - USB deferred; pins/PIO, then DMA, then the CYW43 gate.** Supersedes the order in the entry below.
  - Decision (the user's): do not port the USB controller / CDC host now. A port could silently break enumeration, raw-REPL and the
    DTR/RTS handling (all of which have live-boot CI), and the measurement below does not show a gain big enough to take that risk first.
  - Measured with `cProfile` on the *native* build, no counting proxies (the earlier proxied run inflated the wall time and hid the real split):
    - MicroPython idle, 1 s of `time.sleep_ms()` (3.2 s profiled): the only Python left is USB, about a third of the run (`dpram_updated`,
      `_finish_read`, `_on_endpoint_read`, ~35k endpoint reads/s). Everything else is the C++ batch loop. `compute`
      (`sum(i*i ...)`, 2.9 s unprofiled) is all CPU: no peripheral in the picture, so peripherals cannot speed it up.
    - Pico W scan (11.8 s profiled): the CYW43 gSPI clock listener (`external/cyw43/bus.py` `_clk_listener` + rising/falling, 4.0M calls) is
      ~39% (4.6 s cumulative); DMA (`transfer_swap32`, `set_dreq`, `treq`, `schedule_transfer`) ~13%; USB ~3%.
    - PIO+DMA micro-workload: 0.25 s total, nothing dominant.
  - Why USB is a third of an *idle* run: the CDC host answers every OUT-endpoint arm after 10 us, with a zero-length buffer when the TX
    FIFO is empty, so an idle firmware and the host ping-pong ~35k times per simulated second. A real device would stay armed until the host
    sends data. That is emulation behaviour, not a cost a faster controller should be asked to carry; changing it (e.g. complete the read only
    when there is data) would alter working behaviour and is **not** done - it needs a separate decision, and a separate commit if taken.
  - New order: (1) Phase 3 as written - pins and PIO first, because the CYW43 gSPI listener is a pin-change consumer and its cost is the pin-event
    path; (2) DMA, pulled forward from Phase 4 together with its DREQ coupling to PIO/SPI/UART; (3) the Phase 5 CYW43 gate - the number above
    already says "yes, port `bus.py`/`chip.py`", but it is decided after (1)-(2) change what the listener costs; (4) the rest of Phase 4;
    (5) USB controller + CDC host last. The `logger.info` SEV/YIELD gating idea is dropped from the list: the profile above shows no
    logger cost.
  - Documentation only; nothing implemented. Phase 3 starts from a baseline capture (golden trace of the Pico W boot, pin-event ring contents), as the recipe requires.

- 2026-10-04: **Phase order amended - USB (controller + CDC host) first, then pins/PIO/flash.**
  - Why: the post-Phase-2 profile of MicroPython and CircuitPython boots/REPL sessions shows USB as the largest remaining Python cost; GPIO, pads, PIO, DMA and SSI are negligible in
    those workloads (the flash bring-up SSI accesses are a one-off). The plan said "order by Phase 0's profile"; this applies it.
  - New order: (1) USB controller + CDC host: capture a baseline from the existing Python/Cython implementation first (golden traces, MMIO trace record/replay), write parity tests,
    then port following the existing structure; (2) a cheap win alongside: gate the `logger.info` calls on SEV/YIELD behind a level check so they cost nothing when disabled;
    (3) Phase 3 (pins, PIO, flash path) as written; (4) the rest of Phase 4.
  - Documentation only; nothing here is started. Implementation needs a separate go-ahead.
  - CI note: the CircuitPython CDC control-lines step failed once emulation got faster, because the test held DTR low for 5 *wall-clock* seconds and CircuitPython drops console output
    while DTR is low. Fixed in the test only (`a8eb8d7`): DTR is toggled by guest time (0.5 s low, 1.5 s high). All five workflows green on that commit.

- 2026-10-04: **Phase 2 correction - the bus read path measured against the Cython baseline it replaced, and brought back to it.**
  - What was wrong with the previous entry: the three "fixes" were tuning of C++ I had written generically in Phase 1 (a region table walked linearly, a flag pointer reloaded every
    iteration) instead of carrying over the structure of the working Cython. The pre-0096 bus did a fixed chain of range compares - flash, then SRAM, then boot ROM/DPRAM - with the
    bases as constants; for an instruction fetch that is two compares. The block index of the same entry made it *better than the table walk but still slower than that chain*.
    The "wide" test was already `(op >> 12) == 0xF || (op >> 11) == 0b11101` in the Cython, so that one was a micro-optimisation, not a fix.
  - Measured (`scripts/bench/cpp_bus_fetch.cpp`, same addresses and memory, a MicroPython-like mix of 62% 16-bit flash fetches, 28% SRAM words, 10% flash words; both a cache-resident and a
    2 MiB/264 KiB random working set): Cython-style chain 6.1-7.0 ns/access, the C++ `Bus` with the block index 6.6-7.7 ns (8-11% slower). A first attempt at a runtime-geometry "hot" loop
    was worse (8.1 ns): the Cython speed comes from the bases being constants.
  - Now: `MemoryMap` keeps the generic ordered walk (it decides ties and every unusual access) but tries two constant-base fast paths first - XIP flash at 0x10000000 and SRAM at 0x20000000, the
    RP2040's fixed address map - for 8/16/32-bit reads and writes, with the region's own data pointer (no second lookup) and a single 64-bit bounds compare (the Cython one wrote past a
    buffer's end on an edge access; this does not). A region qualifies only if it is plain sub-word memory with no hook and shares no 256 MiB block with another region, so serving it first
    cannot change which region the walk would pick. Result: 6.7 ns vs 6.5 ns (within 3-4%, noise level) cache-resident, 7.1-7.5 vs 6.8-7.2 on the large set.
  - Proof it still behaves as the walk: `test_memory_map.cpp` runs 200 000 random accesses of every width over the edges of both regions, the mirror and the gaps on a map with the fast paths
    and an identical map with them switched off (`set_fast_paths(false)`) - answers and memory identical; plus `test_memory_map_parity` against the pure-Python bus and the whole suite.
  - End to end, A/B on the same machine (it is slower after the session restart than the numbers above, so only pairs are comparable): synthetic loop 69 -> 73 Minstr/s, MicroPython compute
    4.46 -> 4.09 s. A modest gain; the point of the change was to stop being slower than the baseline.
  - Working rule from here, per the maintainer: for every hot path, take the structure of the working Cython/Python as the baseline, port it first, benchmark against it, and only then change it.

- 2026-10-03: **Phase 2 follow-up - callgrind profile of the C++ loop on a MicroPython boot, and three cheap wins.**
  - Method: the boot run in-process (`time.monotonic` stubbed so the 5 ms budget does not cut batches under valgrind), 25 batches = 25 M instructions, `RP2040PY_DISABLE_STRIP=1` for symbols.
    6.76 G x86 instructions, ~130 per emulated instruction: batch loop 45, CPU `execute()` 38, memory map 31, clock 13 - plus ~25% of the total in Python (alarm callbacks
    into USB, `_PyEval`).
  - Fixed: (1) the per-iteration PIO check re-loaded each flag's pointer from the host struct (24 Ir/instr with both PIOs stopped) - the flag addresses are now held in registers
    (the flags are still read every iteration: a firmware write that starts a PIO must make it step on the next instruction); (2) a memory-map lookup walked the region table - an
    instruction fetch from flash tested the boot ROM first - now indexed by the address's top four bits (a block no region touches serves nothing, a block several regions touch
    falls back to the ordered walk that decides ties; new cases in `test_memory_map.cpp`); (3) the "wide instruction" test is one compare (`opcode >= 0xE800`).
  - Effect, honestly: instructions executed in the profile fell 9% (6.76 -> 6.17 G); the synthetic loop went 114 -> 132 Minstr/s; **MicroPython compute is unchanged within noise (2.6 s)**.
    So the C++ loop's own bookkeeping is no longer the lever for MicroPython: what is left is the handler dispatch (an indirect call per instruction), the generic bus path of every
    load/store and the Python callbacks (USB alarms, `logger.info` per SEV). Taking those further is a design question (specialised fetch/load paths, a C++ USB), not another tuning pass.

- 2026-10-03: **Phase 2 follow-up - MicroPython (the reference firmware) measured against the pre-0096 Cython build, and what the profile says.**
  - Same script, old Cython (`6aeef40`, own venv) vs now, best of 2, MicroPython 1.21 on Pico, outputs identical: `sum(i*i for i in range(60000))` 11.3 -> 2.67 s (4.2x);
    3000 x `time.ticks_us()` 0.33 -> 0.096 s (3.4x); 3000 x `Pin.value()` 0.39 -> 0.11 s (3.5x); `sleep_ms(1000)` 7.9 -> 2.0 s (3.9x). **On MicroPython the gain is 3.4-4.2x**, not
    the 7x of the synthetic loop or the 11x of the CircuitPython boot quoted in the step 4c table.
  - Where the time is now (cProfile of the compute phase, 2.78 s): 2.37 s is inside the C++ batch loop itself, ~0.4 s is Python callbacks - `logger.info` for every SEV/YIELD
    (232 688 calls, 0.19 s) and the USB controller/CDC (0.1 s). The firmware runs 220 Mcycles in 2.5 s = ~85 Mcycles/s, about 0.7x of the real chip's 125 MHz. Clock-tick batching
    (`RP2040PY_CLOCK_TICK_BATCH` 16/64) changes nothing, so the per-instruction clock is not the cost. Phase 3's blocks (pins, PIO, flash path) are therefore **not** what limits
    MicroPython compute; the C++ per-instruction path (flash fetch through the memory map, the bus's decode order) is, and needs a real profiler (valgrind/callgrind are installed here, `perf` is not).
  - API: direct `dir()` comparison of the old and the new build over `RP2040`, the core, SIO, TIMER, the clock, `Simulator`, a GPIO pin and the PPB: one name added (`sio.name`), one removed -
    `timer.alarms`, the Python TIMER's list of alarm objects. Nothing outside the pure `_timer.py` reads it; the native TIMER keeps its alarm nodes inside the C++ clock. Decision: no
    compatibility shim (an implementation detail, not a contract).

- 2026-10-03: **Phase 2, step 4c - the batch loop is C++ (`core/batch.hpp`). Phase 2's measured table.**
  - `run_batch()` is `Simulator._execute_batch()`'s loop (the Cython `_simulator.pyx` one, itself translated from `_execute_batch.py`) over the C++ `Cpu`, `Bus` and `Clock`:
    instruction ceiling, the 5 ms real-time budget checked every 256 iterations, the idle jump to the next alarm floored at one clock, batched or per-instruction clock ticks,
    PIO stepping. **No Python object is touched per iteration** unless something the loop drives touches one. The three couplings that were Python are now bytes and pointers:
    `Simulator.stopped` is a property over a one-byte buffer (`_stop_flag`) the loop reads directly, so a `stop()` from another thread or from an `on_break` in the middle of an
    instruction is seen at the next iteration exactly as before; each PIO's `stopped` is read through the address of its own `cdef public bint` field and only a *running* PIO costs a
    call (`RPPIO.advance`, a C-level call); `time.monotonic()` is called once per 256 iterations. A Python error from an alarm, a peripheral or a PIO is parked and re-raised after
    the loop returns, with the machine left where the exception would have left it (the pending tick time is not flushed, as before).
  - Refused up front rather than silently bypassed: a `SimulationClock` subclass that overrides `tick()` (the loop ticks the C++ clock directly; `MockClock` only adds `advance()`),
    and a PIO that is not the native `RPPIO` (the pure-Python loop handles those).
  - Proof: `tests/cpp/test_batch.cpp`; `tests/test_batch_parity.py` runs the **pure-Python loop and the native one on identical native chips** (the pure loop only uses the Python API) -
    a counting program with 0-4 alarms at awkward times, an idle core woken by an alarm, with `tick_batch` 1 and 16: registers, flags, cycles, simulated time and every alarm's firing time
    must match; a failing alarm callback and a failing peripheral during a load must surface the same exception with the same machine state. The whole suite on both builds.
  - **Phase 2 measured table** (this machine, CPython 3.10 normal build, noisy +-15%; the synthetic loop ticks the clock after every instruction):

    | | synthetic loop, Minstr/s | + `TIMER.TIMELR` read every 8 instr | CircuitPython boot (`cp-boot`) |
    |---|---|---|---|
    | Cython core, Python TIMER (Phase 0) | 13.4 - 16.7 | 7.7 - 8.4 | 12.4 s |
    | + C++ memory map, window registry (Phase 1) | 18.7 - 19.7 | 8.6 - 9.1 | - |
    | + C++ clock and TIMER (2 steps 1-2) | 17.7 - 21.4 | - | 4.3 - 4.7 s |
    | + C++ SIO (step 3) | - | - | 4.5 s (about 5% from SIO alone) |
    | + C++ bus and CPU (step 4a-b) | 25.5 | - | 3.5 s |
    | **+ C++ batch loop (step 4c)** | **104 - 114** | **105** | **1.1 s** |

    The C++ proxy of the Phase 0 table runs the same synthetic loop at ~315-336 Minstr/s with no Python boundary at all: what remains between 114 and that is the per-instruction
    `Clock::tick`/PIO-flag bookkeeping and the bus's window table, not the language of the loop.
  - **What Phase 2 deliberately did not move: the PPB** (NVIC/SysTick/SCB, 32 accesses in a whole boot, 0.1% of the traffic - Phase 0's profile). It stays a Python window
    behind the C++ bus; the bus calls it through the same trampoline a user's replacement would use. Moving it is no longer on any critical path and waits for a profile that
    says otherwise. Also still Python: the interrupt line (`rp2040.set_interrupt` -> `core.set_interrupt`, the TIMER and SIO trampolines), which Phase 3 removes together with the pins.

- 2026-10-03: **Phase 2, step 4b - the Cortex-M0+ core is C++ (`core/cpu.hpp` + `core/cpu_ops.hpp`); `CortexM0Core` is a shell over it.**
  - `Cpu` owns all architectural state (registers, flags, stack banking, the exception model, NVIC-side priorities) and the decode/execute loop: ~90 Thumb handlers
    transcribed from the Cython `op_*` functions, the same `match_pattern`/`resolve_wide` decode, a 64K-entry dispatch table built once per shared object. It calls the
    `Bus` of step 4a directly. Three things leave C++ through a `CpuHost`: `on_break` (BKPT/UDF), `bl_taken` and `log`. `bl_taken` is a call-tracing hook that used to
    be a Python lambda called on **every** BL/BLX; it is now invoked only while a real hook is installed (`bl_hook_enabled`), the default no-op never leaves C++.
  - **Failure model.** A Python error raised inside a bus access or a host call cannot unwind through C++; it is parked in `_pending.pyx`, whose new `pending_flag()` exposes
    a plain `int` the core polls after each such call (a header-level global would be one copy per extension, so the flag lives in the one module everyone cimports). The
    instruction returns `kCpuFault` at that point - a failed load never writes its register, a failed instruction never counts cycles, earlier effects stay - and
    `execute_instruction()` re-raises. Same state the Python exception left.
  - `native/_cortex_m0_core.pyx` shrank from 1.7k lines to a shell: every field is a property onto the C++ struct (same names, same masking), `registers` and
    `interrupt_priorities` are memoryviews over the C++ arrays (zero-copy; `core.registers[i] = x` writes what the instruction loop reads). `_simulator.pyx` reads
    `core._cpu.waiting` as a C field.
  - **Proof.** (1) The state of the old Cython core and the new one after every 100 000 instructions of a real boot - MicroPython (2 M instructions) and CircuitPython (3 M):
    pc, cycles, all 16 registers, flags, IPSR, mode, pending interrupts and an MD5 of SRAM - is byte-identical. (2) `tests/test_cpu_parity.py`: 24 x 1 500 single-step trials
    against the **pure-Python** core - a complete random machine state (registers, flags, mode, stack selection, pending/enabled interrupts, priorities, VTOR, SRAM) and a random
    opcode (biased towards the wide encodings and the data-processing group), one instruction, then registers, flags, exception state, SRAM, the log and `on_break` calls must
    match. Mutants (LSRS carry at shift 32, MOV to SP, LDRSH sign, SXTB, the EXC_RETURN value, the PendSV priority test) all fail it; the first version of the opcode
    distribution let the LSRS-by-32 mutant survive, which is why it is biased. (3) The whole suite, `tests/cpp/test_cpu.cpp`, and trace replay.
  - **Two bugs in the existing code, found by (2), fixed in their own commit before this one** (`fix: banked stack pointer is masked on a stack switch; pure ROR by a
    multiple of 32`): the Cython `switch_stack` copied the banked word into SP unmasked (the pure core masks it); the pure `ROR` by a multiple of 32 pushed `input << 32` into the
    native `u32` helper. An unaligned halfword access is deliberately not compared (undefined in both, as in the bus parity test): the trial moves its base register instead.
  - Speed (this machine, noisy): CircuitPython boot 4.5 s -> 3.5 s; synthetic loop (clock ticked after every instruction) 19-21 -> 25.5 Minstr/s. The loop itself is still
    the Cython `_execute_batch`, one `execute_instruction()` call per instruction; step 4c moves it.

- 2026-10-03: **Phase 2, step 4a - the system bus is one C++ object (`core/bus.hpp`); the CPU can now call it directly.**
  - What `RP2040.read/write_uint8/16/32` of `_rp2040.pyx` decoded is now `Bus`: the caller-owned `MemoryMap`, the `WindowMap`, a handler each for the PPB and SIO,
    and a `BusHost` (`warn`, `dpram_written`) for the two things it cannot do itself (log, tell the USB controller). Every quirk moved with it (unaligned 32-bit reads
    warn and are served anyway; a sub-word access nobody serves is a read-modify-write of the aligned word, but a *window* is tried first with the value replicated; the
    handlers get the caller's unmasked int64; the 256 MiB at 0xD0000000 is all SIO's). The Cython methods are now three lines: call, `raise_if_pending()`, return.
  - `RP2040.sio` and `RP2040.ppb` are properties; assigning a block registers a native handler if its **type** has `_native_window`, otherwise a Python trampoline that
    looks the methods up on every call (so a recorder/profiler/test double that replaces the PPB or SIO keeps seeing every access). The Python SIO trampoline keeps the
    old `int(...) & 0xFFFFFFFF` truncation of the divider's quotient.
  - Proof: `tests/cpp/test_bus.cpp`; the whole existing suite unchanged (1208 passed on the native build) - `test_memory_map_parity`, `test_peripheral_windows`,
    `test_sio_bus` exercise every path - plus `tests/test_bus_host.py` (warning texts identical to the pure bus, a raising logger/PPB/DPRAM hook surfaces from the access,
    a replaced PPB sees offsets and the read-modify-write), and `trace_block.py sio` on cp-boot: 0 mismatches.
  - Design notes for 4b: the "a Python callback failed" flag is deliberately *not* a C++ global - every extension is its own shared object, so a header-level `inline` variable
    would be one copy per module. It stays the single slot in `_pending.pyx`; the C++ CPU will ask for it through a host function (`bool (*failed)(void*)`) after each bus access.
    The batch loop cannot be pure C++ until Phase 3: it calls `pio.advance()` (Cython, `_pio.pyx`) every iteration and polls `Simulator.stopped`; both stay host
    callbacks until then.

- 2026-10-03: **Phase 2, step 3 of 4 - SIO is C++ (registers, spinlocks, the divider and both interpolators), reached by the bus without Python.**
  - `native/core/interpolator.hpp` and `native/core/sio.hpp` (`SioBlock`): a line-for-line translation of `interpolator.py` / `sio.py` (now `_sio.py`, kept as the
    pure-Python reference and the oracle; `sio.py` became the facade, re-exporting `RPSIO` and every register offset). Quirks kept on purpose, because trace
    replay is the acceptance test: the divider's quotient is a genuinely fractional `double` (only the bus truncates it); the divide-by-zero result tests
    the dividend as written with `> 0`; pin re-evaluation after a write is GPIO-only and skipped for spinlock writes; an invalid write warns with the *unmasked*
    value. The one way the Python block raises - a divisor that is non-zero as written but zero as 32 bits (2**32) - is reported by the block
    (`kSioFailDivideByZero`) and re-raised as `ZeroDivisionError` by the shell, with quotient/remainder/CSR/cycles untouched, as in Python.
  - Everything the block cannot own goes through a `SioHost` of function pointers (Python trampolines today, the C++ GPIO block and CPU in Phase 3 / step 4): pin
    input levels, `check_for_updates()` of the pins whose SIO-driven state changed, the divider's 8 cycles, the logger - same messages, word for word.
  - `native/_sio.pyx`: the shell (`gpio_value`, `gpio_output_enable`, the QSPI pair, the divider operands as properties; `interp0`/`interp1` views with the pure
    `Interpolator`'s attribute names). SIO is not a peripheral-table window (0xD0000000+), so `RP2040.sio` became a property: a block whose **type** has
    `_native_window` is called by the bus through its C++ handler (a wrapper/recorder that merely forwards attributes is not bypassed and keeps the Python path).
  - Proof: `tests/cpp/test_sio.cpp` (expectations cross-checked against the Python block, not assumed); `tests/test_sio_parity.py` - 80 randomized register
    sessions plus 120 structured interpolator sessions (every control-word field drawn on purpose) against the pure block on a stand-in chip that records pin
    updates, cycles and logs; `tests/test_sio_bus.py` - native chip vs pure chip over random 8/16/32-bit SIO bus traffic, a wrapper still seeing every access,
    and divide-by-zero / failing pin update surfacing from the bus write. Mutation checks (cycles 8->9, divide-by-zero `>` -> `>=`, a skipped pin, blend alpha mask,
    interpolator mask) all fail the suite. The first interpolator mutants *survived* the uniform-random session (blend only acts from INTERP0's CTRL_LANE0), which
    is why the structured one exists. Real firmware: `scripts/bench/trace_block.py sio` (mp-idle and cp-boot, recorded and replayed across native/pure
    combinations) gives 0 mismatches.
  - Measured on its own (CircuitPython boot, `cp-boot`, two runs each, pure `_sio.RPSIO` vs native on the same chip): 4.82/4.70 s -> 4.45/4.61 s, i.e. about 5%.
    Small on purpose: every SIO access still crosses into Python for the *edges* (pin levels, `check_for_updates`, cycles, log) and the CPU still lives in Cython; step 4
    (CPU + batch loop) is what removes those trampolines. `mp-idle` is too short (0.13 s) to show anything.

- 2026-10-03: **Phase 2, step 2 of 4 - the TIMER is C++, and a firmware's TIMELR poll no longer touches Python.**
  - `native/core/timer.hpp`: `TimerBlock`, a translation of `peripherals/timer.py` (kept as the pure-Python reference and the oracle): the counter
    epoch, the latched high word, INTR/INTE/INTF, PAUSE and four alarms scheduled on the C++ `Clock`. The two things it cannot own reach the outside through
    a `TimerHost` of function pointers - the interrupt line (`rp2040.set_interrupt` today, the NVIC later) and the logger. Every observable of the Python block is
    kept, because trace replay is the acceptance test: each INTR/INTE/INTF change and each alarm fire re-announces **all four** lines in order; which accesses
    warn and with exactly what text; the `raw_write_value` the alias path gives INTR and ARMED; an alias write first *reads* the register (TIMELR's latch included).
    A failing interrupt-line call stops the work at once, as the exception would.
  - `native/_timer.pyx`: the Python-facing shell with `BasePeripheral`'s surface (`read_uint32`, `write_uint32`, `write_uint32_atomic`, `reset`, `warn`..., `name`,
    `rp2040`, `clock`, `raw_write_value`) plus `int_status`. `peripherals/timer.py` became the facade (as `pio.py` is) and the pure class moved to
    `peripherals/_timer.py`. **The block is served by its own C++ functions**: the bus's window registry gained a native path - a block that has `_native_window`
    *on its type* hands over the addresses of its C++ read/write functions and context, and the window is registered with those instead of a Python trampoline.
    The lookup is on the type on purpose: a recorder/profiler that forwards attributes with `__getattr__` must not lend its target's fast path (it would be
    bypassed), and a `Mock` must not answer for every name - both are tested.
  - `native/_pending.pyx`: the deferred-error slot became one shared module (it was a global in the bus and another in the clock), because the thing that fails and the
    thing that re-raises are now in different modules: a TIMER's interrupt callback fails inside `SimulationClock.tick()`. Alarm nodes live inside the block, so
    its `__dealloc__` unlinks them from the clock; the clock reference it needs there is a manual `Py_INCREF` that a cycle collection's `tp_clear` cannot drop.
  - **Verified** against the pure TIMER, which stays the oracle: `tests/test_timer_parity.py` - 60 randomized sessions (8/16/32-bit bus accesses, every alias, implemented and
    unimplemented registers, awkward values, direct method calls, resets, clock ticks up to 2^33 us, injected interrupt-line failures) compared on every value read, every
    interrupt-line call, every logged message, `int_status`, `raw_write_value` and the clock; plus failure-surfacing, the dropped-chip/armed-alarm case, and the wrapper bypass.
    Two deliberately broken C++ versions (INTR cleared by the decoded value instead of the raw one; `reset()` not re-announcing the lines) are caught by 28 and 60 of the 65 tests.
    Real firmware, ~1.3M TIMER events of a MicroPython boot + print + 1 s sleep, all four combinations of record/replay on native/pure: **0 mismatches** each.
    `tests/cpp/test_timer.cpp` covers the block on its own. The whole suite passes (903 + the new ones).
  - **Speed, measured on real boots** (wall clock, no profiling proxies; two runs each): CircuitPython boot **12.4 s -> 4.3-4.7 s (2.7-2.9x)** for the same 2.27 simulated seconds - it was
    97.6% TIMER accesses; its print 0.45-0.5 -> 0.33-0.34 s; MicroPython's 1 s `sleep_ms` 6.8-7.0 -> 6.3-6.9 s (it is SIO-bound next, as the access profile said).
  - Found along the way and fixed in its **own commit** (`fix: TIMER ALARMn registers hold 32 bits`): the pure block stored whatever Python int was written to ALARMn, so a
    negative or wider write made the bus's 32-bit read-back raise `OverflowError`; it now stores what the 32-bit register holds, which also removed the one "deliberate
    difference" the first C++ draft had. `tests/test_peripheral_windows.py`'s "method replaced on the instance" test now patches a pure-Python block - a native block, being
    a `cdef class`, does not allow per-instance method replacement, which is the price of serving it from C++ and is stated here rather than discovered.
- 2026-10-03: **Phase 2, step 1 of 4 - the clock and alarm scheduler are C++.** (Plan order inside Phase 2: clock -> TIMER -> SIO/PPB -> CPU and batch loop;
  the clock first because TIMER, PIO, DMA and every `ExternalDevice` alarm sit on it.)
  - `native/core/clock.hpp`: header-only C++17 `Clock` + `Alarm`: time as a double of nanoseconds, a due-time-sorted list, `tick(delta)` that fires each
    alarm at its own time, a re-read of the list after every callback, and the FIFO tie-break for equal due times (the 0044 starvation rule). Allocation-free:
    an `Alarm` is a node the caller owns and the clock links by pointer. A callback is a function pointer returning `bool`; `false` means "I failed, the
    failure is pending with the caller" and `tick` returns at once with the clock left at that alarm's time (where an exception from the callback leaves the
    pure-Python clock). Standalone checks in `tests/cpp/test_clock.cpp`.
  - `native/_simulation_clock.pyx/.pxd`: `SimulationClock`/`ClockAlarm` keep their API (including `MockClock` subclassing the cdef class) and become a shell
    over the C++ `Clock`; the callbacks are still Python callables, run by a Cython trampoline; a raising callback still propagates out of `tick()`.
    **One thing changed that the arithmetic did not need to**: the old Python-object list *was* what kept a scheduled alarm alive, and made the
    clock <-> alarm cycle visible to the garbage collector. Raw C++ pointers do neither, so `SimulationClock._armed` (a `set`) owns every linked alarm.
    Without it a fire-and-forget `clock.create_alarm(cb).schedule(n)` would never fire, and a dropped chip with alarms pending would leak 16 MiB of flash
    (this was weighed before writing it, not found afterwards).
  - Verified against the pure-Python clock, which stays the oracle: `tests/test_simulation_clock_parity.py` - 40 randomized scripts (alarms that re-arm
    themselves, arm or cancel others, or raise; reschedule; cancel; ticks including 0) compared on the firing log, every observable after every operation and the
    exceptions that escape; plus fire-and-forget, "dropped clock is collected", "raising callback leaves the clock at that alarm's time", and 50 reschedules from
    inside the callback. A deliberately broken FIFO tie-break is caught by 39 of the 40 scripts. The whole existing suite passes unchanged (900 passed).
  - Speed: the synthetic loop, which ticks the clock after every instruction, 16.7-18.1 -> 18.7-19.7 Minstr/s (three paired best-of-2 runs, +-15% noise);
    the TIMER-polling loop 9.1-9.2 -> 9.3-9.9. Modest on purpose: the batch loop still reaches the clock through Python-visible properties
    (`nanos_to_next_alarm`, `has_scheduled_alarm`), and the C++ `Clock` it could call directly is declared in the `.pxd` for that step (CPU and batch loop, step 4).
  - A test-design lesson worth keeping: two alarms that re-arm each other with zero delay loop forever inside one `tick()` - in the reference as much as in
    the port - so the randomized scripts bound every callback action; the first version hung CI-style, and `pkill -f` on a test binary's name killed the
    shell that ran it (the command line contained the pattern). The C++ test runner now has timeouts.
- 2026-10-03: **Before Phase 2 - both prerequisites the record named are done.**
  - **PIO's `write_uint32_atomic` override read, as D2's amendment required:** `native/_pio.pyx`'s `RPPIO.write_uint32_atomic` is line for line
    `BasePeripheral`'s (store `raw_write_value`; if the alias is not 0, `atomic_update(self.read_uint32(offset), type, value)`; then `write_uint32`).
    It exists only because a `cdef class` cannot inherit `BasePeripheral`. So the window handler signature `(offset, raw_value, atomic_type)` fits every
    block, PIO included, and the C++ bus's "alias decode as the default" has one definition to reproduce - including that **an alias write first performs a
    read of the register**, with whatever read side effect that block has.
  - **The trace oracle now has a pin-level input channel.** `tests/utils/mmio_trace.py` records kind `p` events - the 30 GPIO and 6 QSPI *effective*
    input levels (`input_value`: what SIO's `GPIO_IN`/`GPIO_HI_IN` actually return) - just before an SIO read of those registers whenever they differ from
    the last sample, and replay drives the pins from outside to those levels (switching each pad's input-enable on through the PADS block first: a fresh chip's
    GPIO pads reset with it off, which the self-test found by *measuring* rather than assuming - as it did for the QSPI pads, which reset pulled up, so
    `GPIO_HI_IN` is already non-zero on a chip nobody has touched). Self-test: `tests/test_mmio_trace.py` (12 tests, green on pure and native), including
    that dropping the `p` events makes the replay diverge and that a wrong `p` event is reported as the read that depends on it.
  - **Acceptance on real firmware, replayed against a fresh Python SIO:** CircuitPython boot **153,011 events, 0 mismatches** (it had 20+ before: the
    `GPIO_HI_IN` reads, 1 pin sample needed); Pico W CYW43 scan **312,034 events, 0 mismatches** (3 pin samples); MicroPython boot + print 16,837 events,
    0 mismatches. TIMER stays clean on the MicroPython runs from Phase 0 (1.3M events). Recording TIMER through the CircuitPython boot (23M events) does not fit
    `astart()`'s default timeout - the recorder's per-call cost times 23M - so that combination was not run; it is a scale limit of the recorder, not a divergence.
  So the SIO port is no longer blocked on the oracle, and TIMER never was.
- 2026-10-03: **Phase 1, second half - the window registry landed; the C++ build is now `-fno-exceptions -fno-rtti` end to end.**
  - `native/core/window_map.hpp`: header-only C++17, no allocation. A fixed table of window handlers keyed by `address >> 14` (the bus's
    own 16 KiB peripheral windows), each `{read32(ctx, offset), write32(ctx, offset, raw_value, atomic_type), ctx}`; attach on an occupied
    window **replaces** the handler (the override rule: last attached wins), detach/clear, a last-hit cache. This is the D2 amendment made
    concrete: a read gets the offset with its alias bits, a write gets the register offset, the alias as `atomic_type` and the caller's
    **full 64-bit value** (blocks that read `raw_write_value` - and SIO's signed divider - have always seen the unmasked number).
    The handler pointer types are deliberately not `noexcept` (Cython does not emit the specifier); the `WindowMap` methods are.
  - `native/_rp2040.pyx`: the bus now dispatches peripheral reads/writes (including the replicated-word 8/16-bit writes) through the C++
    registry. `chip.peripherals` is still an ordinary `dict` - a `dict` subclass whose every mutation (`[]=`, `del`, `update`, `setdefault`,
    `pop`, `popitem`, `clear`, `|=`, and assigning a whole new dict) is mirrored into the registry - so blocks, boards and tests behave as before.
    Handlers are Python trampolines that look the block's method up **on every call** (a method replaced on an instance later is still the one
    called); an exception cannot cross the C++ frame, so a failing block parks it and the bus re-raises it the moment the lookup returns.
    A block may call back into the bus from inside a handler, including a nested read that fails.
  - Tests: `tests/cpp/test_window_map.cpp`; `tests/test_peripheral_windows.py` (the dict API, replace/restore/delete, the offset/alias/value a
    block sees on reads, 8-, 16- and 32-bit writes, exceptions, re-entrancy, and 3 seeds of randomized accesses to the same blocks on a
    pure and a native chip, compared call for call); `tests/utils/chip_pair.py` (the pure/native chip pair, shared with the memory-map parity test).
    The whole existing suite passes unchanged.
  - Measured: a firmware loop reading a Python peripheral (`TIMER.TIMELR`) every 8 instructions, 7.7-8.4 -> 8.6-9.1 Minstr/s (three paired
    best-of-2 runs, +-15% noise): the registry removes ~60-110 ns of the ~500 ns a Python-peripheral access costs. The plain SRAM loop is unchanged
    (18.8 -> 19.5). The bulk of the cost is the Python block itself, which is Phase 2's job; this phase's job was to make the dispatch a C++ table.
  - **`-fno-exceptions -fno-rtti` everywhere** (the user's rule: all C++ in this project is exception-free, not just the core's headers): `setup.py`
    now passes it (`/EHs-c- /GR-` on MSVC) to every extension, so the Cython-generated translation units are held to the same rule as the core.
    It builds under gcc; the extensions import no C++ exception/RTTI runtime (`nm -D` shows only libc's `__cxa_finalize`). That would have been
    equally true without the flag - there are no throw sites - so the flag is the guard, not the symbol check, and no symbol-check test was added.
    MSVC's `/EHs-c- /GR-` is untested here; the `RP2040PY_REQUIRE_NATIVE` CI leg on Windows is what will say.
  - Found and fixed separately, because they are bugs in existing code and not part of this work: `Timer32` raised `ZeroDivisionError` for a
    ZIGZAG counter with `TOP == 0` (found by this phase's random writes into the PWM registers; rp2040js reads 0 there), and
    `MockClock.advance()` counted the current time twice (nothing used it). Each has its own commit and its own test.
  **CI result for this commit (`9c8391f`), all green:** Pre-commit on ubuntu, windows and macos - with `RP2040PY_REQUIRE_NATIVE=1`
  (`tests/test_native_extension_built.py`, added after the first Phase 1 CI round could not prove from its logs that the extension had built
  off Linux), so the extension **was built** on Windows (MSVC, `/std:c++17 /EHs-c- /GR-`) and macOS (clang, `-std=c++17 -fno-exceptions -fno-rtti`) and
  the parity tests ran there; plus the Pico SDK, Kaluma, CircuitPython and MicroPython firmware workflows. That closes the "other toolchains" item
  below for the desktop targets. **Still untried:** Android, iOS, Emscripten/Pyodide and the cibuildwheel matrix (`publish.yml` runs on release, not
  per push); the abi3 (3.11) build was built and run locally with the earlier memory-map commit but not with the window registry.
  Phase 1 is complete against the plan's list; the CPU is still the Cython `CortexM0Core` (Phase 2).
- 2026-10-03: **Phase 1, first half - the C++ skeleton and the caller-owned memory map landed and are verified; the window
  registry and the cross-toolchain checks did not, so Phase 1 stays open.**
  Done:
  - `src/rp2040py/native/core/memory_map.hpp`: header-only C++17, no exceptions/RTTI/STL, no allocation. A table of caller-owned
    regions `{base, window, size, mask, data, flags}`; `kWordIndexed`, `kSubWord` and `kNotifyOnWrite` reproduce the old bus's
    quirks exactly (flash mirrors are read-only and never used by sub-word accesses; the boot ROM is word-indexed; a DPRAM write
    is reported so the caller can run `usb_ctrl.dpram_updated`).
  - `setup.py`: every native module is now compiled as **C++17** (`language="c++"`, `-std=c++17` / `/std:c++17`, `include_dirs`,
    `depends` on the headers). All of them, not just `_rp2040`, because `_rp2040.pxd` now carries a C++ member and every module
    that cimports it compiles that struct - a C translation unit cannot. Built and run here: CPython 3.10 (normal build) and
    **3.11 abi3 (`Py_LIMITED_API`)** - C++ under the limited API works.
  - `native/_rp2040.pyx`: `_attach_memory_regions()` hands the map pointers into the four existing buffers (zero-copy: they stay
    Python-owned and pinned by the typed memoryviews), and the 8/16/32-bit reads and writes of boot ROM, flash, SRAM and DPRAM go
    through it. The Python API is untouched (`rp2040.sram`, `.flash`, ... are the same memoryviews; the CPU still calls
    `RP2040.read_uint32` etc.).
  - Tests: `tests/cpp/test_memory_map.cpp` run by `tests/test_core_cpp.py` (built with the core's own flags, `-fno-exceptions
    -fno-rtti -Wall -Wextra -Werror`, which the extension build does not use; every header must also compile on its own);
    `tests/test_memory_map_parity.py` replays 4 seeds x ~6,000 randomized and edge-case accesses (all four flash mirrors, region ends,
    the gaps, unaligned 32-bit) on the pure-Python and the native chip and compares every read, the final bytes and the DPRAM hook
    calls. A deliberately wrong flash mirror mask in the native build is caught by all four seeds.
  - Speed, as expected for a translation: synthetic loop 15.2-17.9 Minstr/s before, 17.7-21.4 after (three paired best-of-2 runs, +-15% noise) -
    not worse; the abi3 build still runs ~6.3 (the same ~2.5-3x abi3 penalty as before, not changed by this).
  - Found by the parity test: an odd-address 16-bit write is outside the old contract (`write_uint16`: "we assume that address is
    16-bit aligned") - the pure bus drops the high byte and the old Cython bus wrote past a 4-byte scratch buffer. The parity test keeps
    16-bit accesses aligned and says so; the C++ map answers "not handled" for any access whose bytes would run past a buffer
    (the old answer was undefined behaviour or `IndexError`).
  Not done, so Phase 1 is not closed:
  - The **window registry** (`attach_window`, the handler table with `(offset, raw_value, atomic_type)` per D2's amendment): everything
    that is not memory is still dispatched by the Cython `RP2040`'s dict lookup.
  - **Other toolchains.** Only Linux/gcc was built (3.10 normal, 3.11 abi3). MSVC (`/std:c++17`), clang/macOS, Android, iOS, Emscripten
    and the cibuildwheel matrix are unverified; the per-toolchain flags in `setup.py` are a best reading, tested on none of them.
  - The `.pyi` stubs and mypy coverage need no change here (no Python-visible name changed) - noted so the next phase checks it again.
- 2026-10-03: **Phase 0 done - harness merged, workloads measured, parity oracle self-tested (and it found something).**
  Committed: `scripts/bench/synthetic.py` (the 8-instruction loop, `--mmio` variant), `scripts/bench/profile_access.py`
  (per-block access counting through `MicroPythonDevice`, per phase; four workloads), `scripts/bench/trace_block.py`
  (record a real boot's traffic to one block, replay, diff), `tests/utils/mmio_trace.py` + `tests/test_mmio_trace.py`
  (the record/replay/diff library and its 8-test self-test, green on the pure and native builds).

  *Access profile, more workloads* (Cython build, 3.10; counts are exact, accesses are those reaching a Python block through
  the bus; "boot" = until USB enumerates, so it is short for MicroPython):

  | workload / phase | simulated | accesses | by block |
  |---|---|---|---|
  | `mp-idle` MicroPython 1.21, Pico: boot | 15 ms | 18.3k | SIO 60%, TIMER 18%, SSI 14%, USB 5% |
  | `mp-idle`: a print | 6 ms | 11.6k | SIO 47%, TIMER 44%, USB 9% |
  | `mp-idle`: `sleep_ms(1000)` | 1009 ms | 2.25M | SIO 45%, TIMER 45%, USB 9% |
  | `cp-boot` CircuitPython 10.2.1, Pico: boot | **2265 ms** | **23.7M** | **TIMER 97.6%**, SSI 2.3%, SIO 0.05% |
  | `cp-boot`: a print | 55 ms | 315k | TIMER 55%, SIO 45% |
  | `cp-boot`: `sleep(1)` | 1009 ms | 2.12M | SIO 51%, TIMER 37%, USB 12% |
  | `picow-scan` MicroPython (resolver default for `pico_w`: 1.23.0): boot | 21 ms | 18k | SIO 58%, TIMER 16%, SSI 14%, USB 7% |
  | `picow-scan`: CYW43 up + scan | 694 ms | 1.93M | **PIO1 65%** (1.09M reads), SIO 16%, TIMER 12%, USB 4%, DMA 2.4% |
  | `pio-dma` MicroPython 1.23, Pico (`rp2.DMA` is not in 1.21) | 51 ms | 64k | SIO 81%, USB 9%, TIMER 7%, PIO0 1.6%, DMA 1.6% |

  What this changes in the plan:
  - **TIMER and SIO stay the first targets** - they lead or nearly lead every workload except the Pico W scan, so Phase 2's scope holds.
  - **CircuitPython's boot is the strongest argument for porting TIMER:** 23.1M TIMER reads in 2.27 simulated seconds
    (~10k per simulated ms), i.e. a boot that costs 20.5 s of wall clock on the Cython build, most of it crossing into
    the Python TIMER at ~0.5 us each.
  - **USB is warmer than the plan assumed:** 5-12% of accesses in every idle/sleep window (its own SOF-driven polling),
    even before any data moves. Phase 4 lists it among the "communication blocks"; it should be ordered by this count, not by
    category, and probably sits before UART/SPI/I2C.
  - **On Pico W the hottest block is PIO1** (the CYW43 gSPI driver polling FIFO status), not the CYW43 Python code itself:
    Phase 3 (PIO) is therefore also what unblocks Phase 5's question, and Phase 5's gate should be evaluated *after* PIO
    is native, not before.
  - **DMA is small in these workloads** (1.6-2.4%) - but `pio-dma` is a light script (51 ms); do not demote DMA on this evidence alone.

  *Synthetic benchmark, re-taken with the committed script* (`synthetic.py`, best of 2): Cython 3.10 13.4 Minstr/s,
  PyPy 3.10 24.4 (warmed), CPython 3.11 pure 0.73 - same order as Section 1 (single runs move +-20%, more on PyPy's JIT warm-up).

  *Parity oracle (`tests/utils/mmio_trace.py`)* - replaying a block's recorded session against a fresh instance of the same
  Python block:
  - **TIMER: 0 mismatches** on MicroPython boot + print (5,632 events: 4,126 reads, 478 writes, 1,028 interrupt changes), on a
    run including the 1 s sleep (**1,313,577 events**, replayed in 1.3 s), and on the pure-Python build. A trace of 1.3M events
    is 5.5 MB gzipped.
  - **PPB** (32 events) and **SIO** on MicroPython (17,715 events): 0 mismatches.
  - **SIO on the CircuitPython boot (152,254 events): mismatches** - reads of `0x08` (`GPIO_HI_IN`) recorded as `30`, replayed as `2`.
    That is an input that is not bus traffic (the QSPI pad levels, driven while the flash is being accessed), exactly the case the
    module docstring warns about. The harness is *correct to flag it*; the consequence is a **Phase 2 prerequisite: SIO's
    trace needs a pin-level input channel** (record the GPIO/QSPI level changes as events and feed them to the replay)
    before an SIO port can be judged by this oracle. TIMER's inputs are bus accesses and time, so TIMER can be ported against
    it as it stands.
  - **A contract fact the C++ bus has to match:** the bus sends *every* peripheral write through `write_uint32_atomic()`
    (a plain write is `atomic_type` 0), while SIO and PPB get `write_uint32()` directly. Only `PIO`'s Cython `RPPIO`
    overrides `write_uint32_atomic` (plus `BasePeripheral`'s default) - the other blocks inherit the default, which stores the
    raw written value in `self.raw_write_value`, reads the block's own register, applies the alias, then writes. **But TIMER, IO,
    UART, DMA, USB (and the pure-Python PIO) read `raw_write_value` themselves**, i.e. they depend on seeing the *pre-alias*
    value. So D2's "the C++ bus decodes the aliases itself" is only faithful if the handler is also told the raw value: the
    window handler's write entry point must carry `(offset, raw_value, atomic_type)` and let the block decide, with the alias
    decode as the default. That is an amendment to D2 (made here and in D2, not silently); Phase 1 fixes the signature, and
    PIO's own override has to be read first.
  - Not captured, by design of the first version: pin levels, direct method calls between blocks, and `reset()` calls that bypass the
    dispatch table (none showed up as a mismatch for TIMER).

  Exit criteria of Phase 0, against the plan: harness merged (yes); workload table with the three additional workloads (yes -
  four, the fourth being the PIO+DMA script; "an idle USB CDC session" is covered by `mp-idle`/`cp-boot`'s sleep phases, which
  run with the CDC attached); parity harness self-tested on a Python block against itself (yes - TIMER, PPB, SIO-on-MicroPython; one
  honest failure on SIO-on-CircuitPython, recorded above). Not done: re-taking Sections 1-4 on a CI-comparable machine (this
  environment is a shared 4-core sandbox).
- 2026-10-03: language level fixed at C++17 (D4).
- 2026-10-03: proposed. Measurements above taken; harness not committed; no phase started.
