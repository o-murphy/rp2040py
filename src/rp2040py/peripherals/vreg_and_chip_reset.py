"""Public facade for VREG_AND_CHIP_RESET: prefers the native block in `rp2040py.native._vreg_and_chip_reset` (a Cython shell over `native/core/vreg.hpp`) when it is importable, falling back to the plain-Python reference in
`_vreg_and_chip_reset.py` otherwise. Every caller imports `RPVREGAndChipReset` from here - never from `_vreg_and_chip_reset.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_small_blocks_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._vreg_and_chip_reset import (
    BOD,
    CHIP_RESET,
    HAD_POR,
    HAD_PSM_RESTART,
    HAD_RUN,
    PSM_RESTART_FLAG,
    VREG,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._vreg_and_chip_reset import RPVREGAndChipReset
except ImportError:
    from rp2040py.peripherals._vreg_and_chip_reset import RPVREGAndChipReset

__all__ = (
    "BOD",
    "CHIP_RESET",
    "HAD_POR",
    "HAD_PSM_RESTART",
    "HAD_RUN",
    "PSM_RESTART_FLAG",
    "VREG",
    "RPVREGAndChipReset",
)
