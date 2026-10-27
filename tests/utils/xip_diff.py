"""A lockstep differential oracle for XIP_CTRL and the XIP subsystem: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md ("XIP_CTRL and the XIP subsystem"). The reference side is the *pure-Python chip* (``rp2040py._rp2040.RP2040``): its bus,
its XIP block (``peripherals/_xip_ctrl.py``), its DMA - the whole path a flash access takes. The candidate is whatever ``rp2040py.rp2040.RP2040`` gives (the native chip with its C++ memory map hooks, window and
block when the extensions are built, the same pure chip otherwise). Both are fed one stream of operations:

* **reads and writes of the flash window** at every width and all four aliases, from a small pool of addresses chosen to land in the same sets (two ways, a third tag to evict) and the same lines, plus the ends of
  the 24-bit flash address space; writes must never change the image;
* **every register** (CTRL, FLUSH, STAT, the counters, STREAM_*) and offsets past the block, through the bus and its XOR/SET/CLR aliases;
* the **cache-as-SRAM** segment (``0x15...``): every width, the mirrors, with the cache enabled, disabled and powered down;
* the **auxiliary slave** XIP_AUX, SSI.BAUDR (the stream's pace), pokes of the flash image (so the streamed words differ), counters pushed to their saturation, and a DMA channel paced by ``DREQ_XIP_STREAM`` that
  drains XIP_AUX into memory;
* **simulated time** (``clock.tick()``) and the block's ``reset()``.

After **each** step: the register file read through the bus (the ones a read does not change), the block's state (the tag array, the replacement bits, the data RAM, the FIFO, the flush clock), the DMA channel and the
words it wrote, the clock, and the ordered timestamped log of what the model reported; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference are made by changing its *source text* (``MUTATIONS``) and loading the result as a module of its own, so a mutant is exactly the reference with one expression different.
"""

import gc
import inspect
import random
import types
from collections.abc import Callable
from typing import Any

import rp2040py._cortex_m0_core as pure_core
from rp2040py import _rp2040
from rp2040py.peripherals import _xip_ctrl as P
from rp2040py.rp2040 import RP2040
from utils.is32bit import IS32BIT

XIP_CTRL_BASE = 0x14000000
XIP_AUX = 0x50400000
SRAM = 0x15000000
SSI_BAUDR = 0x18000014
DMA_BASE = 0x50000000
DEST = 0x20010000
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTERS = (P.CTRL, P.CTR_HIT, P.CTR_ACC, P.STREAM_ADDR, P.STREAM_CTR)  # a read of these changes nothing
ALL_OFFSETS = (P.CTRL, P.FLUSH, P.STAT, P.CTR_HIT, P.CTR_ACC, P.STREAM_ADDR, P.STREAM_CTR, P.STREAM_FIFO)
PAST_THE_BLOCK = (0x20, 0x40, 0x800, 0x2000 - 0x1000, 0xFFC, 0x1)
TICKS = (0, 1, 64, 512, 1_000, 2_048, 4_000, 8_000, 8_208, 8_300, 16_000, 100_000)
SETS_USED = (0, 1, 2, 5, 1023)
SPANS = (0x000000, 0x800000, 0xFFE000)  # the low, the middle and the very end of the 24-bit flash address space


def _pure_chip() -> Any:
    """The pure-Python chip. It builds its CPU through the facade, which hands back the *native* core whenever the extension is installed - and a native core only accepts a native chip - so it is given the
    pure core, as the RP2040PY_SKIP_CYTHON=1 build would."""
    saved, _rp2040.CortexM0Core = _rp2040.CortexM0Core, pure_core.CortexM0Core
    try:
        return _rp2040.RP2040()
    finally:
        _rp2040.CortexM0Core = saved


class Rig:
    """One chip plus the timestamped log of everything the XIP model reported."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = _pure_chip() if kind in ("pure", "mutant") else RP2040()
        self.log: list[tuple] = []
        if kind == "mutant":
            assert factory is not None
            module = factory
            new, aux = module.RPXIPCtrl(self.chip, "XIP_CTRL_BASE"), module.RPXIPAux(self.chip, "XIP_AUX_BASE")
            self.chip.xip_ctrl = new
            self.chip.peripherals[0x14000] = new
            self.chip.peripherals[0x50400] = aux
        self._tap()

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", chip.clock.nanos, _m, str(name), str(message))),
            )

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "fread":
            return int((chip.read_uint32, chip.read_uint16, chip.read_uint8)[(4, 2, 1).index(op[2])](op[1]))
        if kind == "fwrite":
            (chip.write_uint32, chip.write_uint16, chip.write_uint8)[(4, 2, 1).index(op[2])](op[1], op[3])
        elif kind == "reg_read":
            return int(chip.read_uint32(XIP_CTRL_BASE + op[2] + op[1]))
        elif kind == "reg_write":
            chip.write_uint32(XIP_CTRL_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "sread":
            return int((chip.read_uint32, chip.read_uint16, chip.read_uint8)[(4, 2, 1).index(op[2])](SRAM + op[1]))
        elif kind == "swrite":
            (chip.write_uint32, chip.write_uint16, chip.write_uint8)[(4, 2, 1).index(op[2])](SRAM + op[1], op[3])
        elif kind == "aux":
            return int(chip.read_uint32(XIP_AUX))
        elif kind == "baudr":
            chip.write_uint32(SSI_BAUDR, op[1])
        elif kind == "poke":
            chip.flash[op[1] : op[1] + len(op[2])] = op[2]
        elif kind == "saturate":
            chip.xip_ctrl.set_counter(op[1], 0xFFFFFFFF - op[2])
        elif kind == "dma":
            chip.write_uint32(DMA_BASE + 0x00, XIP_AUX)
            chip.write_uint32(DMA_BASE + 0x04, DEST)
            chip.write_uint32(DMA_BASE + 0x08, op[1])
            chip.write_uint32(DMA_BASE + 0x0C, 1 | (2 << 2) | (1 << 5) | (P.DREQ_XIP_STREAM << 15))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "reset":
            chip.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, x = self.chip, self.chip.xip_ctrl
        return {
            "regs": tuple(int(chip.read_uint32(XIP_CTRL_BASE + offset)) for offset in (*REGISTERS, P.STAT)),
            "state": tuple((name, value) for name, value in sorted(x.snapshot().items())),
            "dma": (
                int(chip.read_uint32(DMA_BASE + 0x08)),
                int(chip.read_uint32(DMA_BASE + 0x0C)),
                tuple(int(chip.read_uint32(DEST + 4 * n)) for n in range(8)),
            ),
            "flash_head": bytes(chip.flash[0:16]),
            "chip": (float(chip.clock.nanos), float(chip.clk_sys)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _xip_ctrl.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    # -- counters
    "acc_wraps": ("        if self.ctr_acc < COUNTER_MAX:\n            self.ctr_acc += 1\n", "        self.ctr_acc = (self.ctr_acc + 1) & COUNTER_MAX\n", 1),
    "hit_wraps": ("            if self.ctr_hit < COUNTER_MAX:\n                self.ctr_hit += 1\n", "            self.ctr_hit = (self.ctr_hit + 1) & COUNTER_MAX\n", 1),
    "acc_only_counts_with_the_cache_on": ("        if self.ctr_acc < COUNTER_MAX:\n", "        if self.ctr_acc < COUNTER_MAX and self.ctrl & CTRL_EN:\n", 1),
    "hit_write_does_not_clear": ("            self.ctr_hit = 0  # WC: any value clears", "            pass", 1),
    "acc_write_does_not_clear": ("        elif offset == CTR_ACC:\n            self.ctr_acc = 0\n", "        elif offset == CTR_ACC:\n            pass\n", 1),
    "reset_keeps_counters": ("        self.ctr_hit = 0\n        self.ctr_acc = 0\n        self.stream_addr = 0\n", "        self.stream_addr = 0\n", 1),
    # -- aliases and lookup
    "bypass_alias_cached": ("        if not self.ctrl & CTRL_EN or alias == ALIAS_BYPASS:\n            return\n", "        if not self.ctrl & CTRL_EN:\n            return\n", 1),
    "cache_off_still_caches": ("        if not self.ctrl & CTRL_EN or alias == ALIAS_BYPASS:\n            return\n", "        if alias == ALIAS_BYPASS:\n            return\n", 1),
    "noalloc_allocates": ("        elif alias == ALIAS_ALLOCATING:\n            self._allocate(index, tag)\n", "        else:\n            self._allocate(index, tag)\n", 1),
    "nocache_checks_for_a_hit": ("        if alias == ALIAS_NOCACHE:  # \"Don't check for hit, always update cache\"", "        if False:", 1),
    "nocache_counts_a_hit": ("            if way == INVALID:\n                way = self._allocate(index, tag)\n            self.recent[index] = way\n            return\n", "            if way == INVALID:\n                way = self._allocate(index, tag)\n            elif self.ctr_hit < COUNTER_MAX:\n                self.ctr_hit += 1\n            self.recent[index] = way\n            return\n", 1),
    "nocache_leaves_recent": ("            self.recent[index] = way\n            return\n", "            return\n", 1),
    "hit_leaves_recent": ("            self.recent[index] = way\n        elif alias == ALIAS_ALLOCATING:", "        elif alias == ALIAS_ALLOCATING:", 1),
    "alias_bits_wrong": ("alias = (address >> 24) & 3", "alias = (address >> 24) & 1", 2),
    "line_is_16_bytes": ("LINE_SHIFT = 3", "LINE_SHIFT = 4", 1),
    "only_512_sets": ("SETS = 1024", "SETS = 512", 1),
    "tag_loses_a_bit": ("tag = line >> 10", "tag = line >> 11", 2),
    "flash_mask_23_bits": ("FLASH_MASK = 0x00FFFFFF", "FLASH_MASK = 0x007FFFFF", 1),
    "lookup_only_way_0": ("        way = 0 if ways[0] == tag else 1 if ways[1] == tag else INVALID\n", "        way = 0 if ways[0] == tag else INVALID\n", 1),
    # -- replacement
    "allocates_way_1_first": ("        way = 0 if ways[0] == INVALID else 1 if ways[1] == INVALID else 1 - self.recent[index]", "        way = 1 if ways[1] == INVALID else 0 if ways[0] == INVALID else 1 - self.recent[index]", 1),
    "replaces_the_most_recent": ("else 1 - self.recent[index]", "else self.recent[index]", 1),
    "allocation_leaves_recent": ("        ways[way] = tag\n        self.recent[index] = way\n        return way", "        ways[way] = tag\n        return way", 1),
    # -- writes
    "write_invalidates_only_way_0": ("            if ways[1] == tag:\n                ways[1] = INVALID\n", "", 1),
    "write_invalidates_every_alias": ("        if alias == ALIAS_ALLOCATING:\n            line = (address", "        if True:\n            line = (address", 1),
    "write_warns_without_badwrite": ("        elif self.ctrl & CTRL_ERR_BADWRITE:\n", "        else:\n", 1),
    "write_warns_on_the_allocating_alias_too": ("        elif self.ctrl & CTRL_ERR_BADWRITE:\n", "        if self.ctrl & CTRL_ERR_BADWRITE:\n", 1),
    # -- flush
    "flush_is_1024_cycles": ("FLUSH_CYCLES = 1026", "FLUSH_CYCLES = 1024", 1),
    "flush_ready_strict": ("return self.rp2040.clock.ticks >= self.flush_done_ticks", "return self.rp2040.clock.ticks > self.flush_done_ticks", 1),
    "flush_keeps_the_tags": ("        self.tags = [[INVALID, INVALID] for _ in range(SETS)]\n        self.flush_done_ticks", "        self.flush_done_ticks", 1),
    "flush_read_does_not_complete": ("            self.flush_done_ticks = min(self.flush_done_ticks, self.rp2040.clock.ticks)\n", "            pass\n", 1),
    "flush_read_returns_one": ("            return 0\n        if offset == STAT:", "            return 1\n        if offset == STAT:", 1),
    "flush_write_of_zero_flushes": ("            if value & 1:\n                self._start_flush()", "            if True:\n                self._start_flush()", 1),
    "flush_ready_bit_moved": ("STAT_FLUSH_READY = 1 << 0", "STAT_FLUSH_READY = 1 << 3", 1),
    # -- CTRL
    "ctrl_reserved_bit_kept": ("CTRL_MASK = CTRL_EN | CTRL_ERR_BADWRITE | CTRL_POWER_DOWN", "CTRL_MASK = CTRL_EN | CTRL_ERR_BADWRITE | CTRL_POWER_DOWN | 4", 1),
    "power_down_keeps_en": ("                self.ctrl &= ~CTRL_EN  # \"Writing 1 to this bit forces CTRL_EN to 0\"\n", "                pass\n", 1),
    "ctrl_reset_without_err_badwrite": ("CTRL_RESET = CTRL_EN | CTRL_ERR_BADWRITE", "CTRL_RESET = CTRL_EN", 1),
    "reset_keeps_ctrl": ("        self.ctrl = CTRL_RESET\n", "", 1),
    # -- the cache as SRAM
    "sram_ignores_power_down": ("return not self.ctrl & (CTRL_EN | CTRL_POWER_DOWN)", "return not self.ctrl & CTRL_EN", 1),
    "sram_ignores_enable": ("return not self.ctrl & (CTRL_EN | CTRL_POWER_DOWN)", "return not self.ctrl & CTRL_POWER_DOWN", 1),
    "sram_is_8k": ("SRAM_SIZE = 16 * 1024", "SRAM_SIZE = 8 * 1024", 1),
    "sram_read_error_silent": ('            self.warn(\n                f"Read of the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)"\n            )\n', "", 1),
    "sram_write_error_silent": ('            self.warn(\n                f"Write to the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)"\n            )\n', "", 1),
    "sram_write_error_stored": ('(a bus error)"\n            )\n            return\n', '(a bus error)"\n            )\n', 1),
    "sram_cleared_by_reset": ("        self.recent = [0] * SETS  # per set, the way used last: the other is the one to replace\n", "        self.recent = [0] * SETS\n        self.sram[:] = bytes(SRAM_SIZE)\n", 1),
    "sram_cleared_by_flush": ("        self.flush_done_ticks = self.rp2040.clock.ticks + nanos_to_ticks(FLUSH_CYCLES", "        self.sram[:] = bytes(SRAM_SIZE)\n        self.flush_done_ticks = self.rp2040.clock.ticks + nanos_to_ticks(FLUSH_CYCLES", 1),
    "sram_mirror_wider": ("& (SRAM_SIZE - 1) & ~3", "& (SRAM_SIZE * 2 - 1) & ~3", 2),
    # -- streaming
    "fifo_is_3_deep": ("FIFO_DEPTH = 2", "FIFO_DEPTH = 3", 1),
    "stream_ctr_mask": ("STREAM_CTR_MASK = 0x3FFFFF", "STREAM_CTR_MASK = 0xFFFFF", 1),
    "stream_addr_unaligned": ("self.stream_addr = value & 0xFFFFFFFC", "self.stream_addr = value & 0xFFFFFFFF", 1),
    "stream_zero_does_not_halt": ('            if not self.stream_ctr:  # "Write 0 to halt an in-progress stream, and discard any in-flight read"\n                self._stream_alarm.cancel()\n                self.stream_in_flight = False\n', "            if not self.stream_ctr:\n                pass\n", 1),
    "stream_zero_keeps_the_in_flight_flag": ("                self._stream_alarm.cancel()\n                self.stream_in_flight = False\n            else:", "                self._stream_alarm.cancel()\n            else:", 1),
    "word_takes_16_clocks": ("STREAM_WORD_CLOCKS = 32", "STREAM_WORD_CLOCKS = 16", 1),
    "sckdv_floor_is_1": ("& 0xFFFF, 2)", "& 0xFFFF, 1)", 1),
    "sckdv_is_8_bits": ("& 0xFFFF, 2)", "& 0xFF, 2)", 1),
    "stream_addr_stays": ("        self.stream_addr = (self.stream_addr + 4) & 0xFFFFFFFF\n", "", 1),
    "stream_addr_does_not_wrap": ("        self.stream_addr = (self.stream_addr + 4) & 0xFFFFFFFF\n", "        self.stream_addr = self.stream_addr + 4\n", 1),
    "stream_ctr_stays": ("        self.stream_ctr -= 1\n", "", 1),
    "stream_stops_after_a_word": ("        self._update_dreq()\n        self._maybe_start_stream()\n\n    def _update_dreq", "        self._update_dreq()\n\n    def _update_dreq", 1),
    "pop_does_not_restart": ("        self._update_dreq()\n        self._maybe_start_stream()\n        return word", "        self._update_dreq()\n        return word", 1),
    "pop_empty_is_all_ones": ("word = self.stream_fifo.pop(0) if self.stream_fifo else 0", "word = self.stream_fifo.pop(0) if self.stream_fifo else 0xFFFFFFFF", 1),
    "pop_takes_the_newest": ("self.stream_fifo.pop(0)", "self.stream_fifo.pop()", 1),
    "pop_leaves_dreq": ("self.stream_fifo.pop(0) if self.stream_fifo else 0\n        self._update_dreq()\n", "self.stream_fifo.pop(0) if self.stream_fifo else 0\n", 1),
    "dreq_only_when_full": ("        if self.stream_fifo:\n            dma.set_dreq", "        if len(self.stream_fifo) >= FIFO_DEPTH:\n            dma.set_dreq", 1),
    "dreq_number": ("DREQ_XIP_STREAM = 37", "DREQ_XIP_STREAM = 38", 1),
    "word_little_endian_lost": ('int.from_bytes(flash[offset : offset + 4], "little")', 'int.from_bytes(flash[offset : offset + 4], "big")', 1),
    "stat_full_at_one": ("len(self.stream_fifo) >= FIFO_DEPTH", "len(self.stream_fifo) >= 1", 1),
    "stat_empty_inverted": ("(STAT_FIFO_EMPTY if not self.stream_fifo else 0)", "(STAT_FIFO_EMPTY if self.stream_fifo else 0)", 1),
    "reset_keeps_the_fifo": ("        self.stream_fifo: list[int] = []\n", "        self.stream_fifo = getattr(self, \"stream_fifo\", [])\n", 1),
    # -- the registers around
    "unknown_offsets_read_all_ones": ("        return 0\n\n    def write_uint32", "        return 0xFFFFFFFF\n\n    def write_uint32", 1),
    "past_the_block_warns": ("        elif offset > STREAM_FIFO:\n            pass  # cache maintenance past the register block: nothing to do\n        else:", "        else:", 1),
    "aux_does_not_pop": ("        return self.rp2040.xip_ctrl.pop_stream_fifo()", "        return self.rp2040.xip_ctrl.stream_fifo[0] if self.rp2040.xip_ctrl.stream_fifo else 0", 1),
}  # fmt: skip

MUTANTS = tuple(MUTATIONS)


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(P)
    assert source.count(old) == count, (
        f"{name}: {old!r} occurs {source.count(old)} times in _xip_ctrl.py, expected {count}"
    )
    module = types.ModuleType(f"rp2040py.peripherals._xip_ctrl_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_xip_ctrl mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def mutant_rig(name: str) -> Rig:
    """A rig whose XIP is the pure-Python one with one expression changed, to prove the comparison sees a change of *logic*."""
    return Rig("mutant", _mutant_module(name))


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _flash_address(r: random.Random) -> int:
    """An address of the window, from a pool that lands in the same sets: five tags per set (the third evicts), a few sets, all eight bytes of a line."""
    alias = r.choice((0, 0, 0, 1, 2, 3))
    span = r.choice(SPANS)
    way = r.randrange(5) * 0x2000  # 1024 sets x 8 bytes: the same set, the next tag
    set_index = r.choice(SETS_USED) * 8
    return 0x10000000 + (alias << 24) + ((span + way + set_index + r.randrange(8)) & 0xFFFFFF)


def _width_aligned(r: random.Random, address: int) -> tuple[int, int]:
    width = r.choice((4, 4, 4, 2, 1))
    return address & ~(width - 1), width


def _reg_value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == P.CTRL:
        return r.choice((0, 1, 2, 3, 8, 9, 0xB, 0xF, r.getrandbits(32)))
    if offset == P.FLUSH:
        return r.choice((0, 1, 1, 3, r.getrandbits(32)))
    if offset == P.STREAM_ADDR:
        return r.choice(
            (0, 0x40, 0x100, 0xFFFFFC, 0x1000000 - 4, 0xFFFFFFFC, 0xFFFFFFFF, r.getrandbits(24), r.getrandbits(32))
        )
    if offset == P.STREAM_CTR:
        return (
            r.choice((0, 0, 1, 2, 3, 5, 8, 0x3FFFFF, 0x400000, r.getrandbits(32))) if roll < 0.9 else r.getrandbits(32)
        )
    return r.getrandbits(32)


def _scenario_stream(r: random.Random) -> list[tuple]:
    """A stream, run to its end with the FIFO drained in between."""
    ops: list[tuple] = [
        ("poke", r.randrange(0, 0x400, 4), bytes(r.getrandbits(8) for _ in range(r.choice((4, 16, 32))))),
        ("reg_write", P.STREAM_ADDR, r.randrange(0, 0x400, 4), 0),
        ("reg_write", P.STREAM_CTR, r.randrange(1, 7), 0),
    ]
    for _ in range(r.randrange(3, 9)):
        ops.append(("tick", r.choice((256, 512, 600, 1_000, 2_000))))
        ops.append(r.choice((("reg_read", P.STREAM_FIFO, 0), ("aux",), ("reg_read", P.STAT, 0))))
    return ops


def _scenario_dma(r: random.Random) -> list[tuple]:
    ops: list[tuple] = [
        ("poke", 0x40, bytes(r.getrandbits(8) for _ in range(16))),
        ("dma", r.randrange(1, 6)),
        ("reg_write", P.STREAM_ADDR, 0x40, 0),
        ("reg_write", P.STREAM_CTR, r.randrange(1, 6), 0),
    ]
    for _ in range(r.randrange(3, 8)):
        ops.append(("tick", r.choice((256, 512, 1_000, 2_000, 5_000))))
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.04:
            ops.extend(_scenario_stream(r))
        elif roll < 0.07:
            ops.extend(_scenario_dma(r))
        elif roll < 0.36:
            address, width = _width_aligned(r, _flash_address(r))
            ops.append(("fread", address, width))
        elif roll < 0.44:
            address, width = _width_aligned(r, _flash_address(r))
            ops.append(("fwrite", address, width, r.getrandbits(32)))
        elif roll < 0.58:
            offset = r.choice(ALL_OFFSETS)
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("reg_write", offset, _reg_value(r, offset), alias))
        elif roll < 0.62:
            ops.append(("reg_write", r.choice(PAST_THE_BLOCK), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.72:
            ops.append(("reg_read", r.choice(ALL_OFFSETS), r.choice(ALIASES) if r.random() < 0.05 else ALIASES[0]))
        elif roll < 0.74:
            ops.append(("reg_read", r.choice(PAST_THE_BLOCK), ALIASES[0]))
        elif roll < 0.82:
            address, width = _width_aligned(
                r, r.choice((0, 4, 8, 0x3FFC, 0x4000, 0x4004, 0x00FFC000, 0x00FFFFFC, r.randrange(0x1000000)))
            )
            ops.append(("sread", address, width))
        elif roll < 0.88:
            address, width = _width_aligned(
                r, r.choice((0, 4, 8, 0x3FFC, 0x4000, 0x4004, 0x00FFC000, 0x00FFFFFC, r.randrange(0x1000000)))
            )
            ops.append(("swrite", address, width, r.getrandbits(32)))
        elif roll < 0.90:
            ops.append(("aux",))
        elif roll < 0.915:
            ops.append(("baudr", r.choice((0, 1, 2, 3, 4, 16, 0x10000, 0xFFFF1234))))
        elif roll < 0.93:
            ops.append(("poke", r.randrange(0, 0x400, 4), bytes(r.getrandbits(8) for _ in range(4))))
        elif roll < 0.945:
            ops.append(("saturate", r.choice(("ctr_acc", "ctr_hit")), r.randrange(3)))
        elif roll < 0.985:
            ops.append(("tick", r.choice(TICKS)))
        else:
            ops.append(("reset",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def _snapshot(rig: Rig) -> dict[str, Any]:
    try:
        return rig.snapshot()
    except Exception as error:  # noqa: BLE001
        return {"raised": f"{type(error).__name__}: {error}"}


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b.get(key, "<missing>")
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and isinstance(value_b, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:70] for i in where[:6]]} vs {[str(value_b[i])[:70] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    try:
        return _lockstep(a, b, ops, perturb)
    finally:
        del a, b
        if IS32BIT:
            gc.collect()


def _lockstep(a: Rig, b: Rig, ops: list[tuple], perturb: "Callable[[Rig, int], None] | None") -> "Divergence | None":
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = _snapshot(a), _snapshot(b)
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    x = rig.chip.xip_ctrl
    for op in ops:
        before = len(rig.log)
        hit, acc, fifo, flush_ready = x.ctr_hit, x.ctr_acc, len(x.stream_fifo), x.flush_ready
        words_before = int(rig.chip.read_uint32(DEST))
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(
                "log.sram_error"
                if "cache-as-SRAM" in event[4]
                else "log.bad_write"
                if "XIP alias" in event[4]
                else "log.other"
            )
        if op[0] == "fread":
            alias = (op[1] >> 24) & 3
            count(f"read.alias{alias}")
            if x.ctr_hit > hit:
                count("read.hit")
            elif x.ctrl & 1 and alias != 3:
                count("read.miss")
            if op[2] != 4:
                count(f"read.width{op[2]}")
        if op[0] == "fwrite":
            count(f"write.alias{(op[1] >> 24) & 3}")
        if op[0] == "sread":
            count("sram.read.ok" if x.sram_available else "sram.read.refused")
        if op[0] == "swrite":
            count("sram.write.ok" if x.sram_available else "sram.write.refused")
        if x.ctr_acc == 0xFFFFFFFF and acc == 0xFFFFFFFF:
            count("counter.saturated")
        if x.ctr_hit == 0xFFFFFFFF and hit == 0xFFFFFFFF:
            count("counter.hit_saturated")
        if op[0] == "reg_write" and op[1] == P.FLUSH and op[3] == 0 and op[2] & 1:
            count("flush")
        if len(x.stream_fifo) > fifo:
            count("stream.word")
        if len(x.stream_fifo) == P.FIFO_DEPTH:
            count("stream.full")
        if flush_ready != x.flush_ready:
            count("flush.ready_changed")
        if not x.ctrl & 1:
            count("state.cache_off")
        if x.ctrl & P.CTRL_POWER_DOWN:
            count("state.power_down")
        if int(rig.chip.read_uint32(DEST)) != words_before:
            count("dma.word_landed")
        if op[0] == "reset":
            count("reset")
    return counts
