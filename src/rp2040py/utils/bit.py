"""Public facade: prefers the real, fully-typed Cython port in rp2040py.native (compiled at build
time when Cython and a C compiler are available - see hatch_build.py) when it's importable,
falling back to the plain-Python implementation in _bit.py otherwise. Every caller in rp2040py
imports from here, never from _bit.py or rp2040py.native directly.
"""

from rp2040py._native_gate import raise_import_error_on_native_disabled

try:
    raise_import_error_on_native_disabled()
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
except ImportError:
    from rp2040py.utils._bit import (
        bit,
        read_uint16_le,
        read_uint32_le,
        s32,
        u32,
        urshift,
        write_uint16_le,
        write_uint32_le,
    )

__all__ = (
    "bit",
    "read_uint16_le",
    "read_uint32_le",
    "s32",
    "u32",
    "urshift",
    "write_uint16_le",
    "write_uint32_le",
)
