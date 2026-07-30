import struct

__all__ = (
    "decode_hex_buf",
    "decode_hex_uint32",
    "decode_hex_uint32_array",
    "encode_hex_buf",
    "encode_hex_byte",
    "encode_hex_uint32",
    "encode_hex_uint32_be",
    "gdb_checksum",
    "gdb_message",
)


def encode_hex_byte(value: int) -> str:
    return format(value, "02x")


def encode_hex_buf(buf: bytes) -> str:
    return buf.hex()


def encode_hex_uint32_be(value: int) -> str:
    return struct.pack(">I", value & 0xFFFFFFFF).hex()


def encode_hex_uint32(value: int) -> str:
    # Uint32Array uses platform (little-endian) byte order in JS
    return struct.pack("<I", value & 0xFFFFFFFF).hex()


def decode_hex_buf(encoded: str) -> bytes:
    return bytes.fromhex(encoded)


def decode_hex_uint32_array(encoded: str) -> list[int]:
    buf = decode_hex_buf(encoded)
    count = len(buf) // 4
    return list(struct.unpack(f"<{count}I", buf[: count * 4]))


def decode_hex_uint32(encoded: str) -> int:
    return decode_hex_uint32_array(encoded)[0]


def gdb_checksum(text: str) -> str:
    value = sum(ord(c) for c in text) & 0xFF
    return encode_hex_byte(value)


def gdb_message(value: str) -> str:
    return f"${value}#{gdb_checksum(value)}"
