"""UF2 / MicroPython / CircuitPython flash image loading.

UF2 is Microsoft's USB Flashing Format: https://github.com/microsoft/uf2
Implemented directly here (512-byte blocks, 32-byte header) rather than depending on an
external package, since rp2040py otherwise has zero runtime dependencies.
"""

import struct
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import CIRCUITPYTHON, KALUMA, MICROPYTHON
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout

__all__ = (
    "UF2Block",
    "check_flash_image_size",
    "decode_block",
    "dump_circuitpython_flash_image",
    "dump_kaluma_flash_image",
    "dump_micropython_flash_image",
    "load_circuitpython_flash_image",
    "load_kaluma_flash_image",
    "load_kaluma_program",
    "load_micropython_flash_image",
    "load_uf2",
)

# littlefs block size (the flash sector-erase granularity) used to live here as three
# per-firmware-family module constants, all `4096` - see docs/records/0049's "Design update"
# section for why that was wrong: it's per-board/firmware data like `fs_start`/`fs_blockcount`,
# not a code-level universal, even though every board/family tracked so far happens to agree.
# Now sourced as `layout["fs_blocksize"]` from `firmware_retrieve.flash_layout()` below, same as
# `fs_start`/`fs_blockcount` always were.

# Kaluma's "user program" region - what `kaluma flash <file>` writes to over YMODEM, and what
# km_runtime_load() (src/runtime.c) auto-executes on every boot (unless GP22 is pulled low - see
# km_running_script_check() in targets/rp2/src/system.c; floating/pulled-up by default, same as
# this emulator's default GPIO state). KALUMA_PROG_SECTOR_BASE=4, KALUMA_PROG_SECTOR_COUNT=128 in
# board.h: 0xFC000 + 4*4096 = 0x100000. Sits directly before the fs region (0x180000).
KALUMA_PROG_MAX_SIZE = 128 * 4096  # KALUMA_PROG_SECTOR_COUNT * KALUMA_FLASH_SECTOR_SIZE

UF2_BLOCK_SIZE = 512
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30


@dataclass
class UF2Block:
    flash_address: int
    payload: bytes


def decode_block(buffer: bytes) -> UF2Block:
    (
        magic_start0,
        magic_start1,
        _flags,
        target_addr,
        payload_size,
        _block_no,
        _num_blocks,
        _file_size,
    ) = struct.unpack("<8I", buffer[:32])
    (magic_end,) = struct.unpack("<I", buffer[508:512])
    if magic_start0 != UF2_MAGIC_START0 or magic_start1 != UF2_MAGIC_START1 or magic_end != UF2_MAGIC_END:
        raise ValueError("Invalid UF2 block magic numbers")
    return UF2Block(flash_address=target_addr, payload=buffer[32 : 32 + payload_size])


def check_flash_image_size(filename: PathLike, block_size: int, block_count: int) -> None:
    """Raises `ValueError` unless `filename` is exactly `block_size * block_count` bytes - the
    size `build_littlefs_image()` always produces (see `mklittlefs.py`'s `UserContext(buffsize=
    block_size * block_count)`). A mismatch means either a truncated/corrupted file, or an image
    built for a different target's littlefs region (e.g. Kaluma's 128-block image loaded where
    MicroPython's 352-block one is expected) - both worth rejecting up front rather than silently
    loading a partial filesystem or overrunning past the end of the flash region into whatever
    comes after it (`_load_flash_image()` below has no bounds check of its own)."""
    expected_size = block_size * block_count
    actual_size = Path(filename).stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"{filename!r} is {actual_size} bytes, expected exactly {expected_size} "
            f"({block_count} blocks x {block_size} bytes) - built for a different target, or truncated?"
        )


def _load_flash_image(filename: PathLike, rp2040: RP2040, flash_start: int, block_size: int, block_count: int) -> None:
    check_flash_image_size(filename, block_size, block_count)
    flash_end = flash_start + block_size * block_count
    with open(filename, "rb") as f:
        rp2040.flash[flash_start:flash_end] = f.read()


def load_micropython_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(MICROPYTHON, board)
    _load_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])


def load_circuitpython_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(CIRCUITPYTHON, board)
    _load_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])


def load_kaluma_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(KALUMA, board)
    _load_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])


def load_kaluma_program(filename: PathLike, rp2040: RP2040, board: str) -> None:
    with open(filename, "rb") as f:
        source = f.read()
    # km_prog_end() (src/prog.c) appends this NUL terminator on real hardware too -
    # km_prog_get_size() strlen()s the flash content to find the program's length.
    data = source + b"\x00"
    if len(data) > KALUMA_PROG_MAX_SIZE:
        raise ValueError(
            f"{filename!r} is {len(source)} bytes, exceeds Kaluma's "
            f"{KALUMA_PROG_MAX_SIZE}-byte user-program flash region"
        )
    prog_start = _flash_layout(KALUMA, board)["prog_start"]
    rp2040.flash[prog_start : prog_start + len(data)] = data


def load_uf2(filename: PathLike, rp2040: RP2040) -> None:
    with open(filename, "rb") as f:
        while True:
            buffer = f.read(UF2_BLOCK_SIZE)
            if len(buffer) != UF2_BLOCK_SIZE:
                break
            block = decode_block(buffer)
            offset = block.flash_address - FLASH_START_ADDRESS
            rp2040.flash[offset : offset + len(block.payload)] = block.payload


def _dump_flash_image(filename: PathLike, rp2040: RP2040, flash_start: int, block_size: int, block_count: int) -> None:
    flash_end = flash_start + block_size * block_count
    with open(filename, "wb") as f:
        f.write(rp2040.flash[flash_start:flash_end])


def dump_micropython_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(MICROPYTHON, board)
    _dump_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])


def dump_circuitpython_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(CIRCUITPYTHON, board)
    _dump_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])


def dump_kaluma_flash_image(filename: PathLike, rp2040: RP2040, board: str) -> None:
    layout = _flash_layout(KALUMA, board)
    _dump_flash_image(filename, rp2040, layout["fs_start"], layout["fs_blocksize"], layout["fs_blockcount"])
