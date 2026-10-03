"""Fails, instead of silently skipping, when CI expects the compiled extension and it is not there.

`setup.py` builds `rp2040py.native` with `optional=True`, so a toolchain problem (an MSVC or clang
error in the C++17 core, docs/records/0096-cpp-mcu-core.md) would otherwise leave a CI leg running
the pure-Python fallback and every `importorskip("rp2040py.native...")` test quietly skipping. CI sets
RP2040PY_REQUIRE_NATIVE=1 (.github/workflows/pre-commit.yml); locally the test is skipped.
"""

import importlib
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RP2040PY_REQUIRE_NATIVE") != "1", reason="RP2040PY_REQUIRE_NATIVE=1 is not set"
)


# Every .pyx next to this package's sources is an extension setup.py builds: a module added later is required here
# without anyone remembering to list it (a stale or failed build of the newest one is exactly what this guards).
NATIVE_MODULES = sorted(
    p.stem for p in (Path(__file__).resolve().parents[1] / "src" / "rp2040py" / "native").glob("*.pyx")
)


def test_the_list_of_native_modules_is_not_empty():
    assert {"_rp2040", "_timer", "_simulation_clock"} <= set(NATIVE_MODULES)


@pytest.mark.parametrize("module", NATIVE_MODULES)
def test_every_native_module_was_built_and_imports(module):
    importlib.import_module(f"rp2040py.native.{module}")


def test_the_memory_map_is_part_of_the_native_chip():
    from rp2040py.native._rp2040 import RP2040

    chip = RP2040()
    chip.write_uint32(0x20000010, 0xA5A5A5A5)
    assert chip.read_uint32(0x20000010) == 0xA5A5A5A5
