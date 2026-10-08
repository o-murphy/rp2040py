"""Public facade for BUSCTRL: prefers the native block in `rp2040py.native._busctrl` (a Cython shell over `native/core/busctrl.hpp`) when it is importable, falling back to the plain-Python reference in
`_busctrl.py` otherwise. Every caller imports `RPBUSCTRL` from here - never from `_busctrl.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_small_blocks_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._busctrl import (
    BUS_PRIORITY,
    BUS_PRIORITY_ACK,
    BUS_PRIORITY_MASK,
    PERFCTR0,
    PERFCTR1,
    PERFCTR2,
    PERFCTR3,
    PERFSEL0,
    PERFSEL1,
    PERFSEL2,
    PERFSEL3,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._busctrl import RPBUSCTRL
except ImportError:
    from rp2040py.peripherals._busctrl import RPBUSCTRL

__all__ = (
    "BUS_PRIORITY",
    "BUS_PRIORITY_ACK",
    "BUS_PRIORITY_MASK",
    "PERFCTR0",
    "PERFCTR1",
    "PERFCTR2",
    "PERFCTR3",
    "PERFSEL0",
    "PERFSEL1",
    "PERFSEL2",
    "PERFSEL3",
    "RPBUSCTRL",
)
