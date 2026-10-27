"""The native SIO on the native bus (docs/records/0096-cpp-mcu-core.md, Phase 2): the SIO range (0xD0000000...) is not
a peripheral-table window, so the chip serves it through its own `sio`. These tests pin what that path must keep: the bus
agrees with the pure chip, and a failure inside the block reaches the caller of the bus access that caused it - and only
that one. Whole chips, as built: nothing is swapped in (docs/tasks/twin-backend-testing.md)."""

import random

import pytest
from utils.chip_pair import Native, PurePython, make_chip

pytest.importorskip("rp2040py.native._sio", reason="the native extension is not built")

SIO = 0xD0000000
DIV_UDIVISOR, DIV_QUOTIENT, GPIO_OUT, GPIO_OUT_SET, GPIO_OE, GPIO_OE_SET = 0x064, 0x070, 0x010, 0x014, 0x020, 0x024
IO_GPIO2_CTRL = 0x40014000 + 8 * 2 + 4


def _chips():
    return make_chip(PurePython), make_chip(Native)


def test_the_native_chip_gets_the_native_sio_and_serves_it_from_the_bus():
    native = make_chip(Native)

    native.write_uint32(SIO + GPIO_OUT_SET, 0b101)

    assert native.sio.gpio_value == 0b101
    assert native.read_uint32(SIO + GPIO_OUT) == 0b101


def _session(seed):
    rng = random.Random(seed)
    for _ in range(1500):
        offset = rng.choice([rng.randrange(0, 0x180) & ~3, 0x60, 0x64, 0x68, 0x6C, 0x70, 0x74, 0x78, 0x10, 0x14, 0x20])
        offset += rng.choice([0, 0, 0, 0x1000, 0x2000, 0x10000000 - 4])  # the far offsets are invalid SIO addresses
        yield (
            rng.choice(("r32", "w32", "w16", "w8")),
            SIO + offset,
            rng.choice([0, 1, 7, 100, 0xFFFFFFFF, -1, 0x80000000, rng.getrandbits(32)]),
        )


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_native_chip_with_the_native_sio_matches_the_pure_chip_with_the_pure_sio(seed):
    pure, native = _chips()

    for index, (op, address, value) in enumerate(_session(seed)):
        results = []
        for chip in (pure, native):
            if op == "r32":
                results.append(chip.read_uint32(address))
            elif op == "w32":
                results.append(chip.write_uint32(address, value))
            elif op == "w16":
                results.append(chip.write_uint16(address & ~1, value & 0xFFFF))
            else:
                results.append(chip.write_uint8(address, value & 0xFF))
        assert results[0] == results[1], f"op {index} {op} @ {address:#x}: pure {results[0]!r}, native {results[1]!r}"
        assert native.sio.snapshot() == pure.sio.snapshot(), f"SIO state after op {index}"
        assert native.core.cycles == pure.core.cycles


@pytest.mark.parametrize("cls", [PurePython, Native], ids=["pure", "native"])
def test_a_divide_by_zero_surfaces_from_the_bus_write_and_the_bus_keeps_working(cls):
    chip = make_chip(cls)

    with pytest.raises(ZeroDivisionError):
        chip.write_uint32(SIO + DIV_UDIVISOR, 0x100000000)

    chip.write_uint32(SIO + GPIO_OUT_SET, 0b1)  # nothing stale was left behind
    assert chip.read_uint32(SIO + GPIO_OUT) == 1


@pytest.mark.parametrize("cls", [PurePython, Native], ids=["pure", "native"])
def test_a_pin_listener_that_raises_surfaces_from_the_register_write_that_caused_it(cls):
    """SIO tells the pins when it writes an output or an output enable; a device listening on the pin that raises reaches the writer."""

    class Boom(Exception):
        pass

    chip = make_chip(cls)

    def listener(new, old):
        raise Boom("pin 2")

    chip.write_uint32(IO_GPIO2_CTRL, 5)  # GPIO2: SIO
    chip.gpio[2].add_listener(listener)

    with pytest.raises(Boom):
        chip.write_uint32(SIO + GPIO_OE_SET, 0b100)

    assert chip.read_uint32(SIO + GPIO_OE) & 0b100  # the register was updated before the pins were told
