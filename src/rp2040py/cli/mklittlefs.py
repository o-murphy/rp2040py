"""Build or update a littlefs image for MicroPython's filesystem support.

Requires the optional ``littlefs-python`` dependency (``pip install rp2040py[fs]``), imported
lazily here so the rest of the CLI stays usable without it.
"""

import os
from collections.abc import Sequence

from rp2040py.device.load_flash import MICROPYTHON_FS_BLOCKCOUNT, MICROPYTHON_FS_BLOCKSIZE

__all__ = (
    "LITTLEFS_DEFAULT_DISK_VERSION",
    "LITTLEFS_DISK_VERSIONS",
    "build_littlefs_image",
)

# Pins the littlefs on-disk format to v2.0: newer littlefs-python releases default to a newer
# format (v2.1) that MicroPython <=1.21's bundled littlefs fails to mount - and hangs indefinitely
# retrying rather than raising an error. v2.0 stays readable by newer littlefs implementations too
# (incl. MicroPython 1.28's). See docs/PORTING.md#known-differences-from-rp2040js.
LITTLEFS_DISK_VERSIONS = {"2.0": 0x00020000, "2.1": 0x00020001}
LITTLEFS_DEFAULT_DISK_VERSION = "2.0"


def build_littlefs_image(
    output: str,
    files: "Sequence[str]",
    block_size: int = MICROPYTHON_FS_BLOCKSIZE,
    block_count: int = MICROPYTHON_FS_BLOCKCOUNT,
    disk_version: str = LITTLEFS_DEFAULT_DISK_VERSION,
) -> None:
    """Write ``files`` into a littlefs image at ``output``, the first becoming ``main.py``.

    If ``output`` already exists, it's opened and updated in place rather than reformatted.
    """
    try:
        from littlefs import LittleFS, UserContext
    except ImportError as exc:
        raise SystemExit(
            "littlefs-python is required for mklittlefs; install it with `pip install rp2040py[fs]`"
        ) from exc

    if os.path.exists(output):
        with open(output, "rb") as fh:
            context = UserContext(buffer=bytearray(fh.read()))
    else:
        context = UserContext(buffsize=block_size * block_count)

    # mount=True (the default) mounts the existing filesystem if the buffer holds one, and falls
    # back to formatting it otherwise - giving us "create or open if it exists" for free.
    disk_version_ = LITTLEFS_DISK_VERSIONS.get(disk_version)
    lfs = LittleFS(
        context=context, block_size=block_size, block_count=block_count, prog_size=256, disk_version=disk_version_
    )

    main = True
    for filename in files:
        dest_name = "main.py" if main else os.path.basename(filename)
        with open(filename, "rb") as src_file, lfs.open(dest_name, "wb") as lfs_file:
            lfs_file.write(src_file.read())
        main = False

    with open(output, "wb") as fh:
        fh.write(lfs.context.buffer)
