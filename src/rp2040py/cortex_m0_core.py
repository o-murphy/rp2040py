"""Public facade: prefers the real, fully-typed Cython port in rp2040py.native (compiled at build
time when Cython and a C compiler are available - see hatch_build.py) when it's importable,
falling back to the plain-Python implementation in _cortex_m0_core.py otherwise. Every caller in
rp2040py imports from here, never from _cortex_m0_core.py or rp2040py.native directly.
"""

from rp2040py._native_gate import raise_import_error_on_native_disabled

try:
    raise_import_error_on_native_disabled()
    from rp2040py.native._cortex_m0_core import (
        SYSM_CONTROL,
        SYSM_MSP,
        SYSM_PRIMASK,
        SYSM_PSP,
        CortexM0Core,
    )
except ImportError:
    from rp2040py._cortex_m0_core import (
        SYSM_CONTROL,
        SYSM_MSP,
        SYSM_PRIMASK,
        SYSM_PSP,
        CortexM0Core,
    )

__all__ = (
    "SYSM_CONTROL",
    "SYSM_MSP",
    "SYSM_PRIMASK",
    "SYSM_PSP",
    "CortexM0Core",
)
