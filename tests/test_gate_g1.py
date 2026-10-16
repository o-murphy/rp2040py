"""Gate G1 of docs/records/0096-cpp-mcu-core.md: the C++ core alone - no Python, no Cython - boots a stock MicroPython and runs a program on it (the harness is `tests/utils/gate_boot.py`).

The guest's `main.py` (a littlefs image next to the firmware) sums the squares of 0..999 and writes the line to UART0; the line arrives, and the host was called for its bytes and for nothing else.
"""

import pytest
from utils import gate_boot
from utils.emscripten import needs_processes
from utils.littlefs import importorskip_littlefs

importorskip_littlefs()

pytestmark = [
    needs_processes,
    pytest.mark.skipif(gate_boot.cxx() is None and not gate_boot.required(), reason="no C++ compiler on PATH"),
]

MAIN_PY = b"""\
import machine

uart = machine.UART(0, 115200, tx=machine.Pin(0), rx=machine.Pin(1))
total = sum(i * i for i in range(1000))
uart.write(("GATE1 %d\\r\\n" % total).encode())
"""
EXPECTED = "GATE1 332833500"  # sum of the squares of 0..999


def test_micropython_boots_on_the_bare_cpp_core_and_prints_over_uart0(tmp_path):
    rom, flash = gate_boot.micropython_images(tmp_path, MAIN_PY)
    done = gate_boot.run(gate_boot.build_harness(tmp_path), rom, flash, EXPECTED)
    # What the chip asked of the world: the bytes of the line it printed, and nothing else - no warning (every register access MicroPython made is implemented), no other callback.
    assert gate_boot.callbacks(done) == [f"callback uart_byte={len(EXPECTED) + 2}"], done.stderr
