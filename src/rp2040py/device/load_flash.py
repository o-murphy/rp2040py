"""UF2 / MicroPython / CircuitPython flash image loading.

UF2 is Microsoft's USB Flashing Format: https://github.com/microsoft/uf2
Implemented directly here (512-byte blocks, 32-byte header) rather than depending on an
external package, since rp2040py otherwise has zero runtime dependencies.
"""

import struct
from dataclasses import dataclass

from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040

__all__ = (
    "UF2Block",
    "decode_block",
    "load_circuitpython_flash_image",
    "load_micropython_flash_image",
    "load_uf2",
)

MICROPYTHON_FS_FLASH_START = 0xA0000
MICROPYTHON_FS_BLOCKSIZE = 4096
MICROPYTHON_FS_BLOCKCOUNT = 352

CIRCUITPYTHON_FS_FLASH_START = 0x100000
CIRCUITPYTHON_FS_BLOCKSIZE = 4096
CIRCUITPYTHON_FS_BLOCKCOUNT = 512

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


def _load_flash_image(filename: str, rp2040: RP2040, flash_start: int, block_size: int) -> None:
    with open(filename, "rb") as f:
        flash_address = flash_start
        while True:
            buffer = f.read(block_size)
            if len(buffer) != block_size:
                break
            rp2040.flash[flash_address : flash_address + len(buffer)] = buffer
            flash_address += len(buffer)


def load_micropython_flash_image(filename: str, rp2040: RP2040) -> None:
    _load_flash_image(filename, rp2040, MICROPYTHON_FS_FLASH_START, MICROPYTHON_FS_BLOCKSIZE)


def load_circuitpython_flash_image(filename: str, rp2040: RP2040) -> None:
    _load_flash_image(filename, rp2040, CIRCUITPYTHON_FS_FLASH_START, CIRCUITPYTHON_FS_BLOCKSIZE)


def load_uf2(filename: str, rp2040: RP2040) -> None:
    with open(filename, "rb") as f:
        while True:
            buffer = f.read(UF2_BLOCK_SIZE)
            if len(buffer) != UF2_BLOCK_SIZE:
                break
            block = decode_block(buffer)
            offset = block.flash_address - FLASH_START_ADDRESS
            rp2040.flash[offset : offset + len(block.payload)] = block.payload
