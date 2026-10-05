"""Public facade for the DMA controller. Every caller imports `RPDMA`/`DREQChannel`/`TREQ` from here - never from
`_dma.py` directly. Mirrors `peripherals/pio.py`/`timer.py`: it prefers a native implementation when one exists and
falls back to the plain-Python reference in `_dma.py`.

At present only the reference exists; the C++ controller (`native/core/dma.hpp`, record 0096 Phase 4) is the next step,
and `_dma.py` stays the oracle for it (`tests/test_dma_diff.py`).
"""

from rp2040py.peripherals._dma import RPDMA, TREQ, DREQChannel, RPDMAChannel

__all__ = ("RPDMA", "TREQ", "DREQChannel", "RPDMAChannel")
