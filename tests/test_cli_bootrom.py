"""Tests for `_resolve_bootrom_words()` (`--bootrom` support for `run`/`micropython`/`kaluma`/
`bench`, see issue #11): resolving a `b0`/`b1`/`b2` tag or local `.elf`/`.bin` path into the
4096-word list `RP2040.load_bootrom()` expects.

The `.elf` parsing path is exercised against a hand-built minimal ELF32 (one `PT_LOAD` segment at
`p_paddr=0`), not a real downloaded bootrom release - no network dependency for these tests, same
philosophy as `test_firmware_retrieve.py`'s `_isolated_cwd` fixture, which this module also uses
via `retrieve()`.
"""

import struct

import pytest

from rp2040py import cli
from rp2040py.device.bootrom import BOOTROM_B1


def _build_minimal_elf(words: "list[int]") -> bytes:
    """A minimal valid little-endian ELF32 with a single `PT_LOAD` segment at `p_paddr=0`
    containing `words` (packed as little-endian `uint32`s) - just enough for `elftools` to parse,
    matching the shape real `pico-bootrom-rp2040` releases ship (verified against `b0.elf`/
    `b2.elf` while building this feature: one clean 16384-byte `PT_LOAD` at address 0)."""
    data = struct.pack(f"<{len(words)}I", *words)
    ehdr_size = 52
    phdr_size = 32
    data_offset = ehdr_size + phdr_size

    e_ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    ehdr = e_ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2,  # e_type: ET_EXEC
        40,  # e_machine: EM_ARM
        1,  # e_version
        0,  # e_entry
        ehdr_size,  # e_phoff
        0,  # e_shoff
        0,  # e_flags
        ehdr_size,  # e_ehsize
        phdr_size,  # e_phentsize
        1,  # e_phnum
        0,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )
    phdr = struct.pack(
        "<IIIIIIII",
        1,  # p_type: PT_LOAD
        data_offset,  # p_offset
        0,  # p_vaddr
        0,  # p_paddr
        len(data),  # p_filesz
        len(data),  # p_memsz
        5,  # p_flags: R+X
        4,  # p_align
    )
    return ehdr + phdr + data


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_omitted_bootrom_returns_the_bundled_b1_unchanged():
    assert cli._resolve_bootrom_words(None) is BOOTROM_B1


def test_local_elf_path_extracts_the_pt_load_segment_words(tmp_path):
    words = [0x20041F00, 0x000000EF, 0x00000035, 0xDEADBEEF]
    elf_path = tmp_path / "custom.elf"
    elf_path.write_bytes(_build_minimal_elf(words))

    assert cli._resolve_bootrom_words(str(elf_path)) == words


def test_local_bin_path_is_read_as_raw_little_endian_words(tmp_path):
    words = [0x11111111, 0x22222222, 0x33333333]
    bin_path = tmp_path / "custom.bin"
    bin_path.write_bytes(struct.pack("<3I", *words))

    assert cli._resolve_bootrom_words(str(bin_path)) == words


def test_unresolvable_source_exits_instead_of_raising(monkeypatch):
    monkeypatch.setattr(cli, "retrieve", lambda spec, image=None: None)

    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_bootrom_words("not-a-real-tag")

    assert exc_info.value.code == 1


def test_elf_without_a_pt_load_segment_at_address_zero_exits(tmp_path):
    # A PT_LOAD segment present, but not at p_paddr=0 (e.g. the USB DPRAM declaration real bootrom
    # releases also carry) - shouldn't be mistaken for the ROM image itself.
    data = struct.pack("<2I", 0, 0)
    ehdr_size, phdr_size = 52, 32
    e_ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    ehdr = e_ident + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, 0, ehdr_size, 0, 0, ehdr_size, phdr_size, 1, 0, 0, 0)
    phdr = struct.pack("<IIIIIIII", 1, ehdr_size + phdr_size, 0x50100400, 0x50100400, len(data), len(data), 5, 4)
    elf_path = tmp_path / "no_rom_segment.elf"
    elf_path.write_bytes(ehdr + phdr + data)

    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_bootrom_words(str(elf_path))

    assert exc_info.value.code == 1
