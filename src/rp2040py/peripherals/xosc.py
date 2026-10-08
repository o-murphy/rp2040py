"""Public facade for XOSC: prefers the native block in `rp2040py.native._xosc` (a Cython shell over `native/core/xosc.hpp`) when it is importable, falling back to the plain-Python reference in
`_xosc.py` otherwise. Every caller imports `RPXOSC` from here - never from `_xosc.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_psm_xosc_resets_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._xosc import (
    CTRL_ENABLE_BITS,
    CTRL_ENABLE_DISABLE,
    CTRL_ENABLE_ENABLE,
    CTRL_ENABLE_LSB,
    CTRL_FREQ_RANGE_1_15MHZ,
    CTRL_FREQ_RANGE_BITS,
    CTRL_MASK,
    DORMANT_VALUE,
    STARTUP_DELAY_BITS,
    STARTUP_RESET,
    STARTUP_X4,
    STATUS_BADWRITE,
    STATUS_ENABLED,
    STATUS_FREQ_RANGE_BITS,
    STATUS_STABLE,
    WAKE_VALUE,
    XOSC_COUNT,
    XOSC_CTRL,
    XOSC_DORMANT,
    XOSC_STARTUP,
    XOSC_STATUS,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._xosc import RPXOSC
except ImportError:
    from rp2040py.peripherals._xosc import RPXOSC

__all__ = (
    "CTRL_ENABLE_BITS",
    "CTRL_ENABLE_DISABLE",
    "CTRL_ENABLE_ENABLE",
    "CTRL_ENABLE_LSB",
    "CTRL_FREQ_RANGE_1_15MHZ",
    "CTRL_FREQ_RANGE_BITS",
    "CTRL_MASK",
    "DORMANT_VALUE",
    "RPXOSC",
    "STARTUP_DELAY_BITS",
    "STARTUP_RESET",
    "STARTUP_X4",
    "STATUS_BADWRITE",
    "STATUS_ENABLED",
    "STATUS_FREQ_RANGE_BITS",
    "STATUS_STABLE",
    "WAKE_VALUE",
    "XOSC_COUNT",
    "XOSC_CTRL",
    "XOSC_DORMANT",
    "XOSC_STARTUP",
    "XOSC_STATUS",
)
