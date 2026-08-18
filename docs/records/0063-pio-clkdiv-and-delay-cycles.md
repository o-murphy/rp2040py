# 0063. `RPPIO` ignores `SM_CLKDIV` and `[delay]` cycles, so PIO-generated waveforms are not to scale

- Status: **Implemented (2026-08-18)** - both halves, measured against 0062's own trace and
  re-verified live on CYW43. See "Implemented" at the end for what the experiment changed about the
  recommendation below. (Original status, kept: *Proposed — found and measured, nothing implemented
  (2026-08-17). Discovered while building [0062](0062-yd-rp2040-board-and-ws2812.md)'s `Ws2812`,
  which it blocks. No fix is attempted here: this touches the emulator's hottest loop and would need
  its own go-ahead.*)
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

## Addendum, same day: the divider is not hypothetical, and the recommendation changes

The options above were written without knowing whether any real firmware sets `SM_CLKDIV` at all.
Measured now, by reading the register block after MicroPython `1.23.0` brings the Pico W's WLAN up
(the native `StateMachine` is a `cdef class`, so this is read through the memory map rather than
monkeypatched):

    PIO0 SM0-3: enabled=False  CLKDIV int=1 frac=0
    PIO1 SM0  : enabled=True   CLKDIV int=2 frac=0   <- CYW43's gSPI
    PIO1 SM1-3: enabled=False  CLKDIV int=1 frac=0

So CYW43 asks for **sysclk/2** and this emulator runs it at **sysclk** - its bit-banged SPI clock
is twice the rate the driver configured, relative to CPU time. That reframes the third option:
"do nothing and document the ceiling" is no longer a choice between coarse and fine, it is leaving
a measured wrong number in place in the project's most exercised PIO path.

**Recommendation, updated: implement both halves (`[delay]` *and* `CLKDIV`), as a due-time skip
rather than a per-machine tick.** Keep one integer per PIO block - the cycle its earliest machine
is next due at - and have the caller compare that against the cycle counter, touching machines only
when one is actually due. Then:

- it is **cheaper than today**, not more expensive. The current cost is four `step()` calls per CPU
  instruction, each executing a full instruction; after this it is one integer comparison, and with
  CYW43's `CLKDIV=2` half of the state-machine work simply disappears.
- the fractional part is a fixed-point integer accumulator (CLKDIV is 16.8), never a float, so in
  the `cdef` twin the added work is a C-level add and compare.
- nothing decouples from the instruction loop: 0037 coupled them deliberately to kill a livelock,
  and this stays inside that coupling, preserving determinism.

A fourth option considered and rejected: *do not simulate idle cycles at all* - have the machine
compute its pin-change schedule ahead of time and let observers read it. Cheapest of all, and
sound only for straight-line delay runs; it breaks as soon as a program branches on input
(`wait pin`, `jmp !x`), which is what real PIO programs do. Worth keeping as a special case inside
the due-time model if it ever pays, not as the model.

Land it in two steps, because the second is where the risk is:

1. the mechanism, verified against 0062's captured trace - high times must cluster at a 4:9 ratio
   with no overlap, and `Ws2812` must decode `ff 00 aa`;
2. CYW43 live on `1.23.0` and `1.28.0`, because its effective SPI clock halves and 0043 exists
   precisely because that interaction is brittle. `bench` before/after alongside.

## Implemented, 2026-08-18 — both halves, and the ceiling that had to come with them

Built as recommended above: a due-time skip in `RPPIO`, honouring **both** `SM_CLKDIV` and
`[delay]`, in both twins (`peripherals/_pio.py` + `peripherals/_state_machine.py`, and
`native/_pio.pyx` + `native/_state_machine.pyx` with their `.pxd`s). The design is exactly the one
sketched above, with one addition the sketch did not have and that live firmware forced - see "What
the experiment found" below.

### The mechanism

- **One integer per PIO block.** `RPPIO.cycle_fp` counts elapsed system clocks and `next_due_fp`
  holds the earliest cycle any of its four machines is next due at, both in 1/256ths of a system
  clock so a fractional divider needs no float anywhere. `advance(cycles)` - the paced entry point
  `_execute_batch()` now calls once per CPU instruction, with **that instruction's own cycle
  count** rather than a flat 1 - adds and compares, and returns. That is the whole fast path.
- **One integer per state machine.** `StateMachine.div_fp` is `SM_CLKDIV` as a single 16.8
  fixed-point divisor (`CLKDIV_INT == 0` meaning /65536, per datasheet 3.5.5), recomputed on
  every write to the register; `next_due_fp` is that machine's own absolute due time. `step()`
  re-arms it by the cycles the instruction actually cost - which `execute_instruction()` already
  accumulated correctly into `cycles` (1 for the instruction plus its `[delay]`), so the `[delay]`
  half needed no new decoding at all, only somewhere for the number to go.
- **Stalls are excluded, not polled.** A `waiting` machine has no answer to "when next due", so it
  is left out of `next_due_fp` entirely; `check_wait()` - which already runs on every event that
  can unstall a machine (an MMIO FIFO write, a GPIO edge, an IRQ) - re-arms it *absolutely* (a
  stall ends on the cycle its condition becomes true, not on a delta from when it began) and
  lowers the block's due time so the very next `advance()` picks it up. That is what let
  `_execute_batch()`'s `_has_runnable_machine()` scan be deleted outright rather than kept
  alongside.
- `CTRL.CLKDIV_RESTART` is implemented rather than warned about, now that there is a divider phase
  to restart.

### What the experiment found, and what it cost

**The recommendation above was right about the arithmetic and wrong about the loop.** Written as
recommended - "touch machines only when one is actually due", walking through as many instructions
as the due times said were owed - it broke CYW43 immediately and totally: MicroPython v1.23.0's
`nic.scan()` returned `OSError: EPERM` instantly, every run, on both `1.23.0` and a
divider-forced-to-1 build. Bisected by forcing each half in turn:

| build | `nic.scan()` |
|---|---|
| the new mechanism, but advancing PIO exactly 1 cycle per CPU instruction (i.e. the old rate) | works |
| `[delay]` honoured, `CLKDIV` forced to 1 | **EPERM** |
| both honoured | **EPERM** |
| both honoured, **at most one instruction per `advance()` call** | works |

So it was never the *average* rate - CYW43's own `CLKDIV=2` makes its state machine **slower**
relative to the CPU, not faster. It was the **burst**. [0043](0043-pio-dma-first-batch-race.md)
turns on `clock.tick()` running between every single PIO step: a DMA-fed TX FIFO is refilled only
by `SimulationClock` alarms, and those only fire from the CPU loop, so a machine allowed two steps
between two ticks can drain a FIFO the DMA channel has had no chance to refill, raise a premature
`FDEBUG_TXSTALL`, and be read by `cyw43_spi_transfer()`'s TX-only branch as "transfer complete"
after the first few words. Handing `advance()` the instruction's real cycle count (1-3, averaging
~1.4) is enough on its own to produce that second step sometimes, with or without a divider.

**So the ceiling is part of the fix, not a shortcut: a state machine never executes more than one
instruction per CPU instruction.** This only ever *slows* a machine relative to the CPU. A divider
at or below the average cycles-per-instruction still runs "as fast as the CPU dispatches
instructions" - exactly what every state machine did before this change - so nothing that worked
before can be outrun by anything now. Two consequences worth stating plainly:

- **`CLKDIV=1` is still not simulated faithfully**, and cannot be inside 0037's coupling: real
  hardware runs 1-3 PIO instructions per CPU instruction there, and this runs one. What changed is
  that every divider *above* that is now right, which is the entire class of pulse-width protocols.
- **A CPU idle jump still costs PIO its backlog.** The idle branch jumps straight to the next
  scheduled alarm - a millisecond, i.e. 125,000 system clocks, is ordinary - and one instruction
  per `advance()` can never pay that off. Rather than let a machine then run flat out for the next
  125,000 CPU instructions working off a debt it accrued while the CPU was asleep, a backlog beyond
  `MAX_ARREARS_FP` (8 system clocks - more than any single CPU instruction, far less than any idle
  jump) is written off and the machine re-armed from the current cycle, so its *rate* stays right.
  `RPPIO.backlog_drops` counts these. This is the same ceiling PIO always had here (an idle jump
  was worth exactly one PIO step before this change too), now explicit and countable.

One smaller thing the idle branch needed: an idle jump with **no** alarm scheduled at all advances
simulated time by nothing (`nanos_to_next_alarm` is 0), so the cycles handed to `advance()` are
floored at one. Without that floor a PIO fed only by DMA, with no ARM program running at all, can
never make the progress that would let anything wake the CPU up again - `tests/test_pio.py`'s own
0043 regression test is exactly that shape, and it deadlocked until the floor went in.

### Measured

**0062's acceptance test passes.** Real CircuitPython `10.2.1` on the YD-RP2040 board, guest
running `neopixel_write(board.NEOPIXEL, bytearray([0xFF, 0x00, 0xAA]))`, captured as raw GPIO23
edges - the same capture that produced this record's opening trace:

| | before | after | real hardware |
|---|---|---|---|
| `0` bits | 8-16 ns | **272-352 ns** | 312 ns |
| `1` bits | 8-40 ns (*overlapping* the above) | **664-720 ns** | 703 ns |
| bit period | ~24-40 ns | **~1250 ns** | 1250 ns |

Not merely a restored 4:9 ratio: the widths are the **absolute nanosecond values real silicon
produces**, because pacing PIO by system clocks makes 12.8 MHz mean 12.8 MHz. The residual ±40 ns
is the CPU instruction the edge lands inside (1-3 system clocks, and the arrears write-off keeps it
from accumulating) - a seventh of the ~190 ns margin either side of the decision threshold.
`Ws2812` decodes the frame as wire bytes `ff 00 aa`, i.e. `(r, g, b) = (0x00, 0xff, 0xaa)` through
its `GRB` order. Booting the same board with no guest code decodes the status LED cleanly too -
alternating `(0, 0, 0)` and `(11, 11, 11)` frames, where before the fix nothing decoded at all.

**CYW43 live, both firmwares, and it got faster.** `tests/micropython/main-cyw43.py` on
`RPI_PICO_W-1.23.0` and `RPI_PICO_W-1.28.0`, plus `tests/circuitpython/main-cyw43.py` on
CircuitPython `10.2.1` for Pico W: all three complete - scan, join, DHCP, a real TCP connection
through the NAT bridge, DNS, TLS (`1.28.0`), disconnect. Timed on a scan+join+DHCP script:

| | wall clock | simulated time the guest measured |
|---|---|---|
| before | 16.9 s | 829 ms |
| after | **15.0 s** (~11% faster) | 841 ms (+1.4%) |

Both directions are the honest ones: **cheaper** in wall clock, as predicted - a single integer
compare replaces four `step()` calls plus a `_has_runnable_machine()` scan per CPU instruction, and
`CLKDIV=2` halves what survives that compare - and **slower in simulated time**, because CYW43's
gSPI clock is now the `sysclk/2` its driver actually asked for instead of twice that. A CPU-only
workload (300k-iteration guest loop, no PIO enabled) is unchanged at 100.8 s vs 100.1 s, i.e.
inside the noise: the `pio.stopped` short-circuit is untouched.

`tests/test_pio_clkdiv.py` holds the arithmetic itself - integer, fractional and `INT == 0`
dividers, `[delay]`, the two composed, the one-instruction-per-call ceiling, the arrears write-off,
`CLKDIV_RESTART`, re-enable, and a stalled machine costing nothing until something unstalls it -
one system clock at a time, on both twins.

### Still open

- **`CLKDIV=1` fidelity**, as above: bounded by 0037's coupling, not by this record.
- **PIO does not run during a CPU idle jump**, as above. A `MAX_ARREARS_FP` write-off is visible in
  `backlog_drops` when it happens; it did not fire once during either CYW43 boot or the WS2812
  capture, because those drivers busy-wait rather than sleep.
- The fourth option this record rejected (precompute a pin-change schedule instead of simulating
  idle cycles) stays rejected and stays available as a future special case inside the due-time
  model, which is now real.
