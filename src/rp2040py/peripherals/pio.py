"""Public facade: prefers the native Cython port in `rp2040py.native._pio` (compiled at build time
when Cython and a C compiler are available - see `setup.py`) when it's importable, falling back to
the plain-Python reference implementation in `_pio.py` otherwise. Every caller imports `RPPIO` from
here (or from `rp2040py`, which re-exports it) - never from `_pio.py` or `rp2040py.native` directly.
Mirrors `cortex_m0_core.py`/`peripherals/state_machine.py`.

`StateMachine` (its own facade) and `WaitType` (a `pio_registers` value) have no compiled variant of
their own here and are re-exported unchanged, so existing
`from rp2040py.peripherals.pio import RPPIO/StateMachine/WaitType` call sites keep working.
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals.pio_registers import WaitType
from rp2040py.peripherals.state_machine import StateMachine

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._pio import RPPIO
except ImportError:
    from rp2040py.peripherals._pio import RPPIO

__all__ = ("RPPIO", "StateMachine", "WaitType")
