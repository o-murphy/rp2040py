"""Custom hatchling build hook: compiles the real, fully-typed .pyx sources in
src/rp2040py/native into extension modules. Cython/a C compiler are declared build-system
requirements, but this must still degrade gracefully - rp2040py itself has to install everywhere,
including platforms without a working C toolchain. If Cython isn't importable or the compile
step fails, this logs a warning and ships the wheel without the compiled extension; the
try/except ImportError fallback in rp2040py/rp2040.py and rp2040py/utils/bit.py then transparently
uses the pure-Python implementation at runtime.

Set RP2040PY_SKIP_NATIVE_BUILD=1 to skip the cythonize/build_ext step outright and force a
pure-Python-only wheel, regardless of whether Cython/a C compiler are actually available - e.g.
for a deliberately "pure" release artifact rather than a best-effort one.

Stable ABI (abi3): built against CPython's limited API on 3.11+ (Py_LIMITED_API's buffer-protocol
support - needed by this module's heavy use of typed memoryviews - only landed in the limited API
at 3.11), so one wheel per platform covers every 3.11+ interpreter instead of one per minor
version. Skipped on < 3.11 (falls back to a normal, version-specific build) and on free-threaded
builds, where Py_LIMITED_API and Py_GIL_DISABLED are mutually incompatible - see
[tool.cibuildwheel] in pyproject.toml, which builds cp311-abi3 and cp3XXt separately for exactly
this reason.
"""

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# The limited API floor this project targets - must match [tool.cibuildwheel]'s "cp311-abi3" in
# pyproject.toml. Bumping this means bumping that build matrix too.
_ABI3_FLOOR = (3, 11)
_ABI3_HEX = "0x030B0000"

_SETUP_PY_TEMPLATE = """
from Cython.Build import cythonize
from setuptools import Extension, setup

sources = {sources!r}
use_abi3 = {use_abi3!r}

ext_modules = [
    Extension(
        f"rp2040py.native.{{path.rsplit('/', 1)[-1].removesuffix('.pyx')}}",
        [path],
        py_limited_api=use_abi3,
        define_macros=[("Py_LIMITED_API", {abi3_hex!r})] if use_abi3 else [],
    )
    for path in sources
]

setup(
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={{"language_level": "3", "boundscheck": False, "wraparound": False}},
        annotate=True,
    ),
)
"""


def _use_abi3() -> bool:
    if sys.version_info < _ABI3_FLOOR:
        return False
    # Py_LIMITED_API and Py_GIL_DISABLED are mutually incompatible (PEP 703) - free-threaded
    # builds always get a normal, version-specific extension instead.
    return not sysconfig.get_config_var("Py_GIL_DISABLED")


def _abi3_wheel_tag() -> str:
    # hatchling's own `infer_tag` picks packaging.tags.sys_tags()'s first match, which reflects
    # the *running* interpreter's own specific ABI (e.g. "cp311-cp311-..."), not "abi3" - it has
    # no way to know we built a limited-API extension, so the tag has to be constructed by hand.
    from packaging.tags import sys_tags

    tag = next(iter(t for t in sys_tags() if "manylinux" not in t.platform and "musllinux" not in t.platform))
    major, minor = _ABI3_FLOOR
    return f"cp{major}{minor}-abi3-{tag.platform}"


class NativeBuildHook(BuildHookInterface):
    PLUGIN_NAME = "rp2040py-native-cython"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return

        if os.environ.get("RP2040PY_SKIP_NATIVE_BUILD") == "1":
            self.app.display_info("rp2040py.native: RP2040PY_SKIP_NATIVE_BUILD=1, building pure-Python wheel")
            return

        # Cython extensions target CPython's C-API; PyPy's cpyext emulation is a poor fit for it
        # (especially the typed-memoryview-heavy code here) and there's no benefit anyway - PyPy's
        # own JIT already gives the pure-Python fallback most of what Cython buys on CPython.
        if sys.implementation.name != "cpython":
            self.app.display_info(
                f"rp2040py.native: skipping compilation on {sys.implementation.name}, building pure-Python wheel"
            )
            return

        src_dir = Path(self.root) / "src"
        pkg_dir = src_dir / "rp2040py" / "native"
        sources = [f"rp2040py/native/{p.name}" for p in sorted(pkg_dir.glob("*.pyx"))]
        if not sources:
            self.app.display_warning(f"rp2040py.native: no .pyx sources found in {pkg_dir}, skipping compilation")
            return

        use_abi3 = _use_abi3()
        setup_script = src_dir / "_cython_build.py"
        setup_script.write_text(_SETUP_PY_TEMPLATE.format(sources=sources, use_abi3=use_abi3, abi3_hex=_ABI3_HEX))
        try:
            result = subprocess.run(
                [sys.executable, str(setup_script), "build_ext", "--inplace"],
                cwd=str(src_dir),
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            setup_script.unlink(missing_ok=True)

        if result.returncode != 0:
            self.app.display_warning(
                "rp2040py.native: Cython build failed, shipping pure-Python fallback only:\n"
                + result.stdout
                + result.stderr
            )
            return

        so_files = sorted(pkg_dir.glob("*.so")) + sorted(pkg_dir.glob("*.pyd"))
        if not so_files:
            self.app.display_warning("rp2040py.native: Cython build produced no extension modules, skipping")
            return

        for so_path in so_files:
            build_data["force_include"][str(so_path)] = f"rp2040py/native/{so_path.name}"
        for c_path in pkg_dir.glob("*.c"):
            c_path.unlink(missing_ok=True)

        if use_abi3:
            build_data["tag"] = _abi3_wheel_tag()
        else:
            build_data["infer_tag"] = True
        build_data["pure_python"] = False
        self.app.display_success(
            f"rp2040py.native compiled ({'abi3' if use_abi3 else 'version-specific'}): "
            f"{', '.join(p.name for p in so_files)}"
        )
