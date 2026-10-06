"""Register-level conformance of the pure-Python reference against the RP2040 datasheet (docs/records/0098-datasheet-conformance-audit.md).

The datasheet is not in the repository. Get it (https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf), turn it into text, and pass that:

    pdftotext -layout rp2040-datasheet.pdf rp2040.txt
    python scripts/audit/datasheet_conformance.py rp2040.txt TIMER ADC PWM          # blocks to check; none: all of them
    python scripts/audit/datasheet_conformance.py rp2040.txt --list                 # the blocks and how many registers each has

What it does, per register table of the datasheet ("<BLOCK>: <NAME> Register(s)", "Offset: 0x..", then the "Bits / Description / Type / Reset" rows): on a fresh pure-Python chip it reads
the register at BASE + offset and then writes it all ones and reads it back, and reports -

  unimplemented         the read warns "Unimplemented peripheral read" (a register the reference does not have)
  unimpl-wo             the same, for a register the datasheet lists as write-only (not a gap: nothing on the chip reads them)
  reset                 a *writable* field (RW/WC/SC/...) whose reset value differs from the datasheet's. Read-only fields are only noted: the reset column of a status bit is often
                        a 0x0 placeholder (ADC FCS.EMPTY says 0x0, the chip reads 1)
  reserved-or-ro-sticks bits that are reserved or read-only in the datasheet and changed after a write of ones (a register that stores the whole word)
  rw-not-sticking       plain RW bits that read 0 after a write of ones

What it cannot see: behaviour (what a register *does*), registers other than the first of a "CH0_..., CH1_..., ..." header, read side effects (a FIFO pop), and the write-to-clear/self-clearing
types, which it leaves alone. A finding is a question for the datasheet, not a verdict - the output is read by a person.
"""

import collections
import json
import os
import re
import sys

os.environ["RP2040PY_SKIP_CYTHON"] = (
    "1"  # the reference is what is judged; the native blocks are held to it by the lockstep oracles
)

BASES = {
    "SYSINFO": 0x40000000,
    "SYSCFG": 0x40004000,
    "CLOCKS": 0x40008000,
    "RESETS": 0x4000C000,
    "PSM": 0x40010000,
    "XOSC": 0x40024000,
    "PLL": 0x40028000,  # PLL_SYS; PLL_USB is the same registers at 0x4002c000
    "BUSCTRL": 0x40030000,
    "UART": 0x40034000,
    "SPI": 0x4003C000,
    "I2C": 0x40044000,
    "ADC": 0x4004C000,
    "PWM": 0x40050000,
    "TIMER": 0x40054000,
    "WATCHDOG": 0x40058000,
    "RTC": 0x4005C000,
    "ROSC": 0x40060000,
    "VREG_AND_CHIP_RESET": 0x40064000,
    "TBMAN": 0x4006C000,
    "DMA": 0x50000000,
    "PIO": 0x50200000,
    "SSI": 0x18000000,
    "SIO": 0xD0000000,
    "M0PLUS": 0xE0000000,
}

HEADER = re.compile(r"^\s{0,30}([A-Z0-9_]+): (.+?) Registers?\s*$")
OFFSET = re.compile(r"^\s*Offset: (0x[0-9a-fA-F]+)\s*$")
ROW = re.compile(r"^\s{10,40}(\d+(?::\d+)?)\s{2,}(\S.*)$")
TAIL = re.compile(r"\s(RW|RO|WO|SC|WC|W1C|RWF|RF|FIFO|-)\s+(0x[0-9a-fA-F]+|-)\s*$")
NAME = re.compile(r"\s+\S+\s+([A-Za-z0-9_\[\]]+):")
WRITABLE = ("RW", "WO", "SC", "WC", "W1C", "RWF")


def parse(path: str) -> list[dict]:
    """The datasheet's register tables: [{block, name, offset, fields: [(bits, field name or '?', type or '?', reset or '?')]}]."""
    text = open(path, encoding="utf-8", errors="replace").read().split("\n")

    def in_table(i: int) -> bool:
        for j in range(i, max(i - 200, 0), -1):
            if text[j].strip().startswith("Table "):
                return True
            if HEADER.match(text[j]) or OFFSET.match(text[j]):
                return False
        return False

    registers: list[dict] = []
    current = None
    i = 0
    while i < len(text):
        line = text[i]
        header = HEADER.match(line)
        if header:
            for j in range(i + 1, min(i + 8, len(text))):
                offset = OFFSET.match(text[j])
                if offset:
                    current = {
                        "block": header.group(1),
                        "name": header.group(2),
                        "offset": offset.group(1),
                        "fields": [],
                    }
                    registers.append(current)
                    i = j
                    break
        elif current is not None:
            row = ROW.match(line)
            if row and in_table(i):
                chunk = [line]
                k = i + 1
                while k < len(text) and not ROW.match(text[k]) and not HEADER.match(text[k]) and len(chunk) < 12:
                    if text[k].strip().startswith(("Table ", "RP2040 Datasheet")):
                        break
                    chunk.append(text[k])
                    k += 1
                kind, reset = "?", "?"
                for part in chunk:
                    tail = TAIL.search(part)
                    if tail:
                        kind, reset = tail.group(1), tail.group(2)
                        break
                name = NAME.match(line)
                current["fields"].append((row.group(1), name.group(1) if name else "?", kind, reset))
        i += 1
    return registers


def span(bits: str) -> tuple[int, int, int]:
    high, low = (int(x) for x in bits.split(":")) if ":" in bits else (int(bits),) * 2
    return high, low, ((1 << (high - low + 1)) - 1) << low


def masks(register: dict) -> dict[str, int]:
    out = {"known_mask": 0, "known_value": 0, "ro_mask": 0, "ro_value": 0, "writable": 0, "plain": 0}
    for bits, _name, kind, reset in register["fields"]:
        _high, low, mask = span(bits)
        if kind in WRITABLE:
            out["writable"] |= mask
        if kind == "RW":
            out["plain"] |= mask
        if reset.startswith("0x"):
            which = "ro" if kind == "RO" else "known"
            out[f"{which}_mask"] |= mask
            out[f"{which}_value"] |= (int(reset, 16) << low) & mask
    return out


def check(register: dict) -> dict:
    from rp2040py.rp2040 import RP2040

    block = register["block"]
    address = BASES[block] + int(register["offset"], 16)
    m = masks(register)
    write_only = all(f[2] == "WO" for f in register["fields"] if f[2] != "-") and bool(register["fields"])
    row: dict = {"reg": f"{block}.{register['name']}", "offset": register["offset"], "issues": [], "cats": []}
    chip = RP2040()
    warnings: list[str] = []
    chip.logger.warning = lambda _name, message: warnings.append(message)  # type: ignore[method-assign]
    try:
        before = chip.read_uint32(address) & 0xFFFFFFFF
    except Exception as error:  # noqa: BLE001 - a reference that raises is a finding
        row["issues"].append(f"read raised {type(error).__name__}: {error}")
        row["cats"].append("raises")
        return row
    if warnings:
        row["issues"].append("unimplemented (read warns)" + (" - write-only per datasheet" if write_only else ""))
        row["cats"].append("unimpl-wo" if write_only else "unimplemented")
        return row
    if (before ^ m["known_value"]) & m["known_mask"]:
        differing = (before ^ m["known_value"]) & m["known_mask"]
        row["issues"].append(
            f"reset: read 0x{before:08x}, datasheet 0x{m['known_value']:08x}, differing writable bits 0x{differing:08x}"
        )
        row["cats"].append("reset")
    if (before ^ m["ro_value"]) & m["ro_mask"]:
        row["issues"].append(
            f"note (read-only bits, the datasheet's reset may be a placeholder): read 0x{before:08x}, datasheet 0x{m['ro_value']:08x}"
        )
    try:
        chip.write_uint32(address, 0xFFFFFFFF)
        after = chip.read_uint32(address) & 0xFFFFFFFF
    except Exception as error:  # noqa: BLE001
        row["issues"].append(f"write/read-back raised {type(error).__name__}: {error}")
        row["cats"].append("raises")
        return row
    stuck = (before ^ after) & ~m["writable"] & 0xFFFFFFFF
    if stuck:
        row["issues"].append(
            f"reserved or read-only bits changed by a write of ones: 0x{stuck:08x} (read 0x{before:08x} -> 0x{after:08x}; datasheet writable mask 0x{m['writable']:08x})"
        )
        row["cats"].append("reserved-or-ro-sticks")
    lost = m["plain"] & ~after & 0xFFFFFFFF
    if lost:
        row["issues"].append(f"RW bits that read 0 after a write of ones: 0x{lost:08x} (read back 0x{after:08x})")
        row["cats"].append("rw-not-sticking")
    return row


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    registers = parse(argv[1])
    wanted = [a for a in argv[2:] if not a.startswith("--")]
    if "--list" in argv:
        for block, count in sorted(collections.Counter(r["block"] for r in registers).items()):
            print(f"{block:24} {count:3} registers" + ("" if block in BASES else "   (no base address here)"))
        return 0
    rows = [check(r) for r in registers if r["block"] in (wanted or BASES) and r["block"] in BASES]
    if "--json" in argv:
        json.dump(rows, sys.stdout, indent=1)
        return 0
    for row in rows:
        if row["issues"]:
            print(row["reg"], row["offset"])
            for issue in row["issues"]:
                print("    ", issue)
    categories = collections.Counter(c for r in rows for c in r["cats"])
    print("categories:", dict(categories))
    print(f"-- {len(rows)} registers checked, {sum(1 for r in rows if r['issues'])} with findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
