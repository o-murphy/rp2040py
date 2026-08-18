# 0053. Second core (core1) and the inter-core FIFO

- Status: **Proposed — core1/the real FIFO not implemented.** The "Interim option" below landed
  2026-08-18 (a clearer read-side warning only); see that section's own addendum. Everything else
  in this record is still documentation only, no code.
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

**Implemented, 2026-08-18.** `sio.py` now names `FIFO_ST`/`FIFO_WR`/`FIFO_RD` (`0x50`/`0x54`/`0x58`)
explicitly in `read_uint32()` - a read there still returns `0xFFFFFFFF` (unchanged behavior, still
not the real FIFO), but logs "Inter-core FIFO (0x50-0x58) is not implemented. core1/_thread is
unsupported (see docs/records/0053-core1-and-inter-core-fifo.md)" instead of the generic "Read from
invalid SIO address: 50" a caller would otherwise have to trace back to this gap by hand. Writes
are untouched (still silently dropped, as before) - the record's own interim proposal was read-side
only, since that is the half `multicore_fifo_pop_blocking()`'s handshake actually blocks on.
`tests/test_sio.py::test_reading_inter_core_fifo_addresses_names_0053_instead_of_generic_invalid_address`
covers it. Nothing else in this record changed: core1 itself, the real FIFO pair, and the
scheduling/addendum sections above remain proposed, not built.

## Addendum, 2026-08-17: how core1 should be *executed* - and why not on its own thread

Raised while discussing [0063](0063-pio-clkdiv-and-delay-cycles.md): if a second core is added,
should each core run in its own OS thread? Written down here because it is the first question
anyone building this will ask, and the answer decides the shape of everything else.

**No - and the reason that matters is not the GIL.** The GIL is the obvious objection (two threads
interpreting Python do not run in parallel; they timeshare one core and add switch overhead and
contention), and a free-threaded build removes it - this project already tests `cp314t`. But the
second reason survives that removal:

**Everything in this emulator is coupled through one clock.** Every instruction ticks
`SimulationClock`; DMA, PIO, alarms, GPIO listeners and every peripheral hang off it. Two threads
stepping two cores would have to agree on emulated time at essentially every shared access, and a
lock per instruction costs more than the instruction it protects. This is the standard result from
parallel discrete-event simulation: parallelism pays only when components are loosely coupled
enough for lookahead (conservative) or rollback (Time Warp), and a tightly-coupled SoC model in a
Python interpreter loop is the opposite of that.

Two further costs, both real here rather than theoretical:

- **Determinism.** Single-threaded stepping is reproducible; two threads are not. This project
  tests that a flash dump is byte-identical across runs, and debugging a firmware hang depends on
  the run being repeatable.
- **The `ExternalDevice` contract.** Device callbacks fire on the engine room's own thread by
  construction ([0030](0030-external-device-concurrency.md)); two CPU threads would mean device
  state touched from two, which is exactly the concurrency model that record exists to avoid.

The project's own trajectory says the same: [0025](0025-full-asyncio-migration.md)/
[0026](0026-main-thread-asyncio.md) moved *away* from running execution on a background thread, and
`CLAUDE.md` still warns that a leaked engine-room thread busy-loops a whole core. Threads here earn
their keep on **I/O** - the asyncio loop, sockets, the NAT bridge - not on interpretation. CPU speed
comes from Cython and PyPy ([0013], [0031], [0034], [0039], [0047]), and always has.

**The model to build instead: interleaved stepping in the same loop**, sharing one clock - exactly
how `RPPIO` is already stepped since [0037](0037-pio-clock-coupled-stepping.md). Step core0, step
core1, tick, repeat. Then:

- the inter-core FIFO this record is about needs **no locking at all** - there is one thread, so it
  is a plain data structure, and `FIFO_ST`/`FIFO_WR`/`FIFO_RD` become ordinary register reads;
- determinism is preserved, and so is every existing assumption about who touches what;
- the cost is roughly 2x the work per second of emulated time, which is honest - you are emulating
  two cores - with no synchronization overhead added on top.

The one genuinely open knob is the **quantum**: alternating per instruction is the most faithful
and the slowest; interleaving in blocks of N is faster but lets one core run ahead of the other,
which is observable through precisely the FIFO and spinlocks this record is about. That is a
semantics decision to make deliberately, not a threading one.

Prior art agrees on the shape: QEMU's default TCG is a single-threaded round-robin over vCPUs, and
its multi-threaded MTTCG mode works because it emits native code with real atomics and memory
barriers - neither available to a Python interpreter loop. `rp2040js`, this project's ancestor, is
single-threaded by construction.

None of this changes what this record already concluded: the hazard is semantic, not architectural.
A FIFO that looks faithful but is not turns today's honest "core1 is not emulated" warning into a
silent infinite hang, and that stays true whichever way the cores are scheduled.
