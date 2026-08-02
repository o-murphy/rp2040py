"""Boots a Kaluma (https://kaluma.io/) UF2 image and exposes its USB-CDC console for interactive
use - the `KalumaDevice` equivalent of `MicroPythonDevice`. Unlike MicroPython/CircuitPython,
Kaluma has no raw-REPL-equivalent protocol (its REPL is a plain `>` prompt plus dot-commands, no
Ctrl-A/banner/OK/Ctrl-D framing - see kalumajs.org/docs/repl), so there's no `exec()` API here,
only `start()`/`stop()`/`.cdc` (inherited from `BaseDevice`).
"""

from rp2040py.device.base_device import BaseDevice
from rp2040py.device.load_flash import load_kaluma_flash_image

__all__ = ("KalumaDevice",)


class KalumaDevice(BaseDevice):
    """Boots a Kaluma UF2 image. Use as a context manager, or call `start()`/`stop()` directly."""

    def __init__(self, image: str, *, littlefs: "str | None" = None) -> None:
        super().__init__(image)
        if littlefs is not None:
            load_kaluma_flash_image(littlefs, self.mcu)
