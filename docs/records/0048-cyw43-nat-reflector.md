# 0048. CYW43 step 4 NAT bridge: a custom, minimal hand-rolled TCP reflector

- Status: Implemented — verified (2026-08-16)
- Conceived: 2026-08-16
- Related: 0027 (epic, step 4), 0045 (this record supersedes 0045's *engine choice* only - 0045
  itself is kept verbatim, not rewritten, per this repo's append-only convention), 0028 (module
  layout - `external/cyw43/nat.py`, named there as a future sibling of `bus.py`/`chip.py` before
  this record existed), 0030 (`ExternalDevice` concurrency model - `schedule_threadsafe()`)

## Decision

Not a real independent TCP/IP stack. [0045](0045-cyw43-nat-libslirp-cython.md) explored and set
aside `libslirp` (no Windows packaging story), `PyTCP` (empirically proven, that same session, not
to fit - its own RX-destination-ownership and TX-source-ownership checks unconditionally refuse to
originate traffic for an address it doesn't own, no config bypass), and `gVisor` via `cgo` (works,
empirically proven against a real compiled PoC, but needs a brand-new Go toolchain cross-compiled
across all 9 `cibuildwheel` targets including an Android NDK cross-compile, plus vendoring/pinning
questions never resolved). This record instead builds the lightest option discussed in that same
record's own "Alternatives considered" section - a hand-rolled reflector - previously set aside
there for "new, unaudited protocol logic," picked back up now that both "reuse an audited stack"
options turned out not to cleanly fit (PyTCP architecturally, `gVisor` on toolchain cost) and the
explicit goal shifted to minimalism.

**The architectural insight that makes this small:** the guest-facing leg of this bridge is *not* a
real, lossy network. `bus.py`'s `queue_rx_packet()` is already an in-process, lossless, in-order
FIFO - a real background OS thread never drops or reorders a frame between "the guest sent it" and
"the guest's own driver reads it back." That means the guest-facing leg needs none of what makes a
real TCP/IP stack big: no retransmission timers, no congestion control (CUBIC/NewReno/etc.), no RTT
estimation, no out-of-order reassembly, no SACK. It needs only:

- Handshake spoofing - reply to the guest's SYN with our own SYN-ACK, pretending to be the real
  remote destination (freely, since this is bespoke code with no RFC-host-identity check baked in -
  unlike `PyTCP`, which is *correctly*, by design, unwilling to do this).
- seq/ack bookkeeping - translate between the guest-visible TCP sequence space and a real host-side
  byte stream.
- Respecting the guest's advertised receive window (flow control, not congestion control).
- FIN/RST propagation between the two legs.
- A per-flow connection table keyed by the guest's `(src_port, dst_ip, dst_port)` (single guest, so
  no guest IP needed in the key).
- MSS-aware segmentation of host→guest data.

The host-facing leg is a real `asyncio` socket connection to the actual destination - the OS's own
TCP stack does all the correctness-critical, loss-prone work there (retransmission, congestion
control against the real, lossy internet). Architecturally this is the same "reflecting one peer's
observed parameters ... to the corresponding peer" design `passt`'s own man page documents (already
cited in 0045).

## Three prerequisites, confirmed this session against the real vendored `cyw43-driver` source

Confirmed directly (not from documentation) against
`/home/murphy/pyproj/micropython/lib/cyw43-driver` (a separate local checkout the rest of this
project's CYW43 protocol work already sources from) plus
`/home/murphy/pyproj/micropython/lib/lwip` and MicroPython's own `extmod/lwip-include/
lwipopts_common.h`: the reflector (step "4d" below) cannot be exercised at all - the guest never
sends a TCP SYN to intercept - without three things landing first:

- **MAC address (4a).** `cyw43_wifi_init()` (`cyw43_ctrl.c:190-193`, `CYW43_USE_OTP_MAC` set on the
  rp2 port) calls `cyw43_ll_wifi_get_mac()` once during bring-up - a real `WLC_GET_VAR
  cur_etheraddr` ioctl whose response this bus currently answers with the existing generic
  all-zero-fill (`_build_ioctl_success_response()`). `cyw43_ll_wifi_get_mac()` (`cyw43_ll.c:
  1916-1927`) does `memcpy(addr, buf, 6)` - only the response's first 6 bytes matter. That cached
  MAC feeds both `netif->hwaddr` (`cyw43_lwip.c:147`) and Python `config('mac')`
  (`network_cyw43.c:445`) - one correct ioctl answer fixes both.
- **DHCP lease (4b).** Once link is up, `dhcp_network_changed_link_up()` → `dhcp_discover()`
  (`dhcp.c:1039-1062`) sends a broadcast DHCPDISCOVER (UDP, dst port 67, dst MAC
  `ff:ff:ff:ff:ff:ff`) - the very first outbound content-bearing `DATA_HEADER` frame a real boot
  produces. No ARP precedes it: `etharp_output()` (`etharp.c:807-811`) short-circuits ARP entirely
  for broadcast/multicast destinations, and `LWIP_DHCP_DOES_ACD_CHECK` is explicitly `0` in
  MicroPython's own lwIP config (`lwipopts_common.h:57`, "to speed DHCP up") - no ARP-probe of the
  offered address either. Needs a minimal DHCP server answering DISCOVER→OFFER, REQUEST→ACK with
  one fixed lease (single-guest scenario, no real address pool needed).
- **Gateway ARP (4c).** Only once the guest has a lease does `etharp_output()`
  (`etharp.c:836-854`) ARP the *gateway* IP for any off-subnet destination (confirmed: lwIP always
  substitutes `netif_ip4_gw(netif)` as the ARP target for off-subnet traffic, never the real remote
  IP) - this is the request that must resolve before the guest can even frame a TCP SYN at the
  link layer. Needs a trivial responder: ARP request for the gateway IP → ARP reply with a fixed
  fake gateway MAC.

Confirmed order an emulated responder needs to answer: MAC (any time before/during bring-up) →
DHCPDISCOVER → DHCPOFFER → DHCPREQUEST → DHCPACK → (guest attempts an off-subnet connect) → ARP
request for the gateway → ARP reply → (now) TCP SYN.

## Sub-step breakdown (mirrors 0027's own 3a-3g convention)

- **4a - MAC fix.** Fully independent of everything else below - wire-protocol-level only, no NAT
  bridge object involved at all. Unblocks `config('mac')` on live boot immediately.
- **4b - DHCP server.** Needs the new `net.py` pack/parse helpers + a `DhcpServer` + minimal NAT
  wiring. Live-boot-testable alone - the guest broadcasts DHCPDISCOVER unconditionally once linked,
  independent of 4c/4d existing yet.
- **4c - ARP responder.** Depends on 4b (the guest only ARPs the gateway once it already has a
  lease and is about to send an off-subnet packet).
- **4d - the TCP reflector itself.** Hard-depends on 4a+4b+4c - none of them optional, all three
  needed before a live boot ever produces a TCP SYN to intercept.
- **4e - DNS relay.** Added same day, after confirming empirically (`mip.install()` against real
  firmware, `OSError -2`) that hostname-based connections fail without it - `getaddrinfo()` never
  even attempts a TCP connection if the name doesn't resolve first. Architecturally much smaller
  than 4d: UDP has no connection/sequence state to spoof at all, so this is a one-shot relay (query
  bytes forwarded verbatim to a fixed public resolver, response bytes forwarded back verbatim,
  re-addressed to look like it came from the gateway) - no DNS message parsing, no connection
  table, one short-lived real socket per query. Depends on 4b/4c (DHCP hands out the gateway IP as
  the DNS server via option 6; ARP resolves the gateway's MAC) but not on 4d.

## Progress log

- 2026-08-16: Record created, design decided (see "Decision" above), implementation starting this
  session.
- 2026-08-16: 4a/4b/4c/4d all implemented this session - `external/cyw43/net.py` (pure wire-format
  pack/parse + checksum helpers), `external/cyw43/nat.py` (`ArpResponder`/`DhcpServer`/
  `TcpReflector`/`NatBridge`), `bus.py`/`chip.py` wiring (`nat_bridge` attribute, `DATA_HEADER`
  dispatch, `_build_data_frame()`/`queue_rx_ethernet_frame()`). Unit + integration tests added
  (`tests/test_cyw43_net.py`, `tests/test_cyw43_nat.py`) including a full TCP handshake/data/
  FIN round trip and a refused-connection RST test against a real hermetic `asyncio` echo server
  on `127.0.0.1` - `uv run pre-commit run --all-files` clean (mypy/ruff/pytest on both the
  pure-Python and native-Cython builds). One real bug caught and fixed during this pass: guest FIN
  wasn't half-closing the real destination socket (`writer.write_eof()`) - without it, a real
  remote server waiting on EOF as an end-of-input signal would hang forever even though the guest
  had already finished sending. **Not yet done: a real live-boot verification** (real MicroPython
  firmware actually reaching `isconnected() == True` and completing a real socket round trip) -
  everything above is unit/integration-level only so far.
- 2026-08-16: **Live-boot verified, both tracked firmware versions.** `tests/micropython/
  main-cyw43.py` extended: polls `isconnected()` after `connect()` (DHCP is a same-process
  synthetic exchange, so a short poll window is enough), then - once connected - opens a real
  `socket.connect()` to a fixed public IP (`1.1.1.1:80`, chosen specifically because it's
  non-loopback and reachable from the *host* machine; `127.0.0.1` would never reach this bus at
  all, since the guest's own lwIP resolves its own loopback locally). Run against real,
  unmodified `v1.23.0` and `v1.28.0` UF2s (`uv run rp2040py --log-level error micropython --image
  <tag> --board pico_w tests/micropython/main-cyw43.py`): `config('mac')` reads the real
  `_GUEST_MAC`, `ipconfig('addr4')` reads `('10.0.0.2', '255.255.255.0')` (the fixed DHCP lease),
  `isconnected()` becomes `True`, and the real TCP connection to `1.1.1.1:80` round-trips a real
  HTTP response (`HTTP/1.1 301 Moved Permanently`, Cloudflare's own redirect) through the
  reflector - byte-for-byte proof the whole 4a-4d chain works against real firmware, not just
  this session's own hermetic unit/integration tests. This script is wired into CI
  (`ci-micropython.yml`'s `pico_w` matrix step, soft-failing via `|| echo ::warning`, no
  `--expect-text` check) - touching the real internet from CI was an explicit, deliberate choice
  this session (the alternative, a local-only verification script, was considered and rejected in
  favor of also exercising 4d, not just 4a-4c).
- 2026-08-16: **4e (DNS relay) implemented and live-boot verified, same session.** Confirmed the
  gap empirically first: `mip.install("os-path")` against real `v1.28.0` firmware failed fast with
  `OSError -2` (name resolution failure, not a hang - lwIP's own DNS resolver gives up on its own)
  - no TCP connection was ever attempted, exactly as the "Deferred" section below predicted.
  `DnsRelay` added to `nat.py` (`_OneShotDatagramProtocol` + `asyncio.create_datagram_endpoint()`
  against a fixed public resolver, `1.1.1.1:53`, matching the `1.1.1.1:80` precedent already used
  for 4d's own live-boot test), wired into `NatBridge`'s UDP dispatch behind `DhcpServer` (port 53
  vs. port 67) - no `bus.py` changes needed, since it reuses the exact same `queue_ethernet_frame`
  path 4b/4d already established. Unit + integration tests added (hermetic fake resolver on
  `127.0.0.1`, a no-response/timeout case, and an unrelated-UDP-port no-op case) - all passing,
  `pre-commit run --all-files` clean. `tests/micropython/main-cyw43.py` extended to call
  `mip.install("os-path")` after the existing TCP probe; live-booted against both `v1.23.0` and
  `v1.28.0` - `mip` now resolves `micropython.org`, downloads, and installs successfully.

## Deferred, not designed here

No config surface (`Cyw43439(guest_ip=..., ...)` or similar) was added this pass - guest/gateway IP
and MAC are hardcoded module constants in `nat.py`, matching the existing fixed-fake-AP precedent
(`_FAKE_AP_SSID`/etc. in `bus.py`). Easy to add later if a second board profile or test ever needs
a different address.

No backpressure from the real destination socket's own write-buffer high-water mark onto this
bridge's advertised guest-facing receive window - an accepted v1 simplification, not an oversight;
straightforward to add later (shrink the advertised window by
`transport.get_write_buffer_size()`) if a slow real destination + fast guest sender ever proves it
matters in practice.
