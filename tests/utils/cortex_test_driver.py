from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass
class CortexRegisters:
    r0: int
    r1: int
    r2: int
    r3: int
    r4: int
    r5: int
    r6: int
    r7: int
    r8: int
    r9: int
    r10: int
    r11: int
    r12: int
    sp: int
    lr: int
    pc: int
    x_psr: int
    msp: int
    psp: int
    primask: int
    control: int

    n: bool
    z: bool
    c: bool
    v: bool


# A partial set of CortexRegisters fields, keyed by field name (mirrors TS's Partial<ICortexRegisters>)
CortexRegistersPartial = Mapping[str, int | bool]


class ICortexTestDriver(Protocol):
    def init(self) -> None: ...
    def set_pc(self, pc_value: int) -> None: ...
    def write_uint8(self, address: int, value: int) -> None: ...
    def write_uint16(self, address: int, value: int) -> None: ...
    def write_uint32(self, address: int, value: int) -> None: ...
    def set_registers(self, registers: CortexRegistersPartial) -> None: ...
    def single_step(self) -> None: ...
    def read_registers(self) -> CortexRegisters: ...
    def read_uint8(self, address: int) -> int: ...
    def read_uint16(self, address: int) -> int: ...
    def read_uint32(self, address: int) -> int: ...
    def read_int32(self, address: int) -> int: ...
    def tear_down(self) -> None: ...
