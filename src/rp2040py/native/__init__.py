"""Compiled Cython-accelerated backend for rp2040py, built in-place by hatch_build.py when Cython
and a C compiler are available. Not imported unconditionally by anything else in the package -
see rp2040py/rp2040.py and rp2040py/utils/bit.py for the try/except ImportError fallback logic
that uses this when present and the pure-Python implementation otherwise.

Set RP2040PY_SKIP_CYTHON=1 to force the pure-Python fallback at runtime even when the compiled
extension is installed - e.g. to rule out a native-specific bug, or to A/B the two backends
without reinstalling. Compare RP2040PY_SKIP_NATIVE_BUILD (hatch_build.py), which instead skips
compiling the extension in the first place, at build time.
"""

import os
import warnings

try:
    if os.environ.get("RP2040PY_SKIP_CYTHON") == "1":
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")

    from rp2040py.native._bit import (
        bit,
        read_uint16_le,
        read_uint32_le,
        s32,
        u32,
        urshift,
        write_uint16_le,
        write_uint32_le,
    )
    from rp2040py.native._cortex_m0_core import (
        SYSM_CONTROL,
        SYSM_MSP,
        SYSM_PRIMASK,
        SYSM_PSP,
        CortexM0Core,
    )
    from rp2040py.native._rp2040 import (
        APB_START_ADDRESS,
        DPRAM_START_ADDRESS,
        FLASH_END_ADDRESS,
        FLASH_START_ADDRESS,
        RAM_START_ADDRESS,
        RP2040,
        SIO_START_ADDRESS,
    )
except ImportError:
    warnings.warn("Native extensions are not available in this environment - falling back to pure Python", stacklevel=2)

__all__ = (
    "APB_START_ADDRESS",
    "DPRAM_START_ADDRESS",
    "FLASH_END_ADDRESS",
    "FLASH_START_ADDRESS",
    "RAM_START_ADDRESS",
    "RP2040",
    "SIO_START_ADDRESS",
    "SYSM_CONTROL",
    "SYSM_MSP",
    "SYSM_PRIMASK",
    "SYSM_PSP",
    "CortexM0Core",
    "bit",
    "read_uint16_le",
    "read_uint32_le",
    "s32",
    "u32",
    "urshift",
    "write_uint16_le",
    "write_uint32_le",
)
