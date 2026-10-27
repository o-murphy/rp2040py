"""What the USB *host* side (`usb/cdc.py`: the computer at the other end of the cable) needs of the chip's USB controller, as a protocol.

The controller itself - the chip's USB block, in the pure-Python reference, in the native build or behind a wasm module - lives with the MCU (`peripherals/_usb.py`, `native/_usb.pyx`, `native/core/usb.hpp`) and
imports nothing from here; this module is the host's side of the border. Anything that has these members can be driven by `USBCDC`: the tests' stand-ins (`tests/test_cdc.py`,
`tests/utils/wasm_usb.py`) are exactly that. The controller's other side - a *device* the chip drives when it is the host - is `usb/usb_device.py`'s `USBDevice`, also a protocol.
"""

from collections.abc import Callable
from typing import Any, Protocol

__all__ = ("USBHostPort",)


class USBHostPort(Protocol):
    """The controller as the host sees it: hooks the host installs, the transactions it starts, and the pacing it reads."""

    rp2040: Any  # the chip: the host schedules one alarm of its own on `rp2040.clock`
    read_delay_microseconds: int

    # --- hooks the host installs (the controller calls them) ---------------------------------------------------------
    on_usb_enabled: Callable[[], None] | None
    on_reset_received: Callable[[], None] | None
    on_endpoint_write: Callable[[int, bytes | bytearray], None] | None
    on_endpoint_read: Callable[[int, int], None] | None
    on_reset: Callable[[], None] | None

    # --- transactions the host starts --------------------------------------------------------------------------------
    def send_setup_packet(self, setup_packet: bytes | bytearray) -> None: ...

    def endpoint_read_done(self, endpoint: int, buffer: bytes | bytearray, delay: int | None = None) -> None: ...

    def reset_device(self) -> None: ...

    def reset(self) -> None: ...
