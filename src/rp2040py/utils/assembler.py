def opcode_adcs(rdn: int, rm: int) -> int:
    return (0b0100000101 << 6) | ((rm & 7) << 3) | (rdn & 7)


def opcode_adds1(rd: int, rn: int, imm3: int) -> int:
    return (0b0001110 << 9) | ((imm3 & 0x7) << 6) | ((rn & 7) << 3) | (rd & 7)


def opcode_adds2(rdn: int, imm8: int) -> int:
    return (0b00110 << 11) | ((rdn & 7) << 8) | (imm8 & 0xFF)


def opcode_add_sp_plus_imm(rd: int, imm8: int) -> int:
    return (0b10101 << 11) | ((rd & 7) << 8) | ((imm8 >> 2) & 0xFF)


def opcode_add_sp2(imm: int) -> int:
    return (0b101100000 << 7) | ((imm >> 2) & 0x7F)


def opcode_adds_reg(rd: int, rn: int, rm: int) -> int:
    return (0b0001100 << 9) | ((rm & 0x7) << 6) | ((rn & 7) << 3) | (rd & 7)


def opcode_add_reg(rdn: int, rm: int) -> int:
    return (0b01000100 << 8) | ((rdn & 0x8) << 4) | ((rm & 0xF) << 3) | (rdn & 0x7)


def opcode_adr(rd: int, imm8: int) -> int:
    return (0b10100 << 11) | ((rd & 7) << 8) | ((imm8 >> 2) & 0xFF)


def opcode_ands(rn: int, rm: int) -> int:
    return (0b0100000000 << 6) | ((rm & 7) << 3) | (rn & 0x7)


def opcode_asrs(rd: int, rm: int, imm5: int) -> int:
    return (0b00010 << 11) | ((imm5 & 0x1F) << 6) | ((rm & 0x7) << 3) | (rd & 0x7)


def opcode_asrs_reg(rdn: int, rm: int) -> int:
    return (0b0100000100 << 6) | ((rm & 0x7) << 3) | ((rm & 0x7) << 3) | (rdn & 0x7)


def opcode_b_t1(cond: int, imm8: int) -> int:
    return (0b1101 << 12) | ((cond & 0xF) << 8) | ((imm8 >> 1) & 0xFF)


def opcode_b_t2(imm11: int) -> int:
    return (0b11100 << 11) | ((imm11 >> 1) & 0x7FF)


def opcode_bics(rdn: int, rm: int) -> int:
    return (0b0100001110 << 6) | ((rm & 7) << 3) | (rdn & 7)


def opcode_bl(imm: int) -> int:
    imm11 = (imm >> 1) & 0x7FF
    imm10 = (imm >> 12) & 0x3FF
    s = 1 if imm < 0 else 0
    j2 = 1 - (((imm >> 22) & 0x1) ^ s)
    j1 = 1 - (((imm >> 23) & 0x1) ^ s)
    opcode = (0b1101 << 28) | (j1 << 29) | (j2 << 27) | (imm11 << 16) | (0b11110 << 11) | (s << 10) | imm10
    return opcode & 0xFFFFFFFF


def opcode_blx(rm: int) -> int:
    return (0b010001111 << 7) | (rm << 3)


def opcode_bx(rm: int) -> int:
    return (0b010001110 << 7) | (rm << 3)


def opcode_cmn(rn: int, rm: int) -> int:
    return (0b0100001011 << 6) | ((rm & 0x7) << 3) | (rn & 0x7)


def opcode_cmp_imm(rn: int, imm8: int) -> int:
    return (0b00101 << 11) | ((rn & 0x7) << 8) | (imm8 & 0xFF)


def opcode_cmp_reg_t1(rn: int, rm: int) -> int:
    return (0b0100001010 << 6) | ((rm & 0x7) << 3) | (rn & 0x7)


def opcode_cmp_reg_t2(rn: int, rm: int) -> int:
    return (0b01000101 << 8) | (((rn >> 3) & 0x1) << 7) | ((rm & 0xF) << 3) | (rn & 0x7)


def opcode_dmb_sy() -> int:
    return 0x8F50F3BF


def opcode_dsb_sy() -> int:
    return 0x8F4FF3BF


def opcode_eors(rdn: int, rm: int) -> int:
    return (0b0100000001 << 6) | ((rm & 0x7) << 3) | (rdn & 0x7)


def opcode_isb_sy() -> int:
    return 0x8F6FF3BF


def opcode_ldmia(rn: int, registers: int) -> int:
    return (0b11001 << 11) | ((rn & 0x7) << 8) | (registers & 0xFF)


def opcode_ldr_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101100 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldr_imm(rt: int, rn: int, imm5: int) -> int:
    return (0b01101 << 11) | (((imm5 >> 2) & 0x1F) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldr_lit(rt: int, imm8: int) -> int:
    return (0b01001 << 11) | ((imm8 >> 2) & 0xFF) | ((rt & 0x7) << 8)


def opcode_ldrb(rt: int, rn: int, imm5: int) -> int:
    return (0b01111 << 11) | ((imm5 & 0x1F) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldr_sp(rt: int, imm8: int) -> int:
    return (0b10011 << 11) | ((rt & 7) << 8) | ((imm8 >> 2) & 0xFF)


def opcode_ldrb_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101110 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldrh(rt: int, rn: int, imm5: int) -> int:
    return (0b10001 << 11) | (((imm5 >> 1) & 0xF) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldrh_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101101 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldrsb(rt: int, rn: int, rm: int) -> int:
    return (0b0101011 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_ldrsh(rt: int, rn: int, rm: int) -> int:
    return (0b0101111 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_lsls_reg(rdn: int, rm: int) -> int:
    return (0b0100000010 << 6) | ((rm & 0x7) << 3) | (rdn & 0x7)


def opcode_lsls_imm(rd: int, rm: int, imm5: int) -> int:
    return (0b00000 << 11) | ((imm5 & 0x1F) << 6) | ((rm & 0x7) << 3) | (rd & 0x7)


def opcode_lsrs(rd: int, rm: int, imm5: int) -> int:
    return (0b00001 << 11) | ((imm5 & 0x1F) << 6) | ((rm & 0x7) << 3) | (rd & 0x7)


def opcode_lsrs_reg(rdn: int, rm: int) -> int:
    return (0b0100000011 << 6) | ((rm & 0x7) << 3) | (rdn & 0x7)


def opcode_mov(rd: int, rm: int) -> int:
    return (0b01000110 << 8) | ((1 if (rd & 0x8) else 0) << 7) | (rm << 3) | (rd & 0x7)


def opcode_movs(rd: int, imm8: int) -> int:
    return (0b00100 << 11) | ((rd & 0x7) << 8) | (imm8 & 0xFF)


def opcode_movs_reg(rd: int, rm: int) -> int:
    return (0b000000000 << 6) | ((rm & 0x7) << 3) | (rd & 0x7)


def opcode_mrs(rd: int, spec_reg: int) -> int:
    return ((0b1000 << 28) | ((rd & 0xF) << 24) | ((spec_reg & 0xFF) << 16) | 0b1111001111101111) & 0xFFFFFFFF


def opcode_msr(spec_reg: int, rn: int) -> int:
    return ((0b10001000 << 24) | ((spec_reg & 0xFF) << 16) | (0b111100111000 << 4) | (rn & 0xF)) & 0xFFFFFFFF


def opcode_muls(rn: int, rdm: int) -> int:
    return (0b0100001101 << 6) | ((rn & 7) << 3) | (rdm & 7)


def opcode_mvns(rd: int, rm: int) -> int:
    return (0b0100001111 << 6) | ((rm & 7) << 3) | (rd & 7)


def opcode_nop() -> int:
    return 0b1011111100000000


def opcode_orrs(rn: int, rm: int) -> int:
    return (0b0100001100 << 6) | ((rm & 0x7) << 3) | (rn & 0x7)


def opcode_pop(p: bool, register_list: int) -> int:
    return (0b1011110 << 9) | ((1 if p else 0) << 8) | register_list


def opcode_push(m: bool, register_list: int) -> int:
    return (0b1011010 << 9) | ((1 if m else 0) << 8) | register_list


def opcode_rev(rd: int, rn: int) -> int:
    return (0b1011101000 << 6) | ((rn & 0x7) << 3) | (rd & 0x7)


def opcode_rev16(rd: int, rn: int) -> int:
    return (0b1011101001 << 6) | ((rn & 0x7) << 3) | (rd & 0x7)


def opcode_revsh(rd: int, rn: int) -> int:
    return (0b1011101011 << 6) | ((rn & 0x7) << 3) | (rd & 0x7)


def opcode_ror(rdn: int, rm: int) -> int:
    return (0b0100000111 << 6) | ((rm & 0x7) << 3) | (rdn & 0x7)


def opcode_rsbs(rd: int, rn: int) -> int:
    return (0b0100001001 << 6) | ((rn & 0x7) << 3) | (rd & 0x7)


def opcode_sbcs(rn: int, rm: int) -> int:
    return (0b0100000110 << 6) | ((rm & 0x7) << 3) | (rn & 0x7)


def opcode_sev() -> int:
    return 0b1011111101000000


def opcode_stmia(rn: int, registers: int) -> int:
    return (0b11000 << 11) | ((rn & 0x7) << 8) | (registers & 0xFF)


def opcode_str(rt: int, rm: int, imm5: int) -> int:
    return (0b01100 << 11) | (((imm5 >> 2) & 0x1F) << 6) | ((rm & 0x7) << 3) | (rt & 0x7)


def opcode_str_sp(rt: int, imm8: int) -> int:
    return (0b10010 << 11) | ((rt & 7) << 8) | ((imm8 >> 2) & 0xFF)


def opcode_str_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101000 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_strb(rt: int, rm: int, imm5: int) -> int:
    return (0b01110 << 11) | ((imm5 & 0x1F) << 6) | ((rm & 0x7) << 3) | (rt & 0x7)


def opcode_strb_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101010 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_strh(rt: int, rm: int, imm5: int) -> int:
    return (0b10000 << 11) | (((imm5 >> 1) & 0x1F) << 6) | ((rm & 0x7) << 3) | (rt & 0x7)


def opcode_strh_reg(rt: int, rn: int, rm: int) -> int:
    return (0b0101001 << 9) | ((rm & 0x7) << 6) | ((rn & 0x7) << 3) | (rt & 0x7)


def opcode_subs1(rd: int, rn: int, imm3: int) -> int:
    return (0b0001111 << 9) | ((imm3 & 0x7) << 6) | ((rn & 7) << 3) | (rd & 7)


def opcode_subs2(rdn: int, imm8: int) -> int:
    return (0b00111 << 11) | ((rdn & 7) << 8) | (imm8 & 0xFF)


def opcode_subs_reg(rd: int, rn: int, rm: int) -> int:
    return (0b0001101 << 9) | ((rm & 0x7) << 6) | ((rn & 7) << 3) | (rd & 7)


def opcode_sub_sp(imm: int) -> int:
    return (0b101100001 << 7) | ((imm >> 2) & 0x7F)


def opcode_svc(imm8: int) -> int:
    return (0b11011111 << 8) | (imm8 & 0xFF)


def opcode_sxtb(rd: int, rm: int) -> int:
    return (0b1011001001 << 6) | ((rm & 7) << 3) | (rd & 7)


def opcode_sxth(rd: int, rm: int) -> int:
    return (0b1011001000 << 6) | ((rm & 7) << 3) | (rd & 7)


def opcode_tst(rm: int, rn: int) -> int:
    return (0b0100001000 << 6) | ((rn & 7) << 3) | (rm & 7)


def opcode_uxtb(rd: int, rm: int) -> int:
    return (0b1011001011 << 6) | ((rm & 7) << 3) | (rd & 7)


def opcode_udf(imm8: int) -> int:
    return ((0b11011110 << 8) | (imm8 & 0xFF)) & 0xFFFFFFFF


def opcode_udf2(imm16: int) -> int:
    imm12 = imm16 & 0xFFF
    imm4 = (imm16 >> 12) & 0xF
    return ((0b111101111111 << 4) | imm4 | (0b1010 << 28) | (imm12 << 16)) & 0xFFFFFFFF


def opcode_uxth(rd: int, rm: int) -> int:
    return (0b1011001010 << 6) | ((rm & 7) << 3) | (rd & 7)


def opcode_wfe() -> int:
    return 0b1011111100100000


def opcode_wfi() -> int:
    return 0b1011111100110000


def opcode_yield() -> int:
    return 0b1011111100010000
