from unittest.mock import Mock

import pytest
from utils.create_test_driver import create_test_driver
from utils.rp2040_test_driver import RP2040TestDriver

from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.utils.assembler import (
    opcode_adcs,
    opcode_add_reg,
    opcode_add_sp2,
    opcode_add_sp_plus_imm,
    opcode_adds1,
    opcode_adds2,
    opcode_adds_reg,
    opcode_adr,
    opcode_ands,
    opcode_asrs,
    opcode_asrs_reg,
    opcode_b_t1,
    opcode_b_t2,
    opcode_bics,
    opcode_bl,
    opcode_blx,
    opcode_bx,
    opcode_cmn,
    opcode_cmp_imm,
    opcode_cmp_reg_t1,
    opcode_cmp_reg_t2,
    opcode_dmb_sy,
    opcode_dsb_sy,
    opcode_eors,
    opcode_isb_sy,
    opcode_ldmia,
    opcode_ldr_imm,
    opcode_ldr_lit,
    opcode_ldr_reg,
    opcode_ldr_sp,
    opcode_ldrb,
    opcode_ldrb_reg,
    opcode_ldrh,
    opcode_ldrh_reg,
    opcode_ldrsb,
    opcode_ldrsh,
    opcode_lsls_imm,
    opcode_lsls_reg,
    opcode_lsrs,
    opcode_lsrs_reg,
    opcode_mov,
    opcode_movs,
    opcode_movs_reg,
    opcode_mrs,
    opcode_msr,
    opcode_muls,
    opcode_mvns,
    opcode_nop,
    opcode_orrs,
    opcode_pop,
    opcode_push,
    opcode_rev,
    opcode_rev16,
    opcode_revsh,
    opcode_ror,
    opcode_rsbs,
    opcode_sbcs,
    opcode_stmia,
    opcode_str,
    opcode_str_reg,
    opcode_str_sp,
    opcode_strb,
    opcode_strb_reg,
    opcode_strh,
    opcode_strh_reg,
    opcode_sub_sp,
    opcode_subs1,
    opcode_subs2,
    opcode_subs_reg,
    opcode_svc,
    opcode_sxtb,
    opcode_sxth,
    opcode_tst,
    opcode_udf,
    opcode_udf2,
    opcode_uxtb,
    opcode_uxth,
    opcode_wfi,
    opcode_yield,
)
from rp2040py.utils.bit import s32, u32

r0 = 0
r1 = 1
r2 = 2
r3 = 3
r4 = 4
r5 = 5
r6 = 6
r7 = 7
r8 = 8
r11 = 11
r12 = 12
ip = 12
sp = 13
lr = 14
pc = 15

VTOR = 0xE000ED08
EXC_SVCALL = 11


@pytest.fixture
def cpu():
    driver = create_test_driver()
    driver.init()
    yield driver
    driver.tear_down()


def test_should_execute_adcs_r5_r4_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adcs(r5, r4))
    cpu.set_registers({"r4": 55, "r5": 66, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 122
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_adcs_r5_r4_instruction_and_set_negative_overflow_flags(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adcs(r5, r4))
    cpu.set_registers({"r4": 0x7FFFFFFF, "r5": 0, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0x80000000
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is True


def test_should_not_set_the_overflow_flag_when_executing_adcs_r3_r2_adding_0_to_0_with_carry(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adcs(r3, r2))
    cpu.set_registers({"r2": 0, "r3": 0, "c": True, "z": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 1
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_set_the_zero_carry_and_overflow_flag_when_executing_adcs_r0_r0_adding_0x80000000_to_0x80000000(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adcs(r0, r0))
    cpu.set_registers({"r0": 0x80000000, "c": False})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0
    assert registers.n is False
    assert registers.z is True
    assert registers.c is True
    assert registers.v is True


def test_should_execute_a_add_sp_0x10_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": 0x10000040})
    cpu.write_uint16(0x20000000, opcode_add_sp2(0x10))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x10000050


def test_should_execute_a_add_r1_sp_4_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": 0x54})
    cpu.write_uint16(0x20000000, opcode_add_sp_plus_imm(r1, 0x10))
    cpu.set_registers({"r1": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x54
    assert registers.r1 == 0x64


def test_should_execute_adds_r1_r2_3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adds1(r1, r2, 3))
    cpu.set_registers({"r2": 2})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 5
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_adds_r1_1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adds2(r1, 1))
    cpu.set_registers({"r1": 0xFFFFFFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0
    assert registers.n is False
    assert registers.z is True
    assert registers.c is True
    assert registers.v is False


def test_should_execute_adds_r1_r2_r7_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adds_reg(r1, r2, r7))
    cpu.set_registers({"r2": 2})
    cpu.set_registers({"r7": 27})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 29
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_adds_r4_r4_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adds_reg(r4, r4, r2))
    cpu.set_registers({"r2": 0x74BC8000, "r4": 0x43740000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r4 == 0xB8308000
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is True


def test_should_execute_adds_r1_r1_r1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adds_reg(r1, r1, r1))
    cpu.set_registers({"r1": 0xBF8D1424, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0x7F1A2848
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is True


def test_should_execute_add_r1_ip_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_add_reg(r1, ip))
    cpu.set_registers({"r1": 66, "r12": 44})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 110
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_not_update_the_flags_following_add_r3_r12_instruction_encoding_t2(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_add_reg(r3, r12))
    cpu.set_registers({"r3": 0x00002000, "r12": 0xFFFFE000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_add_sp_r8_instruction_and_not_update_the_flags(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_add_reg(sp, r8))
    cpu.set_registers({"sp": 0x20030000, "z": True, "r8": 0x13})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x20030010
    assert registers.z is True


def test_should_execute_add_pc_r8_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_add_reg(pc, r8))
    cpu.set_registers({"r8": 0x11})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000014


def test_should_execute_add_r7_pc_instruction_with_correct_pc_4_value_word_aligned(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_add_reg(r7, pc))
    cpu.set_registers({"r7": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r7 == 0x20000004


def test_should_execute_add_r7_pc_instruction_with_correct_pc_4_value_half_word_aligned(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_nop())
    cpu.write_uint16(0x20000002, opcode_add_reg(r7, pc))
    cpu.set_registers({"r7": 0})
    cpu.single_step()
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r7 == 0x20000006


def test_should_execute_adr_r4_0x50_instruction_and_set_the_overflow_flag_correctly(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_adr(r4, 0x50))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r4 == 0x20000054


def test_should_execute_ands_r5_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ands(r5, r0))
    cpu.set_registers({"r5": 0xFFFF0000})
    cpu.set_registers({"r0": 0xF00FFFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0xF00F0000
    assert registers.n is True
    assert registers.z is False


def test_should_execute_an_asrs_r3_r2_31_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs(r3, r2, 31))
    cpu.set_registers({"r2": 0x80000000, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFFFFFFFF
    assert registers.pc == 0x20000002
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False


def test_should_correctly_update_the_carry_flags_when_executing_asrs_r3_r2_32_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs(r3, r2, 0))
    cpu.set_registers({"r2": 0x80000000, "c": False})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFFFFFFFF
    assert registers.pc == 0x20000002
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_an_asrs_r3_r4_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs_reg(r3, r4))
    cpu.set_registers({"r3": 0x80000040})
    cpu.set_registers({"r4": 0xFF500007})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFF000000
    assert registers.pc == 0x20000002
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_an_asrs_r3_r4_instruction_2(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs_reg(r3, r4))
    cpu.set_registers({"r3": 0x40000040, "r4": 50, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0
    assert registers.pc == 0x20000002
    assert registers.n is False
    assert registers.z is True
    assert registers.c is False


def test_should_execute_an_asrs_r3_r4_instruction_3(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs_reg(r3, r4))
    cpu.set_registers({"r3": 0x40000040, "r4": 31, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0
    assert registers.pc == 0x20000002
    assert registers.n is False
    assert registers.z is True
    assert registers.c is True


def test_should_execute_an_asrs_r3_r4_instruction_4(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs_reg(r3, r4))
    cpu.set_registers({"r3": 0x80000040, "r4": 50, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFFFFFFFF
    assert registers.pc == 0x20000002
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_an_asrs_r3_r4_instruction_5(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_asrs_reg(r3, r4))
    cpu.set_registers({"r3": 0x80000040, "r4": 0, "c": True})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x80000040
    assert registers.pc == 0x20000002
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_bics_r0_r3_correctly(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"r0": 0xFF})
    cpu.set_registers({"r3": 0x0F})
    cpu.write_uint16(0x20000000, opcode_bics(r0, r3))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0xF0
    assert registers.n is False
    assert registers.z is False


def test_should_execute_bics_r0_r3_instruction_and_set_the_negative_flag_correctly(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"r0": 0xFFFFFFFF})
    cpu.set_registers({"r3": 0x0000FFFF})
    cpu.write_uint16(0x20000000, opcode_bics(r0, r3))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0xFFFF0000
    assert registers.n is True
    assert registers.z is False


def test_should_execute_bl_0x34_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_bl(0x34))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000038
    assert registers.lr == 0x20000005


def test_should_execute_bl_0x10_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_bl(-0x10))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000004 - 0x10
    assert registers.lr == 0x20000005


def test_should_execute_bl_3242_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_bl(-3242))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000004 - 3242
    assert registers.lr == 0x20000005


def test_should_execute_blx_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"r3": 0x20000201})
    cpu.write_uint32(0x20000000, opcode_blx(r3))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000200
    assert registers.lr == 0x20000003


def test_should_execute_a_b_n_20_instruction(cpu):
    cpu.set_pc(0x20000000 + 9 * 2)
    cpu.write_uint16(0x20000000 + 9 * 2, opcode_b_t2(0xFEC))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002


def test_should_execute_a_bne_n_6_instruction(cpu):
    cpu.set_pc(0x20000000 + 9 * 2)
    cpu.set_registers({"z": False})
    cpu.write_uint16(0x20000000 + 9 * 2, opcode_b_t1(1, 0x1F8))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x2000000E


def test_should_execute_bx_lr_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"lr": 0x10000200})
    cpu.write_uint32(0x20000000, opcode_bx(lr))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x10000200


def test_should_execute_an_cmn_r5_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmn(r7, r2))
    cpu.set_registers({"r2": 1})
    cpu.set_registers({"r7": -2})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 1
    assert registers.r7 == u32(-2)
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_an_cmp_r5_66_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_imm(r5, 66))
    cpu.set_registers({"r5": 60})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_correctly_set_carry_flag_when_executing_cmp_r0_0(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_imm(r0, 0))
    cpu.set_registers({"r0": 0x80010133})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_an_cmp_r5_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t1(r5, r0))
    cpu.set_registers({"r5": 60})
    cpu.set_registers({"r0": 56})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_an_cmp_r2_r0_instruction_and_not_set_any_flags_when_r0_0xb71b0000_and_r2_0x00b71b00(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t1(r2, r0))
    cpu.set_registers({"r0": 0xB71B0000, "r2": 0x00B71B00})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is False
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_correctly_set_carry_flag_when_executing_cmp_r11_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t2(r11, r3))
    cpu.set_registers({"r3": 0x00000008, "r11": 0xFFFFFFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_an_cmp_ip_r6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t2(ip, r6))
    cpu.set_registers({"r6": 56, "r12": 60})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_set_flags_n_c_when_executing_cmp_r11_r3_instruction_when_r3_0_and_r11_0x80000000(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t2(r11, r3))
    cpu.set_registers({"r3": 0, "r11": 0x80000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_set_flags_n_v_when_executing_cmp_r3_r7_instruction_when_r3_0_and_r7_0x80000000(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t1(r3, r7))
    cpu.set_registers({"r3": 0, "r7": 0x80000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is True


def test_should_set_flags_n_v_when_executing_cmp_r11_r3_instruction_when_r3_0x80000000_and_r11_0(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t2(r11, r3))
    cpu.set_registers({"r3": 0x80000000, "r11": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is True


def test_should_set_flags_n_c_when_executing_cmp_r3_r7_instruction_when_r3_0x80000000_and_r7_0(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_cmp_reg_t1(r3, r7))
    cpu.set_registers({"r3": 0x80000000, "r7": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_correctly_decode_a_dmb_sy_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_dmb_sy())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000004


def test_should_correctly_decode_a_dsb_sy_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_dsb_sy())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000004


def test_should_execute_an_eors_r1_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_eors(r1, r3))
    cpu.set_registers({"r1": 0xF0F0F0F0})
    cpu.set_registers({"r3": 0x08FF3007})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0xF80FC0F7
    assert registers.n is True
    assert registers.z is False


def test_should_correctly_decode_a_isb_sy_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_isb_sy())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000004


def test_should_execute_a_mov_r3_r8_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mov(r3, r8))
    cpu.set_registers({"r8": 55})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 55


def test_should_execute_a_mov_r3_pc_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mov(r3, pc))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x20000004


def test_should_execute_a_mov_sp_r8_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mov(r3, r8))
    cpu.set_registers({"r8": 55})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 55


def test_should_execute_a_muls_r0_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_muls(r0, r2))
    cpu.set_registers({"r0": 5})
    cpu.set_registers({"r2": 1000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 5000000
    assert registers.n is False
    assert registers.z is False


def test_should_execute_a_muls_instruction_with_large_32_bit_numbers_and_produce_the_correct_result(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_muls(r0, r2))
    cpu.set_registers({"r0": 2654435769})
    cpu.set_registers({"r2": 340573321})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 1


def test_should_execute_a_muls_r0_r2_instruction_and_set_the_z_flag_when_the_result_is_zero(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_muls(r0, r2))
    cpu.set_registers({"r0": 0})
    cpu.set_registers({"r2": 1000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0
    assert registers.n is False
    assert registers.z is True


def test_should_execute_a_muls_r0_r2_instruction_and_set_the_n_flag_when_the_result_is_negative(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_muls(r0, r2))
    cpu.set_registers({"r0": -1})
    cpu.set_registers({"r2": 1000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == u32(-1000000)
    assert registers.n is True
    assert registers.z is False


def test_should_execute_a_mvns_r4_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mvns(r4, r3))
    cpu.set_registers({"r3": 0x11115555})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r4 == 0xEEEEAAAA
    assert registers.z is False
    assert registers.n is True


def test_should_execute_a_nop_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_nop())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002


def test_should_execute_orrs_r5_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_orrs(r5, r0))
    cpu.set_registers({"r5": 0xF00F0000})
    cpu.set_registers({"r0": 0xF000FFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0xF00FFFFF
    assert registers.n is True
    assert registers.z is False


def test_should_execute_a_pop_pc_r4_r5_r6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": RAM_START_ADDRESS + 0xF0})
    cpu.write_uint16(0x20000000, opcode_pop(True, (1 << r4) | (1 << r5) | (1 << r6)))
    cpu.write_uint32(0x200000F0, 0x40)
    cpu.write_uint32(0x200000F4, 0x50)
    cpu.write_uint32(0x200000F8, 0x60)
    cpu.write_uint32(0x200000FC, 0x42)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == RAM_START_ADDRESS + 0x100
    assert registers.r4 == 0x40
    assert registers.r5 == 0x50
    assert registers.r6 == 0x60
    assert registers.pc == 0x42


def test_should_execute_a_push_r4_r5_r6_lr_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": RAM_START_ADDRESS + 0x100})
    cpu.write_uint16(0x20000000, opcode_push(True, (1 << r4) | (1 << r5) | (1 << r6)))
    cpu.set_registers({"r4": 0x40, "r5": 0x50, "r6": 0x60, "lr": 0x42})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == RAM_START_ADDRESS + 0xF0
    assert cpu.read_uint8(0x200000F0) == 0x40
    assert cpu.read_uint8(0x200000F4) == 0x50
    assert cpu.read_uint8(0x200000F8) == 0x60
    assert cpu.read_uint8(0x200000FC) == 0x42


def test_should_execute_a_mrs_r0_ipsr_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_mrs(r0, 5))
    cpu.set_registers({"r0": 55})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0
    assert registers.pc == 0x20000004


def test_should_execute_a_msr_msp_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint32(0x20000000, opcode_msr(8, r0))
    cpu.set_registers({"r0": 0x1234})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x1234
    assert registers.pc == 0x20000004


def test_should_execute_a_movs_r5_128_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_movs(r5, 128))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 128
    assert registers.pc == 0x20000002


def test_should_execute_a_ldmia_r0_r1_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldmia(r0, (1 << r1) | (1 << r2)))
    cpu.set_registers({"r0": 0x20000010})
    cpu.write_uint32(0x20000010, 0xF00DF00D)
    cpu.write_uint32(0x20000014, 0x4242)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002
    assert registers.r0 == 0x20000018
    assert registers.r1 == 0xF00DF00D
    assert registers.r2 == 0x4242


def test_should_execute_a_ldmia_r5_r5_instruction_without_writing_back_the_address_to_r5(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldmia(r5, 1 << r5))
    cpu.set_registers({"r5": 0x20000010})
    cpu.write_uint32(0x20000010, 0xF00DF00D)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002
    assert registers.r5 == 0xF00DF00D


def test_should_execute_an_ldr_r0_pc_148_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldr_lit(r0, 148))
    cpu.write_uint32(0x20000000 + 152, 0x42)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0x42
    assert registers.pc == 0x20000002


def test_should_execute_an_ldr_r3_r2_24_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldr_imm(r3, r2, 24))
    cpu.set_registers({"r2": 0x20000000})
    cpu.write_uint32(0x20000000 + 24, 0x55)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x55


def test_should_execute_an_ldr_r3_sp_12_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": 0x20000000})
    cpu.write_uint16(0x20000000, opcode_ldr_sp(r3, 12))
    cpu.write_uint32(0x20000000 + 12, 0x55)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x55


def test_should_execute_an_ldr_r3_r5_r6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldr_reg(r3, r5, r6))
    cpu.set_registers({"r5": 0x20000000})
    cpu.set_registers({"r6": 0x8})
    cpu.write_uint32(0x20000008, 0xFF554211)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFF554211


def test_should_execute_an_ldrb_r4_r2_5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrb(r4, r2, 5))
    cpu.set_registers({"r2": 0x20000000})
    cpu.write_uint16(0x20000005, 0x7766)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r4 == 0x66


def test_should_execute_an_ldrb_r3_r5_r6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrb_reg(r3, r5, r6))
    cpu.set_registers({"r5": 0x20000000})
    cpu.set_registers({"r6": 0x8})
    cpu.write_uint32(0x20000008, 0xFF554211)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x11


def test_should_execute_an_ldrh_r3_r7_4_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrh(r3, r7, 4))
    cpu.set_registers({"r7": 0x20000000})
    cpu.write_uint32(0x20000004, 0xFFFF7766)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x7766


def test_should_execute_an_ldrh_r3_r7_6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrh(r3, r7, 6))
    cpu.set_registers({"r7": 0x20000000})
    cpu.write_uint32(0x20000004, 0x33447766)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x3344


def test_should_execute_an_ldrh_r3_r5_r6_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrh_reg(r3, r5, r6))
    cpu.set_registers({"r5": 0x20000000, "r6": 0x8})
    cpu.write_uint32(0x20000008, 0xFF554211)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x4211


def test_should_execute_an_ldrsb_r5_r3_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrsb(r5, r3, r5))
    cpu.set_registers({"r3": 0x20000000, "r5": 6})
    cpu.write_uint32(0x20000006, 0x85)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0xFFFFFF85


def test_should_execute_an_ldrsh_r5_r3_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ldrsh(r5, r3, r5))
    cpu.set_registers({"r3": 0x20000000})
    cpu.set_registers({"r5": 6})
    cpu.write_uint16(0x20000006, 0xF055)
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0xFFFFF055


def test_should_execute_a_udf_1_instruction(rp2040_factory):
    break_mock = Mock()
    rp2040 = rp2040_factory()
    rp2040.core.pc = 0x20000000
    rp2040.write_uint16(0x20000000, opcode_udf(0x1))
    rp2040.on_break = break_mock
    rp2040.step()
    assert rp2040.core.pc == 0x20000002
    break_mock.assert_called_with(1)


def test_should_execute_a_udf_w_0_t2_encoding_instruction(rp2040_factory):
    break_mock = Mock()
    rp2040 = rp2040_factory()
    rp2040.core.pc = 0x20000000
    rp2040.write_uint32(0x20000000, opcode_udf2(0))
    rp2040.on_break = break_mock
    rp2040.step()
    assert rp2040.core.pc == 0x20000004
    break_mock.assert_called_with(0)


def test_should_execute_a_lsls_r5_r5_18_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsls_imm(r5, r5, 18))
    cpu.set_registers({"r5": 0b00000000000000000011})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0b11000000000000000000
    assert registers.pc == 0x20000002
    assert registers.c is False


def test_should_execute_a_lsls_r5_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsls_reg(r5, r0))
    cpu.set_registers({"r5": 0b00000000000000000011})
    cpu.set_registers({"r0": 0xFF003302})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0b00000000000000001100
    assert registers.pc == 0x20000002
    assert registers.c is False


def test_should_execute_a_lsls_r3_r4_instruction_when_shift_31(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsls_reg(r3, r4))
    cpu.set_registers({"r3": 1, "r4": 0x20, "c": False})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0
    assert registers.pc == 0x20000002
    assert registers.n is False
    assert registers.c is True
    assert registers.z is True


def test_should_execute_a_lsls_r5_r5_18_instruction_with_carry(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsls_imm(r5, r5, 18))
    cpu.set_registers({"r5": 0x00004001})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0x40000
    assert registers.c is True


def test_should_execute_a_lsrs_r5_r0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsrs_reg(r5, r0))
    cpu.set_registers({"r5": 0xFF00000F})
    cpu.set_registers({"r0": 0xFF003302})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0x3FC00003
    assert registers.pc == 0x20000002
    assert registers.c is True


def test_should_return_zero_for_lsrs_r2_r3_with_32_bit_shift(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsrs_reg(r2, r3))
    cpu.set_registers({"r2": 10, "r3": 32})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0
    assert registers.z is True
    assert registers.c is False


def test_should_execute_a_lsrs_r1_r1_1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsrs(r1, r1, 1))
    cpu.set_registers({"r1": 0b10})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0b1
    assert registers.pc == 0x20000002
    assert registers.c is False


def test_should_execute_a_lsrs_r1_r1_0_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_lsrs(r1, r1, 0))
    cpu.set_registers({"r1": 0xFFFFFFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0
    assert registers.pc == 0x20000002
    assert registers.c is True


def test_should_keep_lower_2_bits_of_sp_clear_when_executing_a_movs_sp_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mov(sp, r5))
    cpu.set_registers({"r5": 0x53})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x50


def test_should_keep_lower_bit_of_pc_clear_when_executing_a_movs_pc_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_mov(pc, r5))
    cpu.set_registers({"r5": 0x53})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x52


def test_should_execute_a_movs_r6_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_movs_reg(r6, r5))
    cpu.set_registers({"r5": 0x50})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r6 == 0x50


def test_should_execute_a_rsbs_r0_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_rsbs(r0, r3))
    cpu.set_registers({"r3": 100})
    cpu.single_step()
    registers = cpu.read_registers()
    assert s32(registers.r0) == -100
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_a_rev_r3_r1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_rev(r2, r3))
    cpu.set_registers({"r3": 0x11223344})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0x44332211


def test_should_execute_a_rev16_r0_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_rev16(r0, r5))
    cpu.set_registers({"r5": 0x11223344})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0x22114433


def test_should_execute_a_revsh_r1_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_revsh(r1, r2))
    cpu.set_registers({"r2": 0xEEAA55F0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0xFFFFF055


def test_should_execute_a_ror_r5_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ror(r5, r3))
    cpu.set_registers({"r5": 0x12345678})
    cpu.set_registers({"r3": 0x2004})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x2004
    assert registers.r5 == 0x81234567
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_a_ror_r5_r3_instruction_when_r3_32(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_ror(r5, r3))
    cpu.set_registers({"r5": 0x12345678})
    cpu.set_registers({"r3": 0x2044})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x2044
    assert registers.r5 == 0x81234567
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True


def test_should_execute_a_rsbs_r0_r3_instruction_2(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_rsbs(r0, r3))
    cpu.set_registers({"r3": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert s32(registers.r0) == 0
    assert registers.n is False
    assert registers.z is True
    assert registers.c is True
    assert registers.v is False


def test_should_execute_a_sbcs_r0_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_sbcs(r0, r3))
    cpu.set_registers({"r0": 100, "r3": 55, "c": False})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 44
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_a_sbcs_r0_r3_instruction_2(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_sbcs(r0, r3))
    cpu.set_registers({"r0": 0, "r3": 0xFFFFFFFF, "c": False})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r0 == 0
    assert registers.n is False
    assert registers.z is True
    assert registers.c is False
    assert registers.v is False


def test_should_execute_a_sdmia_r0_r1_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_stmia(r0, (1 << r1) | (1 << r2)))
    cpu.set_registers({"r0": 0x20000010, "r1": 0xF00DF00D, "r2": 0x4242})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002
    assert registers.r0 == 0x20000018
    assert cpu.read_uint32(0x20000010) == 0xF00DF00D
    assert cpu.read_uint32(0x20000014) == 0x4242


def test_should_execute_a_str_r6_r4_20_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_str(r6, r4, 20))
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20, "r6": 0xF00D})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020 + 20) == 0xF00D
    assert registers.pc == 0x20000002


def test_should_execute_a_str_r6_r4_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_str_reg(r6, r4, r5))
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20})
    cpu.set_registers({"r5": 20})
    cpu.set_registers({"r6": 0xF00D})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020 + 20) == 0xF00D
    assert registers.pc == 0x20000002


def test_should_execute_an_str_r3_sp_12_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_str_sp(r3, 12))
    cpu.set_registers({"r3": 0xAA55, "sp": 0x20000000})
    cpu.single_step()
    assert cpu.read_uint8(0x20000000 + 12) == 0x55
    assert cpu.read_uint8(0x20000000 + 13) == 0xAA


def test_should_execute_a_str_r2_r3_r1_instruction_where_r1_r3_32_bits(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_str_reg(r2, r1, r3))
    cpu.set_registers({"r1": -4, "r3": 0x20041E50, "r2": 0x4201337})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20041E4C) == 0x4201337
    assert registers.pc == 0x20000002


def test_should_execute_a_strb_r6_r4_20_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_strb(r6, r4, 0x1))
    cpu.write_uint32(0x20000020, 0xF5F4F3F2)
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20, "r6": 0xF055})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020) == 0xF5F455F2
    assert registers.pc == 0x20000002


def test_should_execute_a_strb_r6_r4_r5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_strb_reg(r6, r4, r5))
    cpu.write_uint32(0x20000020, 0xF5F4F3F2)
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20})
    cpu.set_registers({"r5": 1})
    cpu.set_registers({"r6": 0xF055})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020) == 0xF5F455F2
    assert registers.pc == 0x20000002


def test_should_execute_a_strh_r6_r4_20_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_strh(r6, r4, 0x2))
    cpu.write_uint32(0x20000020, 0xF5F4F3F2)
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20})
    cpu.set_registers({"r6": 0x6655})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020) == 0x6655F3F2
    assert registers.pc == 0x20000002


def test_should_execute_a_strh_r6_r4_r1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_strh_reg(r6, r4, r1))
    cpu.write_uint32(0x20000020, 0xF5F4F3F2)
    cpu.set_registers({"r4": RAM_START_ADDRESS + 0x20})
    cpu.set_registers({"r1": 2})
    cpu.set_registers({"r6": 0x6655})
    cpu.single_step()
    registers = cpu.read_registers()
    assert cpu.read_uint32(0x20000020) == 0x6655F3F2
    assert registers.pc == 0x20000002


def test_should_execute_a_sub_sp_0x10_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.set_registers({"sp": 0x10000040})
    cpu.write_uint16(0x20000000, opcode_sub_sp(0x10))
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.sp == 0x10000030


def test_should_execute_a_subs_r1_1_instruction_with_overflow(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs2(r1, 1))
    cpu.set_registers({"r1": -0x80000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r1 == 0x7FFFFFFF
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is True


def test_should_execute_a_subs_r5_r3_5_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs1(r5, r3, 5))
    cpu.set_registers({"r3": 0})
    cpu.single_step()
    registers = cpu.read_registers()
    assert s32(registers.r5) == -5
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is False


def test_should_execute_a_subs_r5_r3_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs_reg(r5, r3, r2))
    cpu.set_registers({"r3": 6})
    cpu.set_registers({"r2": 5})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 1
    assert registers.n is False
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_a_subs_r3_r3_r2_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs_reg(r3, r3, r2))
    cpu.set_registers({"r2": 8, "r3": 0xFFFFFFFF})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0xFFFFFFF7
    assert registers.n is True
    assert registers.z is False
    assert registers.c is True
    assert registers.v is False


def test_should_execute_a_subs_r5_r3_r2_instruction_and_set_n_v_flags(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs_reg(r5, r3, r2))
    cpu.set_registers({"r3": 0})
    cpu.set_registers({"r2": 0x80000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0x80000000
    assert registers.n is True
    assert registers.z is False
    assert registers.c is False
    assert registers.v is True


def test_should_execute_a_subs_r5_r3_r2_instruction_and_set_z_c_flags(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_subs_reg(r5, r3, r2))
    cpu.set_registers({"r2": 0x80000000})
    cpu.set_registers({"r3": 0x80000000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0
    assert registers.n is False
    assert registers.z is True
    assert registers.c is True
    assert registers.v is False


def test_should_raise_an_svcall_exception_when_svc_instruction_runs(cpu):
    SVCALL_HANDLER = 0x20002000
    cpu.set_registers({"sp": 0x20004000})
    cpu.set_pc(0x20004000)
    cpu.write_uint16(0x20004000, opcode_svc(10))
    cpu.set_registers({"r0": 0x44})
    cpu.write_uint32(VTOR, 0x20040000)
    cpu.write_uint32(0x20040000 + EXC_SVCALL * 4, SVCALL_HANDLER)
    cpu.write_uint16(SVCALL_HANDLER, opcode_movs(r0, 0x55))
    cpu.single_step()
    if isinstance(cpu, RP2040TestDriver):
        assert cpu.rp2040.core.pending_svcall is True
    cpu.single_step()  # SVCall handler should run here
    registers2 = cpu.read_registers()
    if isinstance(cpu, RP2040TestDriver):
        assert cpu.rp2040.core.pending_svcall is False
    assert registers2.pc == SVCALL_HANDLER + 2
    assert registers2.r0 == 0x55


def test_should_execute_a_sxtb_r2_r2_instruction_with_sign_bit_1(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_sxtb(r2, r2))
    cpu.set_registers({"r2": 0x22446688})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0xFFFFFF88


def test_should_execute_a_sxtb_r2_r2_instruction_with_sign_bit_0(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_sxtb(r2, r2))
    cpu.set_registers({"r2": 0x12345678})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0x78


def test_should_execute_a_sxth_r2_r5_instruction_with_sign_bit_1(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_sxth(r2, r5))
    cpu.set_registers({"r5": 0x22448765})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r2 == 0xFFFF8765


def test_should_execute_an_tst_r1_r3_instruction_when_the_result_is_negative(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_tst(r1, r3))
    cpu.set_registers({"r1": 0xF0000000})
    cpu.set_registers({"r3": 0xF0004000})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.n is True


def test_should_execute_an_tst_r1_r3_instruction_when_the_registers_are_different(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_tst(r1, r3))
    cpu.set_registers({"r1": 0xF0, "r3": 0x0F})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.z is True


def test_should_execute_an_uxtb_r5_r3_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_uxtb(r5, r3))
    cpu.set_registers({"r3": 0x12345678})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r5 == 0x78


def test_should_execute_an_uxth_r3_r1_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_uxth(r3, r1))
    cpu.set_registers({"r1": 0x12345678})
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.r3 == 0x5678


def test_should_execute_a_wfi_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_wfi())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002


def test_should_execute_a_yield_instruction(cpu):
    cpu.set_pc(0x20000000)
    cpu.write_uint16(0x20000000, opcode_yield())
    cpu.single_step()
    registers = cpu.read_registers()
    assert registers.pc == 0x20000002
