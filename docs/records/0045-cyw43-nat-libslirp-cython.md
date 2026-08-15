# 0045. CYW43 step 4 (NAT bridge): embed `gVisor`'s `pkg/tcpip` via a `cgo` shared library

- Status: Proposed
- Conceived: 2026-08-14
- Related: 0027 (epic, step 4), 0028 (module layout - `external/cyw43/nat.py`), 0024 (protocol
  research - SLIRP-style NAT rationale), 0030 (external-device concurrency -
  `schedule_threadsafe()`), 0013 (Cython interpreter core - the dual-mode pattern this record
  returns to below, after a same-day detour through pure-Python `PyTCP`)

## Decision (revised 2026-08-14, third same-day revision - see "Latest decision" below)

**Superseded again, same day - see "Latest decision: `gVisor` via `cgo`" further down this
record.** The `PyTCP` direction below was itself empirically tested this session and found not to
fit (see "Load-bearing open question" section's "Resolved" text); `gVisor`'s `pkg/tcpip` was then
evaluated and empirically confirmed working as a replacement, and the user picked the in-process
`cgo`-shared-library integration shape over a subprocess. The text immediately below is kept
verbatim for the append-only trail, not because it's still the plan.

## Decision (proposed, not yet implemented) [SUPERSEDED - see above]

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

**Resolved (2026-08-14): plain accept does not work, and the proposed mitigation does not apply -
the embedding model as designed here doesn't fit.** Tested empirically against real `pytcp==2.7.6`
(pinned via `uv add pytcp`, see "Packaging findings" below), by driving `pytcp.lib.stack`'s
internal singletons directly (see next finding) and injecting a hand-built raw Ethernet/IPv4/TCP
SYN frame, source `10.0.0.2:54321`, destination `93.184.216.243:80` (a foreign address, never
assigned to the stack) - reproducible via the throwaway scratchpad script this session used
(no tracked files touched by the script itself):

- **Baseline (stack owns `10.0.0.1/24`, matching the record's own gateway address):** the SYN is
  dropped at the IPv4 layer before ever reaching TCP. `pytcp/protocols/ip4/phrx.py`'s `_phrx_ip4()`
  has an unconditional destination-ownership check - `if self.ip4_unicast and packet_rx.ip4.dst not
  in {*self.ip4_unicast, *self.ip4_multicast, *self.ip4_broadcast}: drop` - and logs exactly `IP
  packet not destined for this stack, dropping`. `self.ip4_unicast` is a flat list built from
  individually DAD/ARP-probed `Ip4Host` entries (`packet_handler.py`'s `ip4_unicast` property, fed
  by `assign_ip4_address()`) - there is no CIDR/subnet-range membership check anywhere in this
  path, only exact-address set membership.
- **The record's own proposed mitigation ("treat the entire guest-visible `10.0.0.0/16` subnet as
  locally-owned, proxy-ARP-shaped") turns out not to apply to this failure mode at all, independent
  of whether it could be implemented:** a real internet destination like `93.184.216.243` is never
  a member of the guest-local `10.0.0.0/16` range in the first place. "Claiming the guest subnet"
  only ever helps the stack answer for guest-adjacent addresses (e.g. spoofing a second gateway-side
  host on the same LAN) - it does nothing for the actual guest→internet NAT case the whole splice
  architecture exists for. The mitigation as literally described was based on an incomplete model of
  the gap; there is no revision of it that fits without also solving the next bullet.
- **A second, independent, and unconditional gate exists on the TX side with no equivalent
  bypass.** Tested a workaround (never call `assign_ip4_address()`, disable DHCP, so
  `ip4_host`/`ip4_unicast` stay permanently empty - `packet_handler.py`'s `acquire_ip4_addresses()`
  then auto-sets `config.IP4_SUPPORT = False`, so this needs additionally forcing
  `config.IP4_SUPPORT` back to `True` after `start()` to even get frames dispatched to `_phrx_ip4`
  at all). With that double workaround in place, the empty `self.ip4_unicast` does make the RX-side
  check above vacuously pass (`if self.ip4_unicast and ...` short-circuits on the empty list) - the
  SYN reaches the TCP layer, matches a wildcard (`0.0.0.0:80`) listening socket via
  `tcp_listening_socket_patterns` (which does support a local-address wildcard, independent of the
  IP-layer check), creates a new socket keyed to the real remote peer
  (`93.184.216.243/80/10.0.0.2/54321`), and transitions `LISTEN -> SYN_RCVD`. But the attempt to
  transmit the resulting SYN+ACK is then dropped by `pytcp/protocols/ip4/phtx.py`'s `_phtx_ip4()`,
  which has its own, separate, **unconditional** (no empty-list short-circuit) source-ownership
  check - `if ip4_src not in {*self.ip4_unicast, *self.ip4_multicast, *self.ip4_broadcast,
  Ip4Address(0)}: drop` - logging `Unable to sent out IPv4 packet, stack doesn't own IPv4 address
  93.184.216.243, dropping`. The connection retries via PyTCP's own retransmit timer and never
  completes the handshake. No config flag or documented hook disables this check; it is fundamental
  to PyTCP's identity as a host stack (RFC-compliant hosts do not originate packets claiming a
  source address they don't own) rather than a bug or oversight.
- **Conclusion: the embedding model this record designed - PyTCP terminating the guest's TCP
  connection to an arbitrary internet destination in-process, unmodified - does not fit.** Both
  gates (RX destination-ownership, TX source-ownership) would need bypassing via unofficial
  monkeypatching of `PacketHandler`/`_phtx_ip4` internals, not configuration, to make PyTCP
  originate traffic under an address it doesn't own - which undermines the record's own core
  rationale for choosing PyTCP over a hand-rolled reflector in the first place (reusing an
  already-correct, unmodified, tested implementation rather than new/risky protocol logic). Per this
  section's own next step: this should be read as "the current embedding model doesn't fit" and the
  session picking this up next should evaluate the `lwIP`/`gVisor` fallback candidates from
  "Alternatives considered" instead of continuing to force-fit `PyTCP` into a router-grade role its
  own README already disclaimed.

**Packaging findings (2026-08-14), incidental to the above but worth keeping:**

- `pip install pytcp` on this project's `requires-python = ">=3.10"` resolves to `2.7.6` (the
  version tested above) since the newest `2.7.x`, `pytcp==2.7.10`, requires Python `>=3.12`, and
  `pytcp==3.0.9` (a much larger, restructured release) requires Python `>=3.14`.
  `pytcp==2.7.10`'s published wheel on PyPI is itself broken independent of this project's own
  Python floor - it ships only `pytcp/__init__.py` and `pytcp/config.py`; the `pytcp.lib`,
  `pytcp.protocols`, and `pytcp.subsystems` subpackages `__init__.py` imports from are entirely
  absent from the distributed wheel, so `import pytcp` itself raises `ModuleNotFoundError: No
  module named 'pytcp.lib'` - not evaluated further since `2.7.6` (this project's actual resolved
  version) is unaffected and complete.
- `pytcp` briefly landed via a plain `uv add pytcp` as a hard entry in `[project.dependencies]`
  (not the optional-dependency group this record's "Decision" section calls for) while the PoC
  above was running - reverted afterward (`pyproject.toml`/`uv.lock` back to their pre-session
  state) once the embedding model was shown not to fit; not left in place.
- The installed `2.7.6`'s public `TcpIpStack` class (`pytcp/__init__.py`) has **no programmatic
  frame-injection constructor at all** - `__init__` unconditionally `os.open("/dev/net/tun")`s and
  issues a `TUNSETIFF` ioctl to attach a real kernel TAP interface (needs `CAP_NET_ADMIN`), contrary
  to this record's "Not designed here" section's framing of `add_interface()` as the relevant
  surface to figure out. The PoC above bypassed `TcpIpStack` entirely and drove `pytcp.lib.stack`'s
  internal `packet_handler`/`rx_ring`/`tx_ring`/`arp_cache`/`nd_cache`/`timer` singletons directly,
  same sequence as `TcpIpStack.start()`, but calling `rx_ring.start(fd)`/`tx_ring.start(fd)` with an
  `AF_UNIX`/`SOCK_DGRAM` `socketpair()` fd instead of a TAP fd (those two methods accept any
  select()-able fd, not specifically a TAP device). This works but reaches around the public API
  into implementation details with no compatibility guarantee across `PyTCP` releases.

## `lwIP`/`gVisor` fallback evaluation (2026-08-14)

Per the resolved finding above, comparing the two fallback candidates this record's own "Alternatives
considered" section already named, specifically against the exact RX-destination-ownership /
TX-source-ownership gap that sank `PyTCP` - not a general survey, a check of whether each one
actually closes that specific gap.

**`lwIP`:** the vendored copy this project's guest-firmware submodule points to
(`/home/murphy/pyproj/micropython/lib/lwip`, uninitialized before this session - initialized
read-only to inspect it, upstream `lwip-tcpip/lwip` @ `77dcd25a`, v2.2.x, BSD-style license) is a
separate concern from any host-side use (it's the *guest's own* stack, used by the emulated
MicroPython target itself) but was inspected directly here as the closest real copy of the code a
host-side binding would vendor too:

- Real IP-layer forwarding exists (`IP_FORWARD` config option, `opt.h:756-761`, default off; real
  `ip4_forward()` in `src/core/ipv4/ip4.c`) - genuine multi-netif routing between a guest-facing and
  a host-facing `netif`, architecturally distinct from `PyTCP`'s single-stack-identity design.
- But plain upstream `lwIP` has **no NAPT** (address/port translation) - grepped the whole vendored
  tree for `IP_NAPT`/`napt`, found nothing. Bare `IP_FORWARD` routes packets between interfaces
  unchanged; it does not rewrite the guest's private source address into anything a real internet
  host could route a reply back to, which is the actual thing step 4 needs. NAT specifically exists
  only in downstream forks (most notably Espressif's `esp-lwip`, patched for ESP32 Wi-Fi AP-to-STA
  NAT) - not in this project's vendored copy or upstream `lwip-tcpip/lwip`.
- Two integration shapes, neither designed further here: (a) port/reimplement NAPT on top of
  `IP_FORWARD` - a scoped, well-precedented problem (rewrite src IP/port + recompute checksums
  outbound, reverse it inbound, tracked in a translation table) unlike inventing a new TCP state
  machine; or (b) skip forwarding and use `lwIP`'s own raw/netconn socket API the way `PyTCP`'s was
  going to be used - but that risks hitting the identical RX/TX ownership-check tension host stacks
  share by design, **not verified against `lwIP`'s own source this session** - flagged, not assumed.
- Either shape still needs a real Cython/C binding (this project's `native/` dual-mode pattern) across
  the whole `cibuildwheel` matrix - lighter than `libslirp`'s `glib` dependency (`lwIP` needs only
  libc) but real build-system work, the same class of cost that sank the original `libslirp`
  proposal, just smaller.

**`gVisor` `pkg/tcpip`:** verified via web research this session (not source-read, unlike everything
else in this record - flagged as a lower-confidence source than the direct `pytcp`/`lwIP` source
inspection above) that `tcpip.Stack` ships first-class, public, documented API for **exactly** the
two gaps that sank `PyTCP`:

- `Stack.SetPromiscuousMode(nicID, true)` - accept inbound traffic not addressed to the NIC's own
  configured address (closes `PyTCP`'s RX-side gap).
- `Stack.SetSpoofing(nicID, true)` - documented as "allowing endpoints to bind to any address in the
  NIC" (closes `PyTCP`'s TX-side gap - the one with *no* bypass at all in `PyTCP`).
- This is not hypothetical: Tailscale's own userspace-networking mode (used for subnet
  routers/exit nodes on non-Linux platforms, or whenever running unprivileged) is built on exactly
  this - per Tailscale's own docs, it "terminates TCP and UDP connections from the origin ... peer
  and makes new outbound connections to the target ..., stitching them together" - the identical
  splice architecture this record designed around `PyTCP`, running in production today, at scale,
  against arbitrary real internet destinations.
- License: Apache-2.0 - MIT-compatible, none of `PyTCP`'s "optional dependency to dodge GPL"
  complication.
- Real cost: Go, not Python - this project currently has no Go toolchain/build-system presence at
  all. Two integration shapes, neither designed in detail here: (a) `go build -buildmode=c-shared` +
  a `ctypes`/Cython binding - in-process, but reintroduces hand-declared struct-layout risk across
  the 9-target `cibuildwheel` matrix, needs `cgo` (a C compiler - already true for `native/`) per
  target, needs Go's Android cross-compilation (`GOOS=android`) verified per-target, not done here;
  or (b) a small Go subprocess speaking a length-prefixed raw-Ethernet-frame protocol over a
  UNIX/named-pipe socket - structurally identical to the `passt` integration this record already
  evaluated and rejected, except a from-scratch Go binary isn't tied to `passt`'s Linux-only
  `epoll`/`io_uring`/`seccomp` internals, sidestepping `passt`'s specific rejection reason while
  keeping its main advantage (plain non-blocking socket I/O fits the existing engine-room `asyncio`
  loop directly, no in-process struct-layout risk).

**Recommendation, not a decision** (this evaluation doesn't re-supersede the "Decision" section
above - that needs its own explicit go-ahead per this repo's document-vs-implement convention):
`gVisor` is the substantially closer technical fit - built for, and proven in production at, exactly
this splice architecture, closing both gaps `PyTCP` couldn't via first-class public API rather than
internals-reaching workarounds. `lwIP`'s forwarding mode is real but incomplete for NAT without
porting NAPT from a downstream fork, and its alternative socket-API route carries an unverified
version of `PyTCP`'s exact problem. The build-system cost is real either way but not obviously
smaller for `lwIP` - it trades `libslirp`'s `glib` dependency for a same-shape Cython/C binding
effort, while `gVisor` trades that same effort for a Go-toolchain one instead. Left for the session
that picks this up to actually choose between them (or a `passt`-shaped Go subprocess vs. an
in-process `cgo` binding, within the `gVisor` option itself) and write a superseding "Decision".

## `gVisor` empirical verification (2026-08-14), and a Rust survey

Per explicit go-ahead this same session ("try doing gVisor - Go is available"), the `gVisor`
recommendation above was tested the same way `PyTCP` was - a real, compiled program, not just
documentation reading - before any decision was made to commit to it. A parallel, lighter survey of
Rust candidates was also run at the same time, per a separate explicit request this session.

**`gVisor` PoC (Go, throwaway scratchpad program, no tracked files touched by the program itself):**
mirrors the `PyTCP` PoC's structure exactly - `stack.New()` with `ipv4`+`tcp` protocols, a
`channel.Endpoint` NIC (gVisor's own in-memory test link endpoint, no real TAP/root needed, same
role as the `PyTCP` PoC's `socketpair()`), `SetPromiscuousMode`/`SetSpoofing` both enabled, a
`tcp.NewForwarder` registered as the transport handler, **zero addresses ever assigned to the
stack** (stricter than `PyTCP`'s baseline test - this stack never owned any address at all), then
injecting a hand-built SYN via `gVisor`'s own `header.IPv4`/`header.TCP` encoders (source
`10.0.0.2:54321`, destination `93.184.216.243:80`, a foreign address). Result:

```
[inject] sent SYN frame, dst=93.184.216.243:80 (not the stack's own address)
[forwarder] connection request local=93.184.216.243:80 remote=10.0.0.2:54321
[RESULT] TRANSMITTED outbound packet src=93.184.216.243 dst=10.0.0.2 (stack does NOT own src)
[RESULT] TCP flags= S  A    sport=80 dport=54321
```

The forwarder fired for the foreign destination (closes `PyTCP`'s RX gap), and the stack actually
**transmitted a real SYN+ACK packet claiming `93.184.216.243` as its source address** - the exact
operation `PyTCP`'s unconditional TX-side ownership check refused outright, with no bypass. Both
gaps that sank `PyTCP` are confirmed closed empirically, not just per documentation. (The PoC's own
`accepted`-channel readback raced against `CreateEndpoint()` blocking on full handshake completion -
a harness synchronization detail, not a `gVisor` finding; the transmitted-packet capture is the
authoritative result and needed no such synchronization.)

**Packaging finding, `gVisor` side (2026-08-14):** the canonical `gvisor.dev/gvisor` module is
**not reliably consumable via plain `go build`/`go get`** - confirmed both by hitting it directly
(`go build` on `gvisor.dev/gvisor@latest` and on the pinned release tag `release-20260810.0` both
fail identically: `found packages stack (addressable_endpoint_state.go) and bridge
(bridge_test.go) in .../pkg/tcpip/stack`) and by finding this is a known, recurring, long-standing
upstream issue (`google/gvisor` issues #11600, #5636 - present since at least a 2025-03 report,
still reproducing on this session's date) rooted in `gVisor` being a Bazel-first project whose
`go.mod`/source tree isn't first-class-maintained for standard Go tooling. The working fix used
here: `github.com/sagernet/gvisor`'s `go` branch - an actively-maintained fork whose own README
states it exists specifically "to be compatible with standard `go` tooling for convenience," and
which is what `sing-box` (a real, cross-platform, production TUN-based proxy tool) actually depends
on for this exact use case. Two catches worth keeping: (1) it doesn't work as a `replace
gvisor.dev/gvisor => github.com/sagernet/gvisor ...` directive, because the fork's own `go.mod`
declares its module path as `github.com/sagernet/gvisor`, not `gvisor.dev/gvisor` - source imports
need to reference the fork's path directly; (2) its published pseudo-versions
(`v0.0.0-...-sing-box-mod.1.0...`) aren't indexed on the public Go checksum database
(`sum.golang.org` 404s), needing `GOSUMDB=off` for that module to resolve at all - both fixable, but
neither obvious, and worth a real design note (vendoring vs. a documented fork+flags pin) whenever
this becomes an actual build-system integration rather than a scratchpad PoC.

**Rust survey, per explicit separate request this session** - lighter-weight than the Go work above
(web research only, no PoC run), covering the four candidates named: `smoltcp`, `ipstack`
(`narrowlink`), `tcp_ip` (`rustp2p`), `fake-tcp`:

- **`smoltcp`** - the Rust analogue of `lwIP`/`PyTCP` in maturity (widely used, embedded + userspace,
  MIT/Apache-2.0). Has a documented `any_ip` interface mode, but per its own docs that's scoped to
  "a route prefix ... specifies one of the interface's `ip_addrs` as its gateway" - the same
  locally-routed-prefix shape as the proxy-ARP mitigation already found not to apply to `PyTCP`
  (real internet destinations aren't part of any locally-routed prefix either way). More promising:
  its address list is runtime-mutable (`Interface::update_ip_addrs()`), suggesting a per-flow
  "temporarily own the real destination address, drop it when the flow closes" technique - structurally
  different from `gVisor`'s stack-wide promiscuous/spoofing toggle and from `PyTCP`'s fixed,
  unconditional ownership checks. **Not verified this session** - flagged, not assumed, matching
  this record's own standard for every other claim in it.
- **`ipstack`** (`narrowlink/ipstack`) - purpose-built for exactly this use case: `accept()` yields
  TCP/UDP streams straight off a TUN device for arbitrary destinations, no promiscuous-mode dance
  needed since that's the crate's whole reason for existing (its own docs example forwards an
  accepted connection to `1.1.1.1:80` directly). Best architectural fit of any candidate surveyed,
  including `gVisor` - but far less proven than any other option this record has looked at (85
  GitHub stars, small single-maintainer project, no visible evidence of `PyTCP`-grade RFC-audit or
  test coverage, no confirmation its TCP implementation has real congestion control vs. a simpler
  approximation). Apache-2.0, requires `tokio`.
- **`tcp_ip`** (`rustp2p/tcp_ip`) - general-purpose userspace TCP/IP stack for peer-to-peer use
  cases, not specifically NAT/proxy-shaped - not evaluated further.
- **`fake-tcp`** - this is, by its own README's description ("TUN interface based ... TCP stack
  that allows packet oriented tunneling with minimum overhead," built for Datong Sun's `phantun`
  UDP-over-fake-TCP firewall-bypass tool) the same "hand-rolled reflector, no independent congestion
  control" category this record already evaluated and rejected once (see "Alternatives considered"
  above) - not re-evaluated in depth for the same reason it was rejected there.

**Where this leaves the decision:** `gVisor` is now the only fallback candidate in this record with
an actual empirical confirmation (not just documentation or a maturity argument) that it closes both
gaps that sank `PyTCP`, using a real compiled program producing real packet bytes.

## Latest decision: `gVisor` via `cgo`, superseding the `PyTCP` decision above (2026-08-14)

Per explicit user direction this session ("cgo is probably the better option since it'll work
everywhere except iOS"): the integration shape is a `cgo` shared library (`go build
-buildmode=c-shared`), not a Go subprocess. This **returns the project to the `native/` dual-mode
pattern** (record 0013) this record's title originally referenced and then moved away from for
`PyTCP`'s sake - `nat.py`'s `gVisor` binding becomes a compiled extension, gated the same way
`native/` already is (build/Cython/compiler-availability optional, skip gracefully when absent),
not a plain-Python `try: import` gate the way `PyTCP` would have been.

- **iOS caveat checked against this project's actual target matrix and found moot:**
  `pyproject.toml`'s `[tool.cibuildwheel]` only ever targets Linux (x64/x86/ARM64/ARMv7), Windows
  (x64/x86/ARM64), macOS (Apple Silicon/Intel), and Android (`cp313-android_*`/`cp314-android_*`) -
  confirmed directly against the current `pyproject.toml`, no iOS wheel is built at all. The
  reasoning behind picking `cgo` holds without even needing the iOS exception - there is no
  platform in this project's own matrix `cgo` doesn't cover.
- **Still open, not decided here:** the Python-side binding mechanism (`ctypes` calling the
  `cgo`-produced `.so`/`.dll`/`.dylib` directly, vs. a thin Cython `.pyx` wrapper around it) - the
  "Alternatives considered" section's own rejection of a `ctypes` binding to `libslirp` was about
  *hand-declaring struct layouts* for a complex C API; a `cgo`-exported API can be designed with a
  deliberately narrow, `ctypes`-friendly C ABI (a handful of `extern "C"` functions passing only
  primitives/byte buffers, no complex structs) specifically to sidestep that risk - not designed in
  detail here.
- **Still open, not decided here:** which of the two packaging catches found above becomes the
  real answer - vendoring the `SagerNet/gvisor` fork's source directly into this repo (avoids the
  `GOSUMDB=off`/module-path fragility of depending on its Go-module publishing at all) vs. pinning
  it as an ordinary Go module dependency with those two flags/fixes documented in the build.
  Vendoring fits this project's existing pattern more closely (e.g. the local `pico-sdk`/
  `cyw43-driver` checkouts `bus.py`/`chip.py` already source protocol details from), but adds a
  second git-managed copy of a large upstream tree to keep in sync.
- **Still open, not decided here:** a Go toolchain needs adding to this project's CI/build matrix
  (currently zero Go presence) - which CI images/`cibuildwheel` container setup step installs it,
  how per-target cross-compilation (`GOOS`/`GOARCH`, plus `CGO_ENABLED=1` and a matching C
  cross-compiler per target - already partially true for `native/`'s own existing Cython builds) is
  wired into the existing `[tool.cibuildwheel]` matrix, and how `cp313-android_*`/`cp314-android_*`
  specifically get a working `cgo` Android cross-compile (needs the Android NDK's C toolchain,
  `CC`/`CXX` env vars per ABI) - none of this attempted yet.
- **Still open, not decided here:** the same sub-step breakdown (`4a`/`4b`/...) and
  `bus.py`-integration/`schedule_threadsafe()` questions the original `PyTCP`-based "Not designed
  here" section already deferred - none of that is resolved just because the underlying engine
  changed from `PyTCP` to `gVisor`.

This is a real decision (which engine, which integration shape) but still stops short of being a
finished implementation plan - the items above are genuine unknowns, not administrative detail, and
this record continues to hold off on writing actual `nat.py`/build-system code until those are
worked through, per this repo's document-vs-implement convention.

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

## Superseded (2026-08-16): engine choice only

[0048](0048-cyw43-nat-reflector.md) supersedes this record's `gVisor`-via-`cgo` engine choice with a
custom, hand-rolled reflector - the "Alternatives considered" section's own hand-rolled-reflector
option, previously set aside above for "new, unaudited protocol logic," picked back up once both
`PyTCP` (architecturally) and `gVisor` (toolchain cost) turned out not to fit the goal of a genuinely
minimal step 4. This record is kept verbatim below this note, per this repo's append-only
convention - its research (the `PyTCP` negative result, the `gVisor` empirical PoC, the SDPCM
`DATA_HEADER` envelope derivation 0048 itself reuses directly) all stays valid and citable.
