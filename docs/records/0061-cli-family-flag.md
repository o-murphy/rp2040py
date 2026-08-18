# 0061. One firmware command with `--family`, instead of `micropython` + `kaluma`

- Status: **Deferred — documented, not implemented (2026-08-17).** Depends on
  [0059](0059-boardspec-firmware-resolution.md): doing this first would double the work, since
  family selection still forks into separate `FirmwareSpec` constants in several places until that
  lands.
- Conceived: 2026-08-17
- Related: 0059 (firmware resolution in `BoardSpec` - makes `family` a real key rather than a
  branch), 0049 (`--board-spec`, and the by-hand flag validation pattern), 0036
  (`--littlefs`/`--fat12` mutual exclusion - the same pattern's first use), 0033 (shell
  completions, which any CLI reshape has to update), 0001 (the original CLI/device API split)

## The question

`rp2040py micropython` (with a `--circuitpython` boolean) and `rp2040py kaluma` are two
subcommands for what looks like one job: boot a firmware image and talk to it. Could they be one
command with `--family {micropython,circuitpython,kaluma}`?

## What actually differs

Most of the surface is already shared - `--board`, `--board-spec`, `--image`, `--gdb`/
`--gdb-port`, `--bootrom`, `--expect-text`/`--expect-regex`, `--tcp-port`, `--pty`, `--littlefs`,
`--dump-fs` and `--fetch-fw-only` all come from the same `_shared_arg_parser()`. Three things
differ, and only the first is really about the *firmware family*:

1. **Which image and flash layout** - `MICROPYTHON`/`CIRCUITPYTHON`/`KALUMA`. This is exactly what
   0059 collapses into one keyed lookup.
2. **Which device class, and therefore whether code can be run at all.** `MicroPythonDevice`
   carries the raw-REPL runner and serves both MicroPython and CircuitPython; `KalumaDevice` is a
   `BaseDevice` with `__init__` and `dump_flash_image()` and nothing else - its own help text says
   "interactive REPL only". There is no Kaluma equivalent of `-c`/`-m`/`aexec_file()`.
3. **The positional argument means two different things.** `micropython <file.py>` executes the
   script over the REPL; `kaluma <file.js>` stages it into flash as the auto-run *user program*
   (Kaluma's separate YMODEM region, `FlashLayout.prog_start`). Same-looking argument, different
   operation.

Plus a detail: the post-boot console handshake is per-firmware anyway (Ctrl-C for MicroPython,
`\r\n` for CircuitPython, nothing for Kaluma, deliberately - see the README's Kaluma section).

## The real boundary is the protocol, not the family

Today's split is not "one subcommand per firmware" - it is "one per device protocol". `micropython`
is *the raw-REPL device*, which is why CircuitPython rides along on it as a flag rather than
getting its own subcommand; `kaluma` is *the device with no exec surface*. That a firmware family
is currently spelled as `--circuitpython`, a boolean on a command named after a different firmware,
is the clearest symptom that command name and family are not the same axis.

So `--family` unifies the naming, while the thing that actually forks - can this device run code? -
stays forked underneath. That is fine, as long as the CLI says so honestly.

## Proposed increments

**Step 1 (cheap, right regardless of the rest): `--family {micropython,circuitpython}` replaces the
`--circuitpython` boolean** on the existing command, with the old flag kept as a deprecated alias.
It removes the odd shape above and lines the CLI up with 0059's own key. Hours, not days.

**Step 2 (optional): one command** - `rp2040py fw --family {micropython,circuitpython,kaluma}` -
with `micropython`/`kaluma` retained as aliases so nothing breaks. The cost is not the dispatch; it
is the **per-family flag matrix**, which `argparse` cannot express and which therefore gets
hand-validated, exactly as `--littlefs`/`--fat12` (0036) and `--board`/`--board-spec` (0049)
already are:

| flag | micropython | circuitpython | kaluma |
|---|:---:|:---:|:---:|
| `-c` / `-m` / a `.py` script | ✅ | ✅ | ❌ (no exec surface) |
| a `.js` user program | ❌ | ❌ | ✅ (staged into flash, not executed) |
| `--littlefs` | ✅ | ❌ | ✅ |
| `--fat12` | ❌ | ✅ | ❌ |
| `--dump-fs` | ✅ | ✅ | ✅ |
| everything else shared | ✅ | ✅ | ✅ |

Around that: honest `--help` per family, shell completions (0033), README/reference/CHANGELOG, and
tests for the validation matrix itself - the `test_cli_board_spec.py` shape (hand-built
`Namespace`s, no boot) fits.

**Step 3 (only if it happens anyway): full merge** once `KalumaDevice` grows a script-exec path.
Until then a single command advertises flags that are meaningless for one of its `--family` values,
which is worse than two commands that each mean what they say.

## The trap to avoid

If the positional argument survives a merge as-is, one argument silently means "execute this" or
"flash this" depending on `--family`. It should become explicit at that point - `--exec script.py`
versus `--program app.js` - even though that is a breaking change for the `kaluma` spelling, since
the alternative is a footgun that only shows up after a boot.

## Not decided here

- The command's name if step 2 happens (`fw`? `boot`? keep `micropython` as the canonical one with
  `kaluma` an alias?). Renaming a published CLI entry point is the kind of thing that wants a
  deprecation window, not a flag day.
- Whether `--family` should also accept a family a *board file* declares but the built-in registry
  does not - which only becomes a real question once 0059 lets board files carry several.

## 2026-08-18: step 2 confirmed no-break, one new naming question

Revisited whether step 2 (one merged command) can land without breaking existing invocations: yes
- `micropython`/`kaluma` kept as aliases onto the new command is exactly what this record already
  proposed above, so nothing currently scripted against either subcommand needs to change.

One naming question this record's "Not decided here" list didn't have yet: the CLI already has a
third, unrelated subcommand named `run` (`run_parser` in `cli/__init__.py` - boots a raw local
`.hex`/`.uf2` with a GDB server, no firmware-family concept, no `--board`/`--image` firmware
resolution at all). If step 2's merged command is named `fw`, both `fw` and `run` read as "boot
something" from the name alone, and the actual distinction - a known firmware family with a device/
REPL/exec surface, versus an arbitrary image with nothing but a GDB server attached - isn't obvious
from either name. Not resolved here; worth weighing alternative names (`boot`? keeping
`micropython` as the canonical spelling with `kaluma`/`circuitpython` as aliases, per the existing
"Not decided here" entry above?) against `fw` specifically for this collision before step 2 is
picked up.
