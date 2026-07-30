from rp2040py.clock.mock_clock import MockClock
from rp2040py.rp2040 import RP2040
from rp2040py.utils.bit import bit

CH2_WRITE_ADDR = 0x50000084
CH2_TRANS_COUNT = 0x50000088
CH2_AL1_CTRL = 0x50000090
CH2_AL3_READ_ADDR_TRIG = 0x500000BC
CH6_READ_ADDR = 0x50000180
CH6_WRITE_ADDR = 0x50000184
CH6_TRANS_COUNT = 0x50000188
CH6_CTRL_TRIG = 0x5000018C
INTR = 0x50000400

# First offset past the 12 per-channel register blocks (12 * 0x40 = 0x300).
DMA_BASE = 0x50000000
PAST_CHANNELS = DMA_BASE + 0x300

EN = bit(0)
DATA_SIZE_SHIFT = 2
INCR_WRITE = bit(5)
INCR_READ = bit(4)
CHAIN_TO_SHIFT = 11
TREQ_SEL_SHIFT = 15
BUSY = bit(24)

TREQ_PERMANENT = 0x3F


def test_dma_channel_chaining():
    clock = MockClock()
    cpu = RP2040(clock)

    # This test uses DMA to copy 4 chunks of 8-byte data, located in different memory areas,
    # into a single memory area. We use two DMA channels, 2 and 6 (numbers are arbitrary).
    # All the RAM addresses below are arbitrary:
    chunks_addr = [0x20001000, 0x20001100, 0x20002200, 0x20002300]
    dest_addr = 0x20008000
    dma_control_block_addr = 0x2000A000

    # Write the data to be copied, split into four chunks:
    cpu.write_uint32(chunks_addr[0], 0x10)
    cpu.write_uint32(chunks_addr[0] + 4, 0x20)
    cpu.write_uint32(chunks_addr[1], 0x30)
    cpu.write_uint32(chunks_addr[1] + 4, 0x40)
    cpu.write_uint32(chunks_addr[2], 0x50)
    cpu.write_uint32(chunks_addr[2] + 4, 0x60)
    cpu.write_uint32(chunks_addr[3], 0x70)
    cpu.write_uint32(chunks_addr[3] + 4, 0x80)

    # Write the source addresses into a DMA control block:
    cpu.write_uint32(dma_control_block_addr, chunks_addr[0])
    cpu.write_uint32(dma_control_block_addr + 4, chunks_addr[1])
    cpu.write_uint32(dma_control_block_addr + 8, chunks_addr[2])
    cpu.write_uint32(dma_control_block_addr + 12, chunks_addr[3])
    cpu.write_uint32(dma_control_block_addr + 16, 0)  # This marks the end of the chain

    # Channel 2 is used to copy the 8-byte chunks. Configure it:
    cpu.write_uint32(CH2_WRITE_ADDR, dest_addr)
    cpu.write_uint32(CH2_TRANS_COUNT, 2)  # 2 transfers of 4 bytes each = 8 bytes
    cpu.write_uint32(
        CH2_AL1_CTRL,
        EN
        | (6 << CHAIN_TO_SHIFT)
        | INCR_WRITE
        | INCR_READ
        | (TREQ_PERMANENT << TREQ_SEL_SHIFT)
        | (2 << DATA_SIZE_SHIFT),
    )

    # Channel 6 is used to control channel 2:
    cpu.write_uint32(CH6_WRITE_ADDR, CH2_AL3_READ_ADDR_TRIG)
    cpu.write_uint32(CH6_READ_ADDR, dma_control_block_addr)
    cpu.write_uint32(CH6_TRANS_COUNT, 1)  # we'll copy one word at a time
    cpu.write_uint32(CH6_CTRL_TRIG, EN | INCR_READ | (TREQ_PERMANENT << TREQ_SEL_SHIFT) | (2 << DATA_SIZE_SHIFT))

    assert cpu.read_uint32(CH6_CTRL_TRIG) & BUSY == BUSY

    # Now the DMA transfer should be running. Skip some clock cycles, allowing it to finish:
    clock.advance(32)

    # Check that the transfer has indeed completed
    assert cpu.read_uint32(CH2_AL3_READ_ADDR_TRIG) == 0
    assert cpu.read_uint32(CH2_AL1_CTRL) & BUSY == 0
    assert cpu.read_uint32(CH6_CTRL_TRIG) & BUSY == 0
    assert cpu.read_uint32(INTR) == bit(2) | bit(6)

    # Assert that the data was copied correctly:
    assert cpu.read_uint16(dest_addr + 0) == 0x10
    assert cpu.read_uint16(dest_addr + 4) == 0x20
    assert cpu.read_uint16(dest_addr + 8) == 0x30
    assert cpu.read_uint16(dest_addr + 12) == 0x40
    assert cpu.read_uint16(dest_addr + 16) == 0x50
    assert cpu.read_uint16(dest_addr + 20) == 0x60
    assert cpu.read_uint16(dest_addr + 24) == 0x70
    assert cpu.read_uint16(dest_addr + 28) == 0x80
    assert cpu.read_uint16(dest_addr + 32) == 0x0


def test_offset_past_channels_does_not_dispatch_to_nonexistent_channel():
    clock = MockClock()
    cpu = RP2040(clock)

    # Offset 0x300 is the first address after the 12 channel register blocks.
    # It must not be routed to channel index 12 (0x300 >> 6), which does not exist.
    cpu.read_uint32(PAST_CHANNELS)
    cpu.write_uint32(PAST_CHANNELS, 0x1234)
