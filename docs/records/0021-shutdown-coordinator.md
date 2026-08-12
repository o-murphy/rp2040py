# 0021. Unified process-shutdown coordinator (Ctrl+X / --expect-text / SIGTERM)

- Status: Implemented
- Conceived: 2026-08-10
- Related: 0025 (asyncio migration)

<!-- migrated verbatim from docs/BACKLOG.md lines 1341-1419 -->

## Unified process-shutdown coordinator (Ctrl+X / --expect-text / SIGTERM) — DONE

**Goal:** every way `micropython`/`kaluma`/`run` can end - Ctrl+X, `--expect-text` matching,
`SIGTERM`, `Ctrl+C` - used to tear the process down via `os._exit()` from whichever thread noticed
first (the stdin reader, a `Simulator` worker thread, ad hoc). `os._exit()` works, but
unconditionally skips atexit/finally/every normal cleanup hook, and adding a new exit trigger meant
duplicating that teardown logic again at the new call site. Two concrete bugs came out of exactly
this pattern before the fix:

- A plain `SIGTERM` (`timeout`, `kill` without `-9`) while `StdioInteractiveRepl` had the terminal
  in raw mode left the real terminal stuck raw after the process died. `atexit.register()` doesn't
  cover it - confirmed empirically that Python's default `SIGTERM` disposition kills the process at
  the OS level before the interpreter ever runs atexit callbacks, no matter what's registered.
- `GDBTCPServer`'s accept thread is deliberately non-daemon ("a listening GDB server should keep the
  process alive by itself", matching Node's `net.Server.listen()`) and had no `close()` at all -
  switching any exit path from `os._exit()` to a plain `sys.exit()` while `--gdb` was active would
  have hung forever joining that thread. Found *while fixing the SIGTERM bug above*, before it ever
  shipped - not a separate incident, but real enough that it shaped the whole design: every exit
  path had to be gdb-server-safe from the start, not patched in after the fact.

**What landed:**

- `GDBTCPServer.close()` stops and joins the accept thread. Closing the listening socket out from
  under a thread blocked in `accept()` doesn't reliably unblock it on Linux (confirmed the hard way -
  an earlier close()-only version hung indefinitely in a test) and is undefined on Windows/macOS
  too, so `close()` instead uses a short (`0.2s`) `socket.settimeout()` poll against a
  `threading.Event` - portable by construction, not by luck.
- `Simulator.shutdown_request` (a `ShutdownRequest`: a `threading.Event` + exit code, first request
  wins) and `Simulator.wait_for_shutdown(cleanup=...)` - owned by `Simulator` itself, not the CLI,
  since a bare `Simulator` (no `Device`, no REPL - `_cmd_run`'s case) is the one thing every command
  actually has in common. Any thread with a reference to the simulator can call
  `simulator.shutdown_request.request(code)`; the thread actually driving the simulator is the only
  one that acts on it, running `cleanup()` once and then a real `sys.exit(code)`.
- `StdioInteractiveRepl(on_quit=...)`: Ctrl+X and a new `SIGTERM` handler (installed only when raw
  mode was actually engaged, previous handler restored on `stop()`) both call `on_quit(code)` instead
  of tearing the process down themselves. No `on_quit` (standalone use, no shutdown coordinator
  wired up) falls back to the original behavior unchanged - restore the terminal, `os._exit()`
  directly.
- `cli/__init__.py`'s `micropython`/`kaluma` commands compose per-resource cleanup
  (`repl.stop`, `gdb_server.close`, `device.stop`) via `contextlib.ExitStack`, registered right next
  to where each resource is created, instead of a hand-assembled "clean up everything" function that
  has to be kept in sync by hand with whatever the rest of the command does or doesn't construct.
  `wait_for_shutdown()` itself doesn't need an explicit `cleanup` argument in these commands - a
  `SystemExit` raised inside the `with ExitStack():` block unwinds through it normally, running every
  registered callback in reverse order.

**Verified**, not just unit-tested in isolation: a real `rp2040py micropython --gdb` subprocess
under a pty, booted to the REPL prompt, killed by `SIGTERM` mid-session - process exits in ~0.3s (not
hanging), the GDB port stops accepting connections, and the pty's actual termios state (reopened
fresh from its path, not the stale fd) comes back canonical. Same setup with Ctrl+X instead of
`SIGTERM` (a different triggering thread) - same result. Full test suite (450/450) plus dedicated
coverage: `tests/test_gdb_tcp_server.py` (`close()` unblocking/joining/idempotency, a real client
connecting beforehand), `tests/test_simulator_shutdown.py` (`ShutdownRequest`/`wait_for_shutdown`
integration, including a real `GDBTCPServer` to catch exactly the hang this exists to prevent),
`tests/test_stdio_repl.py` (`SIGTERM` restoring the terminal, the previous handler being restored on
`stop()`, both the standalone-fallback and `on_quit`-wired-up behavior for `SIGTERM` and Ctrl+X).

**Not done as part of this:** `_cmd_run`'s own `KeyboardInterrupt` path uses
`Simulator.wait_for_shutdown`'s generic mechanism too now, but `_cmd_run` has no `SIGTERM` handler of
its own (no `StdioInteractiveRepl` there) - an external `SIGTERM` against `run` still hits Python's
default disposition unchanged. Not a regression (that was already true before this work), just not
extended to a command that doesn't own a terminal to protect.

**Update - the standalone (no `on_quit`) fallback was later removed entirely.** Option (b) above
("replace `os._exit()` with a main-thread `sys.exit()`") turned out not to be separate, larger work
after all: `StdioInteractiveRepl` still carried a second shutdown mode alongside the coordinated one
- `on_quit=None` meant Ctrl+X/SIGTERM called `self.stop()` + `os._exit()` directly, from whichever
thread noticed. Nothing in this repo ever constructed it that way (`_cmd_micropython`/`_cmd_kaluma`
always pass `on_quit=shutdown.request`), but that second mode was the actual source of several
follow-up bugs: a "don't join myself" special case in `stop()` (needed only because standalone
Ctrl+X called `stop()` from inside the reader thread itself), a module-global `_active_raw_repl` +
`os_exit()` function purely so a raw `os._exit()` call could still find a terminal to restore, a
`"Pythonista3.app"` special-case inside that function, and a real fd-reuse race between sequential
test runs (a lingering reader thread's own termios-restore firing against a *different* test's pty
after fd numbers got recycled). `on_quit` is now a required constructor argument - every quit
trigger only ever signals it, never exits the process itself - and `os_exit()`/`_active_raw_repl`
are gone. `_cmd_mklittlefs`'s unrelated PyPy-finalization `os._exit(0)` (never coupled to
`_active_raw_repl` in the first place) now just calls `os._exit()` directly.

