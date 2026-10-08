"""Phase 0 of docs/records/0096-cpp-mcu-core.md: the synthetic CPU benchmark.

One tiny Thumb-1 program (8 instructions per iteration: ALU with flags, shift, a store and a load, a
conditional branch) run through the real `Simulator._execute_batch()` of whichever build is
installed (native Cython, or pure Python with ``RP2040PY_SKIP_CYTHON=1``). It prints instructions/s
and checks the final registers, so the same number means the same work on every build.

``--mmio`` swaps the `ldr` from SRAM to a read of a *Python* peripheral (``TIMER.TIMELR``): the
difference between the two runs is what one access into a Python peripheral costs
(record 0096, "The cost of crossing into a Python peripheral").

    python scripts/bench/synthetic.py [--shift 20] [--mmio] [--repeat 3]

Single runs are noisy (+-10-15% on a shared machine); ``--repeat`` reports the best of N.
"""

import argparse
import sys
import time

from rp2040py.cortex_m0_core import CortexM0Core
from rp2040py.simulator import Simulator

SRAM_BASE = 0x20000000
BKPT = 0xBE00


def _movs(rd: int, imm: int) -> int:
    return (0b00100 << 11) | (rd << 8) | imm


def _lsls(rd: int, rm: int, imm: int) -> int:
    return (imm << 6) | (rm << 3) | rd


def _adds_imm3(rd: int, rn: int, imm: int) -> int:
    return (0b0001110 << 9) | (imm << 6) | (rn << 3) | rd


def _adds_reg(rd: int, rn: int, rm: int) -> int:
    return (0b0001100 << 9) | (rm << 6) | (rn << 3) | rd


def _eors(rdn: int, rm: int) -> int:
    return (0b0100000001 << 6) | (rm << 3) | rdn


def _str(rt: int, rn: int, imm: int) -> int:
    return (0b01100 << 11) | ((imm >> 2) << 6) | (rn << 3) | rt


def _ldr(rt: int, rn: int, imm: int) -> int:
    return (0b01101 << 11) | ((imm >> 2) << 6) | (rn << 3) | rt


def _subs_imm8(rdn: int, imm: int) -> int:
    return (0b00111 << 11) | (rdn << 8) | imm


def _bne(offset: int) -> int:
    return (0b1101 << 12) | (1 << 8) | ((offset >> 1) & 0xFF)


def build_program(shift: int, *, mmio: bool = False) -> "tuple[list[int], int, int, int]":
    """(halfwords, setup_instructions, loop_instructions, iterations). `r5` counts `2**shift` down."""
    setup = [_movs(7, 0x20), _lsls(7, 7, 24)]  # r7 = 0x20000000: the SRAM the loop stores to
    if mmio:
        # r1 = 0x40054000 (TIMER); the loop's LDR reads TIMELR from it.
        setup = [_movs(1, 0x40), _lsls(1, 1, 24), _movs(6, 0x54), _lsls(6, 6, 12), _adds_reg(1, 1, 6), *setup]
        load = _ldr(4, 1, 0x0C)
    else:
        load = _ldr(4, 7, 64)
    setup += [_movs(5, 1), _lsls(5, 5, shift), _movs(0, 0), _movs(2, 0)]
    loop = [_adds_imm3(0, 0, 1), _eors(2, 0), _lsls(3, 0, 2), _str(3, 7, 64), load, _adds_reg(2, 2, 4)]
    loop += [_subs_imm8(5, 1)]
    loop.append(_bne(-(len(loop) * 2 + 4)))
    return [*setup, *loop, BKPT], len(setup), len(loop), 1 << shift


def run_once(shift: int, *, mmio: bool) -> "tuple[float, int, int]":
    """(instructions per second, instructions executed, final r2)."""
    program, n_setup, n_loop, iterations = build_program(shift, mmio=mmio)
    simulator = Simulator()
    mcu = simulator.rp2040
    if mmio:  # the TIMER counts on the watchdog's tick: start it as pico-sdk's clocks_init does (clk_ref from the crystal, 12 cycles per tick)
        mcu.write_uint32(0x40008030, 2)
        mcu.write_uint32(0x40058000 + 0x2C, 12 | (1 << 9))
    for i, halfword in enumerate(program):
        mcu.write_uint16(SRAM_BASE + 2 * i, halfword)
    mcu.core.pc = SRAM_BASE
    mcu.core.registers[13] = SRAM_BASE + 0x40000
    simulator.stopped = False  # BKPT stops it (RP2040.on_break -> Simulator.stop)
    total = n_setup + n_loop * iterations + 1
    start = time.perf_counter()
    while not simulator.stopped:
        simulator._execute_batch()
    elapsed = time.perf_counter() - start
    if mcu.core.registers[5] != 0:
        raise SystemExit(f"loop did not finish: r5={mcu.core.registers[5]}")
    return total / elapsed, total, mcu.core.registers[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shift", type=int, default=20, help="2**shift iterations (default 20: ~8.4M instructions)")
    parser.add_argument("--mmio", action="store_true", help="the load reads TIMER.TIMELR (a Python peripheral)")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    best = max((run_once(args.shift, mmio=args.mmio) for _ in range(args.repeat)), key=lambda r: r[0])
    rate, total, r2 = best
    # The SRAM variant's r2 is a pure function of the shift - a cross-build correctness check. The
    # MMIO variant adds a time-dependent TIMELR into r2, so it is not comparable across runs.
    check = "" if args.mmio else f"  r2={r2:#x}"
    print(f"{CortexM0Core.__module__:40s} {rate / 1e6:8.2f} Minstr/s  ({total} instructions){check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
