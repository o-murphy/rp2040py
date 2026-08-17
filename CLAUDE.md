# Working on rp2040py

## Where things stand

Engineering notes live as numbered, append-only records under [docs/records/](docs/records/),
indexed by [docs/0000-TRACKER.md](docs/0000-TRACKER.md) (the scheme itself is
[record 0032](docs/records/0032-docs-restructure.md)). Working notes for an investigation that
isn't root-caused (or otherwise ready to become a record) yet live under
[docs/tasks/](docs/tasks/) instead - the tracker's own "In progress / Proposed" section says which
ones are still open; a task file gets folded into a proper record once it's actually resolved (see
e.g. 0041/0044, both created from a `docs/tasks/*.md` note). "Folded in" is literal: the note is
appended verbatim to that record as an `## Appendix: folded-in working note ...` section and the
`docs/tasks/` file is then **deleted**, so `docs/tasks/` only ever holds still-open notes. Older
records still cite the pre-fold-in `docs/tasks/<name>.md` paths - the tracker's "Conventions"
section maps each one to the record that now carries it.

Two records track the active work - check both before assuming what's done:

- [docs/records/0026-main-thread-asyncio.md](docs/records/0026-main-thread-asyncio.md) - engine-room
  concurrency model. **Done**, all 5 phases landed and verified.
- [docs/records/0027-cyw43-wifi.md](docs/records/0027-cyw43-wifi.md) - CYW43439/Pico W WiFi emulation.
  In progress: steps 0-3g done and live-boot-verified against real MicroPython Pico W firmware
  (v1.23.0/v1.28.0); step 4 (bridging to a real network) not started. The record's own header
  states exactly which step is current - don't assume this summary stays accurate as work
  continues.

Records keep a "Progress log"/inline status markers per phase/step - read those rather than
assuming from the filename alone whether something landed.

## Verification

Use `uv run pre-commit run --all-files` as the standard way to verify a change (runs `uv lock`,
`uv sync`, `mypy`, `ruff` lint+format, and pytest on **both** the pure-Python and native-Cython
builds, in the right order). Don't reach for ad hoc `pytest`/`ruff check`/`mypy` calls instead -
they duplicate config that already exists here and can drift from it. A narrower ad hoc command
(a single `pytest tests/test_x.py`) is fine for a fast inner loop while actively iterating, but
run the full `pre-commit` pass before considering work done.

If `uv` isn't on `PATH` in the shell, prefix with `export PATH="$HOME/.local/bin:$PATH"` first.

### Keep `uv` current - an old one silently breaks CI

**In any session with network access, check `uv --version` against the latest release and upgrade
before running `pre-commit`** (`python3 -m pip install --upgrade uv`, or `uv self update`). The
`uv-lock` hook runs `uv lock --upgrade`, so whatever `uv` you have *writes* `uv.lock` - and CI
installs the newest `uv` via `setup-uv`, then runs the same hook. If the two versions disagree
about the lockfile's contents, CI rewrites `uv.lock`, the `Pre-commit` workflow fails with
"pre-commit modified files, but auto-commit only runs on pull_request events", and the local run
looks perfectly green. It is not a dependency problem and the diff can be a single line: seen
2026-08-17, `uv 0.8.17` writes `exceptiongroup`'s dependency as `{ name = "typing-extensions",
marker = "python_full_version < '3.13'" }` where `uv 0.12.5` writes plain `{ name =
"typing-extensions" }`.

Two practical notes:

- After upgrading, **re-check `uv --version` with the same `PATH` export used above** - `pip`
  installs to `/usr/local/bin`, and a stale binary in `~/.local/bin` (which that export puts
  first) will keep shadowing it until you replace or symlink it.
- With no network to upgrade, don't commit a lockfile an old `uv` regenerated - revert the hook's
  change to `uv.lock` (`git checkout uv.lock`) and leave the refresh to a session that has one.

## Module layout

Keep conceptually distinct pieces in separate top-level modules rather than nesting one inside
another "for tidiness" - e.g. `boards.py` (board registry) and `external/` (the `ExternalDevice`
protocol) are siblings, not one inside the other, even though a board registry uses
`ExternalDevice`. Default to sibling top-level modules unless there's a real ownership/dependency
reason for nesting (e.g. `external/cyw43/bus.py` + `chip.py` + `nat.py` genuinely are all "the
CYW43439 implementation" and belong together under one `cyw43/` package). When a new module's
layout isn't obvious, ask rather than guess.

Prefer a real `@property`/setter over one class poking a bare (even `_`-prefixed) attribute on
another class it doesn't own - e.g. `Simulator` sets `rp2040.simulator = self` through a real
property, not `rp2040._simulator = self` directly.

## "Document" vs. "implement"

When asked to "document" a possible design/decision (задокументуй), that means write it into the
relevant doc as a note about what *should eventually happen* - it does **not** authorize
implementing it, even if the surrounding conversation included a design judgment call. This repo
has an established pattern of writing down future work separately from doing it (see the many
"not started yet"/"deferred, not designed" sections throughout `docs/records/`). Only cross
into actual code changes on an explicit, separate go-ahead. When unsure whether a message is
asking for a doc note or an implementation, ask.

## Threaded Simulator tests

Prefer driving `Simulator._execute_batch()` directly (synchronous, no real OS thread) over
`start_execution()` + a real background thread, whenever a test doesn't specifically need to
verify cross-thread behavior - the established pattern in most of `tests/test_simulator.py`.

When a test genuinely needs a real background thread (`schedule_threadsafe()`, cross-thread
callbacks, etc.):
- Always wrap the risky body in `try: ... finally: simulator.stop()`. A leaked engine-room thread
  with `core.waiting=True` busy-loops a full CPU core for the rest of the test session -
  "idle" doesn't mean cheap to leak here.
- Don't add a blocking "wait for settle" API to `Simulator` itself to paper over a race (a
  `Simulator.stop(wait=True)` blocking on `future.result()` was tried and reverted - it
  measurably slowed the whole suite and could hang under real system load). Fix the race at the
  boundary that actually needs the guarantee instead (e.g. make a shared fake like
  `time.monotonic()`'s test double tolerant of stray extra calls via an unbounded
  `itertools.count()`, rather than trying to bound the leaked thread's lifetime).
