"""Public facade for XIP_CTRL / XIP_AUX: prefers the native block in `rp2040py.native._xip_ctrl` (a Cython shell over `native/core/xip_ctrl.hpp`) when it is importable, falling back to the
plain-Python reference in `_xip_ctrl.py` otherwise. Every caller imports from here - never from `_xip_ctrl.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one
deliberate exception). Held to identical behaviour by `tests/test_xip_diff.py` (docs/records/0098-datasheet-conformance-audit.md, "XIP_CTRL and the XIP subsystem").
"""

from rp2040py._native_gate import raise_import_error_on_native_disabled
from rp2040py.peripherals._xip_ctrl import (
    CTR_ACC,
    CTR_HIT,
    CTRL,
    CTRL_EN,
    CTRL_ERR_BADWRITE,
    CTRL_POWER_DOWN,
    DREQ_XIP_STREAM,
    FLUSH,
    SSI_BAUDR,
    STAT,
    STAT_FIFO_EMPTY,
    STAT_FIFO_FULL,
    STAT_FLUSH_READY,
    STREAM_ADDR,
    STREAM_CTR,
    STREAM_FIFO,
)

try:
    raise_import_error_on_native_disabled()
    from rp2040py.native._xip_ctrl import RPXIPAux, RPXIPCtrl
except ImportError:
    from rp2040py.peripherals._xip_ctrl import RPXIPAux, RPXIPCtrl

__all__ = (
    "CTRL",
    "CTRL_EN",
    "CTRL_ERR_BADWRITE",
    "CTRL_POWER_DOWN",
    "CTR_ACC",
    "CTR_HIT",
    "DREQ_XIP_STREAM",
    "FLUSH",
    "SSI_BAUDR",
    "STAT",
    "STAT_FIFO_EMPTY",
    "STAT_FIFO_FULL",
    "STAT_FLUSH_READY",
    "STREAM_ADDR",
    "STREAM_CTR",
    "STREAM_FIFO",
    "RPXIPAux",
    "RPXIPCtrl",
)
