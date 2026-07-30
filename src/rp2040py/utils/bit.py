import struct
from collections.abc import Iterable, Iterator

# Pre-built struct.Struct instances, not struct.unpack_from()/struct.pack_into() with a format
# string on every call: skips re-parsing the format string each time, and beats both that and a
# hand-rolled byte-index-and-shift loop in local benchmarking (~40% faster than the
# slice + int.from_bytes()/to_bytes() this replaced, for both 16- and 32-bit reads/writes).
_STRUCT_U16_LE = struct.Struct("<H")
_STRUCT_U32_LE = struct.Struct("<I")


def bit(n: int) -> int:
    return 1 << n


def u32(n: int) -> int:
    return n & 0xFFFFFFFF


def s32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n & 0x80000000 else n


def urshift(n: int, shift: int) -> int:
    return u32(n) >> (shift & 31)


def read_uint16_le(buffer: bytes | bytearray, offset: int) -> int:
    value: int = _STRUCT_U16_LE.unpack_from(buffer, offset)[0]
    return value


def write_uint16_le(buffer: bytearray, offset: int, value: int) -> None:
    _STRUCT_U16_LE.pack_into(buffer, offset, value & 0xFFFF)


def read_uint32_le(buffer: bytes | bytearray, offset: int) -> int:
    value: int = _STRUCT_U32_LE.unpack_from(buffer, offset)[0]
    return value


def write_uint32_le(buffer: bytearray, offset: int, value: int) -> None:
    _STRUCT_U32_LE.pack_into(buffer, offset, value & 0xFFFFFFFF)


class Uint32Array:
    """Mimics JS's `Uint32Array`: a fixed-size int buffer where every write is silently
    truncated to an unsigned 32-bit value, matching typed-array semantics."""

    __slots__ = ("_data",)

    def __init__(self, length: int):
        self._data = [0] * length

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: int) -> int:
        return self._data[index]

    def __iter__(self) -> "Iterator[int]":
        return iter(self._data)

    def __setitem__(self, index: int, value: float) -> None:
        # int(): JS TypedArray stores implicitly truncate a fractional value (ToUint32) before
        # masking; Python's `&` raises TypeError on a float, so truncate explicitly here.
        self._data[index] = int(value) & 0xFFFFFFFF

    def set(self, values: "Iterable[float]", offset: int = 0) -> None:
        """Mimics JS TypedArray.prototype.set(): bulk-copies values starting at `offset`."""
        for i, value in enumerate(values):
            self._data[offset + i] = int(value) & 0xFFFFFFFF
