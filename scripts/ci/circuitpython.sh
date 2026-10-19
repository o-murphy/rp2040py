#!/bin/sh
# Runs the CircuitPython firmware checks for one Python runtime and every listed CircuitPython release, one after another (the checks that used to be one matrix job each in
# .github/workflows/ci-circuitpython.yml). The package must already be installed into the environment `uv run --no-sync` will use (`uv sync --python <runtime> --no-dev`).
#
# usage: scripts/ci/circuitpython.sh <python_runtime> <tag>[:flag,flag]...
#   python_runtime  as passed to `uv run --python` (cpython-3.10)
#   tag             a CircuitPython release; flags (comma-separated) switch on more checks for it:
#                     hard_reset  the chip is reset and must re-enumerate (8.0.2 never comes back over USB - docs/records/0093-circuitpython-802-warm-boot-hang.md)
#                     wlan        the Pico W Wi-Fi checks (the CYW43439 through the NAT bridge)
set -u
. "$(dirname "$0")/lib.sh"

[ "$#" -ge 2 ] || { echo "usage: $0 <python_runtime> <tag>[:flag,flag]..." >&2; exit 2; }
runtime="$1"; shift

# `timeout` execs a program, so these are word lists (the runtime has no spaces), not shell functions.
uvrun="uv run --no-sync --no-dev --python $runtime --"
cpy="$uvrun rp2040py micropython --circuitpython"

has() { case "$flags" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

for spec in "$@"; do
    tag="${spec%%:*}"
    rest="${spec#"$tag"}"; flags=",${rest#:},"   # "8.0.2" -> ",," ; "9.2.9:hard_reset,wlan" -> ",hard_reset,wlan,"
    n="CircuitPython $tag"

    # Pull (and cache) the UF2 outside the timed steps; a flaky download is not a failure of its own - the step after it gives a clearer signal.
    ci_soft_step "$n: fetch firmware" 5m $cpy --image "$tag" --fetch-fw-only

    # Boots to the friendly REPL and exits as soon as the banner appears (16 s for 8.0.2, 9 s for 10.2.1 on a developer machine).
    ci_step "$n: boots" 5m $cpy --image "$tag" --expect-text "Adafruit CircuitPython"
    # The CircuitPython half of 0089's Phase 1 reset-cause check. Power-on only (the raw REPL cannot wait for a re-enumeration): CHIP_RESET.HAD_POR with a clear WATCHDOG.REASON
    # is what makes a fresh boot report POWER_ON rather than a stale SOFTWARE/WATCHDOG.
    ci_step "$n: reset reason" 5m $cpy --image "$tag" tests/circuitpython/main-reset-cause.py

    if has hard_reset; then
        # 0089 Phase 2: a host-initiated hard reset re-enumerates, leaves the raw REPL usable, wipes the guest's RAM and reports RESET_PIN.
        ci_step "$n: host-initiated hard reset" 10m $uvrun python tests/hard_reset_run.py --circuitpython --image "$tag"
        # 0089 Phase 4: the RESET button - the sharper of the two live checks, gated on the same flag for the same reason.
        ci_step "$n: RESET button" 10m $uvrun python tests/reset_button_run.py --circuitpython --image "$tag"
        # 0089 Phase 5: Wi-Fi must still work after a hard reset (the emulated CYW43439 sees WL_ON and drops the previous session's registers).
        if has wlan; then
            ci_step "$n: CYW43 comes back after a chip reset" 10m $uvrun python tests/chip_reset_run.py --cyw43 --image "$tag"
        fi
    fi

    # 0088 section 1: CircuitPython exposes DTR as guest state (`supervisor.runtime.serial_connected`), so the guest samples it while the host drops the line. Nothing resets the chip.
    ci_step "$n: CDC control lines" 10m $uvrun python tests/control_lines_run.py --circuitpython --image "$tag"

    if has wlan; then
        # The part of the Wi-Fi test that needs no internet - scan, join, the DHCP lease - is produced by the emulator alone, so a failure is a regression.
        # The step after it adds real TCP / DNS through the NAT bridge and stays soft: a runner's network is not ours.
        ci_step "$n: pico-w WLAN core" 5m sh -c '"$@" > wlan.log 2>&1; rc=$?; cat wlan.log; [ "$rc" = 0 ] && grep -q "CIRCUITPYTHON CYW43 CORE OK" wlan.log' \
            sh $cpy --image "$tag" --board pico_w tests/circuitpython/main-cyw43.py
        ci_soft_step "$n: pico-w WLAN (real sockets)" 5m $cpy --image "$tag" --board pico_w tests/circuitpython/main-cyw43.py
    fi
done

ci_finish "CircuitPython on $runtime"
