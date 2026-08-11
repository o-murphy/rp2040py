"""Boots a Kaluma (https://kaluma.io/) UF2 image and exposes its USB-CDC console for interactive
use - the `KalumaDevice` equivalent of `MicroPythonDevice`. Unlike MicroPython/CircuitPython,
Kaluma has no raw-REPL-equivalent protocol (its REPL is a plain `>` prompt plus dot-commands, no
Ctrl-A/banner/OK/Ctrl-D framing - see kalumajs.org/docs/repl), so there's no `exec()` API here,
only `astart()`/`start_async()`/`stop()`/`.cdc` (inherited from `BaseDevice`, async-native only -
see that module's own docstring for why).
"""

from os import PathLike

from rp2040py.device.base_device import BaseDevice
from rp2040py.device.load_flash import dump_kaluma_flash_image, load_kaluma_flash_image, load_kaluma_program
from rp2040py.utils.logging import LogLevel

__all__ = ("KalumaDevice",)


class KalumaDevice(BaseDevice):
    """Boots a Kaluma UF2 image. Use as an async context manager (`async with`), or call
    `astart()`/`stop()` directly.

    `program`, if given, stages a local `.js` file into Kaluma's "user program" flash region -
    the same one `kaluma flash <file>` writes to on real hardware - which Kaluma auto-executes on
    every boot (unlike `littlefs`, which is just `require('fs')`-accessible storage with no
    auto-run semantics of its own).
    """

    def __init__(
        self,
        image: PathLike,
        *,
        littlefs: "PathLike | None" = None,
        program: "PathLike | None" = None,
        bootrom_words: "list[int] | None" = None,
        log_level: LogLevel = LogLevel.ERROR,
    ) -> None:
        super().__init__(image, bootrom_words=bootrom_words, log_level=log_level)
        if littlefs is not None:
            load_kaluma_flash_image(littlefs, self.mcu)
        if program is not None:
            load_kaluma_program(program, self.mcu)

    def dump_flash_image(self, filename: PathLike) -> None:
        """Dump the device's flash filesystem image (LittleFS for Kaluma)
        to a local file."""
        dump_kaluma_flash_image(filename, self.mcu)
