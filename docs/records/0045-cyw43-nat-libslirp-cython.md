# 0045. CYW43 step 4 (NAT bridge): embed `PyTCP`, optional runtime dependency

- Status: Proposed
- Conceived: 2026-08-14
- Related: 0027 (epic, step 4), 0028 (module layout - `external/cyw43/nat.py`), 0024 (protocol
  research - SLIRP-style NAT rationale), 0030 (external-device concurrency -
  `schedule_threadsafe()`), 0013 (Cython interpreter core - the dual-mode pattern this record no
  longer follows, see "Why not native/Cython after all" below)

## Decision (proposed, not yet implemented)

Supersedes this same record's own earlier same-day text (2026-08-14), which proposed binding the
real, upstream `libslirp` C library as a native-only Cython extension (`native/`-style, gated on a
compiler/Cython/`glib`-`pkg-config` all being available). That direction is itself superseded here,
not amended - see "Why superseded again" below for why `libslirp` didn't survive scrutiny of its own
build-time trade-off.

New direction: embed `PyTCP` (github.com/ccie18643/PyTCP) - a pure-Python, zero-runtime-dependency,
RFC-audited TCP/IP stack (~13,650 unit/integration tests, 120+ per-RFC adherence audits kept in its
own repo) - as an **optional runtime dependency**, not a native extension:

- **`external/cyw43/bus.py`/`chip.py` stay exactly as they are today - pure Python, unchanged.** No
  rewrite. Steps 0-3g (bus decode, SDPCM/ioctl framing, scripted scan/join) are already real,
  working, and live-boot-verified against real MicroPython firmware - this stays the reference
  implementation living in `src/`, not something rewritten away or copied out to `examples/`. This
  part of the original decision survives both supersessions unchanged.
- **No native extension, no build-system changes.** `PyTCP` is pure Python; `nat.py` (or wherever
  the binding ends up living) needs no `.pyx`, no `setup.py` changes, no `[tool.cibuildwheel]`
  changes. Gating is a plain `try: import pytcp except ImportError:` - the same "optional, skip
  gracefully" spirit `native/` uses, just at the ordinary Python-dependency level instead of the
  compiled-extension level.
- **Architecture: PyTCP terminates the guest-facing leg only; a real host socket does the rest.**
  PyTCP is embedded in-process (`stack.init()` → `add_interface()` → `start()`, then its own
  Berkeley-sockets-style API), fed raw Ethernet frames from `bus.py`'s `DATA_HEADER` path (see
  "Resolved research finding" below for the exact envelope) via `add_interface()`, and terminates
  the guest's TCP connection with its own real, tested state machine (full congestion control -
  CUBIC/NewReno/PRR/HyStart++ - SACK/RACK-TLP, RFC 5961 hardening). Our own glue code then relays
  payload bytes between that accepted PyTCP-side connection and a real `socket.socket()`/`asyncio`
  connection actually reaching the internet - architecturally identical to how `passt` (the modern
  QEMU/Podman-ecosystem replacement for `libslirp`/`slirp4netns`, evaluated and set aside during this
  same session's discussion - see "Alternatives considered" below) itself works, per its own man
  page: *"reflecting one peer's observed parameters... to the corresponding peer."* PyTCP does the
  hard, correctness-risk part (a real TCP state machine); our glue code is a thin splice, not a
  second state machine.
- **Pure-Python NAT is no longer a separate deferred tier - `PyTCP` *is* the pure-Python
  implementation.** The earlier libslirp-based text of this record treated "pure-Python NAT" as
  future work distinct from the native path; that distinction doesn't apply here, since this whole
  design is pure Python. Until `nat.py` is actually built, `bus.py`'s existing `_write_wlan()`
  behavior for inbound `DATA_HEADER` frames stays exactly as it is today - acknowledge with
  `_build_flow_control_response()`, silently drop the real Ethernet payload (0038/0041 already fixed
  the real bugs in this path; nothing about it changes here).
- **`boards.py`'s `pico_w` spec keeps attaching a real `Cyw43439` unconditionally**, exactly as
  today - no gating at the `ExternalDevice`-attach level. The only thing gated on `pytcp` being
  installed is whether `bus.py`'s `DATA_HEADER` path can call into a working `nat.py` underneath it.

## Why superseded again: `libslirp`'s own build-time trade-off didn't survive scrutiny

The libslirp proposal's own "Trade-off acknowledged, not resolved here" section (this record's
first revision, same day) flagged `glib`/`pkg-config` as a "heavier, more platform-fiddly
dependency" needing "real per-platform verification" before being called done. Working through that
verification concretely (this session) found it doesn't reduce to a verification task - it's a real
gap: `libslirp` has no Windows packaging story at all (would need `vcpkg`/MSYS2, not something a
`pip install`-ing end user has), and no `cibuildwheel`-integrated way to install `glib` + `libslirp`
dev headers on Windows CI runners either. This matters more here than it would for a narrower
project: `pyproject.toml`'s `[tool.cibuildwheel]` matrix already targets Linux (x64/x86/ARM64/
ARMv7), Windows (x64/x86/ARM64), macOS (Apple Silicon/Intel), and Android - a `libslirp`-shaped
native extension would only ever work on a subset of that matrix, with no line of sight to the rest.

## Alternatives considered (this session), and why each was set aside

Worked through in order, each ruled out for a different, concrete reason - kept here so a future
session doesn't re-derive the same dead ends:

- **`ctypes` binding against a system-installed `libslirp.so`/`.dylib`/`.dll`, instead of Cython.**
  Removes the build-time `pkg-config`/`glib`-headers problem entirely (no compilation - `glib` is
  `libslirp`'s own transitive runtime link dependency, invisible to a `ctypes.CDLL()` caller), but
  doesn't remove the *runtime* problem: end users still need `libslirp`'s shared library present on
  their machine, realistic on Linux (`apt`), plausible on macOS (`brew`), essentially never true on
  Windows by default. Also trades compiler-checked struct layouts for hand-declared `ctypes.Structure`
  definitions - a real, silent-corruption-shaped correctness risk with no compiler to catch a
  mismatch.
- **`passt` (github.com/containers/passt), spawned as a subprocess, talking over a UNIX domain
  socket using the same length-prefixed raw-Ethernet-frame protocol QEMU's own `-netdev stream`
  backend uses.** Genuinely the strongest *native-tool* option evaluated: no C binding at all (not
  even `ctypes`), zero build/CI changes, actively maintained (Red Hat, now Podman's default over
  `slirp4netns`), and its own concurrency story is simpler than any in-process library (plain
  non-blocking UNIX-socket I/O fits directly on the existing engine-room `asyncio` loop, no dedicated
  thread needed). Set aside for one reason only: **`passt` is Linux-only, full stop** - no macOS or
  Windows port exists, or is architecturally close to existing (it leans on Linux-specific
  `epoll`/`io_uring`/`seccomp` internals even in its plain socket mode, not just its namespace-based
  `pasta` mode). Given this project's actual cross-platform target matrix, a Linux-only step 4 was
  judged not good enough as the *primary* design, though it remains a plausible future
  Linux-specific optimization layered on top of the PyTCP baseline (not designed here).
- **`slirp4netns`.** Same Linux-only conclusion as `passt`, for a heavier reason (needs an
  unprivileged user+network namespace and a real `/dev/net/tun` inside it, more moving parts than
  `passt`'s plain socket mode) with no offsetting advantage - set aside without further evaluation
  once `passt` was already ruled out on the same platform-support grounds.
- **A hand-rolled pure-Python TCP responder, deliberately simplified (no independent congestion
  control - just reflecting a real host socket's own observed ACK/window, matching `passt`'s own
  documented internal architecture).** This was the working direction immediately before finding
  `PyTCP` - technically viable (CPython's own `socket`/`asyncio` modules do real, correct TCP for
  the host-facing leg; only a thin reflector for the guest-facing leg would be new code), but means
  shipping new, unaudited protocol logic instead of reusing something already extensively tested.
  Confirmed CPython's own stdlib has no bundled TCP protocol engine to lean on for this (`socket`/
  `asyncio` are wrappers around the OS kernel's TCP, not a reusable userspace implementation) -
  searched `/usr/lib/python3.14` directly for any TCP state-machine code, found none. `PyTCP`
  supersedes this option: same "pure Python, works everywhere" property, but the state machine is
  already written, tested, and RFC-audited rather than new.
- **`gVisor`'s `pkg/tcpip` netstack** (Apache-2.0, Go; the same userspace stack Tailscale uses for
  its own userspace WireGuard networking) and **`lwIP`** (BSD, C, zero external dependencies -
  already vendored as this project's own guest firmware's TCP/IP stack, `/home/murphy/pyproj/
  micropython/lib/lwip`, currently an uninitialized submodule there) were both considered as
  possible engines for the same splice architecture. Recorded here as **documented fallback
  candidates, not designed further** - revisit specifically if `PyTCP`'s pure-Python performance
  proves inadequate once actually measured against a real workload. `gVisor` would introduce a Go
  toolchain to a project that currently has none; `lwIP` is licence-compatible and dependency-free
  but, being C, reintroduces a Cython/`ctypes` binding (without `libslirp`'s `glib` problem
  specifically, since `lwIP` has no external dependency beyond libc).

## Why `PyTCP` over binding a C library at all

`libslirp` and `passt` both solve the "highest correctness-risk piece of the whole 0027 epic" (a
real TCP state machine) by wrapping an existing, correct C/native implementation - the reasoning
this record's first revision gave for preferring `libslirp` over hand-rolling. `PyTCP` gives the
same property - a real, already-correct, extensively-tested (13,650+ tests, 120+ RFC audits)
implementation, not something re-derived from scratch - while also being the *only* option
evaluated this session with zero platform gaps: pure Python means it runs identically everywhere
this project already ships wheels, with no native build, no `pkg-config`, no subprocess, no
runtime shared-library dependency at all.

**License note:** `PyTCP` is GPL-3.0; `rp2040py` itself is MIT. Confirmed explicitly with the
project maintainer (this session, 2026-08-14) that an optional runtime dependency - imported only
when installed, never vendored or statically linked into this project's own distributed code - is
acceptable for this use.

## Load-bearing open question - not resolved this session, no PoC was run

`PyTCP`'s own README states its current scope plainly: *"Host-stack parity is the current scope;
router-grade forwarding is planned."* That is: `PyTCP` is designed to answer for its own configured
interface address(es), as a real host's own TCP/IP stack would - not, by its own admission, to
forward/NAT traffic addressed to arbitrary third-party destinations the way `libslirp`/`passt` are
purpose-built to do. The splice architecture above needs `PyTCP`'s embedded stack to accept/surface
an inbound SYN whose destination is **not** its own configured `10.0.0.1` gateway identity, since
the guest's own lwIP will target real internet IPs (e.g. `93.184.216.243:80`), not `PyTCP`'s own
address.

This is the single first thing a real implementation attempt needs to test empirically - not
assumed to work from reading the README alone. If a plain accept doesn't surface such connections,
the first mitigation to try is configuring `PyTCP`'s interface to treat the entire guest-visible
`10.0.0.0/16` subnet as locally-owned (proxy-ARP-shaped), before concluding the current embedding
model doesn't fit and falling back to `lwIP`/`gVisor` per the "Alternatives considered" section
above.

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

## Resolved research finding: the SDPCM `DATA_HEADER` data-frame envelope

Flagged as unresolved in 0027's own "Open questions" section, and in this record's own earlier
"Not designed here" section - now resolved, verified directly against
`/home/murphy/pyproj/micropython/lib/cyw43-driver/src/cyw43_ll.c` (the local `pico-sdk`/
`cyw43-driver` checkout this project already sources the rest of the CYW43 protocol from):

- **Outbound (guest→host, `cyw43_ll_send_ethernet()`, `cyw43_ll.c:795-819`):** a 12-byte
  `sdpcm_header_t` with `header_length` set to `SDPCM_HEADER_LEN + 2 = 14` specifically for
  `DATA_HEADER` frames (`cyw43_sdpcm_send_common()`, `cyw43_ll.c:~703`), followed by 2 bytes of
  padding (`"there are 2 bytes of padding after the sdpcm header, corresponding to the +2 in
  cyw43_sdpcm_send_common for DATA_HEADER"`, the driver's own comment), followed by a 4-byte
  `sdpcm_bdc_header_t` (`flags=0x20, priority=0, flags2=<interface>, data_offset=0`,
  `cyw43_ll.c:788-793,806-810`), followed by the raw Ethernet frame payload.
- **Inbound (host→guest, `sdpcm_process_rx_packet()`'s `DATA_HEADER` case, `cyw43_ll.c:889-902`):**
  the receiver locates the BDC header via `header->header_length` (sender-defined, not hardcoded on
  the receive side) and reads the interface number from `bdc_header->flags2`, skipping
  `BDC_HEADER_LEN + (data_offset << 2)` bytes to reach the payload - i.e. any sender using the same
  `header_length=14`/`data_offset=0` shape as the outbound side above is valid.
- This matches the shape `bus.py`'s own `_build_async_event()` already uses for its BDC header
  (`bdc_header = bytes([0x20, 0, interface, 0])`) - confirms that existing lead was the right one,
  just not previously checked against the real data-path source directly.

Also folded into 0027's own "Open questions" section as "Resolved (2026-08-14)", matching that
section's existing per-item resolution-date convention.

## Not designed here (deferred to implementation)

- The concrete `PyTCP` embedding API surface actually needed (`add_interface()`'s exact signature
  for feeding/reading raw frames programmatically rather than via a real TAP fd; which parts of its
  Berkeley-sockets API a splice needs versus its daemon/CLI-oriented surface, which is not relevant
  to in-process embedding).
- How accepted `PyTCP`-side connections and the `SlirpCb`-style host→guest delivery map onto this
  project's existing `GPIOPin` listener model and `queue_rx_packet()`/`schedule_threadsafe()`
  primitives (`docs/records/0030-external-device-concurrency.md`'s own contract: `attach()`-installed
  code must never block; real socket I/O goes through `schedule_threadsafe()`).
- A revised implementation-order sub-step breakdown for step 4 (the `4a`/`4b`/... shape 0027's own
  step 3 used for `3a`-`3g`) - not attempted here; left for the session that actually starts this
  work.
- How the optional `nat.py` binds into `bus.py`'s `DATA_HEADER` path *only when `pytcp` is
  installed* (an import-time check, a capability flag on `Cyw43439`, something else) - not
  specified here.
- Whether/how to actually run the feasibility PoC the "Load-bearing open question" section above
  calls for, and what to do if the answer is no - left for the session that starts implementation.

This record documents the decision and its rationale only, per this repo's own document-vs-implement
convention. No source file changes, no build changes, and no `pytcp` dependency were added as part
of writing this record.
