"""Public facade for PSM: prefers the native block in `rp2040py.native._psm` (a Cython shell over `native/core/psm.hpp`) when it is importable, falling back to the plain-Python reference in
`_psm.py` otherwise. Every caller imports `RPPSM` from here - never from `_psm.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_psm_xosc_resets_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._psm import (
    DONE,
    FRCE_OFF,
    FRCE_ON,
    PSM_BITS_MASK,
    WDSEL,
    WDSEL_BUSFABRIC,
    WDSEL_CLOCKS,
    WDSEL_PROC0,
    WDSEL_PROC1,
    WDSEL_RESETS,
    WDSEL_ROM,
    WDSEL_ROSC,
    WDSEL_SIO,
    WDSEL_VREG_AND_CHIP_RESET,
    WDSEL_XIP,
    WDSEL_XOSC,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._psm import RPPSM
except ImportError:
    from rp2040py.peripherals._psm import RPPSM

__all__ = (
    "DONE",
    "FRCE_OFF",
    "FRCE_ON",
    "PSM_BITS_MASK",
    "RPPSM",
    "WDSEL",
    "WDSEL_BUSFABRIC",
    "WDSEL_CLOCKS",
    "WDSEL_PROC0",
    "WDSEL_PROC1",
    "WDSEL_RESETS",
    "WDSEL_ROM",
    "WDSEL_ROSC",
    "WDSEL_SIO",
    "WDSEL_VREG_AND_CHIP_RESET",
    "WDSEL_XIP",
    "WDSEL_XOSC",
)
