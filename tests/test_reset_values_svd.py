"""A fresh chip reads every register as Raspberry Pi's SVD says it resets, except the registers listed with a reason (docs/records/0096-cpp-mcu-core.md, gate G3).

`tests/data/rp2040_reset_values.json` is the SVD (scripts/audit/svd_reset_values.py); `tests/data/rp2040_reset_deviations.json` is every register on which the chip is *meant* to read otherwise - a
constant of the silicon whose SVD reset is a placeholder, a live status, the state the boot ROM leaves, a datasheet text that contradicts the SVD, a feature not implemented - each with its reason
and the value the chip reads. The test holds both builds to exactly that list: a new register that differs fails, and so does a listed one that has started to agree (the entry is then stale) or
that reads another value (it changed without anyone deciding so). Entries of category `unreviewed` are the open questions.
"""

import json
from pathlib import Path

import pytest
from utils.chip_pair import Native, PurePython, make_chip

DATA = Path(__file__).parent / "data"
REGISTERS = [
    r
    for r in json.loads((DATA / "rp2040_reset_values.json").read_text())["registers"]
    if not r["read_modifies"] and r["mask"] and not r["name"].startswith(("USB_DEVICE_DPRAM.", "VIRTUAL."))
]
DEVIATIONS = {d["name"]: d for d in json.loads((DATA / "rp2040_reset_deviations.json").read_text())["deviations"]}
CATEGORIES = {"constant", "status", "boot", "datasheet", "model", "unimplemented", "unreviewed"}


def test_the_deviation_list_is_consistent_with_the_svd_table():
    by_name = {r["name"]: r for r in REGISTERS}
    assert set(DEVIATIONS) <= set(by_name)
    for name, entry in DEVIATIONS.items():
        register = by_name[name]
        assert entry["category"] in CATEGORIES and entry["reason"], name
        assert entry["address"] == register["address"], name
        assert entry["svd"] == register["reset"] & register["mask"], name
        assert entry["ours"] != entry["svd"], f"{name} is listed but does not deviate"


@pytest.mark.parametrize("cls", [PurePython, Native], ids=["pure", "native"])
def test_a_fresh_chip_reads_the_svd_reset_values_but_for_the_listed_registers(cls):
    chip = make_chip(cls)
    unexpected, changed = [], []
    for register in REGISTERS:
        value = chip.read_uint32(register["address"]) & register["mask"]
        wanted = register["reset"] & register["mask"]
        listed = DEVIATIONS.get(register["name"])
        if listed is None:
            if value != wanted:
                unexpected.append(f"{register['name']}: chip 0x{value:x}, SVD 0x{wanted:x}")
        elif value != listed["ours"]:
            changed.append(f"{register['name']}: chip 0x{value:x}, listed 0x{listed['ours']:x}")
    assert not unexpected, f"{len(unexpected)} registers differ from the SVD and are not listed: {unexpected[:10]}"
    assert not changed, f"{len(changed)} listed registers read another value now: {changed[:10]}"
