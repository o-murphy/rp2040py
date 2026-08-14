#!/usr/bin/env python3
"""Generic hex/uf2 firmware runner with a GDB server, for local testing.

Usage: python demo/emulator_run.py [--image path/to/firmware.hex|.uf2]

Thin wrapper around `rp2040py run` (also installed as a console script - see
src/rp2040py/cli.py), kept so this file path keeps working for anyone running it from a checkout
rather than a pip/uv install.
"""

import sys

from rp2040py.cli import main

if __name__ == "__main__":
    main(["run", *sys.argv[1:]])
