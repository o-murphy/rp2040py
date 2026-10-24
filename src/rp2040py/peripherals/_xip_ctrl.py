"""XIP_CTRL (`0x14000000`) and the XIP subsystem around it (RP2040 datasheet 2.6.3, tables 154-162): the cache, the aliases of the flash window, the cache used as SRAM and the flash stream.

Written from the datasheet and the two pico-examples it quotes (`flash_cache_perfctr`, `flash_xip_stream`); what it does and what it leaves is in docs/records/0098-datasheet-conformance-audit.md
("XIP_CTRL and the XIP subsystem"). In short:

- **The cache** (16 kB, two-way set associative, 8-byte lines) is modelled as *tags only*: the data always comes from the flash image, so a hit and a miss return the same bytes and differ only in the
  counters and in what later accesses find. Aliases: `0x10` cacheable and allocating, `0x11` cacheable non-allocating, `0x12` non-cacheable allocating, `0x13` bypass; with `CTRL.EN` clear (or the
  cache powered down) all four bypass. A **write** to `0x10` deletes a matching entry; a write to another alias is a bus fault when `ERR_BADWRITE` is set - which this model has no way to raise, so it warns
  - and is ignored. Reads count `CTR_ACC` (every alias), hits count `CTR_HIT`; writes count neither (the example's sequence shows it).
- **`FLUSH`** clears the tags and takes just over 1024 clk_sys cycles (`STAT.FLUSH_READY` reads 0 meanwhile); the block is flushed when it comes out of reset; a read of `FLUSH` waits for the flush, which
  here means it completes at once. The data RAM keeps its contents through a flush and a reset: that is the **cache as SRAM** (`0x15…`, 16 kB mirrored over the segment), usable only while the cache is
  disabled (an access otherwise is a bus error: it warns and is ignored here).
- **Streaming** (`STREAM_ADDR`, `STREAM_CTR`): a linear run of flash words read in the background into a 2-entry FIFO, read back through `STREAM_FIFO` or the auxiliary slave `XIP_AUX`
  (`0x50400000`); the FIFO drives `DREQ_XIP_STREAM` while it is not empty.

The model's own choices - not in the datasheet: least-recently-used replacement; a flush of 1026 cycles; one streamed word per `SCKDV x 32` clk_sys cycles (a flash read is not timed anywhere else); an
access of the model's CPU is one count (a halfword fetch is an access), where the M0+ fetches words.
"""

from typing import TYPE_CHECKING, Any

from rp2040py.clock._simulation_clock import nanos_to_ticks
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPXIPAux", "RPXIPCtrl")

CTRL = 0x00
FLUSH = 0x04
STAT = 0x08
CTR_HIT = 0x0C
CTR_ACC = 0x10
STREAM_ADDR = 0x14
STREAM_CTR = 0x18
STREAM_FIFO = 0x1C

CTRL_EN = 1 << 0
CTRL_ERR_BADWRITE = 1 << 1
CTRL_POWER_DOWN = 1 << 3
CTRL_MASK = CTRL_EN | CTRL_ERR_BADWRITE | CTRL_POWER_DOWN  # bit 2 and 31:4 are reserved
CTRL_RESET = CTRL_EN | CTRL_ERR_BADWRITE

STAT_FLUSH_READY = 1 << 0
STAT_FIFO_EMPTY = 1 << 1
STAT_FIFO_FULL = 1 << 2

# 16 kB / 8 bytes per line / 2 ways = 1024 sets; the flash address space is 24 bits wide
LINE_SHIFT = 3
SETS = 1024
SET_MASK = SETS - 1
TAG_SHIFT = LINE_SHIFT + 10
FLASH_MASK = 0x00FFFFFF
INVALID = -1
FLUSH_CYCLES = 1026  # "just over 1024 clock cycles" (2.6.3.2)
SRAM_SIZE = 16 * 1024
FIFO_DEPTH = 2
STREAM_CTR_MASK = 0x3FFFFF  # 21:0
STREAM_WORD_CLOCKS = 32  # SCK periods per streamed word, times SCKDV
SSI_BAUDR = 0x14
COUNTER_MAX = 0xFFFFFFFF
DREQ_XIP_STREAM = 37

ALIAS_ALLOCATING = 0  # 0x10…
ALIAS_NOALLOC = 1  # 0x11…
ALIAS_NOCACHE = 2  # 0x12…
ALIAS_BYPASS = 3  # 0x13…


class RPXIPCtrl(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str) -> None:
        super().__init__(rp2040, name)
        self.sram = bytearray(SRAM_SIZE)  # the cache's data RAM: it keeps its contents through a flush and a reset
        self._stream_alarm = rp2040.clock.create_alarm(self._on_stream_word)
        self.flush_done_ticks = 0
        rp2040.clock.add_rebase_listener(
            self._rebased
        )  # the flush deadline is an absolute tick count: it follows the clock when its origin moves
        self.reset()

    def _rebased(self, delta: int) -> None:
        self.flush_done_ticks = max(
            self.flush_done_ticks - delta, 0
        )  # a deadline long past is just "done": it must not drift negative without bound (it would overflow in C++)

    def snapshot(self) -> dict[str, Any]:
        """The block's state as plain values: the registers and counters, the stream (address, count, FIFO, in flight), the flush, the cache tags and recency, and the data RAM."""
        return {
            "ctrl": self.ctrl,
            "ctr_hit": self.ctr_hit,
            "ctr_acc": self.ctr_acc,
            "stream_addr": self.stream_addr,
            "stream_ctr": self.stream_ctr,
            "stream_fifo": tuple(self.stream_fifo),
            "stream_in_flight": bool(self.stream_in_flight),
            "flush_done_ticks": self.flush_done_ticks,
            "flush_ready": bool(self.flush_ready),
            "stat": self.stat,
            "sram_available": bool(self.sram_available),
            "tags": tuple(tuple(ways) for ways in self.tags),
            "recent": tuple(self.recent),
            "sram": bytes(self.sram),
        }

    def set_counter(self, name: str, value: int) -> None:
        """The one thing the bus cannot do: put a counter (`"ctr_acc"` or `"ctr_hit"`) near its ceiling (they count accesses, and the registers only clear), for a saturation test."""
        if name not in ("ctr_acc", "ctr_hit"):
            raise ValueError(f"not a counter: {name!r}")
        setattr(self, name, value)

    def reset(self) -> None:
        """The XIP domain, which is a `PSM.WDSEL` bit rather than a `RESETS` one - there is no `RESETS_RESET_XIP` (0089 Phase 5): the registers back to power-on, the stream stopped and its FIFO
        emptied, and the cache flushed ("the cache is flushed whenever the XIP block is reset"). The data RAM keeps its contents."""
        self._stream_alarm.cancel()
        self.ctrl = CTRL_RESET
        self.ctr_hit = 0
        self.ctr_acc = 0
        self.stream_addr = 0
        self.stream_ctr = 0
        self.stream_fifo: list[int] = []
        self.stream_in_flight = False
        self.tags = [[INVALID, INVALID] for _ in range(SETS)]
        self.recent = [0] * SETS  # per set, the way used last: the other is the one to replace
        self._start_flush()
        self._update_dreq()

    # --- the cache ---------------------------------------------------------------------------------------------------------

    def _start_flush(self) -> None:
        self.tags = [[INVALID, INVALID] for _ in range(SETS)]
        self.flush_done_ticks = self.rp2040.clock.ticks + nanos_to_ticks(FLUSH_CYCLES * 1e9 / self.rp2040.clk_sys)

    @property
    def flush_ready(self) -> bool:
        return self.rp2040.clock.ticks >= self.flush_done_ticks

    def access(self, address: int) -> None:
        """One read of an XIP alias (`0x10…0x13`): counted, looked up and (the alias allowing it) allocated."""
        if self.ctr_acc < COUNTER_MAX:
            self.ctr_acc += 1
        alias = (address >> 24) & 3
        if not self.ctrl & CTRL_EN or alias == ALIAS_BYPASS:
            return
        line = (address & FLASH_MASK) >> LINE_SHIFT
        index = line & SET_MASK
        tag = line >> 10
        ways = self.tags[index]
        way = 0 if ways[0] == tag else 1 if ways[1] == tag else INVALID
        if alias == ALIAS_NOCACHE:  # "Don't check for hit, always update cache"
            if way == INVALID:
                way = self._allocate(index, tag)
            self.recent[index] = way
            return
        if way != INVALID:
            if self.ctr_hit < COUNTER_MAX:
                self.ctr_hit += 1
            self.recent[index] = way
        elif alias == ALIAS_ALLOCATING:
            self._allocate(index, tag)

    def _allocate(self, index: int, tag: int) -> int:
        ways = self.tags[index]
        way = 0 if ways[0] == INVALID else 1 if ways[1] == INVALID else 1 - self.recent[index]
        ways[way] = tag
        self.recent[index] = way
        return way

    def write_access(self, address: int) -> None:
        """One write to an XIP alias. The caching, allocating alias deletes a matching entry; any other alias is a bus fault with `ERR_BADWRITE` set (warned here) and ignored without it."""
        alias = (address >> 24) & 3
        if alias == ALIAS_ALLOCATING:
            line = (address & FLASH_MASK) >> LINE_SHIFT
            ways = self.tags[line & SET_MASK]
            tag = line >> 10
            if ways[0] == tag:
                ways[0] = INVALID
            if ways[1] == tag:
                ways[1] = INVALID
        elif self.ctrl & CTRL_ERR_BADWRITE:
            self.warn(f"Write to the XIP alias at 0x{address:08x} (a bus fault with ERR_BADWRITE set)")

    # --- the cache as SRAM (0x15…) -----------------------------------------------------------------------------------------

    @property
    def sram_available(self) -> bool:
        return not self.ctrl & (CTRL_EN | CTRL_POWER_DOWN)

    def sram_read(self, address: int) -> int | None:
        """The word at `address` of the `0x15…` segment, or None (a bus error, warned) while the cache is enabled or powered down."""
        if not self.sram_available:
            self.warn(
                f"Read of the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)"
            )
            return None
        offset = address & (SRAM_SIZE - 1) & ~3
        return int.from_bytes(self.sram[offset : offset + 4], "little")

    def sram_write(self, address: int, value: int) -> None:
        if not self.sram_available:
            self.warn(
                f"Write to the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)"
            )
            return
        offset = address & (SRAM_SIZE - 1) & ~3
        self.sram[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")

    # --- streaming (2.6.3.4) -----------------------------------------------------------------------------------------------

    def _word_ns(self) -> float:
        sckdv = max(self.rp2040.ssi.read_uint32(SSI_BAUDR) & 0xFFFF, 2)
        return sckdv * STREAM_WORD_CLOCKS * 1e9 / self.rp2040.clk_sys

    def _maybe_start_stream(self) -> None:
        if self.stream_ctr and not self.stream_in_flight and len(self.stream_fifo) < FIFO_DEPTH:
            self.stream_in_flight = True
            self._stream_alarm.schedule(self._word_ns())

    def _on_stream_word(self) -> None:
        self.stream_in_flight = False
        if not self.stream_ctr:
            return
        offset = self.stream_addr & FLASH_MASK
        flash = self.rp2040.flash
        self.stream_fifo.append(int.from_bytes(flash[offset : offset + 4], "little"))
        self.stream_addr = (self.stream_addr + 4) & 0xFFFFFFFF
        self.stream_ctr -= 1
        self._update_dreq()
        self._maybe_start_stream()

    def _update_dreq(self) -> None:
        dma = self.rp2040.dma
        if self.stream_fifo:
            dma.set_dreq(DREQ_XIP_STREAM)
        else:
            dma.clear_dreq(DREQ_XIP_STREAM)

    def pop_stream_fifo(self) -> int:
        """`STREAM_FIFO` and `XIP_AUX`: the oldest streamed word (0 if there is none); room in the FIFO lets the stream go on."""
        word = self.stream_fifo.pop(0) if self.stream_fifo else 0
        self._update_dreq()
        self._maybe_start_stream()
        return word

    # --- the registers -----------------------------------------------------------------------------------------------------

    @property
    def stat(self) -> int:
        return (
            (STAT_FLUSH_READY if self.flush_ready else 0)
            | (STAT_FIFO_EMPTY if not self.stream_fifo else 0)
            | (STAT_FIFO_FULL if len(self.stream_fifo) >= FIFO_DEPTH else 0)
        )

    def read_uint32(self, offset: int) -> int:
        if offset == CTRL:
            return self.ctrl
        if offset == FLUSH:
            # "Reading will hold the bus (stall the processor) until the flush completes": the model has no stall, so the flush completes now. SC: the bit reads 0.
            self.flush_done_ticks = min(self.flush_done_ticks, self.rp2040.clock.ticks)
            return 0
        if offset == STAT:
            return self.stat
        if offset == CTR_HIT:
            return self.ctr_hit
        if offset == CTR_ACC:
            return self.ctr_acc
        if offset == STREAM_ADDR:
            return self.stream_addr
        if offset == STREAM_CTR:
            return self.stream_ctr
        if offset == STREAM_FIFO:
            return self.pop_stream_fifo()
        # Anything else in this block: 0, not BasePeripheral's 0xFFFFFFFF - a real boot writes past the register block (0x2000 was observed during CircuitPython's) and polls what it finds there.
        return 0

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CTRL:
            self.ctrl = value & CTRL_MASK
            if self.ctrl & CTRL_POWER_DOWN:
                self.ctrl &= ~CTRL_EN  # "Writing 1 to this bit forces CTRL_EN to 0"
        elif offset == FLUSH:
            if value & 1:
                self._start_flush()
        elif offset == CTR_HIT:
            self.ctr_hit = 0  # WC: any value clears
        elif offset == CTR_ACC:
            self.ctr_acc = 0
        elif offset == STREAM_ADDR:
            self.stream_addr = value & 0xFFFFFFFC
        elif offset == STREAM_CTR:
            self.stream_ctr = value & STREAM_CTR_MASK
            if not self.stream_ctr:  # "Write 0 to halt an in-progress stream, and discard any in-flight read"
                self._stream_alarm.cancel()
                self.stream_in_flight = False
            else:
                self._maybe_start_stream()
        elif offset in (STAT, STREAM_FIFO):
            pass  # read only
        elif offset > STREAM_FIFO:
            pass  # cache maintenance past the register block: nothing to do
        else:
            super().write_uint32(offset, value)


class RPXIPAux(BasePeripheral):
    """`XIP_AUX` (`0x50400000`): "a simple bus interface providing access only to the streaming FIFO"."""

    def read_uint32(self, offset: int) -> int:
        return self.rp2040.xip_ctrl.pop_stream_fifo()

    def write_uint32(self, offset: int, value: int) -> None:
        pass  # a read-only slave
