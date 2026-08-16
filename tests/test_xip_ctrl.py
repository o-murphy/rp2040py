"""XIP_CTRL registers - see docs/records/0052-xip-ctrl-registers.md.

No cache is modelled; the point of the block is that firmware polling it reads sane values instead
of `BasePeripheral`'s `0xFFFFFFFF`, where every status bit looks set.
"""

from rp2040py.peripherals.xip_ctrl import CTR_ACC, CTR_HIT, CTRL, FIFO_EMPTY, FLUSH, FLUSH_READY, STAT

XIP_CTRL_BASE = 0x14000000


def test_ctrl_and_stat_read_their_datasheet_values(rp2040_factory):
    rp2040 = rp2040_factory()
    assert rp2040.read_uint32(XIP_CTRL_BASE + CTRL) == 0x3
    stat = rp2040.read_uint32(XIP_CTRL_BASE + STAT)
    assert stat & FLUSH_READY
    assert stat & FIFO_EMPTY


def test_a_flush_completes_immediately(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.write_uint32(XIP_CTRL_BASE + FLUSH, 1)
    assert rp2040.read_uint32(XIP_CTRL_BASE + FLUSH) == 1
    assert rp2040.read_uint32(XIP_CTRL_BASE + STAT) & FLUSH_READY


def test_ctrl_is_writable(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.write_uint32(XIP_CTRL_BASE + CTRL, 0x1)
    assert rp2040.read_uint32(XIP_CTRL_BASE + CTRL) == 0x1


def test_counters_stay_zero_rather_than_inventing_numbers(rp2040_factory):
    rp2040 = rp2040_factory()
    assert rp2040.read_uint32(XIP_CTRL_BASE + CTR_HIT) == 0
    assert rp2040.read_uint32(XIP_CTRL_BASE + CTR_ACC) == 0


def test_unknown_offsets_read_zero_not_all_ones(rp2040_factory):
    """The whole reason this block exists: all-ones makes every status bit look set, which is how
    an unimplemented CHIP_RESET convinced the bootrom a psm_restart was pending (0050)."""
    rp2040 = rp2040_factory()
    assert rp2040.read_uint32(XIP_CTRL_BASE + 0x40) == 0


def test_writes_past_the_register_block_are_ignored(rp2040_factory):
    """A real boot writes there; with no cache to maintain there is nothing to do, and it must not
    fall through to the 'undefined address' path."""
    rp2040 = rp2040_factory()
    rp2040.write_uint32(XIP_CTRL_BASE + 0x2000, 0xDEADBEEF)
    assert rp2040.read_uint32(XIP_CTRL_BASE + 0x2000) == 0
