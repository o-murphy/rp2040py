"""MicroPython / CircuitPython UF2 runner with a USB CDC console.

Usage: python demo/micropython_run.py [--image firmware.uf2] [--gdb] [--circuitpython]
                                       [--expect-text "some text"]

Thin wrapper around `rp2040py micropython` (also installed as a console script - see
src/rp2040py/cli.py), kept so this file path keeps working for anyone running it from a checkout
rather than a pip/uv install.
"""

import sys

from rp2040py.cli import main

if __name__ == "__main__":
    main(["micropython", *sys.argv[1:]])
