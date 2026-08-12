# 0015. HLE hook for bootrom __memcpy / __memcpy_44

- Status: Rejected — implemented opt-in, measured net negative
- Conceived: 2026-08-05
- Related: 0013 (Cython), 0016 (JIT), note 0017 (perf)

<!-- migrated verbatim from docs/BACKLOG.md lines 513-617 -->

## HLE hook for bootrom `__memcpy`/`__memcpy_44` — implemented, opt-in, measured net negative

**Goal:** once the 1.21-vs-1.28 investigation above pinned down that real firmware's own `memcpy()`
calls (TinyUSB's `tu_fifo_read`/`tu_fifo_write`, littlefs's `lfs2_bd_read()`) route through two
fixed bootrom routines, and that they're interpreted one emulated Thumb instruction at a time like
everything else, the natural next question was whether HLE-ing (high-level-emulating) just those
two routines - replacing the interpreted copy loop with a native bulk copy - is worth doing as a
real, general (not 1.28-specific) throughput win.

**Design, implemented in `rp2040.py`/`cortex_m0_core.py`:**
- `CortexM0Core.execute_instruction()` checks `core.pc` against `RP2040.hle_memcpy_entries` (a
  `frozenset[int]`, computed once per `load_bootrom()` call, not per instruction) before the normal
  fetch/decode/dispatch path. On a hit, `_hle_memcpy()` runs instead: reads `r0`/`r1`/`r2` (AAPCS
  `dst`/`src`/`n`), calls `RP2040.bus_copy(dst, src, n)`, sets `pc = lr` (masking the Thumb bit),
  and returns a rough (not cycle-accurate) `delta_cycles` estimate - aligned copies cost `~n//8`,
  unaligned ones `~n*4` (matching `_memcpy_aligned`'s `ldmia`/`stmia` throughput vs.
  `__memcpy_slow_lp`'s exact 4-instructions-per-byte, both confirmed against the real
  `bootrom_misc.S` source) - so simulated-time-driven behavior elsewhere (SOF cadence, etc.) isn't
  disturbed by treating the copy as instantaneous. `r0` is left holding the unchanged `dst`
  (matching the real routines' own `mov r0, ip` tail); `r1`-`r3`/`ip` are left untouched, which is a
  stricter subset of "undefined after call" (real `memcpy()` is free to clobber them) so this can't
  violate the ABI; `r4`+ are never touched at all, matching the real routines' own push/pop of
  whichever they use.
- `RP2040.bus_copy(dst, src, n)`: a `bytearray` slice copy (`dst_buf[o:o+n] = src_buf[o:o+n]`) when
  both ends land in RAM or flash (the common case) - correct even when overlapping, since slicing
  the right-hand side first materializes an independent copy before assignment, giving memmove
  semantics regardless of copy direction - falling back to a plain byte-by-byte bus copy (still
  correct, just not the fast path) for anything else, e.g. touching peripheral space, which no real
  firmware routes a `memcpy()` through but isn't assumed impossible here.
- **Detecting where these two routines actually live: a whole-bootrom signature scan, not a
  hardcoded address** (`_find_hle_memcpy_entries()`). Downloaded `b0.elf`/`b2.elf` via `--bootrom`
  (this project already supports B0/B1/B2 - see the "Bootrom B0/B2 support" section below) and
  found the routines' own machine code is byte-for-byte identical across all three revisions - only
  their *position* in ROM differs (B0: `0x2888`/`0x28a0`, B1: `0x2628`/`0x2640`, B2: `0x2604`/
  `0x261c`), presumably because other ROM code shifted around them between revisions, not because
  the routines themselves changed. So `_find_hle_memcpy_entries()` takes the 20-byte signature
  bytes from `BOOTROM_B1`'s own known offsets (source of the signature only) and does a plain
  `bytes.find()` over the *whole* loaded bootrom (16KB, once per `load_bootrom()` call - nowhere
  near the hot per-instruction path) to locate wherever it actually is in the bootrom that's
  currently loaded. This finds the right offset for B0/B1/B2 automatically, and for any future
  revision that happens to carry the same unchanged routine, without a manually-maintained
  per-revision address table - and a revision where the signature genuinely isn't found anywhere
  (the routine's bytes really did change) leaves the hook safely inert for that image instead of
  risking a misfire against code it wasn't verified against.
- **Opt-in, not opt-out:** `RP2040PY_ENABLE_HLE_MEMCPY=1` is required to activate the hook at all;
  `hle_memcpy_entries` stays empty otherwise, checked once in `load_bootrom()`.

**Verified:**
- Full test suite (436/436), `ruff`/`mypy` clean.
- Real MicroPython 1.21 + littlefs boot produces byte-identical output (`Hello, MicroPython!
  version: 1.21.0`) with the hook enabled, across all three bootrom revisions (B0/B1/B2) - each one
  correctly finding its own routines at their own (different) offsets.

**Performance result - measured, both benchmarks, both negative or neutral. Confirms staying
opt-in (default off) was the right call, not just a cautious placeholder:**
- Clean A/B (`rp2040py bench`, 3 runs each side) on the fast MicroPython 1.21 boot-to-first-print
  benchmark: **no measurable improvement** (before: ~334K/331K/332K instructions/sec; after:
  ~324K/337K/320K - both ranges overlap, within run-to-run noise). Makes sense: in that same
  benchmark, `__memcpy` is called only 3,948 times out of ~1.42M total instructions (~0.2%) - not
  enough traffic through the hook to outweigh the small added cost of checking
  `core.pc in self.rp2040.hle_memcpy_entries` on literally every one of the other 99.8% of
  instructions, which never hit it.
- **The real test - a full MicroPython 1.28 boot-and-run A/B, where `memcpy` traffic is ~18x
  higher (72,208 calls) - is now measured, and it's a net regression, not a win.** Baseline (hook
  off): 65,000,000 instructions in 213.86s. With the hook enabled: 217.82s - **~1.8% *slower***,
  not faster. Instructions/sec is actively misleading for this specific comparison and shouldn't be
  used to read these two runs against each other: with the hook enabled, one `__memcpy`/
  `__memcpy_44` call collapses what would have been dozens of interpreted Thumb instructions into a
  single step in the outer instruction-counting loop, so the *step count itself differs between the
  two runs* (65,000,000 without the hook vs. 63,000,000 with it, to reach the same
  `--expect-text` match) - comparing "instructions per second" across runs whose "instruction" no
  longer means the same unit of work compares apples to oranges. Wall-clock time (213.86s vs.
  217.82s) is the only fair comparison, and it's unambiguous: slower with the hook on.
- **Why it nets negative despite real memcpy traffic:** the per-instruction
  `core.pc in self.rp2040.hle_memcpy_entries` check (an attribute lookup plus a `frozenset`
  membership test) is paid by *every single one* of the ~63-65 million instructions in this run,
  while only a modest fraction of them are actually memcpy-loop instructions the hook can skip -
  even with 18x more memcpy traffic than 1.21, that fraction isn't large enough to outweigh a fixed
  tax multiplied across effectively the entire instruction stream. The technique's payoff scales
  with how much of *total* execution time the hooked routine accounts for; here, TinyUSB's
  `tu_fifo_read`/`write` calling into `memcpy` is a real, measured contributor to 1.28's slowdown
  (see the investigation above), but evidently not a large enough share of *this specific
  benchmark's total instruction count* for a per-instruction-checked hook to pay for itself.

**Conclusion: not adopted as a default, and not recommended to enable as-is.** The mechanism itself
works correctly (verified against real MicroPython boots across all three bootrom revisions), so
this isn't a correctness failure - it's a genuine "measured this specific optimization technique,
found it doesn't pay off for this workload" result, which is exactly what the opt-in flag
(`RP2040PY_ENABLE_HLE_MEMCPY`) was designed to make safe to discover without shipping a regression
to anyone who doesn't explicitly ask for it.

**Not started yet (only worth pursuing if someone wants to revisit this technique, not blocking
anything else):**
- Reducing the per-instruction check's own overhead - e.g. an early-exit when
  `hle_memcpy_entries` is empty (not applicable to the measurement above, where it wasn't empty,
  but relevant to make sure the *disabled* default case has effectively zero added cost), or
  moving the check to a less-hot location. Given the *enabled* case is already a net loss even with
  real traffic through the hook, shaving the check's own cost further is unlikely to flip the
  overall verdict on its own - the fundamental issue is that a per-instruction Python-level check
  is expensive relative to the work it's trying to save.
- A coarser-grained version of the same idea - e.g. checking only at basic-block boundaries, or
  only after a cheap pre-filter (like a bloom filter or address-range check) - might change this
  calculus, but hasn't been explored; not worth it without evidence the underlying idea is worth
  saving, given the current measurement.

