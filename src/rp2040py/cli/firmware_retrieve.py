"""Declarative, centralized firmware retrieval: one `FirmwareSpec` per supported firmware
(filename/URL templates, default tag, optional known-version-tag table), loaded from
`firmware_specs.json` next to this module, plus a single generic `retrieve()` that resolves a
version tag/local path/omitted `image` argument into an on-disk file (a UF2 for
MICROPYTHON/CIRCUITPYTHON/KALUMA, an ELF for BOOTROM), downloading it if necessary.

Keeping the per-firmware data in JSON (not Python literals) means bumping a default tag or adding
a new MicroPython release to `known_versions` is a plain data edit, not a code change - and rules
out the kind of `is_circuitpython: bool`-flag special-casing this replaces, where CircuitPython's
resolution silently skipped the same `v`-prefix stripping MicroPython's got (see CHANGELOG).
"""

import json
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import semver

__all__ = ("BOOTROM", "CIRCUITPYTHON", "KALUMA", "MICROPYTHON", "FirmwareSpec", "retrieve")


@dataclass(frozen=True)
class FirmwareSpec:
    filename_template: str  # "{version}" placeholder
    url_template: str  # "{filename}" placeholder, optionally also "{version}"
    default_tag: str
    # short tag -> filename-version (e.g. MicroPython's dated slug), prefix-matched; None for
    # firmware whose tag *is* the filename version (CircuitPython, Kaluma).
    known_versions: "dict[str, str] | None" = None


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
        print(
            f"warning: could not create cache directory {cache_dir} ({exc}); caching in the current directory instead",
            file=sys.stderr,
        )
        return Path()
    return cache_dir


def _parse_version(version: str) -> "semver.Version | None":
    try:
        return semver.Version.parse(version, optional_minor_and_patch=True)
    except ValueError:
        return None


def _resolve_version(spec: FirmwareSpec, tag: str) -> str:
    tag = tag.removeprefix("v")
    if spec.known_versions is not None:
        if tag in spec.known_versions:
            return spec.known_versions[tag]
        # A short tag means its dotted *components* as a prefix (e.g. "1.19" -> "1.19.1"), not a
        # raw string prefix: the old `known_tag.startswith(tag)` would also match "1.20.0"/
        # "1.21.0"/"1.28.0" for tag "1.2" purely from coincidental digit overlap - wrong, since
        # "1.2" means version 1.2.x, disjoint from 1.20.x/1.21.x/1.28.x, not a string fragment of
        # them. `semver.Version.parse(..., optional_minor_and_patch=True)` normalizes both sides
        # to real (major, minor, patch) tuples for comparison instead of comparing raw strings,
        # truncated to how many components the tag itself specified - so "1.19" only ever matches
        # known versions whose (major, minor) is exactly (1, 19), never a same-string-prefix but
        # numerically unrelated one. A short numeric-only tag like "1" still legitimately matches
        # every 1.x release, so pick the highest of those (rather than whichever happened to come
        # first in known_versions' key order, a silent footgun the moment a new version is
        # inserted anywhere but the end).
        precision = tag.count(".") + 1
        tag_version = _parse_version(tag)
        if tag_version is None:
            return tag
        matches = []
        for known_tag, filename_version in spec.known_versions.items():
            known_version = _parse_version(known_tag)
            if known_version is not None and known_version.to_tuple()[:precision] == tag_version.to_tuple()[:precision]:
                matches.append((known_version, filename_version))
        if matches:
            return max(matches)[1]
    return tag


def retrieve(spec: FirmwareSpec, image: "str | None" = None) -> "str | None":
    """
    Args:
        spec: which firmware to resolve (MICROPYTHON/CIRCUITPYTHON/KALUMA/BOOTROM).
        image: a version tag (defaults to `spec.default_tag`) or a local file path.
    """
    if image is None:
        image = spec.default_tag

    local_image = Path(image)
    if local_image.exists():
        print(f"Found local image: {local_image}")
        return str(local_image)

    version = _resolve_version(spec, image)
    filename = spec.filename_template.format(version=version)
    url = spec.url_template.format(version=version, filename=filename)
    cached_path = _cache_dir() / filename

    if cached_path.exists():
        print(f"Found local image: {cached_path}")
        return str(cached_path)

    from urllib.error import HTTPError
    from urllib.request import urlretrieve

    def report_hook(chunk: int, chunk_size: int, size: int) -> object:
        if chunk == 0:
            print(f"Download: {filename} from {url}")
        elif chunk * chunk_size >= size:
            print(f"Download complete: file saved to: {cached_path}")
        return None

    try:
        urlretrieve(url, cached_path, reporthook=report_hook)
    except HTTPError:
        return None
    return str(cached_path)
