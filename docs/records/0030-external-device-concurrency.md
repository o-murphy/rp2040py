# 0030. ExternalDevice concurrency model

- Status: Accepted
- Conceived: 2026-08-12
- Related: 0027 (epic), 0025 (asyncio)

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 652-686 -->

## Concurrency model for `ExternalDevice`s (2026-08-11)

**Question:** how should an attached `ExternalDevice`'s own ongoing work talk to the engine room
without racing it or stalling it — covering both "does real, possibly slow I/O" (CYW43439's NAT
bridge hitting the real internet) and "talks to a *second*, independently-running `RP2040`" (e.g.
two `Simulator`s bridged over a virtual serial cable, each on its own loop - per
docs/MAIN_THREAD_ASYNCIO_BACKLOG.md, that's the caller's own main-thread loop for the common case
now, or a dedicated background thread only for a caller that explicitly wants a second instance
running independently)?

**Decided: extend the existing `call_soon_threadsafe`/`run_coroutine_threadsafe` bridge
(`simulator.py:88-116`), not a thread per device.** Concretely, `RP2040`/`Simulator` grows one
small public method, `schedule_threadsafe(fn_or_coro)`, a thin wrapper around that same bridge —
already the proven mechanism by which outside callers (CLI, tests, GDB connections) safely reach
into a running engine room today. The contract for `ExternalDevice` authors:

- Everything `attach()` installs (GPIO listener callbacks, etc.) runs **synchronously, in-line, on
  the engine-room thread** — same as every existing GPIO listener (`ssi.py`, `gpio_pin.py`) — and
  **must never block**.
- Any slow or blocking work (a real `socket.connect()`/`recv()`, handing bytes to a *different*
  `RP2040`'s engine room) gets handed off via `rp2040.schedule_threadsafe(...)` instead of being
  done inline. Delivering a result back — into this engine room or into a different one entirely —
  uses the same call on whichever `RP2040` owns the target state:
  `other_rp2040.schedule_threadsafe(...)`. One mechanism covers both "reach out to the real
  internet" (CYW43439 NAT) and "reach into another simulator" (a serial-bridge device sitting
  between two independent `Simulator`s) — the target is just some other event loop, possibly on
  another OS thread, and this is the one blessed way to hand off to it safely.

**Why not a dedicated thread + mailbox queue per device (rejected):** the project already tried
and explicitly moved away from exactly this shape once. `pio.py:800-807`'s comment: a background
`threading.Timer` used to drive `RPPIO`'s continuation, it caused a real, reproduced race, and the
fix was moving everything onto the single engine-room asyncio loop instead — making races
"structurally impossible... not races are unlikely." A thread-per-`ExternalDevice` model
reintroduces the same class of problem this project already paid down.

