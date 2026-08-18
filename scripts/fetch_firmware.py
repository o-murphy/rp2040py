#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Refreshes `src/rp2040py/utils/firmware_specs.json` in full - all four firmware families in one
pass, one script, matching docs/CYW43_WIFI_BACKLOG.md's "Candidate redesign" section: `known_versions`
becomes a flat `tag -> url` map fetched at development time and committed straight into
`firmware_specs.json`, not generated at request time by `retrieve()`.

**`micropython`/`circuitpython`/`kaluma` are board-aware**
(`boards: {board: {default_tag, fw: {tag: url}, layout: {...}}}`) - per-board firmware actually
differs for these (cyw43-driver/network stack compiled in or not), and so does where each board's
real firmware places its flash filesystem region - everything board-specific lives together under
its own board key (2026-08-16 reshape, see docs/records/0049's "Design update" section) rather
than being scattered across sibling top-level dicts that all happened to use the same board-name
key by convention. **`bootrom` is deliberately NOT board-aware** (`known_versions: {tag: url}`,
flat, plus its own top-level `default_tag`) - it's a mask ROM baked into the RP2040 die itself at
manufacturing time, identical across every board that chip ends up on, versioned only by silicon
stepping (B0/B1/B2) - see docs/CYW43_WIFI_BACKLOG.md's "Historical/closed" section for why this one
firmware family is explicitly out of scope for board-variant resolution. Keeping both shapes in
one script (rather than one script per family) means that distinction lives in exactly one place
instead of needing to stay in sync across several.

`default_tag` (per board) and `layout` are **not** re-fetched here - there's no API for either
(one's an editorial pin to a known-good version, the other's a hand-curated hardware-config
constant, not release metadata). `main()` only ever seeds a *missing* board's `default_tag`
(`_apply_board_layout()`'s `setdefault`, for a board this script has never seen before) and always
overwrites `layout` outright (still hand-curated below, just re-asserted every run so the
committed JSON can't silently drift from its cited source) - an existing board's `default_tag`
is left exactly as whatever's already committed.

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

**`list --family <family> --slug <slug>`** fetches the same tag->url version map as above, but for
one arbitrary board slug outside `firmware_specs.json` entirely - no `firmware_specs.json` read or
write happens, and no built-in board is touched. It just prints that one slug's map (JSON, i.e.
directly pastable as a `BoardFirmwareSpec.fw` literal) to stdout and returns only that - nothing
else is fetched or merged. For a `--board-spec` board file author (`boards/vcc_gnd_yd_rp2040/`
and friends): `--family` is one of `micropython`/`circuitpython`/`kaluma` (`bootrom` has no
per-board slug, not supported here), and `--slug` is that family's own board identifier -
MicroPython's `ports/rp2/boards/` directory name (e.g. `WAVESHARE_RP2040_ZERO`), CircuitPython's
`ports/raspberrypi/boards/` directory name (e.g. `waveshare_rp2040_zero`), or Kaluma's release
asset `-<suffix>-` segment (e.g. `pico-w`). Example:
`uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_zero`.
"""

import argparse
import json
import re
import sys
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
# only) - see 0035's "Open questions". `fs_blocksize` is littlefs's block size, i.e. the flash
# sector-erase granularity - 4096 for the RP2040's external SPI-NOR flash regardless of board
# (docs/records/0049's "Design update" section: this used to be a `load_flash.py` module constant,
# shared across every board by construction rather than actually sourced per board/firmware family
# like `fs_start`/`fs_blockcount` already were - moved here so a board/firmware combination that
# genuinely needs a different value has somewhere to put it).
_MICROPYTHON_FLASH_LAYOUT = {
    "pico": {"fs_start": "0xa0000", "fs_blockcount": 352, "fs_blocksize": 4096},
    "pico_w": {"fs_start": "0x12c000", "fs_blockcount": 212, "fs_blocksize": 4096},
}

# Kaluma (kaluma-project/kaluma, targets/rp2/boards/{pico,pico-w}/board.h + board.js): identical
# between pico and pico-w (KALUMA_FLASH_SECTOR_COUNT=260, KALUMA_PROG_SECTOR_BASE=4,
# KALUMA_PROG_SECTOR_COUNT=128, board.js's `new Flash(132, 128)`) - Kaluma reserves the same fixed
# code budget regardless of board, unlike MicroPython. Still stored per-board (both keys pointing
# at the same values) so every firmware family's flash layout lives in this one uniform shape.
_KALUMA_FLASH_LAYOUT = {
    "pico": {"prog_start": "0x100000", "fs_start": "0x180000", "fs_blockcount": 128, "fs_blocksize": 4096},
    "pico_w": {"prog_start": "0x100000", "fs_start": "0x180000", "fs_blockcount": 128, "fs_blocksize": 4096},
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
    "pico": {"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    "pico_w": {"fs_start": "0x180000", "fs_blockcount": 512, "fs_blocksize": 4096},
}


def _http_get(url: str, *, accept_json: bool = False) -> bytes:
    headers = {"User-Agent": "rp2040py-firmware-fetch"}
    if accept_json:
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def _fetch_micropython_versions(slug: str, *, page: "str | None" = None) -> "dict[str, str]":
    """The tag->url map for one MicroPython board slug (its `ports/rp2/boards/` directory name,
    e.g. `RPI_PICO_W`/`WAVESHARE_RP2040_ZERO`) - the per-slug half `_fetch_micropython()` (built-in
    boards) and the `list` subcommand (any other board) both call.

    `page` is the `micropython.org/download/<page>/` slug to scrape, when it differs from `slug`
    itself - needed for a board that builds several `BOARD_VARIANT` images (different flash sizes,
    say) off *one* shared download page, e.g. `WEACTSTUDIO`'s page lists `WEACTSTUDIO-FLASH_2M-*`/
    `_FLASH_4M-*`/`_FLASH_8M-*` *and* the bare default `WEACTSTUDIO-*` side by side - `slug` picks
    which of those filename prefixes this call wants, `page` says where to look for any of them.
    Defaults to `slug` (the common one-image-per-page case, true for both built-in boards today).

    The href filter requires an 8-digit build date immediately after `{slug}-` (every real
    filename's own `{BOARD}[-{VARIANT}]-{date}-v{version}.uf2` shape) rather than accepting
    anything up to the next `.uf2` - without that anchor, a bare board slug like `WEACTSTUDIO`
    also prefix-matches its own `WEACTSTUDIO-FLASH_2M-...`/`_FLASH_4M-...` siblings on the same
    page, silently folding several variants' versions into one dict as later hrefs overwrite
    earlier ones under the same version key."""
    html = _http_get(f"https://micropython.org/download/{page or slug}/").decode()
    href_pattern = rf'href="(/resources/firmware/{re.escape(slug)}-\d{{8}}-[^"]*\.uf2)"'
    versions: dict[str, str] = {}
    for href in re.findall(href_pattern, html):
        match = re.search(r"-v(.+)\.uf2$", href.rsplit("/", 1)[-1])
        if match is not None:
            versions[match.group(1)] = "https://micropython.org" + href
    return versions


def _fetch_micropython() -> "dict[str, dict[str, str]]":
    return {board: _fetch_micropython_versions(slug) for board, slug in _MICROPYTHON_BOARD_SLUGS.items()}


# CI nightly/PR-preview builds also live in the same S3 prefix as real releases, named
# "<8-digit-date>-<branch>-PR<n>-<hash>" instead of a version - e.g.
# "20260402-main-PR10916-3d906c3", not something anyone would ever pass as a --image tag. No real
# CircuitPython release tag starts with 8 digits, so this is a safe, cheap filter without needing
# a full semver parse (and without adding a dependency just for this script to filter with).
_CIRCUITPYTHON_NIGHTLY_BUILD = re.compile(r"^\d{8}-")


def _fetch_circuitpython_versions(slug: str) -> "dict[str, str]":
    """The tag->url map for one CircuitPython board slug (its `ports/raspberrypi/boards/`
    directory name, e.g. `raspberry_pi_pico_w`/`waveshare_rp2040_zero`) - the per-slug half
    `_fetch_circuitpython()` (built-in boards) and the `list` subcommand (any other board) both
    call."""
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
    return versions


def _fetch_circuitpython() -> "dict[str, dict[str, str]]":
    return {board: _fetch_circuitpython_versions(slug) for board, slug in _CIRCUITPYTHON_BOARD_SLUGS.items()}


def _fetch_kaluma_releases() -> "list[dict]":
    return json.loads(
        _http_get("https://api.github.com/repos/kaluma-project/kaluma/releases?per_page=100", accept_json=True)
    )


def _kaluma_versions_for_suffix(releases: "list[dict]", suffix: str) -> "dict[str, str]":
    """The tag->url map for one Kaluma release-asset suffix (the `-<suffix>-` segment of
    `kaluma-rp2-<suffix>-<tag>.uf2`, e.g. `pico-w`) against an already-fetched release list - the
    per-slug half `_fetch_kaluma()` (built-in boards, one release fetch shared across both) and the
    `list` subcommand (any other board, its own single release fetch) both call."""
    versions: dict[str, str] = {}
    for release in releases:
        tag = release["tag_name"]
        filename = f"kaluma-rp2-{suffix}-{tag}.uf2"
        if filename in {asset["name"] for asset in release["assets"]}:
            versions[tag] = f"https://github.com/kaluma-project/kaluma/releases/download/{tag}/{filename}"
    return versions


def _fetch_kaluma() -> "dict[str, dict[str, str]]":
    releases = _fetch_kaluma_releases()
    return {
        board: _kaluma_versions_for_suffix(releases, suffix) for board, suffix in _KALUMA_BOARD_ASSET_SUFFIXES.items()
    }


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


# Editorial pins, not fetched from anywhere - the version each family defaults to when no
# explicit tag/board override picks a newer one. Only ever used as a fallback for a board
# `_merge_boards()` sees for the first time (`_apply_board_layout()`'s `setdefault`); an existing
# board's committed `default_tag` is never overwritten by a later run.
_MICROPYTHON_DEFAULT_TAG = "1.21.0"
_CIRCUITPYTHON_DEFAULT_TAG = "8.0.2"
_KALUMA_DEFAULT_TAG = "1.2.1"


def _merge_boards(existing: "dict[str, dict]", fetched: "dict[str, dict[str, str]]", family: str) -> None:
    for board, versions in fetched.items():
        board_entry = existing.setdefault(board, {})
        fw_map = board_entry.setdefault("fw", {})
        added = sorted(set(versions) - set(fw_map))
        fw_map.update(versions)
        print(f"{family}/{board}: {len(versions)} versions found, {len(added)} new: {added}")


def _apply_board_layout(boards: "dict[str, dict]", layout: "dict[str, dict]", default_tag: str) -> None:
    for board, board_entry in boards.items():
        board_entry["layout"] = layout[board]
        board_entry.setdefault("default_tag", default_tag)


_LIST_FETCHERS = {
    "micropython": _fetch_micropython_versions,
    "circuitpython": _fetch_circuitpython_versions,
    "kaluma": lambda slug: _kaluma_versions_for_suffix(_fetch_kaluma_releases(), slug),
}


def _cmd_list(family: str, slug: str, page: "str | None") -> None:
    """`list --family <family> --slug <slug> [--page <page>]`: prints *only* that one slug's
    tag->url map (JSON, directly pastable as a `BoardFirmwareSpec.fw` literal) to stdout. Reads and
    writes nothing - `firmware_specs.json` (built-in boards only) is never touched, and no other
    slug is fetched. `--page` is `micropython`-only (see `_fetch_micropython_versions()`'s own
    docstring for why some boards need it) - rejected outright for the other two families rather
    than silently ignored, since a typo'd `--page` there would otherwise look like it did
    something."""
    if page is not None and family != "micropython":
        print(f"--page is only meaningful for --family micropython, not {family!r}", file=sys.stderr)
        raise SystemExit(2)
    versions = _fetch_micropython_versions(slug, page=page) if family == "micropython" else _LIST_FETCHERS[family](slug)
    if not versions:
        print(f"No {family} versions found for slug {slug!r}" + (f" (page {page!r})" if page else ""), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(versions, indent=2))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list",
        help="print one arbitrary (non-built-in) board slug's tag->url version map and exit - "
        "firmware_specs.json is never read or written",
    )
    list_parser.add_argument(
        "--family",
        required=True,
        choices=tuple(_LIST_FETCHERS),
        help="firmware family to query (bootrom has no per-board slug, not supported here)",
    )
    list_parser.add_argument(
        "--slug",
        required=True,
        help="that family's own board identifier - MicroPython: its ports/rp2/boards/ directory "
        "name (e.g. WAVESHARE_RP2040_ZERO); CircuitPython: its ports/raspberrypi/boards/ directory "
        "name (e.g. waveshare_rp2040_zero); Kaluma: the '-<suffix>-' segment of its release asset "
        "filename (e.g. pico-w)",
    )
    list_parser.add_argument(
        "--page",
        default=None,
        help="micropython.org/download/<page>/ slug to scrape, only when it differs from --slug - "
        "needed for a board with several BOARD_VARIANT images sharing one download page (e.g. "
        "--slug WEACTSTUDIO-FLASH_2M --page WEACTSTUDIO). --family micropython only; rejected for "
        "circuitpython/kaluma, which have no such split",
    )
    return parser


def _cmd_update() -> None:
    specs = json.loads(_SPECS_PATH.read_text())

    _merge_boards(specs["micropython"].setdefault("boards", {}), _fetch_micropython(), "micropython")
    _merge_boards(specs["circuitpython"].setdefault("boards", {}), _fetch_circuitpython(), "circuitpython")
    _merge_boards(specs["kaluma"].setdefault("boards", {}), _fetch_kaluma(), "kaluma")

    _apply_board_layout(specs["micropython"]["boards"], _MICROPYTHON_FLASH_LAYOUT, _MICROPYTHON_DEFAULT_TAG)
    _apply_board_layout(specs["kaluma"]["boards"], _KALUMA_FLASH_LAYOUT, _KALUMA_DEFAULT_TAG)
    _apply_board_layout(specs["circuitpython"]["boards"], _CIRCUITPYTHON_FLASH_LAYOUT, _CIRCUITPYTHON_DEFAULT_TAG)

    bootrom_versions = _fetch_bootrom()
    bootrom_map = specs["bootrom"].setdefault("known_versions", {})
    added = sorted(set(bootrom_versions) - set(bootrom_map))
    bootrom_map.update(bootrom_versions)
    print(f"bootrom: {len(bootrom_versions)} versions found, {len(added)} new: {added}")

    _SPECS_PATH.write_text(json.dumps(specs, indent=2) + "\n")
    print(f"Updated {_SPECS_PATH}")


def main() -> None:
    args = _build_arg_parser().parse_args()
    if args.command == "list":
        _cmd_list(args.family, args.slug, args.page)
        return
    _cmd_update()


if __name__ == "__main__":
    main()
