"""Kaluma (https://kaluma.io/) UF2 runner with a USB CDC console.

Usage: python demo/kaluma_run.py [--image firmware.uf2] [--gdb] [--littlefs littlefs.img]

Thin wrapper around `rp2040py kaluma` (also installed as a console script - see
src/rp2040py/cli/__init__.py), kept so this file path keeps working for anyone running it from a
checkout rather than a pip/uv install.

Verified against Kaluma 1.2.1: boots, USB enumerates, and evaluates real JS at its REPL prompt
(e.g. sending "1+1\\r\\n" gets back "2"). Quit with Ctrl+X, same as `micropython`'s interactive mode.
"""

import sys

from rp2040py.cli import main

if __name__ == "__main__":
    main(["kaluma", *sys.argv[1:]])
