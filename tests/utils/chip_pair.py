"""The two `RP2040` implementations side by side, for parity tests (docs/records/0096-cpp-mcu-core.md).

Skips the importing test module when the native extension is not built. `make_chip(PurePython | Native, ...)`
returns a chip with a silent logger, an optional boot ROM image, and the USB-DPRAM hook recorded in
`chip.hook_calls`.
"""

import pytest

pytest.importorskip("rp2040py.native._rp2040", reason="the native extension is not built")

import rp2040py._cortex_m0_core as pure_core
import rp2040py._rp2040 as pure_rp2040
from rp2040py._rp2040 import RP2040 as PurePython
from rp2040py.native._rp2040 import RP2040 as Native

__all__ = ("Native", "PurePython", "make_chip")


class _Quiet:
    """A logger that says nothing: the two buses warn identically on unmapped/unaligned accesses."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def make_chip(cls, bootrom_words=None):
    if cls is PurePython:
        # The pure chip builds its CPU through the facade, which hands back the *native* core whenever
        # the extension is installed - and a native core only accepts a native chip. Give this one the
        # pure core, as the RP2040PY_SKIP_CYTHON=1 build would.
        saved, pure_rp2040.CortexM0Core = pure_rp2040.CortexM0Core, pure_core.CortexM0Core
        try:
            chip = cls()
        finally:
            pure_rp2040.CortexM0Core = saved
    else:
        chip = cls()
    chip.logger = _Quiet()
    if bootrom_words is not None:
        chip.load_bootrom(bootrom_words)
    chip.hook_calls = []
    chip.usb_ctrl.dpram_updated = lambda offset, value: chip.hook_calls.append((offset, value))
    return chip
