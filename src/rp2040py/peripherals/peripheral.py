from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "BasePeripheral",
    "Peripheral",
    "UnimplementedPeripheral",
    "atomic_update",
)

ATOMIC_NORMAL = 0
ATOMIC_XOR = 1
ATOMIC_SET = 2
ATOMIC_CLEAR = 3


def atomic_update(current_value: int, atomic_type: int, new_value: int) -> int:
    if atomic_type == ATOMIC_XOR:
        return current_value ^ new_value
    if atomic_type == ATOMIC_SET:
        return current_value | new_value
    if atomic_type == ATOMIC_CLEAR:
        return current_value & ~new_value
    print(f"warning: Atomic update called with invalid writeType {atomic_type}")
    return new_value


class Peripheral(Protocol):
    def read_uint32(self, offset: int) -> int: ...
    def write_uint32(self, offset: int, value: int) -> None: ...
    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None: ...


class BasePeripheral:
    def __init__(self, rp2040: "RP2040", name: str):
        self.rp2040 = rp2040
        self.name = name
        self.raw_write_value = 0

    def read_uint32(self, offset: int) -> int:
        self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        if offset > 0x1000:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        return 0xFFFFFFFF

    def write_uint32(self, offset: int, value: int) -> None:
        self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None:
        self.raw_write_value = value
        new_value = (
            atomic_update(self.read_uint32(offset), atomic_type, value) if atomic_type != ATOMIC_NORMAL else value
        )
        self.write_uint32(offset, new_value)

    def debug(self, msg: str) -> None:
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg: str) -> None:
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg: str) -> None:
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg: str) -> None:
        self.rp2040.logger.error(self.name, msg)


class UnimplementedPeripheral(BasePeripheral):
    pass
