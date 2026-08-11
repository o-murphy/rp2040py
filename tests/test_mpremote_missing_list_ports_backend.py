"""Regression test for ``_patch_serial_list_ports_missing_backend`` (rp2040py/cli/__init__.py),
which works around pySerial's ``serial.tools.list_ports_posix`` raising ``ImportError`` for
platforms its own ``sys.platform`` dispatch doesn't recognize - Android/Termux confirmed, and
almost certainly iOS/Pythonista too (same platform-string logic, unverified on-device) - since
pySerial checks for ``'linux'``/``'darwin'``/``'cygwin'``/... explicitly and has no ``'android'`` or
``'ios'`` case. Without the patch, ``rp2040py mpremote <anything>`` fails before argument parsing
even starts, because ``mpremote``'s own ``commands.py`` does ``import serial.tools.list_ports``
unconditionally at module load.

This can't be reproduced by actually running on Android/iOS (this sandbox is neither), so the
failure is simulated instead: ``sys.modules["serial.tools.list_ports"] = None`` makes Python's
import machinery raise ``ImportError`` on the next ``import serial.tools.list_ports`` - the same
exception type (if not the same message) pySerial raises for real on an unrecognized platform, and
exactly what the patch's ``except ImportError`` is written to catch. Run in a subprocess, not
in-process: poisoning the real ``serial.tools.list_ports`` cache would leak into every other test
module that imports it afterwards, since pytest runs all test modules in one process.
"""

import subprocess
import sys

import pytest

pytest.importorskip("mpremote", reason="needs the optional mpremote dev dependency")

# Both tests here shell out via [sys.executable, "-c", ...] - on Android's testbed sys.executable
# is "" (there's no standalone python binary; it's embedded in the app), so Popen([""...]) fails
# with PermissionError before the script even runs. Same root cause/fix as
# test_mpremote_integration.py and test_demo_mklittlefs_dump.py.
is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("subprocess-driven CLI tests need sys.executable, which is empty on Android", allow_module_level=True)

_SCRIPT = """
import sys
sys.modules["serial.tools.list_ports"] = None  # simulate pySerial's ImportError on an unrecognized platform

from rp2040py.cli import _patch_serial_list_ports_missing_backend

_patch_serial_list_ports_missing_backend()

import serial.tools.list_ports

assert serial.tools.list_ports.comports() == []
assert list(serial.tools.list_ports.grep("anything")) == []

# The real motivation: mpremote's commands.py does `import serial.tools.list_ports` at module
# load, so this must now succeed too, exactly as it would inside _cmd_mpremote.
from mpremote.main import main  # noqa: F401

print("OK")
"""

_NOOP_SCRIPT = """
from rp2040py.cli import _patch_serial_list_ports_missing_backend

import serial.tools.list_ports as before

_patch_serial_list_ports_missing_backend()

import serial.tools.list_ports as after

assert before is after
print("OK")
"""


def test_patch_serial_list_ports_missing_backend_lets_mpremote_import_cleanly() -> None:
    result = subprocess.run([sys.executable, "-c", _SCRIPT], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_patch_serial_list_ports_missing_backend_is_a_noop_when_list_ports_already_works() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _NOOP_SCRIPT], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
