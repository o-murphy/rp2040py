# 0102 - a C ABI around the core, for wasm (and, later, cffi)

Status: proposed (2026-10-16), **documentation only - nothing designed in detail, nothing built.** Companion to [0096](0096-cpp-mcu-core.md): this is the "host interface" of its gate G6 and the entry point of its Phase 6 (wasm). Written down on the maintainer's call ("for wasm"); per `CLAUDE.md` a note about what should eventually happen is not a go-ahead to implement it.

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
