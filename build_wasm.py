"""Builds the wasm module of docs/records/0103-wasm-backend.md: the C++ core behind the C ABI of `src/rp2040py/native/wasm/rp2040_wasm.cpp`, compiled by the `ziglang` package (zig's `c++`)
for wasm32-wasi as a *reactor*, so the module imports nothing (no `wasi_snapshot_preview1`): its buffers are in its own linear memory.

    python build_wasm.py [OUTPUT.wasm]

Runs from setup.py's `build_py` on every build (wheel, sdist -> wheel, editable `uv sync`) and by hand. The compiler is `python -m ziglang c++` (`ziglang` is in `[build-system].requires`, so a plain
`pip install` needs nothing preinstalled); RP2040PY_WASM_CXX overrides it (e.g. "clang++ --sysroot=/opt/wasi-sdk/share/wasi-sysroot"). The result must import nothing - checked here - so any bare
WebAssembly host can instantiate it with an empty import object.

The flags are the core's own (`tests/utils/gate_boot.py`: C++17, no exceptions, no RTTI, no FMA contraction) plus the link of a reactor with unused code dropped and the symbols stripped.
"""

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "src" / "rp2040py" / "native" / "core"
SOURCE = ROOT / "src" / "rp2040py" / "native" / "wasm" / "rp2040_wasm.cpp"
DEFAULT_OUTPUT = ROOT / "src" / "rp2040py" / "native" / "wasm" / "rp2040.wasm"

FLAGS = [
    "-target",
    "wasm32-wasi",
    "-mexec-model=reactor",
    "-O2",
    "-std=c++17",
    "-fno-exceptions",
    "-fno-rtti",
    "-ffp-contract=off",
    "-Wall",
    "-Wextra",
    "-Werror",
]
LINK_FLAGS = ["-Wl,--no-entry", "-Wl,--gc-sections", "-s"]


def _compiler() -> "list[str]":
    override = os.environ.get("RP2040PY_WASM_CXX")
    if override:
        return shlex.split(override)
    if importlib.util.find_spec("ziglang") is None:
        raise SystemExit(
            "ziglang is not installed in this environment (builds get it from [build-system].requires). "
            "Run by hand as `uv run --with ziglang python build_wasm.py`, or set RP2040PY_WASM_CXX."
        )
    return [sys.executable, "-m", "ziglang", "c++"]


def _leb128(data: bytes, pos: int) -> "tuple[int, int]":
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def wasm_import_count(data: bytes) -> int:
    """The number of entries in a module's import section (0 when it has none)."""
    if data[:4] != b"\0asm":
        raise ValueError("not a WebAssembly module")
    pos = 8
    while pos < len(data):
        section_id = data[pos]
        size, pos = _leb128(data, pos + 1)
        if section_id == 2:
            return _leb128(data, pos)[0]
        pos += size
    return 0


def build(output: Path = DEFAULT_OUTPUT) -> Path:
    """Compiles the module to `output`, checks that it imports nothing, and returns it. Raises `subprocess.CalledProcessError` (with the compiler's messages) when the build fails."""
    command = [*_compiler(), *FLAGS, f"-I{CORE}", str(SOURCE), "-o", str(output), *LINK_FLAGS]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
    imports = wasm_import_count(output.read_bytes())
    if imports:
        raise SystemExit(f"{output} imports {imports} item(s); it must import nothing")
    return output


if __name__ == "__main__":
    target = build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    print(f"{target} ({target.stat().st_size} bytes)")
