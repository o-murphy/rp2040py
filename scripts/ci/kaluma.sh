#!/bin/sh
# Runs the Kaluma firmware checks for one Python runtime and every listed Kaluma version, one after another (the checks that used to be one matrix job each in
# .github/workflows/ci-kaluma.yml). The package must already be installed into the environment `uv run --no-sync` will use (`uv sync --python <runtime> --no-dev`).
#
# usage: scripts/ci/kaluma.sh <python_runtime> <littlefs_image> <kaluma_version>...
#   python_runtime  as passed to `uv run --python` (cpython-3.10, pypy-3.10, cpython-3.15)
#   littlefs_image  the empty Kaluma-sized filesystem made by `rp2040py mklittlefs --target kaluma`
set -u
. "$(dirname "$0")/lib.sh"

[ "$#" -ge 3 ] || { echo "usage: $0 <python_runtime> <littlefs_image> <kaluma_version>..." >&2; exit 2; }
runtime="$1"; image="$2"; shift 2

# `timeout` execs a program, so this is a word list (the runtime has no spaces), not a shell function.
kaluma="uv run --no-sync --no-dev --python $runtime -- rp2040py kaluma"

for version in "$@"; do
    # Downloads (and caches) the UF2 outside the timed steps, so a slow kaluma-project/kaluma release fetch is not counted against a step's budget.
    ci_soft_step "Kaluma $version: fetch firmware" 5m $kaluma --image "$version" --fetch-fw-only

    # Real code execution: tests/kaluma/index.js is staged into the "user program" flash region and auto-run on boot. Matches the script's own output, not Kaluma's racy boot banner.
    ci_step "Kaluma $version: hello" 5m $kaluma --image "$version" tests/kaluma/index.js --expect-text "Hello, Kaluma!"

    # WiFi through the emulated CYW43439 + NAT bridge (docs/records/0027-cyw43-wifi.md, 0048-cyw43-nat-reflector.md). One runtime only: it buys firmware/stack coverage, not interpreter coverage.
    # The part that needs no internet (scan, join) is a hard check; the real TCP connection to 1.1.1.1:80 is soft - a runner network hiccup is not an emulator regression.
    if [ "$runtime" = "cpython-3.10" ]; then
        ci_step "Kaluma $version: pico-w WLAN core" 5m $kaluma --image "$version" --board pico_w tests/kaluma/main-cyw43.js --expect-text "KALUMA CYW43 CORE OK"
        ci_soft_step "Kaluma $version: pico-w WLAN (real TCP)" 5m $kaluma --image "$version" --board pico_w tests/kaluma/main-cyw43.js --expect-text "KALUMA CYW43 OK"
    fi

    # SSI flash-write path (docs/BACKLOG.md): a littlefs image in Kaluma's own board.js layout, plus tests/kaluma/index-flash-rw.js as the auto-run program.
    ci_step "Kaluma $version: flash read/write" 5m $kaluma --image "$version" --littlefs "$image" tests/kaluma/index-flash-rw.js --expect-text "FLASH RW OK"
done

ci_finish "Kaluma on $runtime"
