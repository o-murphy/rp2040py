"""SIO behaviours the datasheet pins down (docs/records/0098-datasheet-conformance-audit.md): the interpolator worked examples of section 2.3.1.6 (pico-examples hello_interp, with
the output the datasheet prints), the register widths and the divider's reset state. They run on whichever build is selected, through the bus."""

SIO = 0xD0000000
INTERP0, INTERP1 = 0x80, 0xC0
ACCUM0, ACCUM1, BASE0, BASE1, POP0, POP1, PEEK0, PEEK1, CTRL0, CTRL1, ADD0, ADD1 = (
    0x00,
    0x04,
    0x08,
    0x0C,
    0x14,
    0x18,
    0x20,
    0x24,
    0x2C,
    0x30,
    0x34,
    0x38,
)


def _cfg(shift=0, lsb=0, msb=31, signed=0, cross_input=0, cross_result=0, add_raw=0, force_msb=0, blend=0, clamp=0):
    return (
        shift
        | lsb << 5
        | msb << 10
        | signed << 15
        | cross_input << 16
        | cross_result << 17
        | add_raw << 18
        | force_msb << 19
        | blend << 21
        | clamp << 22
    )


def _io(chip):
    def write(offset, value):
        chip.write_uint32(SIO + offset, value & 0xFFFFFFFF)

    def read(offset):
        return chip.read_uint32(SIO + offset) & 0xFFFFFFFF

    return write, read


def test_times_table_and_moving_mask(rp2040_factory):
    write, read = _io(rp2040_factory())
    write(INTERP0 + CTRL0, _cfg())
    write(INTERP0 + ACCUM0, 0)
    write(INTERP0 + BASE0, 9)
    assert [read(INTERP0 + POP0) for _ in range(10)] == [9 * i for i in range(1, 11)]
    write(INTERP0 + ACCUM0, 0x1234ABCD)
    unsigned = [0xD, 0xC0, 0xB00, 0xA000, 0x40000, 0x300000, 0x2000000, 0x10000000]
    signed = [0xFFFFFFFD, 0xFFFFFFC0, 0xFFFFFB00, 0xFFFFA000, 0x40000, 0x300000, 0x2000000, 0x10000000]
    for flag, expected in ((0, unsigned), (1, signed)):
        got = []
        for i in range(8):
            write(INTERP0 + CTRL0, _cfg(lsb=i * 4, msb=i * 4 + 3, signed=flag))
            got.append(read(INTERP0 + ADD0))  # "Reading from ACCUMx_ADD returns the raw lane shift and mask value"
        assert got == expected


def test_cross_blend_and_clamp_examples(rp2040_factory):
    write, read = _io(rp2040_factory())
    write(INTERP0 + CTRL0, _cfg(cross_result=1))
    write(INTERP0 + CTRL1, _cfg(cross_result=1))
    write(INTERP0 + ACCUM0, 123)
    write(INTERP0 + ACCUM1, 456)
    write(INTERP0 + BASE0, 1)
    write(INTERP0 + BASE1, 0)
    got = [(read(INTERP0 + PEEK0), read(INTERP0 + POP1)) for _ in range(4)]
    assert got == [(124, 456), (457, 124), (125, 457), (458, 125)]
    write(INTERP0 + CTRL0, _cfg(blend=1))
    write(INTERP0 + CTRL1, _cfg())
    write(INTERP0 + BASE0, 500)
    write(INTERP0 + BASE1, 1000)
    blended = []
    for i in range(7):
        write(INTERP0 + ACCUM1, 255 * i // 6)
        blended.append(read(INTERP0 + PEEK1))
    assert blended == [500, 582, 666, 748, 832, 914, 998]  # "(note the 255/256 resulting in 998 not 1000)"
    write(INTERP1 + CTRL0, _cfg(shift=2, msb=29, signed=1, clamp=1))
    write(INTERP1 + BASE0, 0)
    write(INTERP1 + BASE1, 255)
    clamped = []
    for i in range(-1024, 1025, 256):
        write(INTERP1 + ACCUM0, i)
        clamped.append(read(INTERP1 + PEEK0))
    assert clamped == [0, 0, 0, 0, 0, 64, 128, 192, 255]


def test_force_msb_belongs_to_its_own_lane_and_is_not_written_back(rp2040_factory):
    write, read = _io(rp2040_factory())
    write(INTERP0 + CTRL0, _cfg())
    write(INTERP0 + CTRL1, _cfg(force_msb=3))
    write(INTERP0 + ACCUM1, 7)
    assert read(INTERP0 + PEEK1) == 7 | (3 << 28)
    assert read(INTERP0 + PEEK0) == 0
    assert read(INTERP0 + POP1) == 7 | (3 << 28)
    assert read(INTERP0 + ACCUM1) == 7  # "No effect on the internal 32-bit datapath"


def test_accumulator_add_registers_are_24_bits_wide(rp2040_factory):
    write, read = _io(rp2040_factory())
    write(INTERP0 + ACCUM0, 0)
    write(INTERP0 + ADD0, 0xFF000005)
    assert read(INTERP0 + ACCUM0) == 5


def test_qspi_gpio_registers_are_6_bits_and_the_divider_resets_ready(rp2040_factory):
    write, read = _io(rp2040_factory())
    write(0x30, 0xFFFFFFFF)  # GPIO_HI_OUT
    write(0x40, 0xFFFFFFFF)  # GPIO_HI_OE
    assert read(0x30) == 0x3F and read(0x40) == 0x3F
    assert read(0x78) == 1  # DIV_CSR: READY set, DIRTY clear
    assert read(0x64) == 0 and read(0x6C) == 0  # DIV_UDIVISOR / DIV_SDIVISOR reset to 0
