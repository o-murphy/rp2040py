"""Public facade for SYSINFO: prefers the native block in `rp2040py.native._sysinfo` (a Cython shell over `native/core/sysinfo.hpp`) when it is importable, falling back to the plain-Python reference in
`_sysinfo.py` otherwise. Every caller imports `RP2040SysInfo` from here - never from `_sysinfo.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/uart.py`; held to identical behaviour by `tests/test_ident_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._sysinfo import CHIP_ID, GITREF_RP2040, PLATFORM

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._sysinfo import RP2040SysInfo
except ImportError:
    from rp2040py.peripherals._sysinfo import RP2040SysInfo

__all__ = ("CHIP_ID", "GITREF_RP2040", "PLATFORM", "RP2040SysInfo")
