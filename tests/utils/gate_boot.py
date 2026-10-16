"""The harness of the gates G1 and G2 of docs/records/0096-cpp-mcu-core.md: `tests/cpp/gate_boot.cpp` compiled with the core's own flags, the images it takes, and a run.

The harness is a C++ program that builds an `Mcu` from the world's buffers (the boot ROM, the flash image, the SRAM, the USB DPRAM) and a host that only counts warnings and collects the UART0
bytes; no Python and no Cython is in it. Here are the pieces the gate tests share.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path

from rp2040py.device.bootrom import BOOTROM_B1
from rp2040py.device.load_flash import UF2_BLOCK_SIZE, decode_block
from rp2040py.memory_map import FLASH_START_ADDRESS

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src" / "rp2040py" / "native" / "core"
FLAGS = ["-std=c++17", "-O2", "-fno-exceptions", "-fno-rtti", "-ffp-contract=off", "-Wall", "-Wextra", "-Werror"]
if platform.machine().lower() in ("i386", "i686"):
    FLAGS += ["-msse2", "-mfpmath=sse"]  # same reason as setup.py


# Under AddressSanitizer (the CI job exports these for the extension build) the harness is built with it as well, so the bare core is checked by the sanitizer too.
if "-fsanitize=address" in os.environ.get("CXXFLAGS", ""):
    FLAGS += ["-fsanitize=address", "-fno-omit-frame-pointer", "-g"]


def required() -> bool:
    """CI sets RP2040PY_REQUIRE_NATIVE=1: a gate whose prerequisite is missing is then a failure, never a skip (a skipped gate tests nothing and looks green)."""
    return os.environ.get("RP2040PY_REQUIRE_NATIVE") == "1"


def cxx() -> "str | None":
    for name in ("c++", "g++", "clang++"):
        if shutil.which(name):
            return name
    return None


def build_harness(directory: Path) -> Path:
    exe = directory / "gate_boot"
    build = subprocess.run(
        [cxx(), *FLAGS, f"-I{CORE}", str(ROOT / "tests" / "cpp" / "gate_boot.cpp"), "-o", str(exe)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert build.returncode == 0, build.stderr
    return exe


def flash_from_uf2(uf2: Path, size: int) -> bytearray:
    flash = bytearray(b"\xff" * size)
    data = uf2.read_bytes()
    for offset in range(0, len(data) - UF2_BLOCK_SIZE + 1, UF2_BLOCK_SIZE):
        block = decode_block(data[offset : offset + UF2_BLOCK_SIZE])
        start = block.flash_address - FLASH_START_ADDRESS
        flash[start : start + len(block.payload)] = block.payload
    return flash


def write_images(directory: Path, flash: bytearray) -> "tuple[Path, Path]":
    rom = bytearray()
    for word in BOOTROM_B1:
        rom += int(word).to_bytes(4, "little")
    (directory / "flash.bin").write_bytes(bytes(flash))
    (directory / "rom.bin").write_bytes(bytes(rom))
    return directory / "rom.bin", directory / "flash.bin"


def run(exe: Path, rom: Path, flash: Path, expected: str) -> "subprocess.CompletedProcess[str]":
    """Runs the harness until `expected` has arrived over UART0 (or 400 million instructions have been spent) and checks that it did."""
    done = subprocess.run(
        [str(exe), str(rom), str(flash), "400", expected], capture_output=True, text=True, check=False, timeout=600
    )
    assert expected in done.stdout, done.stdout + done.stderr
    assert done.returncode == 0, done.stdout + done.stderr
    return done


def callbacks(done: "subprocess.CompletedProcess[str]") -> "list[str]":
    """What the chip asked of the world, one line per kind of callback (the harness reports them on stderr)."""
    return [line for line in done.stderr.splitlines() if line.startswith("callback ")]
