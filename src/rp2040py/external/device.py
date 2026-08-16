"""Component interface for board-level hardware that isn't a memory-mapped peripheral - e.g. the
CYW43439 WiFi chip on a Pico W, wired to the RP2040 purely through GPIO pins (see
docs/records/0028-cyw43-module-layout.md's "Module layout decision" /
docs/records/0029-cyw43-board-composition.md's "Board composition decision"). Mirrors
`peripherals/peripheral.py`'s `Peripheral` `Protocol` - a structural contract, not a base class -
applied outside `peripherals/` since `Peripheral` already means "memory-mapped,
read_uint32/write_uint32", which this isn't. A worked-example write-up of writing your own device
or board lives in docs/reference/external-devices-and-boards.md.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("ExternalDevice", "attach_external_devices")


class ExternalDevice(Protocol):
    def attach(self, rp2040: "RP2040") -> None: ...


def attach_external_devices(rp2040: "RP2040", *devices: ExternalDevice) -> None:
    """Wires each `device` onto `rp2040` (GPIO listeners, etc.) by calling its `attach()`.

    Only safe to call before `Simulator.start_execution()` - what's wired to a board is fixed at
    power-on in this design, not hot-pluggable mid-run (see
    docs/records/0029-cyw43-board-composition.md's "attach()/attach_external_devices() timing"
    section for the race this avoids: GPIOPin's listener set is iterated, unsynchronized, on the
    engine-room thread once execution starts).
    """
    for device in devices:
        device.attach(rp2040)
