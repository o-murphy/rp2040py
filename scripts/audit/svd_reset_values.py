"""Turns the pico-sdk's `RP2040.svd` into the table of register reset values the tests hold the chip to (docs/records/0096-cpp-mcu-core.md, gate G3).

    python scripts/audit/svd_reset_values.py RP2040.svd tests/data/rp2040_reset_values.json

The SVD is Raspberry Pi's own description of the chip (https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/RP2040.svd, BSD-3-Clause, see the licence in the
output); the table is its `peripherals -> registers` flattened to `address, name, resetValue, the readable bits, whether a read has a side effect`, with the `derivedFrom` peripherals (UART1, SPI1, I2C1,
PIO1, PLL_USB) expanded. Nothing is interpreted here: which registers are read without side effects is the test's business (tests/test_reset_values_svd.py).
"""

import json
import sys
import xml.etree.ElementTree as ET

LICENSE = (
    "Copyright (c) 2021 Raspberry Pi Ltd. (RP2040.svd); Copyright 2020 (c) 2020 Raspberry Pi (Trading) Ltd. (pico-sdk LICENSE.TXT). SPDX-License-Identifier: BSD-3-Clause. "
    "Source: https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/RP2040.svd - redistribution under the BSD 3-clause licence of "
    "https://github.com/raspberrypi/pico-sdk/blob/master/LICENSE.TXT"
)


def _int(text: str) -> int:
    return int(text.strip(), 0)


def _registers(peripheral: ET.Element, base: int, name: str) -> "list[dict]":
    rows = []
    for register in peripheral.iter("register"):
        offset = _int(register.findtext("addressOffset"))
        mask = 0  # the bits that can be read (the write-only fields are left out)
        read_modifies = register.find(".//readAction") is not None
        for field in register.iter("field"):
            if field.findtext("access") == "write-only":
                continue
            high, low = (int(part) for part in field.findtext("bitRange").strip("[]").split(":"))  # "[20:16]"
            mask |= ((1 << (high - low + 1)) - 1) << low
        rows.append(
            {
                "address": base + offset,
                "name": f"{name}.{register.findtext('name')}",
                "reset": _int(register.findtext("resetValue") or "0"),
                "mask": mask,
                "read_modifies": read_modifies,  # reading it changes the chip (a FIFO pop, a clear-on-read)
            }
        )
    return rows


def convert(svd_path: str) -> dict:
    root = ET.parse(svd_path).getroot()
    peripherals = {p.findtext("name"): p for p in root.iter("peripheral")}
    rows: list[dict] = []
    for name, peripheral in peripherals.items():
        source = peripherals[peripheral.get("derivedFrom")] if peripheral.get("derivedFrom") else peripheral
        rows += _registers(source, _int(peripheral.findtext("baseAddress")), name)
    rows.sort(key=lambda row: row["address"])
    return {"license": LICENSE, "registers": rows}


if __name__ == "__main__":
    table = convert(sys.argv[1])
    with open(sys.argv[2], "w") as out:
        json.dump(table, out, indent=0)
        out.write("\n")
    print(f"{len(table['registers'])} registers")
