# 0017. Note — Performance: pure-Python interpretation vs V8

- Status: Note (rationale + measurements) - **the figures below are historical; see the 2026-10-05 update at the end**
- Recorded: 2026-08-05
- Related: 0011, 0013, 0015, 0016

<!-- migrated verbatim from docs/PORTING.md lines 499-743 -->

### Performance: pure-Python interpretation is much slower than V8

`CortexM0Core.execute_instruction()` is a large `if`/`elif` chain re-evaluated for every emulated
instruction - straightforward to port faithfully, but CPython interprets it roughly two orders of
magnitude slower than V8 JIT-compiles the equivalent JS. This is a throughput limitation, not a
correctness bug - every interpreter below reaches the same correct "Hello, MicroPython!" REPL
output, just at very different speeds.

`demo/benchmark.py` is a reproducible benchmark for this (see its docstring for usage): a
synthetic mode that isolates raw instruction-dispatch overhead (no bus/peripheral traffic beyond
RAM fetches), and a firmware mode that boots a real image and runs a script to a
REPL/`--expect-text` match, the same workload `ci-micropython.yml` and `ci-pico-sdk.yml` exercise.
Measured on this machine:

| Interpreter                               | Synthetic (instructions/sec) | MicroPython 1.28 + littlefs, running a typical script |
| ----------------------------------------- | ---------------------------- | ----------------------------------------------------- |
| CPython 3.10                              | 499,806                      | 188.98s (342,244 steps/sec)                           |
| CPython 3.10 + `rp2040py.native` (Cython) | 2,049,726 (~4.1x)            | 46.65s (~4.1x, 1,386,605 steps/sec)                   |
| CPython 3.14 + `PYTHON_JIT=1`             | 961,218 (~1.9x)              | 113.77s (~1.7x, 568,486 steps/sec)                    |
| PyPy 3.10                                 | 37,989,746 (~76x)            | 11.59s (~16x, 5,580,638 steps/sec)                    |

("Steps/sec" counts `WFI`/`WFE` clock-fast-forward iterations alongside real instructions, so
it's not directly comparable to the synthetic column's pure instructions/sec - the *ratio between
interpreters* is what's meaningful here, not the absolute numbers.) PyPy's JIT is decisively the
biggest lever; CPython 3.14's still-experimental JIT is a smaller but real, zero-code-change win.
`rp2040py.native` (see "Cython port of the interpreter core" below and `docs/BACKLOG.md`) is on by
default whenever a C compiler is available, so the plain "CPython 3.10" row above is actually the
*worse* case (no compiler, or `RP2040PY_SKIP_CYTHON=1`) - most real installs land on the native row
without doing anything differently. It doesn't touch PyPy at all (compilation is skipped there on
purpose - see below), so PyPy remains the fastest option for CPU-bound runs regardless.
The "MicroPython 1.28 + littlefs" column specifically times booting, mounting a littlefs image,
and running a resident `while True: print(...); time.sleep(1)` script (`tests/micropython/main.py`,
what `ci-micropython.yml` actually boots) to its first line of output - reaching the bare REPL
prompt itself, with no such script auto-running, is fast on every version tested (well under a
second) and isn't what this table measures.

**MicroPython 1.21 is the recommended version to boot in the emulator, not 1.28**: running that
same script is dominated by how much work the firmware itself does per loop iteration, not just
interpreter speed. On the same machine, under the same CPython 3.10, MicroPython 1.21 reaches that
script's first `print()` in 3.72s (1,418,835 steps) versus 1.28's 188.98s (64,679,599 steps) - about
45x fewer steps for byte-identical script content, with the instruction count reproducing exactly
run-to-run (deterministic - a property of the firmware's own control flow, not host-speed
variance). Profiling shows the CPU core essentially never reaches `WFI`/idle during 1.28's run
(waiting is near-zero even over tens of millions of steps), so this is real Thumb code being
interpreted somewhere in 1.28's own compiled firmware, not an emulator hang - the exact upstream
cause (compiler, GC, string formatting, or something else specific to what changed in 1.28's
firmware between it and 1.21) hasn't been isolated further, since it lives in MicroPython's own
compiled code rather than anything in this repo. 1.28 still boots and mounts a `mklittlefs`-built
littlefs image correctly (that's exactly the version pinned `disk_version` fixed compatibility
for, see below), it's simply much more expensive to run typical resident scripts on; use it only
when you specifically need whatever changed between 1.21 and 1.28.

Two mitigations, worth combining:

- **Run CPU-bound demo/CI workloads under PyPy** (`uv run --python pypy3.10 --no-dev -- python
  demo/micropython_run.py ...`) instead of CPython. PyPy's JIT gave a ~15x instructions/sec
  speedup in local benchmarking (once warmed up) and comfortably completes the same MicroPython +
  littlefs boot in well under a minute. Note `--no-dev` (or a separate PyPy-only sync): the `dev`
  dependency group's `mypy` pulls in `ast-serialize`, whose PyO3 build currently requires PyPy
  ≥3.10, so `uv sync`-ing the full dev group under PyPy 3.10 fails - this only matters for
  mypy/ruff/pytest tooling, not for running the emulator itself, whose only runtime dependency
  (`pyelftools`, for `--bootrom` ELF parsing) is a pure-Python wheel with no PyPy-specific build
  issues of its own. `ci-micropython.yml` and `ci-pico-sdk.yml` run the firmware-boot steps against a
  `python_runtime` matrix - `pypy-3.10`, `cpython-3.10`, and `cpython-3.14` (with `PYTHON_JIT=1`)
  - each with a 10-minute timeout, so a regression specific to any one interpreter can't slip
  through even though PyPy is the realistic day-to-day way to run this.
- If profiling ever calls for it, `RP2040.read_uint32`/`write_uint32` and
  `CortexM0Core.execute_instruction()` are the hot path (per `cProfile` on a real boot): the
  bootrom-bounds check used to call `len()` on a `Uint32Array` (a Python-level `__len__`) on every
  single bus access regardless of target address - now cached once as `RP2040.bootrom_byte_size`.
  `CortexM0Core.pc` also used to be a `property` indirecting through `Uint32Array.__getitem__`/
  `__setitem__`; the hot path inside `execute_instruction()` now indexes
  `self.registers[PC_REGISTER]` directly (the `pc` property itself is unchanged and still used by
  external callers like the demo scripts and GDB target). `CortexM0Core` also used to expose its
  own `read_uint32`/`read_uint16`/`read_uint8`/`write_uint32`/`write_uint16`/`write_uint8` methods
  that did nothing but forward to the identically-named `RP2040` methods - pure indirection with
  no external callers (nothing outside the class used `core.read_uint32(...)` etc.), so those were
  removed and all internal call sites now call `self.rp2040.read_uint32(...)` etc. directly.
  `utils/bit.py`'s `read_uint16_le`/`write_uint16_le`/`read_uint32_le`/`write_uint32_le` used to
  slice out a temporary `bytes` object and call `int.from_bytes()`/`int.to_bytes()` on it; they now
  use module-level pre-built `struct.Struct("<H")`/`struct.Struct("<I")` instances'
  `unpack_from`/`pack_into` instead, which read/write directly against the buffer with no
  intermediate allocation - measured ~40% faster for these four functions in isolation, and (more
  strikingly) cut the real MicroPython + littlefs boot time roughly in half under PyPy specifically
  (15.87s -> 9.55s), since PyPy's JIT couldn't optimize away the old temporary-`bytes`-object
  allocation the way it can lean on the already-C-implemented `struct` module. Together with the
  two items above, these gave roughly a 20-25% instructions/sec improvement under CPython in local
  benchmarking.
- **`execute_instruction()` now dispatches via a precomputed table instead of a linear `if`/`elif`
  scan.** Each of the ~90 instruction patterns became its own `_op_*` method; a module-level
  `_DISPATCH_TABLE` (65536 entries, built once at import time from `_DISPATCH_PATTERNS`, in
  original priority order) maps `opcode -> handler` directly for O(1) lookup. Seven patterns
  (`BL`, `DMB`, `DSB`, `ISB`, `MRS`, `MSR`, `UDF` encoding T2) need `opcode2` as well as `opcode`
  to decode correctly, which a flat `opcode`-keyed table alone can't express; their opcode-only
  prefixes all happen to fall inside `0xF000`-`0xF7FF` with zero overlap from any other
  instruction (verified exhaustively, and enforced both by an assertion in
  `_build_dispatch_table()` and by `tests/test_dispatch_table.py`), so that narrow range is
  special-cased to a small hand-written `_resolve_wide()` resolver instead of the table, and the
  main table simply never gets populated there. This was the single biggest lever in the whole
  session: `execute_instruction()`'s own `cProfile` self-time dropped ~65% (the O(n) scan was
  replaced by a single array index), cumulative real-boot `cProfile` time dropped a further ~21%
  on top of the two items above, and the synthetic instructions/sec benchmark went from ~251,654
  to ~426,854 under CPython 3.10 (+70%). Generated mechanically (a one-off script split the
  original `if`/`elif` chain into methods verbatim, preserving every condition's exact source
  text as a lambda rather than hand-deriving mask/value bit patterns) and verified via the full
  126-case instruction test suite plus real MicroPython + littlefs boots on both old (1.16) and
  new (1.28) firmware before and after.
- **`CortexM0Core.registers` is now a plain `list[int]` instead of `Uint32Array`.** It's the
  hottest bus in the whole emulator - every single instruction reads/writes several of its 16
  slots - and every access went through `Uint32Array.__getitem__`/`__setitem__`, a Python-level
  method call, versus a `list` subscript's C-level bytecode op. Compared against rp2040js (Node
  v26/V8) booting the identical firmware+littlefs+script combination the table above measures:
  rp2040js finishes in 4.11s versus CPython 3.10's pre-this-change 224.79s (~55x) and even PyPy's
  11.51s (~2.8x) - `cProfile` on that run showed `Uint32Array.__getitem__`/`__setitem__` alone
  accounting for over 1.8 million calls in a 356K-instruction sample (~3.3 reads + ~1.9 writes per
  instruction), the same class of overhead the `PC_REGISTER` direct-indexing change above already
  removed for one register, now extended to all 16.

  The catch: `Uint32Array.__setitem__` did `int(value) & 0xFFFFFFFF` unconditionally on every
  write, and roughly 60 call sites across `cortex_m0_core.py` (plus `tests/utils/
  rp2040_test_driver.py`'s direct `core.registers[i] = ...` pokes) relied on that implicitly -
  Python ints don't wrap at 32 bits on their own, so e.g. `_subtract_update_flags()` can return a
  genuine negative int, `~register_value` (MVNS, BICS' operand) is always negative, and a left
  shift (LSLS, ROR) can exceed 32 bits outright. Every one of those write sites now masks
  explicitly with `& 0xFFFFFFFF` at the point of assignment instead of relying on the wrapper -
  audited one by one against real ARM semantics rather than masking indiscriminately everywhere
  (though a handful of sites that are simplest to reason about that way, e.g. the `sp`/`lr`/`pc`
  property setters, do mask unconditionally to match the old wrapper's behavior exactly). Two
  sites that looked masked already turned out not to be and only surfaced as real test failures
  once the wrapper's safety net was gone (`test_should_execute_an_cmn_r5_r2_instruction`,
  `test_should_execute_a_subs_r1_1_instruction_with_overflow` - both traced to the test driver's
  `set_registers()` writing raw negative Python ints like `-2` to probe wraparound behavior, which
  the wrapper used to silently fix up) - a reminder that "obviously safe" needs verifying by
  running the full suite, not just reasoning about the production call sites in isolation.

  Verified via the full instruction test suite (with the two fixed test-driver sites above) plus
  real MicroPython + littlefs boots on 1.21 and 1.28 before/after, confirming byte-identical
  instruction-count traces (64,679,598 either way for 1.28's run in the table above - purely a
  dispatch-speed change, not a behavior change). Measured effect: ~13% faster under both CPython
  3.10 and 3.14+JIT on the real 1.28 boot-and-run workload (224.79s -> 195.19s, 130.23s -> 113.13s)
  and ~16% higher synthetic instructions/sec under CPython 3.10 (the synthetic benchmark is
  ADD/SUB-heavy, i.e. almost entirely register reads/writes, so it's more sensitive to this
  specific change than a real boot's mixed workload). PyPy's synthetic/real numbers were
  essentially unchanged (within run-to-run noise) - its JIT already optimizes the old
  `Uint32Array` indirection away at this level, same pattern as the struct-based bit-ops change
  above.
- **`RP2040.bootrom` got the same `list[int]` treatment as `registers` above, and `Uint32Array`
  was deleted from `utils/bit.py` entirely** once that left it with zero remaining callers. Much
  smaller surface than `registers` (`bootrom` is read heavily whenever execution is actually
  inside bootrom - real ROM routines like its own memcpy helper get called repeatedly during
  flash/USB operations - but read/written from just two call sites in `RP2040.read_uint32()`/
  `write_uint32()`, plus one bulk-load site in `load_bootrom()`, versus `registers`' ~60): the
  bulk-load site (`self.bootrom.set(bootrom_data)`) became an explicit masked slice-assignment
  (`self.bootrom[: len(bootrom_data)] = (v & 0xFFFFFFFF for v in bootrom_data)` - `load_bootrom()`
  is called with fewer than the full 4096 words in `tests/test_rp2040.py`, so a full-length
  `self.bootrom[:] = ...` would've been wrong), and the single `write_uint32()` write site got the
  same explicit `& 0xFFFFFFFF`. Verified the same way: full instruction suite, plus real 1.21/1.28
  boots confirming identical instruction counts before/after. Measured effect on top of the
  `registers` change above, under CPython 3.10: another ~3% off the real 1.28 boot-and-run
  (195.19s -> 188.98s); negligible for CPython 3.14+JIT and PyPy (both already fast enough here
  that bootrom's smaller share of total instructions doesn't move the needle much, unlike
  `registers` which every single instruction touches).
- **`RP2040.write_uint32()` checked `find_peripheral()` (a dict lookup) unconditionally, before any
  of the cheap RAM/flash/bootrom range comparisons.** Found while profiling (`cProfile` on a real
  MicroPython 1.21 boot-to-first-print run) why a large share of executed instructions - ~23-28% in
  both 1.21 and 1.28, see the 1.21-vs-1.28 discussion above - go through USB-interrupt-adjacent
  code: `find_peripheral()` showed up called almost 1:1 with every `write_uint32()` call
  (159,443 vs. 158,989 in that trace), which only makes sense if it's being called unconditionally
  rather than as a fallback. It was: unlike `read_uint32()`/`write_uint8()`/`write_uint16()`, which
  all check RAM (and flash/bootrom) via plain integer comparisons *before* falling back to the
  dict-based peripheral lookup, `write_uint32()` did the dict lookup first - so every 32-bit RAM
  write (stack spills, GC, locals - the overwhelmingly common case for real firmware) paid for a
  `dict.get()` that was always going to miss. Reordered to match the other three methods' range
  order. This is a general throughput fix, not specific to the 1.21-vs-1.28 gap or to USB - it
  just happened to surface while investigating that. Verified via the full test suite (436/436) and
  a clean A/B (`rp2040py bench`) on a real MicroPython 1.21 boot-to-first-print run, three runs
  each side: ~273K-313K instructions/sec before (noisy) vs. ~307K-312K after (tighter), roughly a
  7-8% improvement on top of everything above.
- **`RP2040.bootrom_byte_size`'s caching pattern extended to `sram`/`usb_dpram`/`flash`
  (`ram_byte_size`/`dpram_byte_size`/`flash_byte_size`)** - `read_uint16()`/`read_uint8()`/
  `write_uint8()`/`write_uint16()` were still calling `len(self.sram)`/`len(self.flash)` on
  essentially every RAM/flash bus access. Honest result, unlike the fix above: no measurable
  difference on a clean A/B (3 runs each side, same 1.21 benchmark) -
  `bytearray.__len__()` is already O(1) in CPython (a struct field read), so there wasn't
  much to save here, unlike `find_peripheral()`'s real `dict.get()`. Kept for consistency with
  the established pattern and because it cannot be a regression, not because it's a proven win.
- **HLE (high-level emulation) hook for bootrom's `__memcpy`/`__memcpy_44`, opt-in via
  `RP2040PY_ENABLE_HLE_MEMCPY=1` (off by default).** Found while tracing the 1.21-vs-1.28
  instruction-count gap (see above) that real firmware's own `memcpy()` calls - TinyUSB's
  `tu_fifo_read`/`tu_fifo_write`, littlefs's `lfs2_bd_read()` - route through these two bootrom
  entry points, and that interpreting their per-byte/per-word copy loop one emulated Thumb
  instruction at a time is pure overhead once the copy itself can be done as a single Python-level
  bulk operation. `CortexM0Core.execute_instruction()` checks `core.pc` against
  `RP2040.hle_memcpy_entries` (a `frozenset[int]`) before the normal fetch/decode/dispatch path; on
  a hit, `_hle_memcpy()` performs the copy via `RP2040.bus_copy()` (a `bytearray` slice copy when
  both ends land in RAM/flash - the common case - falling back to a plain byte-by-byte bus copy for
  anything else, e.g. touching peripheral space) and jumps directly to the return address, still
  advancing the clock by a rough (not cycle-accurate) cost estimate so SOF-cadence/timing-sensitive
  code elsewhere isn't disturbed by treating the copy as free.

  Detecting *where* these two routines live is a whole-bootrom byte-pattern scan (`bytes.find()`
  over the 16KB bootrom, once per `load_bootrom()` call - nowhere near the hot per-instruction
  path), not a hardcoded address: downloaded `b0.elf`/`b2.elf` via `--bootrom` and confirmed the
  routines' own machine code is byte-for-byte identical across B0/B1/B2 (only their *position*
  differs - B0: `0x2888`/`0x28a0`, B1: `0x2628`/`0x2640`, B2: `0x2604`/`0x261c` - presumably other
  ROM code shifting around them, not the routines themselves changing), so a scan finds the right
  offset for any of the three (or any future revision carrying the same unchanged routine)
  automatically, rather than needing a manually-maintained per-revision offset table. A revision
  where the signature genuinely isn't found anywhere leaves the hook inert for that image rather
  than risking a misfire. Verified booting real MicroPython 1.21 + littlefs against all three
  bootrom revisions with the hook enabled - identical, correct output (`Hello, MicroPython!
  version: 1.21.0`) on each.

  **Measured net negative on both benchmarks - off by default, not recommended to enable.** No
  measurable improvement on the fast 1.21 boot-to-first-print benchmark (memcpy is only ~0.2% of
  its instructions there). The real test - the full MicroPython 1.28 boot-and-run, where `memcpy`
  traffic is ~18x higher - is now measured too: 213.86s baseline vs. 217.82s with the hook enabled,
  **~1.8% *slower***, not faster (instructions/sec is misleading here and shouldn't be used - the
  hook collapses many interpreted instructions into one counted step, so the two runs' step counts
  aren't the same unit; wall-clock time is the only fair comparison). The per-instruction
  `core.pc in self.rp2040.hle_memcpy_entries` check is paid by every single instruction in the run,
  and even 18x more memcpy traffic isn't a large enough share of total execution to outweigh that
  fixed tax. The mechanism itself is correct (verified against real boots on all three bootrom
  revisions) - this is a clean "measured, doesn't pay off" result, not a bug. See
  `docs/BACKLOG.md` for the full numbers and rationale.
- **Ahead-of-time Cython compilation of `CortexM0Core`/`RP2040`'s hot paths - unlike every
  runtime-check-based idea above (dispatch table aside), this one is a genuine ~4x win, on by
  default.** All the items above tried to make the *interpreter loop* itself cheaper or skip parts
  of it conditionally; this instead compiles the whole thing to C ahead of time, so there's no
  per-instruction "should I take the fast path" check to weigh against the savings - the exact
  problem that made the HLE hook and three separate JIT attempts (`docs/JIT_BACKLOG.md`) all net
  negative. Ships as an optional-but-automatic `rp2040py.native` extension: every one of
  `CortexM0Core`'s ~90 instruction handlers is a genuinely C-typed function (not just typed class
  fields - an earlier, narrower attempt at exactly that shipped first and measured only ~2-9%
  real-world despite an ~11.5x isolated estimate, then got replaced by this full port once the gap
  was root-caused to untyped method *bodies* re-boxing every value at the call boundary), dispatched
  through a real C function-pointer table, plus `RP2040`'s `read`/`write_uint8/16/32` bus paths.
  Falls back to the identical pure-Python implementation automatically if no C compiler is
  available at install time, or at runtime via `RP2040PY_SKIP_CYTHON=1`. Measured **~3.9x** on the
  synthetic instructions/sec benchmark and **~4.1x** on a real MicroPython 1.21 boot - see
  `docs/BACKLOG.md`'s "Cython port of the interpreter core" section for the full writeup (the
  root-cause analysis of why the first attempt underperformed, the abi3/stable-ABI build, the PyPy
  regression this found and fixed, and the two real correctness bugs the build-then-test loop
  caught along the way).


## Update 2026-10-05: the tables above no longer describe the project

Everything above was measured with the pure-Python/Cython-per-instruction core, stepping one
instruction at a time from Python. Since then record [0096](0096-cpp-mcu-core.md) moved the core,
the bus and the peripherals into C++ behind a batch engine, and `bench` was fixed to measure that
engine. Re-measured with a littlefs image preloaded (`rp2040py mklittlefs -o lfs.img
tests/micropython/main.py --main main.py`, then `rp2040py bench --image <uf2> --littlefs lfs.img
--expect-text "Hello, MicroPython!"`), CPython 3.10, wall time to the first line of the resident
script:

| Firmware | Pure Python (`RP2040PY_SKIP_CYTHON=1`) | Native (default) |
| --- | --- | --- |
| MicroPython 1.28 | 1.89 s | 0.02 s (~95x) |
| MicroPython 1.21 | 3.36 s | 0.03 s (~110x) |

PyPy 3.10 (7.3.19, pure Python, the native build is skipped there): 1.31 s on 1.28, 1.77 s on 1.21.

What this changes in the text above:

- **The "64.7M steps / 188.98 s for 1.28" and the "1.28 is 45x costlier than 1.21" explanation are
  withdrawn.** The step count included trips round the stepping loop with the core asleep (WFE, no
  timer armed). 1.28 needs ~1 million real instructions to its first line, and both versions now
  reach it in about the same wall time. 1.21 is no longer the recommended version.
- **The V8 comparison is out of date, and is now re-measured head to head.** rp2040js (branch
  `feat/littlefs-write-support`, Node 22.22, `npx tsx demo/micropython-run.ts --image <uf2> --littlefs lfs.img
  --expect-text "Hello, MicroPython!"`, same machine, same firmware and littlefs image, three runs each) versus
  `rp2040py bench` (native, same image), whole-process wall time including interpreter start-up
  (`node`+`tsx` alone ~0.72 s, `python -c "import rp2040py.cli"` ~0.14 s):

  | Firmware | rp2040js (total / minus start-up) | rp2040py native (total / minus start-up) |
  | --- | --- | --- |
  | MicroPython 1.28 | 3.5-4.2 s / ~2.8-3.5 s | 0.21-0.23 s / ~0.07-0.09 s |
  | MicroPython 1.21 | 1.01-1.06 s / ~0.3 s | 0.23-0.25 s / ~0.09-0.11 s |

  **Read these numbers as "time to the first line with each emulator's own idle handling", not as raw
  instruction throughput.** rp2040js's `Simulator.execute()` goes through every idle trip (a core in
  WFE/WFI with no alarm to wake it) inside its 1,000,000-iteration batch and then yields with
  `setTimeout(0)`; rp2040py's batch engine skips an idle core outright. That is the same effect that
  inflated the old "64.7M steps" for 1.28 (it spends most of its time idle by the first line), and it
  is presumably why 1.28 is ~3x slower than 1.21 in rp2040js while the two are level here. Both facts
  are real for someone running the emulator, but the 30-40x on 1.28 mostly measures that design
  difference, not a faster instruction loop; the ~3x on 1.21, which is less idle-dominated, is the
  closer indication of the instruction path. Also: one machine, Node 22 rather than v26, `tsx` rather
  than a compiled build, and the "first line" moment is not identical (rp2040py's simulated time at the
  first line differs between the native and pure builds - an open oddity, record 0096).
- **PyPy was re-measured** (above): ~1.3-1.8 s, i.e. only ~1.4-1.9x faster than CPython's pure build (it was ~16x
  on the old per-instruction engine, whose cost was interpretive overhead PyPy could remove) and ~60x slower than the
  native build. "Run under PyPy" is no longer the advice; the native build is the fast path.
- Preloading littlefs does not change these numbers measurably: the README figures (taken the same
  way) agree with the table here.
