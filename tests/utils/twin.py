"""The twin-backend rig's one shared piece (docs/tasks/twin-backend-testing.md): which whole chip a rig builds.

A rig's *reference* is the pure-Python chip (``_rp2040.RP2040``: its own clock, core, pins and blocks) and its *candidate* is the C++ chip (``native/_rp2040.RP2040``). No block is ever moved from one
chip into the other; the only substitution left in an oracle is a **mutant**, a copy of the reference's source with one expression changed, built into a pure chip (a test of the test).
"""

from typing import Any


def new_chip(kind: str) -> Any:
    """``"default"``: the C++ chip; ``"pure"`` and ``"mutant"``: the pure-Python chip."""
    if kind == "default":
        from rp2040py.native._rp2040 import (
            RP2040,  # imported here: a build without the extension never reaches an oracle
        )
    else:
        from rp2040py._rp2040 import RP2040
    return RP2040()
