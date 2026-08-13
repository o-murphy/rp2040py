**Resolved 2026-08-13 — root cause + fix in [docs/records/0041-cyw43-post-data-header-freeze-fix.md](../records/0041-cyw43-post-data-header-freeze-fix.md).**
Kept below as-is (investigation history), not rewritten - see that record for what actually
happened: an uncaught `RuntimeError` from an over-severe `.error()` log call silently killed
`Simulator.execute()`'s task, and every caller waiting on device-produced state hung forever with
nothing left to ever produce it.

# Task: CYW43 live-boot freezes solid (0% CPU) after early outbound `DATA_HEADER` traffic

Not a `docs/records/` entry - a working note for whoever picks this up next. Found while doing
step 3g's own required live-boot verification (`docs/tasks/3g-scripted-scan-join.md`,
`docs/records/0027-cyw43-wifi.md`) - **not a 3g bug itself** (3g's own code is unit-verified
correct - 42/42 tests, `pre-commit run --all-files` clean), but it currently blocks that
verification from ever reaching script completion, so whoever resumes 3g's live-boot check next
needs this context, and whoever picks up the "raw throughput ceiling" work
(`docs/tasks/simulation-clock-cython-port.md`) should read this too - it's very likely the *same*
already-flagged-but-uninvestigated finding from 0027's own "Same-day follow-up" section, now with
a concrete, reliable repro.

## Symptom, precisely - not "slow," genuinely frozen

0027's own documented "raw throughput ceiling" (`cyw43_delay_ms()`'s busy-wait costing far more
real wall-clock time than the simulated delay it represents) is a **busy-but-slow** problem: the
CPU's PC keeps varying throughout, real (if wasteful) work is happening the whole time. This is
different: `ps`-visible CPU time stops accumulating *entirely* (confirmed via repeated sampling,
zero increase across 90+ real seconds, then again across a full 10-minute window), the process
sits in `S` (interruptible sleep) state, and `/proc/<pid>/wchan` reads `ep_poll` - genuinely
blocked in an epoll wait, not executing anything. 0027's own "Same-day follow-up" section already
flagged something matching this shape once, under PyPy only, and explicitly did not chase it down:
"PyPy... in a 600s bounded run the CPU's PC sat completely frozen on one single address for the
last 130+ seconds - a different, more suspicious shape than the CPython+Cython baseline (which
kept visibly varying the whole time). Not root-caused; flagging as a possible PyPy-specific issue
worth a closer look, not just 'PyPy is slower here.'" **This session's finding: it is not
PyPy-specific** - the identical frozen/0%-CPU shape now reproduces on native CPython+Cython too.

## Reliable repro (2026-08-13)

```
uv run rp2040py --log-level info micropython --board pico_w --image v1.28.0 tests/micropython/main-cyw43.py
```

(native CPython+Cython; also reproduces under `uv run --python pypy3.10 --no-dev -- rp2040py ...`)
using a cached real `v1.28.0` `RPI_PICO_W` UF2. Freezes solid roughly 30-35 real seconds in, at a
consistent (not perfectly identical - varied by ~100 lines across repeated runs, `34878`/`34848`/
`34982`/`33078`-ish output line counts, suggesting real-time-sensitive scheduling is involved, not
a pure deterministic-instruction-count deadlock) point still well inside firmware bring-up, *before*
`nic.active(True)` finishes printing - i.e. before any of step 3g's own scripted `escan`/
`WLC_SET_SSID` code is even reachable.

**Isolated the trigger precisely** via a targeted `print(..., file=sys.stderr)` added temporarily
to `GSPIBus._write_wlan()`'s `DATA_HEADER` branch (removed again before landing - see
`docs/records/0027-cyw43-wifi.md`'s own 3g entry for the `DATA_HEADER`/flow-control-response fix
this branch is part of, a real, separate, already-fixed correctness bug found along the way): real
firmware sends *two* early outbound Ethernet frames well before `active(True)` completes - the
first (64 bytes) gets answered and drained cleanly (some other, later poll consumes it fine); the
freeze happens immediately after the **second** (104 bytes, decodes as an IPv6 packet -
`ethernet_frame[12:14] == 0x86dd`, consistent with Router Solicitation / Duplicate Address
Detection traffic from lwIP's own automatic IPv6 link-local setup, not application code). Confirmed
`self._selected == True` and nothing already queued (`rx_packet_pending=0`, `rx_queue_len=0`) at
the moment this second frame is handled - rules out a `GSPIBus`-side queuing bug specifically (the
FIFO activates/responds exactly the same way it did for the first frame, which drained fine).

**Confirmed NOT caused by `GSPIBus` answering `DATA_HEADER` at all**: temporarily reverting that
branch back to pure silent-ignore (the pre-3g, pre-fix behavior) makes the *same* script progress
past 127,000+ output lines without freezing (still climbing steadily when killed) - i.e. whatever's
freezing only manifests once firmware actually gets a flow-control response to react to. This
doesn't mean the fix is wrong (see the 0027 entry - the credit-desync deadlock it fixes is real,
confirmed via an independent, freeze-free Python-level `GSPIBus` reproduction of 200+ mixed
ioctl/data sends) - it means answering `DATA_HEADER` correctly is what lets firmware's execution
reach *this* already-existing freeze for the first time, the same relationship 3g's own scripted
scan/join responses have to it (letting execution get further than the old generic-ack-only
baseline ever reached).

**Ruled out**: CPU frequency governor artifact (0027's own documented gotcha from a previous
session) - `scaling_governor` was `powersave` but `scaling_cur_freq` was ~3.9GHz of a 4.1GHz max,
not throttled. Not a resource-contention artifact either - `uptime` load average was unremarkable
(~1.5-1.8 on a 6-core machine) with no other competing `rp2040py`/`micropython` processes running.

## Not yet possible in this environment - real blocker for whoever picks this up

**No stack trace obtained** - `gdb -p <pid>` and `py-spy dump --pid <pid>` (via `uvx py-spy`) both
refused: `/proc/sys/kernel/yama/ptrace_scope` is `1` (restricted - only a direct parent process may
ptrace) and there's no passwordless `sudo` in this environment. Whoever picks this up with a
friendlier ptrace policy (or willing to `sudo`) should start there - a single `py-spy dump` while
it's frozen would very likely settle this in minutes instead of hours: is the engine-room task
awaiting an `asyncio.Event`/`Future` that nothing will ever set (a real, fixable concurrency bug),
or a real `asyncio.sleep()` for a computed real-world duration that's merely absurdly, but
finitely, long (a scaling/units bug somewhere in the clock/alarm fast-forward path - `nanos` vs
`micros` vs `millis` mixups are exactly the kind of thing that produces a 1000x-too-long sleep)?

## Where to look, if picking this up without a stack trace

- `clock/simulation_clock.py`'s alarm-scheduling/`nanos_to_next_alarm` path, and whatever computes
  a real-world `asyncio.sleep()` duration from it in `simulator.py`/`_execute_batch.py` - a unit
  mismatch there would produce exactly this shape (genuinely idle, blocked on a real timer that's
  correct in *kind* but wrong in *magnitude*).
- Whatever RP2040 timer/alarm real firmware's lwIP IPv6 stack uses to schedule its own Router
  Solicitation retry / Duplicate Address Detection timeout (`pico-sdk`'s `alarm_pool`/hardware
  timer, or MicroPython's own `mp_hal_ticks_ms()`-based scheduling) - if real firmware is waiting
  on a *correctly-computed* several-second retry timer and the freeze is simply that (not yet
  ruled out - only confirmed frozen through ~10 real minutes, not proven never to resume), that's
  arguably not a bug at all, just something to let run longer or bound with `--expect-text`/a
  wrapping timeout for CI purposes. Distinguishing "correct but slow" from "genuinely will never
  fire" is exactly what the missing stack trace would resolve quickly.
- CLAUDE.md's own note that a leaked engine-room thread with `core.waiting=True` busy-loops (100%
  CPU) is *not* what's observed here (0% CPU) - so if this is a `core.waiting`-adjacent condition
  at all, it's a different one than that existing documented case, not the same leak.

## Don't re-derive

- The credit-desync `DATA_HEADER` fix (`_build_flow_control_response()`) is separately confirmed
  correct and necessary - not what this task is about, don't revert it to "fix" this freeze
  without addressing the credit-desync it exists for instead.
- 3g's own code (`escan`/`WLC_SET_SSID` scripting) is unit-verified correct and was never reached
  in any of the freezing runs (the freeze happens during firmware bring-up, well before
  `active(True)` even finishes) - this is not evidence against 3g's own correctness, just an
  earlier, unrelated blocker in the path to observing 3g's own behavior live.
