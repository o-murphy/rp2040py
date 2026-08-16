# 0053. Second core (core1) and the inter-core FIFO

- Status: **Proposed — not implemented.** Documentation only; no code is changed by this record.
- Conceived: 2026-08-16
- Related: 0050 (where the missing FIFO was hypothesised, checked and *ruled out* as the cause of
  the CircuitPython 10.x stall), 0026/0025 (the asyncio engine-room model any second core would
  have to live inside)

## What is missing today

`sio.py` implements CPUID, the GPIO views, the hardware divider, the interpolators and the 32
spinlocks - but **not** the inter-core FIFO registers: `FIFO_ST` (`0x50`), `FIFO_WR` (`0x54`),
`FIFO_RD` (`0x58`). Reads of those offsets fall through to the block's own
`"Read from invalid SIO address"` warning and return `0xFFFFFFFF`; writes are dropped.

There is also no second core: `RP2040` constructs one `CortexM0Core`, and `CPUID` therefore always
reads `0`.

## Why *not* to implement the registers on their own

This is the part worth writing down, because "just add three registers" looks like the obvious
cheap win and is actively the wrong move.

`multicore_launch_core1()` does not merely push to the FIFO - it runs a **handshake**: push a
sequence, then block reading back an echo of what it pushed, retrying until the sequence matches.
With no second core to answer, a faithful-looking FIFO turns today's honest failure (a warning, and
an all-ones read that firmware will likely trip over quickly) into a **silent infinite block** in
`multicore_fifo_pop_blocking()`. The emulator would look healthy while hanging forever, which is
strictly worse for whoever hits it - and precisely the shape of bug this project has burned
sessions on already (0041, 0044, 0050).

So the registers are only worth adding **together with** a core1 that answers them.

## What implementing it actually involves

Rough shape, not a design:

- A second `CortexM0Core` sharing the same bus, with `CPUID` reflecting which one is executing.
- Scheduling: the engine room currently steps one core in `_execute_batch()`. Two cores means
  interleaving them deterministically (some fixed instruction quantum), since real parallelism is
  not available and non-determinism would make failures unreproducible - a property this project
  has leaned on repeatedly when root-causing.
- The FIFO pair itself (two 4-entry FIFOs, status flags, the `ROE`/`WOF` sticky error bits) plus
  the `SIO_IRQ_PROC0`/`PROC1` interrupts.
- The bootrom's own core1 wait path, so `multicore_launch_core1()`'s handshake terminates the way
  real hardware does.
- Spinlocks are already implemented, but their semantics under two real cores need re-checking.

Cost is dominated by the scheduling decision and by the interpreter core being the hottest code in
the project (0013/0034/0039/0047 all exist to make it faster) - a second core must not slow down
the single-core path that every current user actually runs.

## Trigger for doing it

Nothing today needs it: CircuitPython does not launch core1 on RP2040, and neither MicroPython's
nor CircuitPython's boot path touches the FIFO - confirmed indirectly by the absence of any
`"Read from invalid SIO address"` warning in the debug logs gathered for 0050.

The concrete trigger is **MicroPython's `_thread` module**, which on the rp2 port runs the second
thread on core1. Any user script doing `import _thread; _thread.start_new_thread(...)` hits the
missing FIFO today. That is the case to build this for, and the case to write the first test
against.

## Interim option, if this stays unbuilt

Make the three FIFO offsets *explicitly* unsupported rather than incidentally so: answer reads with
a clear one-time warning naming `_thread`/`multicore` and this record, instead of the generic
"invalid SIO address" line. That is a five-line change and turns a mystery into a message. It is
not implemented here either - noting it so the option is on the table without being mistaken for
the real thing.
