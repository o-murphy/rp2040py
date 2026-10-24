"""XIP_CTRL and the XIP subsystem against the datasheet (2.6.3) - docs/records/0098-datasheet-conformance-audit.md.

The directed sequence of `flash_cache_perfctr` (pico-examples) is the spine: flush, clear the counters, then read the same flash word repeatedly and watch ACC/HIT.
"""

from rp2040py.peripherals.xip_ctrl import (
    CTR_ACC,
    CTR_HIT,
    CTRL,
    CTRL_EN,
    CTRL_ERR_BADWRITE,
    CTRL_POWER_DOWN,
    DREQ_XIP_STREAM,
    FLUSH,
    STAT,
    STAT_FIFO_EMPTY,
    STAT_FIFO_FULL,
    STAT_FLUSH_READY,
    STREAM_ADDR,
    STREAM_CTR,
    STREAM_FIFO,
)

XIP_CTRL_BASE = 0x14000000
XIP = 0x10000000
XIP_NOALLOC = 0x11000000
XIP_NOCACHE = 0x12000000
XIP_BYPASS = 0x13000000
XIP_AUX = 0x50400000
SRAM = 0x15000000

DMA_BASE = 0x50000000
SSI_BAUDR = 0x18000014


class WarningRecorder:
    """Stands in for the chip's logger: what the model reports as a bus fault it cannot raise."""

    def __init__(self):
        self.warnings = []

    def warning(self, name, message):
        self.warnings.append(message)

    def debug(self, name, message): ...

    info = error = debug


def record_warnings(rp2040):
    recorder = WarningRecorder()
    rp2040.logger = recorder
    return recorder.warnings


def one_streamed_word(rp2040):
    """Lets the clock run for one streamed word: SCKDV (at least 2) x 32 clk_sys cycles."""
    sckdv = max(rp2040.read_uint32(SSI_BAUDR) & 0xFFFF, 2)
    rp2040.clock.tick(sckdv * 32 * 1e9 / rp2040.clk_sys)


def reg(rp2040, offset):
    return rp2040.read_uint32(XIP_CTRL_BASE + offset)


def put(rp2040, offset, value):
    rp2040.write_uint32(XIP_CTRL_BASE + offset, value)


def settle_flush(rp2040):
    reg(rp2040, FLUSH)  # "reading will hold the bus until the flush completes"


def test_reset_values(rp2040_factory):
    rp2040 = rp2040_factory()
    assert reg(rp2040, CTRL) == CTRL_EN | CTRL_ERR_BADWRITE
    assert reg(rp2040, CTR_HIT) == 0
    assert reg(rp2040, CTR_ACC) == 0
    assert reg(rp2040, STREAM_ADDR) == 0
    assert reg(rp2040, STREAM_CTR) == 0


def test_the_cache_is_flushed_out_of_reset_and_flush_ready_follows_the_flush(rp2040_factory):
    rp2040 = rp2040_factory()
    assert not reg(rp2040, STAT) & STAT_FLUSH_READY  # just over 1024 cycles
    settle_flush(rp2040)
    assert reg(rp2040, STAT) & STAT_FLUSH_READY
    put(rp2040, FLUSH, 1)
    assert not reg(rp2040, STAT) & STAT_FLUSH_READY
    rp2040.clock.tick(1100 * 1e9 / rp2040.clk_sys)
    assert reg(rp2040, STAT) & STAT_FLUSH_READY


def test_flash_cache_perfctr_sequence(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, FLUSH, 1)
    settle_flush(rp2040)
    put(rp2040, CTR_ACC, 0)
    put(rp2040, CTR_HIT, 0)
    rp2040.read_uint32(XIP)  # miss: the flush emptied the cache
    assert (reg(rp2040, CTR_ACC), reg(rp2040, CTR_HIT)) == (1, 0)
    rp2040.read_uint32(XIP)  # hit
    assert (reg(rp2040, CTR_ACC), reg(rp2040, CTR_HIT)) == (2, 1)
    rp2040.write_uint32(XIP, 0)  # a write to the caching alias deletes the entry, and counts as neither
    assert (reg(rp2040, CTR_ACC), reg(rp2040, CTR_HIT)) == (2, 1)
    rp2040.read_uint32(XIP)  # miss again
    assert (reg(rp2040, CTR_ACC), reg(rp2040, CTR_HIT)) == (3, 1)
    rp2040.read_uint32(XIP)
    assert (reg(rp2040, CTR_ACC), reg(rp2040, CTR_HIT)) == (4, 2)


def test_a_flush_forgets_everything(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.read_uint32(XIP + 0x100)
    put(rp2040, FLUSH, 1)
    put(rp2040, CTR_HIT, 0)
    rp2040.read_uint32(XIP + 0x100)
    assert reg(rp2040, CTR_HIT) == 0


def test_a_line_is_eight_bytes(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.read_uint32(XIP + 0x200)
    rp2040.read_uint32(XIP + 0x204)  # same 8-byte line
    assert reg(rp2040, CTR_HIT) == 1
    rp2040.read_uint32(XIP + 0x208)  # next line
    assert reg(rp2040, CTR_HIT) == 1


def test_two_ways_and_lru_replacement(rp2040_factory):
    rp2040 = rp2040_factory()
    stride = 1024 * 8  # same set, different tag
    for n in range(3):  # the third read evicts the least recently used of the first two
        rp2040.read_uint32(XIP + n * stride)
    put(rp2040, CTR_HIT, 0)
    rp2040.read_uint32(XIP + 2 * stride)
    rp2040.read_uint32(XIP + 1 * stride)
    assert reg(rp2040, CTR_HIT) == 2
    rp2040.read_uint32(XIP)  # was evicted
    assert reg(rp2040, CTR_HIT) == 2


def test_aliases(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.read_uint32(XIP_NOALLOC + 0x40)  # a miss that does not allocate
    rp2040.read_uint32(XIP_NOALLOC + 0x40)
    assert reg(rp2040, CTR_HIT) == 0
    rp2040.read_uint32(XIP_NOCACHE + 0x40)  # never checks for a hit, always updates the cache
    assert reg(rp2040, CTR_HIT) == 0
    rp2040.read_uint32(XIP_NOALLOC + 0x40)  # now present: a hit through the non-allocating alias
    assert reg(rp2040, CTR_HIT) == 1
    rp2040.read_uint32(XIP_BYPASS + 0x80)  # bypass: counted as an access, never looked up
    rp2040.read_uint32(XIP_BYPASS + 0x80)
    rp2040.read_uint32(XIP + 0x80)
    assert reg(rp2040, CTR_HIT) == 1
    assert reg(rp2040, CTR_ACC) == 7


def test_the_cache_off_bypasses_every_alias(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, CTRL, CTRL_ERR_BADWRITE)
    rp2040.read_uint32(XIP + 0x10)
    rp2040.read_uint32(XIP + 0x10)
    assert reg(rp2040, CTR_HIT) == 0
    assert reg(rp2040, CTR_ACC) == 2


def test_the_counters_are_write_to_clear_and_saturate(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.xip_ctrl.set_counter("ctr_acc", 0xFFFFFFFF)
    rp2040.read_uint32(XIP)
    assert reg(rp2040, CTR_ACC) == 0xFFFFFFFF
    put(rp2040, CTR_ACC, 0x1234)
    assert reg(rp2040, CTR_ACC) == 0


def test_all_the_halfword_and_byte_reads_count_one_access_each(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.read_uint16(XIP)
    rp2040.read_uint8(XIP + 1)
    rp2040.read_uint16(XIP_NOCACHE)
    assert reg(rp2040, CTR_ACC) == 3


def test_writes_never_change_the_flash_image(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.flash[0:4] = b"\x78\x56\x34\x12"
    rp2040.write_uint32(XIP, 0xFFFFFFFF)
    rp2040.write_uint16(XIP, 0xFFFF)
    rp2040.write_uint8(XIP, 0xFF)
    assert rp2040.read_uint32(XIP) == 0x12345678


def test_a_write_to_a_non_caching_alias_warns_only_with_err_badwrite(rp2040_factory):
    rp2040 = rp2040_factory()
    warnings = record_warnings(rp2040)
    rp2040.write_uint32(XIP_NOCACHE, 0)
    assert len(warnings) == 1
    put(rp2040, CTRL, CTRL_EN)
    rp2040.write_uint32(XIP_NOCACHE, 0)
    assert len(warnings) == 1


def test_ctrl_masks_its_reserved_bits_and_power_down_forces_en_off(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, CTRL, 0xFFFFFFFF)
    assert (
        reg(rp2040, CTRL) == CTRL_ERR_BADWRITE | CTRL_POWER_DOWN
    )  # 31:4 and bit 2 reserved; POWER_DOWN forced EN to 0


def test_a_reset_restores_the_registers_and_flushes(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.read_uint32(XIP)
    put(rp2040, CTRL, 0)
    rp2040.xip_ctrl.reset()
    assert reg(rp2040, CTRL) == CTRL_EN | CTRL_ERR_BADWRITE
    assert reg(rp2040, CTR_ACC) == 0
    rp2040.read_uint32(XIP)
    assert reg(rp2040, CTR_HIT) == 0


def test_unknown_offsets_read_zero_not_all_ones(rp2040_factory):
    """An unimplemented CHIP_RESET once convinced the bootrom a psm_restart was pending (0050): all-ones makes every status bit look set."""
    rp2040 = rp2040_factory()
    assert reg(rp2040, 0x40) == 0
    rp2040.write_uint32(XIP_CTRL_BASE + 0x2000, 0xDEADBEEF)
    assert rp2040.read_uint32(XIP_CTRL_BASE + 0x2000) == 0


# --- cache as SRAM -------------------------------------------------------------------------------------------------------


def test_cache_as_sram_needs_the_cache_off(rp2040_factory):
    rp2040 = rp2040_factory()
    warnings = record_warnings(rp2040)
    rp2040.write_uint32(SRAM, 0xAABBCCDD)  # the cache is on: a bus error
    assert warnings
    assert rp2040.read_uint32(SRAM) == 0xFFFFFFFF
    put(rp2040, CTRL, CTRL_ERR_BADWRITE)
    rp2040.write_uint32(SRAM + 4, 0xAABBCCDD)
    assert rp2040.read_uint32(SRAM + 4) == 0xAABBCCDD
    put(rp2040, CTRL, CTRL_ERR_BADWRITE | CTRL_POWER_DOWN)  # powered down: unavailable again
    assert rp2040.read_uint32(SRAM + 4) == 0xFFFFFFFF


def test_cache_as_sram_is_16k_mirrored_and_keeps_its_contents_through_flush_and_reset(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, CTRL, 0)
    rp2040.write_uint32(SRAM + 8, 0x11223344)
    assert rp2040.read_uint32(SRAM + 0x4000 + 8) == 0x11223344
    assert rp2040.read_uint32(SRAM + 0x00FFC000 + 8) == 0x11223344
    put(rp2040, FLUSH, 1)
    rp2040.xip_ctrl.reset()
    put(rp2040, CTRL, 0)
    assert rp2040.read_uint32(SRAM + 8) == 0x11223344


def test_cache_as_sram_takes_halfword_and_byte_writes(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, CTRL, 0)
    rp2040.write_uint32(SRAM, 0)
    rp2040.write_uint16(SRAM + 2, 0xBEEF)
    rp2040.write_uint8(SRAM + 1, 0xAA)
    assert rp2040.read_uint32(SRAM) == 0xBEEFAA00


# --- streaming -----------------------------------------------------------------------------------------------------------


def test_stream_reads_words_into_a_two_entry_fifo_and_pops_them_in_order(rp2040_factory):
    rp2040 = rp2040_factory()
    for n in range(4):
        rp2040.flash[0x100 + n * 4 : 0x104 + n * 4] = (0x1000 + n).to_bytes(4, "little")
    put(rp2040, STREAM_ADDR, 0x100)
    put(rp2040, STREAM_CTR, 3)
    assert reg(rp2040, STAT) & STAT_FIFO_EMPTY
    one_streamed_word(rp2040)
    assert not reg(rp2040, STAT) & STAT_FIFO_EMPTY
    assert not reg(rp2040, STAT) & STAT_FIFO_FULL
    one_streamed_word(rp2040)
    assert reg(rp2040, STAT) & STAT_FIFO_FULL
    one_streamed_word(rp2040)  # the FIFO is full: the third word waits for room
    assert reg(rp2040, STREAM_ADDR) == 0x108
    assert reg(rp2040, STREAM_CTR) == 1
    assert reg(rp2040, STREAM_FIFO) == 0x1000
    one_streamed_word(rp2040)  # a pop made room
    assert reg(rp2040, STREAM_CTR) == 0
    assert rp2040.read_uint32(XIP_AUX) == 0x1001  # XIP_AUX is the same FIFO
    assert reg(rp2040, STREAM_FIFO) == 0x1002
    assert reg(rp2040, STREAM_FIFO) == 0  # empty: reads 0
    assert reg(rp2040, STAT) & STAT_FIFO_EMPTY


def test_stream_ctr_zero_halts_and_discards_the_in_flight_read(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, STREAM_ADDR, 0x100)
    put(rp2040, STREAM_CTR, 5)
    put(rp2040, STREAM_CTR, 0)
    one_streamed_word(rp2040)
    one_streamed_word(rp2040)
    assert reg(rp2040, STAT) & STAT_FIFO_EMPTY
    assert reg(rp2040, STREAM_ADDR) == 0x100


def test_the_stream_paces_a_dma_channel_through_dreq_37(rp2040_factory):
    """`flash_xip_stream`'s shape: a DMA channel paced by DREQ_XIP_STREAM drains XIP_AUX into memory."""
    rp2040 = rp2040_factory()
    for n in range(3):
        rp2040.flash[0x40 + n * 4 : 0x44 + n * 4] = (0xA0 + n).to_bytes(4, "little")
    destination = 0x20010000
    rp2040.write_uint32(DMA_BASE + 0x00, XIP_AUX)  # CH0_READ_ADDR
    rp2040.write_uint32(DMA_BASE + 0x04, destination)
    rp2040.write_uint32(DMA_BASE + 0x08, 3)
    rp2040.write_uint32(
        DMA_BASE + 0x0C, 1 | (2 << 2) | (1 << 5) | (DREQ_XIP_STREAM << 15) | (0 << 11)
    )  # EN, word, INCR_WRITE, TREQ_SEL, no chain
    assert rp2040.read_uint32(destination) == 0  # the FIFO is empty: no request yet
    put(rp2040, STREAM_ADDR, 0x40)
    put(rp2040, STREAM_CTR, 3)
    for _ in range(6):
        one_streamed_word(rp2040)
    assert [rp2040.read_uint32(destination + 4 * n) for n in range(3)] == [0xA0, 0xA1, 0xA2]
    assert reg(rp2040, STAT) & STAT_FIFO_EMPTY


def test_a_reset_stops_the_stream(rp2040_factory):
    rp2040 = rp2040_factory()
    put(rp2040, STREAM_ADDR, 0x1000)
    put(rp2040, STREAM_CTR, 4)
    rp2040.xip_ctrl.reset()
    assert reg(rp2040, STREAM_ADDR) == 0
    assert reg(rp2040, STREAM_CTR) == 0
    one_streamed_word(rp2040)
    assert reg(rp2040, STAT) & STAT_FIFO_EMPTY
