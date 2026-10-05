"""Public facade for the DMA controller: prefers the native DMA in `rp2040py.native._dma` (a Cython shell over the C++ block
of `native/core/dma.hpp`, compiled at build time when Cython and a C++ compiler are available - see `setup.py`) when it is
importable, falling back to the plain-Python reference implementation in `_dma.py` otherwise. Every caller imports `RPDMA`/
`DREQChannel`/`TREQ` from here - never from `_dma.py` or `rp2040py.native` directly. Mirrors `peripherals/pio.py`/`timer.py`.

The two are held to identical behaviour by the lockstep differential of `tests/test_dma_diff.py`; the pure-Python class stays
the oracle (docs/records/0096-cpp-mcu-core.md). `RPDMAChannel` is the reference's channel class: the native controller's
channels are views of C++ state with the same attribute names, not instances of it.
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._dma import TREQ, DREQChannel, RPDMAChannel

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._dma import RPDMA
except ImportError:
    from rp2040py.peripherals._dma import RPDMA

__all__ = ("RPDMA", "RPDMAChannel", "DREQChannel", "TREQ")
