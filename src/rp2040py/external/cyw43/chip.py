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
from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("Cyw43439",)


class Cyw43439:
    """Implements `ExternalDevice` structurally (see `external/device.py`). `.bus` is the
    `GSPIBus` doing all the actual protocol work - exposed for tests that want to inspect/drive
    it directly rather than only through a real firmware boot."""

    def __init__(self, *, clk: int = 29, data: int = 24, cs: int = 25, power: int = 23) -> None:
        self.bus = GSPIBus()
        self._clk = clk
        self._data = data
        self._cs = cs
        # WL_ON / WL_REG_ON - "gpio pin to power up the cyw43 chip", pico-sdk's own
        # `src/boards/include/boards/pico_w.h`: `CYW43_DEFAULT_PIN_WL_REG_ON 23u` (0027's 3g rule).
        # Modelled since 0089's Phase 5; before that this chip had no power pin at all, so it could
        # not see the one thing a real reset does show it.
        self._power = power

    def attach(self, rp2040: "RP2040") -> None:
        self.bus.attach_gpio(rp2040, clk=self._clk, data=self._data, cs=self._cs)
        self.bus.nat_bridge = NatBridge(rp2040, queue_ethernet_frame=self.bus.queue_rx_ethernet_frame)

        def _power_listener(new_state: GPIOPinState, old_state: GPIOPinState) -> None:
            # Active-high regulator enable: the chip is powered while the RP2040 drives WL_ON high,
            # and loses its rail - i.e. every register, credit and clock flag - the moment it does
            # not. Only the falling edge does anything; coming back up is the driver's own job, and
            # is exactly what it does after a chip reset releases this pad.
            if old_state == GPIOPinState.HIGH and new_state != GPIOPinState.HIGH:
                self.bus.power_off()

        rp2040.gpio[self._power].add_listener(_power_listener)
