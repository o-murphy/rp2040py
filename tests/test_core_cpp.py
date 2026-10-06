"""Builds and runs the standalone C++ checks of the MCU core headers (docs/records/0096-cpp-mcu-core.md).

The core is C++17 with no exceptions, no RTTI and no STL (record 0096, D4) so that the same sources
build into the Cython extension and, later, a bare wasm32 module. Compiling it here with exactly
those flags and ``-Werror`` is what keeps a stray ``throw``/``dynamic_cast``/``new`` from sneaking in
through the extension build, which does not use those flags. Skipped when no C++ compiler is on PATH.
"""

import platform
import shutil
import subprocess
from pathlib import Path

import pytest
from utils.emscripten import needs_processes

pytestmark = needs_processes  # the checks compile and run C++ in a subprocess

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "rp2040py" / "native" / "core"
FLAGS = [
    "-std=c++17",
    "-fno-exceptions",
    "-fno-rtti",
    "-ffp-contract=off",
    "-Wall",
    "-Wextra",
    "-Werror",
]  # no fused multiply-add: see setup.py
if platform.machine().lower() in ("i386", "i686"):
    # Same reason as setup.py: a 32-bit x86 compiler defaults to the x87 FPU, whose 80-bit intermediates break exact double comparisons.
    FLAGS += ["-msse2", "-mfpmath=sse"]


def _cxx() -> "str | None":
    for name in ("c++", "g++", "clang++"):
        if shutil.which(name):
            return name
    return None


@pytest.mark.skipif(_cxx() is None, reason="no C++ compiler on PATH")
@pytest.mark.parametrize("source", sorted(p.name for p in (ROOT / "tests" / "cpp").glob("test_*.cpp")))
def test_core_headers_pass_their_cpp_checks(source, tmp_path):
    exe = tmp_path / source.removesuffix(".cpp")
    cmd = [_cxx(), *FLAGS, f"-I{CORE}", str(ROOT / "tests" / "cpp" / source), "-o", str(exe)]
    build = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, check=False, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(_cxx() is None, reason="no C++ compiler on PATH")
@pytest.mark.parametrize("header", sorted(p.name for p in CORE.glob("*.hpp")))
def test_every_core_header_is_self_contained_under_the_core_flags(header):
    """Each header compiles on its own - no hidden include-order dependency, no exceptions/RTTI/STL."""
    cmd = [_cxx(), *FLAGS, f"-I{CORE}", "-fsyntax-only", "-x", "c++", str(CORE / header)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_cxx() is None, reason="no C++ compiler on PATH")
@pytest.mark.parametrize("header", sorted(p.name for p in CORE.glob("*.hpp")))
def test_no_core_header_names_a_constant_after_a_windows_python_macro(header):
    """Python's pyconfig.h on Windows defines `PLATFORM` as "win32"; a constant of that name compiles everywhere but MSVC (found by CI on SYSINFO/TBMAN). Emulated here with the macro."""
    cmd = [_cxx(), *FLAGS, f"-I{CORE}", '-DPLATFORM="win32"', "-fsyntax-only", "-x", "c++", str(CORE / header)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode == 0, result.stderr


def test_every_macro_of_the_core_headers_carries_the_project_prefix():
    """A macro is global to every file that includes the header (Python.h, <windows.h>, a host's own code): `R`, `OP` or `PLATFORM` would collide silently. Include guards and helper macros are all `RP2040PY_*`."""
    import re

    bad = []
    for header in sorted(CORE.glob("*.hpp")):
        for number, line in enumerate(header.read_text(encoding="utf-8").splitlines(), 1):
            match = re.match(r"\s*#\s*(?:define|undef)\s+(\w+)", line)
            if match and not match.group(1).startswith("RP2040PY_"):
                bad.append(f"{header.name}:{number}: {match.group(1)}")
    assert not bad, bad
