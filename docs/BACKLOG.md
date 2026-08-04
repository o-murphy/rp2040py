# Backlog / in-progress work notes

Working notes for tasks that span multiple sessions. Not user-facing docs — see README.md /
PORTING.md / CHANGELOG.md for those. One item large enough to need its own file:
[docs/JIT_BACKLOG.md](JIT_BACKLOG.md) (basic-block fusion / mini-JIT).

## SSI flash-write support (branch `feat/ssi-rw-support`)

**Goal:** implement real JEDEC SPI-NOR flash command emulation in `RPSSI` so MicroPython can
actually *write* to the emulated flash (currently a register-only stub — see README's "the
filesystem is not writeable, as the SSI peripheral required for flash writing is not implemented
yet"). Confirmed rp2040js has the same gap — this is a new feature, not a porting bug.

### Done, staged (uncommitted on top of `12249b8`)

- `src/rp2040py/peripherals/ssi.py`: full JEDEC command set (WREN/WRDI, RDSR1/2, WRSR,
  PAGE_PROGRAM, SECTOR_ERASE, BLOCK_ERASE, READ_DATA, READ_JEDEC_ID). Command framing keys off
  chip-select (`rp2040.qspi[1]`, QSPI_SS), **not** `SSIENR` — confirmed via `pico-sdk`'s
  `flash_cs_force()` (`hardware_flash/flash.c`) that RP2040 bit-bangs QSPI_SS via the IO_QSPI pad
  override instead of using SSI's own SER/SSIENR ("in case RAM-resident IRQs are still running...
  the bootrom does the same" — SDK's own comment). `_apply_command()` (erase/program) fires on
  chip-select deassert, matching real flash semantics (command only commits once fully clocked
  in).
- `src/rp2040py/peripherals/io.py`: generalized `RPIO` to take an optional `pins=` list, so the
  same CTRL/STATUS/INTR register block class serves both `IO_BANK0` (30 GPIOs, default) and
  `IO_QSPI` (6 dedicated pins).
- `src/rp2040py/rp2040.py`: `IO_QSPI_BASE` (`0x40018`) now a real `RPIO(pins=self.qspi)` instead
  of `UnimplementedPeripheral` — needed so `flash_cs_force()`'s writes to QSPI_SS's `ctrl` actually
  land somewhere. `rp2040.qspi[*]` pins now constructed with `always_output_enabled=True`.
- `src/rp2040py/gpio_pin.py`: added `always_output_enabled` ctor flag. Needed because
  `raw_output_enable` normally derives from `function_select` (SIO/PWM/PIO0/PIO1) — the 6 QSPI
  pins have no such function-select concept, so `flash_cs_force()`'s OUTOVER-only writes had no
  visible effect on `.value` without this.
- `tests/test_ssi.py`: rewritten to drive CS via the QSPI SS pin (`ctrl` output-override force
  low/high) instead of `SSIENR`, matching the corrected framing above. All command-level tests
  (erase/program/status/JEDEC ID/read-data/write-enable-latch semantics) pass against the
  peripheral driven directly.

### Root-caused and fixed — two independent bugs (both in `src/rp2040py/peripherals/ssi.py`)

The hang (`rp2040py micropython`/`kaluma` never reaching REPL) had **two separate root causes**,
both found by tracing a real boot directly against `RP2040`/`CortexM0Core` (bypassing the CLI's
firmware-download path, which can't reach micropython.org/adafruit's S3 bucket from a sandboxed
session — `github.com` releases *are* reachable there, so Kaluma 1.2.1's UF2, from
`kaluma-project/kaluma`'s GitHub release, is what was used to reproduce this) with
`RPSSI.read_uint32`/`write_uint32` monkeypatched to log every call alongside `core.pc`, plus
register snapshots (`core.registers`) at fixed PCs. Cross-checked against the actual bootrom
*source* (`raspberrypi/pico-bootrom-rp2040`, `bootrom/program_flash_generic.c` — public, unlike the
compiled `BOOTROM_B1` blob) rather than guessing from disassembly alone, since disassembly-only
reverse-engineering (the previous session's approach, see below) was slow and inconclusive.

**Bug 1 — `_cs_asserted` initialized to `False`, desynced from the pin's real reset value.**
`rp2040.qspi[1]` (QSPI_SS)'s own `.value` resolves to `LOW` (i.e. *asserted*) immediately after
construction — `always_output_enabled=True` pins with no function-select driving them yet still
resolve through `GPIOPin.value`'s `output_enable` branch, landing on `LOW` (see `gpio_pin.py`).
Hardcoding `RPSSI._cs_asserted = False` in `__init__` didn't match that, so the very first
chip-select assertion ever performed (a plain "force low" with the pin already reading low - no
edge, so `_on_cs_pin_changed` never fires) was invisible to this peripheral: every byte of that
first command was silently dropped by `write_uint32`'s `self._cs_asserted` guard, starving the
bootrom's `flash_do_cmd_cs()`-equivalent (`flash_put_get()`, see bug 2) of the RX bytes it was
waiting for. Fix: `self._cs_asserted = rp2040.qspi[1].value == GPIOPinState.LOW` at construction,
synced to the pin's actual resolved value instead of hardcoded.

This alone fixed all 9 then-failing `tests/test_ssi.py` cases (every one of them drove a single
command from a fresh `RP2040()` — exactly the "first-ever" case this bug broke) but, on its own,
did **not** fix the real boot hang - confirmed via a Kaluma boot trace that still froze solid at
the same PC range as before.

**Bug 2 — DR0 writes while chip-select is deasserted were dropped entirely, but shouldn't be.**
Tracing further after bug 1's fix showed the *specific* freeze point: `flash_exit_xip()`
(`program_flash_generic.c`) calls `flash_init_spi()` (sets up SSI via `ssi->ser = 1`/`ssienr = 1` -
a legitimate, separate CS-enable path this peripheral doesn't otherwise model) and then
deliberately clocks dummy bytes through DR0 *while chip-select is forced high* (`flash_cs_force
(OUTOVER_HIGH)`) - a real-flash Micron-compatibility dummy-clock sequence, per the source's own
comment. Real SSI FIFO hardware (`TXFLR`/`RXFLR`/`DR0`) is wired independently of the QSPI_SS GPIO
pin - it keeps shifting bytes regardless of chip-select state, since CS here is a purely
software/GPIO-level concern bit-banged on top, not something the SSI shift register itself gates
on. This peripheral's `write_uint32` instead made *all* DR0 FIFO activity conditional on
`self._cs_asserted`, so this deliberate CS-high write vanished - `RXFLR` never incremented, and
`flash_put_get()`'s TX/RX-FIFO-level flow-control loop (`bootrom`'s compiled equivalent of the
loop below) spun forever waiting for bytes that would never arrive. Confirmed by disassembling the
actual stuck PC range (`0x1794`-`0x17d8`) with `capstone` and matching it instruction-for-
instruction against `flash_put_get()`'s compiled shape (the final `tst`/branch at `0x17d4` is
`flash_was_aborted()` checking `IO_QSPI_GPIO_QSPI_SD1_CTRL`'s `INOVER` bits - a debugger-abort
escape valve, not the normal exit path; the *real* exit is the `(tx_count|rx_skip|rx_count)==0`
check at the loop top). Fix: DR0 writes now always advance the FIFO when `SSIENR=1`, regardless of
`_cs_asserted` — chip-selected writes still go through `_shift_byte()`/real command interpretation
as before, but deasserted writes now push a `0xFF` (idle-bus, matching `_shift_byte()`'s own
unrecognized-opcode fallback) into `_rx_queue` instead of nothing, so the firmware-side FIFO
accounting stays consistent either way. Nothing about `_apply_command()`/flash-content semantics
changed - only bytes clocked in while actually chip-selected affect flash state.

**Verified:**
- `tests/test_ssi.py`: 19/19 passing (was 9 failing before bug 1's fix; added 2 regression tests
  for these exact bugs, `test_chip_select_already_asserted_at_reset_is_not_silently_missed` /
  `test_dr0_writes_while_chip_select_deasserted_still_advance_the_fifo`). Full suite: 435/435, no
  regressions.
- Real boot, instruction-level: before both fixes, a Kaluma 1.2.1 boot froze at PC `0x1794`/`0x1798`
  (the `flash_put_get()` loop) with zero forward progress across a 3M-instruction budget. After
  both fixes, the same boot advances through tens of millions of instructions with no repeated/
  stuck PC.
- **Real end-to-end flash read/write, confirmed for both MicroPython and Kaluma:**
  - MicroPython: user-supplied real firmware (1.21.0 and 1.28.0 UF2s - `micropython.org` itself is
    blocked by this sandbox's network policy, `gateway answered 403 to CONNECT`, so these were
    provided directly rather than auto-downloaded) booted to REPL and ran
    `tests/micropython/main-flash-rw.py` (writes a file to the auto-mounted littlefs filesystem,
    reads it back) - printed `FLASH RW OK`. Also green across the *entire* `ci-micropython.yml`
    matrix (8 versions × 3 Python runtimes, including the new flash-rw step).
  - Kaluma 1.2.1 (UF2 fetched from its GitHub release, reachable from this sandbox unlike
    micropython.org): booted with a `--target kaluma`-sized littlefs image and
    `tests/kaluma/index-flash-rw.js` staged as the user program (writes/reads a file via Kaluma's
    own `require("fs")`, a different flash region/filesystem than MicroPython's) - printed
    `FLASH RW OK`. Needed a real second bug fix in the *test script itself* along the way: Kaluma's
    `fs` module has no `writeFileSync`/`readFileSync` (unlike Node) - just synchronous
    `writeFile(path, data)`/`readFile(path)` taking/returning a `Uint8Array`, confirmed against
    `kaluma-project/kaluma`'s actual source (`src/modules/fs/fs.js`, `tests/fs.test.js`). Also
    caught (and fixed) a CI-assertion weakness in the same commit chain: `--expect-text "FLASH RW"`
    matched both the `... OK` and `... FAILED: ...` outcomes, so the buggy script's CI run
    "passed" despite the test failing at runtime - tightened to `"FLASH RW OK"` everywhere
    (matches `ci-micropython.yml`'s equivalent step, which already used the tighter string).
  - Separately noticed and *not* a regression from this work: Kaluma's boot banner ("Welcome to
    Kaluma") prints before its emulated USB-CDC connection is actually up, so `--expect-text
    "Welcome"` is inherently racy (this is already documented in README's Kaluma section) -
    matching a script's own printed output instead (as the flash-rw test above does) is the
    reliable way to check Kaluma results, not the boot banner.
- `mklittlefs --target {micropython,circuitpython,kaluma}` added (mutually exclusive with
  `--block-size`/`--block-count`) to make building a correctly-sized image for each firmware less
  error-prone - this is what the Kaluma flash-rw test above uses instead of spelling out
  `--block-size 4096 --block-count 128` by hand.
- README's "filesystem is not writeable" caveat removed; CHANGELOG updated.

### Not started yet

- Nothing blocking remains for the original goal (real MicroPython + Kaluma flash read/write,
  confirmed end to end above). Possible future follow-ups, not required for this to be considered
  done:
  - CDC wall-clock variance - see its own section below, promoted out of this bullet since it
    warrants real profiling work, not just a footnote.
  - CircuitPython's flash-write path uses the same SSI peripheral but hasn't been separately
    exercised with a dedicated test (CircuitPython doesn't typically write its own filesystem at
    runtime the way MicroPython's `os`/`rp2.Flash` does, so there's less of a natural test to write
    - not treated as blocking).

## CDC (USB serial) performance investigation — root cause found and fixed, one contributor remains

**Goal:** figure out why waiting for text over the emulated USB-CDC REPL (`--expect-text` on
`micropython`/`kaluma`) takes wildly variable wall-clock time run to run - anywhere from well under
a minute to several minutes - even for the *same* firmware/image with nothing else changed.
Noticed while verifying the SSI flash-write fixes above; **not** caused by them - the variance
shows up on plain boots with no flash/littlefs activity at all, so it's a distinct, separate
problem from that work, not a regression from it.

**Root cause #1 (fixed) — `Simulator.execute()`'s idle-tick step accounting, in `src/rp2040py/simulator.py`.**
The loop bounds each `execute()` call to a ~1,000,000-unit budget before yielding back via
`threading.Timer(0, self.execute)` (a real OS-thread handoff, per that function's own NOTE). When
the core is WFI'd (`core.waiting`), it jumps straight to the next clock alarm - which costs
essentially nothing in real time no matter how far away that alarm is - but the old code weighted
that jump by `nanos_to_next_alarm / cycle_nanos` and added it to the same budget as real executed
instructions. USB SOF fires every 1ms of sim-time for the entire life of a connected device
(`RPUSBController._schedule_sof_packet`, `src/rp2040py/peripherals/usb.py`), and at 125MHz
(`cycle_nanos` ≈ 8ns) that 1ms jump alone was ~125,000 "units" - enough to exhaust the whole budget
after only ~8 SOF firings (~8ms of simulated time). Every real device sits WFI'd almost all the time
once booted (waiting on input at the REPL, waiting between interrupts, etc.), so this turned "USB
connected and idle" into a real thread handoff roughly every 8ms of simulated idle time - thousands
of them over the course of a boot-to-REPL wait, each one exposed to real OS-scheduler jitter
(thread-creation latency is small in isolation but highly variable under a loaded/shared host,
e.g. a CI runner), which is exactly the "wildly variable, run to run, nothing else changed" symptom
reported above. Confirmed via an isolated repro (bare `RP2040`/`Simulator`, core forced permanently
`waiting=True`, only a 1ms-recurring alarm active): before the fix, ~300 `execute()` calls /
`threading.Timer` creations were needed to advance 2 real seconds' worth of idle sim-time; after,
just 1. Cross-checked against `_bench_firmware`'s hand-rolled equivalent loop in
`src/rp2040py/cli/__init__.py` (used by `rp2040py bench --image ...`, doesn't go through
`Simulator.execute()` at all) - it already counts an idle tick as exactly one step, same as a real
instruction, so this wasn't a deliberate design choice that `Simulator.execute()` diverged from,
just an inconsistency/bug against the pattern already used elsewhere in this codebase.

**Fix:** `Simulator.execute()`'s idle branch no longer adds `nanos_to_next_alarm / cycle_nanos` to
the budget - it now costs the same 1 unit as everything else (the loop's existing unconditional
`i += 1`). Regression test: `tests/test_simulator.py` (fails with `fire_count == 8` against the old
code, passes - covering >1000 firings in one batch, exactly one `threading.Timer` construction -
after). Full suite: 436/436 (435 + this new test), no regressions.

**Root cause #2 (separate, not fixed, not really fixable here) — raw Thumb-interpretation
throughput for CPU-bound guest code.** Once the guest is actively executing (not WFI'd) - e.g.
MicroPython's `time.sleep()` busy-polls a hardware timer register in a tight loop rather than
WFI-ing - every iteration is a real instruction through this project's pure-Python Thumb
interpreter, and that's just slow, with real (small but nonzero) run-to-run host-speed noise on top
via `RPUSBController.write_delay_microseconds`/etc. This is already documented in README's
MicroPython 1.21-vs-1.28 benchmark section ("identical instruction counts run-to-run... the ~45x
gap is a real difference in how much work 1.28 does per loop iteration, not an emulator bug") -
confirmed independently here by instrumenting a full boot-to-REPL-banner run of MicroPython 1.21:
sim-time-elapsed was identical (~13.6-13.8ms) across repeated runs, but wall-clock varied 0.96s-4.87s
for that identical work, and a follow-up run waiting through a `while True: print(...); time.sleep(1)`
resident script (`tests/micropython/main.py`) spent 61+ real seconds advancing only ~560ms of
sim-time with zero WFI - all real instruction execution, no thread-handoff churn at all (fix #1
doesn't touch this path, and isn't expected to). This piece is inherent to interpreting real
firmware instruction-by-instruction in Python and isn't something to "fix" here beyond what
README's existing PyPy/CPython-3.14-JIT guidance already covers - noted so it isn't mistaken for
leftover work from this investigation.

**Where the "SPI tests finish in seconds regardless of version" observation fits:** that's not
evidence of a distinct USB-specific code path bug (there wasn't one, beyond root cause #1 above) -
`tests/micropython_spi_run.py` watches SPI0 pin callbacks that fire early in boot, before the guest
reaches its CPU-bound resident-script loop, so it's simply exposed to far fewer total instructions
(and far less of root cause #2's noise) than a `--expect-text` test waiting for banner/resident-
script text after full boot.

## MicroPython 1.21-vs-1.28 instruction-count gap — one real fix landed, root cause still not isolated

**Goal:** actually root-cause README/PORTING.md's documented ~45x instruction-count gap (1.21:
1,418,835 steps vs. 1.28: 64,679,599 steps to reach the same script's first `print()`), rather than
leave it at "not isolated further" indefinitely. User supplied both real UF2s
(`RPI_PICO-20231005-v1.21.0.uf2`, `RPI_PICO-20260406-v1.28.0.uf2`) plus a `tests/micropython/
main.py`-based littlefs image (`mklittlefs --target micropython`) to make this reproducible.

**Method:** instrumented `RP2040.core.execute_instruction()` directly (bypassing `Simulator` only
in the sense of driving the same step loop `_bench_firmware` uses, with a `collections.Counter`
keyed on `core.pc` added around it) to get a PC-hit histogram for both versions' full boot-to-
first-`print()` run, then cross-referenced hot PCs against real source: cloned
`raspberrypi/pico-bootrom-rp2040` (public, reachable from this sandbox same as in the SSI
investigation) and disassembled around hot bootrom/flash/RAM addresses with `capstone`
(`pip install capstone` - not a project dependency, installed ad hoc for this investigation only).

**Sanity check confirmed, not contradicted, the existing "boot-to-REPL is fast on both versions"
claim.** Worth recording since the first attempt at checking it was itself wrong: booting both
versions with a *mounted but empty* littlefs (no `main.py`) via `--expect-text ">>>"` made 1.21
appear to hang (timed out at 10M instructions) while 1.28 "found" it almost instantly - looked like
a real, dramatic difference. It wasn't: `--expect-text`'s matcher only checks accumulated output
against a trailing `\n`, and a REPL prompt (`>>> `) is deliberately printed *without* one (it's
waiting for input, not ending a line) - 1.28 only "matched" because its startup happens to print
the prompt twice with a `\r\n` in between (`...\r\n>>> \r\n>>> `), giving the matcher's newline
trigger something to fire on, while 1.21 prints it once (`\r\n>>> `) and never gives the matcher a
second chance. Dumping raw serial output directly (`dump_output.py`, no expect-text matching)
instead of relying on that matcher confirmed both versions reach `>>> ` well within the first
1,000,000 instructions regardless - the documented claim holds fine, this was a test-harness
blind spot (relying on a trailing newline that a REPL prompt doesn't produce), not a real finding
about the firmware.

**Hypothesis 1 (tested, disproved) - 1.28 writes something to flash during boot.** A cluster of hot
PCs sat in RAM (`0x2000xxxx`), which is where `pico-sdk`'s `__not_in_flash_func`-marked flash
erase/program routines normally get copied to run from (real flash can't be read via XIP while
being erased/programmed). Directly falsified: dumped `rp2040.flash`'s littlefs region before and
after a 20M-instruction 1.28 run and diffed byte-for-byte - **zero bytes differ**. Whatever the RAM
code is, it isn't touching flash.

**What the RAM code actually is:** read the PC-relative literal a nearby `ldr r3, [pc, #0x60]`
loads (`0x50110000`) - that's `USBCTRL_REGS`' base address. The loop scans that peripheral's
interrupt-status register bit-by-bit (32 iterations, `tst`/`lsls`-doubling mask, `bl` to a handler
per set bit) - a USB IRQ dispatcher, RAM-placed for latency per normal `pico-sdk` practice, not
flash-related at all.

**Hypothesis 2 (tested, disproved) - 1.28's USB IRQ handling is disproportionately heavier than
1.21's.** Measured each version's total RAM-region instruction share from the same PC histograms:
**1.21: 22.94% of all instructions; 1.28: 28.02%.** Close enough that USB-IRQ-adjacent work scales
roughly *with* the overall ~32x-in-this-sample blowup, not independently ahead of it - so it isn't
the differentiator either. (Both hypotheses being wrong is a normal, useful outcome, not wasted
effort - it narrows down what the real cause *isn't*.)

**Root cause: narrowed down substantially further, still not fully isolated.** Extended the method
above: hooked `execute_instruction()` to record `core.registers[14]` (`lr`) every time `core.pc`
lands on bootrom's `__memcpy` entry (`0x2640`), for both versions' full boot-to-first-`print()` run
(`trace_callers.py`, kept alongside `trace.py`/`disasm.py`/`disasm_bootrom.py` in the scratchpad).
`lr - 4` gives the actual call site, since a 32-bit Thumb `bl` is 4 bytes.

- **1.21: 3,948 total `__memcpy` calls, dominated by one caller (71%, 2,820 calls).** Disassembled
  it (`0x100205ae`) and matched it, field offset for field offset, against the real source: cloned
  `micropython/micropython` at tag `v1.21.0` and confirmed this is `lfs2_bd_read()`'s pcache-hit
  branch in `lib/littlefs/lfs2.c` (`memcpy(data, &pcache->buffer[off-pcache->off], diff)`) - the
  `lfs2_cache_t` struct's `block`/`off`/`size`/`buffer` fields (offsets `0`/`4`/`8`/`0xc` in
  `lfs2.h`) line up exactly with the disassembled register offsets. In other words: 1.21's memcpy
  load is almost entirely normal, expected littlefs block-cache reads (mounting the filesystem,
  reading `main.py`) - nothing surprising.

- **1.28: 72,208 total `__memcpy` calls - ~18x more than 1.21, not just proportionally more (the
  overall instruction count only grew ~32x in this same sample) - and `lfs2_bd_read()` isn't even
  in the top 15 callers anymore.** Two *different*, near-identical-count callers now dominate
  (35,803 and 35,802 calls - suspiciously exact parity, suggesting two sides of one algorithm, e.g.
  a read-then-write pair executed once per element): `0x1003cb44` and `0x1003c8ec`, both calling
  through a second bootrom-`memcpy` trampoline at `0x1003b5d8` (same shape as 1.21's
  `0x100326dc` - `ldr r3,[pc,#4]; ldr r3,[r3,#4]; bx r3`, a tail-call through the bootrom function
  table, not a real call - confirmed this doesn't disturb `lr`, so the captured caller addresses
  are accurate). `diff /tmp/.../mp121_src/lib/littlefs/lfs2.c mp128_src/lib/littlefs/lfs2.c`
  (cloned both tags) shows only a trivial, behavior-preserving condition reordering between
  versions - so littlefs's own code isn't what changed; **1.28 is calling `memcpy` from somewhere
  else entirely, far more often, not just calling the same littlefs path harder.**

- **Identified with certainty: TinyUSB's `tu_fifo_t` ring buffer (`tu_fifo_read`/`tu_fifo_write`),
  not littlefs, GC, or qstr.** `apt-get install gcc-arm-none-eabi` (plus `cmake`/`ninja`, already
  present) turned out to work fine in this sandbox despite `micropython.org`/`downloads.python.org`
  being blocked - apt's own mirrors aren't on the same blocklist, and `github.com` (needed for
  `lib/pico-sdk`/`lib/tinyusb`/`lib/mbedtls` submodules) already was reachable. Built MicroPython
  `v1.28.0` from the cloned source for `BOARD=RPI_PICO` (`make -C ports/rp2 submodules && make -C
  mpy-cross && make -C ports/rp2 BOARD=RPI_PICO`) - not a byte-identical rebuild of the official
  release (different host toolchain version shifts addresses slightly), but close enough that the
  same distinctive instruction pattern (`lsls rX,rX,#17` / `lsrs rX,rX,#17` back to back - the
  15-bit field mask noted below) shows up a few hundred bytes away from where it was in the real
  UF2, squarely inside a cluster of symbols `arm-none-eabi-objdump -d --syms` still had names for:
  `tu_fifo_config`/`tu_fifo_count`/`tu_fifo_empty`/`tu_fifo_full`/`tu_fifo_read`/`tu_fifo_read_n`/
  `tu_fifo_write`/`tu_fifo_write_n` (`lib/tinyusb/src/common/tusb_fifo.c`). The struct offsets match
  exactly, field for field, against `tu_fifo_t` in `tusb_fifo.h`: `buffer` (offset `0`, matches the
  data pointer), `depth` (offset `4`, matches the count field), a packed `item_size:15 /
  overwritable:1` bitfield (offset `6`, matches the 15-bit-masked field exactly), `wr_idx`/`rd_idx`
  (offsets `8`/`0xa`). So the two dominant 1.28 call sites are TinyUSB's own FIFO read/write
  routines - USB endpoint ring-buffer traffic, not application/GC/filesystem code at all.

  Checked whether this is "1.28 uses TinyUSB and 1.21 doesn't" (which would've been a much bigger,
  simpler story): it isn't - `mp121_src/.gitmodules` and `ports/rp2/main.c`/`mphalport.c` both
  reference TinyUSB (`tud_cdc` calls) too. What *does* differ: the pinned TinyUSB submodule commit
  itself (`1fdf2907...` for 1.21 vs. `aa0fc2e0...` for 1.28 - checked via `git ls-tree` on each
  clone) - a real upstream TinyUSB upgrade sits between these two MicroPython releases, on top of
  whatever changed in `ports/rp2/mphalport.c`'s own CDC read/write loop (`CFG_TUD_CDC_EP_BUFSIZE`-
  bounded chunking logic is visible there in 1.21 - worth checking whether 1.28's equivalent chunks
  differently, i.e. more, smaller `tu_fifo` calls per byte transferred).

**Not chased further, and why:** even with the *exact* function identified, the underlying question
("why does this TinyUSB version/config call `tu_fifo_read`/`write` ~18x more") is a difference
inside vendored C dependencies MicroPython pins, not code this project can change - the payoff for
going further than "identified: TinyUSB FIFO, not littlefs/GC/qstr, likely the CDC chunking config
or the TinyUSB version bump" is lower than it was for the `write_uint32` fix below, which was
actionable immediately. Left as a well-scoped, concrete lead for whoever wants to go further:
`mp121_src`/`mp128_src` clones with submodules already fetched, a working from-source build of
1.28 for `BOARD=RPI_PICO` (`arm-none-eabi-gcc`/`cmake`/`ninja` all install cleanly via `apt-get` in
this kind of sandbox despite the firmware-download hosts being blocked), and `trace_callers.py` to
re-run the trace from immediately, rather than re-deriving any of this.

**One real, concrete result from this investigation: a genuine emulator-side perf bug, unrelated to
the version gap itself.** `cProfile` on a real 1.21 boot showed `RP2040.find_peripheral()` (a dict
lookup) called almost 1:1 with every `write_uint32()` call (159,443 vs. 158,989) - only explainable
if it's unconditional rather than a fallback. It was: `write_uint32()` checked it *before* the
cheap RAM/flash/bootrom range comparisons, unlike `read_uint32()`/`write_uint8()`/`write_uint16()`,
which all check those ranges first. Every 32-bit RAM write (stack spills, GC, locals - the
overwhelming majority of writes in any real firmware) paid for a `dict.get()` that could never
succeed. Fixed in `19ccff8` (reordered to match the other three methods); documented in
`docs/PORTING.md`'s running perf log and `CHANGELOG.md`. Measured effect: ~7-8% higher and less
variable instructions/sec on a clean A/B, 3 runs each side, of the fast 1.21 boot-to-first-print
benchmark. **Explicitly does not close the 1.21-vs-1.28 gap** - a single-sample A/B on the full
1.28 boot-and-run workload (213.54s before vs. 214.14s after) showed no measurable difference,
within this project's own already-documented run-to-run wall-clock noise (see the CDC section
above) - the gap is dominated by whatever 1.28's own firmware does differently, not by this
particular emulator-side inefficiency.

**Follow-up mechanical caching (`dd61f14`, `a53dd34`) - honest non-result.** Re-profiling after the
`write_uint32` fix still showed `len(self.sram)`/`len(self.usb_dpram)`/`len(self.flash)` called on
essentially every RAM/flash bus access (`read_uint16()` alone runs on nearly every instruction
fetch), unlike `bootrom_byte_size`, which was already cached at construction for the exact same
reason. Cached all three the same way (`ram_byte_size`/`dpram_byte_size`/`flash_byte_size`). Clean
A/B on the `sram`/`usb_dpram` change (3 runs each side, same 1.21 benchmark as above): **no
measurable difference** (325,319 vs. 325,921 instructions/sec, both well inside run-to-run noise) -
`bytearray.__len__()` is already O(1) in CPython (a struct field read), so unlike `find_peripheral`
(a real `dict.get()` with hashing), there wasn't much to save here. Kept anyway for consistency
with the established `bootrom_byte_size` pattern and because it cannot be a regression - flagged
explicitly as *not* a proven win, unlike the `write_uint32` fix above, so it isn't mistaken for one
later.

**Possible correctness gap, found along the way - checked against real sources, resolved: not a
porting bug, no fix needed.** `read_uint32()` treats flash as spanning `FLASH_START_ADDRESS` to
`FLASH_END_ADDRESS` (`0x10000000`-`0x14000000`, all four XIP mirror regions - XIP/XIP_NOALLOC/
XIP_NOCACHE/XIP_NOCACHE_NOALLOC - folded onto the same backing array via `address & 0x00FFFFFF`,
per its own comment), while `read_uint16()`/`read_uint8()`/`write_uint32()`/`write_uint8()`/
`write_uint16()` all only match the *base* 16MB region - no mirror handling. Before treating this
as a bug to fix, checked both real sources this project cross-references elsewhere: grepped the
already-cloned `raspberrypi/pico-bootrom-rp2040` for any mirror-address handling (`0x11000000`,
`XIP_NOALLOC`, etc.) - no hits, the bootrom itself never references the mirrors directly. More
decisively, cloned `wokwi/rp2040js` (this project's own JS reference implementation) and checked
its `src/rp2040.ts`: **the exact same asymmetry exists there** - `readUint32()` (line 209) checks
against `FLASH_END_ADDRESS`, but `readUint16()`/`readUint8()`/`writeUint32()` (lines 245, 256,
277-278) all check only `FLASH_START_ADDRESS + this.flash.length`, byte-for-byte the same shape as
this project's own methods. So this isn't a divergence introduced by the Python port - rp2040py
faithfully reproduced an existing property of the reference implementation it's ported from. Per
this project's own established porting philosophy (match rp2040js unless there's a documented
reason to diverge - same reasoning already applied to the SSI flash-write gap earlier in this
file), this is not something to fix locally: doing so would mean diverging from the reference for
no documented hardware reason. Closed - no action needed.

**Update: the memcpy call-stack question above was chased further and resolved - see "HLE hook for
bootrom `__memcpy`/`__memcpy_44`" below.** Walking up the call stack (via `lr` captured at
bootrom's `__memcpy` entry, across both versions' full boot-to-first-`print()` runs) found 1.28's
dominant callers are TinyUSB's `tu_fifo_read`/`tu_fifo_write` (confirmed field-for-field against
`tu_fifo_t` by building MicroPython 1.28 from source with debug symbols), not littlefs/GC/qstr -
full trail in that section. That in turn led to a genuine optimization opportunity (HLE-hooking the
bootrom routine itself, since its call frequency and per-call cost were now both understood), also
documented below.

**Not started yet:**
- PyPy 3.10 isn't obtainable in this sandbox for re-running any of this faster - `uv python
  install pypy-3.10` needs `downloads.python.org`, which this sandbox's network policy 403s
  (`gateway answered 403 to CONNECT`), same class of restriction as `micropython.org` elsewhere in
  this file. Not blocking (CPython profiling was sufficient here), but would speed up any follow-up
  tracing significantly if run somewhere unrestricted.

## HLE hook for bootrom `__memcpy`/`__memcpy_44` — implemented, opt-in, measured net negative

**Goal:** once the 1.21-vs-1.28 investigation above pinned down that real firmware's own `memcpy()`
calls (TinyUSB's `tu_fifo_read`/`tu_fifo_write`, littlefs's `lfs2_bd_read()`) route through two
fixed bootrom routines, and that they're interpreted one emulated Thumb instruction at a time like
everything else, the natural next question was whether HLE-ing (high-level-emulating) just those
two routines - replacing the interpreted copy loop with a native bulk copy - is worth doing as a
real, general (not 1.28-specific) throughput win.

**Design, implemented in `rp2040.py`/`cortex_m0_core.py`:**
- `CortexM0Core.execute_instruction()` checks `core.pc` against `RP2040.hle_memcpy_entries` (a
  `frozenset[int]`, computed once per `load_bootrom()` call, not per instruction) before the normal
  fetch/decode/dispatch path. On a hit, `_hle_memcpy()` runs instead: reads `r0`/`r1`/`r2` (AAPCS
  `dst`/`src`/`n`), calls `RP2040.bus_copy(dst, src, n)`, sets `pc = lr` (masking the Thumb bit),
  and returns a rough (not cycle-accurate) `delta_cycles` estimate - aligned copies cost `~n//8`,
  unaligned ones `~n*4` (matching `_memcpy_aligned`'s `ldmia`/`stmia` throughput vs.
  `__memcpy_slow_lp`'s exact 4-instructions-per-byte, both confirmed against the real
  `bootrom_misc.S` source) - so simulated-time-driven behavior elsewhere (SOF cadence, etc.) isn't
  disturbed by treating the copy as instantaneous. `r0` is left holding the unchanged `dst`
  (matching the real routines' own `mov r0, ip` tail); `r1`-`r3`/`ip` are left untouched, which is a
  stricter subset of "undefined after call" (real `memcpy()` is free to clobber them) so this can't
  violate the ABI; `r4`+ are never touched at all, matching the real routines' own push/pop of
  whichever they use.
- `RP2040.bus_copy(dst, src, n)`: a `bytearray` slice copy (`dst_buf[o:o+n] = src_buf[o:o+n]`) when
  both ends land in RAM or flash (the common case) - correct even when overlapping, since slicing
  the right-hand side first materializes an independent copy before assignment, giving memmove
  semantics regardless of copy direction - falling back to a plain byte-by-byte bus copy (still
  correct, just not the fast path) for anything else, e.g. touching peripheral space, which no real
  firmware routes a `memcpy()` through but isn't assumed impossible here.
- **Detecting where these two routines actually live: a whole-bootrom signature scan, not a
  hardcoded address** (`_find_hle_memcpy_entries()`). Downloaded `b0.elf`/`b2.elf` via `--bootrom`
  (this project already supports B0/B1/B2 - see the "Bootrom B0/B2 support" section below) and
  found the routines' own machine code is byte-for-byte identical across all three revisions - only
  their *position* in ROM differs (B0: `0x2888`/`0x28a0`, B1: `0x2628`/`0x2640`, B2: `0x2604`/
  `0x261c`), presumably because other ROM code shifted around them between revisions, not because
  the routines themselves changed. So `_find_hle_memcpy_entries()` takes the 20-byte signature
  bytes from `BOOTROM_B1`'s own known offsets (source of the signature only) and does a plain
  `bytes.find()` over the *whole* loaded bootrom (16KB, once per `load_bootrom()` call - nowhere
  near the hot per-instruction path) to locate wherever it actually is in the bootrom that's
  currently loaded. This finds the right offset for B0/B1/B2 automatically, and for any future
  revision that happens to carry the same unchanged routine, without a manually-maintained
  per-revision address table - and a revision where the signature genuinely isn't found anywhere
  (the routine's bytes really did change) leaves the hook safely inert for that image instead of
  risking a misfire against code it wasn't verified against.
- **Opt-in, not opt-out:** `RP2040PY_ENABLE_HLE_MEMCPY=1` is required to activate the hook at all;
  `hle_memcpy_entries` stays empty otherwise, checked once in `load_bootrom()`.

**Verified:**
- Full test suite (436/436), `ruff`/`mypy` clean.
- Real MicroPython 1.21 + littlefs boot produces byte-identical output (`Hello, MicroPython!
  version: 1.21.0`) with the hook enabled, across all three bootrom revisions (B0/B1/B2) - each one
  correctly finding its own routines at their own (different) offsets.

**Performance result - measured, both benchmarks, both negative or neutral. Confirms staying
opt-in (default off) was the right call, not just a cautious placeholder:**
- Clean A/B (`rp2040py bench`, 3 runs each side) on the fast MicroPython 1.21 boot-to-first-print
  benchmark: **no measurable improvement** (before: ~334K/331K/332K instructions/sec; after:
  ~324K/337K/320K - both ranges overlap, within run-to-run noise). Makes sense: in that same
  benchmark, `__memcpy` is called only 3,948 times out of ~1.42M total instructions (~0.2%) - not
  enough traffic through the hook to outweigh the small added cost of checking
  `core.pc in self.rp2040.hle_memcpy_entries` on literally every one of the other 99.8% of
  instructions, which never hit it.
- **The real test - a full MicroPython 1.28 boot-and-run A/B, where `memcpy` traffic is ~18x
  higher (72,208 calls) - is now measured, and it's a net regression, not a win.** Baseline (hook
  off): 65,000,000 instructions in 213.86s. With the hook enabled: 217.82s - **~1.8% *slower***,
  not faster. Instructions/sec is actively misleading for this specific comparison and shouldn't be
  used to read these two runs against each other: with the hook enabled, one `__memcpy`/
  `__memcpy_44` call collapses what would have been dozens of interpreted Thumb instructions into a
  single step in the outer instruction-counting loop, so the *step count itself differs between the
  two runs* (65,000,000 without the hook vs. 63,000,000 with it, to reach the same
  `--expect-text` match) - comparing "instructions per second" across runs whose "instruction" no
  longer means the same unit of work compares apples to oranges. Wall-clock time (213.86s vs.
  217.82s) is the only fair comparison, and it's unambiguous: slower with the hook on.
- **Why it nets negative despite real memcpy traffic:** the per-instruction
  `core.pc in self.rp2040.hle_memcpy_entries` check (an attribute lookup plus a `frozenset`
  membership test) is paid by *every single one* of the ~63-65 million instructions in this run,
  while only a modest fraction of them are actually memcpy-loop instructions the hook can skip -
  even with 18x more memcpy traffic than 1.21, that fraction isn't large enough to outweigh a fixed
  tax multiplied across effectively the entire instruction stream. The technique's payoff scales
  with how much of *total* execution time the hooked routine accounts for; here, TinyUSB's
  `tu_fifo_read`/`write` calling into `memcpy` is a real, measured contributor to 1.28's slowdown
  (see the investigation above), but evidently not a large enough share of *this specific
  benchmark's total instruction count* for a per-instruction-checked hook to pay for itself.

**Conclusion: not adopted as a default, and not recommended to enable as-is.** The mechanism itself
works correctly (verified against real MicroPython boots across all three bootrom revisions), so
this isn't a correctness failure - it's a genuine "measured this specific optimization technique,
found it doesn't pay off for this workload" result, which is exactly what the opt-in flag
(`RP2040PY_ENABLE_HLE_MEMCPY`) was designed to make safe to discover without shipping a regression
to anyone who doesn't explicitly ask for it.

**Not started yet (only worth pursuing if someone wants to revisit this technique, not blocking
anything else):**
- Reducing the per-instruction check's own overhead - e.g. an early-exit when
  `hle_memcpy_entries` is empty (not applicable to the measurement above, where it wasn't empty,
  but relevant to make sure the *disabled* default case has effectively zero added cost), or
  moving the check to a less-hot location. Given the *enabled* case is already a net loss even with
  real traffic through the hook, shaving the check's own cost further is unlikely to flip the
  overall verdict on its own - the fundamental issue is that a per-instruction Python-level check
  is expensive relative to the work it's trying to save.
- A coarser-grained version of the same idea - e.g. checking only at basic-block boundaries, or
  only after a cheap pre-filter (like a bloom filter or address-range check) - might change this
  calculus, but hasn't been explored; not worth it without evidence the underlying idea is worth
  saving, given the current measurement.

## Basic-block fusion / mini-JIT via `ast`-generated code — moved to docs/JIT_BACKLOG.md

Split into its own dedicated backlog file, **[docs/JIT_BACKLOG.md](JIT_BACKLOG.md)**, given its
size (a real, multi-session undertaking, not a follow-up fix - see that file for the full
motivation, isolated-test results (~13x CPython / ~17x PyPy steady-state on the same
`__memcpy_slow_lp` loop from the investigation above), decoupled/opt-in architecture, phased
implementation plan, and exact file:line integration points).

## Cython port of the interpreter core — implemented, on by default, real-world win confirmed (~4x)

**Status: implemented and merged into the real codebase.** Follows the JIT investigation above:
after three separate JIT attempts all measured net negative (see `docs/JIT_BACKLOG.md`) because
*any* runtime check added to the interpreter's hot path costs more than it saves unless the
accelerated code is a huge share of total execution, the natural next question was whether an
**ahead-of-time, whole-module** approach sidesteps that problem entirely - no runtime "is this
hot" check needed if the *entire* dispatch loop is compiled, not a hand-picked slice of it. It
does, and this time the real-world result actually matches the isolated ceiling estimate - see
"Measured results" below. (An earlier, narrower pass - typing only `cdef class` fields, not method
bodies - shipped first and measured only ~2-9% real-world despite an ~11.5x isolated estimate; see
"Why the first attempt underperformed" below for what that gap actually was and how it was closed.)

**Why this is structurally different from the JIT attempts:** Cython compiles a whole module to C
at build time. There's no per-instruction or per-branch "should I take the fast path" check -
every instruction benefits, not just ones matching a specific pre-selected pattern. Cython-compiled
code still runs *inside* CPython, so every existing C-extension dependency keeps working exactly
as today.

**Architecture: a subpackage of the main package, not a separate pip package.** `rp2040py.native`
(`src/rp2040py/native/`) ships inside `rp2040py` itself, compiled by `setup.py`'s
`Extension`/`cythonize()` (plain `setuptools` + `setuptools-scm`, `build-backend =
"setuptools.build_meta"`). A separate `rp2040py-native` distribution (its own `pyproject.toml`, a
`uv` workspace member, a runtime `importlib.metadata` version-matching guard between the two
packages) was tried first, mirroring `py_ballisticcalc`/`py_ballisticcalc.exts`'s split - and
abandoned same-session in favor of the current in-tree subpackage, since the extra moving parts
(two independently-versioned packages that must always match) bought nothing a `try`/`except
ImportError` inside one package doesn't already give. Every public entry point
(`rp2040py.rp2040.RP2040`, `rp2040py.cortex_m0_core.CortexM0Core`, `rp2040py.utils.bit.*`) is a
thin facade: `try: from rp2040py.native._X import ... except ImportError: from rp2040py._X import
...`, with the real pure-Python reference implementation living in the underscore-prefixed private
module (`_rp2040.py`, `_cortex_m0_core.py`, `_bit.py`) - callers never import the private module
or `rp2040py.native` directly.

The build itself first went through a custom hatchling build hook (`hatch_build.py`, not the
third-party `hatch-cython` plugin, whose default `files.targets` glob matching and
`--inplace`/`--build-lib` handling didn't produce a working wheel in this sandbox - compiled `.so`
files were built but never landed in the wheel), then migrated to plain `setuptools` +
`setuptools-scm` (`setup.py`) same-session, matching `py_ballisticcalc.exts/setup.py`'s own proven
pattern more directly - `optional=True` on each `Extension` is setuptools' native mechanism for
"skip this extension gracefully if it fails to build," replacing the hand-rolled
subprocess-and-catch-the-failure logic the hatchling hook needed. `hatch_build.py`'s
soft-fail/PyPy-skip/abi3 logic below carried over to `setup.py` unchanged in substance, just in
setuptools' idiom instead of hatchling's.

**Build failure modes are all soft, on purpose - this package still has to install everywhere:**

- No C compiler available at build time -> each `Extension`'s `optional=True` makes `build_ext`
  skip it (with a warning) instead of failing the whole build; the facades' `except ImportError`
  transparently uses the pure-Python implementation. (Cython/setuptools/setuptools-scm are still
  hard `[build-system] requires` - they're pure-Python-installable everywhere; it's specifically a
  missing *C compiler* this degrades gracefully for. Cython unavailable at all - e.g. `pip
  install --no-build-isolation` without it present - is handled the same way, via a plain
  `try: from Cython.Build import cythonize except ImportError: return []` in `setup.py`.)
- `RP2040PY_SKIP_NATIVE_BUILD=1` - forces a pure-Python wheel outright at *build* time, regardless
  of whether Cython/a compiler are actually available (e.g. for a deliberately "pure" release
  artifact).
- `RP2040PY_SKIP_CYTHON=1` - a separate, *runtime* gate (checked in each of the three facades, not
  just once in `rp2040py.native/__init__.py` - see "A gate that didn't gate anything" below) that
  forces the pure-Python fallback even when the compiled extension **is** installed. Used by
  pre-commit's `uv-pytest-pure` hook to validate the reference implementation on every commit
  without needing a rebuild, and useful generally for isolating whether a bug is native-specific.
- PyPy - compilation is skipped outright (`sys.implementation.name != "cpython"`), not attempted
  and silently discarded like the other cases. See "PyPy: compiling for it was actively harmful"
  below for why this needed to be a proactive skip, not just a fallback.

**What was actually ported, fully (not just fields this time):**

1. **`src/rp2040py/native/_cortex_m0_core.pyx`** - every one of the ~90 `_op_*` instruction
   handlers as a **module-level `cdef` function** (not a bound method) taking the core instance as
   an explicit first parameter, dispatched through a genuine **C function-pointer table**
   (`DISPATCH_TABLE`, a `ctypedef int (*OpHandler)(CortexM0Core, unsigned int, unsigned int,
   unsigned int) except -1` array of 0x10000 entries, built the same way as the pure-Python
   `_DISPATCH_TABLE`/`_DISPATCH_PATTERNS`/`_resolve_wide()`, including the same wide-opcode-range
   assertion) - not a Python-level list of bound methods. `registers`/`interrupt_priorities` are
   `unsigned int[:]` memoryviews backed by `array.array`, allocated in `__cinit__` (guaranteed to
   run exactly once at allocation, unlike `__init__`, which a subclass could in principle skip).
   `core.rp2040` is typed as the concrete native `RP2040` class (not `object`), via a `.pxd`
   cimport - a hot-path call like `core.rp2040.read_uint32(addr)` (present in nearly every
   load/store instruction) resolves to a direct C-level `cpdef` call instead of a Python attribute
   lookup + method call.
2. **`src/rp2040py/native/_rp2040.pyx`** - the bus hot paths (`read_uint8/16/32`,
   `write_uint8/16/32`). `sram`/`flash`/`usb_dpram`/`bootrom` are typed memoryviews - real, live
   views into the same underlying `bytearray`/`array.array` buffers, not copies, so external code
   that slices/mutates `rp2040.flash[...]` (peripherals, `device/load_flash.py`, tests) keeps
   working unchanged. RAM/flash(base region)/DPRAM/bootrom access branches directly on these
   memoryviews at C speed; SIO/PPB/the `peripherals` dict fall back to ordinary Python calls
   (`self.sio.read_uint32(...)` etc.) since those ~30 peripheral objects (UART, I2C, DMA, PIO,
   GPIO, the clock...) are still plain Python and get no benefit from being typed - only their
   *construction*, in `RP2040.__init__`, is transcribed here (verbatim, to avoid drift from
   `_rp2040.py`), not their internals.

**Why the first attempt underperformed (the real root cause, not just "types didn't help"):**
typing only `cdef class` *fields* makes attribute *access* fast (direct C struct offset), but
every one of the ~90 `_op_*` *method bodies* stayed plain, untyped Python - so a value read via a
fast typed field access was immediately re-boxed into a `PyObject` the moment it crossed into an
untyped method call, and re-unboxed on the way back. Confirmed by literally reading the generated
C (`annotate=True`'s HTML report, color-coded by Python-C-API-call density) rather than guessing:
`__Pyx_PyLong_From_unsigned_int(...)` immediately following a fast pointer-arithmetic field read,
because the *caller* of that read was an untyped method. A follow-up isolated test (12 real
instruction handlers, genuine C function-pointer dispatch - not the field-only pattern) reproduced
~10.9x on a realistic mix, matching the very first isolated estimate almost exactly - confirming
the original ~11.5x thesis was sound, just under-executed the first time. This full port applies
that lesson everywhere: every parameter and local on the per-instruction path is genuinely C-typed,
not just the fields.

**Stable ABI (abi3):** built against `Py_LIMITED_API` for CPython 3.10+ (`setup.py`'s
`_use_abi3()`, plus a `bdist_wheel` `cmdclass` override setting `self.py_limited_api = "cp311"` -
`py_limited_api=True` on the `Extension` controls what the *compiler* builds against, the
`bdist_wheel` option controls what the *wheel filename* gets tagged, and both are needed), producing
one `cp310-abi3` wheel that covers every 3.10+ interpreter instead of one per minor version -
verified directly: built once against 3.10, the identical `.abi3.so` loads and passes the full
437-test suite on 3.12 with zero recompilation. 3.10 specifically (not 3.10) because
`Py_LIMITED_API`'s buffer-protocol support - needed by this code's heavy use of typed memoryviews -
only entered the limited API at 3.10; below that floor, or on free-threaded builds (where
`Py_LIMITED_API` and `Py_GIL_DISABLED` are mutually incompatible per PEP 703 - `setup.py` checks
`sysconfig.get_config_var("Py_GIL_DISABLED")`), `setup.py` falls back to a normal, version-specific
extension instead. `[tool.cibuildwheel]` builds `cp310-abi3` and `cp3XXt`
separately for exactly this reason; a real local `cibuildwheel` run confirmed `auditwheel repair`
correctly relabels the output to `cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64`. One real
compile-time incompatibility found and fixed: `from cpython cimport array` (added for fast
`array.array` construction) reaches into CPython-internal `arrayobject`/`PyTypeObject` struct
layout that doesn't exist under the limited API (GCC: "invalid use of incomplete typedef
`PyTypeObject`") - removed in favor of a plain `import array`, since the only thing actually used
was the ordinary `array.array(...)` constructor call, which needs no special declaration either way.

**PyPy: compiling for it was actively harmful, not just unhelpful.** Before the proactive
`sys.implementation.name != "cpython"` skip existed, `hatch_build.py` happily compiled the Cython
extensions for PyPy too (`_rp2040.pypy310-pp73-x86_64-linux-gnu.so` etc.) - and they *worked*, in
the sense of importing and running correctly. The problem: every hot-path call then went through
PyPy's `cpyext` C-API compatibility shim instead of PyPy's own JIT, and `cpyext` is well known to
be dramatically slower than PyPy's native path for exactly this kind of call-heavy code - so
"accelerating" the interpreter core on PyPy made it *slower* than the plain pure-Python fallback,
which PyPy's JIT would otherwise have handled well on its own. Found via a real CI symptom (the
`Micropython 1.28.0 / pypy-3.10` job in `.github/workflows/ci-micropython.yml` running against its
10-minute per-step timeout) and confirmed by reproducing the old `hatch_build.py` in a git worktree
against `pypy3.10` directly - it silently built a working-but-slow native extension. The fix
(skip compilation outright on non-CPython interpreters) means PyPy always gets the pure-Python
implementation, where its JIT can do what it's actually good at.

**A gate that didn't gate anything (found by testing the actual behavior, not the code):** the
first version of `RP2040PY_SKIP_CYTHON`'s check lived only in `rp2040py.native/__init__.py`'s own
`try`/`except ImportError`. It didn't work - `RP2040`/`CortexM0Core` kept resolving to the native
backend regardless. Root cause: none of the three facades import `from rp2040py.native import X`
(the aggregated namespace `__init__.py` controls); each imports directly from a specific submodule
(`from rp2040py.native._rp2040 import RP2040`). Python always runs a package's `__init__.py`
before importing one of its submodules, but that execution completing (even via an internally
*caught* exception) doesn't prevent a separate, independent import of the submodule itself -
Python's import system doesn't gate submodule imports on what a parent's `__init__.py` did with
its own local names. Fixed by moving the actual check (a tiny shared `rp2040py._native_gate`
module) into each of the three facades directly, at the point where they decide which
implementation to import - verified by actually asserting `RP2040.__module__` under the flag, not
just eyeballing the code.

**Correctness verification:** full test suite (437/437) passes with the compiled extension active,
with `RP2040PY_SKIP_CYTHON=1` forcing the pure-Python path, and with the extension never built at
all - each checked against the *actual installed wheel* (not just in-place `.so` files) in clean
venvs on CPython 3.10, 3.10 (abi3), 3.12 (loading the 3.10-built `.abi3.so` unmodified), and 3.14
free-threaded (falls back to a normal per-version build, confirmed via its `cp314-cp314t` wheel
tag). Two real correctness bugs were caught this way, not by reasoning about the port in the
abstract:

- **`write_uint32`'s sign-preservation bug.** The pure-Python `RP2040.write_uint32` deliberately
  passes the *raw, possibly-negative* Python `value` through to `self.sio.write_uint32(...)` /
  `self.ppb.write_uint32(...)` / `peripheral.write_uint32_atomic(...)` - only the
  bootrom/flash/sram/dpram branches mask it (`value & 0xFFFFFFFF`) before use. The first native
  version masked once, up front, for every branch uniformly. `s32()`/`u32()` are idempotent
  regardless of pre-masking, so this looked harmless - but `sio.py`'s hardware-divider emulation
  does a raw `self.div_dividend > 0` comparison (not through `s32()`) to detect the "divide by
  zero, negative dividend" sentinel case, which silently broke once `div_dividend` was always
  stored pre-masked-to-unsigned (always positive). Caught by
  `test_sio.py::TestHardwareDivider::test_signed_division_by_zero_negative_3000_over_0`.
- **Cython typed-memoryview vs. `bytes` equality.** A `cdef public unsigned char[:] flash`
  field's auto-generated Python getter returns Cython's own typed-memoryview-slice object, which -
  unlike a real builtin `memoryview` - doesn't support content-based `==` against `bytes`/
  `bytearray` (falls back to identity comparison). Broke
  `test_kaluma_device.py`'s `written == b'console.log("hi");\x00'` even though the underlying bytes
  were byte-for-byte identical (`bytes(written) == expected` was `True`). Fixed by exposing
  `sram`/`flash`/`usb_dpram`/`bootrom` as `@property` methods wrapping the internal (now
  non-public, `_`-prefixed) typed memoryview field in a real builtin `memoryview(...)` - still a
  live view onto the same buffer, just one that supports the comparison semantics external callers
  already relied on.

**Measured results - this time matching the isolated ceiling, not falling far short of it:**

- *Synthetic* (`rp2040py bench --instructions 20000000 --block-size 1000`, an ADDS/SUBS mix):
  pure-Python 520,552 instr/sec vs. native 2,049,726 instr/sec - **~3.9x**.
- *Real firmware boot* (MicroPython 1.21.0, `rp2040py bench --image ... --expect-text ">>>"
  --timeout 30`; neither side reaches the REPL prompt within 30s, so this measures sustained
  instruction rate under real, representative boot-time workload rather than wall-clock-to-prompt):
  pure-Python 382,870 instr/sec (12,000,000 instructions in 31.34s) vs. native 1,581,870 instr/sec
  (48,000,000 instructions in 30.34s) - **~4.1x**.
- **~4x, consistent across a synthetic microbenchmark and a real firmware boot workload** - a
  genuine, substantial win, unlike the first attempt's ~2-9%. The isolated 12-instruction test's
  ~10.9x remains a true ceiling, not a promise: `core.rp2040` is typed but its own `read_uint32`
  etc. still cross a real (if now `cpdef`-fast) call boundary per memory access, and a full boot
  still spends real time in still-Python peripherals (DMA, PIO, USB, SPI) this port doesn't touch -
  but closing most of the original gap, rather than capturing only ~5-20% of it, validates that the
  "type the fields, not the methods" theory was the actual bug, not "Cython just doesn't help here."

### Follow-up: two more boxing sources found by reading the generated C, not by guessing

**Status: implemented and merged.** The ~4x above still left a large gap versus the isolated
~10.9x ceiling and versus PyPy (`docs/PORTING.md`'s synthetic instructions/sec table put PyPy at
~16x pure-Python). Both remaining gaps traced back to real Python-C-API traffic still hiding
*inside* code that looked fully C-typed on a first read - found by generating `cython -a`'s
annotated C (`cython -a _rp2040.pyx` / `_cortex_m0_core.pyx`) and grepping the output for
`PyNumber_And`/`__Pyx_PyLong_From_*`/`__Pyx_PyLong_As_*` rather than trusting the yellow/white
annotation coloring alone.

1. **`RP2040.read_uint32/16/8` and `write_uint32/16/8` took an untyped `address`/`value`
   parameter.** `cpdef unsigned int read_uint32(self, address)` with no type on `address` means
   Cython treats it as a plain `object` - so even though every real call site on the hot path
   (`execute_instruction`'s opcode fetch, every `op_*` load/store handler) passes an already-typed
   `unsigned int` C local or memoryview element, Cython has to box that value into a real `PyLong`
   *at the call site* before the call, and the `address & 0xFFFFFFFF` masking on entry then runs
   as Python bigint arithmetic on the boxed value instead of a single C `AND`. Fixed by retyping
   `address`/`value` as `long long` in both `_rp2040.pyx` and `_rp2040.pxd` (matching `_bit.pyx`'s
   own `u32`/`s32` convention, not `unsigned int` - a `long long` round-trips negative/oversized
   Python ints via a plain C `&`, the same as the removed `object` parameter did, so out-of-range
   callers elsewhere in the codebase keep working instead of hitting `OverflowError`). `value` on
   `write_uint32` specifically needed a caller audit first (`sio.write_uint32`'s hardware-divider
   emulation relies on receiving the *true signed* Python value, per the existing "sign-preservation
   bug" entry above) - confirmed safe since a `long long` preserves sign and magnitude for every
   real caller (register-derived values only, all comfortably inside `long long`'s range).

2. **A bare `0xFFFFFFFF`-style literal is not a C literal to Cython - it's a Python `int`
   constant.** This is the bigger one. Any hex literal that doesn't fit inside a signed 32-bit
   `int` (i.e. `0x80000000` through `0xFFFFFFFF`) is parsed by Cython as a Python object constant
   unless explicitly suffixed (`0xFFFFFFFFU`) or cast. That meant `n & 0xFFFFFFFF` - even where `n`
   is a genuine `cdef long long`/`unsigned int` local - silently compiled to
   `PyNumber_And(__Pyx_PyLong_From_PY_LONG_LONG(n), <boxed 0xFFFFFFFF>)` followed by
   `__Pyx_PyLong_As_unsigned_int(...)`: a full box, a Python-level bigint AND, and an unbox, on
   what looked like (and was written to be) a single C instruction. This exact pattern was in
   `_bit.pyx`'s `u32()`/`s32()` themselves - the two helpers this whole port's docstrings hold up
   as the "genuinely C-typed" answer to the first attempt's boxing bug - so every call to `u32()`/
   `s32()` anywhere in the interpreter was paying this tax. It was also directly inline at ~30 more
   sites across `_cortex_m0_core.pyx`/`_rp2040.pyx`, most critically `core.registers[15] =
   (core.registers[15] + 2) & 0xFFFFFFFF` (the PC increment - executed on literally every
   instruction, 8 call sites) and `core.n = (result & 0x80000000) != 0` (the N-flag update in
   `add_update_flags`/`subtract_update_flags` and ~15 `op_*` handlers - executed on essentially
   every arithmetic/logical instruction). Verified in isolation first (a standalone 4-variant `.pyx`
   confirmed a bare literal boxes while `<long long>0xFFFFFFFF`/`0xFFFFFFFFU`/a `cdef long long`
   module constant all compile to a single C `&`), then fixed by mechanically appending the `U`
   suffix to every literal in `{0xFFFFFFFF, 0x80000000, 0xFFFFFFFC, 0xFFFFFFFE, 0xFFFFFFFD,
   0xFFFFFFF9, 0xFFFFFFF1, 0xFFFF0000, 0xF0000000}` across all three `.pyx` files (~78 sites) -
   confirmed each one now compiles to plain C by re-reading the generated C, not just re-running
   the benchmark and assuming.

**Measured results after both fixes, on CPython 3.10 (this project's default target - see
`.python-version` - and, since 3.10 sits below the abi3 floor, a normal per-version build, not the
stable-ABI one 3.11+ gets - see the abi3 finding below for why that distinction turned out to
matter a lot for this specific measurement):**

- *Synthetic* (`rp2040py bench`, default 5,000,000-instruction ADDS/SUBS mix): native throughput
  went from ~523K instr/sec (pure Python) to **~13.3M instr/sec** - **~25.5x**, not the ~4x
  originally measured for the first Cython pass.
- *Real firmware boot* (MicroPython 1.28.0 + littlefs, boot to first `print()`, same fixture as
  the README's table): **46.65s -> 25.83s**, **~7.3x** over the 188.98s pure-Python baseline (was
  ~4.1x). Smaller relative win than the synthetic benchmark because a real boot spends a large,
  unchanged share of its time in still-Python peripherals (UART, SSI/littlefs, USB) that this port
  never touched - the synthetic benchmark is 100% inside the code paths these two fixes actually
  touch, a real boot isn't.
- Closer to PyPy than before, not caught up: PyPy measures ~40M instr/sec synthetic / 8.75s real
  boot on the same machine - synthetic is now ~3x behind (was ~24x), real boot ~3x behind (was
  ~4x). The remaining gap is architectural, not another hidden-boxing bug: PyPy's trace JIT
  specializes the *actual* dynamic instruction mix at runtime, where this port dispatches through a
  fixed, ahead-of-time C function-pointer table sized for the worst case every time.
- `PYTHON_JIT=1` (CPython 3.14's experimental tier-2 JIT) remains slightly *slower* than
  `PYTHON_JIT=0` for native mode even after these fixes (~4.7M vs. ~5.2M instr/sec synthetic,
  measured on the 3.11 abi3 build) - unaffected by either fix since both touched code that already
  ran outside the CPython bytecode interpreter. The outer driving loop (`Simulator.execute`/
  `_bench_synthetic`) is thin Python bytecode that immediately calls into the now much-faster C
  extension per instruction; CPython's tier-2 JIT's specialization/instrumentation bookkeeping is
  pure overhead on a loop that does almost no work at the bytecode level to begin with. Not
  something this project's code controls.

**A third, unrelated discovery made while producing the numbers above: the abi3/`Py_LIMITED_API`
build (what every CPython 3.11+ install actually gets) is measurably slower than a normal
per-version build of the identical source** - ~5.2M instr/sec synthetic / 33.90s real boot on
CPython 3.11 (abi3) vs. the ~13.3M / 25.83s above on 3.10 (normal build), same fixes, same
machine, same run. Not yet root-caused (a reasonable suspect: the limited API routes typed-
memoryview-heavy code like this through more indirection than the normal C-API's direct struct/
slot access, but that's a hypothesis, not confirmed by reading generated code the way the two
boxing bugs above were) and not something this pass changed - `_use_abi3()`'s CPython-3.11-floor
stable-ABI wheel is a deliberate one-wheel-covers-every-3.11+-version distribution tradeoff (see
"Stable ABI (abi3)" earlier in this file), and trading it away is a real decision for whoever owns
that tradeoff, not something to flip silently as a side effect of a performance pass. Flagging it
here as a scoped, standalone follow-up instead.

**Found and fixed in the same pass, unrelated to Cython specifically: `setup.py` was not passing
any explicit optimization flags to the C compiler at all**, relying entirely on whatever
`sysconfig`'s ambient `CFLAGS`/`OPT` happened to be for the interpreter running the build (`-O3` on
this machine, incidentally - not a guarantee `cibuildwheel`'s manylinux containers or every
downstream packager's CPython necessarily share). Fixed by adding explicit
`extra_compile_args=["-O3", "-std=c99"]` (`["/O2", "/W3"]` on MSVC) and `-Wl,-strip-all` at link
time (`RP2040PY_DISABLE_STRIP=1` to keep symbols, e.g. for profiling the extension itself) -
mirrors `py_ballisticcalc.exts/setup.py`'s own platform-flag pattern (this file's docstring already
said it "mirrors" that file), except deliberately *not* copying that file's own `c_compile_args =
["-g", "-O0", "-std=c99"]` - real, still-present `-O0` there, apparently debug flags left over from
a troubleshooting session and never reverted. Confirmed via `sysconfig.get_config_var("CFLAGS")`
that this machine's ambient flags already included `-O3` (so the explicit flags measured as a
no-op here, benchmark identical before/after down to noise) - added anyway since "happens to
already be optimized on this machine" isn't the same guarantee as "is optimized," and the stripped
`.so` files are a genuine, measured win regardless (~334KB vs. ~1.96MB for `_cortex_m0_core.so`,
same benchmarked speed).

**A build hazard found the hard way while producing all the numbers above, unrelated to any of the
three fixes themselves: stale `.so` files from a previous Python-version build silently shadow a
fresh one.** `src/rp2040py/native/` accumulated `_cortex_m0_core.abi3.so`,
`_cortex_m0_core.cpython-310-*.so`, *and* `_cortex_m0_core.cpython-311-*.so` simultaneously after
switching the dev venv between Python versions a few times without cleaning between builds -
Python's extension-suffix search order prefers an exact `cpython-3XX-*` match over the generic
`.abi3.so` when both are present and loadable under that interpreter, so a *stale*, version-
matching `.so` left over from an earlier (possibly broken, mid-experiment) build silently wins over
a freshly-built, correct one sitting right next to it - no error, just the wrong code running,
surfaced here only because a completely unrelated symbol (`SYSM_CONTROL`) happened to be missing
from the stale build and tripped the `except ImportError` fallback loudly. A quieter version of the
same staleness (same symbols present, just older/different codegen) would have produced no warning
at all. `rm -f src/rp2040py/native/*.so src/rp2040py/native/*.c src/rp2040py/native/*.html &&
uv sync --reinstall-package rp2040py --no-cache` before trusting any from-source perf number is the
only real guard against this - a real `pip install`/`uv add` from a clean checkout never hits it
(one target interpreter, one build, nothing stale to shadow it), so this is purely a "rebuilding
in-place across multiple interpreters in the same checkout" hazard, exactly what a local dev/perf
session does.

**Lesson for future work on these three files:** `cython -a`'s color-coded HTML is a reasonable
first pass, but its score numbers are not a reliable 0-9 scale and both boxing bugs above were
found only by grepping the *generated C* for `PyNumber_*`/`__Pyx_PyLong_*` and reading the
surrounding function, not by trusting the annotation view. Any new arithmetic touching a literal at
or above `0x80000000` needs the same treatment (`U`/`LL`/`ULL` suffix, or a `cdef` constant) or it
will silently re-introduce this exact bug. Relatedly: `noexcept` on an individual `op_*` handler is
a dead end as long as they're only ever invoked through `DISPATCH_TABLE`/`OpHandler` - the
generated call site's exception check (`if (unlikely(result == -1)) ...`) is emitted based on the
*function pointer's* declared type, not the concrete function actually behind it at runtime, so
marking individual table entries `noexcept` changes nothing observable; it would need `OpHandler`
itself to be `noexcept`, which isn't safe here (real bus/peripheral/`bl_taken`-callback exceptions
need to keep propagating, not get silently swallowed).

## littlefs persistence to the host `--littlefs` image file — not started

**Goal:** let changes MicroPython makes to its filesystem during a session actually persist back
to the `--littlefs` image file on disk, instead of only existing in the emulated flash's in-memory
buffer for the lifetime of that one process. Right now `load_micropython_flash_image()`
(`src/rp2040py/device/load_flash.py`) only ever reads the image file *into* `rp2040.flash` once at
boot; nothing ever writes that flash region back out to the file, at exit or otherwise - so the
real JEDEC flash-write support landed in the SSI work above (`RPSSI`, see the first section of this
file) lets `os`/`rp2.Flash` write/erase/program the emulated flash correctly *within* a run, but
every one of those writes is silently discarded the moment the process exits. `--image`'s own UF2
firmware is separate and already read-only by design; this is specifically about the `--littlefs`
region.

**Design sketch (ideas for organizing this work; still not implemented):**

1. **Where the write-back function lives.** Add one helper next to the existing loaders in
   `load_flash.py`, e.g. `flush_flash_region(filename, rp2040, flash_start, block_size,
   block_count)` - the mirror image of `_load_flash_image()`, writing
   `rp2040.flash[flash_start : flash_start + block_size*block_count]` out instead of in. One
   generic function reused by MicroPython/CircuitPython/Kaluma's regions, rather than three
   near-duplicates, since loading and flushing differ only in direction (see point 6 on scope).

2. **Target file: a sidecar, never the original `--littlefs` path.** Write to
   `<littlefs-path>.persistent.img` (exact suffix bikesheddable), not back onto the file the user
   passed in. Reasons this beats overwriting in place:
   - The original is often a deliberately-built template (via `mklittlefs`); overwriting it means
     "start clean again" requires rebuilding it by hand instead of just deleting one sidecar file.
   - This is a dev/test tool - a logic bug in the flash-emulation path could silently corrupt the
     original fixture forever if written in place; with a sidecar, the original is always safe to
     fall back to, and a bad sidecar is just deleted.
   - Matches the base-image/overlay pattern used elsewhere for the same problem (qcow2 backing
     files, VM differencing disks): base stays immutable, session state lives in a separate layer.
   - **Loading logic changes accordingly:** at boot, prefer the sidecar if it already exists (it's
     newer than the base), falling back to the original `--littlefs` path only if no sidecar is
     present yet (first run). The original is thus read-only from this feature's point of view -
     only ever a load source, never a write target.
   - Note this is a *separate* decision from the write-safety mechanism below - writing to a
     sidecar path vs. the original path doesn't change how the write itself needs to be done.

3. **Write safety.** Write to `<sidecar-path>.tmp` then `os.replace()` onto the sidecar path -
   atomic on POSIX, so a process killed mid-write-back can't corrupt a previously-good sidecar.
   Closes the "no investigation done yet" gap noted before without needing to model real flash
   power-loss semantics. (Considered and rejected: mmap-backing `rp2040.flash` itself instead of an
   in-memory buffer + explicit flush - doesn't remove the "when to make it durable" question since
   OS page-cache writeback timing isn't ours to control either, can let a torn/mid-command state
   reach disk on its own schedule instead of only at a controlled commit point, and would require
   splitting `rp2040.flash`'s single unified bytearray - which also covers the UF2 firmware region,
   deliberately *not* persisted - into a composite structure. A plain in-memory buffer with an
   explicit, controlled flush point mirrors real flash hardware's own model anyway: even a real
   NOR chip buffers incoming bytes in an internal page register and only commits to persistent
   cells when a program/erase command completes, which is exactly what `_apply_command()` in
   `ssi.py` already emulates - so this isn't a compromise vs. "how real hardware does it," it's the
   same shape.)

4. **When to call it - the actual blocker, found by tracing the CLI's real exit paths.** Both
   `micropython`'s interactive-REPL branch and `kaluma`'s only path funnel through
   `_wait_for_simulator()` (`cli/__init__.py`), and *every intentional quit path calls `os_exit()`*
   (`cli/stdio_repl.py`) instead of returning normally. There are four call sites today, three for
   the same underlying reason: Ctrl+X (inside `StdioInteractiveRepl._read_stdin_loop`, its own
   dedicated daemon thread), an `--expect-text` match (fired from `_make_expect_text_watcher`,
   running on the simulator's `threading.Timer` reschedule chain - also a daemon thread, see
   `simulator.py`), and Ctrl+C/`KeyboardInterrupt` (main thread, but still routed through the same
   helper for consistency + guaranteed terminal restore). `os._exit()` is used for the first two
   specifically because `sys.exit()` called from a *non-main* thread only raises `SystemExit`
   inside that one thread - it doesn't end the process, so the main thread's own
   `while simulator.executing` loop in `_wait_for_simulator` would just keep polling forever,
   oblivious. (The fourth call site, in `_cmd_mklittlefs`, is unrelated - a PyPy-only workaround for
   `littlefs-python`'s C objects finalizing out of order during interpreter shutdown.) All of this
   skips ordinary Python shutdown (`atexit`, `finally`, context-manager `__exit__`) completely, so a
   write-back hook placed only in `BaseDevice.stop()`/`__exit__` would **never fire on either of the
   CLI's two real long-running exit paths** (Ctrl+X/`--expect-text` bypass `device.stop()`
   entirely; `_wait_for_simulator`'s `KeyboardInterrupt` branch calls `simulator.stop()` directly,
   not `device.stop()`). By contrast, the raw-REPL one-shot path (`-c`/`-m`/`<file>`) already calls
   `device.stop()` on every exit via a plain `try`/`except` in `_cmd_micropython`, so a
   `stop()`-based hook *would* cover that path for free.

   **Two ways to actually wire the flush call in, both discussed, neither implemented:**
   - **(a) One hook inside `os_exit()` itself (recommended first cut).** All four call sites
     already funnel through this one shared function in `stdio_repl.py` (it already tracks
     `_active_raw_repl` as module-level state to restore the terminal before exiting) - a
     similar registration mechanism (e.g. "the currently-active device to persist, if any," set by
     `_cmd_micropython`/`_cmd_kaluma` right after constructing `device`) lets `os_exit()` call
     `persist_littlefs()` once, centrally, before it calls `os._exit()`. Small, surgical, doesn't
     touch the threading/exit-timing model at all.
   - **(b) Bigger alternative: replace `os._exit()` with a `threading.Event` + a main-thread-driven
     `sys.exit()`.** Instead of the background thread (Ctrl+X handler, `--expect-text` watcher)
     tearing the whole process down itself, it would just set an `Event`; `_wait_for_simulator`'s
     loop (already polling every 100ms) would check that `Event` alongside `simulator.executing`
     and, once set, perform the actual shutdown itself - `simulator.stop()`, `persist_littlefs()`,
     then a normal `sys.exit()` - from the main thread, where it behaves correctly. Confirmed this
     is technically sound: both background threads in question (`stdio_repl.py`'s stdin reader,
     `simulator.py`'s `threading.Timer` chain) are already `daemon=True`, so a clean main-thread
     exit wouldn't hang waiting on them - Python's normal shutdown abandons daemon threads
     immediately regardless of what they're doing (including the stdin reader thread, permanently
     parked in a blocking `os.read()` that nothing can interrupt short of killing the process,
     which is fine since it's never waited on). This would make ordinary `atexit`/`finally` hooks
     work again, which is architecturally nicer, but it's a real refactor of working, tested exit
     machinery (`_wait_for_simulator`'s loop, `StdioInteractiveRepl`'s Ctrl+X handler,
     `_make_expect_text_watcher`, and deciding who calls `simulator.stop()` in each case - notably,
     the `--expect-text` path doesn't call it today at all, relying on `os._exit()` to make that
     moot) - worth doing if a general graceful-shutdown mechanism is wanted for its own sake, but
     more than persistence alone justifies. **(a) is the pragmatic choice for this feature
     specifically; (b) is a legitimate but separate, larger piece of work.**

5. **Flag surface: `--persistent PATH`, value required - not a boolean.** Both `micropython` and
   `kaluma` subcommands already have a positional `filename` argument (`nargs="?"`, e.g.
   `rp2040py micropython script.py`) - an optional-value flag (`nargs='?'` with a `const` for
   "given with no value") is a real argparse footgun here: `--persistent script.py` would get
   silently swallowed as `--persistent`'s own value instead of `filename`'s, depending on argument
   order. Requiring a value sidesteps this entirely, and also means "write in place" isn't a
   special case needing its own code path - it falls out for free: if the user passes
   `--persistent` pointing at the same path as `--littlefs`, the write function just writes there,
   no `if path == littlefs_path` branch needed anywhere. Not passing `--persistent` at all keeps
   the feature off (today's default, opt-in), and no auto-derived filename magic (e.g. inventing
   `.persistent.img` when no path is given) - simpler to keep the path fully explicit than to write
   and maintain path-derivation logic for a rarely-used flag.
   - Considered and rejected: dropping the flag entirely and always persisting in place by default,
     documenting it as the user's responsibility to back up their own template. Rejected because
     it's not hypothetical risk - it's already flagged below (see point 7's CI note) that
     `ci-micropython.yml`/`tests/test_device.py` may rely on every run starting from the same clean
     image; making persistence unconditional would silently violate that for existing CI, not just
     for new opt-in users. Only reasonable if that assumption is first audited and found not to
     hold - not done yet.

6. **Programmatic API (`MicroPythonDevice`/`KalumaDevice`).** Expose this as an explicit
   `persist_littlefs()` (or `flush()`) method rather than an implicit constructor flag or
   `__exit__`-only behavior - callers embedding this as a library (per `mp_device.py`'s own stated
   use cases: test runners, Thonny-style tools) are far more likely to want to control exactly
   *when* a flush happens (e.g. right after one specific `exec()` completes) than to get one
   silently attached to context-manager exit.

7. **Scope for a first cut.** Prototype against MicroPython only (extending the existing
   `tests/micropython/main-flash-rw.py`), matching the reasoning above, but keep the point-1
   helper's signature generic over `(flash_start, block_size, block_count)` from the start so
   wiring in CircuitPython's `--fat12` region and Kaluma's `--littlefs`/
   `KALUMA_PROG_FLASH_START` afterward is a call-site addition, not a rewrite.

This is a design sketch to make the work easier to pick up, not an implementation - none of the
above is committed yet.

## PTY / real serial port passthrough for external tools — not started

**Goal:** something like `rp2040py micropython --pty` / `rp2040py kaluma --pty` (exact flag name
not decided - "ttyrepl" was also floated) that exposes the emulated device's USB-CDC console as a
real host-side pseudo-terminal, instead of only being reachable through this project's own
`StdioInteractiveRepl`/raw-REPL API. The point is letting *external* tools that expect a real
serial port - Thonny IDE, `mpremote`, `screen`, `minicom`, etc. - connect to the emulator exactly
like they would to a real Pico over USB, with no rp2040py-specific client needed.

**Rough shape, not designed yet:**
- `pty.openpty()`/`os.openpty()` gives a master/slave fd pair; bridge `USBCDC`'s existing
  read/write path to the master fd instead of (or in addition to) stdio, and print the slave side's
  path (`/dev/pts/N` on Linux) for the user to point their tool at - similar in spirit to `socat`'s
  `PTY,link=<path>` convention, so it's discoverable without guessing.
- Decide whether this is a flag on the existing `micropython`/`kaluma` subcommands (probably
  simpler, reuses all existing boot/littlefs/bootrom plumbing) or a distinct mode.
  `demo/kaluma_run.py`'s door already models "boot + hand off a serial-shaped interface to
  something else" fairly closely, since that's what its own `StdioInteractiveRepl` does today -
  a PTY-backed swap-in for the stdio side is probably the smallest change, but hasn't been
  scoped out.
- No investigation done yet into interactions with the raw-REPL machinery (`device/raw_repl.py`)
  used by `-c`/`-m`/`<filename>` - those likely need to keep working independently of whether `--pty`
  is also active, or be explicitly mutually exclusive with it; not yet decided which.

## Bootrom B0/B2 support (issue #11) — DONE

Landed in `cf4eed8` (#16) + follow-up `#17`. Design rationale (ELF `PT_LOAD` extraction,
`pyelftools` as a normal dependency, `--bootrom <tag|path>` wiring) is preserved in the PR
history. No remaining work here.
