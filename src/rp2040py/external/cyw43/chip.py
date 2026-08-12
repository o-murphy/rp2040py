"""`Cyw43439` - the `ExternalDevice` (see `external/device.py`) that owns a `GSPIBus` (`bus.py`) and
wires it onto a board's real WL_CLK/WL_D/WL_CS pins. Currently just that: `bus.py`'s F0/F1 decode
is the entire chip model so far (docs/CYW43_WIFI_BACKLOG.md step 3b/3c/3d) - no SDPCM/ioctl/firmware
acceptance yet (step 3e/3f/3g), so real firmware's init handshake can get past the F0 test-register
poll and the ALP/HT/KSO clock handshake, but nothing beyond that.
"""

from typing import TYPE_CHECKING

from rp2040py.external.cyw43.bus import GSPIBus

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("Cyw43439",)


class Cyw43439:
    """Implements `ExternalDevice` structurally (see `external/device.py`). `.bus` is the
    `GSPIBus` doing all the actual protocol work - exposed for tests that want to inspect/drive
    it directly rather than only through a real firmware boot."""

    def __init__(self, *, clk: int = 29, data: int = 24, cs: int = 25) -> None:
        self.bus = GSPIBus()
        self._clk = clk
        self._data = data
        self._cs = cs

    def attach(self, rp2040: "RP2040") -> None:
        self.bus.attach_gpio(rp2040, clk=self._clk, data=self._data, cs=self._cs)
