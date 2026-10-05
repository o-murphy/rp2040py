"""Public facade for the I2C (DW_apb_i2c): prefers the native I2C in `rp2040py.native._i2c` (a Cython shell over the C++ block of `native/core/i2c.hpp`, compiled at build
time when Cython and a C++ compiler are available - see `setup.py`) when it is importable, falling back to the plain-Python reference implementation in `_i2c.py`
otherwise. Every caller imports `RPI2C`/`I2CMode`/`I2CSpeed` from here - never from `_i2c.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one
deliberate exception: it is the pure build and uses the reference). The two enums are the reference's own: the native block hands the same members to its callbacks.
Mirrors `peripherals/spi.py`/`uart.py`/`dma.py`/`ssi.py`.

The two are held to identical behaviour by the lockstep differential of `tests/test_i2c_diff.py`; the pure-Python class stays the oracle
(docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._i2c import I2CMode, I2CSpeed

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._i2c import RPI2C
except ImportError:
    from rp2040py.peripherals._i2c import RPI2C

__all__ = (
    "RPI2C",
    "I2CMode",
    "I2CSpeed",
)
