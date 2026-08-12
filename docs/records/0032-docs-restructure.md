# 0032. Documentation restructure — numbered event-log records

- Status: Accepted / in progress
- Conceived: 2026-08-12
- Related: supersedes the monolithic `docs/*_BACKLOG.md` files (see 0000-TRACKER.md)

## Context

The docs directory had grown into a handful of large, mixed-purpose files
(`BACKLOG.md` ~1460 lines, `CYW43_WIFI_BACKLOG.md` ~1140, `PORTING.md` ~800, plus
the asyncio/JIT backlogs). Each file glued together several unrelated concerns with
different lifespans:

- **durable design** (how a subsystem works, why a decision was made),
- **volatile status** (`DONE`, `merged`, `not started`, `resolved during PR N`),
- **one-off research** (reverse-engineering, protocol references),
- **bug postmortems** (root-cause writeups).

The volatile status buried the durable signal, so "what is the current design of X?"
required reading through the status history of several PRs. The files were working
memory for cross-session continuity, and development happens in interleaved phases —
so a single file per epic conflated the whole timeline.

## Decision

Reorganize `docs/` as an **append-only event log of numbered records**.

- **Number = a record**, assigned in the order ideas/notes arose, immutable. One
  shared counter covers both **ideas** and **research/postmortem notes**;
  **references** (living how-to / checklists) are *not* numbered.
- Each record carries a `Status` and append-only, dated **events**
  (`Proposed` → `Implemented` / `Rejected` / `Superseded`) with cross-links.
  Nothing is edited in place or deleted — only appended.
- `Superseded` uses a **two-way link**: the losing record points to the winner and
  vice-versa (e.g. 0014 threading model ↔ 0025 asyncio migration).
- **Tiered granularity, per record:**
  - **B1** — one file, events as sections inside. For short-lived ideas
    (proposed → quickly implemented/closed, no cross-cutting).
  - **B2** — separate small files/records per event. For long-running, cross-cutting
    threads (threading→async, the performance arc, the CYW43 epic).
- `0000-TRACKER.md` is a **projection of state** — one row per idea (checkbox + link
  + state + short note). Notes have no lifecycle row; they are linked from ideas.
- A changelog can be derived from the tracker by filtering to
  `Implemented`/`Rejected` events in date order.

## Why not plain ADR

Architecture Decision Records are *immutable decisions*. Much of this content is not a
decision: it is living work with state, research, and postmortems. Forcing all of it
into ADR form would misfit the research/checklist material and lose the state tracking
that the project actually relies on. This model keeps the ADR virtues (stable numbered
records, rationale preserved) while adding explicit state and a chronological spine.

## Consequences

- **Zero information loss** was a hard requirement — including negative results, so we
  never revisit a dead end twice. Migration relocated content **verbatim** (exact line
  slices), never re-worded; only ephemeral git-state phrasing (`staged uncommitted on
  top of <sha>`) was dropped, since git already records it.
- Rejected/negative results (e.g. 0015 memcpy HLE) live permanently in their record's
  `Rejected` section and must **never** reappear as a backlog TODO.
- Distillation of verbose history is deliberately **deferred** while `cythonize/pio` is
  an active branch; for now records are relocated, not trimmed.
- The B1/B2 split is a hybrid that can later collapse to one variant without breaking
  numbers or links.

## Migration mapping

The full old-block → record mapping (with source line ranges, dates and PRs) is the
basis for records 0001–0031. See 0000-TRACKER.md for the index.

## Appendix — original BACKLOG.md preamble (preserved verbatim)

<!-- migrated verbatim from docs/BACKLOG.md lines 1-21 -->

# Backlog / in-progress work notes

Working notes for tasks that span multiple sessions. Not user-facing docs — see README.md /
PORTING.md / CHANGELOG.md for those. Items large enough to need their own file:
[docs/JIT_BACKLOG.md](JIT_BACKLOG.md) (basic-block fusion / mini-JIT),
[docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md) (replacing every
`threading`-based workaround - `stdio_repl.py`'s reader thread, `pio.py`'s `RLock`,
`gdb_tcp_server.py`'s accept-thread poll, ... - with one `asyncio` event loop),
[docs/MAIN_THREAD_ASYNCIO_BACKLOG.md](MAIN_THREAD_ASYNCIO_BACKLOG.md) (put `Simulator`'s
engine-room loop on the process main thread instead of a dedicated background thread, matching
upstream rp2040js's single-threaded model for the common single-instance case - **done**, all 5
phases landed and verified),
[docs/CYW43_WIFI_BACKLOG.md](CYW43_WIFI_BACKLOG.md) (Pico W WiFi emulation - new feature, not a
porting gap; gSPI protocol fully documented from the official BSD-licensed `pico-sdk`/`cyw43-driver`
source - command header bit layout, register maps, real `WLC_*`/`WLC_E_*` IDs - plus the phased plan
under consideration; steps 0-3f done and verified against a real MicroPython Pico W boot, step 3g
next - see that file's "Implementation order"). Currently paused mid-3g in favor of the simulator
performance side quest documented in this file's own "Cython port of the interpreter core" section
below (PIO port + opt-in `clock.tick()` batching), found while profiling why a real cyw43 firmware
boot was so slow.

