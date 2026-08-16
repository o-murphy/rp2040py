from os import PathLike
from pathlib import Path

import pytest

from rp2040py.utils.firmware_retrieve import (
    BOOTROM,
    CIRCUITPYTHON,
    KALUMA,
    MICROPYTHON,
    FirmwareSpec,
    _resolve_url,
    retrieve,
)

# A synthetic version map, not any real spec's actual `boards`/`known_versions` table - keeps
# these tests independent of that table's actual contents changing over time, while deliberately
# including the exact shape that broke the old `known_tag.startswith(tag)` string-prefix matching:
# "1.20.0"/"1.21.0"/"1.28.0" all share the raw string prefix "1.2" despite being semantically
# unrelated (1.2.x) versions, plus a prerelease tag to confirm real semver precedence (not just
# numeric splitting) is what decides ties.
_SYNTHETIC_VERSIONS = {
    "1.16": "https://example.invalid/dated-1.16",
    "1.19.1": "https://example.invalid/dated-1.19.1",
    "1.20.0": "https://example.invalid/dated-1.20.0",
    "1.21.0": "https://example.invalid/dated-1.21.0",
    "1.28.0": "https://example.invalid/dated-1.28.0",
    "1.29.0-preview": "https://example.invalid/dated-1.29.0-preview",
}


class TestResolveUrl:
    """Regression coverage for the `known_tag.startswith(tag)` bug: a short tag like "1.2" used to
    string-prefix-match "1.20.0"/"1.21.0"/"1.28.0" alike (wrong - "1.2" means version 1.2.x, which
    none of them are), and picked whichever came first in the version map's key order rather than
    the actual highest match."""

    def test_exact_key_match_short_circuits_everything_else(self):
        assert _resolve_url(_SYNTHETIC_VERSIONS, "1.16") == "https://example.invalid/dated-1.16"

    def test_two_component_tag_matches_only_its_own_minor_family(self):
        # Not "dated-1.20.0"/"dated-1.28.0" - "1.19" and "1.19.1" share (major, minor) = (1, 19),
        # which none of the others do despite the raw string "1.2" being a substring of "1.20"/
        # "1.21"/"1.28" too.
        assert _resolve_url(_SYNTHETIC_VERSIONS, "1.19") == "https://example.invalid/dated-1.19.1"

    def test_ambiguous_string_prefix_with_no_real_minor_match_resolves_to_nothing(self):
        # "1.2" as a *version* is 1.2.x - disjoint from 1.20.x/1.21.x/1.28.x despite the string
        # "1.2" being a literal prefix of all three. No known version has (major, minor) = (1, 2),
        # so this must NOT silently resolve to any of them.
        assert _resolve_url(_SYNTHETIC_VERSIONS, "1.2") is None

    def test_bare_major_tag_resolves_to_the_highest_matching_version_not_key_order(self):
        # "1.29.0-preview" is listed before none of the others in insertion order tie-breaking
        # terms - it's simply the highest real version by semver precedence (1.29.0 > 1.28.0 etc.
        # regardless of the prerelease suffix, since precedence compares major.minor.patch first).
        assert _resolve_url(_SYNTHETIC_VERSIONS, "1") == "https://example.invalid/dated-1.29.0-preview"

    def test_v_prefix_is_stripped_before_matching(self):
        assert _resolve_url(_SYNTHETIC_VERSIONS, "v1.21.0") == "https://example.invalid/dated-1.21.0"

    def test_unparseable_tag_resolves_to_nothing(self):
        assert _resolve_url(_SYNTHETIC_VERSIONS, "not-a-version-at-all") is None


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    # retrieve() checks the current directory for a local-path passthrough, and its cache
    # directory (~/.cache/rp2040py, i.e. home-relative) for anything resolved by tag - isolate
    # both so tests can't see each other's "downloaded" files or the real user's actual cache.
    # Patching Path.home() directly (rather than the HOME env var) is required for this to work on
    # Windows too: ntpath's expanduser() prefers USERPROFILE over HOME, so with HOME alone this
    # fixture was a no-op on Windows runners and every test after the first in the same
    # cibuildwheel job saw the previous test's "downloaded" files in the real user cache dir.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def _cache_path(filename: PathLike) -> Path:
    """Where `retrieve()` would look for/write `filename` once resolved, given the isolated
    `HOME` above - pre-creating a file here (rather than in the cwd) is what makes a test exercise
    the "already cached, don't re-download" path."""
    path = Path.home() / ".cache" / "rp2040py" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class _FakeResponse:
    """Stands in for the context-managed response `urlopen()` returns - just enough of its
    protocol (`with ... as response:`, `response.read(size)`) for `retrieve()`'s manual
    chunked-copy loop to drive."""

    def __init__(self, data: bytes) -> None:
        self._remaining = data

    def __enter__(self) -> "_FakeResponse":  # noqa: PYI034 (Self needs Python 3.11+)
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        chunk, self._remaining = self._remaining[:size], self._remaining[size:]
        return chunk


def _fake_urlopen(monkeypatch: pytest.MonkeyPatch, calls: list, data: bytes = b"downloaded") -> None:
    """Patches `urllib.request.urlopen` (as `retrieve()` imports and calls it) to hand back
    `data` without touching the network, recording each requested URL in `calls`."""

    def _urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(data)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)


def _url_filename(url: str) -> str:
    return url.rsplit("/", 1)[-1]


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_existing_local_path_without_touching_the_network(spec, monkeypatch):
    local = Path("my_image.uf2")
    with open(local, "wb") as f:
        f.write(b"fake uf2 contents")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download for a path that already exists")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    assert retrieve(spec, local) == local


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA])
def test_no_image_argument_defaults_to_the_spec_default_tag(spec):
    board_spec = spec.boards["pico"]
    url = board_spec.fw[board_spec.default_tag]
    cached = _cache_path(_url_filename(url))
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(spec) == cached


def test_bootrom_default_tag_is_board_agnostic():
    url = BOOTROM.known_versions[BOOTROM.default_tag]
    cached = _cache_path(_url_filename(url))
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(BOOTROM) == cached


def test_known_version_tag_resolves_to_the_real_url():
    cached = _cache_path("RPI_PICO-20231005-v1.21.0.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(MICROPYTHON, "1.21.0") == cached


def test_pico_w_board_resolves_to_a_different_url_than_pico():
    """The whole point of this redesign: the same tag, different board, must resolve to the
    board-specific build - not the same (or a board-agnostic) file."""
    pico_cached = _cache_path("RPI_PICO-20231005-v1.21.0.uf2")
    pico_w_cached = _cache_path("RPI_PICO_W-20231005-v1.21.0.uf2")
    with open(pico_cached, "wb") as f:
        f.write(b"pico build")
    with open(pico_w_cached, "wb") as f:
        f.write(b"pico_w build")

    assert retrieve(MICROPYTHON, "1.21.0", board="pico") == pico_cached
    assert retrieve(MICROPYTHON, "1.21.0", board="pico_w") == pico_w_cached


def test_kaluma_pico_w_asset_name_includes_the_w_suffix():
    cached = _cache_path("kaluma-rp2-pico-w-1.2.1.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(KALUMA, "1.2.1", board="pico_w") == cached


def test_unknown_board_returns_none_instead_of_raising():
    assert retrieve(MICROPYTHON, "1.21.0", board="not_a_real_board") is None


def test_unknown_tag_returns_none_instead_of_falling_back_to_it_as_a_literal_suffix():
    # Old behavior (kept `known_versions: dict[tag, filename-version]` around a template) fell
    # back to using the raw, unresolved tag as the filename-version suffix, silently producing a
    # URL that would 404 - this asserts the new behavior instead: a clear failure right away, no
    # network round trip attempted for a tag that was never going to resolve to anything real.
    def _boom(*args, **kwargs):
        raise AssertionError("must not attempt a download for a tag that never resolved to a URL")

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        assert retrieve(MICROPYTHON, "not-a-known-tag") is None
    finally:
        urllib.request.urlopen = original


def test_bootrom_ignores_the_board_argument():
    url = BOOTROM.known_versions["b2"]
    cached = _cache_path(_url_filename(url))
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(BOOTROM, "b2", board="pico_w") == cached


@pytest.mark.parametrize(
    ("spec", "tag", "board", "filename"),
    [
        (MICROPYTHON, "v1.21.0", "pico", "RPI_PICO-20231005-v1.21.0.uf2"),
        # Regression test: CircuitPython used to skip _resolve_known_version() entirely (dead code
        # in the old is_circuitpython-flag design), so a v-prefixed tag built a filename with the
        # literal "v" still in it and 404'd - unlike MicroPython, which stripped it correctly.
        (CIRCUITPYTHON, "v8.0.2", "pico", "adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2"),
        (KALUMA, "v1.2.1", "pico", "kaluma-rp2-pico-1.2.1.uf2"),
    ],
)
def test_v_prefixed_tag_is_normalized_the_same_as_bare_tag(spec, tag, board, filename):
    cached = _cache_path(filename)
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(spec, tag, board=board) == cached


def test_downloads_when_no_local_file_matches(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, calls)

    result = retrieve(MICROPYTHON, "1.21.0")

    expected_path = _cache_path("RPI_PICO-20231005-v1.21.0.uf2")
    assert result == expected_path
    assert calls == ["https://micropython.org/resources/firmware/RPI_PICO-20231005-v1.21.0.uf2"]
    with open(expected_path, "rb") as f:
        assert f.read() == b"downloaded"


def test_kaluma_download_url_includes_the_version_path_segment(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, calls)

    result = retrieve(KALUMA, "1.2.1")

    expected_path = _cache_path("kaluma-rp2-pico-1.2.1.uf2")
    assert result == expected_path
    assert calls == ["https://github.com/kaluma-project/kaluma/releases/download/1.2.1/kaluma-rp2-pico-1.2.1.uf2"]


def test_bootrom_download_url_includes_the_version_path_segment(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, calls)

    result = retrieve(BOOTROM, "b2")

    expected_path = _cache_path("b2.elf")
    assert result == expected_path
    assert calls == ["https://github.com/raspberrypi/pico-bootrom-rp2040/releases/download/b2/b2.elf"]


def test_direct_url_downloads_and_caches_under_its_own_basename(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, calls)
    url = "https://example.invalid/custom/my-firmware-1.0.0.uf2"

    result = retrieve(MICROPYTHON, url)

    expected_path = _cache_path("my-firmware-1.0.0.uf2")
    assert result == expected_path
    assert calls == [url]


def test_direct_url_ignores_board_entirely():
    """Board-gated resolution is purely a tag-path concern - a raw URL is used exactly as given
    regardless of --board (docs/CYW43_WIFI_BACKLOG.md's "Board only ever affects the tag path")."""
    url = "https://example.invalid/custom/my-firmware-1.0.0.uf2"
    cached = _cache_path("my-firmware-1.0.0.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(MICROPYTHON, url, board="pico_w") == cached


def test_direct_url_with_no_path_component_caches_under_a_url_hash(monkeypatch):
    calls = []
    _fake_urlopen(monkeypatch, calls)
    url = "https://example.invalid/?id=12345"

    import hashlib

    expected_filename = hashlib.sha256(url.encode()).hexdigest()[:16]

    result = retrieve(MICROPYTHON, url)

    assert result == _cache_path(expected_filename)


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_none_on_http_error_instead_of_raising(spec, monkeypatch):
    from urllib.error import HTTPError

    def _fake_urlopen(url, timeout=None):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    assert retrieve(spec) is None


def test_removes_partial_file_and_returns_none_on_timeout(monkeypatch):
    class _DyingResponse:
        def __enter__(self) -> "_DyingResponse":  # noqa: PYI034 (Self needs Python 3.11+)
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            raise TimeoutError("timed out")

    def _fake_urlopen(url: str, timeout: float | None = None) -> "_DyingResponse":
        return _DyingResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = retrieve(MICROPYTHON, "1.21.0")

    assert result is None
    assert not Path(_cache_path("RPI_PICO-20231005-v1.21.0.uf2")).exists()


def test_firmware_spec_boards_and_known_versions_are_mutually_exclusive_by_family():
    """Documents the contract every built-in spec follows (module docstring): board-aware
    families set `boards`, never `known_versions`; BOOTROM sets `known_versions`, never
    `boards`."""
    for spec in (MICROPYTHON, CIRCUITPYTHON, KALUMA):
        assert isinstance(spec, FirmwareSpec)
        assert spec.boards is not None
        assert spec.known_versions is None
    assert BOOTROM.boards is None
    assert BOOTROM.known_versions is not None
