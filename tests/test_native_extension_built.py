"""Fails, instead of silently skipping, when CI expects the compiled extension and it is not there.

`setup.py` builds `rp2040py.native` with `optional=True`, so a toolchain problem (an MSVC or clang
error in the C++17 core, docs/records/0096-cpp-mcu-core.md) would otherwise leave a CI leg running
the pure-Python fallback and every `importorskip("rp2040py.native...")` test quietly skipping. CI sets
RP2040PY_REQUIRE_NATIVE=1 (.github/workflows/pre-commit.yml); locally the test is skipped.
"""

import importlib
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RP2040PY_REQUIRE_NATIVE") != "1", reason="RP2040PY_REQUIRE_NATIVE=1 is not set"
)


@pytest.mark.parametrize(
    "module",
    ["_bit", "_cortex_m0_core", "_gpio_pin", "_pio", "_rp2040", "_simulation_clock", "_simulator", "_state_machine"],
)
def test_every_native_module_was_built_and_imports(module):
    importlib.import_module(f"rp2040py.native.{module}")


def test_the_memory_map_is_part_of_the_native_chip():
    from rp2040py.native._rp2040 import RP2040

    chip = RP2040()
    chip.write_uint32(0x20000010, 0xA5A5A5A5)
    assert chip.read_uint32(0x20000010) == 0xA5A5A5A5
