#!/bin/sh
# Boots the Pico SDK "hello world" example of every listed SDK release on one Python runtime, one after another (the checks that used to be one matrix job per (release, runtime) in
# .github/workflows/ci-pico-sdk.yml). The package must already be installed into the environment `uv run --no-sync` will use (`uv sync --python <runtime> --no-dev`).
#
# usage: scripts/ci/pico_sdk.sh <python_runtime> <firmware_dir> <sdk_release>...
#   python_runtime  as passed to `uv run --python` (pypy-3.10, cpython-3.10, cpython-3.15)
#   firmware_dir    holds one pico-firmware-<release>/ per release, each with the hello_usb.uf2 and hello_serial.uf2 that the workflow's build-firmware job made from that SDK's source
set -u
. "$(dirname "$0")/lib.sh"

[ "$#" -ge 3 ] || { echo "usage: $0 <python_runtime> <firmware_dir> <sdk_release>..." >&2; exit 2; }
runtime="$1"; firmware="$2"; shift 2

# `timeout` execs a program, so this is a word list (the runtime has no spaces), not a shell function.
uvrun="uv run --no-sync --no-dev --python $runtime --"

serials=""
for release in "$@"; do
    # CortexM0Core.execute_instruction() is a pure-Python instruction interpreter; CPython is much slower here than V8 JIT-compiling the equivalent JS in rp2040js, hence the generous
    # timeout - and why this also runs under PyPy and CPython 3.15's experimental JIT (see docs/PORTING.md#known-differences-from-rp2040js).
    ci_step "Pico SDK $release: hello world" 5m $uvrun python demo/micropython_run.py --image "$firmware/pico-firmware-$release/hello_usb.uf2" --expect-text "Hello, world!"
    serials="${serials:+$serials:}$firmware/pico-firmware-$release/hello_serial.uf2"
done

# Gate G2 of docs/records/0096-cpp-mcu-core.md: the example that prints over UART0 (hello_world/serial, built from source for every release) boots on the bare C++ core - no Python in the run:
# a C++ harness builds the chip from the images and counts what the chip asks of its host. One runtime is enough: the Python here only prepares the images. One pytest run for all the
# releases, so the harness is compiled once. (`uv run` here syncs the dev group the test's conftest may use; the project itself is already built.)
if [ "$runtime" = "cpython-3.10" ]; then
    ci_step "Gate G2: hello_world/serial on the bare C++ core ($# releases)" 20m \
        env RP2040PY_REQUIRE_PICO_SERIAL=1 RP2040PY_PICO_SERIALS="$serials" \
        uv run --python cpython-3.10 --with pytest --with pytest-xdist -- python -m pytest tests/test_gate_g2.py -q -p no:cacheprovider -n 0 -rs
fi

ci_finish "Pico SDK on $runtime"
