# 0093. CircuitPython 8.0.2 never comes back after a chip reset

- Status: **Open - investigated, not root-caused (2026-08-20).** A record rather than a
  `docs/tasks/` working note **by the maintainer's call**, deliberately against this project's own
  convention (which says an un-root-caused investigation lives in `tasks/` until it is resolved) -
  so the numbering is stable while it sits. Nothing here is a plan; it is what was ruled out, what
  was measured, and the one experiment left.
- Conceived: 2026-08-20
- Related: [0089](0089-one-reset-for-every-trigger.md) (the reset umbrella - its Phase 2 first hit
  this, and its **Phase 5 was predicted to fix it and did not**), [0050](0050-qspi-pad-reset-values.md)
  (the same shape of bug, root-caused: a CircuitPython version that would not boot, fixed by pad
  reset values), [0035](0035-board-aware-fs-flash-offset.md) (wild execution as a failure mode
  here)

## Symptom

`python tests/hard_reset_run.py --circuitpython --image 8.0.2` times out: the device resets, the
firmware starts, and it never re-enumerates over USB. `ci-circuitpython.yml` gates the hard-reset
and RESET-button steps on `hard_reset: true` for exactly this reason.

**The boundary is between 8.x and 9.x**, re-measured with the same script on 2026-08-20 rather than
carried over from an earlier session:

| version | `hard_reset_run.py --circuitpython` |
|---|---|
| 8.0.2 | **times out** - never re-enumerates (tested to 90 s; the 120 s CI cap does not help) |
| 9.2.9 | passes, reports `microcontroller.ResetReason.RESET_PIN` |
| 10.2.1 | passes, reports `microcontroller.ResetReason.RESET_PIN` |

So this is not "CircuitPython versus this emulator" - it is one major version. Whatever changed in
CircuitPython's early boot between 8.x and 9.x is where the answer is, and narrowing *which* 8.x/9.x
release first works would cost one run per candidate.

## What it is not

Each of these was measured, not reasoned about, and each kills an obvious hypothesis:

- **Not a poll-loop hang on an unimplemented register.** With the device's logger replaced by a
  capturing one, the emulator emits **26 complaints in 30 s** of being stuck - all `PLL_SYS`/
  `PLL_USB`/`SSI` "unimplemented peripheral" lines, none of them repeating. The CPU is executing
  code the emulator is perfectly happy with.
- **Not SRAM contents surviving the reset** (0089's D6). Zeroing all 264 KB immediately after
  `hard_reset()` changes nothing: same region, same non-return.
- **Not the reset-cause registers.** `hard_reset(cause=ResetCause.POWER_ON)` leaves `REASON=0`,
  `SCRATCH=[0]*8`, `CHIP_RESET=0x100` (`HAD_POR`) - byte-identical to a cold boot. Still stuck.
  (0089's Appendix already reported the same for `cause=WATCHDOG`.)
- **Not slowness.** It is genuinely slow - see the rate below - but the thing it is doing does not
  finish in 90 s of wall clock either, where the whole cold boot takes 16 s.
- **Not flash work.** SSI access counters freeze at 136 reads / 98 writes and stay there for 26+ s,
  and a CRC of the CIRCUITPY region never changes. It touches no peripheral at all while stuck.

## What it is doing, measured

Timeline after `hard_reset()` (native build, PC sampled every 20 ms):

| window | where | what |
|---|---|---|
| t+0 → 2.2 s | flash `0x100378aa`-`0x100378b4` | a `busy_wait_until()`, and it **completes** |
| t+2.3 → 15.1 s | flash around `0x1006f1c0` | ~13 s, not investigated further |
| t+15.2 s → ∞ | **SRAM `0x20003400`-`0x20003a00`** | ~75% of all samples, indefinitely (tested to 90 s) |

The first window is decoded from the instruction bytes rather than guessed - it is pico-sdk's
`busy_wait_until()` verbatim:

```
100378aa  51 6a  LDR r1,[r2,#0x24]   ; TIMERAWH   (r2 = 0x40054000, TIMER_BASE)
100378ac  99 42  CMP r1,r3           ; r3 = hi_target = 0
100378ae  02 d1  BNE +4
100378b0  91 6a  LDR r1,[r2,#0x28]   ; TIMERAWL
100378b2  a1 42  CMP r1,r4           ; r4 = lo_target
100378b4  f9 d3  BCC -14
```

`r0 = 2320776`, `r4 = 2520776` - exactly 200 000 µs apart, i.e. a `busy_wait_us(200 ms)`. It is a
red herring: it finishes, and a later sample shows simulated time 5.9 s past its target.

**The decisive measurement: a cold boot never enters that SRAM region at all.** Sampling the same
range across a full cold boot gives **0 of 534 samples**, against 1485 of 1979 after a hard reset.
So this is not a slow version of the normal path - it is a path the firmware only takes on a warm
start.

## The remaining hypothesis, untested

With SRAM contents and the reset-cause registers both excluded, what still differs between this
emulator's cold-boot path and its warm-reset path is short:

1. **`cdc.reset()` / `RPUSBController.reset()`** - the warm path calls them; a cold boot has never
   enumerated at all. `RPUSBController.reset()` is a hand-written register reset, so an incomplete
   one is plausible. **The experiment that was queued and not run**: perform
   `mcu.reset(preserve_flash=True)` + `core.pc = FLASH_START_ADDRESS` *without* `cdc.reset()`, and
   check whether the PC still ends up in `0x20003400`-`0x20003a00` (success criterion: it leaves
   that region, since `on_device_connected` cannot fire while the host side stays stale).
2. **Simulated time is not zero.** A cold boot starts at `clock.nanos == 0`; a warm reset happens
   ~2.3 s in, so every `time_us_64()` the firmware reads is already large. Nothing here resets the
   simulation clock, and nothing should - but 8.0.2 reading a large boot time is a real difference
   from every cold boot this project has ever tested.

## Cost, for whoever picks this up

Each experiment is a full CircuitPython boot (~16 s) plus the observation window, and the stuck
phase runs at **~131 ms of simulated time per wall-second** in the native build (~8× slower than
real time) because a busy-wait has no WFI for `execute_batch()`'s idle jump to exploit. Budget
accordingly, and prefer bounded windows with a clear success criterion over "wait and see" - a
900-second run tells you nothing a 45-second one with the right probe does not.

## 2026-08-20: three emulator bugs found on the same path - and 8.0.2 still does not come back

Chasing the CI reds that 0089's Phase 1/2 steps produced turned up three real defects in the
emulator, all on the reset path this record lives on. Two of them fixed MicroPython, **none of
them fixed 8.0.2**, and that is the useful part: the shape this record describes is not any of
them.

1. **`Timer32PeriodicAlarm` re-armed at a zero delta.** A watchdog *timeout* fired forever at one
   instant inside a single `clock.tick()`, so simulated time stopped. MicroPython 1.16/1.17 reach
   `machine.reset()` through that path (pico-sdk 1.2.0 reboots by loading a 50 ms timeout rather
   than writing `CTRL.TRIGGER`), so those two versions never reset at all.
2. **Double-buffered USB endpoint writes went out in the wrong order** (buffer 1 before buffer 0),
   scrambling any response longer than 64 bytes.
3. **`SIE_STATUS.CONNECTED` was a latch rather than a level.** pico-sdk's own
   `rp2040_usb_device_enumeration_fix()` busy-waits on that bit from inside a timer alarm
   (`TIMER_IRQ_3`, IPSR 19 - confirmed by disassembling the stuck image), while TinyUSB's ISR
   clears the connect status from the other side. After a chip reset the two race the wrong way
   round, the bit never comes back, and the firmware spins there forever: the device re-enumerates
   as far as `SET_ADDRESS` and then answers nothing. **This is a genuine
   "never comes back after a reset" mechanism** - the same sentence as this record's title - and
   it is now fixed.

**Measured after all three: `tests/hard_reset_run.py --circuitpython --image 8.0.2` still fails
with `TimeoutError: did not complete within 120.0s` on `ahard_reset()`.** MicroPython 1.16, 1.17
and 1.19.1 pass the equivalent checks.

So this record stays open, and one plausible hypothesis is now ruled out rather than merely
untested: 8.0.2's failure is not the enumeration-fix deadlock, not the alarm freeze, and not the
packet reordering. What has *not* been checked since the fixes is whether the failure signature
moved - it is worth re-running the instrumentation that found the above (a `SIE_STATUS` poll trace
plus the core's `pc`/IPSR while it hangs) against 8.0.2 specifically, since the tooling now exists
and the three known-bad mechanisms are out of the way.

## 2026-08-20 (later): SRAM ruled out by experiment, and where the warm boot actually goes

Three measurements, all on `tests/hard_reset_run.py --circuitpython --image 8.0.2` or the
instrumentation around it.

**1. The core runs; it just never reaches USB.** After the reset the pc keeps moving and simulated
time keeps advancing - this is not a hang in the emulator's sense. What is missing is any contact
with the USB block at all: instrumenting `RPUSBController.read_uint32`/`write_uint32` counts
**zero** accesses after the reset, so the firmware never gets as far as initialising USB, and the
host's `on_usb_enabled` (which is what `ahard_reset()` waits behind) can never fire. Contrast
MicroPython, where "firmware enabled USB" lands within a tenth of a second of the reset.

**2. Execution ends up in SRAM.** Sampled pc after the reset: `0x100378b0` → `0x1006f1f4` →
`0x200038dc` → `0x20003666`, i.e. it leaves flash for the SRAM region and stays there. This
record's own "a cold boot never enters the SRAM region it gets stuck in" now has the addresses.

**3. SRAM *contents* are not the cause.** The obvious hypothesis - the emulator deliberately does
not clear SRAM (0089's D6, matching hardware), so 8.x trips over something the previous session
left - is **wrong**, and this is an experiment rather than an argument: zeroing all 264 KiB inside
`_enter_reset()` changes nothing, `ahard_reset()` still times out after 120s. So whatever diverges
is not stale RAM state.

### Method note, for whoever picks this up

Sampling the pc from a **simulation-clock alarm** (`clock.create_alarm()`, rescheduled every 0.5 ms
of simulated time) rather than from wall-clock polling is what makes a cold boot and a warm boot
comparable: both traces are then taken at the same simulated instants, on a clock that stops when
the emulator does. A cold boot produces ~4650 samples over 2.3 s of simulated time.

What that showed at region granularity:

| | cold boot | after the reset |
|---|---|---|
| pc range | `0x0000002a` .. `0x1006fb16` | `0x10000216` .. `0x20003a00` |
| bootrom (`< 0x4000`) | yes | never |
| SRAM (`0x2000_0000+`) | never | yes |

The cold boot's excursions below `0x4000` are the firmware calling **bootrom** helpers (the ROM
table's flash/memory routines); the warm boot never calls one. Both start from the same place -
`_leave_reset()` points the core at `FLASH_START_ADDRESS`, exactly as `_aconnect()` does on a cold
boot - so the divergence is in what the code *finds* when it gets there, not in where it starts.

### The divergence, to the millisecond

Both traces run together through early init (`0x100378xx`) and both reach the same high-flash
region at ~200 ms. Then, at **the same instant**, they go different ways:

| | sample | simulated time | pc |
|---|---|---|---|
| cold boot enters the **bootrom** | 2401 | 1201.00 ms | `0x00002440` |
| warm boot enters **SRAM** | 2402 | 1201.33 ms | `0x2000398c` |

The cold boot never executes a single instruction in SRAM; the warm boot never executes one in the
bootrom. One third of a millisecond apart, out of a 2.3-second boot - so this is not drift, it is
one call going to a different address.

That shape says **a call through a pointer**: at that point the firmware picks a routine and jumps
to it, and after a reset it picks a RAM-resident one where a cold boot picks a ROM one. The SDK
does exactly this around flash access - `rom_func_lookup()` for the bootrom's flash helpers, versus
`.ramfunc` copies that must not execute from XIP while the flash is busy - and ~1.2 s into a
CircuitPython boot is when it is mounting (or formatting) CIRCUITPY.

**Next steps, in order:**

1. Dump the SRAM around `0x2000398c` at the moment it hangs and disassemble it (capstone; the
   bytes are not in the `.uf2`, they are whatever the firmware copied there), to see what that
   routine polls.
2. Count peripheral accesses *by block* during the warm boot the way the USB ones were counted -
   the suspicion is `SSI`/`XIP_CTRL`, which 0089's Phase 5 resets on a RUN-pin reset, since a
   RAM-resident flash routine waiting on SSI status would spin exactly like this.
3. Only then decide whether the defect is the reset (resetting something boot2 does not restore)
   or the XIP/SSI model itself.

## 2026-08-20 (later still): boot2 completes, SSI is innocent, and the spin touches no peripheral

Following the plan above, in order - and the suspicion in step 2 turned out to be **wrong**, which
is the useful part.

**Which blocks the warm boot talks to at all** (counted per peripheral, over 40 s of being stuck):

    SSI@18000        read 136, write 98
    XIP_CTRL@14000   read 4,   write 4
    WATCHDOG@40058   read 3,   write 2
    USB@50110        0

**But that SSI traffic is not a spin - it is boot2, and it completes.** The last SSI exchange while
stuck is byte-for-byte the sequence a cold boot ends on:

    wr BAUDR=0x4 | wr CTRLR0=0x00070000 | wr SSIENR=1 | wr DR0=0x35 | wr DR0=0x35 | rd SR=0xe
    | rd DR0=0xff | rd DR0=0x02 | wr SSIENR=0 | wr CTRLR0=0x005f0300 | wr SPI_CTRLR0=0x00002221
    | wr SSIENR=1 | wr DR0=0xeb | wr DR0=0xa0 | rd SR=0xe | rd DR0=0x02 | wr SSIENR=0
    | wr SPI_CTRLR0=0xa0002022 | wr SSIENR=1

That is `boot2_w25q080`: read status register 2 (`0x35`), configure quad I/O (`0xeb`), then hand
the flash to XIP continuous-read mode (`SPI_CTRLR0=0xa0002022`). It runs, it finishes, and XIP
works afterwards - the warm boot goes on executing from flash (`0x100378xx`, `0x1006f1xx`), which
it could not do otherwise. **So the XIP/SSI model is not the defect, and step 2's hypothesis is
dead.**

**What is left is a spin that touches nothing.** While stuck, the pc cycles through a handful of
flash addresses plus SRAM (`0x100378b2`, `0x100378ac`, `0x1006f118`, `0x200034fe`), IPSR is **0**
(thread mode, so not inside a fault or interrupt handler), simulated time advances, and essentially
no MMIO happens - a few hundred accesses in 40 s, all of them the boot2 sequence above. A loop with
no peripheral reads in it is waiting on **memory**, not on hardware: a flag some interrupt should
have set, or a value it expects the previous stage to have left behind.

That reframes the search away from "which register do we model wrong" and towards "which write
does the warm boot not see". Worth trying next, in this order:

1. Disassemble the SRAM routine at `0x200034fe` (dump `mcu.sram` around it - the bytes are not in
   the `.uf2`) and the flash loop at `0x100378ac`, which together are the whole spin.
2. Check whether an interrupt is *pending but never taken* - `core.pending_interrupts` vs
   `enabled_interrupts` while stuck. `PPB.reset()` runs on every reset path, so NVIC enables are
   cleared; if firmware re-enables them by a route we do not model, an ISR-set flag never moves.
3. Only then go back to the register level.

## Correction (2026-08-20): the spin *is* polling a peripheral - the TIMER

The section above says the stuck loop "reads no peripheral at all". **That is wrong, and the error
was in the instrumentation, not the emulator**: the per-block counter watched SSI, XIP_CTRL, USB
and the watchdog, and the TIMER (`0x40054`) was not on the list - so its hundreds of thousands of
reads were counted as zero.

Disassembling the flash half of the spin says what it actually is:

    0x100378aa  ldr r1, [r2, #0x24]   ; TIMERAWH
    0x100378ac  cmp r1, r3
    0x100378ae  bne 0x100378b6
    0x100378b0  ldr r1, [r2, #0x28]   ; TIMERAWL
    0x100378b2  cmp r1, r4
    0x100378b4  blo 0x100378aa        ; loop until the 64-bit time reaches the target

That is pico-sdk's `busy_wait_until()` / `time_reached()` on `TIMERAWH`/`TIMERAWL`, which is a
perfectly ordinary thing for firmware to be doing - so "it is waiting on memory" was the wrong
inference too. Both corrections come from the same missing watch entry.

### What the timer actually does after a reset: it restarts, and it runs

| | TIMERAW | simulated clock |
|---|---|---|
| cold boot, sampled | 0.347 s → 0.697 s → 1.034 s | identical |
| cold boot, at enumeration | **2.323 s** | 2.32 s |
| after the reset | 0.069 s → 0.418 s → 0.779 s → 1.119 s | 2.39 s → 3.44 s |

So the TIMER restarts from zero (0089's Phase 5 follow-up made it do exactly that) and then
advances in step with the simulation clock. Nothing is frozen.

### And that kills the "it is just slow" theory too

Given a **900-second** budget instead of the usual 120, the warm boot reaches **13+ seconds of
guest time** - five times what a cold boot needs to enumerate - and still does not come up. Its pc
by then is in SRAM (`0x200038a8`, `0x200039ee`, `0x20003826`), cycling through a range rather than a
tight three-instruction loop.

So the state of the investigation is: **the warm boot completes boot2, keeps XIP working, keeps
time running, and then loops inside a routine in SRAM for as long as it is given.** The next step
is unchanged and now unavoidable - disassemble `0x20003780..0x20003a80` out of the emulator's SRAM
(the bytes are whatever the firmware copied there, so they are not in the `.uf2`) and read what
that loop is waiting for.

## 2026-08-20 (final for today): it is wild execution into blank SRAM

Dumped the 768 bytes of SRAM the warm boot keeps executing in (`0x20003780..0x20003a80`, taken out
of the emulator after 75 s of being stuck - those bytes are not in the `.uf2`, so this is the only
way to see them) and disassembled it.

**66 of the 768 bytes are non-zero.** The "hot" addresses decode as `movs r0, r0` - because
`0x0000` *is* that instruction. The warm boot is not sitting in a routine that waits for something.
It is sliding through **blank memory**: a NOP sled, which is what an ARM core does with zeroed RAM
until it hits a word that decodes into a branch.

That also explains the flash addresses that looked like a loop. Disassembled, they are ordinary
library code, hot because everything uses them:

- `0x1006f100..0x1006f13a` - the SDK's hardware-divider helper. It saves and restores
  `DIV_UDIVIDEND`/`DIV_UDIVISOR`/`DIV_QUOTIENT`/`DIV_REMAINDER` (SIO `+0x60/0x64/0x70/0x74`) around
  a call and spins on `DIV_CSR` (`+0x78`) bit 0 with `ldr r5,[r6,#0x78]; lsrs r5,r5,#1; blo` - the
  READY wait.
- `0x100378aa..0x100378b4` - `busy_wait_until()`, as the correction above established.

So the shape of the failure is: **normal firmware code → a jump through a pointer that lands in
blank RAM → a NOP sled → back into flash → round again**. Not a deadlock on a peripheral; a wild
branch, which is the failure mode [0035](0035-board-aware-fs-flash-offset.md) is about and which
this record's own "Related" line already pointed at.

**The one thing left to catch is the wild jump itself**: the flash instruction immediately before
the pc first enters SRAM. Today's traces sample every 0.5 ms of simulated time, which is ~60 000
instructions - far too coarse to name it. The way to get it is a much finer alarm (10 us, ~1 250
instructions) recording *transitions* only: keep the previous pc, and when the current one is in
SRAM and the previous was in flash, record the pair. That names the call site, and the call site
names what it read to get the address.

Worth stating for whoever does that: the value it jumps to is very likely something the reset left
in a place a cold boot fills in - which brings this back to `_enter_reset()`'s scope, but from the
other end than the last three attempts.

### The jump, caught

Re-run with a 10 us alarm recording only flash->SRAM transitions (the recipe above), first one on
the warm boot:

    transition #1: flash 0x100378b2 -> SRAM 0x20000250, at simulated 2.5230 s

`0x100378b2` is inside `busy_wait_until()` - the `cmp r1, r4` of the TIMER wait loop. So the wild
branch happens **on the way out of that wait**, not on the way in, and it lands at `0x20000250`:
low SRAM, in the region a firmware puts its relocated vector table and `.ramfunc` copies, and one
that this image evidently never filled (the dump above is blank there).

Two caveats to carry forward rather than lose:

- 10 us is still ~1 250 instructions, so `0x100378b2` is "the last place it was seen", not
  provably the instruction that branched. The next refinement is 1 us plus the core registers -
  `lr` and `sp` at the transition name the caller and let the stack be read back.
- A return-out-of-a-wait going wild has a small set of causes worth checking in order: a corrupted
  return address on the stack, an exception taken with a vector table that points into unpopulated
  RAM (`PPB.reset()` zeroes `VTOR` on every reset path - if the firmware's re-relocation does not
  happen, every vector reads as blank RAM), or a `.ramfunc` pointer resolved before the copy that
  fills it.

The middle one is the first to test, because it is the one this project's reset path can plausibly
break: `VTOR` is reset by us, and what refills it is firmware code that a cold boot runs and a warm
boot may not reach in the same order.

## Correction (2026-08-20, second): low SRAM is fine, and 0x20000250 is real code

Two hypotheses from the sections above are now dead by measurement, both of them mine.

**VTOR is relocated identically on both boots.** Instrumenting writes to it:

    cold: firmware writes VTOR = 0x10000100, then VTOR = 0x20000000
    WARM: firmware writes VTOR = 0x10000100, then VTOR = 0x20000000

So "the warm boot never re-relocates the vector table" is wrong, and with it the reasoning that
`PPB.reset()` zeroing VTOR is what breaks this.

**And low SRAM is populated after the reset, byte for byte.** Comparing `0x20000000..0x20000400`:

| | non-zero bytes | `[0x20000250]` | vectors |
|---|---|---|---|
| cold boot | 812 / 1024 | `1b88 9847 8046 fff7 33ff b847 a847 fff7` | sp=`0x20042000` reset=`0x100001f7` |
| after the reset | 808 / 1024 | **identical** | **identical** |

So the jump the previous section caught - flash `0x100378b2` into SRAM `0x20000250` - is not a wild
branch at all: `0x20000250` is real, correctly-copied code on both boots (`ldrh r3,[r3]; blx r3;
mov r8,r0; …` - a trampoline that calls through registers). The firmware is *supposed* to go there.

**What survives from the blank-SRAM finding** is narrower than it was written: the region actually
dumped blank was `0x20003780..0x20003a80`, and the pc samples proven to be inside it are
`0x200038a8`, `0x2000398c`, `0x200039ee` and `0x20003a00`. `0x200034fe` and `0x20003826`, also
sampled, are *below* that window and were never dumped - so they are unproven either way.

So the corrected picture is: the warm boot calls legitimate RAM code at `0x20000250`, that code
calls through registers (`blx r3`/`blx r7`/`blx r4`), and execution *later* ends up in a genuinely
blank region a few kilobytes further up. The wild branch is **downstream of `0x20000250`**, inside
that trampoline, and the thing to catch is the register it calls through.

**Next step, precisely:** the same 10 us transition capture, but triggering on the pc entering
`0x20003400..0x20003a80` (the blank window) rather than SRAM in general, and recording `lr`, `sp`
and the low registers at that moment. That names which pointer was zero - and *that* is the value
whose provenance decides whether this record ends in the emulator's reset path or in 8.x's own
startup ordering.

