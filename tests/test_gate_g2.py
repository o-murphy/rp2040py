"""Gate G2 of docs/records/0096-cpp-mcu-core.md: a pico-sdk example boots on the bare C++ core and prints over UART0 (the harness is `tests/utils/gate_boot.py`).

pico-examples' `hello_world/serial` (stdio over UART0) is built from source by the Pico SDK job of CI (`make -C hello_world/serial`; nothing is downloaded) into
`~/pico/pico-examples/build/hello_world/serial/hello_serial.uf2`; where it is not built (a developer's machine without the SDK) the test skips.
"""

from pathlib import Path

import pytest
from utils import gate_boot
from utils.emscripten import needs_processes

pytestmark = [needs_processes, pytest.mark.skipif(gate_boot.cxx() is None, reason="no C++ compiler on PATH")]

PICO_SERIAL = Path.home() / "pico" / "pico-examples" / "build" / "hello_world" / "serial" / "hello_serial.uf2"


def test_a_pico_sdk_example_prints_over_uart0_on_the_bare_cpp_core(tmp_path):
    if not PICO_SERIAL.exists():
        pytest.skip(f"{PICO_SERIAL} is not built (the Pico SDK job builds it)")
    rom, flash = gate_boot.write_images(tmp_path, gate_boot.flash_from_uf2(PICO_SERIAL, 2 * 1024 * 1024))
    done = gate_boot.run(gate_boot.build_harness(tmp_path), rom, flash, "Hello, world!")
    reported = gate_boot.callbacks(done)
    # the host was called for the bytes of the lines printed and for nothing else: no warning, no other callback
    assert len(reported) == 1 and reported[0].startswith("callback uart_byte="), done.stderr
