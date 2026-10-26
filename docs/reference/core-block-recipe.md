# Adding a peripheral block to the C++ core

The checklist every block of `src/rp2040py/native/core/` follows (record
[0096](../records/0096-cpp-mcu-core.md), "Core host contract" and the per-block recipe). The shared pieces are in
`core/core_host.hpp`; `core/uart.hpp` + `native/_uart.pyx` is the smallest complete example.

## A. Before any code: what the block must do (the datasheet, then the SDK)

The steps below make the C++ equal to the Python reference; they say nothing about whether the reference is *right*. That is settled first, from real sources, by the method of record
[0098](../records/0098-datasheet-conformance-audit.md) ("Method") - the same "3g rule" as for devices: every hardware fact is cited to an upstream source, never taken from memory or from
what looks plausible. This applies to every block that is ported from now on - ROSC, IO_BANK0/PADS_BANK0, XIP_CTRL, USB and the clock tree among them: it is not an audit to do afterwards, it is the first step of the port, followed by the oracle and the rest of this recipe in the same pass (the blocks ported earlier were audited after the fact, record 0098). The order that worked for SYSINFO/TBMAN (0098, "SYSINFO and TBMAN"):

1. **Get the datasheet as text** (not in the repo): `pdftotext -layout rp2040-datasheet.pdf rp2040.txt` from
   <https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf>. Run the mechanical pass on the block:
   `python scripts/audit/datasheet_conformance.py rp2040.txt BLOCK`. It reads the register tables and checks the reference's resets, reserved bits, read-only bits and missing registers. Every
   finding is a question for the datasheet, not yet a bug (a reset of `-` or a `0x0` placeholder on a read-only field is the usual false alarm).
2. **Read the chapters, not only the tables**: the block's "Overview" and "Operation" sections say what the registers *do* (enable gating, what a write while disabled does, FIFO levels, DREQ
   conditions, interrupts). The tool cannot see any of that. Write each rule down with its section number; the ones the reference does not follow are the findings.
3. **Cross-check against pico-sdk** (`raw.githubusercontent.com/raspberrypi/pico-sdk/master/...`): `src/rp2040/hardware_regs/include/hardware/regs/*.h` (bit layout), `hardware_structs`, and the
   driver `.c` of the block (how real firmware drives it, what it polls). The generated `*_RESET` values in the register headers are not always what the silicon or the SDK's own code expects
   (`sysinfo.h` says manufacturer `0x926`, `platform.c` asserts `0x927`; `tbman.h` says reset `0x5` for a register with two defined bits) - when they disagree, the SDK *code* and the field table win,
   and the note says which source was followed and which was not.
4. **Where the sources are silent, say so and do not invent**: keep the existing behaviour, mark it "not independently sourced" in the reference's docstring and in 0098's "Left, unsourced"
   list (SYSINFO `PLATFORM`/`GITREF` are the example). A feature the datasheet describes and the reference lacks is *documented* in 0098's backlog table, not implemented (CLAUDE.md:
   documenting is not implementing) - it needs its own go-ahead.
5. **Fix the reference first**, in its own commit (and ask, per section 3 below), with a Python test that cites the datasheet section or SDK file and runs on both builds
   (`tests/test_X_datasheet.py`, `RP2040PY_SKIP_CYTHON=1` as well). Only then port: the oracle keeps the C++ equal to the corrected reference, and a logic mutant of every new rule proves the
   oracle would notice it being dropped.
6. **Record it** in 0098 (a section for the block: what was wrong, which source, which test and mutant; what was left, unsourced) in the same commit as the port.

## 0. Constraints (compiled in, not promised)

Header-only C++17; no exceptions, RTTI, STL or allocation; single-threaded. `tests/test_core_cpp.py` builds every header and every
`tests/cpp/test_*.cpp` with `-fno-exceptions -fno-rtti -Wall -Wextra -Werror`, and macOS CI uses clang - run the headers through
`clang++` too (`-Wunused-const-variable` is the usual surprise: mark a constant only a shell reads `[[maybe_unused]]`).

**Never name a constant after a macro a platform header defines.** Python's `pyconfig.h` on Windows defines `PLATFORM` as the string `"win32"`, so `constexpr uint32_t PLATFORM = 4;` compiles on Linux and macOS and breaks only under MSVC (found by CI on SYSINFO/TBMAN: the register is `REG_PLATFORM` in C++). The same goes for anything `<windows.h>` drags in (`min`, `max`, `ERROR`, `IN`, `OUT`, `NEAR`...). A Linux compile does not see it: the standalone checks can be run once with `-DPLATFORM='"win32"'` to emulate the clash.

**Every macro starts with `RP2040PY_`** - include guards (`RP2040PY_CORE_X_HPP`) and helper macros alike (`RP2040PY_OP`, `RP2040PY_R`, `RP2040PY_PIO_TRY`...), and a helper macro is `#undef`-ed at the end of its header. A macro has no namespace: an unprefixed `R` or `OP` is global to everything that includes the header. `tests/test_core_cpp.py` fails on any `#define`/`#undef` in `core/*.hpp` without the prefix.

## 1. The reference and the facade (before any C++)

- `peripherals/X.py` -> `git mv` to `peripherals/_X.py` (the pure-Python reference, kept as the oracle). Both chips import their block directly: the pure chip
  (`_rp2040.py`) `peripherals/_X`, the native chip (`native/_rp2040.pyx`) `native/_X`. **No facade by default.** Add a `peripherals/X.py` facade (imports `native/_X` when it can;
  `native_disabled()` / `RP2040PY_SKIP_CYTHON` forces the reference) only when something outside the two chips imports the block's name or constants - the public
  a device's type hint or constants, a test (no block class is a top-level export any more: `RPPIO`, `RPI2C` and `RPUSBController` left it) - because a native shell cannot be built on its own (it is the view of the chip's block). 15 facades nobody imported
  were removed (record 0096, Phase 5b).
- Anything the shell and the reference must compute the same way (a rounding, a division, an error message) is a function in `_X.py`
  that both call - not two copies. (Cython's `cdivision=True` turned `int / 64` into integer division in the UART shell.)

## 2. The oracle (before any C++), proven before it judges

`tests/utils/X_diff.py` + `tests/test_X_diff.py`: two chips, one generated operation stream, everything observable compared after every
step - every register offset through the bus **and its XOR/SET/CLR aliases**, reads of unimplemented offsets (the warnings are compared),
the block's side effects (IRQ line, DREQs, callbacks) as an ordered log, an exception on one side and not the other. Then prove it:
agreement over several seeds (the comparison is `snapshot()` plus the registers read through the bus), perturbations of the candidate - **bus writes or the explicit inputs, never a back-door attribute** - caught at the step they are made, logic mutants
of the reference (one subclass each) caught, coverage counts asserted so a green run means something. A mutant that survives is
a hole in the generator - widen it.

## 3. The block (`core/X.hpp`)

- Registers as plain members; `read(offset)`, `write(offset, int64 value)`, `write_atomic(offset, raw, alias)` using
  `decode_atomic()` (remember the raw value, decode against a *read*, with its side effects), `reset()`.
- A `XHost` of plain function pointers + `void* ctx` + `const int* failed`. Everything outside the block is reached through it. Report an
  unimplemented register as `RegWarn` data, never as a string. A host call that fails makes the block return `false` at once, leaving
  exactly the state the reference's exception would have left.
  The host struct is for the **world** only (a device on a bus, a pin listener, a console, a reset request): it has no slot that lets
  the host stand its own object in for a chip block, and a new block adds none ([the border plan](../tasks/narrow-the-border-plan.md)).
  The chip's blocks are never replaced; the two backends are compared as whole chips (`tests/utils/twin.py`).
- `WindowHandler window_handler() { return BlockWindow<XBlock>::handler(this); }`.
- Keep the reference's quirks and say so in the header comment, each one pinned by the oracle or the C++ checks. A real bug of the
  reference is *not* fixed in the port: fix it in its own commit first (and ask), then port (section A, step 5).

## 4. C++ checks and mutation testing

`tests/cpp/test_X.cpp`: directed tests of every behaviour, with a recording host (and failure injection per host function).
Then the textual mutation pass over the header, `python scripts/audit/cpp_mutants.py X` (swap, drop, off-by-one every line of logic; built with address + undefined sanitizers and `-fno-sanitize-recover`, so an
out-of-bounds index a mutant introduces is a failure): every survivor is a gap in the checks to close, or an *equivalent* change recorded with its reason in `scripts/audit/cpp_mutants_equivalents.json`
(the script exits 1 on an unlisted survivor and on a stale entry). Run it when the header or its checks change - hundreds of compiles, so not per push.

## 5. The shell (`native/_X.pxd/.pyx/.pyi`)

A `cdef class` over the block; trampolines for each host function that catch `BaseException`, `park_error()` it and return `False`;
**no attribute-per-register mirrors** - state is changed through the bus and observed through one `snapshot()` (a dict of plain values, built by the shell in one call from the C++ block's public fields, with the same keys as the reference's `snapshot()`; record 0096, the snapshot series). Keep a property only for plumbing another block or the chip reads (a frequency, a pin image, a tick hook) and an explicit input method only for what the bus cannot do (`fire_timeout()`, `raise_interrupt()`, `set_counter()`); `read_uint32`/`write_uint32`/
`write_uint32_atomic` that `raise_if_pending()`; `_native_window()` on the *type* (the bus registers the block's own C++ functions).
Check what the differential says the first time it runs reference against native - it will find something.

## 6. Verify and record

Differential over many more seeds than CI runs; live boots (MicroPython, CircuitPython, Kaluma, Pico SDK as far as the block is
touched) and the one scenario that exercises it; an A/B against the commit before if the block is on a hot path; full
`uv run pre-commit run --all-files` on both builds; a progress-log entry in the record and the tracker row.
And the living checklist: update the block's rows in [feature-coverage.md](feature-coverage.md) in the same commit (set the status of what the pass implemented, add what it found missing with its *when*), and the stub / partial lists in `scripts/audit/register_status.py`, and regenerate [register-coverage.md](register-coverage.md) (`scripts/audit/register_coverage.py`, see its docstring) when registers were added.
