"""Public facade for RTC: prefers the native block in `rp2040py.native._rtc` (a Cython shell over `native/core/rtc.hpp`) when it is importable, falling back to the plain-Python reference in
`_rtc.py` otherwise. Every caller imports `RP2040RTC` from here - never from `_rtc.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/watchdog.py`; held to identical behaviour by `tests/test_rtc_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._rtc import RP2040RTC
except ImportError:
    from rp2040py.peripherals._rtc import RP2040RTC

__all__ = ("RP2040RTC",)
