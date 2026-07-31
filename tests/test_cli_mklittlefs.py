import pytest

littlefs = pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra (littlefs-python)")

from rp2040py.cli.mklittlefs import (
    LITTLEFS_DEFAULT_DISK_VERSION,
    LITTLEFS_DISK_VERSIONS,
    build_littlefs_image,
)


def _read_back(image: str, filename: str) -> bytes:
    from littlefs import LittleFS, UserContext

    from rp2040py.device.load_flash import MICROPYTHON_FS_BLOCKCOUNT, MICROPYTHON_FS_BLOCKSIZE

    with open(image, "rb") as fh:
        context = UserContext(buffer=bytearray(fh.read()))
    lfs = LittleFS(
        context=context, block_size=MICROPYTHON_FS_BLOCKSIZE, block_count=MICROPYTHON_FS_BLOCKCOUNT, mount=True
    )
    with lfs.open(filename, "rb") as f:
        return f.read()


def test_first_file_becomes_main_py(tmp_path):
    main_src = tmp_path / "app.py"
    main_src.write_bytes(b"print('hello')\n")
    image = str(tmp_path / "littlefs.img")

    build_littlefs_image(image, [str(main_src)])

    assert _read_back(image, "main.py") == b"print('hello')\n"


def test_subsequent_files_keep_their_basename(tmp_path):
    main_src = tmp_path / "app.py"
    main_src.write_bytes(b"import lib\n")
    lib_src = tmp_path / "lib.py"
    lib_src.write_bytes(b"VALUE = 42\n")
    image = str(tmp_path / "littlefs.img")

    build_littlefs_image(image, [str(main_src), str(lib_src)])

    assert _read_back(image, "main.py") == b"import lib\n"
    assert _read_back(image, "lib.py") == b"VALUE = 42\n"


def test_binary_content_is_preserved_byte_for_byte(tmp_path):
    # mklittlefs used to open source files in text mode, which corrupted .mpy (compiled
    # MicroPython bytecode) files and any other non-UTF-8/binary content, plus translated line
    # endings in .py sources - writing/reading raw bytes here guards against a regression.
    main_src = tmp_path / "app.py"
    main_src.write_bytes(b"\r\n\x00\xff\xfe binary garbage \r\n")
    image = str(tmp_path / "littlefs.img")

    build_littlefs_image(image, [str(main_src)])

    assert _read_back(image, "main.py") == b"\r\n\x00\xff\xfe binary garbage \r\n"


def test_existing_image_is_updated_in_place_not_reformatted(tmp_path):
    first_src = tmp_path / "first.py"
    first_src.write_bytes(b"first\n")
    second_src = tmp_path / "second.py"
    second_src.write_bytes(b"second\n")
    other_src = tmp_path / "other.py"
    other_src.write_bytes(b"other\n")
    image = str(tmp_path / "littlefs.img")

    build_littlefs_image(image, [str(first_src), str(other_src)])
    build_littlefs_image(image, [str(second_src), str(other_src)])

    # main.py now comes from the second build, but other.py survives untouched from the first.
    assert _read_back(image, "main.py") == b"second\n"
    assert _read_back(image, "other.py") == b"other\n"


def test_unknown_disk_version_raises_value_error_instead_of_crashing_in_littlefs_python(tmp_path):
    main_src = tmp_path / "app.py"
    main_src.write_bytes(b"pass\n")
    image = str(tmp_path / "littlefs.img")

    with pytest.raises(ValueError, match="disk_version"):
        build_littlefs_image(image, [str(main_src)], disk_version="9.9")


@pytest.mark.parametrize("disk_version", list(LITTLEFS_DISK_VERSIONS))
def test_every_advertised_disk_version_actually_works(tmp_path, disk_version):
    main_src = tmp_path / "app.py"
    main_src.write_bytes(b"pass\n")
    image = str(tmp_path / "littlefs.img")

    build_littlefs_image(image, [str(main_src)], disk_version=disk_version)

    assert _read_back(image, "main.py") == b"pass\n"


def test_default_disk_version_is_2_0_for_old_micropython_compatibility():
    # See docs/PORTING.md#littlefs-image-format-vs-old-micropython-not-actually-a-port-bug: newer
    # littlefs-python defaults changed to a format MicroPython <=1.21 can't mount.
    assert LITTLEFS_DEFAULT_DISK_VERSION == "2.0"
    assert LITTLEFS_DISK_VERSIONS["2.0"] == 0x00020000
