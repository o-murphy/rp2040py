"""Shared runtime gate for the three public facades (rp2040py.rp2040, rp2040py.cortex_m0_core,
rp2040py.utils.bit) that each choose between rp2040py.native's compiled backend and the
pure-Python implementation.

Each facade imports directly from a specific rp2040py.native submodule (e.g. `from
rp2040py.native._rp2040 import RP2040`), not from the rp2040py.native package itself - once
rp2040py.native has been imported once, Python caches it in sys.modules and its own __init__.py
body never re-runs, so a check placed only there cannot block a later, independent submodule
import. This has to be checked at every one of those three call sites instead.
"""

import os

__all__ = ("native_disabled", "raise_import_error_on_native_disabled")


def native_disabled() -> bool:
    """True if RP2040PY_SKIP_CYTHON=1 is set, forcing every facade to use its pure-Python
    implementation even when rp2040py.native is installed and importable."""
    return os.environ.get("RP2040PY_SKIP_CYTHON") == "1"


def raise_import_error_on_native_disabled() -> None:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
