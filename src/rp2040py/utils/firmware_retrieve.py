"""Declarative, centralized firmware retrieval: one `FirmwareSpec` per supported firmware, loaded
from `firmware_specs.json` next to this module, plus a single generic `retrieve()` that resolves a
version tag/local path/direct URL/omitted `image` argument into an on-disk file (a UF2 for
MICROPYTHON/CIRCUITPYTHON/KALUMA, an ELF for BOOTROM), downloading it if necessary.

**Board-aware tag resolution (2026-08-12), per docs/CYW43_WIFI_BACKLOG.md's "Candidate redesign"
section.** `known_versions: dict[tag, filename-version]` plus `filename_template`/`url_template`
string substitution is gone - `FirmwareSpec.boards: dict[board, dict[tag, url]]` maps a tag
straight to a full download URL instead, nested per board for firmware that genuinely differs by
board (MicroPython/CircuitPython/Kaluma - the ones that actually ship separate Pico-W-specific
builds with the network stack compiled in). `FirmwareSpec.known_versions: dict[tag, url]` (flat,
no board nesting) is BOOTROM's own shape instead - a mask ROM baked into the RP2040 die itself,
identical across every board, versioned only by silicon stepping (B0/B1/B2) - never board-specific,
so board-nesting it would be actively misleading. Exactly one of `boards`/`known_versions` is set
per spec; `retrieve()`'s own `board` argument is used (and required to be a real, known board) only
when resolving a tag against a `boards`-shaped spec - ignored entirely for a local path or a raw
URL (those are used exactly as given, no board-based resolution at all), and ignored for a
`known_versions`-shaped spec (BOOTROM) since there's nothing to select between.

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

__all__ = ("BOOTROM", "CIRCUITPYTHON", "KALUMA", "MICROPYTHON", "FirmwareSpec", "flash_layout", "retrieve")

_logger = logging.getLogger(__name__)

# Applies per socket operation (connect, and each individual read()), not to the download as a
# whole - urlopen()'s own `timeout` param, same as socket.settimeout() under the hood - so a slow
# but still-progressing transfer is never cut off, only a genuinely stuck one (server never
# responds, or goes silent mid-transfer) is.
_DOWNLOAD_TIMEOUT_SECONDS = 30

_DEFAULT_BOARD = "pico"


@dataclass(frozen=True)
class FirmwareSpec:
    default_tag: str
    # board -> {tag: url} - set for firmware that genuinely differs per board (MicroPython/
    # CircuitPython/Kaluma). `None` for board-agnostic firmware (BOOTROM) - see module docstring.
    boards: "dict[str, dict[str, str]] | None" = None
    # tag -> url, board-agnostic. Only ever set when `boards` is `None` (BOOTROM); unused
    # otherwise.
    known_versions: "dict[str, str] | None" = None
    # board -> {"fs_start": "0x...", "fs_blockcount": N, ...} - where a firmware's own compiled
    # filesystem (and, for Kaluma, "user program") flash region actually lives, real values sourced
    # from that firmware's own upstream board config, not guessed (see docs/records/0035 for the
    # MicroPython derivation - a board with a bigger compiled binary, like pico_w's CYW43 driver +
    # lwIP stack, needs a correspondingly relocated filesystem region, or writing a filesystem image
    # silently overwrites the tail of the firmware itself). Set for MICROPYTHON/KALUMA; `None` for
    # CIRCUITPYTHON (not yet audited the same way - see 0035's "Open questions") and BOOTROM (no
    # filesystem concept at all). Kaluma's values happen to be identical across every board key
    # (confirmed against kaluma-project/kaluma's own board.js/board.h) - still stored per-board here
    # rather than as a separate board-invariant shape, so every firmware family's flash layout lives
    # in this one place uniformly.
    flash_layout: "dict[str, dict[str, str | int]] | None" = None


def _load_specs() -> "dict[str, FirmwareSpec]":
    raw = json.loads(files(__package__).joinpath("firmware_specs.json").read_text())
    return {name: FirmwareSpec(**spec) for name, spec in raw.items()}


_SPECS = _load_specs()
MICROPYTHON = _SPECS["micropython"]
CIRCUITPYTHON = _SPECS["circuitpython"]
KALUMA = _SPECS["kaluma"]
BOOTROM = _SPECS["bootrom"]


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


def flash_layout(spec: FirmwareSpec, board: str) -> "dict[str, int]":
    """Resolves `spec.flash_layout[board]` (MICROPYTHON/KALUMA only - see `FirmwareSpec.flash_layout`'s
    own docstring) into an all-`int` dict, parsing each `"0x..."` string value - `firmware_specs.json`
    stores them as hex strings for human readability, plain JSON has no hex-literal syntax. Raises
    `KeyError` for a spec with no `flash_layout` at all, or a `board` not present in it - both
    considered a caller bug (every board this project actually supports for a `flash_layout`-bearing
    spec must have an entry), not a runtime condition to degrade gracefully from.
    """
    if spec.flash_layout is None:
        raise KeyError(f"{spec!r} has no flash_layout")
    entry = spec.flash_layout[board]
    return {key: (int(value, 16) if isinstance(value, str) else value) for key, value in entry.items()}


def retrieve(spec: FirmwareSpec, image: "str | None" = None, board: str = _DEFAULT_BOARD) -> "Path | None":
    """
    Args:
        spec: which firmware to resolve (MICROPYTHON/CIRCUITPYTHON/KALUMA/BOOTROM).
        image: a version tag (defaults to `spec.default_tag`), a local file path, or a direct
            `http(s)://` URL (downloaded, cached, and reused from cache on subsequent runs the
            same way a resolved tag already is).
        board: which board's firmware variant to resolve a *tag* to (default: `"pico"`) - consulted
            only for `spec.boards`-shaped specs (MICROPYTHON/CIRCUITPYTHON/KALUMA) resolving a
            version tag; ignored entirely when `image` is a local path or URL, and ignored for a
            `known_versions`-shaped spec (BOOTROM - board-agnostic, see module docstring).
    """
    if image is None:
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
        version_map = spec.boards[board]
    else:
        version_map = spec.known_versions or {}

    url = _resolve_url(version_map, image)
    if url is None:
        _logger.error("Unknown version tag %r", image)
        return None

    return _download(url, _cache_filename(url))
