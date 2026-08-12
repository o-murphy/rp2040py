"""Public facade: prefers the native Cython port in `rp2040py.native._simulator` (compiled at
build time when Cython and a C compiler are available - see `setup.py`) when it's importable,
falling back to the plain-Python reference implementation in `_execute_batch.py` otherwise.
`simulator.py` imports `execute_batch` from here - never from `_execute_batch.py` or
`rp2040py.native` directly. Mirrors `cortex_m0_core.py`/`peripherals/state_machine.py`'s own
facade exactly.
"""

from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._simulator import execute_batch
except ImportError:
    from rp2040py._execute_batch import execute_batch

__all__ = ("execute_batch",)
