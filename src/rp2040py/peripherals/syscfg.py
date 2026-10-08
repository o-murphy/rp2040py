"""Public facade for SYSCFG: prefers the native block in `rp2040py.native._syscfg` (a Cython shell over `native/core/syscfg.hpp`) when it is importable, falling back to the plain-Python reference in
`_syscfg.py` otherwise. Every caller imports `RP2040SysCfg` from here - never from `_syscfg.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_small_blocks_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._syscfg import (
    DBGFORCE,
    DBGFORCE_RESET,
    DBGFORCE_WRITABLE,
    MEMPOWERDOWN,
    MEMPOWERDOWN_MASK,
    PROC0_NMI_MASK,
    PROC1_NMI_MASK,
    PROC_CONFIG,
    PROC_CONFIG_RESET,
    PROC_CONFIG_WRITABLE,
    PROC_IN_SYNC_BYPASS,
    PROC_IN_SYNC_BYPASS_HI,
    PROC_IN_SYNC_BYPASS_HI_MASK,
    PROC_IN_SYNC_BYPASS_MASK,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._syscfg import RP2040SysCfg
except ImportError:
    from rp2040py.peripherals._syscfg import RP2040SysCfg

__all__ = (
    "DBGFORCE",
    "DBGFORCE_RESET",
    "DBGFORCE_WRITABLE",
    "MEMPOWERDOWN",
    "MEMPOWERDOWN_MASK",
    "PROC0_NMI_MASK",
    "PROC1_NMI_MASK",
    "PROC_CONFIG",
    "PROC_CONFIG_RESET",
    "PROC_CONFIG_WRITABLE",
    "PROC_IN_SYNC_BYPASS",
    "PROC_IN_SYNC_BYPASS_HI",
    "PROC_IN_SYNC_BYPASS_HI_MASK",
    "PROC_IN_SYNC_BYPASS_MASK",
    "RP2040SysCfg",
)
