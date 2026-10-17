"""Gate G1 on wasm (docs/records/0103-wasm-backend.md, the proof of concept): the same MicroPython and the same `main.py` as `test_gate_g1.py`, but the core is the wasm module built by
`build_wasm.py` (zig, wasm32-wasi reactor) and the host is `wasmhost`. The module imports nothing, so the only things the host does are write the images into its memory, call `rp2040_run`
and read the UART0 bytes back."""

import pytest
from utils import gate_boot, wasm_core
from utils.emscripten import needs_processes
from utils.littlefs import importorskip_littlefs

importorskip_littlefs()
pytest.importorskip("wasmhost")

pytestmark = needs_processes

MAIN_PY = b"""\
import machine

uart = machine.UART(0, 115200, tx=machine.Pin(0), rx=machine.Pin(1))
total = sum(i * i for i in range(1000))
uart.write(("GATE1 %d\\r\\n" % total).encode())
"""
EXPECTED = "GATE1 332833500"  # sum of the squares of 0..999
BUDGET_SLICES = 400  # a slice is up to a million instructions


@pytest.fixture(scope="module")
def wasm_bytes(tmp_path_factory):
    return wasm_core.wasm_bytes(tmp_path_factory.mktemp("wasm"))


def test_micropython_boots_on_the_wasm_core_and_prints_over_uart0(wasm_bytes, tmp_path):
    rom, flash = gate_boot.micropython_images(tmp_path, MAIN_PY)
    _, exports = wasm_core.instantiate(wasm_bytes)

    rom_bytes, flash_bytes = rom.read_bytes(), flash.read_bytes()
    assert len(rom_bytes) <= exports.rp2040_rom_size() and len(flash_bytes) <= exports.rp2040_flash_size()
    wasm_core.load_flash(exports, flash_bytes, rom_bytes)

    for _ in range(BUDGET_SLICES):
        assert exports.rp2040_run() >= 0, "the core faulted"
        if EXPECTED in wasm_core.uart_text(exports).decode("latin-1"):
            break
    else:
        pytest.fail(f"no {EXPECTED!r} after {BUDGET_SLICES} slices")

    # What the chip asked of the world: the bytes of the line, and no warning - every register access MicroPython made is implemented.
    assert exports.rp2040_uart_length() == len(EXPECTED) + 2
    assert exports.rp2040_uart_dropped() == 0
    assert exports.rp2040_warnings() == 0
