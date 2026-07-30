from collections.abc import Iterable, Iterator


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
    return int.from_bytes(buffer[offset : offset + 2], "little")


def write_uint16_le(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "little")


def read_uint32_le(buffer: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(buffer[offset : offset + 4], "little")


def write_uint32_le(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")


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
