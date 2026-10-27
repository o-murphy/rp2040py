"""How long an unpaced DMA transfer takes: "The DMA can perform one read access and one write access, up to 32 bits in size, every clock cycle" (RP2040 datasheet 2.5),
i.e. one word per clk_sys cycle, so a 16 KiB copy is 4096 cycles. MicroPython's own `ports/rp2/rp2_dma.py` times exactly that (30..79 us)."""

from utils.clock_tree import set_clk_sys

DMA = 0x50000000
READ_ADDR, WRITE_ADDR, TRANS_COUNT, CTRL_TRIG = 0x00, 0x04, 0x08, 0x0C
CTRL_WORD_COPY = (
    1 | (2 << 2) | (1 << 4) | (1 << 5) | (0x3F << 15)
)  # EN, 32-bit, both addresses increment, TREQ_SEL permanent
BUSY = 1 << 24
SRC, DST = 0x20000000, 0x20010000


def _start(chip, words):
    chip.write_uint32(DMA + READ_ADDR, SRC)
    chip.write_uint32(DMA + WRITE_ADDR, DST)
    chip.write_uint32(DMA + TRANS_COUNT, words)
    chip.write_uint32(DMA + CTRL_TRIG, CTRL_WORD_COPY)


def _busy(chip):
    return bool(chip.read_uint32(DMA + CTRL_TRIG) & BUSY)


def test_a_word_copy_takes_one_clk_sys_cycle_per_word(rp2040_factory):
    chip = rp2040_factory()
    for index in range(8):
        chip.write_uint32(SRC + 4 * index, 0x1000 + index)
    _start(chip, 4096)  # 125 MHz: 8 ns a word, 32768 ns in all
    chip.clock.tick(32767)
    assert _busy(chip)
    chip.clock.tick(1)
    assert not _busy(chip)
    assert [chip.read_uint32(DST + 4 * index) for index in range(8)] == [0x1000 + index for index in range(8)]


def test_the_cycle_follows_clk_sys(rp2040_factory):
    chip = rp2040_factory()
    set_clk_sys(chip, 50e6)  # 20 ns a word
    _start(chip, 100)
    chip.clock.tick(1999)
    assert _busy(chip)
    chip.clock.tick(1)
    assert not _busy(chip)
