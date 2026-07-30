"""Generic hex/uf2 firmware runner with a GDB server, for local testing.

Usage: python demo/emulator_run.py [--image path/to/firmware.hex|.uf2]
"""

import argparse
import sys

from bootrom import BOOTROM_B1
from intelhex import load_hex
from load_flash import load_uf2

from rp2040py.gdb.gdb_tcp_server import GDBTCPServer
from rp2040py.simulator import Simulator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="hello_uart.hex")
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

    gdb_server = GDBTCPServer(simulator, 3333)
    print(f"RP2040 GDB Server ready! Listening on port {gdb_server.port}")

    def _on_byte(value: int) -> None:
        sys.stdout.buffer.write(bytes([value]))
        sys.stdout.flush()

    mcu.uart[0].on_byte = _on_byte

    simulator.rp2040.core.pc = 0x10000000
    simulator.execute()


if __name__ == "__main__":
    main()
