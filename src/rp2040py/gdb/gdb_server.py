"""RP2040 GDB Server

Copyright (C) 2021, Uri Shaked
"""

from __future__ import annotations

import re
import struct
from typing import TYPE_CHECKING

from rp2040py.cortex_m0_core import SYSM_CONTROL, SYSM_MSP, SYSM_PRIMASK, SYSM_PSP
from rp2040py.gdb.gdb_target import IGDBTarget
from rp2040py.gdb.gdb_utils import decode_hex_buf, encode_hex_buf, encode_hex_byte, encode_hex_uint32, gdb_message
from rp2040py.utils.logging import ConsoleLogger, Logger, LogLevel

if TYPE_CHECKING:
    from rp2040py.gdb.gdb_connection import GDBConnection

__all__ = ()

STOP_REPLY_SIGINT = "S02"
STOP_REPLY_TRAP = "S05"

# string value: armv6m-none-unknown-eabi
LLDB_TRIPLE = "61726d76366d2d6e6f6e652d756e6b6e6f776e2d65616269"

REGISTERS = [
    "name:r0;bitsize:32;offset:0;encoding:int;format:hex;set:General Purpose Registers;generic:arg1;gcc:0;dwarf:0;",
    "name:r1;bitsize:32;offset:4;encoding:int;format:hex;set:General Purpose Registers;generic:arg2;gcc:1;dwarf:1;",
    "name:r2;bitsize:32;offset:8;encoding:int;format:hex;set:General Purpose Registers;generic:arg3;gcc:2;dwarf:2;",
    "name:r3;bitsize:32;offset:12;encoding:int;format:hex;set:General Purpose Registers;generic:arg4;gcc:3;dwarf:3;",
    "name:r4;bitsize:32;offset:16;encoding:int;format:hex;set:General Purpose Registers;gcc:4;dwarf:4;",
    "name:r5;bitsize:32;offset:20;encoding:int;format:hex;set:General Purpose Registers;gcc:5;dwarf:5;",
    "name:r6;bitsize:32;offset:24;encoding:int;format:hex;set:General Purpose Registers;gcc:6;dwarf:6;",
    "name:r7;bitsize:32;offset:28;encoding:int;format:hex;set:General Purpose Registers;gcc:7;dwarf:7;",
    "name:r8;bitsize:32;offset:32;encoding:int;format:hex;set:General Purpose Registers;gcc:8;dwarf:8;",
    "name:r9;bitsize:32;offset:36;encoding:int;format:hex;set:General Purpose Registers;gcc:9;dwarf:9;",
    "name:r10;bitsize:32;offset:40;encoding:int;format:hex;set:General Purpose Registers;gcc:10;dwarf:10;",
    "name:r11;bitsize:32;offset:44;encoding:int;format:hex;set:General Purpose Registers;generic:fp;gcc:11;dwarf:11;",
    "name:r12;bitsize:32;offset:48;encoding:int;format:hex;set:General Purpose Registers;gcc:12;dwarf:12;",
    "name:sp;bitsize:32;offset:52;encoding:int;format:hex;set:General Purpose Registers;generic:sp;alt-name:r13;gcc:13;dwarf:13;",
    "name:lr;bitsize:32;offset:56;encoding:int;format:hex;set:General Purpose Registers;generic:ra;alt-name:r14;gcc:14;dwarf:14;",
    "name:pc;bitsize:32;offset:60;encoding:int;format:hex;set:General Purpose Registers;generic:pc;alt-name:r15;gcc:15;dwarf:15;",
    "name:cpsr;bitsize:32;offset:64;encoding:int;format:hex;set:General Purpose Registers;generic:flags;alt-name:psr;gcc:16;dwarf:16;",
]

TARGET_XML = """<?xml version="1.0"?>
<!DOCTYPE target SYSTEM "gdb-target.dtd">
<target version="1.0">
<architecture>arm</architecture>
<feature name="org.gnu.gdb.arm.m-profile">
<reg name="r0" bitsize="32" regnum="0" save-restore="yes" type="int" group="general"/>
<reg name="r1" bitsize="32" regnum="1" save-restore="yes" type="int" group="general"/>
<reg name="r2" bitsize="32" regnum="2" save-restore="yes" type="int" group="general"/>
<reg name="r3" bitsize="32" regnum="3" save-restore="yes" type="int" group="general"/>
<reg name="r4" bitsize="32" regnum="4" save-restore="yes" type="int" group="general"/>
<reg name="r5" bitsize="32" regnum="5" save-restore="yes" type="int" group="general"/>
<reg name="r6" bitsize="32" regnum="6" save-restore="yes" type="int" group="general"/>
<reg name="r7" bitsize="32" regnum="7" save-restore="yes" type="int" group="general"/>
<reg name="r8" bitsize="32" regnum="8" save-restore="yes" type="int" group="general"/>
<reg name="r9" bitsize="32" regnum="9" save-restore="yes" type="int" group="general"/>
<reg name="r10" bitsize="32" regnum="10" save-restore="yes" type="int" group="general"/>
<reg name="r11" bitsize="32" regnum="11" save-restore="yes" type="int" group="general"/>
<reg name="r12" bitsize="32" regnum="12" save-restore="yes" type="int" group="general"/>
<reg name="sp" bitsize="32" regnum="13" save-restore="yes" type="data_ptr" group="general"/>
<reg name="lr" bitsize="32" regnum="14" save-restore="yes" type="int" group="general"/>
<reg name="pc" bitsize="32" regnum="15" save-restore="yes" type="code_ptr" group="general"/>
<reg name="xPSR" bitsize="32" regnum="16" save-restore="yes" type="int" group="general"/>
</feature>
<feature name="org.gnu.gdb.arm.m-system">
<reg name="msp" bitsize="32" regnum="17" save-restore="yes" type="data_ptr" group="system"/>
<reg name="psp" bitsize="32" regnum="18" save-restore="yes" type="data_ptr" group="system"/>
<reg name="primask" bitsize="1" regnum="19" save-restore="yes" type="int8" group="system"/>
<reg name="basepri" bitsize="8" regnum="20" save-restore="yes" type="int8" group="system"/>
<reg name="faultmask" bitsize="1" regnum="21" save-restore="yes" type="int8" group="system"/>
<reg name="control" bitsize="2" regnum="22" save-restore="yes" type="int8" group="system"/>
</feature>
</target>"""

LOG_NAME = "GDBServer"


class GDBServer:
    def __init__(self, target: IGDBTarget):
        self.target = target
        self.logger: Logger = ConsoleLogger(LogLevel.WARN, True)
        self._connections: set[GDBConnection] = set()

    def process_gdb_message(self, cmd: str) -> str | None:
        rp2040 = self.target.rp2040
        core = rp2040.core
        if cmd == "Hg0":
            return gdb_message("OK")

        head = cmd[0]

        if head == "?":
            return gdb_message(STOP_REPLY_TRAP)

        if head == "q":
            # Query things
            if cmd.startswith("qSupported:"):
                return gdb_message("PacketSize=4000;vContSupported+;qXfer:features:read+")
            if cmd == "qAttached":
                return gdb_message("1")
            if cmd.startswith("qXfer:features:read:target.xml"):
                return gdb_message("l" + TARGET_XML)
            if cmd.startswith("qRegisterInfo"):
                index = int(cmd[13:], 16)
                register = REGISTERS[index] if 0 <= index < len(REGISTERS) else None
                if register:
                    return gdb_message(register)
                return gdb_message("E45")
            if cmd == "qHostInfo":
                return gdb_message(f"triple:{LLDB_TRIPLE};endian:little;ptrsize:4;")
            if cmd == "qProcessInfo":
                return gdb_message("pid:1;endian:little;ptrsize:4;")
            return gdb_message("")

        if head == "v":
            if cmd == "vCont?":
                return gdb_message("vCont;c;C;s;S")
            if cmd.startswith("vCont;c"):
                if not self.target.executing:
                    self.target.execute()
                return None
            if cmd.startswith("vCont;s"):
                rp2040.step()
                register_status = []
                for i in range(17):
                    value = core.x_psr if i == 16 else core.registers[i]
                    register_status.append(f"{encode_hex_byte(i)}:{encode_hex_uint32(value)}")
                return gdb_message(f"T05{';'.join(register_status)};reason:trace;")
            return gdb_message("")

        if head == "c":
            if not self.target.executing:
                self.target.execute()
            return gdb_message("OK")

        if head == "g":
            # Read registers
            values = [core.registers[i] for i in range(16)]
            values.append(core.x_psr)
            buf = struct.pack("<17I", *(v & 0xFFFFFFFF for v in values))
            return gdb_message(encode_hex_buf(buf))

        if head == "p":
            # Read register
            register_index = int(cmd[1:], 16)
            if 0 <= register_index <= 15:
                return gdb_message(encode_hex_uint32(core.registers[register_index]))

            def special_register(sysm: int) -> str:
                return gdb_message(encode_hex_uint32(core.read_special_register(sysm)))

            if register_index == 0x10:
                return gdb_message(encode_hex_uint32(core.x_psr))
            if register_index == 0x11:
                return special_register(SYSM_MSP)
            if register_index == 0x12:
                return special_register(SYSM_PSP)
            if register_index == 0x13:
                return special_register(SYSM_PRIMASK)
            if register_index == 0x14:
                self.logger.warning(LOG_NAME, "TODO BASEPRI")
                return gdb_message(encode_hex_uint32(0))  # TODO BASEPRI
            if register_index == 0x15:
                self.logger.warning(LOG_NAME, "TODO faultmask")
                return gdb_message(encode_hex_uint32(0))  # TODO faultmask
            if register_index == 0x16:
                return special_register(SYSM_CONTROL)
            return gdb_message("")

        if head == "P":
            # Write register
            params = cmd[1:].split("=")
            register_index = int(params[0], 16)
            register_value = params[1].strip()
            register_bytes = 1 if register_index > 0x12 else 4
            decoded_value = decode_hex_buf(register_value)
            if register_index < 0 or register_index > 0x16 or len(decoded_value) != register_bytes:
                return gdb_message("E00")
            value_buffer = bytearray(4)
            value_buffer[: len(decoded_value[:4])] = decoded_value[:4]
            value = struct.unpack("<I", bytes(value_buffer))[0]
            if register_index == 0x10:
                core.x_psr = value
            elif register_index == 0x11:
                core.write_special_register(SYSM_MSP, value)
            elif register_index == 0x12:
                core.write_special_register(SYSM_PSP, value)
            elif register_index == 0x13:
                core.write_special_register(SYSM_PRIMASK, value)
            elif register_index == 0x14:
                self.logger.warning(LOG_NAME, "TODO BASEPRI")  # TODO BASEPRI
            elif register_index == 0x15:
                self.logger.warning(LOG_NAME, "TODO faultmask")  # TODO faultmask
            elif register_index == 0x16:
                core.write_special_register(SYSM_CONTROL, value)
            else:
                core.registers[register_index] = value
            return gdb_message("OK")

        if head == "m":
            # Read memory
            params = cmd[1:].split(",")
            address = int(params[0], 16)
            length = int(params[1], 16)
            result = ""
            for i in range(length):
                result += encode_hex_byte(rp2040.read_uint8(address + i))
            return gdb_message(result)

        if head == "M":
            # Write memory
            params = re.split(r"[,:]", cmd[1:])
            address = int(params[0], 16)
            length = int(params[1], 16)
            data = decode_hex_buf(params[2][: length * 2])
            for i, byte_value in enumerate(data):
                self.debug(f"Write {byte_value:x} to {address + i:x}")
                rp2040.write_uint8(address + i, byte_value)
            return gdb_message("OK")

        return gdb_message("")

    def add_connection(self, connection: GDBConnection) -> None:
        rp2040 = self.target.rp2040
        self._connections.add(connection)

        def _on_break(code: int) -> None:
            self.target.stop()
            rp2040.core.pc -= rp2040.core.break_rewind
            for conn in self._connections:
                conn.on_breakpoint()

        rp2040.on_break = _on_break

    def remove_connection(self, connection: GDBConnection) -> None:
        self._connections.discard(connection)

    def debug(self, msg: str) -> None:
        self.logger.debug(LOG_NAME, msg)

    def info(self, msg: str) -> None:
        self.logger.info(LOG_NAME, msg)

    def warn(self, msg: str) -> None:
        self.logger.warning(LOG_NAME, msg)

    def error(self, msg: str) -> None:
        self.logger.error(LOG_NAME, msg)
