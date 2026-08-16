"""`Cyw43439` - the `ExternalDevice` (see `external/device.py`) that owns a `GSPIBus` (`bus.py`) and
wires it onto a board's real WL_CLK/WL_D/WL_CS pins, plus a `NatBridge` (`nat.py`, step 4 - docs/
records/0048-cyw43-nat-reflector.md) onto `bus.nat_bridge`. `bus.py`'s F0/F1/F2 decode, SDPCM+ioctl
framing, and scripted scan/join (step 3) are the wire-protocol side of the chip model; `nat.py` is
what answers real outbound Ethernet traffic (`DATA_HEADER` frames) once the guest actually tries to
use the link - MAC address, DHCP lease, gateway ARP, and the TCP reflector itself.
"""

from typing import TYPE_CHECKING

from rp2040py.external.cyw43.bus import GSPIBus
from rp2040py.external.cyw43.nat import NatBridge

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
        self.bus.nat_bridge = NatBridge(rp2040, queue_ethernet_frame=self.bus.queue_rx_ethernet_frame)
