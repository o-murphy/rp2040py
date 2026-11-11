"""Record the SPI/DC/CS/RST event stream a real firmware drives into ``external/st7735s.py``'s ``St7735s``.

Runs ``demo/lcd_run.py`` (MicroPython + the vendor driver of the Waveshare RP2040-LCD-0.96) as it is, with the ``St7735s`` methods wrapped to write
one record per event, and Pillow replaced by a stub (the frames are not decoded here). ``trace.bin`` is two bytes per event, ``(flags, byte)``:

    0x01  CS was low        0x02  DC was high (data, not a command)
    0x40  RST went low      0x80  CS went high after having been low

    python scripts/bench/script_devices/record_lcd_trace.py /tmp/work/lcd

``lcd_reference.py`` turns it into the replayed trace and the reference result. Record 0109 says what the replay does and does not measure.
"""

import hashlib
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.modules["PIL"] = MagicMock()
    sys.modules["PIL.Image"] = MagicMock()
    sys.path.insert(0, str(ROOT))

    from rp2040py.external.st7735s import St7735s
    from rp2040py.gpio_pin import GPIOPinState

    events = bytearray()
    flushed: list[str] = []
    original_transmit, original_cs, original_rst, original_flush = (
        St7735s._on_transmit,
        St7735s._on_cs_changed,
        St7735s._on_rst_changed,
        St7735s._flush,
    )

    def transmit(self: St7735s, byte: int) -> None:
        flags = (1 if self._cs.value == GPIOPinState.LOW else 0) | (2 if self._dc.value != GPIOPinState.LOW else 0)
        events.extend((flags, byte))
        original_transmit(self, byte)

    def cs_changed(self: St7735s, state: GPIOPinState, old_state: GPIOPinState) -> None:
        if state != GPIOPinState.LOW and old_state == GPIOPinState.LOW:
            events.extend((0x80, 0))
        original_cs(self, state, old_state)

    def rst_changed(self: St7735s, state: GPIOPinState, old_state: GPIOPinState) -> None:
        if state == GPIOPinState.LOW:
            events.extend((0x40, 0))
        original_rst(self, state, old_state)

    def flush(self: St7735s) -> None:
        if self._dirty:
            flushed.append(hashlib.md5(bytes(self.frame_buffer)).hexdigest())
        original_flush(self)

    St7735s._on_transmit, St7735s._on_cs_changed, St7735s._on_rst_changed, St7735s._flush = (
        transmit,
        cs_changed,
        rst_changed,
        flush,
    )

    sys.argv = ["lcd_run.py", "--screenshot", str(out_dir / "out")]
    try:
        runpy.run_path(str(ROOT / "demo" / "lcd_run.py"), run_name="__main__")
    except SystemExit:
        pass
    (out_dir / "trace.bin").write_bytes(bytes(events))
    (out_dir / "frames.txt").write_text("\n".join(flushed))
    print(f"events {len(events) // 2}, frames flushed {len(flushed)}")


if __name__ == "__main__":
    main()
