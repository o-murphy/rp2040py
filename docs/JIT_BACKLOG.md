# JIT / basic-block fusion — implementation plan

Dedicated backlog for this one feature, split out of `docs/BACKLOG.md` because of its size (a
real, multi-session undertaking, not a follow-up fix) - see that file for this project's other
working notes. Status as of this writing: **Phases 0-1 done and integrated
(`RP2040PY_ENABLE_JIT=1`, default off); Phase 1's real, fully-integrated result was net negative
on the actual target workload, so Phases 2-3 are not being pursued as originally scoped - see
Phase 1's writeup below for why, and "what would actually be needed to reopen this" for what could
change that.**

## Goal

Confirm whether *basic-block fusion* - compiling a hot loop's instruction sequence into one
native Python function instead of interpreting it one emulated Thumb instruction at a time - is a
real, worthwhile performance lever for this emulator, and if so, build it as a genuinely optional,
decoupled component rather than something woven into the core interpreter.

## Motivation

The HLE hook for bootrom `__memcpy`/`__memcpy_44` (see `docs/BACKLOG.md`) confirmed that
per-instruction dispatch overhead (`read_uint16()` fetch, dispatch-table lookup, a full Python
method call per instruction, flag bookkeeping) is real and costly - but that hooking it at the
*per-instruction check* granularity (one `frozenset` membership test added to every single
instruction) doesn't pay for itself: the check's own fixed cost is paid by the ~99%+ of
instructions that never hit it, and it measured **net negative** (~1.8% slower) on a full
MicroPython 1.28 boot. The natural follow-up question: is the *underlying idea* (skip repeated
instruction-by-instruction interpretation for known-hot code) still worth pursuing at a coarser
granularity - fusing a whole hot loop into one compiled unit, rather than hooking one specific
known routine?

## Isolated test (already done, results validated the idea)

Built a standalone script (`ast_jit_test.py`, kept only in a scratchpad during investigation - not
part of the repo, and not run against it here) that compares, for the exact same semantic work:

- **"Real interpretation"**: point a real `RP2040`/`CortexM0Core` at the actual bootrom
  `__memcpy_slow_lp` loop (`0x2632` in `BOOTROM_B1` - confirmed hot in `docs/BACKLOG.md`'s
  1.21-vs-1.28 investigation: `subs r2,#1; ldrb r3,[r1,r2]; strb r3,[r0,r2]; bne`) and let
  `execute_instruction()` run it for real, byte by byte, exactly as production code does today.
- **"ast-compiled"**: a Python function built via `ast.parse()`/`compile()`/`exec()` (genuinely
  constructed as an AST and compiled, not a plain hand-written function, to test the actual
  technique) that performs the *same* semantic operation - a `while` loop calling the *same*
  `rp2040.read_uint8()`/`rp2040.write_uint8()` bus methods per byte - but as a single fused Python
  function instead of four separately-dispatched Thumb instructions per byte. Deliberately **not**
  a bulk `bytearray` slice copy (that would be re-testing the already-measured-negative HLE idea,
  not this one) - the only thing being removed is per-instruction interpretation overhead; the
  actual bus-level work stays identical and was verified byte-for-byte equal after every run.

**Results (50,000 bytes, 3 runs each, correctness-verified every run):**

- **CPython 3.10**: real interpretation ~480ms (~104K bytes/sec) vs. ast-compiled ~37ms (~1.35M
  bytes/sec) - **~13x faster**.
- **PyPy (7.3.15/3.9, installed via `apt-get install pypy3` - `uv python install pypy-3.10` isn't
  obtainable in this sandbox, `downloads.python.org` is 403'd same as noted in `docs/BACKLOG.md`,
  but PyPy 3.9 via apt was enough for this test; had to run against a throwaway copy of
  `src/rp2040py` with `from __future__ import annotations` prepended to every file, since PyPy
  3.9's runtime doesn't support PEP 604 `X | Y` annotation syntax the way 3.10+ does - the real
  package/repo was never touched)**: real interpretation ~6ms steady-state after JIT warm-up
  (~8.3M bytes/sec) vs. ast-compiled ~0.35ms steady-state (~145M bytes/sec) - **~17x faster at
  steady state** (~9.7x if averaged including the JIT-warmup-skewed first run of each).
- **Key finding: PyPy's own JIT does not eliminate this advantage - the relative gap is at least
  as large under PyPy as under CPython, if not larger at steady state.** PyPy's JIT optimizes each
  function it sees hot, but "real interpretation" still pays the *generic* dispatch structure
  (fetch, table lookup, method call, flag bookkeeping) once per Thumb instruction regardless of how
  fast each individual step gets - four such round-trips per byte here - while the fused version is
  one tight, trivially-JIT-specializable loop with two bus calls per byte. The technique's benefit
  is orthogonal to, not redundant with, PyPy - real additional headroom even for users already on
  PyPy, not just a CPython-only win.

## Architecture: decoupled, opt-in, minimal-touch

Explicit design goal (not yet built): the JIT should be a self-contained component the core
interpreter *calls into* through a couple of trivial, cheap integration points - not logic woven
directly into `cortex_m0_core.py`/`rp2040.py`. Two integration points are structurally unavoidable
(the feature needs *some* way to intercept execution and *some* way to hear about writes), but each
should cost close to nothing when the feature is off, with all real complexity living in its own
module(s):

- **Execution hook, `CortexM0Core.execute_instruction()` (`cortex_m0_core.py:1398`).** Revised
  after Phase 0's own measurement (see below) disproved the original plan of a plain
  `if self._jit is not None: ...` check inlined into the always-executed path: even that "cheap"
  check cost ~3.4% on every instruction, disabled or not. What's actually implemented instead:
  `execute_instruction()` stays exactly what it was before this feature existed (no check, no
  cost), and a second method, `_execute_instruction_jit()`, carries the same body plus the hook
  next to the existing HLE check (`cortex_m0_core.py:1404`) - `CortexM0Core.__init__` binds
  `self.execute_instruction = self._execute_instruction_jit` as an instance attribute *only* when
  `rp2040.jit is not None`, so the disabled path never even evaluates the check, and the enabled
  path pays for it once per instance construction, not once per instruction. `RP2040.jit` itself
  defaults to `None`, only becoming a real `JITEngine` instance when `RP2040PY_ENABLE_JIT=1` is set
  (mirroring the existing `RP2040PY_ENABLE_HLE_MEMCPY` flag's naming/shape).
- **Write-invalidation hook.** A single optional callback registered on `RP2040`, called from each
  write path (`write_uint8`, `write_uint16`, `write_uint32`, `bus_copy()` in `rp2040.py`) only if
  `self.jit is not None` - not the invalidation *logic* itself living in `rp2040.py`, just a
  call-out. Unlike the execution hook, this check was *not* moved out of the always-executed path:
  writes are far less frequent per instruction than fetch/dispatch (most instructions don't write
  at all), so the same measurement concern doesn't apply here - confirmed by the Phase 0 A/B below,
  which found no measurable cost from the write-path checks.
- **Everything else** - hot-loop detection, per-instruction codegen, the compiled-block cache, the
  invalidation logic behind that callback - lives entirely under a new `src/rp2040py/jit/` package,
  imported and instantiated only when `RP2040PY_ENABLE_JIT=1`. With the flag off, that package
  should ideally never even be imported (keeps startup cost and import-time surface at zero for the
  overwhelming majority of users who won't use this).

## Phased implementation plan

Each phase should be independently measured (clean A/B, same methodology as the HLE hook and
`write_uint32` fix in `docs/BACKLOG.md`) and independently justify moving to the next - stopping
after any phase if the numbers don't hold up is a legitimate outcome, not a failure.

**Phase 0 - scaffolding only, no real codegen. DONE.**
Added both integration points (execution hook, write-invalidation callback) wired up to a stub
`JITEngine` that never actually compiles anything (`try_execute()` always returns `None`). Goal:
prove the interface itself is genuinely non-invasive - full test suite (436/436) unchanged, and a
clean A/B confirming the disabled-by-default path costs nothing measurable, *and* that even the
enabled-but-stub path costs nothing measurable (isolating "interface overhead" from "codegen
correctness/benefit," which the later phases will need to reason about separately). No risk to
emulation correctness at this stage - nothing new is ever executed.

Results: the first attempt at the execution hook - a plain
`if self.rp2040.jit is not None: ...` check inlined into `execute_instruction()`, always
evaluated regardless of the flag - measured ~3.4% *slower* even disabled (17.43s baseline vs.
18.02s, 8 runs each, `RPI_PICO-20231005-v1.21.0.uf2` running `-c "x=0\nfor i in range(5000):
x+=i\nprint(x)"`): the doc text above called this risk out in advance ("this default-off path
needs to be at least that cheap, ideally cheaper" than the HLE hook's frozenset check), and it
wasn't cheap enough - one extra `LOAD_ATTR`/`COMPARE_OP`/`POP_JUMP_IF_FALSE` on the single hottest
loop in the emulator, executed on every instruction regardless of whether the flag is set, adds up
over tens of millions of instructions.

Fixed by moving the check out of the always-executed path entirely instead of trying to make it
cheaper: `CortexM0Core.execute_instruction()` stays byte-for-byte what it was before this phase,
and a separate `_execute_instruction_jit()` variant (same body, plus the JIT hook) is bound over
it as an instance attribute in `__init__()`, but *only* when `rp2040.jit is not None` - see
`cortex_m0_core.py`. The disabled (default) path is then not "a cheap check that's skipped," it's
"the exact same code that ran before this phase existed," so its cost is definitionally zero.
Re-measured after the fix: 17.59s (6 runs) vs. the same 17.43s baseline - within run-to-run noise
(individual runs in both groups ranged over ~0.6s), i.e. no measurable overhead. The enabled-but-
stub path measured ~19.24s (4 runs, +10.4% vs. baseline) - the real cost of the per-instruction
`try_execute()` call plus the `on_write()` calls on every RAM/flash write, paid only by whoever
opts in. Full test suite (436/436) green both with the flag unset and with it set to the stub.

Correctness, beyond the simple arithmetic-loop benchmark above: the loop only exercises
ADD/CMP/branch and RAM writes, so it doesn't say much about the write-invalidation hook on its
own - real firmware boots exercising flash/littlefs/DMA/peripheral writes are a better test of
that. Ran both `tests/micropython/main-flash-rw.py` (real internal-flash writes through littlefs,
via the SSI/DMA path) and `tests/micropython_spi_run.py`'s SPI test (SPI0 peripheral + DMA + USB
DPRAM traffic) against `RPI_PICO-20231005-v1.21.0.uf2`, flag unset and flag set to the stub -
identical pass/fail output and identical printed results in all four runs. Consistent with the
structural argument for why Phase 0 can't miscompute anything (`try_execute()` always returns
`None`, so no new code path is ever actually *executed*, only *called into and returned from*) -
the write-invalidation hook (`on_write()`) is the one new thing that runs on real writes in this
phase, and it's a no-op, so there's nothing for it to get wrong yet either.

**Phase 1 - one known, fixed loop (proof of concept). DONE - net negative, stopping here.**
Implemented codegen for exactly one pattern - `__memcpy_slow_lp`'s exact instruction sequence,
already validated in isolation above - fully integrated: real hot-loop detection (decodes the
actual instruction bits at candidate addresses rather than matching a hardcoded PC, so it finds
the pattern under any register assignment or bootrom location - `jit/engine.py`'s
`_decode_memcpy_slow_lp`), a real per-`load_bootrom()` cache (`JITEngine._blocks`, populated by
`JITEngine.load()`, mirroring `_find_hle_memcpy_entries`'s own lifecycle), real write-invalidation
(`JITEngine.on_write()` evicts any cached block whose own instruction bytes were overwritten), and
real cycle accounting (`_MemcpySlowLpBlock.run()` computes the exact same total cycle count the
interpreter would have accumulated across all four instructions × N iterations, not an estimate -
verified below).

Correctness: a new `tests/test_jit.py` asserts the compiled block produces *identical* registers,
flags, memory contents, and total cycle count to plain per-instruction interpretation of the exact
same instruction bytes (parametrized over two different register assignments, to confirm detection
isn't hardcoded to r0-r3) - this is a stronger, permanent check than the ad-hoc real-boot
comparisons Phase 0 relied on. Full test suite (444/444) green both with the flag unset and with
it enabled end-to-end against real MicroPython 1.21 boots: the plain boot-to-print test, the
flash-read/write test (littlefs through the SSI/DMA path), and the SPI+DMA+DPRAM test all produced
identical pass/fail output and identical printed results, flag off vs. on.

Wall-clock result on the actual target workload - a full MicroPython 1.28 boot (TinyUSB-heavy,
the same one the HLE hook's own negative result was measured against) - is a **net negative**,
same shape as the HLE hook: ~130s disabled vs. ~141s enabled (~8-9% slower, `cpython-3.14`,
consistent across repeated runs). Instrumenting `JITEngine.try_execute()` explains why: out of
**61,295,704** total instructions executed during that boot, the compiled block matched and fired
only **71,262** times (0.12%), copying 855,300 bytes total. `__memcpy_slow_lp` is bootrom
`__memcpy`'s *byte-at-a-time tail handler* - real memcpy calls route the bulk of their bytes
through a separate word-aligned fast path (`ldmia`/`stmia`, 4 words at a time - not a pattern this
phase targets at all) and only fall into this loop for the ≤3 leftover unaligned bytes at the
start/end of a copy. So the per-instruction cache-lookup cost (`try_execute()`'s `dict.get()`,
paid by all 61M instructions in the boot, not just the loop's own) can never be paid back by a
loop that only ever accounts for ~0.1% of total execution - the *same* fundamental problem the HLE
hook's negative result already demonstrated (hooking a per-instruction check at a granularity far
finer than the actual hot code doesn't pay for itself), just reached by a different, more
"legitimate-looking" mechanism (real per-block codegen, not a bulk-copy shortcut).

This is exactly the stopping condition the plan called out in advance ("if this phase doesn't show
a real win once fully integrated, that's a legitimate stopping point"). Phases 2-3 (below) assumed
Phase 1 would justify generalizing this same fusion technique to more instruction patterns; since
the technique's own integration overhead outweighs the benefit at this granularity, extending it to
more patterns would only add more code paying the same cost for the same reason, not fix the root
cause. Not pursuing Phases 2-3 as originally scoped - see "what would actually be needed" below.

**Follow-up attempt: branch-only detection (also DONE - also net negative).** Tried option (2)
above: moved the check off the per-instruction path entirely. `execute_instruction()` reverted to
being byte-for-byte what it was before Phase 1 (the `_execute_instruction_jit` method-swap from
the first attempt is gone); instead, `CortexM0Core.__init__` builds a per-instance copy of the
opcode dispatch table (`self._dispatch_table`, only when JIT is enabled) with the B(cond) opcode
range remapped to `_op_b_with_cond_jit` - the loop's own repeat instruction (`bne`) is the only
place that now checks the block cache, and only when the branch is actually taken. Every other
opcode's handler, and the disabled path entirely, are untouched. `_MemcpySlowLpBlock.run()` no
longer self-accounts cycles (it's now invoked as an ordinary dispatch-handler return value, so the
caller's existing `self.cycles += delta_cycles` already covers it - the first version double-risked
this until caught by `tests/test_jit.py`'s cycle-count equivalence check).

Result: check volume dropped **18.7x** (3,274,113 branch-taken checks vs. the original
61,295,704 per-instruction checks - same 71,262 hits, confirmed via the same instrumentation
approach). Despite that, wall-clock got **worse in relative terms**, not better - measured on real
PyPy 3.10.16/7.3.19 (obtained directly from the user after `downloads.python.org`/`pypy.org` proved
unreachable from this sandbox's network policy; confirmed to run this project's actual 3.10+ syntax
natively, no `from __future__ import annotations` shim needed unlike the earlier PyPy 3.9 test):
~13.8s disabled vs. ~16.5s enabled (~20% slower, 2 runs each side) on the same full 1.28 boot.

Why fewer checks made things *worse* in relative terms: PyPy's own JIT is very good at
specializing simple, uniform instruction-dispatch loops - `_op_b_with_cond` unmodified is exactly
that kind of hot, tight, pure-integer function, and PyPy already made the *disabled* baseline
dramatically faster than CPython (~13.8s vs. ~112-130s on CPython) by tracing and inlining it
aggressively. Splicing a cross-module method call and dict lookup into that same hot path - even
though it only fires on a fraction of calls - appears to disrupt how well PyPy can trace/inline the
*surrounding* branch-dispatch code for every conditional branch in the program, not just the
71,262 that matter. Fewer total checks didn't help because the problem was never purely
"check-count × cost-per-check" - *where* the check sits in the hot path matters independently of
how often it fires.

**What would actually be needed to reopen this.** Both structural fixes suggested after the first
attempt are now tried and both net negative. The one remaining lever, per the original
1.21-vs-1.28 investigation in `docs/BACKLOG.md`: target the *actual* bulk-copy loop
(`ldmia`/`stmia`-based, word-aligned), where the real instruction volume is - `__memcpy_slow_lp`
was always just the ≤3-byte tail handler, never the dominant path. Detecting and compiling a
multi-word unrolled loop is meaningfully harder than either of this phase's two attempts (more
instruction variety, register-list handling for `ldmia`/`stmia` rather than three fixed registers),
but it's the only remaining path to a check that fires often enough, on expensive-enough work, to
plausibly pay for itself.

**Phase 2 - a small, common subset of instruction patterns.**
Expand codegen to the patterns that show up most often in hot loops per already-gathered profiling
data (`docs/PORTING.md`'s perf log) - likely load/store variants, ADDS/SUBS, CMP, conditional
branches - not all ~90 patterns at once. Any block containing an unsupported pattern simply doesn't
get compiled (falls back to normal interpretation) - partial coverage is an acceptable, safe
intermediate state, not a blocker. Each added pattern goes through the equivalence test suite
(below) before being trusted.

**Phase 3 - full coverage and refinement.**
Remaining instruction patterns, interrupt-latency handling, clock-accounting refinement, and
whatever else Phase 1/2 turned up as open questions. Only worth reaching if Phases 1-2 clearly paid
off.

## Exact integration points for the eventual full implementation

**Not currently being built** - kept here for reference, since it's still the accurate shape of
what generalizing past Phase 1 would take *if* one of the "what would actually be needed to reopen
this" changes above first fixes the per-instruction dispatch cost that made Phase 1 net negative.
Written before Phase 1 ran; the actual Phase 1 code (`src/rp2040py/jit/engine.py`) ended up
simpler than this sketch (no separate `codegen.py`/`block_compiler.py`/`block_cache.py` modules -
one file was enough for one hardcoded pattern), but the concerns below (interrupt latency, clock
accounting, an equivalence test suite) are real and would still apply to any broader attempt.

- **Code generator** (new, e.g. `src/rp2040py/jit/codegen.py`) - the single largest piece. Each
  instruction pattern in `_DISPATCH_PATTERNS` (`cortex_m0_core.py:1462`, matched via the loop at
  `cortex_m0_core.py:1548`, plus the `_resolve_wide()` special case for the seven wide-encoding
  patterns noted in that table's own comment) needs a *second* implementation alongside its
  existing `_op_*` method: not "execute this instruction" but "emit AST/source that, when compiled,
  has the same effect." Real risk of the two forms drifting out of sync, which would be a
  silent-wrong-emulation bug, the worst kind to debug - this is exactly what the equivalence test
  suite below exists to catch.
- **Hot-loop detection + block compilation** (new, e.g. `src/rp2040py/jit/block_compiler.py`) - a
  per-PC visit counter to decide what's "hot" enough to compile, decoding a basic block's
  instruction sequence (from a loop head to its back-edge) via the same fetch logic
  `RP2040.read_uint16()`/`read_uint32()` (`rp2040.py`) already use, driving the codegen above, and
  `compile()`-ing the result.
- **Compiled-block cache with write-invalidation** (new, e.g. `src/rp2040py/jit/block_cache.py`) -
  keyed by PC; invalidates any cached block whose address range overlaps a write, via the
  write-invalidation callback above, so self-modifying code (real firmware does write to flash it
  may later execute from, via the SSI path - see `docs/BACKLOG.md`'s "SSI flash-write support"
  section) can't run stale compiled code.
- **`Simulator.execute()` (`simulator.py:17`)'s clock/cycle accounting** - currently ticks the
  clock once per single instruction; a compiled block executing many instructions per Python-level
  call needs this reworked, in the same spirit as (but a bigger version of) the idle-tick-accounting
  fix in `docs/BACKLOG.md`'s CDC investigation section (also a `Simulator.execute()` accounting
  bug).
- **Interrupt-latency interaction** - real hardware can take an interrupt between *any* two
  instructions; `CortexM0Core.check_for_interrupts()` (`cortex_m0_core.py:393`), called at the top
  of `execute_instruction()` before every single instruction today, is the relevant existing check.
  A fused block executing multiple instructions as one atomic Python call either needs to prove
  that doesn't matter for the specific blocks it fuses, or add calls to this same check at explicit
  points inside the generated code (which cuts into the benefit) - not investigated yet; likely a
  Phase 3 concern.
- **Exhaustive codegen-vs-interpretation equivalence test suite** (new, alongside the existing
  `tests/test_dispatch_table.py`, which only checks the dispatch table's own structure/coverage,
  not codegen output) - for every instruction pattern added, run both the real `_op_*` handler and
  the generated-and-compiled equivalent from identical starting register/flag state and assert
  identical resulting state. This is what makes it safe to add patterns incrementally across
  Phase 2 without correctness regressing as coverage grows.

## Relationship to the existing HLE memcpy hook

A general JIT, once it reaches `__memcpy_slow_lp`-shaped loops in Phase 1, would naturally
subsume what the HLE hook (`RP2040PY_ENABLE_HLE_MEMCPY`, see `docs/BACKLOG.md`) does today, likely
better (compiled-and-cached beats a per-instruction `frozenset` check). Not removing the HLE hook
now - it's an independent, already-shipped (if net-negative and off-by-default) piece - but worth
revisiting once Phase 1 lands, since the two may end up redundant.

## Scope estimate

Roughly comparable in scope to (arguably larger than) the dispatch-table work described in
`docs/PORTING.md`'s perf log (that project's own "single biggest lever" of its session) - doubled,
since every instruction pattern eventually needs both an interpreter form and a codegen form kept
in sync. A real, multi-session undertaking. The phased plan above exists specifically so it can be
picked up, measured, and stopped at any phase boundary without the whole thing needing to land at
once.
