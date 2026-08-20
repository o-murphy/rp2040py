# Live-boot regression for docs/records/0089-one-reset-for-every-trigger.md's §1.3 reset-cause
# table, MicroPython half: a fresh boot must report PWRON_RESET, and a boot that follows the
# guest's own machine.reset() must report WDT_RESET - because machine.reset() on rp2 is
# watchdog_reboot(0, SRAM_END, 0), i.e. the emulator's watchdog TRIGGER path
# (ports/rp2/modmachine.c, read at v1.28.0).
#
# Self-resetting on purpose: it is the only trigger a guest script can pull today. The RUN-pin and
# host-API triggers land on the same BaseDevice.hard_reset(), covered by unit tests in
# tests/test_watchdog_reset.py until 0089's Phases 2/4 give them a caller. If the reset cause were
# reported as PWRON both times, this loops forever and the CI step's own timeout is the failure.
import time

import machine

cause = machine.reset_cause()

if cause == machine.PWRON_RESET:
    machine.reset()

# Printed in a loop rather than once: main.py runs before USB enumerates (the same race real
# hardware has - tests/micropython/main.py loops for exactly this reason), so a single print at
# boot would be long gone by the time any host attaches to the console.
while True:
    if cause == machine.WDT_RESET:
        print("RESET CAUSE OK: reset_cause() ==", cause)
    else:
        print("UNEXPECTED reset_cause:", cause)
    time.sleep(1)
