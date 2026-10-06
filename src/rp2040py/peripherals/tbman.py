"""Public facade for TBMAN: prefers the native block in `rp2040py.native._tbman` (a Cython shell over `native/core/tbman.hpp`) when it is importable, falling back to the plain-Python reference in
`_tbman.py` otherwise. Every caller imports `RPTBMAN` from here - never from `_tbman.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/uart.py`; held to identical behaviour by `tests/test_ident_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._tbman import ASIC, PLATFORM

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._tbman import RPTBMAN
except ImportError:
    from rp2040py.peripherals._tbman import RPTBMAN

__all__ = ("ASIC", "PLATFORM", "RPTBMAN")
