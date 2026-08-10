"""Tests for demo/mklittlefs_dump.py - the `mklittlefs`-without-`littlefs-python` script generator
(see README.md's "Filesystem support" section). Driven via subprocess rather than importing the
module directly: demo/ isn't part of the installed package or on tests/'s import path, and running
it the same way the docs tell users to (`python demo/mklittlefs_dump.py ...`) also exercises its
actual CLI, not just the underlying function."""

import subprocess
import sys
from pathlib import Path

import pytest

# Every test here shells out to a real `python demo/mklittlefs_dump.py` via
# [sys.executable, str(SCRIPT), ...] (see _run() below) - on Android's testbed sys.executable is
# "" (there's no standalone python binary; it's embedded in the app), so Popen([""...]) fails with
# PermissionError before the script even runs. Same root cause/fix as test_mpremote_integration.py.
is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("subprocess-driven CLI tests need sys.executable, which is empty on Android", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "demo" / "mklittlefs_dump.py"


def _run(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )


def test_writes_each_file_under_its_own_basename(tmp_path):
    app = tmp_path / "app.py"
    app.write_bytes(b"import lib\n")
    lib = tmp_path / "lib.py"
    lib.write_bytes(b"VALUE = 42\n")

    result = _run(str(app), str(lib))

    assert result.returncode == 0
    assert "with open('app.py', 'wb') as fp:" in result.stdout
    assert repr(b"import lib\n") in result.stdout
    assert "with open('lib.py', 'wb') as fp:" in result.stdout
    assert repr(b"VALUE = 42\n") in result.stdout
    assert "os.listdir('/')" in result.stdout


def test_main_writes_the_matching_file_as_main_py_instead_of_its_own_basename(tmp_path):
    app = tmp_path / "app.py"
    app.write_bytes(b"print('hi')\n")

    result = _run(str(app), "--main", "app.py")

    assert result.returncode == 0
    assert "with open('main.py', 'wb') as fp:" in result.stdout
    assert "with open('app.py', 'wb') as fp:" not in result.stdout


def test_output_flag_writes_to_a_file_instead_of_stdout(tmp_path):
    app = tmp_path / "app.py"
    app.write_bytes(b"print('hi')\n")
    out = tmp_path / "flash_script.py"

    result = _run(str(app), "--output", str(out))

    assert result.returncode == 0
    assert result.stdout == ""
    assert "with open('app.py', 'wb') as fp:" in out.read_text()


def test_main_not_matching_any_file_errors_out(tmp_path):
    app = tmp_path / "app.py"
    app.write_bytes(b"print('hi')\n")

    result = _run(str(app), "--main", "no_such.py")

    assert result.returncode == 1
    assert "--main" in result.stderr


def test_colliding_destination_names_error_out(tmp_path):
    (tmp_path / "pkg_a").mkdir()
    (tmp_path / "pkg_b").mkdir()
    lib_a = tmp_path / "pkg_a" / "lib.py"
    lib_a.write_bytes(b"a\n")
    lib_b = tmp_path / "pkg_b" / "lib.py"
    lib_b.write_bytes(b"b\n")

    result = _run(str(lib_a), str(lib_b))

    assert result.returncode == 1
    assert "lib.py" in result.stderr
