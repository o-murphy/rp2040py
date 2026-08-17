# 0063. `RPPIO` ignores `SM_CLKDIV` and `[delay]` cycles, so PIO-generated waveforms are not to scale

- Status: **Proposed — found and measured, nothing implemented (2026-08-17).** Discovered while
  building [0062](0062-yd-rp2040-board-and-ws2812.md)'s `Ws2812`, which it blocks. No fix is
  attempted here: this touches the emulator's hottest loop and would need its own go-ahead.
- Conceived: 2026-08-17
- Related: [0062](0062-yd-rp2040-board-and-ws2812.md) (the device that hit it), 0037 (PIO stepping
  coupled to the CPU's instruction loop - the design this is a gap in, not a regression of), 0031 /
  0047 (the PIO/GPIO hot paths any fix has to stay inside), 0027 (CYW43, the biggest existing PIO
  user and therefore the biggest regression risk)

## What is wrong

`StateMachine` parses both of the RP2040's timing controls and then uses neither for pacing:

- **`SM_CLKDIV`** is stored (`clock_div_int`/`clock_div_frac`) and read back correctly, so firmware
  configuring a 12.8 MHz state machine on a 125 MHz system clock sees exactly what it wrote - and
  nothing else in the emulator ever consults it.
- **`[delay]` cycles** are decoded out of each instruction and added to a `self.cycles` counter,
  which nothing reads for pacing either.

`PIO.step()` advances every enabled state machine by exactly one instruction, and `_execute_batch`
calls it once per CPU instruction (0037's coupling). So every PIO program runs at "one instruction
per CPU instruction", regardless of the divider it asked for or the delays it wrote.

## Why it went unnoticed

Because the biggest PIO user in this project does not care. CYW43's bit-banged gSPI (0027) is a
*clocked* protocol: the emulated chip samples on the emulated clock edge the state machine drives,
so both sides are on the same relative timescale and only the *order* of edges matters. The same
is true of anything else the emulator hosts on both ends of the wire.

It breaks the moment a protocol carries meaning in the *width* of a pulse rather than in edges
relative to a clock line - which is exactly the class WS2812 belongs to, along with DHT11/22,
servo PWM over PIO, IR remote codes, and one-wire.

## Measurement (0062's own evidence)

Real CircuitPython `10.2.1` on the YD-RP2040 board, guest code writing `neopixel_write(p,
bytearray([0xFF, 0x00, 0xAA]))`, captured as raw GPIO23 edges:

    expected bits: 11111111 00000000 10101010
    high times:    16,32,16,16,24,16,32,16 | 8,16,8,16,16,16,16,16 | 32,8,32,8,40,16,24,16  (ns)

The hardware would give 703 ns for every `1` and 312 ns for every `0`, on a fixed 1.25 us period.
What arrives instead is 8-40 ns with the two symbols **overlapping**: the byte of all-zeros
contains 16 ns highs, and so does the byte of all-ones. No threshold - absolute, duty-cycle, or
adaptive - can separate populations that overlap. The information is destroyed before any device
sees it.

Two things are visible in that trace, and they are separable problems:

1. **Scale.** 4 and 9 PIO cycles became 1 and 2 CPU instructions. A decoder can be written to be
   scale-free (0062's is), so this alone is survivable.
2. **Resolution and jitter.** Because delays are dropped, a symbol is only 1-2 steps long, and any
   stall (FIFO refill, the CPU's own scheduling) is the same size as the signal. This is what
   actually destroys the encoding, and no device-side cleverness recovers it.

## Options, in increasing order of faithfulness

1. **Honour `[delay]` only.** Give each state machine a small "cycles owed" counter: `step()`
   decrements it and returns instead of executing when it is non-zero. Restores the 4:9 ratio and
   most of the resolution, is a handful of lines in both the Python and Cython twins, and leaves
   the overall speed of a PIO program unchanged relative to the CPU (so CYW43's throughput is
   unaffected in the direction that matters - it would run *slower* per PIO instruction, which is
   the honest direction but still a real behaviour change).
2. **Honour `SM_CLKDIV` too.** The complete fix: accumulate fractional CPU-cycles-per-PIO-cycle and
   step when the accumulator crosses 1. Also makes PIO programs run at their real speed relative to
   the CPU, which is the part most likely to shake out latent assumptions elsewhere - a state
   machine that currently keeps up with the CPU would suddenly be ~10x slower, and CYW43's boot
   path is full of hand-tuned interactions between the two (0037/0043 both exist because of that
   coupling's edge cases).
3. **Do nothing, and document the ceiling.** Pulse-width protocols stay out of reach; edge-order
   protocols keep working. This is the status quo, and it is defensible for as long as nobody needs
   the former - but it needs *saying*, because the failure mode is silent: a device receives a
   plausible-looking stream of edges and decodes garbage, which is precisely how 0062's `Ws2812`
   spent an afternoon looking like a decoder bug.

Option 1 is the recommended first step if this is taken up: it is small, it is the half that
actually restores the encoding, and it can be measured against 0062's captured trace before
anything larger is attempted.

## What must be true of any fix

- **Both twins.** `peripherals/_state_machine.py` and `native/_state_machine.pyx` must stay in
  lockstep, and the parity tests already run under `RP2040PY_SKIP_CYTHON=0` and `=1`.
- **CYW43 must be re-verified live**, not just unit-tested: it is the one real PIO consumer, its
  boot path is timing-sensitive (0043 exists because of a first-batch/refill race), and the
  `ci-micropython.yml` CYW43 jobs are the bar.
- **Measure the hot loop.** 0047 got the PIO/GPIO path to where it is; a per-step counter decrement
  is cheap, but a fractional-divider accumulator per state machine per step is not obviously so.
- **0062's trace is the acceptance test.** Replaying the same guest write should produce high times
  clustered around a 4:9 ratio with no overlap, and its `Ws2812` should then decode `ff 00 aa`.
