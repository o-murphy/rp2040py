# 0013. Cython port of the interpreter core

- Status: Implemented — on by default, ~4x real-world win confirmed
- Conceived: 2026-08-05 · #20
- Related: #20 · follow-up 0031 (PIO Cython + tick batching) · note 0017 (perf)
- Note: the "PIO Cython port + opt-in clock.tick() batching" follow-up was extracted to record 0031.

<!-- migrated verbatim from docs/BACKLOG.md lines 626-964 -->

## Cython port of the interpreter core — implemented, on by default, real-world win confirmed (~4x)

**Status: implemented and merged into the real codebase.** Follows the JIT investigation above:
after three separate JIT attempts all measured net negative (see `docs/JIT_BACKLOG.md`) because
*any* runtime check added to the interpreter's hot path costs more than it saves unless the
accelerated code is a huge share of total execution, the natural next question was whether an
**ahead-of-time, whole-module** approach sidesteps that problem entirely - no runtime "is this
hot" check needed if the *entire* dispatch loop is compiled, not a hand-picked slice of it. It
does, and this time the real-world result actually matches the isolated ceiling estimate - see
"Measured results" below. (An earlier, narrower pass - typing only `cdef class` fields, not method
bodies - shipped first and measured only ~2-9% real-world despite an ~11.5x isolated estimate; see
"Why the first attempt underperformed" below for what that gap actually was and how it was closed.)

**Why this is structurally different from the JIT attempts:** Cython compiles a whole module to C
at build time. There's no per-instruction or per-branch "should I take the fast path" check -
every instruction benefits, not just ones matching a specific pre-selected pattern. Cython-compiled
code still runs *inside* CPython, so every existing C-extension dependency keeps working exactly
as today.

**Architecture: a subpackage of the main package, not a separate pip package.** `rp2040py.native`
(`src/rp2040py/native/`) ships inside `rp2040py` itself, compiled by `setup.py`'s
`Extension`/`cythonize()` (plain `setuptools` + `setuptools-scm`, `build-backend =
"setuptools.build_meta"`). A separate `rp2040py-native` distribution (its own `pyproject.toml`, a
`uv` workspace member, a runtime `importlib.metadata` version-matching guard between the two
packages) was tried first, mirroring `py_ballisticcalc`/`py_ballisticcalc.exts`'s split - and
abandoned same-session in favor of the current in-tree subpackage, since the extra moving parts
(two independently-versioned packages that must always match) bought nothing a `try`/`except
ImportError` inside one package doesn't already give. Every public entry point
(`rp2040py.rp2040.RP2040`, `rp2040py.cortex_m0_core.CortexM0Core`, `rp2040py.utils.bit.*`) is a
thin facade: `try: from rp2040py.native._X import ... except ImportError: from rp2040py._X import
...`, with the real pure-Python reference implementation living in the underscore-prefixed private
module (`_rp2040.py`, `_cortex_m0_core.py`, `_bit.py`) - callers never import the private module
or `rp2040py.native` directly.

The build itself first went through a custom hatchling build hook (`hatch_build.py`, not the
third-party `hatch-cython` plugin, whose default `files.targets` glob matching and
`--inplace`/`--build-lib` handling didn't produce a working wheel in this sandbox - compiled `.so`
files were built but never landed in the wheel), then migrated to plain `setuptools` +
`setuptools-scm` (`setup.py`) same-session, matching `py_ballisticcalc.exts/setup.py`'s own proven
pattern more directly - `optional=True` on each `Extension` is setuptools' native mechanism for
"skip this extension gracefully if it fails to build," replacing the hand-rolled
subprocess-and-catch-the-failure logic the hatchling hook needed. `hatch_build.py`'s
soft-fail/PyPy-skip/abi3 logic below carried over to `setup.py` unchanged in substance, just in
setuptools' idiom instead of hatchling's.

**Build failure modes are all soft, on purpose - this package still has to install everywhere:**

- No C compiler available at build time -> each `Extension`'s `optional=True` makes `build_ext`
  skip it (with a warning) instead of failing the whole build; the facades' `except ImportError`
  transparently uses the pure-Python implementation. (Cython/setuptools/setuptools-scm are still
  hard `[build-system] requires` - they're pure-Python-installable everywhere; it's specifically a
  missing *C compiler* this degrades gracefully for. Cython unavailable at all - e.g. `pip
  install --no-build-isolation` without it present - is handled the same way, via a plain
  `try: from Cython.Build import cythonize except ImportError: return []` in `setup.py`.)
- `RP2040PY_SKIP_NATIVE_BUILD=1` - forces a pure-Python wheel outright at *build* time, regardless
  of whether Cython/a compiler are actually available (e.g. for a deliberately "pure" release
  artifact).
- `RP2040PY_SKIP_CYTHON=1` - a separate, *runtime* gate (checked in each of the three facades, not
  just once in `rp2040py.native/__init__.py` - see "A gate that didn't gate anything" below) that
  forces the pure-Python fallback even when the compiled extension **is** installed. Used by
  pre-commit's `uv-pytest-pure` hook to validate the reference implementation on every commit
  without needing a rebuild, and useful generally for isolating whether a bug is native-specific.
- PyPy - compilation is skipped outright (`sys.implementation.name != "cpython"`), not attempted
  and silently discarded like the other cases. See "PyPy: compiling for it was actively harmful"
  below for why this needed to be a proactive skip, not just a fallback.

**What was actually ported, fully (not just fields this time):**

1. **`src/rp2040py/native/_cortex_m0_core.pyx`** - every one of the ~90 `_op_*` instruction
   handlers as a **module-level `cdef` function** (not a bound method) taking the core instance as
   an explicit first parameter, dispatched through a genuine **C function-pointer table**
   (`DISPATCH_TABLE`, a `ctypedef int (*OpHandler)(CortexM0Core, unsigned int, unsigned int,
   unsigned int) except -1` array of 0x10000 entries, built the same way as the pure-Python
   `_DISPATCH_TABLE`/`_DISPATCH_PATTERNS`/`_resolve_wide()`, including the same wide-opcode-range
   assertion) - not a Python-level list of bound methods. `registers`/`interrupt_priorities` are
   `unsigned int[:]` memoryviews backed by `array.array`, allocated in `__cinit__` (guaranteed to
   run exactly once at allocation, unlike `__init__`, which a subclass could in principle skip).
   `core.rp2040` is typed as the concrete native `RP2040` class (not `object`), via a `.pxd`
   cimport - a hot-path call like `core.rp2040.read_uint32(addr)` (present in nearly every
   load/store instruction) resolves to a direct C-level `cpdef` call instead of a Python attribute
   lookup + method call.
2. **`src/rp2040py/native/_rp2040.pyx`** - the bus hot paths (`read_uint8/16/32`,
   `write_uint8/16/32`). `sram`/`flash`/`usb_dpram`/`bootrom` are typed memoryviews - real, live
   views into the same underlying `bytearray`/`array.array` buffers, not copies, so external code
   that slices/mutates `rp2040.flash[...]` (peripherals, `device/load_flash.py`, tests) keeps
   working unchanged. RAM/flash(base region)/DPRAM/bootrom access branches directly on these
   memoryviews at C speed; SIO/PPB/the `peripherals` dict fall back to ordinary Python calls
   (`self.sio.read_uint32(...)` etc.) since those ~30 peripheral objects (UART, I2C, DMA, PIO,
   GPIO, the clock...) are still plain Python and get no benefit from being typed - only their
   *construction*, in `RP2040.__init__`, is transcribed here (verbatim, to avoid drift from
   `_rp2040.py`), not their internals.

**Why the first attempt underperformed (the real root cause, not just "types didn't help"):**
typing only `cdef class` *fields* makes attribute *access* fast (direct C struct offset), but
every one of the ~90 `_op_*` *method bodies* stayed plain, untyped Python - so a value read via a
fast typed field access was immediately re-boxed into a `PyObject` the moment it crossed into an
untyped method call, and re-unboxed on the way back. Confirmed by literally reading the generated
C (`annotate=True`'s HTML report, color-coded by Python-C-API-call density) rather than guessing:
`__Pyx_PyLong_From_unsigned_int(...)` immediately following a fast pointer-arithmetic field read,
because the *caller* of that read was an untyped method. A follow-up isolated test (12 real
instruction handlers, genuine C function-pointer dispatch - not the field-only pattern) reproduced
~10.9x on a realistic mix, matching the very first isolated estimate almost exactly - confirming
the original ~11.5x thesis was sound, just under-executed the first time. This full port applies
that lesson everywhere: every parameter and local on the per-instruction path is genuinely C-typed,
not just the fields.

**Stable ABI (abi3):** built against `Py_LIMITED_API` for CPython 3.10+ (`setup.py`'s
`_use_abi3()`, plus a `bdist_wheel` `cmdclass` override setting `self.py_limited_api = "cp311"` -
`py_limited_api=True` on the `Extension` controls what the *compiler* builds against, the
`bdist_wheel` option controls what the *wheel filename* gets tagged, and both are needed), producing
one `cp310-abi3` wheel that covers every 3.10+ interpreter instead of one per minor version -
verified directly: built once against 3.10, the identical `.abi3.so` loads and passes the full
437-test suite on 3.12 with zero recompilation. 3.10 specifically (not 3.10) because
`Py_LIMITED_API`'s buffer-protocol support - needed by this code's heavy use of typed memoryviews -
only entered the limited API at 3.10; below that floor, or on free-threaded builds (where
`Py_LIMITED_API` and `Py_GIL_DISABLED` are mutually incompatible per PEP 703 - `setup.py` checks
`sysconfig.get_config_var("Py_GIL_DISABLED")`), `setup.py` falls back to a normal, version-specific
extension instead. `[tool.cibuildwheel]` builds `cp310-abi3` and `cp3XXt`
separately for exactly this reason; a real local `cibuildwheel` run confirmed `auditwheel repair`
correctly relabels the output to `cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64`. One real
compile-time incompatibility found and fixed: `from cpython cimport array` (added for fast
`array.array` construction) reaches into CPython-internal `arrayobject`/`PyTypeObject` struct
layout that doesn't exist under the limited API (GCC: "invalid use of incomplete typedef
`PyTypeObject`") - removed in favor of a plain `import array`, since the only thing actually used
was the ordinary `array.array(...)` constructor call, which needs no special declaration either way.

**PyPy: compiling for it was actively harmful, not just unhelpful.** Before the proactive
`sys.implementation.name != "cpython"` skip existed, `hatch_build.py` happily compiled the Cython
extensions for PyPy too (`_rp2040.pypy310-pp73-x86_64-linux-gnu.so` etc.) - and they *worked*, in
the sense of importing and running correctly. The problem: every hot-path call then went through
PyPy's `cpyext` C-API compatibility shim instead of PyPy's own JIT, and `cpyext` is well known to
be dramatically slower than PyPy's native path for exactly this kind of call-heavy code - so
"accelerating" the interpreter core on PyPy made it *slower* than the plain pure-Python fallback,
which PyPy's JIT would otherwise have handled well on its own. Found via a real CI symptom (the
`Micropython 1.28.0 / pypy-3.10` job in `.github/workflows/ci-micropython.yml` running against its
10-minute per-step timeout) and confirmed by reproducing the old `hatch_build.py` in a git worktree
against `pypy3.10` directly - it silently built a working-but-slow native extension. The fix
(skip compilation outright on non-CPython interpreters) means PyPy always gets the pure-Python
implementation, where its JIT can do what it's actually good at.

**A gate that didn't gate anything (found by testing the actual behavior, not the code):** the
first version of `RP2040PY_SKIP_CYTHON`'s check lived only in `rp2040py.native/__init__.py`'s own
`try`/`except ImportError`. It didn't work - `RP2040`/`CortexM0Core` kept resolving to the native
backend regardless. Root cause: none of the three facades import `from rp2040py.native import X`
(the aggregated namespace `__init__.py` controls); each imports directly from a specific submodule
(`from rp2040py.native._rp2040 import RP2040`). Python always runs a package's `__init__.py`
before importing one of its submodules, but that execution completing (even via an internally
*caught* exception) doesn't prevent a separate, independent import of the submodule itself -
Python's import system doesn't gate submodule imports on what a parent's `__init__.py` did with
its own local names. Fixed by moving the actual check (a tiny shared `rp2040py._native_gate`
module) into each of the three facades directly, at the point where they decide which
implementation to import - verified by actually asserting `RP2040.__module__` under the flag, not
just eyeballing the code.

**Correctness verification:** full test suite (437/437) passes with the compiled extension active,
with `RP2040PY_SKIP_CYTHON=1` forcing the pure-Python path, and with the extension never built at
all - each checked against the *actual installed wheel* (not just in-place `.so` files) in clean
venvs on CPython 3.10, 3.10 (abi3), 3.12 (loading the 3.10-built `.abi3.so` unmodified), and 3.14
free-threaded (falls back to a normal per-version build, confirmed via its `cp314-cp314t` wheel
tag). Two real correctness bugs were caught this way, not by reasoning about the port in the
abstract:

- **`write_uint32`'s sign-preservation bug.** The pure-Python `RP2040.write_uint32` deliberately
  passes the *raw, possibly-negative* Python `value` through to `self.sio.write_uint32(...)` /
  `self.ppb.write_uint32(...)` / `peripheral.write_uint32_atomic(...)` - only the
  bootrom/flash/sram/dpram branches mask it (`value & 0xFFFFFFFF`) before use. The first native
  version masked once, up front, for every branch uniformly. `s32()`/`u32()` are idempotent
  regardless of pre-masking, so this looked harmless - but `sio.py`'s hardware-divider emulation
  does a raw `self.div_dividend > 0` comparison (not through `s32()`) to detect the "divide by
  zero, negative dividend" sentinel case, which silently broke once `div_dividend` was always
  stored pre-masked-to-unsigned (always positive). Caught by
  `test_sio.py::TestHardwareDivider::test_signed_division_by_zero_negative_3000_over_0`.
- **Cython typed-memoryview vs. `bytes` equality.** A `cdef public unsigned char[:] flash`
  field's auto-generated Python getter returns Cython's own typed-memoryview-slice object, which -
  unlike a real builtin `memoryview` - doesn't support content-based `==` against `bytes`/
  `bytearray` (falls back to identity comparison). Broke
  `test_kaluma_device.py`'s `written == b'console.log("hi");\x00'` even though the underlying bytes
  were byte-for-byte identical (`bytes(written) == expected` was `True`). Fixed by exposing
  `sram`/`flash`/`usb_dpram`/`bootrom` as `@property` methods wrapping the internal (now
  non-public, `_`-prefixed) typed memoryview field in a real builtin `memoryview(...)` - still a
  live view onto the same buffer, just one that supports the comparison semantics external callers
  already relied on.

**Measured results - this time matching the isolated ceiling, not falling far short of it:**

- *Synthetic* (`rp2040py bench --instructions 20000000 --block-size 1000`, an ADDS/SUBS mix):
  pure-Python 520,552 instr/sec vs. native 2,049,726 instr/sec - **~3.9x**.
- *Real firmware boot* (MicroPython 1.21.0, `rp2040py bench --image ... --expect-text ">>>"
  --timeout 30`; neither side reaches the REPL prompt within 30s, so this measures sustained
  instruction rate under real, representative boot-time workload rather than wall-clock-to-prompt):
  pure-Python 382,870 instr/sec (12,000,000 instructions in 31.34s) vs. native 1,581,870 instr/sec
  (48,000,000 instructions in 30.34s) - **~4.1x**.
- **~4x, consistent across a synthetic microbenchmark and a real firmware boot workload** - a
  genuine, substantial win, unlike the first attempt's ~2-9%. The isolated 12-instruction test's
  ~10.9x remains a true ceiling, not a promise: `core.rp2040` is typed but its own `read_uint32`
  etc. still cross a real (if now `cpdef`-fast) call boundary per memory access, and a full boot
  still spends real time in still-Python peripherals (DMA, PIO, USB, SPI) this port doesn't touch -
  but closing most of the original gap, rather than capturing only ~5-20% of it, validates that the
  "type the fields, not the methods" theory was the actual bug, not "Cython just doesn't help here."

### Follow-up: two more boxing sources found by reading the generated C, not by guessing

**Status: implemented and merged.** The ~4x above still left a large gap versus the isolated
~10.9x ceiling and versus PyPy (`docs/PORTING.md`'s synthetic instructions/sec table put PyPy at
~16x pure-Python). Both remaining gaps traced back to real Python-C-API traffic still hiding
*inside* code that looked fully C-typed on a first read - found by generating `cython -a`'s
annotated C (`cython -a _rp2040.pyx` / `_cortex_m0_core.pyx`) and grepping the output for
`PyNumber_And`/`__Pyx_PyLong_From_*`/`__Pyx_PyLong_As_*` rather than trusting the yellow/white
annotation coloring alone.

1. **`RP2040.read_uint32/16/8` and `write_uint32/16/8` took an untyped `address`/`value`
   parameter.** `cpdef unsigned int read_uint32(self, address)` with no type on `address` means
   Cython treats it as a plain `object` - so even though every real call site on the hot path
   (`execute_instruction`'s opcode fetch, every `op_*` load/store handler) passes an already-typed
   `unsigned int` C local or memoryview element, Cython has to box that value into a real `PyLong`
   *at the call site* before the call, and the `address & 0xFFFFFFFF` masking on entry then runs
   as Python bigint arithmetic on the boxed value instead of a single C `AND`. Fixed by retyping
   `address`/`value` as `long long` in both `_rp2040.pyx` and `_rp2040.pxd` (matching `_bit.pyx`'s
   own `u32`/`s32` convention, not `unsigned int` - a `long long` round-trips negative/oversized
   Python ints via a plain C `&`, the same as the removed `object` parameter did, so out-of-range
   callers elsewhere in the codebase keep working instead of hitting `OverflowError`). `value` on
   `write_uint32` specifically needed a caller audit first (`sio.write_uint32`'s hardware-divider
   emulation relies on receiving the *true signed* Python value, per the existing "sign-preservation
   bug" entry above) - confirmed safe since a `long long` preserves sign and magnitude for every
   real caller (register-derived values only, all comfortably inside `long long`'s range).

2. **A bare `0xFFFFFFFF`-style literal is not a C literal to Cython - it's a Python `int`
   constant.** This is the bigger one. Any hex literal that doesn't fit inside a signed 32-bit
   `int` (i.e. `0x80000000` through `0xFFFFFFFF`) is parsed by Cython as a Python object constant
   unless explicitly suffixed (`0xFFFFFFFFU`) or cast. That meant `n & 0xFFFFFFFF` - even where `n`
   is a genuine `cdef long long`/`unsigned int` local - silently compiled to
   `PyNumber_And(__Pyx_PyLong_From_PY_LONG_LONG(n), <boxed 0xFFFFFFFF>)` followed by
   `__Pyx_PyLong_As_unsigned_int(...)`: a full box, a Python-level bigint AND, and an unbox, on
   what looked like (and was written to be) a single C instruction. This exact pattern was in
   `_bit.pyx`'s `u32()`/`s32()` themselves - the two helpers this whole port's docstrings hold up
   as the "genuinely C-typed" answer to the first attempt's boxing bug - so every call to `u32()`/
   `s32()` anywhere in the interpreter was paying this tax. It was also directly inline at ~30 more
   sites across `_cortex_m0_core.pyx`/`_rp2040.pyx`, most critically `core.registers[15] =
   (core.registers[15] + 2) & 0xFFFFFFFF` (the PC increment - executed on literally every
   instruction, 8 call sites) and `core.n = (result & 0x80000000) != 0` (the N-flag update in
   `add_update_flags`/`subtract_update_flags` and ~15 `op_*` handlers - executed on essentially
   every arithmetic/logical instruction). Verified in isolation first (a standalone 4-variant `.pyx`
   confirmed a bare literal boxes while `<long long>0xFFFFFFFF`/`0xFFFFFFFFU`/a `cdef long long`
   module constant all compile to a single C `&`), then fixed by mechanically appending the `U`
   suffix to every literal in `{0xFFFFFFFF, 0x80000000, 0xFFFFFFFC, 0xFFFFFFFE, 0xFFFFFFFD,
   0xFFFFFFF9, 0xFFFFFFF1, 0xFFFF0000, 0xF0000000}` across all three `.pyx` files (~78 sites) -
   confirmed each one now compiles to plain C by re-reading the generated C, not just re-running
   the benchmark and assuming.

**Measured results after both fixes, on CPython 3.10 (this project's default target - see
`.python-version` - and, since 3.10 sits below the abi3 floor, a normal per-version build, not the
stable-ABI one 3.11+ gets - see the abi3 finding below for why that distinction turned out to
matter a lot for this specific measurement):**

- *Synthetic* (`rp2040py bench`, default 5,000,000-instruction ADDS/SUBS mix): native throughput
  went from ~523K instr/sec (pure Python) to **~13.3M instr/sec** - **~25.5x**, not the ~4x
  originally measured for the first Cython pass.
- *Real firmware boot* (MicroPython 1.28.0 + littlefs, boot to first `print()`, same fixture as
  the README's table): **46.65s -> 25.83s**, **~7.3x** over the 188.98s pure-Python baseline (was
  ~4.1x). Smaller relative win than the synthetic benchmark because a real boot spends a large,
  unchanged share of its time in still-Python peripherals (UART, SSI/littlefs, USB) that this port
  never touched - the synthetic benchmark is 100% inside the code paths these two fixes actually
  touch, a real boot isn't.
- Closer to PyPy than before, not caught up: PyPy measures ~40M instr/sec synthetic / 8.75s real
  boot on the same machine - synthetic is now ~3x behind (was ~24x), real boot ~3x behind (was
  ~4x). The remaining gap is architectural, not another hidden-boxing bug: PyPy's trace JIT
  specializes the *actual* dynamic instruction mix at runtime, where this port dispatches through a
  fixed, ahead-of-time C function-pointer table sized for the worst case every time.
- `PYTHON_JIT=1` (CPython 3.14's experimental tier-2 JIT) remains slightly *slower* than
  `PYTHON_JIT=0` for native mode even after these fixes (~4.7M vs. ~5.2M instr/sec synthetic,
  measured on the 3.11 abi3 build) - unaffected by either fix since both touched code that already
  ran outside the CPython bytecode interpreter. The outer driving loop (`Simulator.execute`/
  `_bench_synthetic`) is thin Python bytecode that immediately calls into the now much-faster C
  extension per instruction; CPython's tier-2 JIT's specialization/instrumentation bookkeeping is
  pure overhead on a loop that does almost no work at the bytecode level to begin with. Not
  something this project's code controls.

**A third, unrelated discovery made while producing the numbers above: the abi3/`Py_LIMITED_API`
build (what every CPython 3.11+ install actually gets) is measurably slower than a normal
per-version build of the identical source** - ~5.2M instr/sec synthetic / 33.90s real boot on
CPython 3.11 (abi3) vs. the ~13.3M / 25.83s above on 3.10 (normal build), same fixes, same
machine, same run. Not yet root-caused (a reasonable suspect: the limited API routes typed-
memoryview-heavy code like this through more indirection than the normal C-API's direct struct/
slot access, but that's a hypothesis, not confirmed by reading generated code the way the two
boxing bugs above were) and not something this pass changed - `_use_abi3()`'s CPython-3.11-floor
stable-ABI wheel is a deliberate one-wheel-covers-every-3.11+-version distribution tradeoff (see
"Stable ABI (abi3)" earlier in this file), and trading it away is a real decision for whoever owns
that tradeoff, not something to flip silently as a side effect of a performance pass. Flagging it
here as a scoped, standalone follow-up instead.

**Found and fixed in the same pass, unrelated to Cython specifically: `setup.py` was not passing
any explicit optimization flags to the C compiler at all**, relying entirely on whatever
`sysconfig`'s ambient `CFLAGS`/`OPT` happened to be for the interpreter running the build (`-O3` on
this machine, incidentally - not a guarantee `cibuildwheel`'s manylinux containers or every
downstream packager's CPython necessarily share). Fixed by adding explicit
`extra_compile_args=["-O3", "-std=c99"]` (`["/O2", "/W3"]` on MSVC) and `-Wl,-strip-all` at link
time (`RP2040PY_DISABLE_STRIP=1` to keep symbols, e.g. for profiling the extension itself) -
mirrors `py_ballisticcalc.exts/setup.py`'s own platform-flag pattern (this file's docstring already
said it "mirrors" that file), except deliberately *not* copying that file's own `c_compile_args =
["-g", "-O0", "-std=c99"]` - real, still-present `-O0` there, apparently debug flags left over from
a troubleshooting session and never reverted. Confirmed via `sysconfig.get_config_var("CFLAGS")`
that this machine's ambient flags already included `-O3` (so the explicit flags measured as a
no-op here, benchmark identical before/after down to noise) - added anyway since "happens to
already be optimized on this machine" isn't the same guarantee as "is optimized," and the stripped
`.so` files are a genuine, measured win regardless (~334KB vs. ~1.96MB for `_cortex_m0_core.so`,
same benchmarked speed).

**A build hazard found the hard way while producing all the numbers above, unrelated to any of the
three fixes themselves: stale `.so` files from a previous Python-version build silently shadow a
fresh one.** `src/rp2040py/native/` accumulated `_cortex_m0_core.abi3.so`,
`_cortex_m0_core.cpython-310-*.so`, *and* `_cortex_m0_core.cpython-311-*.so` simultaneously after
switching the dev venv between Python versions a few times without cleaning between builds -
Python's extension-suffix search order prefers an exact `cpython-3XX-*` match over the generic
`.abi3.so` when both are present and loadable under that interpreter, so a *stale*, version-
matching `.so` left over from an earlier (possibly broken, mid-experiment) build silently wins over
a freshly-built, correct one sitting right next to it - no error, just the wrong code running,
surfaced here only because a completely unrelated symbol (`SYSM_CONTROL`) happened to be missing
from the stale build and tripped the `except ImportError` fallback loudly. A quieter version of the
same staleness (same symbols present, just older/different codegen) would have produced no warning
at all. `rm -f src/rp2040py/native/*.so src/rp2040py/native/*.c src/rp2040py/native/*.html &&
uv sync --reinstall-package rp2040py --no-cache` before trusting any from-source perf number is the
only real guard against this - a real `pip install`/`uv add` from a clean checkout never hits it
(one target interpreter, one build, nothing stale to shadow it), so this is purely a "rebuilding
in-place across multiple interpreters in the same checkout" hazard, exactly what a local dev/perf
session does.

**Lesson for future work on these three files:** `cython -a`'s color-coded HTML is a reasonable
first pass, but its score numbers are not a reliable 0-9 scale and both boxing bugs above were
found only by grepping the *generated C* for `PyNumber_*`/`__Pyx_PyLong_*` and reading the
surrounding function, not by trusting the annotation view. Any new arithmetic touching a literal at
or above `0x80000000` needs the same treatment (`U`/`LL`/`ULL` suffix, or a `cdef` constant) or it
will silently re-introduce this exact bug. Relatedly: `noexcept` on an individual `op_*` handler is
a dead end as long as they're only ever invoked through `DISPATCH_TABLE`/`OpHandler` - the
generated call site's exception check (`if (unlikely(result == -1)) ...`) is emitted based on the
*function pointer's* declared type, not the concrete function actually behind it at runtime, so
marking individual table entries `noexcept` changes nothing observable; it would need `OpHandler`
itself to be `noexcept`, which isn't safe here (real bus/peripheral/`bl_taken`-callback exceptions
need to keep propagating, not get silently swallowed).


<!-- migrated verbatim from docs/BACKLOG.md lines 1085-1135 -->

### Not yet tried - real, scoped-out follow-ups, not vague ideas

Each of these was identified during this pass and deliberately left for later, not forgotten:

1. **`_execute_batch()`'s own per-instruction dispatch loop → native.** The single biggest
   remaining lever, **confirmed, not just theorized**: it's now proportionally the dominant
   remaining cost (~46.8% of total profiled time, up from ~43.8% before either part above, since
   the other two legs got cheaper around it) - exactly what the original three-way profile
   predicted before any of this landed. Even with a native CPU core, the `while` loop in
   `simulator.py` that calls `core.execute_instruction()` one instruction at a time stays plain
   Python - only the instruction *handler* was ever ported, not the loop driving it. A genuine
   further port would need to move the loop's own control flow into native code (plus whatever it
   touches per iteration - `rp2040.core.waiting`, the now-batched clock bookkeeping,
   `_TIME_CHECK_INTERVAL`'s real-time budget check), which is a larger, more invasive change than
   either part above (it's the piece everything else in `Simulator` hangs off of) and was
   deliberately scoped out up front, not attempted here.
2. **`rx_fifo`/`tx_fifo` inlined as raw C arrays inside the native `StateMachine`**, instead of the
   plain `utils.fifo.FIFO` Python objects it still uses today. Each state machine owns exactly two
   fixed-size-4 FIFOs; a small `unsigned int[4]` + start/count pair per FIFO would remove the last
   Python-object-attribute-access hot spot inside `StateMachine` itself (`fifo.py`'s own
   `empty`/`full`/`push`/`pull` showed up as a real, if modest, line item in the post-Part-1
   profile - 0.536s self-time over 6.1M calls). Not done here specifically to keep Part 1's diff
   small enough to review and correctness-verify in one pass (a second, independent
   implementation of FIFO's exact semantics, "kept in sync by hand" the same way this file's
   `WAIT_TYPE_*`/register-bit constants already are, is a second place the two implementations
   could silently drift) - worth profiling again first to confirm it's still worth the added
   surface, now that the bigger PIO-internal costs are gone.
3. **The bitfield-derived property getters** (`sideset_count`, `in_base`, `push_threshold`, etc.)
   are Cython `property`s over already-typed fields today, not bare `cdef` methods - faster than
   the pure-Python version already, but a `property` still pays Python's `__get__` protocol
   overhead per access that a plain `cdef` method call wouldn't. Left as properties deliberately
   for this pass (same call syntax as the reference implementation, `self.sideset_count` not
   `self.sideset_count()` - zero external-facing change) - converting them is a candidate only if a
   future profile still shows them hot after the two bigger levers above are addressed.
4. **`RP2040.gpio`/`.dma` still aren't cimportable from native code at all** - `StateMachine.rp2040`
   stays `object`-typed because `native/_rp2040.pxd`'s typed surface only covers the bus
   `read_uint32`/`write_uint32` family, not `gpio`/`dma` (they live in `RP2040`'s own `cdef dict
   __dict__`). Every `self.rp2040.gpio[i].input_value`/`self.rp2040.dma.set_dreq(...)` call
   (`jmp_condition`'s PIN case, `check_wait`'s PIN case, `_update_dma_rx`/`_update_dma_tx`, ...)
   still goes through ordinary Python attribute lookup regardless of `StateMachine` itself being
   native. Giving `GPIOPin`/`RPDMA` the same typed-`.pxd` treatment `RP2040` already has would be a
   materially bigger, separate effort (new native modules, not an extension of this one) - not
   attempted, not even scoped in detail yet.
5. **Firmware download itself hasn't been re-verified end-to-end against this faster baseline** -
   every profiling run in this section used a bounded 25s window (`asyncio.wait_for(...,
   timeout=...)`, chosen so cProfile always gets to write its stats regardless of how far the boot
   got), not a full boot to completion. "9.2x more PIO steps in the same window" is solid evidence
   of a real speedup, but isn't the same claim as "a full firmware download now finishes in N
   seconds" - that number hasn't been measured. See `docs/CYW43_WIFI_BACKLOG.md`'s own note on this
   under its "Performance side quest" entry.

