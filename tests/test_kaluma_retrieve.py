import pytest

from rp2040py.cli.kaluma_retrieve import KALUMA_DEFAULT_TAG, retrieve_kaluma


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    # retrieve_kaluma() checks/writes relative filenames in the current directory - run each test
    # from a throwaway directory so tests can't see each other's "downloaded" files.
    monkeypatch.chdir(tmp_path)


def test_returns_existing_local_path_without_touching_the_network(monkeypatch):
    local = "my_image.uf2"
    with open(local, "wb") as f:
        f.write(b"fake uf2 contents")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download for a path that already exists")

    monkeypatch.setattr("urllib.request.urlretrieve", _boom)

    assert retrieve_kaluma(local) == local


def test_version_tag_resolves_to_the_release_filename():
    filename = "kaluma-rp2-pico-1.2.1.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_kaluma("1.2.1") == filename


def test_v_prefixed_tag_is_normalized_the_same_as_bare_tag():
    filename = "kaluma-rp2-pico-1.2.1.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_kaluma("v1.2.1") == filename


def test_no_image_argument_defaults_to_the_recommended_tag():
    filename = f"kaluma-rp2-pico-{KALUMA_DEFAULT_TAG}.uf2"
    with open(filename, "wb") as f:
        f.write(b"fake")

    assert retrieve_kaluma() == filename


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

    result = retrieve_kaluma("1.2.1")

    expected_filename = "kaluma-rp2-pico-1.2.1.uf2"
    assert result == expected_filename
    expected_url = "https://github.com/kaluma-project/kaluma/releases/download/1.2.1/kaluma-rp2-pico-1.2.1.uf2"
    assert calls == [(expected_url, expected_filename)]
    with open(expected_filename, "rb") as f:
        assert f.read() == b"downloaded"


def test_returns_none_on_http_error_instead_of_raising(monkeypatch):
    from urllib.error import HTTPError

    def _fake_urlretrieve(url, filename, reporthook=None):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    assert retrieve_kaluma("not-a-real-version") is None
