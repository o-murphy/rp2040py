"""
Minimal Intel HEX loader
Part of AVR8js

Copyright (C) 2019, Uri Shaked
"""


def load_hex(source: str, target: bytearray, base_address: int = 0) -> None:
    high_addr = 0

    for line in source.split("\n"):
        line = line.strip()
        if not line or line[0] != ":":
            continue

        type_code = line[7:9]

        if type_code == "04":
            high_addr = int(line[9:13], 16)
        elif type_code == "00":
            byte_count = int(line[1:3], 16)
            addr = ((high_addr << 16) | int(line[3:7], 16)) - base_address

            data_start = 9
            for i in range(byte_count):
                pos = data_start + i * 2
                target[addr + i] = int(line[pos : pos + 2], 16)
