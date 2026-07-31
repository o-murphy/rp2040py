"""Runs micropython.uf2 + littlefs-spi.img and checks the bytes bit-banged over SPI0
against the expected messages passed as positional command-line arguments.

Usage: python tests/micropython_spi_run.py "hello world" "h" "0123456789abcdef..."
"""

import argparse
import os
import time

from rp2040py.cli.mp_retrieve import retrieve_micropython
from rp2040py.device.load_flash import load_micropython_flash_image, load_uf2
from rp2040py.gpio_pin import GPIOPinState
from rp2040py.simulator import Simulator
from rp2040py.utils.logging import ConsoleLogger, LogLevel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected", nargs="*")
    parser.add_argument("--image", default="micropython.uf2")
    args = parser.parse_args()
    expected_messages = list(args.expected)

    simulator = Simulator()
    mcu = simulator.rp2040

    from rp2040py.device.bootrom import BOOTROM_B1

    mcu.load_bootrom(BOOTROM_B1)
    mcu.logger = ConsoleLogger(LogLevel.ERROR)

    image_name = retrieve_micropython(args.image)
    if image_name is None:
        print(f"Could not find micropython image: {image_name}")
        os._exit(1)

    print(f"Loading uf2 image {args.image}")
    load_uf2(args, mcu)

    littlefs = "littlefs-spi.img"
    if os.path.exists(littlefs):
        print(f"Loading littlefs image {littlefs}")
        load_micropython_flash_image(littlefs, mcu)

    spi_buf = bytearray()

    def _on_cs_changed(state: GPIOPinState, old_state: GPIOPinState) -> None:
        if not spi_buf:
            return

        if state == GPIOPinState.HIGH and old_state == GPIOPinState.LOW:
            expected = expected_messages.pop(0) if expected_messages else None
            if spi_buf.decode("latin-1") != expected:
                print("SPI TEST FAILED.")
                # os._exit(), not sys.exit(): this callback runs on a Simulator
                # worker thread (threading.Timer), and sys.exit() there only
                # terminates that thread, not the whole process (unlike Node's
                # process.exit(), which the upstream JS relies on here).
                os._exit(1)
            else:
                print("SPI MESSAGE RECEIVED.")
                spi_buf.clear()

            if not expected_messages:
                print("SPI TEST PASSED.")
                os._exit(0)

    mcu.gpio[5].add_listener(_on_cs_changed)

    transmit_alarm = mcu.clock.create_alarm(lambda: mcu.spi[0].complete_transmit(0))

    def _on_transmit(char: int) -> None:
        spi_buf.append(char)
        transmit_alarm.schedule(2000)  # 2us per byte, so 4 MHz SPI

    mcu.spi[0].on_transmit = _on_transmit

    mcu.core.pc = 0x10000000
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
