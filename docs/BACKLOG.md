# Backlog / in-progress work notes

Working notes for tasks that span multiple sessions. Not user-facing docs — see README.md /
PORTING.md / CHANGELOG.md for those.

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

**Rough shape, not designed yet:**
- Simplest version: on clean shutdown (`BaseDevice.stop()`/CLI exit path), dump
  `rp2040.flash[MICROPYTHON_FS_FLASH_START : MICROPYTHON_FS_FLASH_START + block_size*block_count]`
  back to the `--littlefs` file path. Needs a decision on *when* - only on graceful exit (misses
  power-loss/Ctrl+C/crash cases, arguably the most realistic to test since that's when real
  flash-persistence bugs matter) vs. periodically/on every completed flash command
  (`_apply_command()` in `ssi.py` already knows exactly when an erase/program actually commits -
  could hook a write-back there instead, closer to "real flash," but far more I/O if unbuffered).
- CircuitPython's `--fat12` path and Kaluma's `--littlefs`/user-program region
  (`KALUMA_FS_FLASH_START`/`KALUMA_PROG_FLASH_START`) are the same shape of problem, not just
  MicroPython's - whatever mechanism gets built should probably cover all of them rather than being
  MicroPython-specific, though MicroPython is the natural one to prototype against first since it's
  the one with an existing flash-rw test (`tests/micropython/main-flash-rw.py`).
- Worth deciding whether this should be opt-in (a new flag, e.g. `--littlefs-persist`/similar) or
  the default once it exists - persisting by default changes today's implicit "every run starts
  from the same clean image" behavior, which some existing tests/CI usage may be relying on
  (worth auditing `ci-micropython.yml` and `tests/test_device.py` for that assumption before
  deciding).
- No investigation done yet into partial-write safety (process killed mid-write-back corrupting the
  image file worse than the crash itself would have) - a real flash chip's own power-loss semantics
  probably aren't yet worth modeling here, but at minimum a write-to-temp-file-then-rename would
  avoid this tool being the thing that corrupts an otherwise-fine image.

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
