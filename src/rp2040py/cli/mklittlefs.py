"""Build a littlefs image for MicroPython's filesystem support.

Requires the optional ``littlefs-python`` dependency (``pip install rp2040py[fs]``), imported
lazily here so the rest of the CLI stays usable without it.
"""

from collections.abc import Sequence
from os import PathLike
from pathlib import Path

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
    output: PathLike,
    files: "Sequence[PathLike]",
    block_size: int = MICROPYTHON_FS_BLOCKSIZE,
    block_count: int = MICROPYTHON_FS_BLOCKCOUNT,
    disk_version: str = LITTLEFS_DEFAULT_DISK_VERSION,
    main: "str | None" = None,
    force: bool = False,
) -> None:
    """Write ``files`` into a fresh littlefs image at ``output``, each keeping its own basename.

    ``main``, if given, must match the *basename* of one of ``files`` (e.g. ``"app.py"``, not
    ``"src/app.py"``) - that file is written as ``main.py`` (MicroPython's auto-run entry point)
    instead of its own basename. Pass nothing for filesystems that don't need one, e.g. staging
    modules for a raw-REPL-driven test. ``files`` may be empty, producing a freshly formatted
    empty image.

    Always builds from scratch - never merges with any existing content at ``output``, since a
    previous image there may have used a different ``block_size``/``block_count`` (mounting it
    with today's values would otherwise silently ignore them, or reformat and drop files without
    warning). Raises ``ValueError`` if ``output`` already exists and ``force`` is not set, instead
    of overwriting it by surprise.

    ``disk_version`` selects the littlefs on-disk format, one of ``LITTLEFS_DISK_VERSIONS``
    (``"2.0"`` or ``"2.1"``).
    """
    try:
        from littlefs import LittleFS, UserContext
    except ImportError as exc:
        raise SystemExit(
            "littlefs-python is required for mklittlefs; install it with `pip install rp2040py[fs]`"
        ) from exc

    if disk_version not in LITTLEFS_DISK_VERSIONS:
        raise ValueError(f"unknown disk_version {disk_version!r}; expected one of {tuple(LITTLEFS_DISK_VERSIONS)}")

    if Path(output).exists() and not force:
        raise ValueError(f"{output!r} already exists - pass --force to overwrite it")

    basenames = [Path(f).name for f in files]
    if main is not None and main not in basenames:
        raise ValueError(f"--main {main!r} must match the basename of one of the given files: {basenames}")

    # Two files landing on the same littlefs destination (two same-named files in different host
    # directories, or a file already named main.py colliding with --main's target) would otherwise
    # silently overwrite one another - whichever gets written last wins, with no warning.
    dest_names: dict[str, PathLike] = {}
    for filename in files:
        dest_name = "main.py" if Path(filename).name == main else Path(filename).name
        if dest_name in dest_names:
            raise ValueError(f"{filename!r} and {dest_names[dest_name]!r} would both be written as {dest_name!r}")
        dest_names[dest_name] = filename

    context = UserContext(buffsize=block_size * block_count)
    lfs = LittleFS(
        context=context,
        block_size=block_size,
        block_count=block_count,
        prog_size=256,
        disk_version=LITTLEFS_DISK_VERSIONS[disk_version],
    )

    for dest_name, filename in dest_names.items():
        with open(filename, "rb") as src_file, lfs.open(dest_name, "wb") as lfs_file:
            lfs_file.write(src_file.read())

    # Explicit unmount, not left to GC: littlefs-python's LittleFS otherwise only gets torn down
    # implicitly when garbage collected, and under PyPy's non-refcounting GC that can happen at an
    # arbitrary later point (even interpreter shutdown) instead of deterministically here like
    # under CPython - confirmed as the cause of a real "lfs_file_sync: Assertion
    # `lfs_mlist_isopen(...)` failed" abort (SIGABRT) writing a multi-file image under PyPy.
    lfs.unmount()

    with open(output, "wb") as fh:
        fh.write(lfs.context.buffer)
