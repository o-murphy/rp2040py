"""Tests for demo/mkfat12.py - the CIRCUITPY (FAT12) image builder CircuitPython runs `code.py`
off (docs/records/0085). Driven via subprocess for the same reasons test_demo_mklittlefs_dump.py
is: demo/ isn't part of the installed package or on tests/'s import path, and running it the way
the docs tell users to also exercises its CLI rather than just the underlying functions.

The on-disk structure is parsed here with plain `struct` rather than by importing the module's own
parser, so a bug in `Fat12Volume` can't agree with itself: these assertions are against the FAT12
layout as documented, and against the numbers a real CircuitPython-formatted volume was measured
to have."""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("subprocess-driven CLI tests need sys.executable, which is empty on Android", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "demo" / "mkfat12.py"

# The Waveshare RP2040-LCD-0.96's own numbers, and mkfat12's defaults: a 1 MiB CIRCUITPY volume
# inside the 2 MiB flash region `--fat12` loads (see the script's own docstring for both).
VOLUME_BYTES = 1024 * 1024
IMAGE_BYTES = 4096 * 512


def _run(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )


def _read(image: Path, name: str) -> bytes:
    """`--read` as raw bytes. Deliberately not `text=True` like `_run()`: on Windows that would
    translate the newlines on the way out and compare something other than what is in the image -
    which is how these tests passed on Linux and failed on the windows-latest leg of the
    pre-commit matrix, before mkfat12 was fixed to write bytes to stdout."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", str(image), "--read", name],
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _build(tmp_path: Path, *files: str, extra: "list[str] | None" = None) -> Path:
    output = tmp_path / "circuitpy.img"
    result = _run("--output", str(output), *files, *(extra or []))
    assert result.returncode == 0, result.stderr
    return output


def _write(tmp_path: Path, name: str, text: str) -> Path:
    # newline="": no platform translation, so the bytes that land in the image are the bytes
    # written here on every OS - the same reason _read() below stays in binary.
    path = tmp_path / name
    path.write_text(text, newline="")
    return path


def _root_entries(image: bytes) -> "dict[bytes, tuple[int, int]]":
    """`{raw 8.3 name: (first cluster, size)}` for the volume's root directory, straight out of
    the BPB - deliberately not using the module's own reader."""
    reserved = struct.unpack("<H", image[14:16])[0]
    fat_count = image[16]
    root_entries = struct.unpack("<H", image[17:19])[0]
    sectors_per_fat = struct.unpack("<H", image[22:24])[0]
    root_offset = (reserved + fat_count * sectors_per_fat) * 512
    found = {}
    for index in range(root_entries):
        entry = image[root_offset + index * 32 : root_offset + index * 32 + 32]
        if entry[0] == 0x00:
            break
        if entry[11] in (0x0F, 0x08):  # long-name fragment, volume label
            continue
        found[entry[:11]] = (struct.unpack("<H", entry[26:28])[0], struct.unpack("<I", entry[28:32])[0])
    return found


def test_fresh_volume_matches_the_geometry_real_firmware_formats(tmp_path):
    # Every field here was read back off a CIRCUITPY volume CircuitPython formatted itself on this
    # board (docs/records/0085): 512-byte sectors, one sector per cluster, one reserved sector,
    # a single FAT, 512 root entries, media 0xf8, and the volume's own sector count.
    image = _build(tmp_path).read_bytes()
    assert struct.unpack("<H", image[11:13])[0] == 512
    assert image[13] == 1
    assert struct.unpack("<H", image[14:16])[0] == 1
    assert image[16] == 1
    assert struct.unpack("<H", image[17:19])[0] == 512
    assert struct.unpack("<H", image[19:21])[0] == VOLUME_BYTES // 512
    assert image[21] == 0xF8
    assert image[54:57] == b"FAT"  # what FatFS's own check_fs() looks for
    assert struct.unpack("<H", image[510:512])[0] == 0xAA55


def test_image_is_exactly_the_flash_region_and_padded_as_erased(tmp_path):
    # load_circuitpython_flash_image() rejects anything but an exact fs_blocksize * fs_blockcount,
    # and the region past the volume reads as erased flash on a real dump, not as zeros.
    image = _build(tmp_path).read_bytes()
    assert len(image) == IMAGE_BYTES
    assert image[VOLUME_BYTES:] == b"\xff" * (IMAGE_BYTES - VOLUME_BYTES)


def test_files_round_trip_under_their_own_and_renamed_paths(tmp_path):
    code = _write(tmp_path, "guest.py", "print('hello')\n")
    boot = _write(tmp_path, "boot.py", "print('boot')\n")
    output = _build(tmp_path, f"{code}=code.py", str(boot))

    entries = _root_entries(output.read_bytes())
    assert set(entries) == {b"CODE    PY ", b"BOOT    PY "}
    assert entries[b"CODE    PY "][1] == len(code.read_bytes())

    for name, source in (("code.py", code), ("boot.py", boot)):
        assert _read(output, name) == source.read_bytes()


def test_rewriting_a_file_replaces_it_rather_than_duplicating_it(tmp_path):
    first = _write(tmp_path, "first.py", "print('first')\n" * 200)  # more than one cluster
    output = _build(tmp_path, f"{first}=code.py")
    second = _write(tmp_path, "second.py", "print('second')\n")

    result = _run("--output", str(output), "--base", str(output), f"{second}=code.py")
    assert result.returncode == 0, result.stderr
    assert list(_root_entries(output.read_bytes())) == [b"CODE    PY "]
    assert _read(output, "code.py") == second.read_bytes()


def test_content_survives_byte_for_byte_including_crlf(tmp_path):
    # The regression test for the Windows-only failure above, made to fail everywhere rather than
    # only where stdout translates newlines: `--read` decoding to text mangled *two* things - CRLF
    # (visible only on Windows) and any byte that isn't valid UTF-8 (visible everywhere, via
    # errors="replace"). A faithful reader returns the file's bytes on every platform.
    source = tmp_path / "crlf.py"
    source.write_bytes(b"print('a')\r\nprint('b')\n\rtrail\xff\xfe\x80ing")
    output = _build(tmp_path, f"{source}=code.py")
    assert _read(output, "code.py") == source.read_bytes()


def test_a_name_that_needs_a_long_filename_entry_is_refused(tmp_path):
    # settings.toml is the one CircuitPython file this builder genuinely cannot write, since it
    # needs an LFN chain - refused outright rather than silently mangled into SETTIN~1.TOM.
    source = _write(tmp_path, "settings.py", "x = 1\n")
    result = _run("--output", str(tmp_path / "out.img"), f"{source}=settings.toml")
    assert result.returncode != 0
    assert "8.3" in result.stderr


def test_read_needs_a_base_image(tmp_path):
    result = _run("--read", "boot_out.txt")
    assert result.returncode != 0
    assert "--base" in result.stderr
