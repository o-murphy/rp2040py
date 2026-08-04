"""Phase 1 mini-JIT tests (see docs/JIT_BACKLOG.md): pattern detection and the compiled
`__memcpy_slow_lp` block, checked against plain interpretation of the exact same instruction bytes
for equivalence (registers, flags, memory, and cycle count all must match exactly).
"""

import pytest

from rp2040py.jit.engine import JITEngine
from rp2040py.memory_map import RAM_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.utils.assembler import (
    opcode_b_t1,
    opcode_ldmia,
    opcode_ldrb_reg,
    opcode_stmia,
    opcode_strb_reg,
    opcode_subs2,
)

BOOTROM_WORD_COUNT = 4 * 1024


def _pack_word(low16: int, high16: int) -> int:
    return (low16 & 0xFFFF) | ((high16 & 0xFFFF) << 16)


def _memcpy_slow_lp_words(dst_reg: int, src_reg: int, count_reg: int, scratch_reg: int) -> list[int]:
    subs = opcode_subs2(count_reg, 1)
    ldrb = opcode_ldrb_reg(scratch_reg, src_reg, count_reg)
    strb = opcode_strb_reg(scratch_reg, dst_reg, count_reg)
    bne = opcode_b_t1(1, -10)  # NE, branch back 10 bytes (to the subs)
    return [_pack_word(subs, ldrb), _pack_word(strb, bne)]


def _bootrom_with_pattern_at(
    word_offset: int, dst_reg: int, src_reg: int, count_reg: int, scratch_reg: int
) -> list[int]:
    words = [0] * BOOTROM_WORD_COUNT
    pattern = _memcpy_slow_lp_words(dst_reg, src_reg, count_reg, scratch_reg)
    words[word_offset : word_offset + len(pattern)] = pattern
    return words


def _bulk_copy_words(dst_reg: int, src_reg: int, count_reg: int, reg_list: int) -> list[int]:
    byte_step = reg_list.bit_count() * 4
    ldmia = opcode_ldmia(src_reg, reg_list)
    stmia = opcode_stmia(dst_reg, reg_list)
    subs = opcode_subs2(count_reg, byte_step)
    bcs = opcode_b_t1(2, -10)  # CS, branch back 10 bytes (to the ldmia)
    return [_pack_word(ldmia, stmia), _pack_word(subs, bcs)]


def _bootrom_with_bulk_pattern_at(
    word_offset: int, dst_reg: int, src_reg: int, count_reg: int, reg_list: int
) -> list[int]:
    words = [0] * BOOTROM_WORD_COUNT
    pattern = _bulk_copy_words(dst_reg, src_reg, count_reg, reg_list)
    words[word_offset : word_offset + len(pattern)] = pattern
    return words


@pytest.fixture
def jit_rp2040(monkeypatch):
    monkeypatch.setenv("RP2040PY_ENABLE_JIT", "1")
    return RP2040()


def _run_interpreted(monkeypatch, dst_reg, src_reg, count_reg, scratch_reg, dst_base, src_base, count, data):
    monkeypatch.delenv("RP2040PY_ENABLE_JIT", raising=False)
    rp2040 = RP2040()
    entry_pc = 0x100
    rp2040.load_bootrom(_bootrom_with_pattern_at(entry_pc // 4, dst_reg, src_reg, count_reg, scratch_reg))
    for i, byte in enumerate(data):
        rp2040.write_uint8(src_base + i, byte)

    core = rp2040.core
    core.registers[dst_reg] = dst_base
    core.registers[src_reg] = src_base
    core.registers[count_reg] = count
    core.pc = entry_pc
    core.cycles = 0

    exit_pc = entry_pc + 8
    steps = 0
    while core.pc != exit_pc:
        rp2040.step()
        steps += 1
        if steps > count * 8 + 8:
            raise AssertionError("interpreted loop did not terminate as expected")

    return rp2040, core, steps


def _run_jit(monkeypatch, dst_reg, src_reg, count_reg, scratch_reg, dst_base, src_base, count, data):
    monkeypatch.setenv("RP2040PY_ENABLE_JIT", "1")
    rp2040 = RP2040()
    entry_pc = 0x100
    rp2040.load_bootrom(_bootrom_with_pattern_at(entry_pc // 4, dst_reg, src_reg, count_reg, scratch_reg))
    for i, byte in enumerate(data):
        rp2040.write_uint8(src_base + i, byte)

    core = rp2040.core
    core.registers[dst_reg] = dst_base
    core.registers[src_reg] = src_base
    core.registers[count_reg] = count
    core.pc = entry_pc
    core.cycles = 0

    exit_pc = entry_pc + 8
    jit_steps = 0
    while core.pc != exit_pc:
        rp2040.step()
        jit_steps += 1
        if jit_steps > 4:
            raise AssertionError("JIT-enabled loop took more than one interpreted iteration plus the compiled tail")

    return rp2040, core, jit_steps


@pytest.mark.parametrize(
    ("dst_reg", "src_reg", "count_reg", "scratch_reg"),
    [
        (0, 1, 2, 3),  # the real bootrom's own register assignment
        (4, 5, 6, 7),  # different registers - proves detection isn't hardcoded to r0-r3
    ],
)
def test_jit_memcpy_slow_lp_matches_interpretation(monkeypatch, dst_reg, src_reg, count_reg, scratch_reg):
    src_base = RAM_START_ADDRESS + 0x1000
    dst_base = RAM_START_ADDRESS + 0x2000
    count = 37
    data = bytes((i * 7 + 3) & 0xFF for i in range(count))

    ref_rp2040, ref_core, steps = _run_interpreted(
        monkeypatch, dst_reg, src_reg, count_reg, scratch_reg, dst_base, src_base, count, data
    )
    jit_rp2040, jit_core, jit_steps = _run_jit(
        monkeypatch, dst_reg, src_reg, count_reg, scratch_reg, dst_base, src_base, count, data
    )

    assert steps > 1  # sanity: the interpreted reference really did loop multiple times
    # First iteration runs interpreted (subs, ldrb, strb), then the 4th step's bne hands the rest
    # of the loop to the compiled block in one call - regardless of count, never more than 4 steps.
    assert jit_steps == 4

    for i in range(count):
        assert ref_rp2040.read_uint8(dst_base + i) == data[i]
        assert jit_rp2040.read_uint8(dst_base + i) == data[i]

    assert jit_core.registers == ref_core.registers
    assert jit_core.n == ref_core.n
    assert jit_core.z == ref_core.z
    assert jit_core.c == ref_core.c
    assert jit_core.v == ref_core.v
    assert jit_core.cycles == ref_core.cycles


def _run_bulk_interpreted(monkeypatch, dst_reg, src_reg, count_reg, reg_list, dst_base, src_base, count, data):
    monkeypatch.delenv("RP2040PY_ENABLE_JIT", raising=False)
    rp2040 = RP2040()
    entry_pc = 0x100
    rp2040.load_bootrom(_bootrom_with_bulk_pattern_at(entry_pc // 4, dst_reg, src_reg, count_reg, reg_list))
    for i, byte in enumerate(data):
        rp2040.write_uint8(src_base + i, byte)

    core = rp2040.core
    core.registers[dst_reg] = dst_base
    core.registers[src_reg] = src_base
    # The real routine's own pre-loop `sub r2, #byte_step` already happened by the time execution
    # reaches the loop's own entry (the ldmia) - see _BulkCopyBlock's class docstring.
    byte_step = reg_list.bit_count() * 4
    core.registers[count_reg] = count - byte_step
    core.pc = entry_pc
    core.cycles = 0

    exit_pc = entry_pc + 8
    steps = 0
    while core.pc != exit_pc:
        rp2040.step()
        steps += 1
        if steps > (count // byte_step) * 8 + 8:
            raise AssertionError("interpreted loop did not terminate as expected")

    return rp2040, core, steps


def _run_bulk_jit(monkeypatch, dst_reg, src_reg, count_reg, reg_list, dst_base, src_base, count, data):
    monkeypatch.setenv("RP2040PY_ENABLE_JIT", "1")
    rp2040 = RP2040()
    entry_pc = 0x100
    rp2040.load_bootrom(_bootrom_with_bulk_pattern_at(entry_pc // 4, dst_reg, src_reg, count_reg, reg_list))
    for i, byte in enumerate(data):
        rp2040.write_uint8(src_base + i, byte)

    core = rp2040.core
    core.registers[dst_reg] = dst_base
    core.registers[src_reg] = src_base
    byte_step = reg_list.bit_count() * 4
    core.registers[count_reg] = count - byte_step
    core.pc = entry_pc
    core.cycles = 0

    exit_pc = entry_pc + 8
    jit_steps = 0
    while core.pc != exit_pc:
        rp2040.step()
        jit_steps += 1
        if jit_steps > 4:
            raise AssertionError("JIT-enabled loop took more than one interpreted iteration plus the compiled tail")

    return rp2040, core, jit_steps


@pytest.mark.parametrize(
    ("dst_reg", "src_reg", "count_reg", "reg_list"),
    [
        (0, 1, 2, 0b01111000),  # the real bootrom's own assignment: r0/r1/r2, {r3,r4,r5,r6}
        (5, 6, 4, 0b00000111),  # different registers/reg_list - proves detection isn't hardcoded
    ],
)
def test_jit_bulk_copy_matches_interpretation(monkeypatch, dst_reg, src_reg, count_reg, reg_list):
    byte_step = reg_list.bit_count() * 4
    src_base = RAM_START_ADDRESS + 0x1000
    dst_base = RAM_START_ADDRESS + 0x2000
    count = byte_step * 5  # 5 full blocks
    data = bytes((i * 11 + 5) & 0xFF for i in range(count))

    ref_rp2040, ref_core, steps = _run_bulk_interpreted(
        monkeypatch, dst_reg, src_reg, count_reg, reg_list, dst_base, src_base, count, data
    )
    jit_rp2040, jit_core, jit_steps = _run_bulk_jit(
        monkeypatch, dst_reg, src_reg, count_reg, reg_list, dst_base, src_base, count, data
    )

    assert steps > 1  # sanity: the interpreted reference really did loop multiple times
    assert jit_steps == 4

    for i in range(count):
        assert ref_rp2040.read_uint8(dst_base + i) == data[i]
        assert jit_rp2040.read_uint8(dst_base + i) == data[i]

    assert jit_core.registers == ref_core.registers
    assert jit_core.n == ref_core.n
    assert jit_core.z == ref_core.z
    assert jit_core.c == ref_core.c
    assert jit_core.v == ref_core.v
    assert jit_core.cycles == ref_core.cycles


def test_jit_bulk_copy_ignores_non_matching_code(jit_rp2040):
    # STMIA uses a different register list than LDMIA - must not be detected.
    entry_pc = 0x100
    words = [0] * BOOTROM_WORD_COUNT
    ldmia = opcode_ldmia(1, 0b01111000)
    stmia = opcode_stmia(0, 0b00111000)  # NOT the same reg_list
    subs = opcode_subs2(2, 16)
    bcs = opcode_b_t1(2, -10)
    words[entry_pc // 4 : entry_pc // 4 + 2] = [_pack_word(ldmia, stmia), _pack_word(subs, bcs)]
    jit_rp2040.load_bootrom(words)

    assert jit_rp2040.jit is not None
    assert entry_pc not in jit_rp2040.jit._blocks


def test_jit_bulk_copy_write_invalidates_cached_block(jit_rp2040):
    entry_pc = 0x100
    jit_rp2040.load_bootrom(_bootrom_with_bulk_pattern_at(entry_pc // 4, 0, 1, 2, 0b01111000))
    assert entry_pc in jit_rp2040.jit._blocks

    jit_rp2040.write_uint32(entry_pc, 0)

    assert entry_pc not in jit_rp2040.jit._blocks


def test_jit_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RP2040PY_ENABLE_JIT", raising=False)
    rp2040 = RP2040()
    assert rp2040.jit is None
    # No instance-level override bound - _fetch_decode_execute resolves to the plain, unmodified
    # class method, and dispatch uses the shared module-level table (see CortexM0Core.__init__'s
    # branch-only method-swap, only applied when JIT is enabled).
    assert "_fetch_decode_execute" not in vars(rp2040.core)
    assert "_dispatch_table" not in vars(rp2040.core)


def test_jit_enabled_binds_instruction_hook(jit_rp2040):
    assert jit_rp2040.jit is not None
    assert "_fetch_decode_execute" in vars(jit_rp2040.core)
    assert "_dispatch_table" in vars(jit_rp2040.core)


def test_jit_ignores_non_matching_code(jit_rp2040):
    # A superficially similar loop that decrements by 2 instead of 1 - must not be detected, since
    # the compiled block only replicates the exact `subs #1` semantics.
    entry_pc = 0x100
    words = [0] * BOOTROM_WORD_COUNT
    subs = opcode_subs2(2, 2)  # subs r2, #2 - NOT the pattern
    ldrb = opcode_ldrb_reg(3, 1, 2)
    strb = opcode_strb_reg(3, 0, 2)
    bne = opcode_b_t1(1, -10)
    words[entry_pc // 4 : entry_pc // 4 + 2] = [_pack_word(subs, ldrb), _pack_word(strb, bne)]
    jit_rp2040.load_bootrom(words)

    assert jit_rp2040.jit is not None
    assert entry_pc not in jit_rp2040.jit._blocks


def test_jit_declines_on_zero_count(jit_rp2040):
    entry_pc = 0x100
    jit_rp2040.load_bootrom(_bootrom_with_pattern_at(entry_pc // 4, 0, 1, 2, 3))
    core = jit_rp2040.core
    core.registers[0] = RAM_START_ADDRESS
    core.registers[1] = RAM_START_ADDRESS + 0x100
    core.registers[2] = 0  # n == 0 - real hardware would underflow, so this must decline, not crash
    core.pc = entry_pc

    block = jit_rp2040.jit._blocks[entry_pc]
    assert block.run(core) is None


def test_jit_write_invalidates_cached_block(jit_rp2040):
    entry_pc = 0x100
    jit_rp2040.load_bootrom(_bootrom_with_pattern_at(entry_pc // 4, 0, 1, 2, 3))
    assert entry_pc in jit_rp2040.jit._blocks

    jit_rp2040.write_uint32(entry_pc, 0)  # clobber one of the loop's own instructions

    assert entry_pc not in jit_rp2040.jit._blocks


def test_jit_engine_scan_finds_all_patterns_once():
    words = [0] * BOOTROM_WORD_COUNT
    pattern_a = _memcpy_slow_lp_words(0, 1, 2, 3)
    pattern_b = _memcpy_slow_lp_words(4, 5, 6, 7)
    words[0x40:0x42] = pattern_a
    words[0x80:0x82] = pattern_b

    engine = JITEngine()
    engine.load(lambda pc: (words[pc // 4] >> (16 if pc % 4 else 0)) & 0xFFFF, len(words))

    assert set(engine._blocks) == {0x40 * 4, 0x80 * 4}
