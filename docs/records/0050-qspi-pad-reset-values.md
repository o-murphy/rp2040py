# 0050. CircuitPython 10.x boot stall: PADS_QSPI reset values (`GPIO_QSPI_SS`'s pull-up)

- Status: **Implemented — verified (2026-08-16).** Root cause of
  `docs/tasks/circuitpython-10x-boot-stall.md`, which this record supersedes and folds in below.
- Conceived: 2026-08-16
- Related: 0006 (GPIO pull-up/down on a floating pin - the model this fix relies on; that record
  built it for bank0, this one notices the QSPI bank never got the matching *defaults*), 0048
  (whose CircuitPython gap check surfaced the stall), 0035 (the other "a board/bank-specific
  constant was wrong" bug)

## Symptom

CircuitPython **10.x** produced zero console output, indefinitely, at 99.9% CPU - on `--board pico`
as much as `pico_w`, so nothing to do with CYW43. 9.2.9 and 8.0.2 reached the REPL in seconds.

## Root cause

`GPIOPin.__init__` gives every pad `0b0110110` - PADS_BANK0's reset value. That is right for the 30
ordinary GPIOs and wrong for the six QSPI pads, which reset differently (RP2040 datasheet 2.19.6.3):

| pad | reset | pull |
| --- | --- | --- |
| `GPIO_QSPI_SCLK` | `0x56` | pull-down |
| `GPIO_QSPI_SS` | `0x5A` | **pull-up** |
| `GPIO_QSPI_SD0..SD3` | `0x52` | none |

`GPIO_QSPI_SS`'s pull-up is load-bearing hardware, not a detail: it holds the flash deselected when
nothing drives the line, and it is the entire mechanism behind reading the BOOTSEL button, which
shorts SS to ground when pressed. Firmware therefore treats **low as "pressed"**.

With the bank0 default instead, SS resets to pull-*down* with input disabled, so an undriven SS
reads low forever - a permanently-held BOOTSEL button. Firmware that merely samples it misreads it;
firmware that *waits* for it to go high hangs. CircuitPython 10.x does exactly that, from a
RAM-resident routine (it has to be: touching QSPI pads disturbs XIP, so the poll cannot itself run
from flash). The observed loop, at `0x2000011e`:

```
0x2000011e: 6893   LDR r3, [r2, #8]    ; r2 = 0xd0000000 -> SIO GPIO_HI_IN
0x20000120: 4219   TST r1, r3          ; r1 = 0x2 -> bit 1 = QSPI_SS
0x20000122: d0fc   BEQ -8              ; spin while the bit is clear
```

98% of sampled PCs sat in those three instructions. 9.x never polls this way, which is why the same
emulator boots it fine - the defect was always present, 10.x is simply the first firmware to depend
on the bit.

## Fix

`src/rp2040py/qspi_pads.py` holds the datasheet reset values; `_rp2040.py` and
`native/_rp2040.pyx` apply them to `self.qspi` right after constructing the pins. Nothing else
changes - 0006 already built the "undriven pad resolves from whichever pull is enabled" model, so
correct defaults are all that was missing. `tests/test_qspi_pads.py` covers the values, the
per-pad pull, that `GPIO_HI_IN` reads SS high undriven, and that actively driving SS low (what
pressing BOOTSEL would do) still wins over the pull-up.

## Verification

- `pc_sample.py`-style PC sampling on real 10.2.1: before, 98% of samples in the three-instruction
  spin and **0 bytes** of console; after, 187 bytes of console and PCs spread across
  flash/SRAM/bootrom.
- End to end through the CLI, which is what actually matters:
  `Adafruit CircuitPython 10.2.1 on 2026-05-13; Raspberry Pi Pico W with rp2040` and a `>>>`
  prompt.
- `uv run pre-commit run --all-files` clean (both builds).

## Two changes made while chasing this that are *not* the fix

Kept deliberately, each a real defect, each proven irrelevant here by re-running 10.2.1 with them
reverted and the QSPI fix alone - it still booted. Recording that explicitly so neither gets
credited later:

- **`SEV` never set the event register.** ARMv6-M B1.5.18 says SEV sets the Event Register of every
  PE including the one executing it, and `_op_wfe` already consumed `event_registered` correctly -
  but `_op_sev` only logged. `__sev(); __wfe();`, the standard idiom for making the next WFE fall
  straight through, therefore parked the core in `waiting` forever. Fixed in both backends, with
  `opcode_sev()`/`opcode_wfe()` added to the assembler and two tests in `test_instructions.py`.
- **`VREG_AND_CHIP_RESET` was an `UnimplementedPeripheral`**, so `CHIP_RESET` read back
  `0xFFFFFFFF` and its `PSM_RESTART_FLAG` (bit 24) looked permanently set. The bootrom tests that
  bit on its reset path, writes it to clear (also into the void), re-reads it as set, and loops.
  The emulator's own cold boot jumps straight to `FLASH_START_ADDRESS` and skips the bootrom, so
  nothing hits this today - it is a latent trap for any future bootrom-path work, now closed by a
  minimal `RPVREGAndChipReset` with datasheet reset values and write-1-to-clear on that one bit.

## Method note - two wrong turns worth not repeating

The first two instrumentation attempts produced confident, entirely wrong conclusions, and both
failed the same way: **driving `Simulator._execute_batch()` on a device that was never started.**
A freshly constructed `MicroPythonDevice` has not had `BaseDevice._aconnect()`'s
`core.pc = FLASH_START_ADDRESS` applied and has no running engine room, so batches either execute
nothing (PC pinned at the entry point) or wander the bootrom. That produced a detailed, plausible
"CircuitPython never leaves the bootrom" story, complete with a traced `PSM_RESTART_FLAG` loop -
all of it an artifact.

What caught it: running the **same** instrument against 9.2.9, which is known to boot. It showed
the identical "stuck in the bootrom" signature, which is impossible for a firmware that reaches a
REPL. Any harness for this emulator should be validated against a known-good firmware *before* its
output is trusted, and must set the cold-boot PC and `start_execution()` the way `_aconnect()`
does - `pc_sample.py`'s docstring in this session's scratch space records the working shape.

## Appendix: folded-in working note `docs/tasks/circuitpython-10x-boot-stall.md` (2026-08-16)

Reproduced verbatim, then deleted from `docs/tasks/`, per the tracker's own convention. One claim
in it is **wrong** and is corrected here rather than in the text below: it states that
`firmware_specs.json`'s CircuitPython board map "tops out at `9.2.9`". It does not - the map has
carried 10.x since before this session (30 `10.*` tags for `pico_w`, `10.2.1` among them). The
misreading came from printing the dict in insertion order, where `"10.x"` sorts before `"8.x"`
as a string. Nothing else below is affected.

### Task: CircuitPython 10.x never reaches the REPL (9.x/8.x do)

Not a `docs/records/` entry - a working note for whoever picks this up next. Found while doing
[0048](0048-cyw43-nat-reflector.md)'s own "CircuitPython live boot" gap check
(2026-08-16) - **not a CYW43/NAT bug**: it reproduces on plain `--board pico`, with no
`Cyw43439` involvement at all. Not root-caused.

#### Repro

```
uv run rp2040py --log-level error micropython --circuitpython --board pico \
  --image <adafruit-circuitpython-raspberry_pi_pico_w-en_US-10.2.1.uf2>
```

Produces **zero bytes** of console output, indefinitely (observed across separate runs of 15, 20
and 23 real minutes, all killed by their own timeouts, never by the emulator). CPU sits at 99.9%
with process CPU-time tracking wall-clock 1:1 throughout - busy, not blocked.

#### What's confirmed

- **Version-specific, not a broken CircuitPython story.** The same command against `9.2.9` and
  against `8.0.2` (this project's own default, per the README) reaches the friendly REPL in
  seconds:
  `Adafruit CircuitPython 9.2.9 on 2025-09-07; Raspberry Pi Pico with rp2040` / `>>>`. Only 10.x
  hangs. `firmware_specs.json`'s own `circuitpython` board map tops out at `9.2.9`, so nothing in
  this repo has ever pointed at a 10.x build.
- **Not board-specific.** Identical no-output behavior on `--board pico` and `--board pico_w`,
  which is what rules out the CYW43 emulation as a cause.
- **Where it stops, from `--log-level debug`.** Boot proceeds normally through PLL_SYS/PLL_USB
  setup and SSI (flash) init, then in the last ~90ms of logged activity:
  - `[RP2040] Write to undefined address: 14000004` and `14002000`, plus
    `Read from invalid memory address: 14000004` - that's **XIP_CTRL** (`XIP_CTRL_BASE`
    `0x14000000`: cache flush/enable and the cache-SRAM window), which this emulator does not
    implement at all.
  - ~69 reads of `ROSC_BASE` offsets `0x18`/`0x1c` (`RANDOMBIT`) - the ring oscillator's entropy
    source, also unimplemented.
  - `PIO0`/`PIO1 clkDivRestart not implemented`, ~50 times.
  - then **360 `SEV` instructions** and, after that, complete logging silence for the rest of the
    run - i.e. the CPU is executing a loop that touches no unimplemented peripheral, so nothing
    more gets logged.
- **Ruled out: the missing inter-core FIFO.** `sio.py` genuinely has no `FIFO_ST` (`0x50`),
  `FIFO_WR` (`0x54`) or `FIFO_RD` (`0x58`) - the SDK's `multicore_launch_core1()` handshake ends
  each push with `__sev()`, which would fit the SEV storm exactly. But the debug log contains
  **zero** `Read from invalid SIO address` warnings, and that fallback is what an unimplemented
  SIO offset would hit. So whatever the SEV loop is, it is not reading the core1 FIFO. (The FIFO
  registers really are absent, and a second core really isn't emulated - that just isn't what this
  bug is.)

#### Where to look next

- The three unimplemented blocks the boot touches right before going quiet are the obvious
  suspects, in rough order of suspicion: **XIP_CTRL** (10.x may enable/flush the XIP cache and
  then depend on it behaving), **ROSC RANDOMBIT** (an entropy-gathering loop that never
  accumulates would spin exactly like this - CircuitPython seeds `os.urandom`/`random` at
  startup), and **PIO `clkDivRestart`**.
- Distinguishing them cheaply: implement one at a time as a stub with plausible semantics
  (RANDOMBIT returning alternating/pseudo-random bits is a two-line change) and re-run. If the
  boot advances past its current stopping point, that block was the blocker.
- A PC histogram over the spinning region would name the loop outright. `--gdb` (the built-in GDB
  server, port 3333) is available and does not need the host `ptrace` permissions that blocked
  0041's own investigation in this environment.
- Worth checking what changed in CircuitPython 10.0's RP2040 port relative to 9.2 - a new
  dependency on any of the three blocks above would be the answer, and their release notes /
  `ports/raspberrypi` diff are public.

*(Outcome: the PC histogram was indeed what named it - none of the three suspects was the cause.)*

#### Don't re-derive

- 9.2.9 and 8.0.2 both boot; 10.2.1 does not; the difference is not the board. Re-confirmed on
  separate runs.
- The SEV storm is not the core1-FIFO handshake (no invalid-SIO reads) - checked, don't re-check.
- "No output" is not the CLI buffering: these runs used the console/`--expect-text` path, which
  streams. (The *script/exec* path does buffer everything until the script ends - that's a
  separate trap, documented in 0048's own TLS/WebSocket entry.)

#### Impact / why it matters beyond this gap

`README.md` documents `--circuitpython` as a supported mode, names **8.0.2** as the default, and
even uses `--image 10.2.1` as its example of picking a different version - which is precisely the
combination that hangs. There is also **no CircuitPython CI of any kind** in this repo (no
workflow, no `tests/circuitpython/` before this session), so nothing would have caught it. The
CircuitPython CYW43 check this was found under runs against `9.2.9` for exactly this reason.
