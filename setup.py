"""setup.py for rp2040py: builds the optional rp2040py.native Cython extension.

All package metadata (name, dependencies, dynamic version via setuptools-scm, etc.) lives in
pyproject.toml - this file only adds the compiled extension, and does so optionally: rp2040py
must install everywhere, including on platforms without a working C toolchain, in which case this
falls back to a pure-Python wheel. The try/except ImportError fallback in rp2040py/rp2040.py,
rp2040py/cortex_m0_core.py, and rp2040py/utils/bit.py then transparently uses the pure-Python
implementation at runtime - correctness is identical either way, only speed differs (see
docs/BACKLOG.md's "Cython port of the interpreter core" section).

Mirrors the pattern in https://github.com/o-murphy/py-ballisticcalc's py_ballisticcalc.exts/setup.py.

Env vars:
- RP2040PY_SKIP_NATIVE_BUILD=1 - skip building the extension outright, forcing a pure-Python wheel
  regardless of whether Cython/a C compiler are actually available (e.g. for a deliberately "pure"
  release artifact rather than a best-effort one).

Stable ABI (abi3): built against Py_LIMITED_API for CPython 3.11+ - its buffer-protocol support,
needed by this code's heavy use of typed memoryviews, only entered the limited API at 3.11 (verified
directly: forcing the floor down to 3.10 fails to compile - Py_buffer's struct fields, e.g. format/
strides/itemsize, are hidden by CPython's own headers below the 3.11 hex gate). Below that floor,
or on free-threaded builds (Py_LIMITED_API and Py_GIL_DISABLED are mutually incompatible per PEP
703), falls back to a normal, version-specific extension - which also means 3.10 (this project's
default dev target, see .python-version) always gets the normal build, never abi3, and measures
faster for it (see docs/BACKLOG.md's Cython follow-up section: ~13.3M vs. ~5.2M instr/sec
synthetic, same source, abi3 vs. normal). Must match [tool.cibuildwheel] in pyproject.toml, which
builds cp311-abi3 and cp3XXt separately for exactly this reason.
"""

import platform
import sys
import sysconfig
from os import environ
from pathlib import Path

from setuptools import Extension, setup

# Cross-compiled target detection.
# NOTE: platform.system() reports the *host* OS under cross-compilation
# (pyodide-build, Android, and iOS builds all run setup.py with the host
# interpreter and only patch sysconfig to describe the target), so it cannot
# be used to detect these targets. sysconfig.get_platform() is the correct
# signal, since the cross-build tooling overrides the host's sysconfig data
# to reflect the target platform.
_SYSCONFIG_PLATFORM = sysconfig.get_platform()
IS_EMSCRIPTEN = "emscripten" in _SYSCONFIG_PLATFORM
IS_ANDROID = "android" in _SYSCONFIG_PLATFORM
IS_IOS = "ios" in _SYSCONFIG_PLATFORM

# Stable ABI target: build once per platform, compatible with Python 3.11+.
# Disabled when:
#   - coverage tracing is requested (CYTHON_TRACE uses internal CPython APIs)
#   - building on free-threaded Python (Py_GIL_DISABLED conflicts with Py_LIMITED_API)
#   - targeting Emscripten: each Pyodide release pins one exact
#     CPython+Emscripten build with no forward-ABI guarantee between
#     versions, and wheel resolution there requires an exact cp3XX match
#     rather than abi3.
#   - targeting Android: extensions must link libpythonX.Y.so explicitly,
#     because Bionic's dlopen requires every dependency to resolve to a
#     literal file at load time (no lazy/implicit symbol resolution from the
#     host process the way glibc allows for stable-ABI extensions). This
#     hardcodes a runtime dependency on one specific CPython point release
#     regardless of the abi3 tag on the wheel, so stable ABI buys nothing
#     here and only hides a real version pin (confirmed empirically: a
#     cp311-abi3-android wheel built against the cp313 Android crossenv
#     failed to import on a 3.14 runtime with
#     "library libpython3.13.so not found").
#   - targeting iOS: same rationale as Android, app runtimes typically embed
#     one specific CPython build.
_ABI3_FLOOR = (3, 11)
_ABI3_HEX = "0x030B0000"
_GIL_DISABLED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
USE_LIMITED_API = (
    sys.version_info >= _ABI3_FLOOR and not _GIL_DISABLED and not IS_EMSCRIPTEN and not IS_ANDROID and not IS_IOS
)

# Relative to setup.py's own directory, not Path(__file__).parent (absolute) - setuptools
# rejects absolute paths in Extension sources ("setup() arguments must *always* be /-separated
# paths relative to the setup.py directory").
_NATIVE_DIR = Path("src/rp2040py/native")

# Explicit optimization flags rather than relying on the ambient interpreter's own sysconfig
# CFLAGS: those happen to already include -O3 on a normal CPython build, but that's an implicit
# default this project doesn't control, not a guarantee - a debug build, a different distro's
# CPython packaging, or a cibuildwheel manylinux container could hand back something else (or
# nothing) for OPT/CFLAGS. This is a hand-written interpreter core on a genuinely hot per-instruction
# path (see docs/BACKLOG.md's Cython section), so it needs -O2/-O3 for real, not by accident.
# RP2040PY_DISABLE_STRIP=1 keeps debug symbols in the built .so (e.g. for profiling this
# extension itself with a sampling profiler that wants symbol names, or debugging a segfault) -
# stripped is the default since none of the three compiled modules are meant to be debugged in
# the shipped wheel, only symbols the (much larger, unstripped) sdist->local rebuild path needs.
_DISABLE_STRIP = environ.get("RP2040PY_DISABLE_STRIP") == "1"

# Platform-specific compiler flags
is_msvc = platform.system() == "Windows"
is_macos = platform.system() == "Darwin"

# -O3, not -O0: an earlier version of this project's sibling (py_ballisticcalc.exts/setup.py, the
# pattern this file mirrors) shipped exactly this list with -O0 instead - debug flags left in from
# a troubleshooting session and never reverted, silently building its C extensions fully
# unoptimized. Found by reading that file side by side with this one while chasing an unrelated
# performance question here; -O3 is deliberate, not a typo for the more conservative -O2 - the
# isolated Cython-vs-boxing win this module depends on (see docs/BACKLOG.md) wants every
# optimization pass available, and this is a small, self-contained extension where -O3's usual
# risks (code bloat, aggressive inlining hurting icache on a large codebase) don't apply.
if is_msvc:
    _EXTRA_COMPILE_ARGS = ["/O2", "/W3"]
    _EXTRA_LINK_ARGS: list[str] = []
elif is_macos:
    _EXTRA_COMPILE_ARGS = ["-O3", "-std=c99"]
    # No -Wl,-strip-all here: that's GNU ld syntax (see the Linux branch below) - Apple's linker
    # rejects it outright ("ld: unknown options: -strip-all"), which broke every macOS wheel build
    # the one time this was tried unconditionally on "not Windows" instead of "Linux specifically"
    # (a real, CI-caught regression, not a hypothetical). Apple's ld does have its own strip flags
    # (-S/-x), but skipping strip here entirely rather than guessing at one - this project has no
    # macOS runner to actually verify a replacement against, and strip is a binary-size nice-to-
    # have (confirmed no speed difference on Linux), not worth a second unverified platform-specific
    # guess.
    _EXTRA_LINK_ARGS = []
elif IS_EMSCRIPTEN:
    # pyodide-build already sets CC=emcc/CXX=em++ and its own CFLAGS/CXXFLAGS/
    # LDFLAGS (typically -Oz for size) before invoking setup.py -- don't
    # override CC/CXX here, and keep flags minimal so we don't fight or
    # duplicate what the cross-build environment already applies.
    # "-Wl,-strip-all" is dropped: em++'s linker wrapper does not reliably
    # support arbitrary native-ld passthrough flags for stripping.
    _EXTRA_COMPILE_ARGS = ["-std=c99"]
    _EXTRA_LINK_ARGS = []
else:
    _EXTRA_COMPILE_ARGS = ["-O3", "-std=c99"]
    # -Wl,-strip-all: drops debug symbols/relocation info from the built .so at link time (smaller
    # wheel, marginally faster load - doesn't touch the optimizations above, which happen at
    # compile time on the .c GCC/Clang already emitted from Cython's own generated source). GNU ld
    # syntax specifically - verified Linux-only, see the Darwin branch above for why it isn't
    # shared with macOS.
    _EXTRA_LINK_ARGS = [] if _DISABLE_STRIP else ["-Wl,-strip-all"]


def _build_ext_modules() -> list[Extension]:
    if environ.get("RP2040PY_SKIP_NATIVE_BUILD") == "1":
        return []
    # Cython extensions target CPython's C-API; PyPy's cpyext emulation is a poor fit for it
    # (especially the typed-memoryview-heavy code here) and there's no benefit anyway - PyPy's
    # own JIT already gives the pure-Python fallback most of what Cython buys on CPython.
    if sys.implementation.name != "cpython":
        return []

    sources = sorted(_NATIVE_DIR.glob("*.pyx"))
    if not sources:
        return []

    try:
        from Cython.Build import cythonize
    except ImportError:
        return []

    ext_modules = [
        Extension(
            f"rp2040py.native.{path.stem}",
            [str(path)],
            py_limited_api=USE_LIMITED_API,
            define_macros=[("Py_LIMITED_API", _ABI3_HEX)] if USE_LIMITED_API else [],
            extra_compile_args=_EXTRA_COMPILE_ARGS,
            extra_link_args=_EXTRA_LINK_ARGS,
            # If the compiler/toolchain is genuinely missing, build_ext skips this extension
            # (with a warning) instead of failing the whole build - rp2040py must install
            # everywhere, including without a C compiler.
            optional=True,
        )
        for path in sources
    ]
    return cythonize(
        ext_modules,
        compiler_directives={"language_level": "3", "boundscheck": False, "wraparound": False},
        annotate=True,
    )


cmdclass = {}
if USE_LIMITED_API:
    from wheel.bdist_wheel import bdist_wheel

    # py_limited_api=True on the Extension above tells Cython/the compiler to build against the
    # limited API; this subclass is what actually tags the *wheel* filename as cp310-abi3-* -
    # they're two separate settings, not one.
    class _bdist_wheel_abi3(bdist_wheel):
        def finalize_options(self):
            super().finalize_options()
            self.py_limited_api = f"cp{_ABI3_FLOOR[0]}{_ABI3_FLOOR[1]}"

    cmdclass["bdist_wheel"] = _bdist_wheel_abi3

setup(ext_modules=_build_ext_modules(), cmdclass=cmdclass)
