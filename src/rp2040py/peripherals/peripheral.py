import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "BasePeripheral",
    "Peripheral",
    "UnimplementedPeripheral",
    "atomic_update",
)

# Module-level, not the project's own Logger Protocol/ConsoleLogger: atomic_update() is a bare
# utility function with no peripheral/RP2040 instance in scope to hang a component-named logger
# call off of - this is the plain stdlib case the Logger Protocol was never meant to cover.
_logger = logging.getLogger(__name__)

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
    _logger.warning("Atomic update called with invalid writeType %s", atomic_type)
    return new_value


class Peripheral(Protocol):
    """What `RP2040.peripherals` holds: a memory-mapped block the bus can address, and reset.

    The three bus methods are how `RP2040.read_uint32()`/`write_uint32()` dispatch to it. `reset()`
    is here because being resettable is as much part of being a hardware block as being addressable
    - every real peripheral has a reset, and `RP2040.reset()` is entitled to call it on anything in
    that dict without narrowing the type first (docs/records/0089-one-reset-for-every-trigger.md,
    Phase 5).

    Every implementer in this tree already satisfies it: the 26 classes deriving from
    `BasePeripheral` inherit its documented default, and `native/_pio.pyx`'s `RPPIO` - the one
    implementer that structurally *cannot* inherit it, being a `cdef class` - has its own. Note
    what this does and does not buy: it makes the contract complete and checkable, but it does not
    *find* a missing reset, because `BasePeripheral`'s default satisfies it trivially. The blocks
    that still need a real one are named in `RP2040.reset()`'s own docstring, which is where that
    gap is tracked.
    """

    def read_uint32(self, offset: int) -> int: ...
    def write_uint32(self, offset: int, value: int) -> None: ...
    def write_uint32_atomic(self, offset: int, value: int, atomic_type: int) -> None: ...
    def reset(self) -> None: ...


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

    def reset(self) -> None:
        """Return this block's registers to their power-on values, in place.

        The default does nothing, which is correct for a block whose registers are read-only chip
        identity (`SYSINFO`, `TBMAN`) - there is no state to clear. **Any block that holds state
        must override it**, and the ones that still do not are named as a known gap in
        `RP2040.reset()`'s own docstring rather than left to be discovered: a silently-inherited
        no-op is exactly how a missing reset stops being visible
        (docs/records/0089-one-reset-for-every-trigger.md, Phase 5).

        Declared on the `Peripheral` Protocol above as well, so a caller holding one of those does
        not have to narrow the type to reset it. This class supplies the default; a `Peripheral`
        that is not a `BasePeripheral` (`native/_pio.pyx`'s `RPPIO`, a `cdef class`) writes its own.

        What a reset must *not* touch is the same everywhere in this phase: callbacks, GPIO
        listeners, DREQ identity and analog inputs are wiring, not state - a chip reset does not
        unsolder anything.
        """

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
