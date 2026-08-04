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

Stable ABI (abi3): built against Py_LIMITED_API for CPython 3.10+ - its buffer-protocol support,
needed by this code's heavy use of typed memoryviews, only entered the limited API at 3.10. Below
that floor, or on free-threaded builds (Py_LIMITED_API and Py_GIL_DISABLED are mutually
incompatible per PEP 703), falls back to a normal, version-specific extension. Must match
[tool.cibuildwheel] in pyproject.toml, which builds cp310-abi3 and cp3XXt separately for exactly
this reason.
"""

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
