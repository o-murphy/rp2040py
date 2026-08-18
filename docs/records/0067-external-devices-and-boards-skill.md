# 0067. A Claude Code skill for adding `ExternalDevice`s and boards

- Status: Implemented (2026-08-18)
- Conceived: 2026-08-18 · Implemented: 2026-08-18
- Related: 0049 (external device authoring docs - the reference doc this skill sits on top of,
  never duplicates), 0059 (`BoardSpec` firmware resolution / the promotion checklist this skill
  cites), 0027 (the "3g rule" this skill leads with), 0046 (`Epd2in9G` / the raw-buffer callback
  boundary this skill tells a new display device to copy), 0032 (docs restructure - the general
  precedent for "a process artifact gets a record too"), 0066 (board support expansion - the
  checklist this skill exists to make executing against easier)

## What

[`.claude/skills/external-devices-and-boards/SKILL.md`](../../.claude/skills/external-devices-and-boards/SKILL.md) -
a project-scoped Claude Code skill, checked into the repo, invoked automatically whenever a session
is asked to add/emulate a device or board (or by name, `/external-devices-and-boards`). It is
deliberately **not** a rewrite of
[docs/reference/external-devices-and-boards.md](../../docs/reference/external-devices-and-boards.md) -
that reference doc stays the one place the design/API is explained, and the skill points at it
first rather than re-deriving it. What the skill adds on top is the *execution* layer an agent
needs that the reference doc (correctly) doesn't carry:

- **The 3g rule stated up front**, as the first thing to internalize before writing anything -
  every electrical fact cited to a real upstream source, never guessed or measured by trial and
  error (0027).
- **A template ladder** for a new device (`led_mock.py` → `key_mock.py`/`bootsel_button.py` →
  `ws2812.py` → `st7735s.py`/`epd2in9g.py` → `cyw43/`), so the first move is "which existing file is
  this most like" rather than a blank page.
- **Which test file proves what**, concretely, by pointing at real in-tree examples rather than
  describing an abstract pattern: `test_led_mock.py`'s SIO-register-driving helper for the minimal
  case, `test_ws2812.py`'s real-driver-timing waveforms for anything timing-coded,
  `test_boards.py`'s `_retrieve` monkeypatching so a board test never hits the network, and -
  the part with no analogue in the reference doc at all - **live-boot verification**:
  `tests/pico_spec.py` plus `ci-micropython.yml`'s `test-board-spec` job, cited as the actual bar a
  board file has to clear, not just a local smoke test.
- **`scripts/fetch_firmware.py list --family --slug [--page]`** (built this session, see the
  script's own docstring) as the way to populate a new board's firmware version history, instead of
  hand-typing one pinned URL the way most `boards/` files did until now.
- **0059's five-item promotion checklist**, restated as the concrete gate before assuming a new
  `boards/` example should graduate into `boards.BOARDS` (real `--board` support) - most new boards
  should stay examples, which the skill says explicitly rather than leaving as an inference.

## Why now

Prompted directly by working through [0066](0066-board-support-expansion.md)'s checklist of ~90
addable boards - the reference doc and the promotion checklist already existed, but re-deriving
"which test file, which live-boot command, which existing device to copy" from scratch each time a
board from that checklist gets picked up is exactly the kind of repeated, session-spanning grunt
work a skill exists to remove.

## `.gitignore` fix, made alongside this

`.gitignore` blanket-ignored `.claude/` (line 79, `.claude/`), which would have silently excluded
this skill from every commit - a shared, checked-in skill under `.claude/skills/` is exactly the
kind of file that ignore line was never meant to catch (it exists for local session state, not
project-level Claude Code configuration). Changed to:

```
.claude/*
!.claude/skills/
```

- everything else directly under `.claude/` stays ignored by default (nothing else lived there at
the time of this record), while `.claude/skills/` and everything under it is tracked. Verified with
`git check-ignore -v .claude/skills/external-devices-and-boards/SKILL.md` returning nothing (exit
1 - not ignored) after the change.

## Not decided here

- Whether other `.claude/` subdirectories (`agents/`, `commands/`, a shared `settings.json` versus
  a local `settings.local.json`) should also be carved out of the ignore - out of scope until one of
  them actually exists in this repo.
