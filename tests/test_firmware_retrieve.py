from pathlib import Path

import pytest

from rp2040py.cli.firmware_retrieve import BOOTROM, CIRCUITPYTHON, KALUMA, MICROPYTHON, retrieve


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    # retrieve() checks the current directory for a local-path passthrough, and its cache
    # directory (~/.cache/rp2040py, i.e. HOME-relative) for anything resolved by tag - isolate
    # both so tests can't see each other's "downloaded" files or the real user's actual cache.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


def _cache_path(filename: str) -> str:
    """Where `retrieve()` would look for/write `filename` once resolved by tag, given the
    isolated `HOME` above - pre-creating a file here (rather than in the cwd) is what makes a test
    exercise the "already cached, don't re-download" path."""
    path = Path.home() / ".cache" / "rp2040py" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_existing_local_path_without_touching_the_network(spec, monkeypatch):
    local = "my_image.uf2"
    with open(local, "wb") as f:
        f.write(b"fake uf2 contents")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download for a path that already exists")

    monkeypatch.setattr("urllib.request.urlretrieve", _boom)

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

    def _fake_urlretrieve(url, filename, reporthook=None):
        calls.append((url, str(filename)))
        with open(filename, "wb") as f:
            f.write(b"downloaded")
        if reporthook is not None:
            reporthook(0, 8192, len(b"downloaded"))
            reporthook(1, 8192, len(b"downloaded"))
        return filename, None

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    result = retrieve(MICROPYTHON, "1.21.0")

    expected_path = _cache_path("RPI_PICO-20231005-v1.21.0.uf2")
    assert result == expected_path
    assert calls == [("https://micropython.org/resources/firmware/RPI_PICO-20231005-v1.21.0.uf2", expected_path)]
    with open(expected_path, "rb") as f:
        assert f.read() == b"downloaded"


def test_kaluma_download_url_includes_the_version_path_segment(monkeypatch):
    calls = []

    def _fake_urlretrieve(url, filename, reporthook=None):
        calls.append((url, str(filename)))
        with open(filename, "wb") as f:
            f.write(b"downloaded")

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    result = retrieve(KALUMA, "1.2.1")

    expected_path = _cache_path("kaluma-rp2-pico-1.2.1.uf2")
    assert result == expected_path
    assert calls == [
        (
            "https://github.com/kaluma-project/kaluma/releases/download/1.2.1/kaluma-rp2-pico-1.2.1.uf2",
            expected_path,
        )
    ]


def test_bootrom_download_url_includes_the_version_path_segment(monkeypatch):
    calls = []

    def _fake_urlretrieve(url, filename, reporthook=None):
        calls.append((url, str(filename)))
        with open(filename, "wb") as f:
            f.write(b"downloaded")

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    result = retrieve(BOOTROM, "b2")

    expected_path = _cache_path("b2.elf")
    assert result == expected_path
    assert calls == [
        (
            "https://github.com/raspberrypi/pico-bootrom-rp2040/releases/download/b2/b2.elf",
            expected_path,
        )
    ]


@pytest.mark.parametrize("spec", [MICROPYTHON, CIRCUITPYTHON, KALUMA, BOOTROM])
def test_returns_none_on_http_error_instead_of_raising(spec, monkeypatch):
    from urllib.error import HTTPError

    def _fake_urlretrieve(url, filename, reporthook=None):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    assert retrieve(spec, "not-a-real-version") is None
