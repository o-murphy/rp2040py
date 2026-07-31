"""Generic USB-CDC REPL runner, for firmware other than MicroPython/CircuitPython.

Usage: python demo/kaluma_run.py --image path/to/firmware.uf2 [--gdb]

Unlike demo/micropython_run.py, this isn't a thin wrapper around an `rp2040py.cli` subcommand:
`micropython`'s interactive mode assumes MicroPython/CircuitPython's own REPL conventions (the
`--circuitpython` nudge byte, raw-REPL protocol for `-c`/`-m`/`<filename>`), which don't apply to
other USB-CDC-console firmware. This talks to `USBCDC` directly instead - the same building block
`MicroPythonDevice`/`micropython`'s interactive mode are themselves built on - to demonstrate that
rp2040py's USB/CDC emulation isn't MicroPython-specific: any firmware presenting a CDC-ACM serial
console (a plain ANSI/VT100 terminal on the other end) works the same way.

Verified against Kaluma (https://kaluma.io/, a JavaScript runtime for RP2040) 1.2.1: boots, USB
enumerates, and evaluates real JS at its REPL prompt (e.g. sending "1+1\\r\\n" gets back "2").
Kaluma's own littlefs-backed code storage doesn't mount cleanly on the emulator's blank flash (logs
"Bad block at 0x0" / "No space left on device" on boot) - harmless for REPL use, since that only
affects loading code previously flashed via `.flash -w`, not interactive evaluation.

Quit with Ctrl+X, same as `micropython`'s interactive mode.
"""

import argparse
import os
import sys
import termios
import threading
import time
import tty

from rp2040py.device.bootrom import BOOTROM_B1
from rp2040py.device.load_flash import load_uf2
from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="UF2 firmware image with a USB-CDC console")
    parser.add_argument("--gdb", action="store_true")
    parser.add_argument("--gdb-port", type=int, default=3333)
    args = parser.parse_args()

    simulator = Simulator()
    mcu = simulator.rp2040
    mcu.load_bootrom(BOOTROM_B1)
    mcu.logger = ConsoleLogger(LogLevel.ERROR)

    print(f"Loading uf2 image {args.image}")
    load_uf2(args.image, mcu)

    if args.gdb:
        gdb_server = GDBTCPServer(simulator, args.gdb_port)
        print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    cdc = USBCDC(mcu.usb_ctrl)

    def _on_serial_data(value: "bytes | bytearray") -> None:
        sys.stdout.buffer.write(value)
        sys.stdout.flush()

    # Registered before start so nothing the device prints while enumerating is dropped.
    cdc.on_serial_data = _on_serial_data

    stdin_fd = sys.stdin.fileno()
    old_termios = None
    if sys.stdin.isatty():
        old_termios = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    def _restore_termios() -> None:
        if old_termios is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)

    def _read_stdin_loop() -> None:
        try:
            while True:
                chunk = os.read(stdin_fd, 4096)
                if not chunk:
                    break
                # 24 is Ctrl+X
                if chunk[0] == 24:
                    # os._exit(), not sys.exit(): this runs on the dedicated stdin reader thread,
                    # not the main thread, so sys.exit() would only terminate that thread instead
                    # of the whole process.
                    _restore_termios()
                    os._exit(0)
                for byte in chunk:
                    cdc.send_serial_byte(byte)
        finally:
            _restore_termios()

    threading.Thread(target=_read_stdin_loop, daemon=True).start()

    def _on_device_connected() -> None:
        # A blank line nudges most CDC REPLs (Kaluma's own docs: "if you cannot see the prompt,
        # press Enter several times") into printing their prompt without assuming anything about
        # the firmware's actual REPL protocol.
        cdc.send_serial_byte(ord("\r"))
        cdc.send_serial_byte(ord("\n"))

    cdc.on_device_connected = _on_device_connected

    mcu.core.pc = 0x10000000
    simulator.execute()

    # simulator.execute() only runs the first burst synchronously and then reschedules itself via
    # threading.Timer, so main() would otherwise return immediately and leave the process hanging
    # in interpreter shutdown, joining that non-daemon timer chain forever - and unresponsive to
    # Ctrl+C there. Waiting here on the main thread keeps KeyboardInterrupt handling clean. See
    # demo/micropython_run.py / tests/micropython_spi_run.py for the same pattern.
    try:
        while simulator.executing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        _restore_termios()
        simulator.stop()
        os._exit(130)


if __name__ == "__main__":
    main()
