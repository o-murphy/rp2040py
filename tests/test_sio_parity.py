"""The native SIO (C++ `SioBlock`, docs/records/0096-cpp-mcu-core.md Phase 2) against the pure-Python reference.

Both blocks are built explicitly (never through the `rp2040py.sio` facade, which hands out the pure class for both under
RP2040PY_SKIP_CYTHON=1) on a stand-in chip that records every edge of the block: the pins `check_for_updates()`d, the
cycles charged to the core, the messages logged. Randomized sessions of register reads and writes (every register, the
spinlocks, both interpolators, awkward values), direct attribute pokes, resets, pin-level changes and injected failures
run on both; then everything observable must be identical: every value read (the divider's quotient stays fractional),
every exception, the cycle count, the pin-update calls in order, the log, and all of the registers - the interpolators'
included.
"""

import random
from types import SimpleNamespace

import pytest

pytest.importorskip("rp2040py.native._sio", reason="the native extension is not built")

from rp2040py._sio import (
    DIV_CSR,
    DIV_QUOTIENT,
    DIV_REMAINDER,
    DIV_SDIVIDEND,
    DIV_SDIVISOR,
    DIV_UDIVIDEND,
    DIV_UDIVISOR,
    GPIO_HI_IN,
    GPIO_IN,
    GPIO_OE,
    GPIO_OE_SET,
    GPIO_OUT,
    GPIO_OUT_SET,
    INTERP0_BASE_1AND0,
    INTERP1_PEEK_FULL,
    SPINLOCK0,
    SPINLOCK31,
)
from rp2040py._sio import RPSIO as PureSIO
from rp2040py.native._sio import RPSIO as NativeSIO

REGISTERS = [
    *range(0x000, 0x080, 4),  # CPUID .. DIV_CSR, including the FIFO (warns) and the gaps
    *range(0x080, 0x100, 4),  # both interpolators
    *range(SPINLOCK0, SPINLOCK31 + 4, 4),
]
ATTRIBUTES = ["gpio_value", "gpio_output_enable", "qspi_gpio_value", "qspi_gpio_output_enable"]
STATE = [
    *ATTRIBUTES,
    "div_dividend",
    "div_divisor",
    "div_quotient",
    "div_remainder",
    "div_csr",
    "spin_lock",
]
INTERP_STATE = [
    "accum0",
    "accum1",
    "base0",
    "base1",
    "base2",
    "ctrl0",
    "ctrl1",
    "result0",
    "result1",
    "result2",
    "smresult0",
    "smresult1",
]


class Boom(Exception):
    pass


class Probe:
    """One SIO implementation on a stand-in chip that records everything the block does to it."""

    def __init__(self, cls):
        self.log = []
        self.fail_pin_at = None  # the Nth pin update from now raises
        self.fail_input = False  # the next read of the GPIO input levels raises
        outer = self

        class Pin:
            def __init__(self, index):
                self.index = index

            def check_for_updates(self):
                outer.log.append(("pin", self.index))
                if outer.fail_pin_at is not None:
                    outer.fail_pin_at -= 1
                    if outer.fail_pin_at < 0:
                        outer.fail_pin_at = None
                        raise Boom("pin")

        class Chip:
            def __init__(self):
                self.core = SimpleNamespace(cycles=0)
                self.qspi = [SimpleNamespace(input_value=False) for _ in range(6)]
                self.gpio = [Pin(i) for i in range(30)]
                self.logger = SimpleNamespace(**{n: outer._logger(n) for n in ("debug", "info", "warning", "error")})
                self._levels = 0

            @property
            def gpio_values(self):
                if outer.fail_input:
                    outer.fail_input = False
                    raise Boom("input")
                return self._levels

        self.chip = Chip()
        self.sio = cls(self.chip)

    def _logger(self, level):
        return lambda component, message: self.log.append((level, component, message))

    def apply(self, op):
        kind = op[0]
        try:
            if kind == "read":
                return ("r", self.sio.read_uint32(op[1]))
            if kind == "write":
                self.sio.write_uint32(op[1], op[2])
            elif kind == "divide":
                self.sio.update_hardware_divider(op[1])
            elif kind == "poke":
                setattr(self.sio, op[1], op[2])
            elif kind == "reset":
                self.sio.reset()
            elif kind == "levels":
                self.chip._levels = op[1]
                for pin, level in zip(self.chip.qspi, op[2], strict=True):
                    pin.input_value = level
            elif kind == "fail_pin":
                self.fail_pin_at = op[1]
            elif kind == "fail_input":
                self.fail_input = True
        except Boom as error:
            return ("boom", str(error))
        except ZeroDivisionError as error:
            return ("zerodiv", str(error))
        return None

    def observe(self):
        state = {name: getattr(self.sio, name) for name in STATE}
        for index, interp in enumerate((self.sio.interp0, self.sio.interp1)):
            state.update({f"i{index}.{name}": getattr(interp, name) for name in INTERP_STATE})
        return state, self.chip.core.cycles


def _value(rng):
    return rng.choice(
        [
            0, 1, 2, 3, 7, 0xF, 0xFF, 0x100, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x100000000,
            -1, -2, -16, -0x80000000, 0x12345678, 0x3FFFFFFF, 0x40000000,
            rng.getrandbits(32), rng.getrandbits(32), -rng.getrandbits(31), rng.getrandbits(8),
        ]
    )  # fmt: skip


def _script(seed, length=500):
    rng = random.Random(seed)
    ops = []
    interesting = [GPIO_IN, GPIO_HI_IN, GPIO_OUT, GPIO_OE, GPIO_OUT_SET, GPIO_OE_SET, DIV_UDIVIDEND, DIV_UDIVISOR]
    interesting += [
        DIV_SDIVIDEND,
        DIV_SDIVISOR,
        DIV_QUOTIENT,
        DIV_REMAINDER,
        DIV_CSR,
        INTERP0_BASE_1AND0,
        INTERP1_PEEK_FULL,
    ]
    for _ in range(length):
        offset = rng.choice(interesting) if rng.random() < 0.35 else rng.choice(REGISTERS)
        if rng.random() < 0.03:
            offset = rng.randrange(0, 0x10000000) & ~3  # far outside the register file
        roll = rng.random()
        if roll < 0.35:
            ops.append(("read", offset))
        elif roll < 0.8:
            ops.append(("write", offset, _value(rng)))
        elif roll < 0.84:
            ops.append(("divide", rng.random() < 0.5))
        elif roll < 0.88:
            ops.append(("poke", rng.choice(ATTRIBUTES), rng.getrandbits(32) & rng.choice([0xFFFFFFFF, 0x3FFFFFFF])))
        elif roll < 0.9:
            ops.append(("reset",))
        elif roll < 0.95:
            ops.append(("levels", rng.getrandbits(30), [rng.random() < 0.5 for _ in range(6)]))
        elif roll < 0.98:
            ops.append(("fail_pin", rng.randrange(3)))
        else:
            ops.append(("fail_input",))
    return ops


@pytest.mark.parametrize("seed", range(80))
def test_the_native_sio_matches_the_pure_python_sio(seed):
    pure, native = Probe(PureSIO), Probe(NativeSIO)

    for index, op in enumerate(_script(seed)):
        expected, got = pure.apply(op), native.apply(op)
        assert got == expected, f"op {index} {op}: native {got!r}, pure {expected!r}"
        assert native.observe() == pure.observe(), f"state after op {index} {op}"
        assert native.log == pure.log, f"log/pin calls after op {index} {op}"

    assert any(entry[0] == "pin" for entry in pure.log), "the session should have updated some pins"


def test_the_two_blocks_are_the_two_implementations():
    assert Probe(PureSIO).sio.__class__.__module__ == "rp2040py._sio"
    assert Probe(NativeSIO).sio.__class__.__module__ == "rp2040py.native._sio"


def test_the_divider_quotient_stays_fractional_and_only_the_bus_truncates_it():
    for cls in (PureSIO, NativeSIO):
        probe = Probe(cls)
        probe.sio.write_uint32(DIV_UDIVIDEND, 100)
        probe.sio.write_uint32(DIV_UDIVISOR, 7)

        assert probe.sio.read_uint32(DIV_QUOTIENT) == pytest.approx(100 / 7)
        assert probe.sio.read_uint32(DIV_REMAINDER) == 2
        assert probe.chip.core.cycles == 16  # eight per recompute, one per operand write


def test_a_divisor_that_is_zero_as_32_bits_raises_and_leaves_the_result_alone():
    for cls in (PureSIO, NativeSIO):
        probe = Probe(cls)
        probe.sio.write_uint32(DIV_UDIVIDEND, 50)
        probe.sio.write_uint32(DIV_UDIVISOR, 5)
        before, cycles = probe.observe()[0].copy(), probe.chip.core.cycles

        with pytest.raises(ZeroDivisionError):
            probe.sio.write_uint32(DIV_UDIVISOR, 0x100000000)

        after = probe.observe()[0]
        assert probe.chip.core.cycles == cycles
        assert after == {**before, "div_divisor": 0x100000000}


# --- the interpolators, with control words that mean something ---------------------------------------
# Uniformly random 32-bit control words rarely make a lane do anything but pass its input through; here every field is
# drawn on purpose (shift, mask range, signedness, cross inputs, add-raw, forced MSBs, blend, clamp) and the operands are
# the values that sit on the edges of the arithmetic.


def _ctrl(rng):
    word = rng.randrange(32) | (rng.randrange(32) << 5) | (rng.randrange(32) << 10)
    word |= rng.getrandbits(1) << 15  # signed
    word |= rng.getrandbits(1) << 16  # cross_input
    word |= rng.getrandbits(1) << 17  # cross_result
    word |= rng.getrandbits(1) << 18  # add_raw
    word |= rng.randrange(4) << 19  # force_msb
    word |= (rng.random() < 0.4) << 21  # blend (acts from lane 0's word of INTERP0 only, but is stored by both)
    word |= (rng.random() < 0.3) << 22  # clamp (lane 0 of INTERP1 only, likewise)
    return word


def _interp_script(seed, length=300):
    rng = random.Random(seed)
    ops = []
    edges = [
        0,
        1,
        0xFF,
        0x100,
        0x7FFFFFFF,
        0x80000000,
        0xFFFFFFFF,
        -1,
        -0x80000000,
        rng.getrandbits(32),
        rng.getrandbits(32),
    ]
    for _ in range(length):
        base = rng.choice([0x080, 0x0C0])  # INTERP0 / INTERP1
        roll = rng.random()
        if roll < 0.2:
            ops.append(("write", base + 0x2C, _ctrl(rng)))
        elif roll < 0.4:
            ops.append(("write", base + 0x30, _ctrl(rng)))
        elif roll < 0.7:
            ops.append(
                ("write", base + rng.choice([0x00, 0x04, 0x08, 0x0C, 0x10, 0x34, 0x38, 0x3C]), rng.choice(edges))
            )
        else:
            ops.append(
                (
                    "read",
                    base + rng.choice([0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x34, 0x38]),
                )
            )
    return ops


@pytest.mark.parametrize("seed", range(120))
def test_the_native_interpolators_match_the_pure_python_ones(seed):
    pure, native = Probe(PureSIO), Probe(NativeSIO)
    reads = 0

    for index, op in enumerate(_interp_script(seed)):
        expected, got = pure.apply(op), native.apply(op)
        assert got == expected, f"op {index} {op:}: native {got!r}, pure {expected!r}"
        assert native.observe() == pure.observe(), f"state after op {index} {op}"
        reads += op[0] == "read"

    assert reads
