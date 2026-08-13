from rp2040py.clock.mock_clock import MockClock
from rp2040py.peripherals.dma import DREQChannel
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

SPI0_BASE = 0x4003C000
SSPCR1 = SPI0_BASE + 0x004
SSPDR = SPI0_BASE + 0x008
SSE = bit(1)

CH0_READ_ADDR = DMA_BASE + 0x000
CH0_WRITE_ADDR = DMA_BASE + 0x004
CH0_TRANS_COUNT = DMA_BASE + 0x008
CH0_AL1_CTRL = DMA_BASE + 0x010
CH1_READ_ADDR = DMA_BASE + 0x040
CH1_WRITE_ADDR = DMA_BASE + 0x044
CH1_TRANS_COUNT = DMA_BASE + 0x048
CH1_AL1_CTRL = DMA_BASE + 0x050
MULTI_CHAN_TRIGGER = DMA_BASE + 0x430


def test_dma_channel_chaining(rp2040_factory):
    clock = MockClock()
    cpu = rp2040_factory(clock)

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


def test_offset_past_channels_does_not_dispatch_to_nonexistent_channel(rp2040_factory):
    clock = MockClock()
    cpu = rp2040_factory(clock)

    # Offset 0x300 is the first address after the 12 channel register blocks.
    # It must not be routed to channel index 12 (0x300 >> 6), which does not exist.
    cpu.read_uint32(PAST_CHANNELS)
    cpu.write_uint32(PAST_CHANNELS, 0x1234)


def test_spi_dma_paired_tx_rx_transfer_completes_without_listener(rp2040_factory):
    """Regression test for docs/tasks/main-spi-hang.md: a DMA-driven SPI write (the shape
    machine_spi.c's machine_spi_transfer() uses for any transfer >= 32 bytes - two DMA channels,
    one paced by DREQ_SPIx_TX feeding SSPDR, one paced by DREQ_SPIx_RX draining it) used to hang
    forever with RPSPI's default (no real completion-timing listener wired - the raw-REPL exec
    path this bug was found on never wires one, unlike tests/micropython_spi_run.py's own
    delayed listener) `on_transmit`, for any transfer longer than the 8-deep RX FIFO. Two
    independent bugs stacked to cause it, both exercised here: RPDMA.reset() (run once during
    RP2040.__init__) used to wipe RPSPI's already-correct construction-time DREQ_SPI0_TX level,
    stalling the TX channel before its first byte; and SimulationClock.link_alarm()'s same-
    timestamp tie-break used to let a self-rescheduling zero-delay TX channel perpetually cut in
    front of its own already-pending zero-delay RX alarm, starving RX until TX finished and
    overflowing the RX FIFO along the way."""
    clock = MockClock()
    cpu = rp2040_factory(clock)

    src_addr = 0x2001_0000
    dst_addr = 0x2002_0000
    # Longer than the 8-deep RX FIFO, so a starved RX channel would provably never keep up -
    # matches main-spi.py's own third `spi.write()` call (48 bytes, over the 32-byte DMA
    # threshold `machine_spi_transfer()` uses to pick the DMA path over a blocking byte loop).
    message = b"0123456789abcdef0123456789abcdef0123456789abcdef"
    for i, value in enumerate(message):
        cpu.write_uint8(src_addr + i, value)

    cpu.write_uint32(SSPCR1, SSE)  # spi_init()-equivalent: enable the SPI peripheral.

    # Channel 0 = TX: src_addr -> SSPDR, paced by DREQ_SPI0_TX, chained to itself (no chaining).
    cpu.write_uint32(CH0_READ_ADDR, src_addr)
    cpu.write_uint32(CH0_WRITE_ADDR, SSPDR)
    cpu.write_uint32(CH0_TRANS_COUNT, len(message))
    cpu.write_uint32(
        CH0_AL1_CTRL,
        EN | INCR_READ | (0 << CHAIN_TO_SHIFT) | (DREQChannel.DREQ_SPI0_TX << TREQ_SEL_SHIFT),
    )

    # Channel 1 = RX: SSPDR -> dst_addr, paced by DREQ_SPI0_RX, chained to itself.
    cpu.write_uint32(CH1_READ_ADDR, SSPDR)
    cpu.write_uint32(CH1_WRITE_ADDR, dst_addr)
    cpu.write_uint32(CH1_TRANS_COUNT, len(message))
    cpu.write_uint32(
        CH1_AL1_CTRL,
        EN | (1 << CHAIN_TO_SHIFT) | (DREQChannel.DREQ_SPI0_RX << TREQ_SEL_SHIFT),
    )

    # dma_start_channel_mask((1u << chan_rx) | (1u << chan_tx)): start both together.
    cpu.write_uint32(MULTI_CHAN_TRIGGER, bit(0) | bit(1))

    # Real firmware's dma_channel_wait_for_finish_blocking() would busy-poll BUSY forever here
    # if either channel got stuck - a generous but bounded amount of simulated time is enough to
    # prove genuine completion rather than merely "hasn't hung yet in the time we happened to
    # check", the same distinction this bug's own task file could never settle for real firmware.
    clock.advance(1_000_000)  # 1 simulated second.

    assert cpu.read_uint32(CH0_TRANS_COUNT) == 0
    assert not cpu.dma.channels[0].active
    assert cpu.read_uint32(CH1_TRANS_COUNT) == 0
    assert not cpu.dma.channels[1].active
    # RPSPI's default `on_transmit` always echoes back 0 (no real full-duplex loopback modeled) -
    # this asserts the RX DMA channel actually wrote real per-byte completions, not that it
    # merely stopped being busy.
    assert bytes(cpu.read_uint8(dst_addr + i) for i in range(len(message))) == bytes(len(message))
