"""Declarative, centralized firmware retrieval: one `FirmwareSpec` per supported firmware, loaded
from `firmware_specs.json` next to this module, plus a single generic `retrieve()` that resolves a
version tag/local path/direct URL/omitted `image` argument into an on-disk file (a UF2 for
MICROPYTHON/CIRCUITPYTHON/KALUMA, an ELF for BOOTROM), downloading it if necessary.

**Board-aware tag resolution (2026-08-12), per docs/CYW43_WIFI_BACKLOG.md's "Candidate redesign"
section.** `known_versions: dict[tag, filename-version]` plus `filename_template`/`url_template`
string substitution is gone - `FirmwareSpec.boards: dict[board, BoardFirmwareSpec]` maps a tag
straight to a full download URL instead (`BoardFirmwareSpec.fw`), nested per board for firmware
that genuinely differs by board (MicroPython/CircuitPython/Kaluma - the ones that actually ship
separate Pico-W-specific builds with the network stack compiled in). `FirmwareSpec.known_versions:
dict[tag, url]` (flat, no board nesting) is BOOTROM's own shape instead - a mask ROM baked into the
RP2040 die itself, identical across every board, versioned only by silicon stepping (B0/B1/B2) -
never board-specific, so board-nesting it would be actively misleading. Exactly one of
`boards`/`known_versions` is set per spec; `retrieve()`'s own `board` argument is used (and
required to be a real, known board) only when resolving a tag against a `boards`-shaped spec -
ignored entirely for a local path or a raw URL (those are used exactly as given, no board-based
resolution at all), and ignored for a `known_versions`-shaped spec (BOOTROM) since there's nothing
to select between.

**Per-board `default_tag`/flash layout (2026-08-16), per docs/records/0049's "Design update"
section.** Everything genuinely board-specific for a `boards`-shaped spec - which tag to default
to, the tag->url map, and where that board's real firmware places its flash filesystem region -
lives together in one `BoardFirmwareSpec` per board, instead of being scattered across sibling
top-level dicts (a former `default_tag` shared by every board in the family; a `flash_layout` dict
keyed by the same board-name string as `boards` but structurally unrelated to it) that all
happened to use the same key by convention rather than by construction. BOOTROM keeps its own
top-level `default_tag` (board-agnostic, no `boards` at all).

**`BoardFirmwareSpec` is also the custom-board declaration format (2026-08-17), per
docs/records/0059.** `boards.BoardSpec.firmware: dict[family, BoardFirmwareSpec]` reuses this exact
type, keyed by this file's own top-level family names, so a built-in board and a hand-written
board file describe "which images exist and where does the filesystem go" in one shape resolved by
one function - `family_of()` maps a `FirmwareSpec` back to its family name for that path, and
`board_flash_layout()` parses one declaration's `layout` without needing a board name to look it
up through. The only thing that stays different is who maintains the data: `scripts/
fetch_firmware.py` regenerates it here, a board file's author edits theirs by hand.

`firmware_specs.json` isn't generated at request time by this module - it's fetched at development
time by `scripts/fetch_firmware.py` (scrapes MicroPython's/CircuitPython's/Kaluma's/the
RP2040 bootrom's own real, authoritative release sources) and committed straight into the JSON, so
the shipped index always has real, verified tags and URLs as of whenever it was last refreshed -
no filename-guessing or template drift at request time, just a lookup. Re-run that script and
commit the diff to pick up new releases.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

import semver

__all__ = (
    "BOOTROM",
    "CIRCUITPYTHON",
    "KALUMA",
    "MICROPYTHON",
    "SPECS",
    "BoardFirmwareSpec",
    "FirmwareSpec",
    "board_flash_layout",
    "family_of",
    "flash_layout",
    "retrieve",
)

_logger = logging.getLogger(__name__)

# Applies per socket operation (connect, and each individual read()), not to the download as a
# whole - urlopen()'s own `timeout` param, same as socket.settimeout() under the hood - so a slow
# but still-progressing transfer is never cut off, only a genuinely stuck one (server never
# responds, or goes silent mid-transfer) is.
_DOWNLOAD_TIMEOUT_SECONDS = 30

_DEFAULT_BOARD = "pico"


@dataclass(frozen=True)
class BoardFirmwareSpec:
    """One board's own slice of a `boards`-shaped `FirmwareSpec` (MICROPYTHON/CIRCUITPYTHON/
    KALUMA) - everything genuinely board-specific lives together here, not scattered across
    sibling top-level dicts keyed by the same board-name string (2026-08-16 reshape - see
    docs/records/0049's "Design update" section)."""

    default_tag: str
    fw: "dict[str, str]"  # tag -> url
    # "fs_start"/"fs_blockcount"/"fs_blocksize", plus "prog_start" for Kaluma - where this board's
    # real firmware places its flash filesystem (and, for Kaluma, "user program") region, sourced
    # from that firmware's own upstream board config, not guessed (see docs/records/0035 for the
    # MicroPython derivation - a board with a bigger compiled binary, like pico_w's CYW43 driver +
    # lwIP stack, needs a correspondingly relocated filesystem region, or writing a filesystem
    # image silently overwrites the tail of the firmware itself). `fs_blocksize` is littlefs's
    # block size (the flash sector-erase granularity - 4096 for every board/family tracked so far,
    # but genuinely per-board/firmware data, not a hardware universal - see docs/records/0049's
    # "Design update" section for why this isn't a `load_flash.py` module constant). `None` only
    # for a firmware family with no filesystem concept at all - none of MICROPYTHON/CIRCUITPYTHON/
    # KALUMA qualify today, kept optional for whatever's added next.
    layout: "dict[str, str | int] | None" = None


@dataclass(frozen=True)
class FirmwareSpec:
    # board -> BoardFirmwareSpec - set for firmware that genuinely differs per board (MicroPython/
    # CircuitPython/Kaluma). `None` for board-agnostic firmware (BOOTROM) - see module docstring.
    boards: "dict[str, BoardFirmwareSpec] | None" = None
    # BOOTROM only: board-agnostic default tag/version map. Board-aware families keep their own
    # `default_tag`/`fw` per board instead (`BoardFirmwareSpec`) - a single tag can be the right
    # default for `pico` but wrong for `pico_w` in principle, even though every family tracked so
    # far happens to agree.
    default_tag: "str | None" = None
    known_versions: "dict[str, str] | None" = None


def _load_specs() -> "dict[str, FirmwareSpec]":
    raw = json.loads(files(__package__).joinpath("firmware_specs.json").read_text())
    specs = {}
    for name, spec in raw.items():
        boards = spec.get("boards")
        if boards is not None:
            boards = {board: BoardFirmwareSpec(**board_spec) for board, board_spec in boards.items()}
        specs[name] = FirmwareSpec(
            boards=boards, default_tag=spec.get("default_tag"), known_versions=spec.get("known_versions")
        )
    return specs


SPECS = _load_specs()
MICROPYTHON = SPECS["micropython"]
CIRCUITPYTHON = SPECS["circuitpython"]
KALUMA = SPECS["kaluma"]
BOOTROM = SPECS["bootrom"]


def family_of(spec: FirmwareSpec) -> "str | None":
    """The `firmware_specs.json` top-level name (`"micropython"`/`"circuitpython"`/`"kaluma"`/
    `"bootrom"`) `spec` was loaded under - the same strings `boards.BoardSpec.firmware` is keyed by
    (docs/records/0059), which is what lets one resolution path serve both a built-in board and a
    custom board file. Identity, not equality: two `FirmwareSpec`s can compare equal without being
    the shipped one, and a `FirmwareSpec` holds dicts, so it isn't hashable enough for a reverse
    dict either. `None` for a spec a caller built themselves (a board file's own inline
    declaration, a test double) - it simply isn't one of the four shipped families."""
    return next((name for name, known in SPECS.items() if known is spec), None)


def _cache_dir() -> Path:
    """Where downloaded firmware/bootrom files are cached across runs and projects -
    `~/.cache/rp2040py` - rather than the current directory, so e.g. `--image 1.21.0` doesn't
    re-download the same UF2 into every project checkout separately. Falls back to `Path(".")`
    (today's original behavior, download into the current directory) if the cache directory can't
    be created for any reason (no `HOME`, a read-only filesystem, a sandboxed environment, ...) -
    caching is a nice-to-have, not something that should ever turn into a hard failure."""
    cache_dir = Path.home() / ".cache" / "rp2040py"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning(
            "could not create cache directory %s (%s); caching in the current directory instead", cache_dir, exc
        )
        return Path()
    return cache_dir


def _parse_version(version: str) -> "semver.Version | None":
    try:
        return semver.Version.parse(version, optional_minor_and_patch=True)
    except ValueError:
        return None


def _resolve_url(version_map: "dict[str, str]", tag: str) -> "str | None":
    """Resolves `tag` against `version_map` (a `{tag: url}` mapping - either one board's own slice
    of a `FirmwareSpec.boards`, or a board-agnostic `known_versions`), returning the matching URL,
    or `None` if nothing matches. An exact key match short-circuits everything else. Otherwise a
    short tag means its dotted *components* as a prefix (e.g. "1.19" -> "1.19.1"), not a raw string
    prefix: comparing raw strings would also match "1.20.0"/"1.21.0"/"1.28.0" for tag "1.2" purely
    from coincidental digit overlap - wrong, since "1.2" means version 1.2.x, disjoint from
    1.20.x/1.21.x/1.28.x, not a string fragment of them. `semver.Version.parse(...,
    optional_minor_and_patch=True)` normalizes both sides to real (major, minor, patch) tuples for
    comparison instead, truncated to how many components the tag itself specified - so "1.19" only
    ever matches known versions whose (major, minor) is exactly (1, 19). A bare numeric tag like
    "1" still legitimately matches every 1.x release, so pick the highest of those (rather than
    whichever happened to come first in `version_map`'s key order, a silent footgun the moment a
    new version is inserted anywhere but the end)."""
    tag = tag.removeprefix("v")
    if tag in version_map:
        return version_map[tag]
    precision = tag.count(".") + 1
    tag_version = _parse_version(tag)
    if tag_version is None:
        return None
    matches = []
    for known_tag, url in version_map.items():
        known_version = _parse_version(known_tag)
        if known_version is not None and known_version.to_tuple()[:precision] == tag_version.to_tuple()[:precision]:
            matches.append((known_version, url))
    if matches:
        return max(matches)[1]
    return None


def _cache_filename(url: str) -> str:
    """Derives a cache filename from `url`'s own path - its basename, when it has one (every URL
    `firmware_specs.json` itself ever produces does). Falls back to a short hash of the whole URL
    string for a raw `--image <url>` with no usable path component of its own (e.g. a query-
    string-only download link) - hashing the URL string itself, not its content: hashing content
    would require downloading first, defeating the point of checking the cache before fetching."""
    name = Path(urlparse(url).path).name
    if name:
        return name
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _download(url: str, filename: str) -> "Path | None":
    cached_path = _cache_dir() / filename
    if cached_path.exists():
        _logger.info("Found local image: %s", str(cached_path))
        return cached_path

    from urllib.error import HTTPError
    from urllib.request import urlopen

    _logger.info("Download: %s from %s", filename, url)
    try:
        with urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, open(cached_path, "wb") as f:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    except (HTTPError, TimeoutError):
        # Cleans up a partial file from a download that died mid-transfer (e.g. the timeout above
        # firing after some bytes already landed) - otherwise a retry would find cached_path
        # already "exists()" and hand back a truncated, corrupt image instead of re-downloading.
        cached_path.unlink(missing_ok=True)
        return None
    _logger.info("Download complete: file saved to: %s", str(cached_path))
    return cached_path


def board_flash_layout(board_spec: BoardFirmwareSpec) -> "dict[str, int] | None":
    """One `BoardFirmwareSpec.layout` as an all-`int` dict, parsing each `"0x..."` string value -
    `firmware_specs.json` stores them as hex strings for human readability, plain JSON has no
    hex-literal syntax, and a hand-written board file follows the same convention. `None` for a
    declaration with no `layout` at all. The board-name-free half of `flash_layout()` below, so a
    `boards.BoardSpec` that already holds its own `BoardFirmwareSpec` (docs/records/0059) can parse
    it without inventing a `FirmwareSpec` and a board key to look it up through."""
    if board_spec.layout is None:
        return None
    return {key: (int(value, 16) if isinstance(value, str) else value) for key, value in board_spec.layout.items()}


def flash_layout(spec: FirmwareSpec, board: str) -> "dict[str, int]":
    """Resolves `spec.boards[board].layout` into an all-`int` dict (see `board_flash_layout()`).
    Raises `KeyError` for a spec with no `boards` at all (BOOTROM), a `board` not present in it, or
    a board with no `layout` - all considered a caller bug (every board this project actually
    supports for a layout-bearing spec must have an entry), not a runtime condition to degrade
    gracefully from.
    """
    if spec.boards is None:
        raise KeyError(f"{spec!r} has no boards")
    layout = board_flash_layout(spec.boards[board])
    if layout is None:
        raise KeyError(f"{spec!r}'s {board!r} board has no layout")
    return layout


def retrieve(spec: FirmwareSpec, image: "str | None" = None, board: str = _DEFAULT_BOARD) -> "Path | None":
    """
    Args:
        spec: which firmware to resolve (MICROPYTHON/CIRCUITPYTHON/KALUMA/BOOTROM).
        image: a version tag (defaults to the resolved board's own `default_tag` for a
            `spec.boards`-shaped spec, or `spec.default_tag` for BOOTROM), a local file path, or a
            direct `http(s)://` URL (downloaded, cached, and reused from cache on subsequent runs
            the same way a resolved tag already is).
        board: which board's firmware variant to resolve a *tag* to (default: `"pico"`) - consulted
            only for `spec.boards`-shaped specs (MICROPYTHON/CIRCUITPYTHON/KALUMA) resolving a
            version tag (including the default one, when `image` is omitted); ignored entirely
            when `image` is a local path or URL, and ignored for a `known_versions`-shaped spec
            (BOOTROM - board-agnostic, see module docstring).
    """
    if image is None:
        if spec.boards is not None:
            if board not in spec.boards:
                _logger.error("Unknown board %r - choices are %s", board, sorted(spec.boards))
                return None
            image = spec.boards[board].default_tag
        else:
            assert spec.default_tag is not None, f"{spec!r} has neither boards nor a default_tag"
            image = spec.default_tag

    local_image = Path(image)
    if local_image.exists():
        _logger.info("Found local image: %s", str(local_image))
        return local_image

    if image.startswith(("http://", "https://")):
        return _download(image, _cache_filename(image))

    if spec.boards is not None:
        if board not in spec.boards:
            _logger.error("Unknown board %r - choices are %s", board, sorted(spec.boards))
            return None
        version_map = spec.boards[board].fw
    else:
        version_map = spec.known_versions or {}

    url = _resolve_url(version_map, image)
    if url is None:
        _logger.error("Unknown version tag %r", image)
        return None

    return _download(url, _cache_filename(url))
