from rp2040py.cortex_m0_core import SYSM_CONTROL, SYSM_MSP, SYSM_PRIMASK, SYSM_PSP
from rp2040py.rp2040 import RP2040
from rp2040py.utils.bit import s32, u32

from .cortex_test_driver import CortexRegisters, CortexRegistersPartial


class RP2040TestDriver:
    def __init__(self, rp2040: RP2040):
        self.rp2040 = rp2040

    def init(self) -> None:
        pass  # this page intentionally left blank!

    def tear_down(self) -> None:
        self.rp2040.pio[0].stop()
        self.rp2040.pio[1].stop()

    def set_pc(self, pc_value: int) -> None:
        self.rp2040.core.pc = pc_value

    def write_uint8(self, address: int, value: int) -> None:
        self.rp2040.write_uint8(address, value)

    def write_uint16(self, address: int, value: int) -> None:
        self.rp2040.write_uint16(address, value)

    def write_uint32(self, address: int, value: int) -> None:
        self.rp2040.write_uint32(address, value)

    def set_registers(self, registers: CortexRegistersPartial) -> None:
        core = self.rp2040.core
        for key, value in registers.items():
            if key == "r0":
                core.registers[0] = value
            elif key == "r1":
                core.registers[1] = value
            elif key == "r2":
                core.registers[2] = value
            elif key == "r3":
                core.registers[3] = value
            elif key == "r4":
                core.registers[4] = value
            elif key == "r5":
                core.registers[5] = value
            elif key == "r6":
                core.registers[6] = value
            elif key == "r7":
                core.registers[7] = value
            elif key == "r8":
                core.registers[8] = value
            elif key == "r9":
                core.registers[9] = value
            elif key == "r10":
                core.registers[10] = value
            elif key == "r11":
                core.registers[11] = value
            elif key == "r12":
                core.registers[12] = value
            elif key == "sp":
                core.registers[13] = value
            elif key == "lr":
                core.registers[14] = value
            elif key == "pc":
                core.registers[15] = value
            elif key == "x_psr":
                core.x_psr = value
            elif key == "msp":
                core.write_special_register(SYSM_MSP, value)
            elif key == "psp":
                core.write_special_register(SYSM_PSP, value)
            elif key == "primask":
                core.write_special_register(SYSM_PRIMASK, value)
            elif key == "control":
                core.write_special_register(SYSM_CONTROL, value)
            elif key == "n":
                core.n = bool(value)
            elif key == "z":
                core.z = bool(value)
            elif key == "c":
                core.c = bool(value)
            elif key == "v":
                core.v = bool(value)

    def single_step(self) -> None:
        self.rp2040.step()

    def read_registers(self) -> CortexRegisters:
        core = self.rp2040.core
        registers = core.registers
        x_psr = core.x_psr
        return CortexRegisters(
            r0=registers[0],
            r1=registers[1],
            r2=registers[2],
            r3=registers[3],
            r4=registers[4],
            r5=registers[5],
            r6=registers[6],
            r7=registers[7],
            r8=registers[8],
            r9=registers[9],
            r10=registers[10],
            r11=registers[11],
            r12=registers[12],
            sp=registers[13],
            lr=registers[14],
            pc=registers[15],
            x_psr=x_psr,
            msp=core.read_special_register(SYSM_MSP),
            psp=core.read_special_register(SYSM_PSP),
            primask=core.read_special_register(SYSM_PRIMASK),
            control=core.read_special_register(SYSM_CONTROL),
            n=bool(x_psr & 0x80000000),
            z=bool(x_psr & 0x40000000),
            c=bool(x_psr & 0x20000000),
            v=bool(x_psr & 0x10000000),
        )

    def read_uint8(self, address: int) -> int:
        return self.rp2040.read_uint8(address)

    def read_uint16(self, address: int) -> int:
        return self.rp2040.read_uint16(address)

    def read_uint32(self, address: int) -> int:
        return u32(self.rp2040.read_uint32(address))

    def read_int32(self, address: int) -> int:
        return s32(self.rp2040.read_uint32(address))
