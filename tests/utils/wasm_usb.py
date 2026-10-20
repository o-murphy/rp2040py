"""The host end of the USB wire for the wasm core (docs/records/0103-wasm-backend.md, 2026-10-20).

The module answers the wire's callbacks itself (queues in its own memory) and hands the host an event stream; `usb/cdc.py` - the USB CDC host, an external device written in Python - is used
as it is, behind `WasmUsb`, which has the methods and attributes of `RPUSBController` that `USBCDC` touches and turns the module's events into the callbacks `USBCDC` installs. The host's
answers reach the chip when a slice of instructions ends, not in the same simulated microsecond as with the Cython controller.
"""

from collections.abc import Callable
from typing import Any

from rp2040py.usb.cdc import USBCDC

EVENT_ENABLED, EVENT_RESET_RECEIVED, EVENT_READ_REQUEST, EVENT_IN_DATA = 1, 2, 3, 4


class _Alarm:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.pending = False

    def schedule(self, nanos: float) -> None:
        self.pending = True  # fires after the next slice

    def cancel(self) -> None:
        self.pending = False


class _Clock:
    def __init__(self) -> None:
        self.alarms: list[_Alarm] = []

    def create_alarm(self, callback: Callable[[], None]) -> _Alarm:
        alarm = _Alarm(callback)
        self.alarms.append(alarm)
        return alarm


class WasmUsb:
    """What `USBCDC` needs of an `RPUSBController`, over the module's exports."""

    def __init__(self, exports: Any) -> None:
        self.exports = exports
        self.rp2040 = self  # `USBCDC` reaches the clock through `usb.rp2040.clock`
        self.clock = _Clock()
        self.read_delay_microseconds = 10
        self.on_usb_enabled: Callable[[], None] | None = None
        self.on_reset_received: Callable[[], None] | None = None
        self.on_endpoint_write: Callable[[int, bytes | bytearray], None] | None = None
        self.on_endpoint_read: Callable[[int, int], None] | None = None
        self.on_reset: Callable[[], None] | None = None
        self._scratch = exports.rp2040_usb_scratch()

    def reset_device(self) -> None:
        assert self.exports.rp2040_usb_reset_device() == 0

    def send_setup_packet(self, packet: "bytes | bytearray") -> None:
        data = bytes(packet)
        self.exports.memory.write(self._scratch, data)
        assert self.exports.rp2040_usb_send_setup(len(data)) == 0

    def endpoint_read_done(self, endpoint: int, buffer: "bytes | bytearray", delay: "float | None" = None) -> None:
        data = bytes(buffer)
        self.exports.memory.write(self._scratch, data)
        assert self.exports.rp2040_usb_endpoint_read_done(endpoint, len(data)) == 0

    def reset(self) -> None:
        pass

    def pump(self) -> None:
        """Delivers what the chip did since the last call (as the callbacks of the Cython controller), then the alarms the host armed."""
        exports = self.exports
        length = exports.rp2040_usb_events_length()
        if length:
            data = bytes(exports.memory.read(exports.rp2040_usb_events(), length))
            exports.rp2040_usb_events_clear()
            position = 0
            while position < len(data):
                kind, endpoint = data[position], data[position + 1]
                size = data[position + 2] | data[position + 3] << 8
                payload = data[position + 4 : position + 4 + size]
                position += 4 + size
                if kind == EVENT_ENABLED and self.on_usb_enabled:
                    self.on_usb_enabled()
                elif kind == EVENT_RESET_RECEIVED and self.on_reset_received:
                    self.on_reset_received()
                elif kind == EVENT_READ_REQUEST and self.on_endpoint_read:
                    self.on_endpoint_read(endpoint, payload[0] | payload[1] << 8)
                elif kind == EVENT_IN_DATA and self.on_endpoint_write:
                    self.on_endpoint_write(endpoint, payload)
        for alarm in self.clock.alarms:
            if alarm.pending:
                alarm.pending = False
                alarm.callback()


class WasmCdc:
    """A CDC console on the wasm core: `run()` steps the chip in slices and serves the host side between them."""

    def __init__(self, exports: Any, slice_instructions: int = 50_000) -> None:
        self.exports = exports
        self.slice = slice_instructions
        self.usb = WasmUsb(exports)
        self.cdc = USBCDC(self.usb)  # type: ignore[arg-type]  # duck-typed controller
        self.received = bytearray()
        self.connected = False
        self.slices = 0
        self.cdc.on_serial_data = self.received.extend
        self.cdc.on_device_connected = self._connected

    def _connected(self) -> None:
        self.connected = True

    def run(self, max_slices: int, until: "Callable[[], bool]") -> bool:
        for _ in range(max_slices):
            self.slices += 1
            assert self.exports.rp2040_run_n(self.slice) >= 0, "the core faulted"
            self.usb.pump()
            if until():
                return True
        return False

    def send(self, data: bytes) -> None:
        for byte in data:
            self.cdc.send_serial_byte(byte)
