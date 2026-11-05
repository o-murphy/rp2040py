"""`ICortexTestDriver` over the wasm module of docs/records/0103-wasm-backend.md: the instruction, SIO and PIO tests of `create_test_driver()` run against the C++ core inside a WebAssembly engine
(`RP2040PY_TEST_DRIVER=wasm`), the way upstream rp2040js runs the same specs against a GDB server (`TEST_GDB_SERVER`).

It reaches the chip only through the module's exports: the bus (`rp2040_read*` / `rp2040_write*`), the registers and the special registers, and `rp2040_step`. A test that needs the chip *object*
(`cpu.rp2040`, e.g. `cpu.rp2040.core.waiting` or `cpu.rp2040.pio[0]`) is skipped: that is a part of the in-process API the module does not export, and the list of those skips is the result of the
experiment (docs/records/0099-wasm-drop-in-for-rp2040js.md, step 2a).
"""

import tempfile
from pathlib import Path

import pytest

from . import wasm_core
from .cortex_test_driver import CortexRegisters, CortexRegistersPartial

SYSM_XPSR, SYSM_MSP, SYSM_PSP, SYSM_PRIMASK, SYSM_CONTROL = 3, 8, 9, 16, 20
_REGISTER_INDEX = {f"r{i}": i for i in range(13)} | {"sp": 13, "lr": 14, "pc": 15}
_SPECIAL = {"x_psr": SYSM_XPSR, "msp": SYSM_MSP, "psp": SYSM_PSP, "primask": SYSM_PRIMASK, "control": SYSM_CONTROL}
_FLAG_BIT = {"n": 31, "z": 30, "c": 29, "v": 28}
_MASK = 0xFFFFFFFF

_exports = None  # one module per process: a test starts from `rp2040_boot()`, a power-on reset, which is cheaper than a new engine


def _module_exports():
    global _exports
    if _exports is None:
        with tempfile.TemporaryDirectory() as directory:
            _, _exports = wasm_core.instantiate(wasm_core.wasm_bytes(Path(directory)))
    return _exports


class WasmTestDriver:
    def __init__(self) -> None:
        self._x = _module_exports()

    def init(self) -> None:
        assert self._x.rp2040_boot() == 0

    def tear_down(self) -> None:
        pass

    @property
    def rp2040(self):
        pytest.skip("needs the in-process chip object, which the wasm module does not export")

    def set_pc(self, pc_value: int) -> None:
        self._x.rp2040_set_register(15, pc_value & _MASK)

    def write_uint8(self, address: int, value: int) -> None:
        self._x.rp2040_write8(address & _MASK, value & 0xFF)

    def write_uint16(self, address: int, value: int) -> None:
        self._x.rp2040_write16(address & _MASK, value & 0xFFFF)

    def write_uint32(self, address: int, value: int) -> None:
        self._x.rp2040_write32(address & _MASK, value & _MASK)

    def set_registers(self, registers: CortexRegistersPartial) -> None:
        x = self._x
        for key, value in registers.items():
            if key in _REGISTER_INDEX:
                x.rp2040_set_register(_REGISTER_INDEX[key], int(value) & _MASK)
            elif key in _SPECIAL:
                x.rp2040_core_set_special(0, _SPECIAL[key], int(value) & _MASK)
            elif key in _FLAG_BIT:
                x_psr = x.rp2040_core_special(0, SYSM_XPSR) & _MASK
                bit = 1 << _FLAG_BIT[key]
                x.rp2040_core_set_special(0, SYSM_XPSR, (x_psr | bit) if value else (x_psr & ~bit & _MASK))

    def single_step(self) -> None:
        assert self._x.rp2040_step() >= 0, "the core faulted"

    def read_registers(self) -> CortexRegisters:
        x = self._x
        r = [x.rp2040_register(i) & _MASK for i in range(16)]
        x_psr = x.rp2040_core_special(0, SYSM_XPSR) & _MASK
        return CortexRegisters(
            *r[:13],
            sp=r[13],
            lr=r[14],
            pc=r[15],
            x_psr=x_psr,
            msp=x.rp2040_core_special(0, SYSM_MSP) & _MASK,
            psp=x.rp2040_core_special(0, SYSM_PSP) & _MASK,
            primask=x.rp2040_core_special(0, SYSM_PRIMASK) & _MASK,
            control=x.rp2040_core_special(0, SYSM_CONTROL) & _MASK,
            n=bool(x_psr & 0x80000000),
            z=bool(x_psr & 0x40000000),
            c=bool(x_psr & 0x20000000),
            v=bool(x_psr & 0x10000000),
        )

    def read_uint8(self, address: int) -> int:
        return self._x.rp2040_read8(address & _MASK) & 0xFF

    def read_uint16(self, address: int) -> int:
        return self._x.rp2040_read16(address & _MASK) & 0xFFFF

    def read_uint32(self, address: int) -> int:
        return self._x.rp2040_read32(address & _MASK) & _MASK

    def read_int32(self, address: int) -> int:
        value = self.read_uint32(address)
        return value - (1 << 32) if value & 0x80000000 else value
