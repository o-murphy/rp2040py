"""Common UF2-boot lifecycle shared by device wrappers (`MicroPythonDevice`, `KalumaDevice`):
loads a UF2 image, creates the USB-CDC console, and blocks `start()`/`stop()` around actually
running the emulator. Subclasses add their own image-specific extras (littlefs/fat12 loading, a
raw-REPL `exec()` API, ...) on top.
"""

import threading
from os import PathLike

from rp2040py.device.load_flash import load_uf2
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("DEFAULT_TIMEOUT", "BaseDevice", "connect_blocking")

DEFAULT_TIMEOUT = 30.0


def connect_blocking(cdc: USBCDC, simulator: Simulator, mcu: RP2040, timeout: "float | None") -> None:
    connected = threading.Event()
    cdc.on_device_connected = connected.set
    mcu.core.pc = FLASH_START_ADDRESS
    simulator.start_execution()
    if not connected.wait(timeout):
        raise TimeoutError(f"device did not enumerate over USB within {timeout}s")


class BaseDevice:
    """Boots a UF2 image and exposes its USB-CDC console (`.cdc`). Use as a context manager, or
    call `start()`/`stop()` directly for more control over the lifecycle."""

    def __init__(
        self, image: PathLike, *, bootrom_words: "list[int] | None" = None, log_level: LogLevel = LogLevel.ERROR
    ) -> None:
        self.simulator = Simulator()
        self.mcu: RP2040 = self.simulator.rp2040

        if bootrom_words is None:
            from rp2040py.device.bootrom import BOOTROM_B1

            bootrom_words = BOOTROM_B1

        self.mcu.load_bootrom(bootrom_words)
        self.mcu.logger = ConsoleLogger(log_level)
        load_uf2(image, self.mcu)
        self.cdc = USBCDC(self.mcu.usb_ctrl)
        self.mcu.watchdog.on_watchdog_trigger = self._on_watchdog_trigger
        self._started = False

    def _on_watchdog_trigger(self) -> None:
        """A real `machine.reset()`/`machine.bootloader()` (mpremote's `reset`/`bootloader`
        shortcuts) writes the watchdog's TRIGGER bit to force a hardware reset - without this,
        RPWatchdog's default handler just logs a warning and the emulated CPU spins forever
        waiting for a reset that never happens. Mirrors connect_blocking()'s own cold-boot
        sequence (reset then jump straight to flash's entry point), but preserving flash content
        and resetting mcu/cdc in place rather than replacing them - external code (this object's
        own self.cdc) holds a direct reference to mcu.usb_ctrl that must stay valid."""
        self.mcu.reset(preserve_flash=True)
        self.mcu.core.pc = FLASH_START_ADDRESS
        self.cdc.reset()

    def start(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """Boot the device, blocking until it enumerates over USB, or raising `TimeoutError` after
        `timeout` (if given)."""
        if self._started:
            raise RuntimeError("start() already called")
        self._started = True
        connect_blocking(self.cdc, self.simulator, self.mcu, timeout)

    def stop(self) -> None:
        """Halt the emulator. Safe to call even if `start()` was never called."""
        self.simulator.stop()

    def __enter__(self) -> "BaseDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def dump_flash_image(self, filename: PathLike) -> None:
        raise NotImplementedError
