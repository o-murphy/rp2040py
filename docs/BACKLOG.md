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

### BLOCKING — real boot hangs before REPL (regression, not yet root-caused)

Reported by user: `rp2040py micropython` (and presumably `kaluma`) now hangs, never reaches REPL.
Confirmed via `git worktree` A/B: baseline (pre-SSI-work, `12249b8`'s parent) reaches REPL in
~349,615 instructions; current branch does not reach it within a 20M-instruction budget.

**Symptom:** CPU spins forever inside the bootrom at PC `0x1784`–`0x17d6`. Disassembled (via
`arm-none-eabi-objdump -D -b binary -m arm -M force-thumb --adjust-vma=0` on the extracted
`BOOTROM_B1` bytes) and confirmed this is the compiled body of `flash_do_cmd_cs()` (pico-sdk
`hardware_flash/flash.c`) — it reads `SSI_TXFLR` (`ldr r7,[r4,#32]`) and `SSI_RXFLR`
(`ldr r6,[r4,#36]`) directly off `r4 = 0x18000000` (SSI's XIP-mapped base), sums them, and loops
while there's "room" (`TXFLR+RXFLR <= 13`) to push more TX and/or still-outstanding RX to drain.

**Progress so far:**
1. Fixed CS framing (SSIENR → QSPI_SS pin) — was wrong, confirmed via SDK source.
2. Fixed `raw_output_enable` gap for QSPI pins (`always_output_enabled`) — confirmed via trace
   that "CS pin: LOW -> HIGH" now fires correctly on `flash_cs_force()`'s writes.
3. Both fixes *reduced* the spin (1,247,617 loop iterations in 20M instructions before the
   `always_output_enabled` fix → 310,117 after) but did **not** eliminate it. Something else is
   still wrong.
4. Added live tracing (`RPSSI.write_uint32`/`read_uint32` monkeypatched) around one instance of
   the loop. Captured a concrete register snapshot at loop entry (PC `0x1794`):
   `r0=0x0 r1=0x0 r2=0x4 r3=0x0 r4=0x18000000 r5=0x4 r6=0x4 r7=0x20041f74`, i.e. `r2` (tx_remaining)
   starts at `4`, decrementing over the next ~100 instructions (`r2`: 4→3→1→0) while `r6`/`r7`
   (RXFLR/TXFLR) stay pinned at small values — consistent with the loop actually running a real
   4-byte command, not spinning on a zero-length no-op. Need to confirm whether it ever terminates
   normally (tx_remaining AND rx_remaining both hit 0) or whether RX never drains because DR0
   reads aren't being issued/queued right.
5. Earlier trace (`dr0_trace.py`, before the register-snapshot version above) showed long runs of
   *only* `RXFLR read -> 0` / `TXFLR read -> 0` with **no interleaved DR0 write/read log lines at
   all** — meaning at least part of the spin isn't even reaching the DR0 shift path. Not yet
   reconciled with point 4's non-zero `r2` — may be a different call site / different point in the
   same loop, or a bug in the trace instrumentation itself (needs re-verification).

**Suspicious but unconfirmed:** `SSI_TXFLR`'s `read_uint32` returns `self._txflr`, a field that is
*only* ever set via a direct write to that offset and never dynamically reflects real pending-TX
state (unlike `SSI_RXFLR`, correctly `len(self._rx_queue)`). Current belief (not yet proven): this
is actually *correct* — real hardware treats TXFLR as read-only status and firmware never writes
it, so it should just stay 0 — but this asymmetry was flagged mid-investigation and hasn't been
fully ruled out as contributing to the loop's exit condition never being satisfied.

**Next step:** re-run the register-snapshot trace (`/tmp/.../scratchpad/dr0_trace2.py`, latest
version) for a longer window past the first ~100 instructions to see whether the loop actually
exits and re-enters repeatedly (i.e. `flash_do_cmd_cs` is being called over and over, each time
completing fine — which would mean the *hang* is somewhere else entirely, not in this loop) or
whether a single invocation truly never terminates. If it never terminates, trace exactly which
DR0 read/write is expected next vs. what `RPSSI` actually does at that instant — likely need to
add PC to the existing `dr0_trace.py` log lines to correlate reads/writes with loop position.
Scratch scripts for this live in
`/tmp/claude-1000/-home-murphy-pyproj-rp2040py/a1ee3572-f961-4af3-89b7-d660cca73f58/scratchpad/`
(`dr0_trace.py`, `dr0_trace2.py`, `qspi_trace3.py`, `loop_inspect.py`, `bootrom.bin`) — not
committed, session-scratch only.

### Not started yet

- Once the hang is fixed: re-run full `tests/test_ssi.py` + full suite (430 passing before this
  branch) for regressions.
- Real end-to-end validation: format + write + read-back via littlefs at a live REPL.
- README/CHANGELOG/PORTING.md: remove the "filesystem is not writeable" caveat once confirmed
  working.

## Bootrom B0/B2 support (issue #11) — DONE

Landed in `cf4eed8` (#16) + follow-up `#17`. Plan file at
`~/.claude/plans/playful-foraging-creek.md` is now historical — superseded by the merged PRs, kept
only for reference on the original design rationale (ELF `PT_LOAD` extraction, `pyelftools` as a
normal dependency, `--bootrom <tag|path>` wiring). No remaining work here.
