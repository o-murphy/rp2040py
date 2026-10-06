"""Public facade for the PPB (the Cortex-M0's private peripheral bus: SysTick, NVIC, the SCB registers): prefers the native PPB in `rp2040py.native._ppb` (a Cython shell over the
C++ block of `native/core/ppb.hpp`, compiled at build time when Cython and a C++ compiler are available - see `setup.py`) when it is importable, falling back to the plain-Python
reference implementation in `_ppb.py` otherwise. Every caller imports `RPPPB` and the register offsets from here - never from `_ppb.py` or `rp2040py.native` directly (the
pure-Python chip, `_rp2040.py`, is the one deliberate exception: it is the pure build and uses the reference). Mirrors `peripherals/pwm.py`/`adc.py`/`dma.py`.

The two are held to identical behaviour by the lockstep differential of `tests/test_ppb_diff.py`; the pure-Python class stays the oracle
(docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._ppb import CPUID, ICSR, SHPR2, SHPR3, VTOR

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._ppb import RPPPB
except ImportError:
    from rp2040py.peripherals._ppb import RPPPB

__all__ = (
    "CPUID",
    "ICSR",
    "RPPPB",
    "SHPR2",
    "SHPR3",
    "VTOR",
)
