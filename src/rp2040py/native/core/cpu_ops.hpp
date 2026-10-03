// The Thumb instruction handlers of core/cpu.hpp, transcribed from the Cython `op_*` functions of `_cortex_m0_core.pyx` (which are
// themselves 1:1 from `_cortex_m0_core.py`), plus the decode (`match_pattern`, `resolve_wide`) and the 64K-entry dispatch table.
// Included at the end of cpu.hpp (and including it, so it also stands alone: the include guards make the cycle harmless).
//
// A handler returns its cycle count, or kCpuFault when a bus access or a host callback failed (see the failure model in cpu.hpp):
// it returns *at that point*, so a failed load never writes its destination register and a failed instruction never counts cycles.
#ifndef RP2040PY_CORE_CPU_OPS_HPP
#define RP2040PY_CORE_CPU_OPS_HPP

#include "cpu.hpp"

namespace rp2040core {
namespace cpu_ops {

using cpu_detail::PC_REGISTER;
using cpu_detail::SP_REGISTER;
using cpu_detail::s32;
using cpu_detail::sign_extend16;
using cpu_detail::sign_extend8;
using cpu_detail::u32;

#define OP(name) inline int name(Cpu& core, uint32_t opcode, uint32_t opcode2, uint32_t opcode_pc)
#define UNUSED_OPERANDS (void)opcode, (void)opcode2, (void)opcode_pc
#define R core.registers
// Fail the instruction right here if the bus access or callback just made parked a Python error.
#define CHECK_FAILED() \
    do {               \
        if (core.failed()) return kCpuFault; \
    } while (0)

inline int cycles_io(uint32_t addr, bool write) noexcept {
    if (addr >= 0xD0000000u && addr - 0xD0000000u < 0x10000000u) return 0;  // SIO
    if (addr >= 0x40000000u && addr - 0x40000000u < 0x10000000u) return write ? 4 : 3;  // APB
    return 1;
}

inline bool bx_write_pc(Cpu& core, uint32_t address) noexcept {
    if (core.current_mode == cpu_detail::MODE_HANDLER && (address >> 28) == 0xF) return core.exception_return(address & 0x0FFFFFFFu);
    core.registers[15] = address & 0xFFFFFFFEu;
    return true;
}

inline int64_t add_update_flags(Cpu& core, int64_t addend1, int64_t addend2) noexcept {
    const int64_t raw_result = addend1 + addend2;
    const int64_t unsigned_sum = u32(raw_result);
    const int64_t signed_sum = s32(addend1) + s32(addend2);
    core.n = (raw_result & 0x80000000LL) != 0;
    core.z = (raw_result & 0xFFFFFFFFLL) == 0;
    core.c = raw_result != unsigned_sum;
    core.v = s32(raw_result) != signed_sum;
    return unsigned_sum;
}

inline int64_t subtract_update_flags(Cpu& core, int64_t minuend, int64_t subtrahend) noexcept {
    const int64_t result = minuend - subtrahend;
    const uint32_t result_u = static_cast<uint32_t>(u32(result));
    const uint32_t minuend_u = static_cast<uint32_t>(u32(minuend));
    const uint32_t subtrahend_u = static_cast<uint32_t>(u32(subtrahend));
    core.n = (result_u & 0x80000000u) != 0;
    core.z = result_u == 0;
    core.c = minuend >= subtrahend;
    core.v = ((result_u & 0x80000000u) != 0 && (minuend_u & 0x80000000u) == 0 && (subtrahend_u & 0x80000000u) != 0) ||
             ((result_u & 0x80000000u) == 0 && (minuend_u & 0x80000000u) != 0 && (subtrahend_u & 0x80000000u) == 0);
    return result;
}

inline void set_nz(Cpu& core, uint32_t result) noexcept {
    core.n = (result & 0x80000000u) != 0;
    core.z = result == 0;
}

OP(op_adcs) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    R[rdn] = static_cast<uint32_t>(add_update_flags(core, R[rm], static_cast<int64_t>(R[rdn]) + (core.c ? 1 : 0)));
    return 1;
}
OP(op_add_register_sp_plus_immediate) {
    UNUSED_OPERANDS;
    R[(opcode >> 8) & 0x7] = R[13] + ((opcode & 0xFF) << 2);
    return 1;
}
OP(op_add_sp_plus_immediate) {
    UNUSED_OPERANDS;
    R[13] = (R[13] + ((opcode & 0x7F) << 2)) & 0xFFFFFFFCu;
    return 1;
}
OP(op_adds_encoding_t1) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = static_cast<uint32_t>(add_update_flags(core, R[(opcode >> 3) & 0x7], (opcode >> 6) & 0x7));
    return 1;
}
OP(op_adds_encoding_t2) {
    UNUSED_OPERANDS;
    const uint32_t rdn = (opcode >> 8) & 0x7;
    R[rdn] = static_cast<uint32_t>(add_update_flags(core, R[rdn], opcode & 0xFF));
    return 1;
}
OP(op_adds_register) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = static_cast<uint32_t>(add_update_flags(core, R[(opcode >> 3) & 0x7], R[(opcode >> 6) & 0x7]));
    return 1;
}
OP(op_add_register) {
    UNUSED_OPERANDS;
    int delta = 1;
    const uint32_t rm = (opcode >> 3) & 0xF;
    const uint32_t rdn = ((opcode & 0x80) >> 4) | (opcode & 0x7);
    const uint32_t left = rdn == PC_REGISTER ? R[15] + 2 : R[rdn];
    const uint32_t right = rm == PC_REGISTER ? R[15] + 2 : R[rm];
    const uint32_t result = left + right;
    if (rdn != SP_REGISTER && rdn != PC_REGISTER) {
        R[rdn] = result;
    } else if (rdn == PC_REGISTER) {
        R[rdn] = result & 0xFFFFFFFEu;
        delta += 1;
    } else {
        R[rdn] = result & 0xFFFFFFFCu;
    }
    return delta;
}
OP(op_adr) {
    UNUSED_OPERANDS;
    R[(opcode >> 8) & 0x7] = (opcode_pc & 0xFFFFFFFCu) + 4 + ((opcode & 0xFF) << 2);
    return 1;
}
OP(op_ands_encoding_t2) {
    UNUSED_OPERANDS;
    const uint32_t rdn = opcode & 0x7;
    const uint32_t result = R[rdn] & R[(opcode >> 3) & 0x7];
    R[rdn] = result;
    set_nz(core, result);
    return 1;
}
OP(op_asrs_immediate) {
    UNUSED_OPERANDS;
    const uint32_t imm5 = (opcode >> 6) & 0x1F, rm = (opcode >> 3) & 0x7, rd = opcode & 0x7;
    const uint32_t input = R[rm];
    const uint32_t shift_n = imm5 ? imm5 : 32;
    int32_t result;
    if (shift_n < 32) result = static_cast<int32_t>(s32(input)) >> shift_n;
    else result = (input & 0x80000000u) ? -1 : 0;
    R[rd] = static_cast<uint32_t>(result);
    core.n = (static_cast<uint32_t>(result) & 0x80000000u) != 0;
    core.z = result == 0;
    core.c = (input & (1u << (shift_n - 1))) != 0;
    return 1;
}
OP(op_asrs_register) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    const uint32_t input = R[rdn];
    const uint32_t amount = R[rm] & 255;
    const uint32_t shift_n = amount < 32 ? amount : 32;
    int32_t result;
    if (shift_n < 32) result = static_cast<int32_t>(s32(input)) >> shift_n;
    else result = (input & 0x80000000u) ? -1 : 0;
    R[rdn] = static_cast<uint32_t>(result);
    core.n = (static_cast<uint32_t>(result) & 0x80000000u) != 0;
    core.z = result == 0;
    core.c = (input & (1u << ((shift_n - 1) & 31))) != 0;  // shift_n can be 0 here: `& 31` is the source's JS wraparound
    return 1;
}
OP(op_b_with_cond) {
    UNUSED_OPERANDS;
    int delta = 1;
    int32_t imm8 = static_cast<int32_t>((opcode & 0xFF) << 1);
    const uint32_t cond = (opcode >> 8) & 0xF;
    if (imm8 & (1 << 8)) imm8 = (imm8 & 0x1FF) - 0x200;
    if (core.check_condition(cond)) {
        R[15] = static_cast<uint32_t>(static_cast<int64_t>(R[15]) + imm8 + 2);
        delta += 1;
    }
    return delta;
}
OP(op_b) {
    UNUSED_OPERANDS;
    int32_t imm11 = static_cast<int32_t>((opcode & 0x7FF) << 1);
    if (imm11 & (1 << 11)) imm11 = (imm11 & 0x7FF) - 0x800;
    R[15] = static_cast<uint32_t>(static_cast<int64_t>(R[15]) + imm11 + 2);
    return 2;
}
OP(op_bics) {
    UNUSED_OPERANDS;
    const uint32_t rdn = opcode & 0x7;
    const uint32_t result = R[rdn] & ~R[(opcode >> 3) & 0x7];
    R[rdn] = result;
    set_nz(core, result);
    return 1;
}
OP(op_bkpt) {
    UNUSED_OPERANDS;
    core.break_rewind = 2;
    core.on_break(opcode & 0xFF);
    CHECK_FAILED();
    return 1;
}
OP(op_bl) {
    UNUSED_OPERANDS;
    const uint32_t imm11 = opcode2 & 0x7FF, j2 = (opcode2 >> 11) & 0x1, j1 = (opcode2 >> 13) & 0x1;
    const uint32_t imm10 = opcode & 0x3FF, s = (opcode >> 10) & 0x1;
    const uint32_t i1 = 1 - (s ^ j1), i2 = 1 - (s ^ j2);
    const uint32_t imm32 = ((s ? 0xFFu : 0u) << 24) | ((i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1));
    R[14] = (R[15] + 2) | 0x1;
    R[15] = R[15] + 2 + imm32;
    core.bl_taken(false);
    CHECK_FAILED();
    return 3;
}
OP(op_blx) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0xF;
    R[14] = R[15] | 0x1;
    R[15] = R[rm] & ~1u;
    core.bl_taken(true);
    CHECK_FAILED();
    return 2;
}
OP(op_bx) {
    UNUSED_OPERANDS;
    if (!bx_write_pc(core, R[(opcode >> 3) & 0xF])) return kCpuFault;
    return 2;
}
OP(op_cmn_register) {
    UNUSED_OPERANDS;
    add_update_flags(core, R[opcode & 0x7], R[(opcode >> 3) & 0x7]);
    return 1;
}
OP(op_cmp_immediate) {
    UNUSED_OPERANDS;
    subtract_update_flags(core, R[(opcode >> 8) & 0x7], opcode & 0xFF);
    return 1;
}
OP(op_cmp_register) {
    UNUSED_OPERANDS;
    subtract_update_flags(core, R[opcode & 0x7], R[(opcode >> 3) & 0x7]);
    return 1;
}
OP(op_cmp_register_encoding_t2) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0xF, rn = ((opcode >> 4) & 0x8) | (opcode & 0x7);
    subtract_update_flags(core, R[rn], R[rm]);
    return 1;
}
OP(op_cpsid_i) {
    UNUSED_OPERANDS;
    core.pm = true;
    return 1;
}
OP(op_cpsie_i) {
    UNUSED_OPERANDS;
    core.pm = false;
    core.interrupts_updated = true;
    return 1;
}
OP(op_barrier) {  // DMB SY / DSB SY / ISB SY
    UNUSED_OPERANDS;
    R[15] += 2;
    return 3;
}
OP(op_eors) {
    UNUSED_OPERANDS;
    const uint32_t rdn = opcode & 0x7;
    const uint32_t result = R[(opcode >> 3) & 0x7] ^ R[rdn];
    R[rdn] = result;
    set_nz(core, result);
    return 1;
}
OP(op_ldmia) {
    UNUSED_OPERANDS;
    int delta = 1;
    const uint32_t rn = (opcode >> 8) & 0x7, reg_list = opcode & 0xFF;
    uint32_t address = R[rn];
    for (uint32_t i = 0; i < 8; ++i) {
        if (reg_list & (1u << i)) {
            const uint32_t word = core.bus().read32(address);
            CHECK_FAILED();
            R[i] = word;
            address += 4;
            delta += 1;
        }
    }
    if (!(reg_list & (1u << rn))) R[rn] = address;  // write back
    return delta;
}

// The load/store family: `addr` computed by the caller, cycles = 1 + cycles_io.
#define LOAD_OP(name, address_expr, rt_expr, read_expr)       \
    OP(name) {                                                \
        UNUSED_OPERANDS;                                      \
        const uint32_t rt = (rt_expr);                        \
        const uint32_t addr = (address_expr);                 \
        const int delta = 1 + cycles_io(addr, false);         \
        const uint32_t value = (read_expr);                   \
        CHECK_FAILED();                                       \
        R[rt] = value;                                        \
        return delta;                                         \
    }
#define STORE_OP(name, address_expr, rt_expr, write_stmt)     \
    OP(name) {                                                \
        UNUSED_OPERANDS;                                      \
        const uint32_t rt = (rt_expr);                        \
        const uint32_t addr = (address_expr);                 \
        const int delta = 1 + cycles_io(addr, true);          \
        write_stmt;                                           \
        CHECK_FAILED();                                       \
        return delta;                                         \
    }

LOAD_OP(op_ldr_immediate, R[(opcode >> 3) & 0x7] + (((opcode >> 6) & 0x1F) << 2), opcode & 0x7, core.bus().read32(addr))
LOAD_OP(op_ldr_sp_immediate, R[13] + ((opcode & 0xFF) << 2), (opcode >> 8) & 0x7, core.bus().read32(addr))
LOAD_OP(op_ldr_literal, ((R[15] + 2) & 0xFFFFFFFCu) + ((opcode & 0xFF) << 2), (opcode >> 8) & 7, core.bus().read32(addr))
LOAD_OP(op_ldr_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().read32(addr))
LOAD_OP(op_ldrb_immediate, R[(opcode >> 3) & 0x7] + ((opcode >> 6) & 0x1F), opcode & 0x7, core.bus().read8(addr))
LOAD_OP(op_ldrb_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().read8(addr))
LOAD_OP(op_ldrh_immediate, R[(opcode >> 3) & 0x7] + (((opcode >> 6) & 0x1F) << 1), opcode & 0x7, core.bus().read16(addr))
LOAD_OP(op_ldrh_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().read16(addr))
LOAD_OP(op_ldrsb, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, sign_extend8(core.bus().read8(addr)))
LOAD_OP(op_ldrsh, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, sign_extend16(core.bus().read16(addr)))

STORE_OP(op_str_immediate, R[(opcode >> 3) & 0x7] + (((opcode >> 6) & 0x1F) << 2), opcode & 0x7, core.bus().write32(addr, R[rt]))
STORE_OP(op_str_sp_immediate, R[13] + ((opcode & 0xFF) << 2), (opcode >> 8) & 0x7, core.bus().write32(addr, R[rt]))
STORE_OP(op_str_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().write32(addr, R[rt]))
STORE_OP(op_strb_immediate, R[(opcode >> 3) & 0x7] + ((opcode >> 6) & 0x1F), opcode & 0x7, core.bus().write8(addr, R[rt]))
STORE_OP(op_strb_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().write8(addr, R[rt]))
STORE_OP(op_strh_immediate, R[(opcode >> 3) & 0x7] + (((opcode >> 6) & 0x1F) << 1), opcode & 0x7, core.bus().write16(addr, R[rt]))
STORE_OP(op_strh_register, R[(opcode >> 6) & 0x7] + R[(opcode >> 3) & 0x7], opcode & 0x7, core.bus().write16(addr, R[rt]))

OP(op_lsls_immediate) {
    UNUSED_OPERANDS;
    const uint32_t imm5 = (opcode >> 6) & 0x1F, rm = (opcode >> 3) & 0x7, rd = opcode & 0x7;
    const uint32_t input = R[rm];
    const uint32_t result = input << imm5;
    R[rd] = result;
    set_nz(core, result);
    if (imm5) core.c = (input & (1u << (32 - imm5))) != 0;
    return 1;
}
OP(op_lsls_register) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    const uint32_t input = R[rdn], count = R[rm] & 0xFF;
    const uint32_t result = count >= 32 ? 0 : (input << count);
    R[rdn] = result;
    set_nz(core, result);
    if (count) core.c = (input & (1u << ((32 - count) & 31))) != 0;  // `& 31`: the source's mod-32 shift wraparound
    return 1;
}
OP(op_lsrs_immediate) {
    UNUSED_OPERANDS;
    const uint32_t imm5 = (opcode >> 6) & 0x1F, rm = (opcode >> 3) & 0x7, rd = opcode & 0x7;
    const uint32_t input = R[rm];
    const uint32_t result = imm5 ? (input >> imm5) : 0;
    R[rd] = result;
    set_nz(core, result);
    core.c = ((input >> (imm5 ? imm5 - 1 : 31)) & 0x1) != 0;
    return 1;
}
OP(op_lsrs_register) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    const uint32_t amount = R[rm] & 0xFF, input = R[rdn];
    const uint32_t result = amount < 32 ? (input >> amount) : 0;
    R[rdn] = result;
    set_nz(core, result);
    core.c = amount <= 32 ? ((input >> ((amount - 1) & 31)) & 0x1) != 0 : false;
    return 1;
}
OP(op_mov) {
    UNUSED_OPERANDS;
    int delta = 1;
    const uint32_t rm = (opcode >> 3) & 0xF, rd = ((opcode >> 4) & 0x8) | (opcode & 0x7);
    uint32_t value = rm == PC_REGISTER ? R[15] + 2 : R[rm];
    if (rd == PC_REGISTER) {
        delta += 1;
        value &= ~1u;
    } else if (rd == SP_REGISTER) {
        value &= ~3u;
    }
    R[rd] = value;
    return delta;
}
OP(op_movs) {
    UNUSED_OPERANDS;
    const uint32_t value = opcode & 0xFF;
    R[(opcode >> 8) & 7] = value;
    set_nz(core, value);
    return 1;
}
OP(op_mrs) {
    UNUSED_OPERANDS;
    uint32_t value;
    if (!core.read_special_register(opcode2 & 0xFF, &value)) return kCpuFault;
    R[(opcode2 >> 8) & 0xF] = value;
    R[15] += 2;
    return 3;
}
OP(op_msr) {
    UNUSED_OPERANDS;
    if (!core.write_special_register(opcode2 & 0xFF, R[opcode & 0xF])) return kCpuFault;
    R[15] += 2;
    return 3;
}
OP(op_muls) {
    UNUSED_OPERANDS;
    const uint32_t rn = (opcode >> 3) & 0x7, rdm = opcode & 0x7;
    const uint32_t result = static_cast<uint32_t>((static_cast<uint64_t>(R[rn]) * R[rdm]) & 0xFFFFFFFFu);
    R[rdm] = result;
    set_nz(core, result);
    return 1;
}
OP(op_mvns) {
    UNUSED_OPERANDS;
    const uint32_t result = ~R[(opcode >> 3) & 7];
    R[opcode & 7] = result;
    set_nz(core, result);
    return 1;
}
OP(op_orrs_encoding_t2) {
    UNUSED_OPERANDS;
    const uint32_t rdn = opcode & 0x7;
    const uint32_t result = R[rdn] | R[(opcode >> 3) & 0x7];
    R[rdn] = result;
    set_nz(core, result);
    return 1;
}
OP(op_pop) {
    UNUSED_OPERANDS;
    int delta = 1;
    const uint32_t p = (opcode >> 8) & 1;
    uint32_t address = R[13];
    for (uint32_t i = 0; i < 8; ++i) {
        if (opcode & (1u << i)) {
            const uint32_t word = core.bus().read32(address);
            CHECK_FAILED();
            R[i] = word;
            address += 4;
            delta += 1;
        }
    }
    if (p) {
        R[13] = (address + 4) & 0xFFFFFFFCu;
        const uint32_t target = core.bus().read32(address);
        CHECK_FAILED();
        if (!bx_write_pc(core, target)) return kCpuFault;
        delta += 2;
    } else {
        R[13] = address & 0xFFFFFFFCu;
    }
    return delta;
}
OP(op_push) {
    UNUSED_OPERANDS;
    int delta = 1;
    uint32_t bit_count = 0;
    for (uint32_t i = 0; i < 9; ++i) {
        if (opcode & (1u << i)) bit_count += 1;
    }
    uint32_t address = R[13] - 4 * bit_count;
    for (uint32_t i = 0; i < 8; ++i) {
        if (opcode & (1u << i)) {
            core.bus().write32(address, R[i]);
            CHECK_FAILED();
            delta += 1;
            address += 4;
        }
    }
    if (opcode & (1u << 8)) {
        core.bus().write32(address, R[14]);
        CHECK_FAILED();
    }
    R[13] = (R[13] - 4 * bit_count) & 0xFFFFFFFCu;
    return delta;
}
OP(op_rev) {
    UNUSED_OPERANDS;
    const uint32_t v = R[(opcode >> 3) & 0x7];
    R[opcode & 0x7] = ((v & 0xFF) << 24) | (((v >> 8) & 0xFF) << 16) | (((v >> 16) & 0xFF) << 8) | ((v >> 24) & 0xFF);
    return 1;
}
OP(op_rev16) {
    UNUSED_OPERANDS;
    const uint32_t v = R[(opcode >> 3) & 0x7];
    R[opcode & 0x7] = (((v >> 16) & 0xFF) << 24) | (((v >> 24) & 0xFF) << 16) | ((v & 0xFF) << 8) | ((v >> 8) & 0xFF);
    return 1;
}
OP(op_revsh) {
    UNUSED_OPERANDS;
    const uint32_t v = R[(opcode >> 3) & 0x7];
    R[opcode & 0x7] = sign_extend16(((v & 0xFF) << 8) | ((v >> 8) & 0xFF));
    return 1;
}
OP(op_ror) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    const uint32_t input = R[rdn];
    const uint32_t shift = (R[rm] & 0xFF) % 32;
    const uint32_t result = shift == 0 ? input : ((input >> shift) | (input << (32 - shift)));
    R[rdn] = result;
    set_nz(core, result);
    core.c = (result & 0x80000000u) != 0;
    return 1;
}
OP(op_negs_rsbs) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = static_cast<uint32_t>(subtract_update_flags(core, 0, R[(opcode >> 3) & 0x7]));
    return 1;
}
OP(op_nop) {
    UNUSED_OPERANDS;
    (void)core;
    return 1;
}
OP(op_sbcs_encoding_t1) {
    UNUSED_OPERANDS;
    const uint32_t rm = (opcode >> 3) & 0x7, rdn = opcode & 0x7;
    // The addition is 64-bit on purpose: rm = 0xFFFFFFFF with the carry clear needs the *unwrapped* 0x100000000 to reach
    // subtract_update_flags for its `minuend >= subtrahend` borrow check to come out right.
    R[rdn] = static_cast<uint32_t>(subtract_update_flags(core, R[rdn], static_cast<int64_t>(R[rm]) + (1 - (core.c ? 1 : 0))));
    return 1;
}
OP(op_sev) {
    UNUSED_OPERANDS;
    // ARMv6-M (B1.5.18): SEV sets the Event Register of every PE, including the one executing it (record 0050).
    core.event_registered = true;
    core.log(kCpuLogSev, 0, 0, 0);
    CHECK_FAILED();
    return 1;
}
OP(op_stmia) {
    UNUSED_OPERANDS;
    int delta = 1;
    const uint32_t rn = (opcode >> 8) & 0x7, reg_list = opcode & 0xFF;
    uint32_t address = R[rn];
    for (uint32_t i = 0; i < 8; ++i) {
        if (reg_list & (1u << i)) {
            core.bus().write32(address, R[i]);
            CHECK_FAILED();
            address += 4;
            delta += 1;
        }
    }
    if (!(reg_list & (1u << rn))) R[rn] = address;  // write back
    return delta;
}
OP(op_sub_sp_minus_immediate) {
    UNUSED_OPERANDS;
    R[13] = (R[13] - ((opcode & 0x7F) << 2)) & 0xFFFFFFFCu;
    return 1;
}
OP(op_subs_encoding_t1) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = static_cast<uint32_t>(subtract_update_flags(core, R[(opcode >> 3) & 0x7], (opcode >> 6) & 0x7));
    return 1;
}
OP(op_subs_encoding_t2) {
    UNUSED_OPERANDS;
    const uint32_t rdn = (opcode >> 8) & 0x7;
    R[rdn] = static_cast<uint32_t>(subtract_update_flags(core, R[rdn], opcode & 0xFF));
    return 1;
}
OP(op_subs_register) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = static_cast<uint32_t>(subtract_update_flags(core, R[(opcode >> 3) & 0x7], R[(opcode >> 6) & 0x7]));
    return 1;
}
OP(op_svc) {
    UNUSED_OPERANDS;
    core.pending_svcall = true;
    core.interrupts_updated = true;
    return 1;
}
OP(op_sxtb) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = sign_extend8(R[(opcode >> 3) & 0x7]);
    return 1;
}
OP(op_sxth) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = sign_extend16(R[(opcode >> 3) & 0x7]);
    return 1;
}
OP(op_tst) {
    UNUSED_OPERANDS;
    set_nz(core, R[opcode & 0x7] & R[(opcode >> 3) & 0x7]);
    return 1;
}
OP(op_udf) {
    UNUSED_OPERANDS;
    core.break_rewind = 2;
    core.on_break(opcode & 0xFF);
    CHECK_FAILED();
    return 1;
}
OP(op_udf_encoding_t2) {
    UNUSED_OPERANDS;
    core.break_rewind = 4;
    core.on_break(((opcode & 0xF) << 12) | (opcode2 & 0xFFF));
    CHECK_FAILED();
    R[15] += 2;
    return 1;
}
OP(op_uxtb) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = R[(opcode >> 3) & 0x7] & 0xFF;
    return 1;
}
OP(op_uxth) {
    UNUSED_OPERANDS;
    R[opcode & 0x7] = R[(opcode >> 3) & 0x7] & 0xFFFF;
    return 1;
}
OP(op_wfe) {
    UNUSED_OPERANDS;
    if (core.event_registered) core.event_registered = false;
    else core.waiting = true;
    return 2;
}
OP(op_wfi) {
    UNUSED_OPERANDS;
    core.waiting = true;
    return 2;
}
OP(op_yield) {
    UNUSED_OPERANDS;
    core.log(kCpuLogYield, 0, 0, 0);  // nothing else for now: wait for event
    CHECK_FAILED();
    return 1;
}

inline OpFn match_pattern(uint32_t opcode) noexcept {
    if (opcode >> 6 == 0b0100000101) return op_adcs;
    if (opcode >> 11 == 0b10101) return op_add_register_sp_plus_immediate;
    if (opcode >> 7 == 0b101100000) return op_add_sp_plus_immediate;
    if (opcode >> 9 == 0b0001110) return op_adds_encoding_t1;
    if (opcode >> 11 == 0b00110) return op_adds_encoding_t2;
    if (opcode >> 9 == 0b0001100) return op_adds_register;
    if (opcode >> 8 == 0b01000100) return op_add_register;
    if (opcode >> 11 == 0b10100) return op_adr;
    if (opcode >> 6 == 0b0100000000) return op_ands_encoding_t2;
    if (opcode >> 11 == 0b00010) return op_asrs_immediate;
    if (opcode >> 6 == 0b0100000100) return op_asrs_register;
    if (opcode >> 12 == 0b1101 && ((opcode >> 9) & 0x7) != 0b111) return op_b_with_cond;
    if (opcode >> 11 == 0b11100) return op_b;
    if (opcode >> 6 == 0b0100001110) return op_bics;
    if (opcode >> 8 == 0b10111110) return op_bkpt;
    if (opcode >> 7 == 0b010001111 && (opcode & 0x7) == 0) return op_blx;
    if (opcode >> 7 == 0b010001110 && (opcode & 0x7) == 0) return op_bx;
    if (opcode >> 6 == 0b0100001011) return op_cmn_register;
    if (opcode >> 11 == 0b00101) return op_cmp_immediate;
    if (opcode >> 6 == 0b0100001010) return op_cmp_register;
    if (opcode >> 8 == 0b01000101) return op_cmp_register_encoding_t2;
    if (opcode == 0xB672) return op_cpsid_i;
    if (opcode == 0xB662) return op_cpsie_i;
    if (opcode >> 6 == 0b0100000001) return op_eors;
    if (opcode >> 11 == 0b11001) return op_ldmia;
    if (opcode >> 11 == 0b01101) return op_ldr_immediate;
    if (opcode >> 11 == 0b10011) return op_ldr_sp_immediate;
    if (opcode >> 11 == 0b01001) return op_ldr_literal;
    if (opcode >> 9 == 0b0101100) return op_ldr_register;
    if (opcode >> 11 == 0b01111) return op_ldrb_immediate;
    if (opcode >> 9 == 0b0101110) return op_ldrb_register;
    if (opcode >> 11 == 0b10001) return op_ldrh_immediate;
    if (opcode >> 9 == 0b0101101) return op_ldrh_register;
    if (opcode >> 9 == 0b0101011) return op_ldrsb;
    if (opcode >> 9 == 0b0101111) return op_ldrsh;
    if (opcode >> 11 == 0b00000) return op_lsls_immediate;
    if (opcode >> 6 == 0b0100000010) return op_lsls_register;
    if (opcode >> 11 == 0b00001) return op_lsrs_immediate;
    if (opcode >> 6 == 0b0100000011) return op_lsrs_register;
    if (opcode >> 8 == 0b01000110) return op_mov;
    if (opcode >> 11 == 0b00100) return op_movs;
    if (opcode >> 6 == 0b0100001101) return op_muls;
    if (opcode >> 6 == 0b0100001111) return op_mvns;
    if (opcode >> 6 == 0b0100001100) return op_orrs_encoding_t2;
    if (opcode >> 9 == 0b1011110) return op_pop;
    if (opcode >> 9 == 0b1011010) return op_push;
    if (opcode >> 6 == 0b1011101000) return op_rev;
    if (opcode >> 6 == 0b1011101001) return op_rev16;
    if (opcode >> 6 == 0b1011101011) return op_revsh;
    if (opcode >> 6 == 0b0100000111) return op_ror;
    if (opcode >> 6 == 0b0100001001) return op_negs_rsbs;
    if (opcode == 0b1011111100000000) return op_nop;
    if (opcode >> 6 == 0b0100000110) return op_sbcs_encoding_t1;
    if (opcode == 0b1011111101000000) return op_sev;
    if (opcode >> 11 == 0b11000) return op_stmia;
    if (opcode >> 11 == 0b01100) return op_str_immediate;
    if (opcode >> 11 == 0b10010) return op_str_sp_immediate;
    if (opcode >> 9 == 0b0101000) return op_str_register;
    if (opcode >> 11 == 0b01110) return op_strb_immediate;
    if (opcode >> 9 == 0b0101010) return op_strb_register;
    if (opcode >> 11 == 0b10000) return op_strh_immediate;
    if (opcode >> 9 == 0b0101001) return op_strh_register;
    if (opcode >> 7 == 0b101100001) return op_sub_sp_minus_immediate;
    if (opcode >> 9 == 0b0001111) return op_subs_encoding_t1;
    if (opcode >> 11 == 0b00111) return op_subs_encoding_t2;
    if (opcode >> 9 == 0b0001101) return op_subs_register;
    if (opcode >> 8 == 0b11011111) return op_svc;
    if (opcode >> 6 == 0b1011001001) return op_sxtb;
    if (opcode >> 6 == 0b1011001000) return op_sxth;
    if (opcode >> 6 == 0b0100001000) return op_tst;
    if (opcode >> 8 == 0b11011110) return op_udf;
    if (opcode >> 6 == 0b1011001011) return op_uxtb;
    if (opcode >> 6 == 0b1011001010) return op_uxth;
    if (opcode == 0b1011111100100000) return op_wfe;
    if (opcode == 0b1011111100110000) return op_wfi;
    if (opcode == 0b1011111100010000) return op_yield;
    return nullptr;
}

#undef OP
#undef UNUSED_OPERANDS
#undef R
#undef CHECK_FAILED
#undef LOAD_OP
#undef STORE_OP

}  // namespace cpu_ops

// Opcode range 0xF000-0xF7FF is exclusively owned by these 7 opcode2-dependent conditions (the table below has no entry there). Order
// matters and mirrors the original priority.
inline OpFn Cpu::resolve_wide(uint32_t opcode, uint32_t opcode2) noexcept {
    using namespace cpu_ops;
    if ((opcode >> 11) == 0b11110 && (opcode2 >> 14) == 0b11 && ((opcode2 >> 12) & 0x1) == 1) return op_bl;
    if (opcode == 0xF3BF && ((opcode2 & 0xFFF0) == 0x8F50 || (opcode2 & 0xFFF0) == 0x8F40 || (opcode2 & 0xFFF0) == 0x8F60)) return op_barrier;
    if (opcode == 0b1111001111101111 && (opcode2 >> 12) == 0b1000) return op_mrs;
    if ((opcode >> 4) == 0b111100111000 && (opcode2 >> 8) == 0b10001000) return op_msr;
    if ((opcode >> 4) == 0b111101111111 && (opcode2 >> 12) == 0b1010) return op_udf_encoding_t2;
    return nullptr;
}

// One 64K-entry table per shared object (a function-local static: header-only, built on first use; the initialisation is
// thread-safe by the language rules).
inline const OpFn* Cpu::dispatch_table() noexcept {
    struct Table {
        OpFn entries[0x10000];
        Table() noexcept {
            for (uint32_t opcode = 0; opcode < 0x10000; ++opcode) {
                entries[opcode] = (opcode >= cpu_detail::WIDE_RANGE_START && opcode < cpu_detail::WIDE_RANGE_END)
                                      ? nullptr
                                      : cpu_ops::match_pattern(opcode);
            }
        }
    };
    static const Table table;
    return table.entries;
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_CPU_OPS_HPP
