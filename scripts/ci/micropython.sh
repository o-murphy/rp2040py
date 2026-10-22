#!/bin/sh
# Runs the MicroPython firmware checks for one Python runtime and every listed MicroPython version, one after another (the checks that used to be one matrix job each in
# .github/workflows/ci-micropython.yml). The package must already be installed into the environment `uv run --no-sync` will use (`uv sync --python <runtime> --no-dev`),
# and the littlefs images built by that workflow's build-littlefs job must be in the current directory.
#
# usage: scripts/ci/micropython.sh <python_runtime> <version>[:pico_w]...
#   python_runtime  as passed to `uv run --python` (cpython-3.10, pypy-3.10, cpython-3.15)
#   version         a MicroPython release tag; a ":pico_w" suffix adds the Pico W (CYW43439 Wi-Fi) checks for it
set -u
. "$(dirname "$0")/lib.sh"

[ "$#" -ge 2 ] || { echo "usage: $0 <python_runtime> <version>[:pico_w]..." >&2; exit 2; }
runtime="$1"; shift

# `timeout` execs a program, so these are word lists (the runtime has no spaces), not shell functions.
uvrun="uv run --no-sync --no-dev --python $runtime --"
mpy="$uvrun rp2040py micropython"

for spec in "$@"; do
    version="${spec%%:*}"
    pico_w=0; [ "$spec" != "$version" ] && pico_w=1
    n="MicroPython $version"

    # Downloads (and caches) the UF2 outside the timed steps, so a slow micropython.org fetch is not counted against a step's budget.
    ci_soft_step "$n: fetch firmware" 5m $mpy --image "$version" --fetch-fw-only

    ci_step "$n: hello" 5m $mpy --image "$version" --littlefs littlefs.img --expect-text "Hello, MicroPython!"
    ci_step "$n: SPI" 5m $uvrun python tests/micropython_spi_run.py --image "$version" "hello world" "h" "0123456789abcdef0123456789abcdef0123456789abcdef"
    ci_step "$n: flash read/write" 5m $mpy --image "$version" --littlefs littlefs-flash-rw.img --expect-text "FLASH RW OK"

    # docs/records/0089-one-reset-for-every-trigger.md, Phase 1: the guest boots, sees PWRON_RESET, calls machine.reset(), and must come back reporting WDT_RESET - the emulator
    # kept the watchdog's own REASON across the reboot it caused and did not forge a chip-level cause. A guest that never got PWRON first would reset-loop until this times out.
    ci_step "$n: reset cause" 5m $mpy --image "$version" --littlefs littlefs-reset-cause.img --expect-text "RESET CAUSE OK"
    # 0089 Phase 2: the host side of a reset - that ahard_reset() waited for the re-enumeration, then the guest lost its RAM and reports the reset it actually was.
    ci_step "$n: host-initiated hard reset" 5m $uvrun python tests/hard_reset_run.py --image "$version"
    # 0089 Phase 4: the RESET button / RUN pin - the same three assertions through the pin, plus that the core's PC does not move while the button is down.
    ci_step "$n: RESET button" 5m $uvrun python tests/reset_button_run.py --image "$version"
    # 0089 Phase 5: a pad the guest left driving must be released by machine.reset(); also proves the WDSEL model end to end.
    ci_step "$n: chip reset releases pads" 5m $uvrun python tests/chip_reset_run.py --pads --image "$version"
    # 0088 section 1: the CDC control lines can be changed after enumeration, and the console survives the requests.
    ci_step "$n: CDC control lines" 5m $uvrun python tests/control_lines_run.py --image "$version"

    # scripts/ci-common.sh sets `set -e` inside the function, so it runs in its own shell and cannot end this script.
    ci_step "$n: flash dump is deterministic" 5m sh -c '. scripts/ci-common.sh; run_micropython_dump_test "$0" "$1"' "$runtime" "$version"

    if [ "$pico_w" = 1 ]; then
        # The part of the Wi-Fi test that needs no internet - scan, join, the DHCP lease, disconnect - is produced by the emulator alone, so a failure is a regression.
        # The step after it adds real sockets (TCP, DNS, NTP, TLS) and stays soft: a runner's network is not ours.
        ci_step "$n: pico-w WLAN core" 5m sh -c '"$@" > wlan-core.log 2>&1; rc=$?; cat wlan-core.log; [ "$rc" = 0 ] && grep -q "MICROPYTHON CYW43 CORE OK" wlan-core.log' \
            sh $mpy --image "$version" --board pico_w tests/micropython/main-cyw43-core.py
        ci_soft_step "$n: pico-w WLAN (real sockets)" 5m $mpy --image "$version" --board pico_w tests/micropython/main-cyw43.py
    fi
done

ci_finish "MicroPython on $runtime"
