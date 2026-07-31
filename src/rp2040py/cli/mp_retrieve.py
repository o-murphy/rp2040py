import os

__all__ = ("retrieve_micropython",)

MICROPYTHON_FW_FILENAME = "RPI_PICO-{version}.uf2"
MICROPYTHON_FW_DOWNLOAD_URL = "https://micropython.org/resources/firmware/{filename}"
MICROPYTHON_KNOWN_FW_VERSIONS = {
    "1.16": "20210618-v1.16",
    "1.17": "20210902-v1.17",
    "1.18": "20220117-v1.18",
    "1.19.1": "20220618-v1.19.1",
    "1.20.0": "20230426-v1.20.0",
    "1.21.0": "20231005-v1.21.0",
    "1.28.0": "20260406-v1.28.0",
}
MICROPYTHON_DEFAULT_TAG = "1.21.0"

CIRCUITPYTHON_FW_FILENAME = "adafruit-circuitpython-raspberry_pi_pico-en_US-{version}.uf2"
CIRCUITPYTHON_FW_DOWNLOAD_URL = (
    "https://adafruit-circuit-python.s3.amazonaws.com/bin/raspberry_pi_pico/en_US/{filename}"
)
CIRCUITPYTHON_DEFAULT_TAG = "8.0.2"  # newest is "10.2.1"


def _resolve_known_version(tag: str, is_circuitpython: bool = False) -> str | None:
    if is_circuitpython:
        return tag.removeprefix("v")
    versions = MICROPYTHON_KNOWN_FW_VERSIONS
    tag = tag.removeprefix("v")
    if tag in versions:
        return versions[tag]
    for version in versions:
        if version.startswith(tag):
            return versions[version]
    return None


def _resolve_url(version_or_tag: str, is_circuitpython: bool = False) -> tuple[str, str]:
    if is_circuitpython:
        filename = CIRCUITPYTHON_FW_FILENAME.format(version=version_or_tag)
        return filename, CIRCUITPYTHON_FW_DOWNLOAD_URL.format(filename=filename)
    else:
        resolved = _resolve_known_version(version_or_tag, is_circuitpython=is_circuitpython)
        version = resolved or version_or_tag
        filename = MICROPYTHON_FW_FILENAME.format(version=version)
        return filename, MICROPYTHON_FW_DOWNLOAD_URL.format(filename=filename)


def retrieve_micropython(image: str | None = None, is_circuitpython: bool = False) -> str | None:
    """
    Args:
       image (str | None): micropython version tag (defaults to *DEFAULT_TAG), local filepath or version
    """
    if image is None:
        image = MICROPYTHON_DEFAULT_TAG if not is_circuitpython else CIRCUITPYTHON_DEFAULT_TAG

    if os.path.exists(image):
        print(f"Found local image: {image}")
        return image

    filename, url = _resolve_url(image, is_circuitpython=is_circuitpython)

    if os.path.exists(filename):
        print(f"Found local image: {filename}")
        return filename

    from urllib.error import HTTPError
    from urllib.request import urlretrieve

    def report_hook(chunk: int, chunk_size: int, size: int) -> object:
        if chunk == 0:
            print(f"Download: {filename} from {url}")
        elif chunk * chunk_size >= size:
            print(f"Download complete: file saved to: {filename}")
        return None

    try:
        urlretrieve(url, filename, reporthook=report_hook)
    except HTTPError:
        return None
    return filename


if __name__ == "__main__":
    retrieve_micropython()
