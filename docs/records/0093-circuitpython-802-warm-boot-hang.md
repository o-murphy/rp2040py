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

