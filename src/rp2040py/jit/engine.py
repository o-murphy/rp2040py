"""Phase 0 scaffolding for the basic-block-fusion mini-JIT - see docs/JIT_BACKLOG.md for the full
plan. This module is only imported when RP2040PY_ENABLE_JIT=1 is set (see RP2040.__init__ in
rp2040.py); with the flag unset, `RP2040.jit` stays `None` and this file is never touched.

`JITEngine` here always declines to compile anything - `try_execute()` never returns a result, so
`CortexM0Core.execute_instruction()`'s hook always falls through to normal interpretation. The
point of this phase is proving the *integration points themselves* (the execution-hook check in
cortex_m0_core.py, the write-invalidation callback in rp2040.py) add no measurable overhead and no
behavior change, before any real code generation exists to introduce actual risk.
"""

__all__ = ("JITEngine",)


class JITEngine:
    """Phase 0: a stub. Real hot-loop detection, codegen, and a compiled-block cache land in later
    phases (see docs/JIT_BACKLOG.md) - this class exists only to exercise the two integration
    points (RP2040.jit / the write-invalidation callback) end to end with zero functional risk.
    """

    def try_execute(self, pc: int) -> "int | None":
        """Called from CortexM0Core.execute_instruction() before normal fetch/decode/dispatch.
        Returning None means "no compiled block for this PC, interpret normally" - always the case
        in Phase 0. A real implementation would return the elapsed cycle count on a cache hit,
        matching execute_instruction()'s own return contract.
        """
        return None

    def on_write(self, address: int, length: int) -> None:
        """Called from every RP2040 write path (write_uint8/write_uint16/write_uint32/bus_copy)
        after the write completes. Phase 0 has no cache to invalidate, so this is a no-op - later
        phases evict any compiled block whose address range overlaps [address, address + length).
        """
