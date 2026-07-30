"""Regression coverage for CortexM0Core's opcode dispatch table (see cortex_m0_core.py and
docs/PORTING.md#known-differences-from-rp2040js for the full design rationale).

execute_instruction() decodes via a precomputed 65536-entry table for opcodes outside
0xF000-0xF7FF, and a small hand-written resolver (_resolve_wide) for opcodes inside it, because
resolving those 2048 opcodes correctly can depend on opcode2 as well as opcode. That split is only
sound as long as no *other* (opcode-only) instruction pattern ever matches inside 0xF000-0xF7FF -
_build_dispatch_table() already asserts this at import time, but a dedicated test makes the
invariant discoverable (surfaces in a stack trace here, not as "import of rp2040py mysteriously
raises AssertionError") and documents *why* it must hold for whoever adds the next instruction.
"""

from rp2040py.cortex_m0_core import _DISPATCH_TABLE, WIDE_RANGE_END, WIDE_RANGE_START


def test_wide_range_has_no_table_entries():
    """0xF000-0xF7FF must stay exclusively owned by _resolve_wide()'s opcode2-dependent checks.

    If this fails, a newly added instruction's condition matches an opcode in this range - update
    CortexM0Core._resolve_wide() to also handle it, in the correct priority position relative to
    the existing checks there (BL, DMB, DSB, ISB, MRS, MSR, UDF T2).
    """
    for opcode in range(WIDE_RANGE_START, WIDE_RANGE_END):
        assert _DISPATCH_TABLE[opcode] is None, (
            f"opcode 0x{opcode:04x} unexpectedly has a table entry; it should be handled by "
            "CortexM0Core._resolve_wide() instead"
        )


def test_dispatch_table_is_populated():
    """Sanity check that the table isn't degenerate (e.g. broken by a future refactor that leaves
    it empty but still imports cleanly)."""
    populated = sum(1 for handler in _DISPATCH_TABLE if handler is not None)
    assert populated > 50
