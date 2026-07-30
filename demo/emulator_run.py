"""Generic hex/uf2 firmware runner with a GDB server, for local testing.

Usage: python demo/emulator_run.py [--image path/to/firmware.hex|.uf2]
"""

import argparse
import os
import sys
import time

from bootrom import BOOTROM_B1
from intelhex import load_hex
from load_flash import load_uf2

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.simulator import Simulator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="hello_uart.hex")
    parser.add_argument("--gdb-port", type=int, default=3333)
    args = parser.parse_args()

    simulator = Simulator()
    mcu = simulator.rp2040
    mcu.load_bootrom(BOOTROM_B1)

    image_name = args.image
    extension = image_name.rsplit(".", 1)[-1]
    if extension == "hex":
        print(f"Loading hex image {image_name}")
        with open(image_name) as f:
            load_hex(f.read(), mcu.flash, 0x10000000)
    elif extension == "uf2":
        print(f"Loading uf2 image {image_name}")
        load_uf2(image_name, mcu)
    else:
        print(f"Unsupported file type: {extension}")
        sys.exit(1)

    gdb_server = GDBTCPServer(simulator, args.gdb_port)
    print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    def _on_byte(value: int) -> None:
        sys.stdout.buffer.write(bytes([value]))
        sys.stdout.flush()

    mcu.uart[0].on_byte = _on_byte

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
        simulator.stop()
        os._exit(130)


if __name__ == "__main__":
    main()
