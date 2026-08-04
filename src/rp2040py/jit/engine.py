"""Basic-block-fusion mini-JIT - see docs/JIT_BACKLOG.md for the full plan. This module is only
imported when RP2040PY_ENABLE_JIT=1 is set (see RP2040.__init__ in rp2040.py); with the flag
unset, `RP2040.jit` stays `None` and this file is never touched.

Phase 1 (see docs/JIT_BACKLOG.md): real codegen for two known, fixed loops - both confirmed
against bootrom_misc.S in raspberrypi/pico-bootrom-rp2040, not just reverse-engineered from raw
bytes:

- `__memcpy_slow_lp`, the byte-at-a-time tail handler (`subs count,#1; ldrb scratch,[?];
  strb scratch,[?]; bne` back to the `subs`) - validated in isolation first (~13x on CPython,
  ~17x on PyPy), then measured net negative twice once integrated (see docs/JIT_BACKLOG.md): this
  loop only ever accounts for ~0.1% of a real boot's instructions, so no integration overhead was
  ever going to be repaid by speeding it up alone.
- `_memcpy_aligned`'s 16-byte bulk loop (`ldmia src!,{...}; stmia dst!,{...}; subs count,#16; bcs`
  back to the `ldmia`) - the actual dominant copy path `__memcpy_slow_lp` is only the tail of.

Detection for both is by decoding the actual instruction bits at candidate addresses (register
assignment and location both derived from what's decoded, not hardcoded) - not a byte-signature
match the way the HLE memcpy hook works, since generating correct code needs to know *which*
registers each loop uses, not just "is this routine present."

Every other pattern still falls through to normal interpretation untouched: this only ever
recognizes these two specific loop shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.cortex_m0_core import PC_REGISTER

if TYPE_CHECKING:
    from rp2040py.cortex_m0_core import CortexM0Core

__all__ = ("JITEngine",)

ReadUint16 = Callable[[int], int]

# SUBS (immediate, encoding T2): 00111 Rdn(3) imm8(8)
_SUBS_T2_MASK = 0b11111_000_00000000
_SUBS_T2_VALUE = 0b00111_000_00000000
# LDRB (register): 0101110 Rm(3) Rn(3) Rt(3)
_LDRB_REG_MASK = 0b1111111_000_000_000
_LDRB_REG_VALUE = 0b0101110_000_000_000
# STRB (register): 0101010 Rm(3) Rn(3) Rt(3)
_STRB_REG_MASK = 0b1111111_000_000_000
_STRB_REG_VALUE = 0b0101010_000_000_000
# B (with cond): 1101 cond(4) imm8(8)
_B_COND_MASK = 0b1111_0000_00000000
_B_COND_VALUE = 0b1101_0000_00000000
_COND_NE = 1
_COND_CS = 2
# LDMIA: 11001 Rn(3) register_list(8)
_LDMIA_MASK = 0b11111_000_00000000
_LDMIA_VALUE = 0b11001_000_00000000
# STMIA: 11000 Rn(3) register_list(8)
_STMIA_MASK = 0b11111_000_00000000
_STMIA_VALUE = 0b11000_000_00000000


class _MemcpySlowLpBlock:
    """A compiled stand-in for one occurrence of the `__memcpy_slow_lp` pattern, bound to the
    specific registers it was found using at `entry_pc`. `run()` performs the exact same
    architectural work as four interpreted Thumb instructions per byte (same bus calls, same
    flag/register/PC end state, same total cycle count) - just without paying per-instruction
    fetch/decode/dispatch overhead for the interpreted loop body on every byte.
    """

    __slots__ = ("count_reg", "dst_reg", "entry_pc", "exit_pc", "scratch_reg", "src_reg")

    def __init__(self, dst_reg: int, src_reg: int, count_reg: int, scratch_reg: int, entry_pc: int) -> None:
        self.dst_reg = dst_reg
        self.src_reg = src_reg
        self.count_reg = count_reg
        self.scratch_reg = scratch_reg
        self.entry_pc = entry_pc
        self.exit_pc = (entry_pc + 8) & 0xFFFFFFFF

    def run(self, core: CortexM0Core) -> int | None:
        """Returns the elapsed cycle count on success, or None to defer to normal per-instruction
        interpretation for this one call (safe fallback - the loop's own state hasn't been touched
        yet at that point, so falling back just re-does the work the slow way this one time).

        Declines (returns None) rather than risk an inaccurate cycle count in two cases: `n == 0`
        (the real hardware's own behavior here is to underflow into a very long loop - not this
        method's place to special-case what's really a caller bug upstream, so let the interpreter
        do exactly what it would always do), and a copy whose source or destination range crosses
        between address regions with different per-access timing (see CortexM0Core.cycles_io) -
        never observed from this routine's real callers (TinyUSB/littlefs copies stay within one
        RAM or flash range), but not assumed impossible either.
        """
        registers = core.registers
        n = registers[self.count_reg]
        if n == 0:
            return None

        rp2040 = core.rp2040
        dst_base = registers[self.dst_reg]
        src_base = registers[self.src_reg]

        io_src_first = core.cycles_io(src_base)
        io_src_last = core.cycles_io((src_base + n - 1) & 0xFFFFFFFF)
        io_dst_first = core.cycles_io(dst_base, True)
        io_dst_last = core.cycles_io((dst_base + n - 1) & 0xFFFFFFFF, True)
        if io_src_first != io_src_last or io_dst_first != io_dst_last:
            return None

        read_uint8 = rp2040.read_uint8
        write_uint8 = rp2040.write_uint8
        scratch = registers[self.scratch_reg]
        remaining = n
        while remaining:
            remaining -= 1
            scratch = read_uint8((src_base + remaining) & 0xFFFFFFFF)
            write_uint8((dst_base + remaining) & 0xFFFFFFFF, scratch)

        registers[self.count_reg] = 0
        registers[self.scratch_reg] = scratch
        registers[PC_REGISTER] = self.exit_pc
        # Same end state as CortexM0Core._subtract_update_flags(1, 1) - the final `subs`
        # (count_reg going from 1 to 0) is always the last one to run, regardless of n.
        core.n = False
        core.z = True
        core.c = True
        core.v = False

        # Unlike a standalone hook (e.g. CortexM0Core._hle_memcpy()), this is only ever invoked
        # from CortexM0Core._op_b_with_cond_jit() - an ordinary dispatch-table handler - so its
        # return value flows back through the normal handler contract: the caller
        # (_fetch_decode_execute_jit) adds it to core.cycles itself. Adding it here too would
        # double-count.
        return n * (5 + io_src_first + io_dst_first) - 1


def _decode_memcpy_slow_lp(read_uint16: ReadUint16, pc: int) -> _MemcpySlowLpBlock | None:
    """Attempts to decode the `__memcpy_slow_lp` pattern starting at `pc`. `read_uint16` is
    `RP2040.read_uint16`, passed in rather than imported (importing rp2040.py here would import
    this package right back - see rp2040.py's own lazy-import comment on the HLE signature scan
    for the same reason) - only ever called at scan time (see JITEngine.load below), never on the
    per-instruction hot path.
    """
    op0 = read_uint16(pc)
    if op0 & _SUBS_T2_MASK != _SUBS_T2_VALUE or (op0 & 0xFF) != 1:
        return None
    count_reg = (op0 >> 8) & 0x7

    op1 = read_uint16(pc + 2)
    if op1 & _LDRB_REG_MASK != _LDRB_REG_VALUE:
        return None
    ldrb_rm = (op1 >> 6) & 0x7
    ldrb_rn = (op1 >> 3) & 0x7
    scratch_reg = op1 & 0x7
    if count_reg == ldrb_rm:
        src_reg = ldrb_rn
    elif count_reg == ldrb_rn:
        src_reg = ldrb_rm
    else:
        return None

    op2 = read_uint16(pc + 4)
    if op2 & _STRB_REG_MASK != _STRB_REG_VALUE or (op2 & 0x7) != scratch_reg:
        return None
    strb_rm = (op2 >> 6) & 0x7
    strb_rn = (op2 >> 3) & 0x7
    if count_reg == strb_rm:
        dst_reg = strb_rn
    elif count_reg == strb_rn:
        dst_reg = strb_rm
    else:
        return None

    op3 = read_uint16(pc + 6)
    if op3 & _B_COND_MASK != _B_COND_VALUE or (op3 >> 8) & 0xF != _COND_NE:
        return None
    imm8 = (op3 & 0xFF) << 1
    if imm8 & (1 << 8):
        imm8 = (imm8 & 0x1FF) - 0x200
    branch_pc = pc + 6
    target = (branch_pc + 4 + imm8) & 0xFFFFFFFF
    if target != pc:
        return None

    if len({count_reg, scratch_reg, src_reg, dst_reg}) != 4:
        return None

    return _MemcpySlowLpBlock(dst_reg, src_reg, count_reg, scratch_reg, pc)


class _BulkCopyBlock:
    """A compiled stand-in for one occurrence of `_memcpy_aligned`'s 16-byte bulk loop
    (`ldmia src!,{...}; stmia dst!,{...}; subs count,#byte_step; bcs` back to the `ldmia`).

    Confirmed against bootrom_misc.S: the loop always transfers unconditionally *before* checking
    whether another block remains, but a single `sub r2, #byte_step` immediately before the loop
    (not part of the repeating body this class matches) already accounts for one iteration's worth
    of budget, so the loop as a whole transfers exactly `original_count // byte_step` blocks - no
    off-by-one over-copy despite the transfer-then-check order. Since `byte_step` is a power-of-two
    multiple of 4 (never touching bits 0-3 of `count`... down to whichever low bits `byte_step`
    itself doesn't cover), `count`'s low bits are invariant across every iteration of this loop and
    whatever comes after it - not depended on here, but why the arithmetic below is safe to trust
    against the real routine's own remainder-handling code that runs after this loop exits.
    """

    __slots__ = ("byte_step", "count_reg", "dst_reg", "entry_pc", "exit_pc", "num_regs", "reg_list", "src_reg")

    def __init__(self, dst_reg: int, src_reg: int, count_reg: int, reg_list: int, entry_pc: int) -> None:
        self.dst_reg = dst_reg
        self.src_reg = src_reg
        self.count_reg = count_reg
        self.reg_list = reg_list
        self.num_regs = reg_list.bit_count()
        self.byte_step = self.num_regs * 4
        self.entry_pc = entry_pc
        self.exit_pc = (entry_pc + 8) & 0xFFFFFFFF

    def run(self, core: CortexM0Core) -> int:
        """Unlike _MemcpySlowLpBlock.run(), this never declines: LDMIA/STMIA in this codebase's
        own cost model (CortexM0Core._op_ldmia/_op_stmia) never call cycles_io() - a flat
        `1 + registers-transferred` cost regardless of address - so there's no region-boundary case
        to bail out on, and `count == 0` is a normal, expected state here (see the class docstring:
        the loop transfers unconditionally *before* checking for more, so `count == 0` still means
        "one more full block, then done" rather than the caller-bug case _MemcpySlowLpBlock guards
        against).
        """
        registers = core.registers
        rp2040 = core.rp2040
        byte_step = self.byte_step
        n = registers[self.count_reg]

        iterations = n // byte_step + 1
        total_bytes = iterations * byte_step
        dst_base = registers[self.dst_reg]
        src_base = registers[self.src_reg]

        rp2040.bus_copy(dst_base, src_base, total_bytes)

        new_dst = (dst_base + total_bytes) & 0xFFFFFFFF
        new_src = (src_base + total_bytes) & 0xFFFFFFFF
        registers[self.dst_reg] = new_dst
        registers[self.src_reg] = new_src
        registers[self.count_reg] = (n - total_bytes) & 0xFFFFFFFF

        # The last LDMIA's own registers, in increasing register-number (== increasing address)
        # order, retain whatever it last loaded - matching real LDMIA semantics after the loop.
        address = (new_src - byte_step) & 0xFFFFFFFF
        for reg in range(8):
            if self.reg_list & (1 << reg):
                registers[reg] = rp2040.read_uint32(address)
                address = (address + 4) & 0xFFFFFFFF

        registers[PC_REGISTER] = self.exit_pc
        # The loop's own final `subs count, #byte_step` always subtracts byte_step from a value in
        # [0, byte_step) (see class docstring) - minuend < subtrahend, and both are far below
        # 0x80000000, so CortexM0Core._subtract_update_flags's result is the same shape regardless
        # of count or byte_step: negative (n set), nonzero (z clear), a borrow (c clear), no
        # signed overflow (v clear).
        core.n = True
        core.z = False
        core.c = False
        core.v = False

        return iterations * (2 * self.num_regs + 5) - 1


def _decode_bulk_copy(read_uint16: ReadUint16, pc: int) -> _BulkCopyBlock | None:
    """Attempts to decode `_memcpy_aligned`'s 16-byte bulk-copy loop starting at `pc` (the LDMIA).
    See _decode_memcpy_slow_lp's docstring for why `read_uint16` is passed in rather than imported.
    """
    op0 = read_uint16(pc)
    if op0 & _LDMIA_MASK != _LDMIA_VALUE:
        return None
    src_reg = (op0 >> 8) & 0x7
    reg_list = op0 & 0xFF
    if reg_list == 0 or reg_list & (1 << src_reg):
        return None

    op1 = read_uint16(pc + 2)
    if op1 & _STMIA_MASK != _STMIA_VALUE:
        return None
    dst_reg = (op1 >> 8) & 0x7
    if (op1 & 0xFF) != reg_list or dst_reg == src_reg or reg_list & (1 << dst_reg):
        return None

    op2 = read_uint16(pc + 4)
    if op2 & _SUBS_T2_MASK != _SUBS_T2_VALUE:
        return None
    count_reg = (op2 >> 8) & 0x7
    if count_reg == src_reg or count_reg == dst_reg or reg_list & (1 << count_reg):
        return None
    byte_step = reg_list.bit_count() * 4
    if (op2 & 0xFF) != byte_step:
        return None

    op3 = read_uint16(pc + 6)
    if op3 & _B_COND_MASK != _B_COND_VALUE or (op3 >> 8) & 0xF != _COND_CS:
        return None
    imm8 = (op3 & 0xFF) << 1
    if imm8 & (1 << 8):
        imm8 = (imm8 & 0x1FF) - 0x200
    branch_pc = pc + 6
    target = (branch_pc + 4 + imm8) & 0xFFFFFFFF
    if target != pc:
        return None

    return _BulkCopyBlock(dst_reg, src_reg, count_reg, reg_list, pc)


class JITEngine:
    """Owns the compiled-block cache and the two pattern detectors. `load()` is called once per
    `RP2040.load_bootrom()` (mirroring `_find_hle_memcpy_entries` - see rp2040.py), never on the
    per-instruction path; `try_execute()`/`on_write()` are the two hot-path entry points described
    in docs/JIT_BACKLOG.md's architecture section.
    """

    def __init__(self) -> None:
        self._blocks: dict[int, _MemcpySlowLpBlock | _BulkCopyBlock] = {}

    def load(self, read_uint16: ReadUint16, word_count: int) -> None:
        """Scans every 2-byte-aligned offset in the loaded bootrom for either known pattern and
        caches whatever matches - cheap relative to a full bootrom load (a few thousand candidate
        offsets, each a handful of integer comparisons), and, like the HLE signature scan, nowhere
        near the hot per-instruction path. The two patterns can't both match the same `pc` (their
        first instruction's opcode ranges don't overlap), so trying both here is unambiguous.
        """
        self._blocks.clear()
        byte_count = word_count * 4
        for pc in range(0, byte_count - 6, 2):
            block: _MemcpySlowLpBlock | _BulkCopyBlock | None = _decode_memcpy_slow_lp(read_uint16, pc)
            if block is None:
                block = _decode_bulk_copy(read_uint16, pc)
            if block is not None:
                self._blocks[pc] = block

    def try_execute(self, core: CortexM0Core, pc: int) -> int | None:
        """Called from CortexM0Core._op_b_with_cond_jit() when a taken conditional branch's target
        is `pc` - see that method's docstring for why the check lives on the branch path rather
        than on every instruction (an earlier version did the latter and measured net negative -
        see docs/JIT_BACKLOG.md). Returns the elapsed cycle count on a compiled-block hit, or None
        to let the branch complete normally (no block cached for `pc`, or the cached block itself
        declined - see _MemcpySlowLpBlock.run above).
        """
        block = self._blocks.get(pc)
        if block is None:
            return None
        return block.run(core)

    def on_write(self, address: int, length: int) -> None:
        """Evicts any cached block whose own instruction bytes - not the data it copies, which is
        irrelevant to cache validity - overlap [address, address + length). Self-modifying code
        touching one of these four instructions is not something real firmware is expected to do
        (this only ever matches inside the bootrom, which real firmware also never writes to), but
        the bus technically allows writing there, so this stays correct rather than assuming it.
        """
        if not self._blocks:
            return
        end = address + length
        stale = [pc for pc in self._blocks if address < pc + 8 and pc < end]
        for pc in stale:
            del self._blocks[pc]
