"""Gate G4 of docs/records/0096-cpp-mcu-core.md: `machine.freq()` works on the empty host - a stock MicroPython 1.29.0 on the bare C++ core (the harness is `tests/utils/gate_boot.py`; no Python, no
Cython, no clock-tree hook) changes the system clock and the core's time, the DMA's transfers and the PWM counter follow it.

The guest times the same work at 125 MHz, after `machine.freq(250_000_000)` and back at 125 MHz, with the TIMER (`time.ticks_us()`, which runs off clk_ref and does not move with clk_sys): a busy loop (the
CPU), a memory-to-memory DMA copy (the DMA) and the free-running counter of PWM slice 0 (the PWM: counts per microsecond). It prints the figures and `machine.freq()`; the host sees the bytes and nothing else.
This is `scripts/debug/live_freq.py` on the bare core, with the DMA and the PWM added.
"""

import pytest
from utils import gate_boot
from utils.emscripten import needs_processes
from utils.littlefs import importorskip_littlefs

importorskip_littlefs()

pytestmark = [
    needs_processes,
    pytest.mark.skipif(gate_boot.cxx() is None and not gate_boot.required(), reason="no C++ compiler on PATH"),
]

MAIN_PY = b"""\
import machine, rp2, sys, time

uart = machine.UART(0, 115200, tx=machine.Pin(0), rx=machine.Pin(1))  # MicroPython's stdio is the USB CDC: the gate's host sees UART0 only

def say(text):
    uart.write(text + "\\r\\n")

def spin():
    t = time.ticks_us()
    for i in range(20000):
        pass
    return time.ticks_diff(time.ticks_us(), t)

src = bytearray(65536)
dst = bytearray(65536)
dma = rp2.DMA()
ctrl = dma.pack_ctrl(size=2, inc_read=True, inc_write=True)

def copy():
    dma.config(read=src, write=dst, count=16384, ctrl=ctrl)
    t = time.ticks_us()
    dma.active(1)
    while dma.active():
        pass
    return time.ticks_diff(time.ticks_us(), t)

mem = machine.mem32
mem[0x40050000] = 1  # PWM slice 0: CSR.EN, divider 1, TOP 0xFFFF, free-running

def pwm_ticks_per_us():
    t0 = time.ticks_us()
    c0 = mem[0x40050008] & 0xFFFF
    while time.ticks_diff(time.ticks_us(), t0) < 100:
        pass
    c1 = mem[0x40050008] & 0xFFFF
    elapsed = time.ticks_diff(time.ticks_us(), t0)
    return ((c1 - c0) & 0xFFFF) // elapsed

def measure():
    return spin(), copy(), pwm_ticks_per_us()

try:
    a = measure()
    machine.freq(250_000_000)
    b = measure()
    f250 = machine.freq()
    machine.freq(125_000_000)
    c = measure()
    say("G4 %d %d %s %s %s" % (machine.freq(), f250, " ".join(map(str, a)), " ".join(map(str, b)), " ".join(map(str, c))))
except Exception as error:
    say("G4 FAILED %r" % (error,))
say("G4END")
"""


def test_machine_freq_moves_the_cpu_the_dma_and_the_pwm_on_the_bare_cpp_core(tmp_path):
    rom, flash = gate_boot.micropython_images(tmp_path, MAIN_PY)
    done = gate_boot.run(gate_boot.build_harness(tmp_path), rom, flash, "G4END")
    line = next(line for line in done.stdout.splitlines() if line.startswith("G4 "))
    print(line)  # visible with -s: freq, f250, then (spin us, copy us, PWM ticks/us) at 125, 250 and 125 MHz
    numbers = [int(token) for token in line.split()[1:]]
    freq, f250 = numbers[0], numbers[1]
    (spin_a, copy_a, pwm_a), (spin_b, copy_b, pwm_b), (spin_c, copy_c, pwm_c) = (
        numbers[2:5],
        numbers[5:8],
        numbers[8:11],
    )
    assert (freq, f250) == (125_000_000, 250_000_000), line
    # the same work at 250 MHz takes about half the time on the TIMER, and the same again back at 125 MHz
    assert 1.7 < spin_a / spin_b < 2.3 and 0.9 < spin_c / spin_a < 1.1, line
    assert 1.5 < copy_a / copy_b < 2.5 and 0.9 < copy_c / copy_a < 1.1, line
    # PWM slice 0 counts clk_sys: ~125 then ~250 ticks per microsecond
    assert 100 <= pwm_a <= 150 and 200 <= pwm_b <= 300 and 100 <= pwm_c <= 150, line
    # and the host was called for the bytes only
    assert gate_boot.callbacks(done) and all(c.startswith("callback uart_byte=") for c in gate_boot.callbacks(done)), (
        done.stderr
    )
