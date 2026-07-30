"""A programmatic API for driving a MicroPython/CircuitPython firmware image in the emulator -
the library equivalent of the ``rp2040py micropython`` CLI subcommand, for embedding in other
Python programs rather than a terminal (e.g. a test runner, or a Thonny-style tool that wants to
evaluate code against a device and read back the result).

.. code-block:: python

    from rp2040py.device import MicroPythonDevice

    with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
        stdout, stderr = device.exec("print(1 + 1)")
        assert stdout == b"2\\r\\n"
"""

import threading

from rp2040py.device.bootrom import BOOTROM_B1
from rp2040py.device.load_flash import load_circuitpython_flash_image, load_micropython_flash_image, load_uf2
from rp2040py.device.raw_repl import RawReplError, RawReplRunner
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("MicroPythonDevice",)

DEFAULT_TIMEOUT = 30.0


def _exec_via_raw_repl(cdc: USBCDC, source: bytes, timeout: "float | None") -> "tuple[bytes, bytes]":
    runner = RawReplRunner(source, cdc.send_serial_byte)
    done = threading.Event()
    errors: list[RawReplError] = []

    def _on_serial_data(data: bytes | bytearray) -> None:
        try:
            runner.feed(data)
        except RawReplError as exc:
            errors.append(exc)
            done.set()
            return
        if runner.result is not None:
            done.set()

    # This callback (and thus runner.feed()) runs on the Simulator's worker thread, not the
    # thread calling exec() - the threading.Event is what makes exec() a normal blocking call
    # for that caller, with no os._exit() tricks needed (contrast the CLI's interactive REPL
    # path, which really does need those - see cli/__init__.py).
    cdc.on_serial_data = _on_serial_data
    runner.start()

    if not done.wait(timeout):
        raise TimeoutError(f"raw-REPL exec did not complete within {timeout}s")
    if errors:
        raise errors[0]
    assert runner.result is not None
    return runner.result


class MicroPythonDevice:
    """Boots a MicroPython/CircuitPython UF2 image on a daemon thread and lets you run code on
    it programmatically via the raw-REPL protocol (the same one `mpremote run`/`pyboard.py` and
    Thonny's "Run" use over a real serial port).

    Use as a context manager, or call `start()`/`stop()` directly for more control over the
    lifecycle. `exec()`/`exec_file()` block the calling thread until the device finishes running
    the given code (or `timeout` elapses), returning its `(stdout, stderr)`.
    """

    def __init__(
        self,
        image: str,
        *,
        littlefs: "str | None" = None,
        fat12: "str | None" = None,
        circuitpython: bool = False,
    ) -> None:
        self.simulator = Simulator()
        self.mcu: RP2040 = self.simulator.rp2040
        self.mcu.load_bootrom(BOOTROM_B1)
        self.mcu.logger = ConsoleLogger(LogLevel.ERROR)
        load_uf2(image, self.mcu)
        if littlefs is not None:
            load_micropython_flash_image(littlefs, self.mcu)
        if fat12 is not None:
            load_circuitpython_flash_image(fat12, self.mcu)
        self.circuitpython = circuitpython
        self.cdc = USBCDC(self.mcu.usb_ctrl)
        self._thread: threading.Thread | None = None

    def start(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """Boot the device on a daemon thread; blocks until it enumerates over USB."""
        if self._thread is not None:
            raise RuntimeError("start() already called")

        connected = threading.Event()
        self.cdc.on_device_connected = connected.set

        self.mcu.core.pc = FLASH_START_ADDRESS
        self._thread = threading.Thread(target=self.simulator.execute, daemon=True)
        self._thread.start()

        if not connected.wait(timeout):
            raise TimeoutError(f"device did not enumerate over USB within {timeout}s")

    def stop(self) -> None:
        """Halt the emulator. Safe to call even if `start()` was never called."""
        self.simulator.stop()

    def exec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Run `code` on the device via the raw-REPL protocol and return its `(stdout, stderr)`.

        Interrupts anything already running on the device first (e.g. an auto-run `main.py` from
        a littlefs image), same as `mpremote run`/`pyboard.py` do.
        """
        if self._thread is None:
            raise RuntimeError("call start() (or enter as a context manager) before exec()")
        return _exec_via_raw_repl(self.cdc, code.encode(), timeout)

    def exec_file(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        with open(path) as f:
            return self.exec(f.read(), timeout=timeout)

    def __enter__(self) -> "MicroPythonDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
