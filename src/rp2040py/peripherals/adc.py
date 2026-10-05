"""Public facade for the ADC: prefers the native ADC in `rp2040py.native._adc` (a Cython shell over the C++ block of `native/core/adc.hpp`, compiled at build time when Cython
and a C++ compiler are available - see `setup.py`) when it is importable, falling back to the plain-Python reference implementation in `_adc.py` otherwise. Every caller imports
`RPADC` from here - never from `_adc.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception: it is the pure build and uses the
reference). Mirrors `peripherals/i2c.py`/`spi.py`/`uart.py`/`dma.py`/`ssi.py`.

The two are held to identical behaviour by the lockstep differential of `tests/test_adc_diff.py`; the pure-Python class stays the oracle
(docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._adc import RPADC
except ImportError:
    from rp2040py.peripherals._adc import RPADC

__all__ = ("RPADC",)
