"""Public facade: prefers the native Cython port in `rp2040py.native._state_machine` (compiled at
build time when Cython and a C compiler are available - see `setup.py`) when it's importable,
falling back to the plain-Python reference implementation in `_state_machine.py` otherwise. Every
caller imports `StateMachine` from here (or from `rp2040py`/`rp2040py.peripherals.pio`, which
re-export it) - never from `_state_machine.py` or `rp2040py.native` directly. Mirrors
`cortex_m0_core.py`'s own facade exactly.
"""

from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._state_machine import StateMachine
except ImportError:
    from rp2040py.peripherals._state_machine import StateMachine

__all__ = ("StateMachine",)
