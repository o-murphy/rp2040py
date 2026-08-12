#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Refreshes `src/rp2040py/utils/firmware_specs.json` in full - all four firmware families in one
pass, one script, matching docs/CYW43_WIFI_BACKLOG.md's "Candidate redesign" section: `known_versions`
becomes a flat `tag -> url` map fetched at development time and committed straight into
`firmware_specs.json`, not generated at request time by `retrieve()`.

**`micropython`/`circuitpython`/`kaluma` are board-aware** (`boards: {board: {tag: url}}`) -
per-board firmware actually differs for these (cyw43-driver/network stack compiled in or not).
**`bootrom` is deliberately NOT board-aware** (`known_versions: {tag: url}`, flat) - it's a mask
ROM baked into the RP2040 die itself at manufacturing time, identical across every board that chip
ends up on, versioned only by silicon stepping (B0/B1/B2) - see docs/CYW43_WIFI_BACKLOG.md's
"Historical/closed" section for why this one firmware family is explicitly out of scope for
board-variant resolution. Keeping both shapes in one script (rather than one script per family)
means that distinction lives in exactly one place instead of needing to stay in sync across
several.

Sources, one function each below:
- MicroPython: `https://micropython.org/download/{RPI_PICO,RPI_PICO_W}/`, scraped HTML.
- CircuitPython: the public S3 bucket's own REST listing API (`?prefix=...` on the bucket root
  returns an XML `ListBucketResult`, not to be confused with `/index.html?prefix=...`, which is a
  JS-rendered listing page - not scrapable without a JS engine). Gets the *full* version history,
  correcting an earlier assumption that only the current stable/prerelease were reachable at all.
- Kaluma: the GitHub releases API (`kaluma-project/kaluma`) - confirmed here (2026-08-12) that,
  contrary to this doc's own original "no clean per-board split" note, Kaluma *does* publish
  separate `kaluma-rp2-pico-*`/`kaluma-rp2-pico-w-*` assets on every release since 1.1.0.
- Bootrom: the GitHub releases API (`raspberrypi/pico-bootrom-rp2040`) - one `<tag>.elf` asset per
  release, three releases total (b0/b1/b2), all board-agnostic per the above.

Usage: `uv run scripts/fetch_firmware.py` (stdlib only - no inline deps to install). Re-run
whenever new releases land in any of the four; diff the resulting `firmware_specs.json` change
before committing, same as any other generated-but-committed file.
"""

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_SPECS_PATH = Path(__file__).resolve().parent.parent / "src" / "rp2040py" / "utils" / "firmware_specs.json"

_MICROPYTHON_BOARD_SLUGS = {"pico": "RPI_PICO", "pico_w": "RPI_PICO_W"}
_CIRCUITPYTHON_BOARD_SLUGS = {"pico": "raspberry_pi_pico", "pico_w": "raspberry_pi_pico_w"}
# Kaluma also ships pico2/pico2_w assets, deliberately not fetched here - this project only
# emulates the original RP2040, not RP2350/Pico 2.
_KALUMA_BOARD_ASSET_SUFFIXES = {"pico": "pico", "pico_w": "pico-w"}

_S3_XML_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

# Where each firmware family's own filesystem (Kaluma also: "user program") flash region actually
# lives - not fetched from any live source (there's no API for it; these are board hardware-config
# constants, not release metadata), curated by hand from that firmware's own upstream board config
# and re-asserted here every run so `firmware_specs.json`'s copy can't silently drift from its real
# source. See docs/records/0035-board-aware-fs-flash-offset.md for the full derivation/root-cause
# writeup this was added for.
#
# MicroPython (ports/rp2/boards/{RPI_PICO,RPI_PICO_W}/mpconfigboard.h, ports/rp2/rp2_flash.c):
# MICROPY_HW_FLASH_STORAGE_BASE = PICO_FLASH_SIZE_BYTES(0x200000) - MICROPY_HW_FLASH_STORAGE_BYTES
# - RPI_PICO: 1408*1024 -> 0x200000-0x160000 = 0xa0000, 352 blocks (0x160000/4096).
# - RPI_PICO_W: 848*1024 (smaller - leaves more flash for the CYW43 driver/lwIP stack) ->
#   0x200000-0xd4000 = 0x12c000, 212 blocks (0xd4000/4096).
# Not yet verified stable across every tracked MicroPython version tag (checked against v1.28.0
# only) - see 0035's "Open questions".
_MICROPYTHON_FLASH_LAYOUT = {
    "pico": {"fs_start": "0xa0000", "fs_blockcount": 352},
    "pico_w": {"fs_start": "0x12c000", "fs_blockcount": 212},
}

# Kaluma (kaluma-project/kaluma, targets/rp2/boards/{pico,pico-w}/board.h + board.js): identical
# between pico and pico-w (KALUMA_FLASH_SECTOR_COUNT=260, KALUMA_PROG_SECTOR_BASE=4,
# KALUMA_PROG_SECTOR_COUNT=128, board.js's `new Flash(132, 128)`) - Kaluma reserves the same fixed
# code budget regardless of board, unlike MicroPython. Still stored per-board (both keys pointing
# at the same values) so every firmware family's flash layout lives in this one uniform shape.
_KALUMA_FLASH_LAYOUT = {
    "pico": {"prog_start": "0x100000", "fs_start": "0x180000", "fs_blockcount": 128},
    "pico_w": {"prog_start": "0x100000", "fs_start": "0x180000", "fs_blockcount": 128},
}

# CircuitPython (adafruit/circuitpython, ports/raspberrypi/{mpconfigport.h,link-rp2040.ld,
# boards/raspberry_pi_pico{,_w}/{mpconfigboard.mk,link.ld}}): CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR
# = CIRCUITPY_FIRMWARE_SIZE + CIRCUITPY_INTERNAL_NVM_SIZE(4096) - i.e. the same "firmware size
# varies per board, filesystem starts right after" shape as MicroPython, just computed from the
# *start* of flash instead of the end.
# - raspberry_pi_pico: default firmware_size = 1020K (link-rp2040.ld, no board override) ->
#   0x10000000 + 1020K + 4096 = 0x10100000, i.e. offset 0x100000 (matches this project's
#   pre-existing, apparently-already-correct-for-plain-pico constant).
# - raspberry_pi_pico_w: overrides firmware_size = 1532K (boards/raspberry_pi_pico_w/link.ld,
#   "Must be accompanied by a linker script change" per mpconfigboard.mk's own comment - the CYW43
#   driver/lwIP stack needs the extra room, same underlying reason as MicroPython's) ->
#   0x10000000 + 1532K + 4096 = 0x10180000, offset 0x180000.
# fs_blockcount left at the existing 512 for both boards (unlike MicroPython's, which genuinely
# shrinks on pico_w) - rp2040py's own emulated flash buffer is 16MB (_rp2040.py), far bigger than
# either board's real 2MB chip, so a generous fixed region past the real firmware's end doesn't
# collide with anything either board actually uses; only the *start* address matters for
# correctness (matching where real firmware's own compiled code actually ends).
_CIRCUITPYTHON_FLASH_LAYOUT = {
    "pico": {"fs_start": "0x100000", "fs_blockcount": 512},
    "pico_w": {"fs_start": "0x180000", "fs_blockcount": 512},
}


def _http_get(url: str, *, accept_json: bool = False) -> bytes:
    headers = {"User-Agent": "rp2040py-firmware-fetch"}
    if accept_json:
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def _fetch_micropython() -> "dict[str, dict[str, str]]":
    boards: dict[str, dict[str, str]] = {}
    for board, slug in _MICROPYTHON_BOARD_SLUGS.items():
        html = _http_get(f"https://micropython.org/download/{slug}/").decode()
        href_pattern = rf'href="(/resources/firmware/{re.escape(slug)}-[^"]*\.uf2)"'
        versions: dict[str, str] = {}
        for href in re.findall(href_pattern, html):
            match = re.search(r"-v(.+)\.uf2$", href.rsplit("/", 1)[-1])
            if match is not None:
                versions[match.group(1)] = "https://micropython.org" + href
        boards[board] = versions
    return boards


# CI nightly/PR-preview builds also live in the same S3 prefix as real releases, named
# "<8-digit-date>-<branch>-PR<n>-<hash>" instead of a version - e.g.
# "20260402-main-PR10916-3d906c3", not something anyone would ever pass as a --image tag. No real
# CircuitPython release tag starts with 8 digits, so this is a safe, cheap filter without needing
# a full semver parse (and without adding a dependency just for this script to filter with).
_CIRCUITPYTHON_NIGHTLY_BUILD = re.compile(r"^\d{8}-")


def _fetch_circuitpython() -> "dict[str, dict[str, str]]":
    boards: dict[str, dict[str, str]] = {}
    for board, slug in _CIRCUITPYTHON_BOARD_SLUGS.items():
        xml_bytes = _http_get(f"https://adafruit-circuit-python.s3.amazonaws.com/?prefix=bin/{slug}/en_US/")
        root = ET.fromstring(xml_bytes)
        key_pattern = re.compile(rf"^adafruit-circuitpython-{re.escape(slug)}-en_US-(.+)\.uf2$")
        versions: dict[str, str] = {}
        for content in root.findall("s3:Contents", _S3_XML_NS):
            key = content.findtext("s3:Key", namespaces=_S3_XML_NS)
            if key is None:
                continue
            match = key_pattern.match(key.rsplit("/", 1)[-1])
            if match is not None and not _CIRCUITPYTHON_NIGHTLY_BUILD.match(match.group(1)):
                versions[match.group(1)] = f"https://adafruit-circuit-python.s3.amazonaws.com/{key}"
        boards[board] = versions
    return boards


def _fetch_kaluma() -> "dict[str, dict[str, str]]":
    releases = json.loads(
        _http_get("https://api.github.com/repos/kaluma-project/kaluma/releases?per_page=100", accept_json=True)
    )
    boards: dict[str, dict[str, str]] = {board: {} for board in _KALUMA_BOARD_ASSET_SUFFIXES}
    for release in releases:
        tag = release["tag_name"]
        asset_names = {asset["name"] for asset in release["assets"]}
        for board, suffix in _KALUMA_BOARD_ASSET_SUFFIXES.items():
            filename = f"kaluma-rp2-{suffix}-{tag}.uf2"
            if filename in asset_names:
                boards[board][tag] = f"https://github.com/kaluma-project/kaluma/releases/download/{tag}/{filename}"
    return boards


def _fetch_bootrom() -> "dict[str, str]":
    releases = json.loads(
        _http_get("https://api.github.com/repos/raspberrypi/pico-bootrom-rp2040/releases?per_page=20", accept_json=True)
    )
    versions: dict[str, str] = {}
    for release in releases:
        tag = release["tag_name"]
        for asset in release["assets"]:
            if asset["name"] == f"{tag}.elf":
                versions[tag] = asset["browser_download_url"]
    return versions


def _merge_boards(existing: "dict[str, dict[str, str]]", fetched: "dict[str, dict[str, str]]", family: str) -> None:
    for board, versions in fetched.items():
        board_map = existing.setdefault(board, {})
        added = sorted(set(versions) - set(board_map))
        board_map.update(versions)
        print(f"{family}/{board}: {len(versions)} versions found, {len(added)} new: {added}")


def main() -> None:
    specs = json.loads(_SPECS_PATH.read_text())

    _merge_boards(specs["micropython"].setdefault("boards", {}), _fetch_micropython(), "micropython")
    _merge_boards(specs["circuitpython"].setdefault("boards", {}), _fetch_circuitpython(), "circuitpython")
    _merge_boards(specs["kaluma"].setdefault("boards", {}), _fetch_kaluma(), "kaluma")

    specs["micropython"]["flash_layout"] = _MICROPYTHON_FLASH_LAYOUT
    specs["kaluma"]["flash_layout"] = _KALUMA_FLASH_LAYOUT
    specs["circuitpython"]["flash_layout"] = _CIRCUITPYTHON_FLASH_LAYOUT

    bootrom_versions = _fetch_bootrom()
    bootrom_map = specs["bootrom"].setdefault("known_versions", {})
    added = sorted(set(bootrom_versions) - set(bootrom_map))
    bootrom_map.update(bootrom_versions)
    print(f"bootrom: {len(bootrom_versions)} versions found, {len(added)} new: {added}")

    _SPECS_PATH.write_text(json.dumps(specs, indent=2) + "\n")
    print(f"Updated {_SPECS_PATH}")


if __name__ == "__main__":
    main()
