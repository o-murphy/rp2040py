"""Basic-block-fusion mini-JIT. See docs/JIT_BACKLOG.md for the phased plan.

Only imported when RP2040PY_ENABLE_JIT=1 is set - see RP2040.__init__ in rp2040.py.
"""

from rp2040py.jit.engine import JITEngine

__all__ = ("JITEngine",)
