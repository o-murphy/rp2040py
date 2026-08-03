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
  - Investigate why plain-boot wall-clock time (waiting on `--expect-text` over the emulated
    USB-CDC REPL) varies noticeably run to run in this sandbox (tens of seconds to several
    minutes) even for the same firmware/image - not yet root-caused, but tests that don't need to
    wait on USB-CDC at all (e.g. `tests/micropython_spi_run.py`, which watches SPI0 hardware pins
    directly) consistently finish in seconds regardless, so USB enumeration timing/variance is the
    leading suspect over anything littlefs/SSI-related.
  - CircuitPython's flash-write path uses the same SSI peripheral but hasn't been separately
    exercised with a dedicated test (CircuitPython doesn't typically write its own filesystem at
    runtime the way MicroPython's `os`/`rp2.Flash` does, so there's less of a natural test to write
    - not treated as blocking).

## Bootrom B0/B2 support (issue #11) — DONE

Landed in `cf4eed8` (#16) + follow-up `#17`. Design rationale (ELF `PT_LOAD` extraction,
`pyelftools` as a normal dependency, `--bootrom <tag|path>` wiring) is preserved in the PR
history. No remaining work here.
