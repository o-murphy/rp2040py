"""XIP_CTRL (`0x14000000`) - the execute-in-place cache's control registers.

**No cache is modelled, and none should be.** This emulator reads flash directly, so there is no
hit/miss behaviour to reproduce and no timing model that a cache would change. What firmware
legitimately expects, though, is that these registers *exist* and read back sane values: the
RP2040 boot path enables the cache, flushes it, and polls for the flush to complete. Left
unimplemented, `BasePeripheral` answers every read with `0xFFFFFFFF` - every status bit reads as
set, which is the same class of trap that made `CHIP_RESET` look like a permanent psm_restart (see
docs/records/0050-qspi-pad-reset-values.md's own "not the fix" section).

So: the registers are real, flushes complete instantly (there is nothing to flush), and the hit/
access counters stay at zero rather than inventing numbers. Writes to offsets past the register
block are ignored rather than warned about - a real boot does write there (`0x2000` was observed
during CircuitPython's), and with no cache to maintain there is nothing for us to do about it.
"""

from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RPXIPCtrl",)

CTRL = 0x00
FLUSH = 0x04
STAT = 0x08
CTR_HIT = 0x0C
CTR_ACC = 0x10
STREAM_ADDR = 0x14
STREAM_CTR = 0x18
STREAM_FIFO = 0x1C

# CTRL: EN (bit 0) | ERR_BADWRITE (bit 1). Datasheet reset value.
_CTRL_RESET = 0x00000003

# STAT: FLUSH_READY (bit 1) and FIFO_EMPTY (bit 2). Both are permanently true here - a flush that
# has nothing to flush is always ready, and a streaming FIFO that never streams is always empty.
FLUSH_READY = 1 << 1
FIFO_EMPTY = 1 << 2
_STAT_VALUE = FLUSH_READY | FIFO_EMPTY

_LAST_REGISTER = STREAM_FIFO


class RPXIPCtrl(BasePeripheral):
    def __init__(self, rp2040: "object", name: str) -> None:  # type: ignore[override]
        super().__init__(rp2040, name)  # type: ignore[arg-type]
        self.ctrl = _CTRL_RESET
        self.stream_addr = 0
        self.stream_ctr = 0

    def read_uint32(self, offset: int) -> int:
        if offset == CTRL:
            return self.ctrl
        if offset == FLUSH:
            # Reading FLUSH blocks until the flush completes on real hardware; here it is already
            # done by the time anyone can look.
            return 1
        if offset == STAT:
            return _STAT_VALUE
        if offset in (CTR_HIT, CTR_ACC):
            return 0  # no cache, so no hits and no accesses to count
        if offset == STREAM_ADDR:
            return self.stream_addr
        if offset == STREAM_CTR:
            return self.stream_ctr
        if offset == STREAM_FIFO:
            return 0
        # Anything else in this block: 0, not BasePeripheral's 0xFFFFFFFF - see the module
        # docstring for why all-ones is actively dangerous for a register block firmware polls.
        return 0

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CTRL:
            self.ctrl = value & 0xFFFFFFFF
            return
        if offset == FLUSH:
            return  # nothing to flush
        if offset == STREAM_ADDR:
            self.stream_addr = value & 0xFFFFFFFC
            return
        if offset == STREAM_CTR:
            self.stream_ctr = value & 0xFFFFFFFF
            return
        if offset in (STAT, CTR_HIT, CTR_ACC, STREAM_FIFO):
            return  # read-only / counters we do not keep
        if offset > _LAST_REGISTER:
            return  # cache maintenance on a cache that does not exist
        super().write_uint32(offset, value)
