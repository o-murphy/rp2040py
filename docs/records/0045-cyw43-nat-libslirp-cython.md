# 0045. CYW43 step 4 (NAT bridge): bind real `libslirp`, native-only

- Status: Proposed
- Conceived: 2026-08-14
- Related: 0027 (epic, step 4), 0028 (module layout - `external/cyw43/nat.py`), 0024 (protocol
  research - SLIRP-style NAT rationale), 0030 (external-device concurrency -
  `schedule_threadsafe()`), 0013 (Cython interpreter core - the dual-mode pattern this follows)

## Decision (proposed, not yet implemented)

Supersedes 0027's own 2026-08-11 step-4 design note, which reads:

> *Not* the actual `libslirp` C library QEMU embeds — a SLIRP-**style** userspace NAT written in
> plain Python: ordinary unprivileged `socket.connect()`/`send()`/`recv()`/`getaddrinfo()` calls
> made by the `rp2040py` process itself, no TUN/TAP interface, no raw sockets, no root.

New direction: bind the real, upstream `libslirp` C library as an **optional native extension** for
step 4's NAT/TCP bridge, added to this project's standard dual-mode pattern (`native/` optional
Cython extension + pure-Python fallback, `docs/records/0013-cython-core.md`) rather than as an
exception to it:

- **`external/cyw43/bus.py`/`chip.py` stay exactly as they are today - pure Python, unchanged.** No
  rewrite. Steps 0-3g (bus decode, SDPCM/ioctl framing, scripted scan/join) are already real,
  working, and live-boot-verified against real MicroPython firmware - this stays the reference
  implementation living in `src/`, not something rewritten away or copied out to `examples/`.
- **`libslirp` binding is native-only, and starts as a proof of concept**, not a finished feature.
  `nat.py` (or wherever the binding ends up living) is Cython (`.pyx`), built as part of the existing
  optional `native/`-style extension machinery, gated on a compiler/Cython/`glib`-`pkg-config` all
  being available - same "optional, skip gracefully" shape `setup.py` already uses, extended to cover
  the new `glib` dependency specifically (open trade-off, see below - unresolved here).
- **Pure-Python NAT is explicitly deferred, not designed, not scheduled.** A pure-Python build keeps
  today's existing `_write_wlan()` behavior for inbound `DATA_HEADER` frames - acknowledge with
  `_build_flow_control_response()`, silently drop the real Ethernet payload - exactly what already
  ships and is already proven safe (0038/0041 already fixed the real bugs in this path; nothing
  about it changes here). Whether a pure-Python NAT implementation is ever worth building once the
  native `libslirp` PoC proves the approach out is an open question for a future session.
- **`boards.py`'s `pico_w` spec keeps attaching a real `Cyw43439` unconditionally**, native or
  pure-Python, exactly as today - no gating at the `ExternalDevice`-attach level. The only thing
  gated on native-extension availability is whether `bus.py`'s `DATA_HEADER` path can call into a
  working `nat.py` underneath it.

## Why superseded, not just amended

The original paragraph's own stated reason to avoid libslirp - "no TUN/TAP interface... unlike
QEMU's `tap` netdev mode, which needs `CAP_NET_ADMIN`/root on Linux, `vmnet.framework` on macOS, or
an installed TAP driver on Windows" - doesn't actually distinguish libslirp from the plain-Python
approach it's arguing for. **libslirp is the C library behind QEMU's `-netdev user` mode
specifically** - the no-root, no-TUN/TAP, ordinary-host-socket-API mode - not `-netdev tap`, which
is the thing that actually needs root/`vmnet`/a TAP driver. The property the original paragraph
wants ("never drops below the ordinary client-socket API... expected to work identically on Linux,
macOS, and Windows") is a property libslirp itself already has. The original call appears to have
conflated "the actual `libslirp` C library QEMU embeds" with QEMU's unrelated `tap` backend, not a
real functional gap in libslirp itself.

## Why libslirp over a hand-written Python mini-stack

0027's step 4 needs, at minimum, DHCP (so `ifconfig()` settles to real addresses, matching the
`10.10.0.1`/`255.255.0.0`/`10.0.0.1`/`10.0.0.1` shape 0024 already confirmed from Wokwi), ARP, ICMP
echo, and - to satisfy the epic's own stated goal (`urequests.get()` reaching the real internet,
0024's own confirmed capability bar) - a real TCP proxy: a per-connection state machine tracking the
guest lwIP stack's SEQ/ACK/window against a real host socket's `connect()`/`send()`/`recv()`, i.e. a
small user-mode TCP/IP stack in its own right. `libslirp` already *is* exactly that: TCP, UDP,
ICMP, DHCP, DNS forwarding, and ARP, over ordinary unprivileged host sockets, actively maintained as
part of the QEMU project (BSD-licensed, C, no root/TUN/TAP). Hand-rolling the TCP piece in pure
Python would be the single highest correctness-risk, highest-effort piece of the entire 0027 epic;
binding a real, already-correct implementation avoids re-deriving a TCP state machine the way
`bus.py`'s gSPI/SDPCM work re-derived Broadcom's own protocol byte-for-byte from source; there
libslirp itself is the "real source."

## Related finding: an absent CYW43 device doesn't hang real firmware (live-verified 2026-08-14)

Not load-bearing for the decision above (attach gating isn't part of this plan - see "Decision"),
but worth keeping as a fact established this session: confirmed via a throwaway scratchpad harness
(no tracked files touched) that real MicroPython v1.28.0 `RPI_PICO_W` firmware, booted against
`tests/micropython/main-cyw43.py` with **no** `Cyw43439` device attached to the gSPI bus at all
(monkeypatched `boards.BOARDS["pico_w"]` to drop it from `extras`), does **not** hang - it reaches
the REPL and completes in 4.3s wall-clock. `network.WLAN(...)`/`nic.active(True)` both return
normally (prints `[CYW43] Failed to start CYW43`, consistent with 0027's own notes on `v1.28.0`'s
`active()` semantics); the first real failure is a clean `OSError: [Errno 1] EPERM` at `nic.scan()`
- an ordinary Python exception, not a stall. Only this one firmware version/board/harness
combination was exercised - not a general guarantee across every supported firmware.

## Trade-off acknowledged, not resolved here

`libslirp` needs `glib`/`pkg-config` at build time - a heavier, more platform-fiddly dependency than
the existing header-only `native/` Cython interpreter core (`setup.py` currently links no external
C library at all). Cross-platform wheel building (`[tool.cibuildwheel]` in `pyproject.toml`, which
today only has to worry about the interpreter core across CPython versions/abi3) will need real
per-platform verification - Linux/macOS/Windows glib availability, and what "optional, skip
gracefully" looks like when glib specifically (not just a compiler) is missing - before this can be
called done. Flagged as follow-up work for whoever implements this, not designed here.

## Not designed here (deferred to implementation)

- The concrete libslirp C API binding shape: Cython `cdef extern` declarations directly against
  `libslirp.h`, vs. a small hand-written C shim compiled alongside it.
- How `slirp_input()` (guest→host, i.e. `GSPIBus._write_wlan()`'s existing `DATA_HEADER` path,
  today `bus.py`'s `_build_flow_control_response()`-only stub) and the `SlirpCb` callback table
  (`send_packet` for host→guest, plus its timer/register-poll/etc. callbacks) map onto this
  project's existing `GPIOPin` listener model and `queue_rx_packet()`/`schedule_threadsafe()`
  primitives (`docs/records/0030-external-device-concurrency.md`'s own contract: `attach()`-installed
  code must never block; real socket I/O goes through `schedule_threadsafe()`).
- A revised implementation-order sub-step breakdown for step 4 (the `4a`/`4b`/... shape 0027's own
  step 3 used for `3a`-`3g`) - not attempted here; left for the session that actually starts this
  work.
- Exact SDPCM data-frame envelope parsing needed on the `bus.py` side before any of this - already
  flagged as unresolved research homework in 0027's own "Open questions" section - still applies
  unchanged.
- How the native-only `nat.py` binds into `bus.py`'s `DATA_HEADER` path *only when built* (an
  import-time check, a capability flag on `Cyw43439`, something else) - not specified here.

This record documents the decision and its rationale only, per this repo's own document-vs-implement
convention. No source file changes, no build changes, and no `libslirp`/`glib` dependency were added
as part of writing this record.
