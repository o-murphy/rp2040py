"""The native SIO on the native bus (docs/records/0096-cpp-mcu-core.md, Phase 2): the SIO range (0xD0000000...) is not
a peripheral-table window, so the chip learns about a native block through its `sio` property instead. These tests pin
what that path must keep: the bus agrees with the pure chip, a wrapper around the block still sees every access, and a
failure inside the block reaches the caller of the bus access that caused it - and only that one."""

import random

import pytest
from utils.chip_pair import Native, PurePython, make_chip

pytest.importorskip("rp2040py.native._sio", reason="the native extension is not built")

from rp2040py._sio import RPSIO as PureSIO
from rp2040py.native._sio import RPSIO as NativeSIO

SIO = 0xD0000000
DIV_UDIVISOR, DIV_QUOTIENT, GPIO_OUT, GPIO_OUT_SET, GPIO_OE_SET = 0x064, 0x070, 0x010, 0x014, 0x024


def _chips():
    pure, native = make_chip(PurePython), make_chip(Native)
    # Both blocks built explicitly: the facade hands out one class or the other depending on RP2040PY_SKIP_CYTHON.
    pure.sio = PureSIO(pure)
    native.sio = NativeSIO(native)
    return pure, native


def test_the_native_chip_gets_the_native_sio_and_serves_it_from_the_bus():
    native = make_chip(Native)
    native.sio = NativeSIO(native)

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
        for name in ("gpio_value", "gpio_output_enable", "div_csr", "div_dividend", "div_divisor", "spin_lock"):
            assert getattr(native.sio, name) == getattr(pure.sio, name), f"{name} after op {index}"
        assert native.core.cycles == pure.core.cycles


def test_a_wrapper_around_the_native_sio_still_sees_every_access():
    """The native fast path is looked up on the block's *type*: a recorder/profiler that forwards attributes is not
    bypassed by lending out its target's C++ handler."""
    native = make_chip(Native)
    native.sio = NativeSIO(native)
    seen = []

    class Wrapper:
        def __init__(self, target):
            self.target = target

        def __getattr__(self, name):
            return getattr(self.target, name)

        def read_uint32(self, offset):
            seen.append(("r", offset))
            return self.target.read_uint32(offset)

        def write_uint32(self, offset, value):
            seen.append(("w", offset))
            self.target.write_uint32(offset, value)

    native.sio = Wrapper(native.sio)

    native.read_uint32(SIO + GPIO_OUT)
    native.write_uint32(SIO + GPIO_OUT_SET, 1)

    assert seen == [("r", GPIO_OUT), ("w", GPIO_OUT_SET)]
    assert native.sio.gpio_value == 1


@pytest.mark.parametrize("pure", [True, False], ids=["pure", "native"])
def test_a_divide_by_zero_surfaces_from_the_bus_write_and_the_bus_keeps_working(pure):
    chip = make_chip(Native)
    chip.sio = (PureSIO if pure else NativeSIO)(chip)

    with pytest.raises(ZeroDivisionError):
        chip.write_uint32(SIO + DIV_UDIVISOR, 0x100000000)

    chip.write_uint32(SIO + GPIO_OUT_SET, 0b1)  # nothing stale was left behind
    assert chip.read_uint32(SIO + GPIO_OUT) == 1


def test_a_pin_update_that_raises_surfaces_from_the_register_write_that_caused_it():
    class Boom(Exception):
        pass

    for pure in (True, False):
        chip = make_chip(Native)
        chip.sio = (PureSIO if pure else NativeSIO)(chip)

        class FailingPins:
            def __init__(self, real):
                self.real = real

            def __len__(self):
                return len(self.real)

            def __getitem__(self, index):
                if index == 2:
                    raise Boom("pin 2")
                return self.real[index]

        chip.gpio = FailingPins(chip.gpio)

        with pytest.raises(Boom):
            chip.write_uint32(SIO + GPIO_OE_SET, 0b100)

        assert chip.sio.gpio_output_enable == 0b100  # the register was updated before the pins were told
