"""The native bus (C++ MemoryMap, docs/records/0096-cpp-mcu-core.md Phase 1) against the pure-Python bus.

Both `RP2040` implementations get the same randomized and edge-case 8/16/32-bit accesses over the
boot ROM, the four flash mirrors, SRAM, USB DPRAM and the gaps between them. Every read must return
the same value, the same writes must leave the same bytes, and the same USB-DPRAM hook calls must
fire. This is what holds the C++ translation to the semantics it replaced, quirks included (flash
mirrors are read-only; sub-word accesses never use a mirror; the boot ROM is word-indexed; a DPRAM
write is reported to `usb_ctrl.dpram_updated`).

Not compared at random, because the old code had no defined answer: an unaligned 16-bit access (`RP2040.write_uint16`
says "we assume that address is 16-bit aligned"; the pure and Cython buses disagreed about an odd address, and
the Cython one wrote past a 4-byte scratch buffer). The one case that used to raise on the pure side - an access whose
bytes run past the end of a memory (rp2040js fixed the same crash in 1.4.1) - is defined now and held by
`test_an_access_running_past_the_end_of_a_memory_is_served_alike`: the C++ map reports it as "not handled", which the
caller turns into an ordinary unmapped access, and the pure bus does the same.
"""

import random

import pytest
from utils.chip_pair import Native, PurePython, make_chip

BOOTROM, BOOTROM_SIZE = 0x00000000, 16 * 1024
FLASH, FLASH_SIZE, FLASH_WINDOW = 0x10000000, 16 * 1024 * 1024, 0x04000000
SRAM, SRAM_SIZE = 0x20000000, 264 * 1024
DPRAM, DPRAM_SIZE = 0x50100000, 4 * 1024

# (name, base, bytes of backing memory, bytes of address space reads cover)
REGIONS = [
    ("bootrom", BOOTROM, BOOTROM_SIZE, BOOTROM_SIZE),
    ("flash", FLASH, FLASH_SIZE, FLASH_WINDOW),
    ("sram", SRAM, SRAM_SIZE, SRAM_SIZE),
    ("dpram", DPRAM, DPRAM_SIZE, DPRAM_SIZE),
]


def _addresses(rng):
    """Edge addresses of every region, plus the gaps around them, plus random ones inside."""
    out = []
    for _name, base, size, window in REGIONS:
        for edge in (
            0,
            1,
            2,
            3,
            4,
            size - 8,
            size - 4,
            size - 3,
            size - 2,
            size - 1,
            size,
            size + 1,
            window - 4,
            window - 1,
            window,
        ):
            out.append(base + edge)
            out.append((base + edge) & 0xFFFFFFFF)
        out += [base - 4, base - 1]
        out += [base + rng.randrange(window) for _ in range(300)]
    # All four flash mirrors, explicitly.
    for mirror in range(4):
        out += [FLASH + mirror * 0x01000000 + off for off in (0, 4, 0x1234, 0x00FFFFFC)]
    out += [0x40000000 - 4, 0x4FFFFFFC, 0x50000000, 0x60000000]  # unmapped (peripherals are not exercised here)
    return [a for a in out if 0 <= a <= 0xFFFFFFFF]


def _operations(seed, count):
    rng = random.Random(seed)
    addresses = _addresses(rng)
    for _ in range(count):
        width = rng.choice((8, 16, 32))
        address = rng.choice(addresses)
        if width == 16 or rng.random() < 0.7:  # 16-bit accesses are always aligned; 32-bit ones mostly
            address &= ~(width // 8 - 1)
        yield rng.choice(("r", "w")), width, address, rng.getrandbits(32)


def _apply(chip, kind, width, address, value):
    if kind == "r":
        return {8: chip.read_uint8, 16: chip.read_uint16, 32: chip.read_uint32}[width](address)
    {8: chip.write_uint8, 16: chip.write_uint16, 32: chip.write_uint32}[width](address, value)
    return None


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_native_bus_matches_the_pure_python_bus(seed):
    rng = random.Random(seed * 7919)
    bootrom_words = [rng.getrandbits(32) for _ in range(BOOTROM_SIZE // 4)]
    pure, native = make_chip(PurePython, bootrom_words), make_chip(Native, bootrom_words)

    for index, (kind, width, address, value) in enumerate(_operations(seed, 6000)):
        expected = _apply(pure, kind, width, address, value)
        got = _apply(native, kind, width, address, value)
        assert got == expected, f"op {index}: {kind}{width} @ {address:#x} (value {value:#x}): {got!r} != {expected!r}"

    assert native.hook_calls == pure.hook_calls
    assert bytes(native.sram) == bytes(pure.sram)
    assert bytes(native.flash) == bytes(pure.flash)
    assert bytes(native.usb_dpram) == bytes(pure.usb_dpram)
    assert list(native.bootrom) == list(pure.bootrom)


_ENDS = [
    ("sram", SRAM, SRAM_SIZE),
    ("dpram", DPRAM, DPRAM_SIZE),
    *[(f"flash mirror {mirror}", FLASH + mirror * 0x01000000, 0x01000000) for mirror in range(4)],
]


@pytest.mark.parametrize(("name", "base", "size"), _ENDS, ids=[end[0] for end in _ENDS])
def test_an_access_running_past_the_end_of_a_memory_is_served_alike(name, base, size):
    """The last bytes of a memory: a word that starts in it and ends past it, and a halfword on its last byte. Neither chip may raise, and they must agree on what comes back, what is
    written, and on the DPRAM hook. (The pure bus raised `struct.error` here, the C++ bus never did; rp2040js 1.4.1 fixed the same crash.)"""
    rng = random.Random(size)
    bootrom_words = [rng.getrandbits(32) for _ in range(BOOTROM_SIZE // 4)]
    pure, native = make_chip(PurePython, bootrom_words), make_chip(Native, bootrom_words)
    top = base + size
    for chip in (pure, native):
        for offset in range(8):  # the last bytes hold something that would show a wrong read or write
            if name == "sram":
                chip.sram[SRAM_SIZE - 8 + offset] = 0x10 + offset
            elif name == "dpram":
                chip.usb_dpram[DPRAM_SIZE - 8 + offset] = 0x10 + offset
            else:
                chip.flash[(top - 8 + offset) & (FLASH_SIZE - 1)] = 0x10 + offset

    for distance in (3, 2, 1):  # a word starting 3, 2 or 1 bytes before the end
        address = top - distance
        assert _apply(pure, "r", 32, address, 0) == _apply(native, "r", 32, address, 0) == 0xFFFFFFFF
        _apply(pure, "w", 32, address, 0xAABBCCDD)
        _apply(native, "w", 32, address, 0xAABBCCDD)
    address = top - 1  # a halfword on the last byte
    assert _apply(pure, "r", 16, address, 0) == _apply(native, "r", 16, address, 0)
    _apply(pure, "w", 16, address, 0xEEFF)
    _apply(native, "w", 16, address, 0xEEFF)

    assert native.hook_calls == pure.hook_calls
    assert bytes(native.sram) == bytes(pure.sram)
    assert bytes(native.flash) == bytes(pure.flash)
    assert bytes(native.usb_dpram) == bytes(pure.usb_dpram)


def test_a_dpram_write_reports_its_offset_and_the_original_value():
    chip = make_chip(Native, [0] * (BOOTROM_SIZE // 4))

    chip.write_uint32(DPRAM + 0x20, 0xDEADBEEF)
    chip.write_uint32(DPRAM + 0x24, -1)  # a signed Python int reaches the hook untouched, as before

    assert chip.hook_calls == [(0x20, 0xDEADBEEF), (0x24, -1)]
    assert chip.read_uint32(DPRAM + 0x20) == 0xDEADBEEF


def test_the_buffers_are_the_chips_own_memory_not_copies():
    """Zero-copy (record 0096, D2): the C++ map reads and writes the very bytes the Python views expose."""
    chip = make_chip(Native, [0] * (BOOTROM_SIZE // 4))

    chip.sram[0x100] = 0x5A  # a Python-side write is visible to the C++ bus ...
    assert chip.read_uint8(SRAM + 0x100) == 0x5A

    chip.write_uint32(SRAM + 0x200, 0x01020304)  # ... and a C++-side write to the Python view
    assert bytes(chip.sram[0x200:0x204]) == bytes([4, 3, 2, 1])

    chip.flash[0x10] = 0xA5  # flash, through a mirror
    assert chip.read_uint8(FLASH + 0x10) == 0xA5
    assert chip.read_uint32(FLASH + 0x02000000 + 0x10) & 0xFF == 0xA5
