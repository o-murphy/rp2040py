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
  decode (`atomic_update`, today in `BasePeripheral`) moves into the C++ bus so a Python block writes only `read32`/`write32`.
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
- GPIO/IO/pads and PIO + state machines (already Cython: 0031, 0047) to C++. Pin changes go to the caller's event ring,
  with a direct-callback mode for an `ExternalDevice` that must answer combinationally (SPI bit-bang) - *measured*, not assumed, which one
  each shipped device needs.
- XIP/SSI/QSPI (the flash bring-up the 3.7k SSI accesses are).
- Exit: `tests/test_pio*.py`, `test_gpio_pin.py`, `test_ssi.py`, `test_qspi_pads.py` green unchanged; PIO benchmark recorded;
  the CYW43 live boot (0027) still reaches a scan.

### Phase 4 - the communication blocks and DMA
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

## Progress log

- 2026-10-03: language level fixed at C++17 (D4).
- 2026-10-03: proposed. Measurements above taken; harness not committed; no phase started.
