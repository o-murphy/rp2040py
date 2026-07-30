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
)

R0 = 0
R1 = 1
R2 = 2
R3 = 3
R4 = 4
R5 = 5
R6 = 6
R7 = 7
R8 = 8
IP = 12
LR = 14

PRIMASK = 16


def test_adcs():
    assert opcode_adcs(R3, R0) == 0x4143


def test_add_sp_plus_imm():
    assert opcode_add_sp_plus_imm(R1, 4) == 0xA901


def test_add_sp2():
    assert opcode_add_sp2(12) == 0xB003


def test_adds1():
    assert opcode_adds1(R0, R3, 0) == 0x1C18


def test_adds_reg():
    assert opcode_adds_reg(R1, R1, R3) == 0x18C9


def test_add_reg():
    assert opcode_add_reg(R1, IP) == 0x4461


def test_adds2():
    assert opcode_adds2(R1, 1) == 0x3101


def test_ands():
    assert opcode_ands(R5, R0) == 0x4005


def test_adr():
    assert opcode_adr(R4, 52) == 0xA40D


def test_asrs():
    assert opcode_asrs(R3, R2, 31) == 0x17D3


def test_asrs_reg():
    assert opcode_asrs_reg(R3, R4) == 0x4123


def test_b_t1():
    assert opcode_b_t1(1, 0x1F8) == 0xD1FC


def test_b_t2():
    assert opcode_b_t2(0xFEC) == 0xE7F6


def test_bics():
    assert opcode_bics(R0, R3) == 0x4398


def test_bl_negative():
    assert opcode_bl(-198) == 0xFF9DF7FF


def test_bl_positive():
    assert opcode_bl(10) == 0xF805F000


def test_bl_negative_large():
    assert opcode_bl(-3242) == 0xF9ABF7FF


def test_blx():
    assert opcode_blx(R1) == 0x4788


def test_bx():
    assert opcode_bx(LR) == 0x4770


def test_cmn():
    assert opcode_cmn(R5, R6) == 0x42F5


def test_cmp_imm():
    assert opcode_cmp_imm(R5, 66) == 0x2D42


def test_cmp_reg_t1():
    assert opcode_cmp_reg_t1(R5, R0) == 0x4285


def test_cmp_reg_t2():
    assert opcode_cmp_reg_t2(IP, R6) == 0x45B4


def test_eors():
    assert opcode_eors(R1, R3) == 0x4059


def test_dmb_sy():
    assert opcode_dmb_sy() == 0x8F50F3BF


def test_dsb_sy():
    assert opcode_dsb_sy() == 0x8F4FF3BF


def test_ldmia():
    assert opcode_ldmia(R0, (1 << R1) | (1 << R2)) == 0xC806


def test_isb_sy():
    assert opcode_isb_sy() == 0x8F6FF3BF


def test_lsls_reg():
    assert opcode_lsls_reg(R5, R0) == 0x4085


def test_lsrs():
    assert opcode_lsrs(R1, R1, 1) == 0x0849


def test_lsrs_reg():
    assert opcode_lsrs_reg(R0, R4) == 0x40E0


def test_ldr_imm():
    assert opcode_ldr_imm(R3, R2, 24) == 0x6993


def test_ldr_lit():
    assert opcode_ldr_lit(R0, 148) == 0x4825


def test_ldr_reg():
    assert opcode_ldr_reg(R3, R3, R4) == 0x591B


def test_ldr_sp():
    assert opcode_ldr_sp(R3, 12) == 0x9B03


def test_ldrb():
    assert opcode_ldrb(R0, R1, 0) == 0x7808


def test_ldrb_reg():
    assert opcode_ldrb_reg(R2, R5, R4) == 0x5D2A


def test_ldrh():
    assert opcode_ldrh(R3, R0, 2) == 0x8843


def test_ldrh_reg():
    assert opcode_ldrh_reg(R4, R0, R1) == 0x5A44


def test_ldrsb():
    assert opcode_ldrsb(R3, R2, R3) == 0x56D3


def test_ldrsh():
    assert opcode_ldrsh(R5, R3, R5) == 0x5F5D


def test_lsls_imm():
    assert opcode_lsls_imm(R5, R5, 18) == 0x04AD


def test_mov():
    assert opcode_mov(R3, R8) == 0x4643


def test_movs():
    assert opcode_movs(R5, 128) == 0x2580


def test_movs_reg():
    assert opcode_movs_reg(R6, R5) == 0x002E


def test_mrs():
    assert opcode_mrs(R6, PRIMASK) == 0x8610F3EF


def test_msr():
    assert opcode_msr(PRIMASK, R6) == 0x8810F386


def test_muls():
    assert opcode_muls(R2, R0) == 0x4350


def test_mvns():
    assert opcode_mvns(R3, R3) == 0x43DB


def test_orrs():
    assert opcode_orrs(R3, R0) == 0x4303


def test_pop():
    assert opcode_pop(True, (1 << R0) | (1 << R1)) == 0xBD03


def test_push():
    assert opcode_push(True, (1 << R4) | (1 << R5) | (1 << R6)) == 0xB570


def test_rev():
    assert opcode_rev(R3, R1) == 0xBA0B


def test_rev16():
    assert opcode_rev16(R2, R7) == 0xBA7A


def test_revsh():
    assert opcode_revsh(R1, R5) == 0xBAE9


def test_ror():
    assert opcode_ror(R6, R0) == 0x41C6


def test_rsbs():
    assert opcode_rsbs(R0, R3) == 0x4258


def test_sbcs():
    assert opcode_sbcs(R0, R3) == 0x4198


def test_stmia():
    assert opcode_stmia(R2, 1 << R0) == 0xC201


def test_str():
    assert opcode_str(R6, R4, 20) == 0x6166


def test_str_sp():
    assert opcode_str_sp(R1, 4) == 0x9101


def test_str_reg():
    assert opcode_str_reg(R2, R1, R4) == 0x510A


def test_strb():
    assert opcode_strb(R3, R2, 0) == 0x7013


def test_strb_reg():
    assert opcode_strb_reg(R3, R2, R5) == 0x5553


def test_strh():
    assert opcode_strh(R1, R3, 4) == 0x8099


def test_strh_reg():
    assert opcode_strh_reg(R1, R3, R2) == 0x5299


def test_sub_sp():
    assert opcode_sub_sp(12) == 0xB083


def test_subs1():
    assert opcode_subs1(R3, R0, 1) == 0x1E43


def test_subs_reg():
    assert opcode_subs_reg(R1, R1, R0) == 0x1A09


def test_subs2():
    assert opcode_subs2(R3, 13) == 0x3B0D


def test_svc():
    assert opcode_svc(0) == 0xDF00


def test_sxtb():
    assert opcode_sxtb(R2, R2) == 0xB252


def test_sxth():
    assert opcode_sxth(R6, R3) == 0xB21E


def test_tst():
    assert opcode_tst(R1, R3) == 0x4219


def test_udf():
    assert opcode_udf(1) == 0xDE01


def test_udf2():
    assert opcode_udf2(0) == 0xA000F7F0


def test_uxtb():
    assert opcode_uxtb(R3, R3) == 0xB2DB


def test_uxth():
    assert opcode_uxth(R3, R0) == 0xB283
