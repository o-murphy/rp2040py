"""The peripheral window registry (C++ WindowMap, docs/records/0096-cpp-mcu-core.md Phase 1, D2).

The native chip dispatches peripheral accesses through a C++ table of windows, while `chip.peripherals` stays
the ordinary Python dict that blocks, boards and tests have always used. These tests pin both halves:
what the dict still does, and that the bus agrees with it - against the pure-Python chip wherever the
pure chip has an answer.
"""

import random

import pytest
from utils.chip_pair import Native, PurePython, make_chip

from rp2040py.peripherals._timer import RPTimer as PureTimer

FREE_KEY = 0x40068  # APB address 0x40068000: no built-in block lives there
FREE_BASE = FREE_KEY << 12
OTHER_KEY = 0x40070  # a second free window (0x40070000)
OTHER_BASE = OTHER_KEY << 12
TIMER_KEY, TIMER_BASE, TIMELR = 0x40054, 0x40054000, 0x0C
SET, CLEAR = 0x2000, 0x3000


class Block:
    """A pure-Python peripheral that records every call it gets."""

    def __init__(self, value=0xA5A5A5A5):
        self.value = value
        self.calls = []

    def read_uint32(self, offset):
        self.calls.append(("r", offset))
        return (self.value ^ offset) & 0xFFFFFFFF

    def write_uint32(self, offset, value):
        self.calls.append(("w", offset, value))

    def write_uint32_atomic(self, offset, value, atomic_type):
        self.calls.append(("a", offset, value, atomic_type))

    def reset(self):
        self.calls.append(("x",))


@pytest.fixture(params=[PurePython, Native], ids=["pure", "native"])
def chip(request):
    return make_chip(request.param)


# --- the dict is still a dict, and the bus follows it ---------------------------------------------


def test_peripherals_is_a_real_dict_with_the_same_contents_as_the_pure_chip():
    pure, native = make_chip(PurePython), make_chip(Native)

    assert isinstance(native.peripherals, dict)
    assert sorted(native.peripherals) == sorted(pure.peripherals)
    assert {k: type(v).__name__ for k, v in native.peripherals.items()} == {
        k: type(v).__name__ for k, v in pure.peripherals.items()
    }


def test_a_block_added_to_the_dict_is_reachable_through_the_bus(chip):
    block = Block()
    chip.peripherals[FREE_KEY] = block

    assert chip.read_uint32(FREE_BASE + 0x10) == (0xA5A5A5A5 ^ 0x10)
    chip.write_uint32(FREE_BASE + 0x20, 7)
    chip.write_uint32(FREE_BASE + SET + 0x20, 8)
    chip.write_uint32(FREE_BASE + CLEAR + 0x24, 9)

    assert block.calls == [("r", 0x10), ("a", 0x20, 7, 0), ("a", 0x20, 8, 2), ("a", 0x24, 9, 3)]


def test_a_read_sees_the_offset_with_its_alias_bits_and_a_write_does_not(chip):
    block = Block()
    chip.peripherals[FREE_KEY] = block

    chip.read_uint32(FREE_BASE + SET + 0x30)
    chip.write_uint32(FREE_BASE + SET + 0x30, 1)

    assert block.calls == [("r", SET + 0x30), ("a", 0x30, 1, 2)]


def test_sub_word_writes_reach_a_block_as_a_replicated_word(chip):
    block = Block()
    chip.peripherals[FREE_KEY] = block

    chip.write_uint8(FREE_BASE + 0x41, 0xAB)
    chip.write_uint16(FREE_BASE + 0x42, 0xCDEF)

    assert block.calls == [("a", 0x40, 0xABABABAB, 0), ("a", 0x40, 0xCDEFCDEF, 0)]


def test_the_caller_s_unmasked_value_reaches_the_block(chip):
    block = Block()
    chip.peripherals[FREE_KEY] = block

    chip.write_uint32(FREE_BASE, -2)
    chip.write_uint32(FREE_BASE, 0x1_2345_6789)

    assert block.calls == [("a", 0, -2, 0), ("a", 0, 0x1_2345_6789, 0)]


def test_replacing_a_built_in_block_takes_over_its_window_and_restoring_it_gives_it_back(chip):
    original = chip.peripherals[TIMER_KEY]
    stand_in = Block(value=0x600DF00D)

    chip.peripherals[TIMER_KEY] = stand_in
    assert chip.read_uint32(TIMER_BASE + TIMELR) == (0x600DF00D ^ TIMELR)
    assert stand_in.calls == [("r", TIMELR)]

    chip.peripherals[TIMER_KEY] = original
    chip.read_uint32(TIMER_BASE + TIMELR)  # the real block again: the stand-in sees nothing more
    assert stand_in.calls == [("r", TIMELR)]


def test_deleting_a_block_makes_its_window_unmapped(chip):
    chip.peripherals[FREE_KEY] = Block()
    del chip.peripherals[FREE_KEY]

    assert chip.read_uint32(FREE_BASE) == 0xFFFFFFFF  # "read from invalid memory address"
    chip.write_uint32(FREE_BASE, 1)  # "write to undefined address": warned, not raised


def test_a_method_replaced_on_the_instance_after_the_block_was_added_is_the_one_called(chip):
    # A Python block (the pure-Python TIMER here; the chip's own TIMER is a native, un-patchable block when the
    # extension is built): the trampoline looks the method up on every call, so a later replacement is honoured.
    timer = PureTimer(chip, "TIMER_BASE")
    chip.peripherals[TIMER_KEY] = timer
    timer.read_uint32 = lambda offset: 0x1234

    assert chip.read_uint32(TIMER_BASE + TIMELR) == 0x1234


def test_the_dict_methods_all_keep_the_registry_in_step(chip):
    a, b, c = Block(1), Block(2), Block(3)
    chip.peripherals.update({FREE_KEY: a})
    assert chip.read_uint32(FREE_BASE) == (1 ^ 0)

    assert chip.peripherals.setdefault(FREE_KEY, b) is a  # present: unchanged
    assert chip.peripherals.setdefault(OTHER_KEY, b) is b  # absent: added
    assert chip.read_uint32(OTHER_BASE) == (2 ^ 0)

    assert chip.peripherals.pop(OTHER_KEY) is b
    assert chip.read_uint32(OTHER_BASE) == 0xFFFFFFFF
    assert chip.peripherals.pop(OTHER_KEY, None) is None

    chip.peripherals |= {FREE_KEY: c}
    assert chip.read_uint32(FREE_BASE) == (3 ^ 0)

    chip.peripherals.clear()
    assert chip.read_uint32(FREE_BASE) == 0xFFFFFFFF
    assert chip.read_uint32(TIMER_BASE + TIMELR) == 0xFFFFFFFF  # the built-in blocks went too


def test_assigning_a_new_dict_replaces_the_whole_table(chip):
    block = Block()
    chip.peripherals = {FREE_KEY: block}

    assert chip.read_uint32(FREE_BASE) == (0xA5A5A5A5 ^ 0)
    assert chip.read_uint32(TIMER_BASE + TIMELR) == 0xFFFFFFFF
    assert list(chip.peripherals) == [FREE_KEY]


def test_a_key_no_address_can_produce_is_kept_in_the_dict_but_never_reached(chip):
    block = Block()
    chip.peripherals[FREE_KEY + 1] = block  # the bus's own keys have their low two bits clear

    assert chip.peripherals[FREE_KEY + 1] is block
    assert chip.read_uint32(FREE_BASE) == 0xFFFFFFFF
    assert block.calls == []


# --- exceptions and re-entrancy --------------------------------------------------------------------


class Exploding(Block):
    def read_uint32(self, offset):
        raise ValueError(f"read {offset:#x}")

    def write_uint32_atomic(self, offset, value, atomic_type):
        raise RuntimeError(f"write {offset:#x}")


def test_an_exception_in_a_block_reaches_the_caller_and_the_bus_keeps_working(chip):
    chip.peripherals[FREE_KEY] = Exploding()
    ok = Block()
    chip.peripherals[OTHER_KEY] = ok

    with pytest.raises(ValueError, match="read 0x8"):
        chip.read_uint32(FREE_BASE + 8)
    with pytest.raises(RuntimeError, match="write 0x10"):
        chip.write_uint32(FREE_BASE + 0x10, 1)
    with pytest.raises(RuntimeError):
        chip.write_uint8(FREE_BASE + 0x11, 1)

    assert chip.read_uint32(OTHER_BASE) == (0xA5A5A5A5 ^ 0)  # nothing stale was left behind
    assert chip.read_uint32(TIMER_BASE + TIMELR) is not None


class Reentrant(Block):
    """A block that reads another window (and tolerates that read failing) while serving its own."""

    def __init__(self, chip, other):
        super().__init__()
        self.chip, self.other = chip, other

    def read_uint32(self, offset):
        try:
            inner = self.chip.read_uint32(self.other + offset)
        except ValueError:
            inner = 0xEE
        return inner + 1


def test_a_block_may_call_back_into_the_bus_while_serving_a_read(chip):
    inner = Block()
    chip.peripherals[OTHER_KEY] = inner
    chip.peripherals[FREE_KEY] = Reentrant(chip, OTHER_BASE)

    assert chip.read_uint32(FREE_BASE + 0x14) == (0xA5A5A5A5 ^ 0x14) + 1
    assert inner.calls == [("r", 0x14)]


def test_a_failure_inside_a_nested_read_is_the_outer_block_s_to_handle(chip):
    chip.peripherals[OTHER_KEY] = Exploding()
    chip.peripherals[FREE_KEY] = Reentrant(chip, OTHER_BASE)

    assert chip.read_uint32(FREE_BASE) == 0xEE + 1  # the inner ValueError was caught by the outer block


# --- the two chips agree ---------------------------------------------------------------------------


def _session(seed):
    rng = random.Random(seed)
    for _ in range(1500):
        window = rng.choice([FREE_BASE, OTHER_BASE, OTHER_BASE + 0x4000, OTHER_BASE + 0x8000])  # the last two: unmapped
        offset = rng.choice([0, 4, 8, 0x3FFC, 0x1000, 0x2008, 0x3010, rng.randrange(0, 0x4000) & ~3])
        yield (
            rng.choice(("r32", "w32", "w16", "w8")),
            window + offset,
            rng.choice([0, 1, 0xFFFFFFFF, -1, rng.getrandbits(32)]),
        )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_native_and_pure_dispatch_the_same_accesses_to_the_same_blocks(seed):
    pure, native = make_chip(PurePython), make_chip(Native)
    pure_blocks, native_blocks = [Block(7), Block(8)], [Block(7), Block(8)]
    for chip, blocks in ((pure, pure_blocks), (native, native_blocks)):
        chip.peripherals[FREE_KEY] = blocks[0]
        chip.peripherals[OTHER_KEY] = blocks[1]

    for op, address, value in _session(seed):
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
        assert results[0] == results[1], f"{op} @ {address:#x}: pure {results[0]!r}, native {results[1]!r}"

    assert [b.calls for b in native_blocks] == [b.calls for b in pure_blocks]
