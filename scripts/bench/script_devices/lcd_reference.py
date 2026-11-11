"""The reference for the ST7735S replay of ``lcd/``: the project's own ``St7735s`` run over a recorded event stream.

Reads ``trace.bin`` (``record_lcd_trace.py``), writes ``trace_tex.bin`` - the same events with every pixel byte of a ``RAMWR`` replaced by a
fixed pseudo-random byte (the vendor driver paints two flat frames, 0x00 and 0xFF, which would hide a wrong address) - and ``expected.txt``,
``<flushes per pass> <FNV-1a of the frame buffer>``, which every port in ``lcd/`` has to reproduce. Then times the class itself, in three ways:

    full    ``_on_transmit`` (reads CS/DC, computes the byte time, schedules the alarm - the last two stubbed)
    logic   ``_on_command`` / ``_on_data`` only: what the ports implement, so the comparable figure
    empty   the replay loop and its dispatch with no device work (what to take off ``logic`` to see the device alone)

    python scripts/bench/script_devices/lcd_reference.py /tmp/work/lcd
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PASSES = 20
FNV_OFFSET, FNV_PRIME = 2166136261, 16777619


def textured(trace: bytes) -> bytes:
    out = bytearray()
    command: int | None = None
    state = 12345
    for flags, byte in zip(trace[0::2], trace[1::2], strict=True):
        if flags == 1:
            command = byte
        if flags == 3 and command == 0x2C:
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            byte = (state >> 16) & 0xFF
        out += bytes((flags, byte))
    return bytes(out)


def fnv1a(buffer: "bytes | bytearray") -> int:
    value = FNV_OFFSET
    for byte in buffer:
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def main() -> None:
    directory = Path(sys.argv[1])
    sys.path.insert(0, str(ROOT))
    from rp2040py.external.st7735s import St7735s
    from rp2040py.gpio_pin import GPIOPinState

    trace = textured((directory / "trace.bin").read_bytes())
    (directory / "trace_tex.bin").write_bytes(trace)
    events = list(zip(trace[0::2], trace[1::2], strict=True))
    low, high = GPIOPinState.LOW, GPIOPinState.HIGH

    class Pin:
        __slots__ = ("value",)

    class Spi:
        clock_frequency = 10_000_000
        data_bits = 8

    class Alarm:
        def schedule(self, nanos: int) -> None:
            pass

    def make() -> "tuple[St7735s, list[int]]":
        device = St7735s()
        flushes = [0]
        device._cs, device._dc, device._spi, device._tx_alarm = Pin(), Pin(), Spi(), Alarm()  # type: ignore[assignment]
        device.on_frame = lambda _buffer: flushes.__setitem__(0, flushes[0] + 1)
        return device, flushes

    def replay(device: St7735s, mode: str) -> None:
        device._reset_state()
        chip_select, data_command = device._cs, device._dc
        for flags, byte in events:
            if flags & 0x80:
                if mode != "empty":
                    device._on_cs_changed(high, low)
            elif flags & 0x40:
                if mode != "empty":
                    device._on_rst_changed(low, high)
            elif mode == "full":
                chip_select.value = low if flags & 1 else high  # type: ignore[attr-defined]
                data_command.value = high if flags & 2 else low  # type: ignore[attr-defined]
                device._on_transmit(byte)
            elif flags & 1 and mode == "logic":
                if flags & 2:
                    device._on_data(byte)
                else:
                    device._on_command(byte)

    device, flushes = make()
    replay(device, "full")
    expected = f"{flushes[0]} {fnv1a(device.frame_buffer)}"
    (directory / "expected.txt").write_text(expected + "\n")
    print(f"expected (flushes per pass, FNV-1a): {expected}")
    for mode in ("full", "logic", "empty"):
        best = float("inf")
        for _ in range(3):
            device, _ = make()
            start = time.perf_counter()
            for _ in range(PASSES):
                replay(device, mode)
            best = min(best, time.perf_counter() - start)
        print(
            f"python[{mode:5s}] {best / (PASSES * len(events)) * 1e9:8.1f} ns/event  {best / PASSES * 1e3:7.2f} ms/pass"
            f"  ({len(events)} events)"
        )


if __name__ == "__main__":
    main()
