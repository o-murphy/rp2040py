"""The wasm build of the core (docs/records/0103-wasm-backend.md) runs: a hand-assembled program writes to UART0 and the host reads the bytes out of the module's memory.

No firmware, no network, no `ziglang` when the package ships the module - so the same file runs in the wheel tests on every platform (cibuildwheel's `test-command`), Pyodide and Android
included, on whatever engine `wasmhost` finds there.
"""

import pytest
from utils import wasm_core

wasmhost = pytest.importorskip("wasmhost")


@pytest.fixture(scope="module")
def module_bytes(tmp_path_factory):
    return wasm_core.wasm_bytes(tmp_path_factory.mktemp("wasm"))


def test_the_module_imports_nothing(module_bytes):
    module, _ = wasm_core.instantiate(module_bytes)
    assert wasmhost.Module.imports(module) == []


def test_a_program_writes_to_uart0(module_bytes):
    _, exports = wasm_core.instantiate(module_bytes)
    wasm_core.load_flash(exports, wasm_core.tiny_program(b"HI!\n"))
    assert exports.rp2040_run() > 0
    assert wasm_core.uart_text(exports) == b"HI!\n"
    assert exports.rp2040_warnings() == 0  # every register the program touched is implemented
