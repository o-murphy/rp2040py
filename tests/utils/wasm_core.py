"""The wasm build of the C++ core (docs/records/0103-wasm-backend.md) for the tests: where to get the module, an engine for it, and a program small enough to need neither network nor firmware.

The module is the one in the installed package (`rp2040py/native/wasm/rp2040.wasm`, put there by `setup.py`'s `build_py` - so a wheel under test, on Pyodide or Android, runs the module it ships);
from a checkout without it, `build_wasm.py` builds it (needs `ziglang`). Where neither exists the test is skipped, and fails under RP2040PY_REQUIRE_WASM=1 (CI sets it for the wasm gates).
"""

import importlib.util
import os
import struct
from importlib import resources
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def required() -> bool:
    return os.environ.get("RP2040PY_REQUIRE_WASM") == "1"


def _unavailable(message: str):
    if required():
        pytest.fail(message)
    pytest.skip(message)


def wasm_bytes(directory: Path) -> bytes:
    """The module's bytes: the packaged file, else a fresh build in `directory`."""
    try:
        packaged = resources.files("rp2040py.native.wasm") / "rp2040.wasm"
        if packaged.is_file():
            return packaged.read_bytes()
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    script = ROOT / "build_wasm.py"
    if not script.is_file() or importlib.util.find_spec("ziglang") is None:
        _unavailable("no wasm module in the package, and nothing to build it with (build_wasm.py, ziglang)")
    spec = importlib.util.spec_from_file_location("build_wasm", script)
    build_wasm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_wasm)
    return build_wasm.build(directory / "rp2040.wasm").read_bytes()


def instantiate(wasm: bytes):
    """`(module, exports)` on whatever engine `wasmhost` finds (wasmtime, Node, Bun, JavaScriptCore, or the page's own in Pyodide); no engine is `_unavailable`."""
    wasmhost = pytest.importorskip("wasmhost")
    try:
        module = wasmhost.Module(wasm)
        instance = wasmhost.Instance(module, {})
    except wasmhost.WasmError:
        raise
    except Exception as error:  # noqa: BLE001 - no engine at all
        _unavailable(f"no WebAssembly engine for wasmhost ({type(error).__name__}: {error})")
    instance.exports._initialize()
    return module, instance.exports


def tiny_program(text: bytes) -> bytes:
    """A Thumb program for the flash entry (the core starts at its first byte): take every block out of reset, enable UART0, write `text` to its data register, loop forever.

    Hand-assembled (no toolchain, no firmware): LDR rt,[pc,#imm] (0x4800 | rt << 8 | imm8), MOVS rd,#imm8 (0x2000 | rd << 8 | imm8), STR rt,[rn,#imm5 * 4] (0x6000 | imm5 << 6 | rn << 3 | rt),
    B . (0xE7FE); the three constants are in a literal pool after the code.
    """
    halfwords: list[int] = []
    literals: list[int] = []  # the pool's values, in order
    loads: list[tuple[int, int]] = []  # (index of the LDR halfword, index in the pool)

    def load(rt: int, value: int) -> None:
        loads.append((len(halfwords), len(literals)))
        literals.append(value)
        halfwords.append(0x4800 | rt << 8)

    load(0, 0x4000C000)  # RESETS
    halfwords.append(0x2100)  # MOVS r1,#0
    halfwords.append(0x6001)  # STR r1,[r0,#0]            RESETS.RESET = 0: nothing held in reset
    load(2, 0x40034000)  # UART0
    load(3, 0x301)  # UARTEN | TXE | RXE
    halfwords.append(0x6000 | 12 << 6 | 2 << 3 | 3)  # STR r3,[r2,#0x30]   UARTCR
    for char in text:
        halfwords.append(0x2100 | char)  # MOVS r1,#char
        halfwords.append(0x6000 | 2 << 3 | 1)  # STR r1,[r2,#0]         UARTDR
    halfwords.append(0xE7FE)  # B .
    pool = (len(halfwords) * 2 + 3) & ~3
    for index, slot in loads:
        offset = (pool + 4 * slot - ((index * 2 + 4) & ~3)) // 4
        assert 0 <= offset < 256
        halfwords[index] |= offset
    code = struct.pack(f"<{len(halfwords)}H", *halfwords)
    return code + b"\0" * (pool - len(code)) + struct.pack(f"<{len(literals)}I", *literals)


def load_flash(exports, image: bytes, rom: bytes = b"") -> None:
    """Erases the flash (inside the module: only the image crosses the boundary), writes the images and builds the chip."""
    exports.rp2040_erase_flash()
    if rom:
        exports.memory.write(exports.rp2040_rom(), rom)
    exports.memory.write(exports.rp2040_flash(), image)
    assert exports.rp2040_boot() == 0


def uart_text(exports) -> bytes:
    return bytes(exports.memory.read(exports.rp2040_uart(), exports.rp2040_uart_length()))
