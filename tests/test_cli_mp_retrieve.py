import pytest

from rp2040py.cli.mp_retrieve import (
    MICROPYTHON_DEFAULT_TAG,
    MICROPYTHON_KNOWN_FW_VERSIONS,
    retrieve_micropython,
)


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    # retrieve_micropython() checks/writes relative filenames in the current directory - run each
    # test from a throwaway directory so tests can't see each other's "downloaded" files.
    monkeypatch.chdir(tmp_path)


def test_returns_existing_local_path_without_touching_the_network(monkeypatch):
    local = "my_image.uf2"
    with open(local, "wb") as f:
        f.write(b"fake uf2 contents")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download for a path that already exists")

    monkeypatch.setattr("urllib.request.urlretrieve", _boom)

    assert retrieve_micropython(local) == local


def test_known_version_tag_resolves_to_the_dated_filename():
    filename = f"RPI_PICO-{MICROPYTHON_KNOWN_FW_VERSIONS['1.21.0']}.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython("1.21.0") == filename


def test_v_prefixed_tag_is_normalized_the_same_as_bare_tag():
    filename = f"RPI_PICO-{MICROPYTHON_KNOWN_FW_VERSIONS['1.21.0']}.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython("v1.21.0") == filename


def test_no_image_argument_defaults_to_the_recommended_tag():
    filename = f"RPI_PICO-{MICROPYTHON_KNOWN_FW_VERSIONS[MICROPYTHON_DEFAULT_TAG]}.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython() == filename


def test_circuitpython_default_tag_differs_from_micropythons():
    filename = "adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython(None, is_circuitpython=True) == filename


def test_circuitpython_version_maps_to_adafruit_filename():
    filename = "adafruit-circuitpython-raspberry_pi_pico-en_US-10.2.1.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython("10.2.1", is_circuitpython=True) == filename


def test_unknown_tag_falls_back_to_using_it_as_the_raw_version_suffix():
    filename = "RPI_PICO-not-a-known-tag.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_micropython("not-a-known-tag") == filename


def test_downloads_when_no_local_file_matches(monkeypatch):
    calls = []

    def _fake_urlretrieve(url, filename, reporthook=None):
        calls.append((url, filename))
        with open(filename, "wb") as f:
            f.write(b"downloaded")
        if reporthook is not None:
            reporthook(0, 8192, len(b"downloaded"))
            reporthook(1, 8192, len(b"downloaded"))
        return filename, None

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    result = retrieve_micropython("1.21.0")

    expected_filename = f"RPI_PICO-{MICROPYTHON_KNOWN_FW_VERSIONS['1.21.0']}.uf2"
    assert result == expected_filename
    assert calls == [(f"https://micropython.org/resources/firmware/{expected_filename}", expected_filename)]
    with open(expected_filename, "rb") as f:
        assert f.read() == b"downloaded"


def test_returns_none_on_http_error_instead_of_raising(monkeypatch):
    from urllib.error import HTTPError

    def _fake_urlretrieve(url, filename, reporthook=None):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    assert retrieve_micropython("not-a-real-version") is None
