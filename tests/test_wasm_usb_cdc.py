"""MicroPython's USB CDC REPL on the wasm core (docs/records/0103-wasm-backend.md, 2026-10-20): the module answers the host's end of the USB wire itself and `usb/cdc.py` - unchanged, behind
`tests/utils/wasm_usb.py` - is the host. Held here: the device enumerates, the REPL answers a command, a stream longer than a packet comes back complete, and the module imported nothing,
dropped no event and overflowed no queue doing it.

The USB side needs the bus to tell the module about writes to the DPRAM (`BusHost.dpram_written`): without it the firmware's armed buffer never starts a transfer and the device stays silent
after its first SETUP; this test is what fails then."""

import pytest
from utils import gate_boot, wasm_core
from utils.emscripten import needs_processes
from utils.littlefs import importorskip_littlefs
from utils.wasm_usb import WasmCdc

importorskip_littlefs()
pytest.importorskip("wasmhost")

pytestmark = needs_processes

ENUMERATION_SLICES = 400
REPL_SLICES = 4000


@pytest.fixture(scope="module")
def wasm_bytes(tmp_path_factory):
    return wasm_core.wasm_bytes(tmp_path_factory.mktemp("wasm"))


@pytest.fixture
def console(wasm_bytes, tmp_path):
    rom, flash = gate_boot.micropython_images(tmp_path, b"pass\n")
    _, exports = wasm_core.instantiate(wasm_bytes)
    wasm_core.load_flash(exports, flash.read_bytes(), rom.read_bytes())
    cdc = WasmCdc(exports)
    assert cdc.run(ENUMERATION_SLICES, lambda: cdc.connected), "the device did not enumerate"
    return cdc


def test_the_repl_answers_over_usb_cdc(console):
    console.send(b"print(6*7)\r\n")
    assert console.run(REPL_SLICES, lambda: b"42\r\n>>> " in console.received), bytes(console.received)
    assert b"MicroPython" in console.received  # the banner comes with the first input


def test_a_stream_longer_than_a_packet_comes_back_complete(console):
    console.send(b"for i in range(60): print('line', i, 'x' * 40)\r\n\r\n")
    assert console.run(
        REPL_SLICES, lambda: b"line 59 " in console.received and console.received.rstrip().endswith(b">>>")
    )
    for i in range(60):
        assert b"line %d " % i in console.received
    exports = console.exports
    assert exports.rp2040_usb_events_dropped() == 0
    assert exports.rp2040_usb_overflow() == 0
    assert exports.rp2040_warnings() == 0
