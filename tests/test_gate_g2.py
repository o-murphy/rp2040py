"""Gate G2 of docs/records/0096-cpp-mcu-core.md: a pico-sdk example boots on the bare C++ core and prints over UART0 (the harness is `tests/utils/gate_boot.py`).

pico-examples' `hello_world/serial` (stdio over UART0) is built from source by the Pico SDK job of CI (`make -C hello_world/serial`; nothing is downloaded) into
`~/pico/pico-examples/build/hello_world/serial/hello_serial.uf2`; where it is not built (a developer's machine without the SDK) the test skips.
"""

import os
from pathlib import Path

import pytest
from utils import gate_boot
from utils.emscripten import needs_processes

pytestmark = [
    needs_processes,
    pytest.mark.skipif(gate_boot.cxx() is None and not gate_boot.required(), reason="no C++ compiler on PATH"),
]

PICO_SERIAL = Path.home() / "pico" / "pico-examples" / "build" / "hello_world" / "serial" / "hello_serial.uf2"


def _examples() -> "list[Path]":
    """The images to boot: `RP2040PY_PICO_SERIALS` (paths separated by os.pathsep) when the Pico SDK job sets it - one hello_serial.uf2 per SDK release, each built by its own job and fetched as an artifact -
    otherwise the one image a developer's own SDK build leaves in ~/pico."""
    listed = os.environ.get("RP2040PY_PICO_SERIALS")
    return [Path(p) for p in listed.split(os.pathsep) if p] if listed else [PICO_SERIAL]


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """The C++ harness, compiled once for every image of the module."""
    return gate_boot.build_harness(tmp_path_factory.mktemp("harness"))


@pytest.mark.parametrize("uf2", _examples(), ids=lambda path: path.parent.name)
def test_a_pico_sdk_example_prints_over_uart0_on_the_bare_cpp_core(uf2, request, tmp_path):
    if not uf2.exists():
        # the Pico SDK job builds the example, so there it must exist
        if os.environ.get("RP2040PY_REQUIRE_PICO_SERIAL") == "1":
            pytest.fail(f"{uf2} is not built")
        pytest.skip(f"{uf2} is not built (the Pico SDK job builds it)")
    # only now: a machine without the SDK skips above without compiling anything
    harness = request.getfixturevalue("harness")
    rom, flash = gate_boot.write_images(tmp_path, gate_boot.flash_from_uf2(uf2, 2 * 1024 * 1024))
    done = gate_boot.run(harness, rom, flash, "Hello, world!")
    reported = gate_boot.callbacks(done)
    # the host was called for the bytes of the lines printed and for nothing else: no warning, no other callback
    assert len(reported) == 1 and reported[0].startswith("callback uart_byte="), done.stderr
