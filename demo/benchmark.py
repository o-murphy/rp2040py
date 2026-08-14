#!/usr/bin/env python3
"""Benchmark CortexM0Core.execute_instruction() throughput.

Two modes:

- Synthetic (default): writes a straight-line block of alternating ADDS/SUBS instructions to
  RAM and repeatedly executes it, resetting PC between blocks. No branches, no bus/peripheral
  traffic beyond RAM fetches - isolates raw instruction-dispatch overhead, useful for comparing
  interpreters (CPython vs PyPy vs CPython 3.14+ JIT) or measuring the effect of core-level
  optimizations.
- Firmware boot (--image): loads a real hex/uf2 image (plus an optional littlefs.img) and runs
  it to completion (or until --expect-text is found, or --timeout elapses), reporting wall time
  and instructions/sec for a realistic end-to-end workload. Mirrors demo/micropython_run.py's
  boot path (including the USB CDC console MicroPython/CircuitPython print to) but headless - no
  GDB server, no stdin thread - and exits on its own.

Usage:
    python demo/benchmark.py                                   # synthetic, 5,000,000 instructions
    python demo/benchmark.py --instructions 20000000            # synthetic, more instructions
    python demo/benchmark.py --image micropython.uf2 --expect-text "Hello, MicroPython!"

Thin wrapper around `rp2040py bench` (also installed as a console script - see
src/rp2040py/cli.py), kept so this file path keeps working for anyone running it from a checkout
rather than a pip/uv install.
"""

import sys

from rp2040py.cli import main

if __name__ == "__main__":
    main(["bench", *sys.argv[1:]])
