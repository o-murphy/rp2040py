"""MicroPython / CircuitPython UF2 runner with a USB CDC console.

Usage: python demo/micropython_run.py [--image firmware.uf2] [--gdb] [--circuitpython]
                                       [--expect-text "some text"]
"""

import argparse
import os
import sys
import termios
import threading
import time
import tty

from bootrom import BOOTROM_B1
from load_flash import load_circuitpython_flash_image, load_micropython_flash_image, load_uf2

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image")
    parser.add_argument("--expect-text")
    parser.add_argument("--gdb", action="store_true")
    parser.add_argument("--gdb-port", type=int, default=3333)
    parser.add_argument("--circuitpython", action="store_true")
    args = parser.parse_args()

    simulator = Simulator()
    mcu = simulator.rp2040
    mcu.load_bootrom(BOOTROM_B1)
    mcu.logger = ConsoleLogger(LogLevel.ERROR)

    if not args.circuitpython:
        image_name = args.image or "RPI_PICO-20230426-v1.20.0.uf2"
    else:
        image_name = args.image or "adafruit-circuitpython-raspberry_pi_pico-en_US-8.0.2.uf2"
    print(f"Loading uf2 image {image_name}")
    load_uf2(image_name, mcu)

    if os.path.exists("littlefs.img") and not args.circuitpython:
        print("Loading uf2 image littlefs.img")
        load_micropython_flash_image("littlefs.img", mcu)
    elif os.path.exists("fat12.img") and args.circuitpython:
        load_circuitpython_flash_image("fat12.img", mcu)

    if args.gdb:
        gdb_server = GDBTCPServer(simulator, args.gdb_port)
        print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    cdc = USBCDC(mcu.usb_ctrl)

    def _on_device_connected() -> None:
        if not args.circuitpython:
            # We send a newline so the user sees the MicroPython prompt
            cdc.send_serial_byte(ord("\r"))
            cdc.send_serial_byte(ord("\n"))
        else:
            cdc.send_serial_byte(3)

    cdc.on_device_connected = _on_device_connected

    current_line = ""

    def _on_serial_data(value: bytes) -> None:
        nonlocal current_line
        sys.stdout.buffer.write(value)
        sys.stdout.flush()

        for byte in value:
            char = chr(byte)
            if char == "\n":
                if args.expect_text and args.expect_text in current_line:
                    print(f'Expected text found: "{args.expect_text}"')
                    print("TEST PASSED.")
                    # os._exit(), not sys.exit(): this callback runs on a Simulator
                    # worker thread (threading.Timer), and sys.exit() there only
                    # terminates that thread, not the whole process (unlike Node's
                    # process.exit(), which the upstream JS relies on here).
                    os._exit(0)
                current_line = ""
            else:
                current_line += char

    cdc.on_serial_data = _on_serial_data

    stdin_fd = sys.stdin.fileno()
    old_termios = None
    if sys.stdin.isatty():
        old_termios = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    def _read_stdin_loop() -> None:
        try:
            while True:
                chunk = os.read(stdin_fd, 4096)
                if not chunk:
                    break
                # 24 is Ctrl+X
                if chunk[0] == 24:
                    # os._exit(), not sys.exit(): this runs on the dedicated stdin
                    # reader thread, not the main thread, so sys.exit() would only
                    # terminate that thread instead of the whole process.
                    if old_termios is not None:
                        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
                    os._exit(0)
                for byte in chunk:
                    cdc.send_serial_byte(byte)
        finally:
            if old_termios is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)

    threading.Thread(target=_read_stdin_loop, daemon=True).start()

    simulator.rp2040.core.pc = 0x10000000
    simulator.execute()

    # simulator.execute() only runs the first burst synchronously and then
    # reschedules itself via threading.Timer, so main() would otherwise return
    # immediately and leave the process hanging in interpreter shutdown,
    # joining that non-daemon timer chain forever - and unresponsive to
    # Ctrl+C there. Waiting here on the main thread keeps KeyboardInterrupt
    # handling clean.
    try:
        while simulator.executing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        if old_termios is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
        simulator.stop()
        os._exit(130)


if __name__ == "__main__":
    main()
