from pathlib import Path

import pytest

from rp2040py.cli.firmware_retrieve import (
    BOOTROM,
    CIRCUITPYTHON,
    KALUMA,
    MICROPYTHON,
    FirmwareSpec,
    _resolve_version,
    retrieve,
)

# A synthetic spec, not MICROPYTHON's real known_versions table - keeps these tests independent of
# that table's actual contents changing over time, while deliberately including the exact shape
# that broke the old `known_tag.startswith(tag)` string-prefix matching: "1.20.0"/"1.21.0"/
# "1.28.0" all share the raw string prefix "1.2" despite being semantically unrelated (1.2.x)
# versions, plus a prerelease tag to confirm real semver precedence (not just numeric splitting)
# is what decides ties.
_SYNTHETIC_SPEC = FirmwareSpec(
    filename_template="fw-{version}.bin",
    url_template="https://example.invalid/{filename}",
    default_tag="1.21.0",
    known_versions={
        "1.16": "dated-1.16",
        "1.19.1": "dated-1.19.1",
        "1.20.0": "dated-1.20.0",
        "1.21.0": "dated-1.21.0",
        "1.28.0": "dated-1.28.0",
        "1.29.0-preview": "dated-1.29.0-preview",
    },
)


class TestResolveVersion:
    """Regression coverage for the `known_tag.startswith(tag)` bug: a short tag like "1.2" used to
    string-prefix-match "1.20.0"/"1.21.0"/"1.28.0" alike (wrong - "1.2" means version 1.2.x, which
    none of them are), and picked whichever came first in known_versions' key order rather than
    the actual highest match."""

    def test_exact_key_match_short_circuits_everything_else(self):
        assert _resolve_version(_SYNTHETIC_SPEC, "1.16") == "dated-1.16"

    def test_two_component_tag_matches_only_its_own_minor_family(self):
        # Not "dated-1.20.0"/"dated-1.28.0" - "1.19" and "1.19.1" share (major, minor) = (1, 19),
        # which none of the others do despite the raw string "1.2" being a substring of "1.20"/
        # "1.21"/"1.28" too.
        assert _resolve_version(_SYNTHETIC_SPEC, "1.19") == "dated-1.19.1"

    def test_ambiguous_string_prefix_with_no_real_minor_match_falls_back_unchanged(self):
        # "1.2" as a *version* is 1.2.x - disjoint from 1.20.x/1.21.x/1.28.x despite the string
        # "1.2" being a literal prefix of all three. No known version has (major, minor) = (1, 2),
        # so this must NOT silently resolve to any of them.
        assert _resolve_version(_SYNTHETIC_SPEC, "1.2") == "1.2"

    def test_bare_major_tag_resolves_to_the_highest_matching_version_not_key_order(self):
        # "1.29.0-preview" is listed *before* none of the others in insertion order tie-breaking
        # terms - it's simply the highest real version by semver precedence (1.29.0 > 1.28.0 etc.
        # regardless of the prerelease suffix, since precedence compares major.minor.patch first).
        assert _resolve_version(_SYNTHETIC_SPEC, "1") == "dated-1.29.0-preview"

    def test_v_prefix_is_stripped_before_matching(self):
        assert _resolve_version(_SYNTHETIC_SPEC, "v1.21.0") == "dated-1.21.0"

    def test_unparseable_tag_falls_back_to_itself(self):
        assert _resolve_version(_SYNTHETIC_SPEC, "not-a-version-at-all") == "not-a-version-at-all"


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


def _cache_path(filename: str) -> str:
    """Where `retrieve()` would look for/write `filename` once resolved by tag, given the
    isolated `HOME` above - pre-creating a file here (rather than in the cwd) is what makes a test
    exercise the "already cached, don't re-download" path."""
    path = Path.home() / ".cache" / "rp2040py" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


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


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_existing_local_path_without_touching_the_network(spec, monkeypatch):
    local = "my_image.uf2"
    with open(local, "wb") as f:
        f.write(b"fake uf2 contents")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download for a path that already exists")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    assert retrieve(spec, local) == local


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_no_image_argument_defaults_to_the_spec_default_tag(spec):
    # The default tag is itself resolved through known_versions (MicroPython's dated-slug table),
    # same as any other tag - not assumed to already be the filename version.
    version = (spec.known_versions or {}).get(spec.default_tag, spec.default_tag)
    filename = spec.filename_template.format(version=version)
    cached = _cache_path(filename)
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(spec) == cached


def test_known_version_tag_resolves_to_the_dated_filename():
    cached = _cache_path("RPI_PICO-20231005-v1.21.0.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(MICROPYTHON, "1.21.0") == cached


def test_unknown_tag_falls_back_to_using_it_as_the_raw_version_suffix():
    cached = _cache_path("RPI_PICO-not-a-known-tag.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(MICROPYTHON, "not-a-known-tag") == cached


@pytest.mark.parametrize(
    ("spec", "tag", "filename"),
    [
        (MICROPYTHON, "v1.21.0", "RPI_PICO-20231005-v1.21.0.uf2"),
        # Regression test: CircuitPython used to skip _resolve_known_version() entirely (dead code
        # in the old is_circuitpython-flag design), so a v-prefixed tag built a filename with the
        # literal "v" still in it and 404'd - unlike MicroPython, which stripped it correctly.
        (CIRCUITPYTHON, "v8.0.2", "adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2"),
        (KALUMA, "v1.2.1", "kaluma-rp2-pico-1.2.1.uf2"),
    ],
)
def test_v_prefixed_tag_is_normalized_the_same_as_bare_tag(spec, tag, filename):
    cached = _cache_path(filename)
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(spec, tag) == cached


def test_circuitpython_version_maps_to_adafruit_filename():
    cached = _cache_path("adafruit-circuitpython-raspberry_pi_pico-en_US-10.2.1.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(CIRCUITPYTHON, "10.2.1") == cached


def test_kaluma_version_tag_resolves_to_the_release_filename():
    cached = _cache_path("kaluma-rp2-pico-1.2.1.uf2")
    with open(cached, "wb") as f:
        f.write(b"fake")

    assert retrieve(KALUMA, "1.2.1") == cached


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


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_none_on_http_error_instead_of_raising(spec, monkeypatch):
    from urllib.error import HTTPError

    def _fake_urlopen(url, timeout=None):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    assert retrieve(spec, "not-a-real-version") is None


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
