import os

__all__ = ("retrieve_kaluma",)

KALUMA_FW_FILENAME = "kaluma-rp2-pico-{version}.uf2"
KALUMA_FW_DOWNLOAD_URL = "https://github.com/kaluma-project/kaluma/releases/download/{version}/{filename}"
# Newest release still shipping a plain (non-W) `pico` RP2040 asset - 1.3.0+ dropped it in favor
# of pico2/pico2-w only. Also the version demo/kaluma_run.py was manually verified against.
KALUMA_DEFAULT_TAG = "1.2.1"


def _resolve_url(version_or_tag: str) -> tuple[str, str]:
    version = version_or_tag.removeprefix("v")
    filename = KALUMA_FW_FILENAME.format(version=version)
    return filename, KALUMA_FW_DOWNLOAD_URL.format(version=version, filename=filename)


def retrieve_kaluma(image: str | None = None) -> str | None:
    """
    Args:
       image (str | None): Kaluma version tag (defaults to *DEFAULT_TAG), or a local file path.
    """
    if image is None:
        image = KALUMA_DEFAULT_TAG

    if os.path.exists(image):
        print(f"Found local image: {image}")
        return image

    filename, url = _resolve_url(image)

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
    retrieve_kaluma()
