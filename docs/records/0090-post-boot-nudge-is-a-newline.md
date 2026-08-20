# 0090 - The post-boot nudge is a newline, for both firmware families

- Status: **Implemented 2026-08-20.** `MicroPythonDevice._post_boot_handshake()` sends `\r\n` to
  MicroPython *and* CircuitPython; the Ctrl-C the CLI used to send to CircuitPython is gone.
- Conceived: 2026-08-20, while [0089](0089-one-reset-for-every-trigger.md)'s Phase 0.1 moved this
  code out of `cli/__init__.py` and onto the device - the move made the difference between the two
  families visible, and asking "does the newline work for CircuitPython too?" turned out to have a
  measurable answer that reverses the old choice.
- Related: [0089] (Phase 0.1 moved the nudge onto the device; Phase 2's reset re-runs it),
  [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md) (item 4, the same move),
  [0085](0085-circuitpython-code-py-and-wifi-on-screen.md) (`demo/lcd_run.py --code`, the
  CircuitPython case this would have broken), [0036](0036-littlefs-fat12-exclusivity.md) (the
  other CLI-shaped tidy-up landed the same day).

## What a nudge is for

A host that attaches to an already-booted board has missed the banner, and neither firmware speaks
again until it is spoken to. So the console sends something the moment the device enumerates:
MicroPython answers a newline with its prompt, and CircuitPython treats one as the "press any key
to enter the REPL" it is waiting on once `code.py` has finished.

`cli/__init__.py` did this itself, branching on `--circuitpython`: `\r\n` for MicroPython, Ctrl-C
for CircuitPython. Whether that branch was ever *needed* had not been tested - it dates from
CircuitPython support being added, where Ctrl-C is the documented way to get from a running
`code.py` to a prompt.

## The measurement (2026-08-20)

Both firmwares booted in this emulator on `--board pico`, nudged once at enumeration, console
captured byte for byte. The axis that matters is whether a startup script is running - and both
families auto-run one (`main.py`, `code.py`), which is precisely how this project's own CI drives
them.

| nudge | MicroPython v1.23.0 | CircuitPython 8.0.2 |
|---|---|---|
| newline, **no** startup script | `\r\n>>> ` | `Auto-reload is on...` / `Press any key...` / banner / `>>> ` |
| Ctrl-C, **no** startup script | prompt | **byte-identical** to the newline's output |
| newline, **script running** | script keeps printing (`Hello, MicroPython! version: 1.23.0` x4) | script keeps printing (`CODE-RUNNING 2,3,4`) |
| Ctrl-C, **script running** | `KeyboardInterrupt` at `main.py`, line 9 -> REPL | `KeyboardInterrupt` at `code.py`, line 7 -> `Code done running.` -> `Press any key...`, **and no prompt** |

Two things fall out of the bottom-right cell in particular: Ctrl-C kills a CircuitPython `code.py`
*and* still does not leave you at a prompt, so it is not even better at the one job it was picked
for.

## Decision

**One nudge, a bare newline, both families.** `_post_boot_handshake()` no longer branches on
`self.circuitpython` at all.

The principle it follows: *attaching to a board must not disturb what the board is doing.* A nudge
exists to wake up an idle REPL, not to stop a program. A user who does want to interrupt one can
type Ctrl-C into the console themselves, exactly as on hardware - the interactive REPL forwards it.

## What this was about to break

Not hypothetical - both were live at the moment the choice was made:

- **Three jobs in `ci-micropython.yml`** (`--littlefs littlefs.img`, `littlefs-flash-rw.img`, and
  0089 Phase 1's new `littlefs-reset-cause.img`) run with `--expect-text` against a script that
  keeps printing. Reproduced with the exact CI command: with a Ctrl-C nudge the run prints
  `KeyboardInterrupt: File "main.py", line 9`, drops to `>>> `, and never emits the expected text
  again.
- **`demo/lcd_run.py --code` (CircuitPython) was a regression introduced by 0089's Phase 0.1
  itself.** That demo constructs `MicroPythonDevice` directly and therefore never used to receive
  any nudge; moving the CLI's Ctrl-C onto every device connect would have interrupted its `code.py`
  at enumeration - the very script the demo exists to run.

## Consequences, stated so they are not surprises

- **The nudge goes out on every `MicroPythonDevice` connect**, including library/exec-only paths
  that previously got none (0089 Phase 0.1's own deliberate change). Harmless in both directions:
  `RawReplRunner.start()` opens with Ctrl-C, Ctrl-C, Ctrl-A regardless, so an `aexec()` does not
  care what preceded it.
- **On CircuitPython with a `code.py` that never ends, no prompt appears by itself.** That is the
  point, not an omission - it matches what a terminal attached to real hardware shows, and Ctrl-C
  from the console is one keystroke away.
- **A hard reset re-runs it** (0089 Phase 2's `ahard_reset()` calls the same method), so this
  decision applies to the console that comes back after a reset too, not only to a cold boot.
- `KalumaDevice` still gets no nudge at all: `BaseDevice._post_boot_handshake()` is a no-op and
  Kaluma does not override it. Its banner is racy by nature and a staged `<script.js>`'s output
  arrives on its own (`cli/__init__.py`'s own note).

## Verification

- Unit: `tests/test_device.py` - `test_post_boot_handshake_sends_a_newline_for_micropython`,
  `test_post_boot_handshake_sends_a_newline_for_circuitpython_too`,
  `test_connect_runs_the_post_boot_handshake_after_enumeration` (it must not fire before the device
  is on the bus), and 0089 Phase 2's reset test, which asserts the same bytes go out again after
  `ahard_reset()`.
- Live, with the real CI commands: `--littlefs <img> --expect-text "Hello, MicroPython!"` ->
  TEST PASSED; `--circuitpython --expect-text "Adafruit CircuitPython"` -> TEST PASSED on **8.0.2**
  and **10.2.1**.
