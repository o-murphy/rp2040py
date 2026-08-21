# CircuitPython counterpart of tests/micropython/main-reset-cause.py, for the same §1.3 table.
# Only the power-on row is checkable from a guest script here: this runs over the raw REPL (the
# `rp2040py micropython --circuitpython <file>` path), and a microcontroller.reset() would drop USB
# mid-exec with nothing on the host side waiting for the re-enumeration until 0089's Phase 2.
import microcontroller  # type: ignore[import-not-found]

reason = microcontroller.cpu.reset_reason
print("reset_reason:", reason)
assert reason is microcontroller.ResetReason.POWER_ON, reason
print("RESET CAUSE OK")
