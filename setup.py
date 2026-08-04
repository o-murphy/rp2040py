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

_ABI3_FLOOR = (3, 11)
_ABI3_HEX = "0x030B0000"

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

if platform.system() == "Windows":
    _EXTRA_COMPILE_ARGS = ["/O2", "/W3"]
    _EXTRA_LINK_ARGS: list[str] = []
else:
    # -O3, not -O0: an earlier version of this project's sibling (py_ballisticcalc.exts/setup.py,
    # the pattern this file mirrors) shipped exactly this list with -O0 instead - debug flags left
    # in from a troubleshooting session and never reverted, silently building its C extensions
    # fully unoptimized. Found by reading that file side by side with this one while chasing an
    # unrelated performance question here; -O3 is deliberate, not a typo for the more conservative
    # -O2 - the isolated Cython-vs-boxing win this module depends on (see docs/BACKLOG.md) wants
    # every optimization pass available, and this is a small, self-contained extension where -O3's
    # usual risks (code bloat, aggressive inlining hurting icache on a large codebase) don't apply.
    _EXTRA_COMPILE_ARGS = ["-O3", "-std=c99"]
    # -Wl,-strip-all: drops debug symbols/relocation info from the built .so at link time (smaller
    # wheel, marginally faster load - doesn't touch the optimizations above, which happen at
    # compile time on the .c GCC/Clang already emitted from Cython's own generated source).
    _EXTRA_LINK_ARGS = [] if _DISABLE_STRIP else ["-Wl,-strip-all"]


def _use_abi3() -> bool:
    if sys.version_info < _ABI3_FLOOR:
        return False
    # Py_LIMITED_API and Py_GIL_DISABLED are mutually incompatible (PEP 703) - free-threaded
    # builds always get a normal, version-specific extension instead.
    return not sysconfig.get_config_var("Py_GIL_DISABLED")


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

    use_abi3 = _use_abi3()
    ext_modules = [
        Extension(
            f"rp2040py.native.{path.stem}",
            [str(path)],
            py_limited_api=use_abi3,
            define_macros=[("Py_LIMITED_API", _ABI3_HEX)] if use_abi3 else [],
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
if _use_abi3():
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
