# 0048. CYW43 step 4 NAT bridge: a custom, minimal hand-rolled TCP reflector

- Status: Implemented — verified (2026-08-16), merged in [PR #37](https://github.com/o-murphy/rp2040py/pull/37).
  **Still open in the tracker** (`[ ]` under "In progress / Proposed") until the "Known gaps"
  section at the bottom of this record is closed — the happy path works end to end, this is not yet
  a complete WiFi emulation.
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
- 2026-08-16: A first push of this session's work to `feat/cyw43-tcp-reflector` (the 4a-4d commit
  only, before 4e existed) hit a real CI flake on `windows-latest`:
  `test_tcp_reflector_sends_rst_on_a_refused_connection`'s fixed `asyncio.sleep(0.2)` wasn't long
  enough there - a closed-port loopback connect attempt takes measurably longer to fail on Windows
  than on Linux, so `_read_f2_response()` returned an empty frame (`IndexError` unwrapping it).
  Fixed by replacing every fixed post-write `asyncio.sleep()` in `tests/test_cyw43_nat.py` (6 call
  sites, not just the one that happened to flake first) with a `_wait_for_pending_packet()` helper
  that polls `SPI_STATUS_REGISTER`'s own pending bit instead of guessing a duration. Verified
  locally (`pre-commit run --all-files` clean); not yet re-pushed to confirm on CI.
- 2026-08-16: Picked up 3 of the 4 easy/high-value items from the "Known gaps" section below (user
  chose everything except the CircuitPython live-boot check): the real-RST-vs-FIN bug, the
  connect-timeout/flow-leak gap, and generalizing `DnsRelay` into `UdpRelay` (see that section's
  own now-annotated bullets for the technical detail on each). `tests/micropython/main-cyw43.py`
  further extended with an `ntptime.settime()` call (needs a real UDP round trip *not* addressed to
  the gateway's DNS port - exactly what the `UdpRelay` generalization was for) - live-booted
  against both `v1.23.0` and `v1.28.0`: RTC gets set to the real current time via a real NTP
  round trip through the emulator. 4 new/changed hermetic tests added (real-RST-via-`SO_LINGER`,
  connect-timeout-via-injectable-`connect_fn`, general-UDP-relay-to-a-real-destination, DHCP-
  still-wins-over-the-generalized-relay) - full suite (17 tests in `test_cyw43_nat.py`) stable
  across repeated local runs, `pre-commit run --all-files` clean. ~~Still not pushed.~~
  (superseded - pushed and merged, see the final entry below.)
- 2026-08-16: A second push (PR #37) hit a second real CI flake on `windows-latest`, this time in
  the RST-propagation test just added above: `test_tcp_reflector_propagates_a_real_reset_as_rst_
  not_a_clean_fin` observed `flags=17` (`TCP_ACK|TCP_FIN`) instead of `TCP_RST` - the `SO_LINGER`
  "force a real kernel RST" trick this test used (mentioned in the entry directly above, since
  corrected) is documented to request a hard/abortive close on Windows too, but didn't reliably
  surface as `ConnectionResetError` through Python's asyncio there in practice. Read as: the
  underlying `nat.py` fix itself was never in question (the reflector correctly forwarded whatever
  it actually observed from the real socket - a clean FIN, because that's what Windows' asyncio
  layer actually delivered) - this was purely a test-portability bug, chasing OS-specific
  socket-close semantics instead of testing the exception-handling code path directly. Rewrote the
  test to inject a fake `connect_fn`/reader (`_ImmediatelyResetReader`, reusing the same
  `connect_fn` injection seam the connect-timeout test already established) that raises
  `ConnectionResetError` directly - no real socket, no OS dependency, faster (no real I/O) and
  strictly more precisely targeted at the code this test exists to cover. Verified stable across 5
  repeated local runs; `pre-commit run --all-files` clean. ~~Still not pushed.~~ (superseded -
  pushed and merged, see the entry directly below.)
- 2026-08-16: **Pushed and merged.** [PR #37](https://github.com/o-murphy/rp2040py/pull/37)
  (`feat/cyw43-tcp-reflector` → `main`, 7 commits, head `56bc2ec`) merged as `9f5348f`, 2026-08-16
  00:19 UTC. This closes the "not yet re-pushed to confirm on CI" caveat both entries above left
  open: **all 62 checks green on the merged head**, including all three `pre-commit run
  --all-files` OS jobs (`ubuntu-latest`, `macos-latest`, and - the one that mattered here -
  `windows-latest`), the full MicroPython `1.16`-through-`1.29.0-preview` × cpython-3.10/3.14/
  pypy-3.10 matrix, the Pico SDK `1.2.0`-`2.3.0` matrix, Kaluma, and both codecov gates. Both
  `windows-latest` flakes documented in the two entries above (the fixed-`asyncio.sleep()` race and
  the `SO_LINGER` real-kernel-RST portability bug) are therefore confirmed fixed on real CI, not
  just locally. Nothing in this record's "Known gaps" section changed as part of the merge - that
  inventory is still the accurate open-work list, and is now also surfaced in the tracker's own
  "In progress / Proposed" section rather than living only inside this record.

- 2026-08-16: **TLS/HTTPS and WebSocket verified live on `v1.28.0`** - closing the first half of
  the "Unverified, not necessarily broken" gap below. Run against a real, unmodified `v1.28.0`
  RPI_PICO_W UF2 supplied for this check, with `--board pico_w`:
  - **Real TLS to a real public host.** `socket.getaddrinfo('micropython.org', 443)` (through the
    UDP DNS relay) → TCP connect through the reflector → `ssl.wrap_socket(..., server_hostname=...)`
    → handshake completes → `HEAD / HTTP/1.0` → `b'HTTP/1.1 426 Upgrade Required'` comes back
    decrypted. A real certificate chain, not a local fixture.
  - **WebSocket (RFC 6455) and WebSocket-over-TLS.** Against a purpose-built hand-rolled echo
    server (handshake + one echoed text frame) bound to the container's own **non-loopback** IP -
    loopback would never cross this bus at all, same reason 4d's own test targets `1.1.1.1`.
    Guest side: `HTTP/1.1 101 Switching Protocols`, then a masked client frame `hello`, then
    `echo:hello` back - identical result over plain TCP (port 8765) and over TLS (port 8766,
    self-signed). Server side independently logged `frame in: b'hello'` for both legs. TLS
    handshake cost ~640 *simulated* ms; the whole probe ran in ~1.5 simulated seconds.
  - **A false alarm worth recording, because it looks exactly like an emulator hang.** The first
    two attempts appeared to freeze for 15-30 real minutes with zero output. Neither was an
    emulator bug: (a) the CLI's script/exec mode drives the raw REPL, which buffers *all* guest
    stdout until the script ends, so a slow run is indistinguishable from a hung one by watching
    the log - `ps` showing 99.9% CPU and CPU-time tracking wall-time 1:1 is what actually
    distinguishes them; (b) the probe used `sock.read(160)` on the WebSocket upgrade response,
    and MicroPython's `read(n)` blocks until *exactly* n bytes arrive - the `101` response is
    shorter, so the guest sat waiting for bytes that would never come. `recv()` is the correct
    call; `tests/micropython/main-cyw43.py`'s new TLS step carries a comment saying so.
  - **`v1.23.0` parity, same day, same probes.** WS: `101` + `echo:hello`. Real TLS to
    `micropython.org:443`: handshake done, `b'HTTP/1.1 426 Upgrade Required'` back. WSS: the TLS
    handshake completed over the splice, then the *probe* failed with `AttributeError: 'SSLSocket'
    object has no attribute 'send'` - a real firmware API difference (v1.23.0's `SSLSocket` has
    `write`/`read` but not `send`/`recv`; v1.28.0 has both), not an emulator fault. Re-run with a
    `send`-or-`write` shim (and a 1-byte-at-a-time reader, since `read(n)` has the blocking
    behavior described above): `101 Switching Protocols` + `echo:hello`, so **WSS passes on
    v1.23.0 too**. Recorded because it is the second of two API-shape traps in this area:
    anything probing TLS across both tracked firmwares has to write to the older, narrower
    surface.
  - Landed: the TLS step is now part of `tests/micropython/main-cyw43.py` (so CI exercises it via
    the existing soft-failing `pico_w` job). The WebSocket check deliberately is **not** landed in
    CI - it needs a fixture server bound to the runner's own routable IP, injected into a script
    the guest reads statically, which is real machinery for a path that is payload-agnostic by
    construction and already covered by the plain-TCP and TLS steps. Verified by hand, recorded
    here, not automated.

- 2026-08-19: **CircuitPython 10.2.1 live-verified too**, which is what the 2026-08-16 entry below
  said was needed before the CYW43 job could move off 9.2.9 ("moving it to 10.x needs a live
  re-verification first, not just a matrix edit"). The exact CI command
  (`rp2040py micropython --circuitpython --image 10.2.1 --board pico_w
  tests/circuitpython/main-cyw43.py`) runs unchanged and clean: same `_GUEST_MAC`, the
  `RP2040PY-GUEST` scan result, `connected: True`, DHCP `10.0.0.2`/`10.0.0.1`, a real 151-byte
  HTTP response from `1.1.1.1:80` through the reflector, DNS for `micropython.org` →
  `176.58.119.26`, and the script's own `CIRCUITPYTHON CYW43 OK`. `ci-circuitpython.yml`'s
  `10.2.1` matrix entry is therefore now `wlan: true` - it had been the boot-banner-only
  regression test for [0050](0050-qspi-pad-reset-values.md). Found while building
  [0085](0085-circuitpython-code-py-and-wifi-on-screen.md)'s WiFi-on-a-panel demo, which drives
  the same path from guest `code.py` instead.

- 2026-08-16: **CircuitPython live-boot verified** - closing the other half of the "Unverified"
  gap. `tests/circuitpython/main-cyw43.py` added (the `wifi`/`socketpool` counterpart of the
  MicroPython script) and run against real CircuitPython **9.2.9** with `--board pico_w`:
  `wifi.radio.enabled` → `True`, `mac_address` → `00:10:18:00:00:02` (byte-for-byte `bus.py`'s own
  `_GUEST_MAC`, so 4a works here too), `start_scanning_networks()` → the fixed fake
  `RP2040PY-GUEST` AP, `connect()` → `connected == True`, DHCP → `ipv4_address 10.0.0.2` /
  `ipv4_gateway 10.0.0.1`, a real TCP socket to `1.1.1.1:80` → 151 bytes of real HTTP back, and
  `pool.getaddrinfo('micropython.org', 80)` → `176.58.119.26` through the UDP DNS relay. The
  earlier reasoning ("expected to work - both vendor the same `cyw43-driver`") holds: a completely
  different host network stack drives the identical emulated bus with no changes to `bus.py` or
  `nat.py`.
  - One test-only difference from the MicroPython script, worth knowing before writing any
    CircuitPython WiFi test here: CircuitPython validates the passphrase length client-side (WPA2's
    8-64 rule) and raises `ValueError` before anything reaches the chip, so the MicroPython
    script's `'key'` is rejected outright. The emulator scripts the join unconditionally, so only
    the length matters, never the value.
  - **A separate, pre-existing blocker found on the way, unrelated to this record:**
    CircuitPython **10.x does not boot under this emulator at all** - zero console output,
    indefinitely, on plain `--board pico` as well, so not a CYW43 issue. 9.2.9 and 8.0.2 both
    reach the REPL in seconds. Not root-caused; full investigation trail, including the three
    unimplemented peripheral blocks the boot touches immediately before it goes quiet and the
    core1-FIFO hypothesis that was checked and *ruled out*, is in
    `docs/tasks/circuitpython-10x-boot-stall.md`. That is also why the verification above uses
    9.2.9. **Since root-caused and fixed** the same day, in
    [0050](0050-qspi-pad-reset-values.md): `PADS_QSPI`'s reset values were bank0's, so
    `GPIO_QSPI_SS` came up with a pull-*down* and read low forever - a permanently-held BOOTSEL
    button, which 10.x polls from RAM during boot. 10.2.1 now boots, and is in
    `ci-circuitpython.yml`'s matrix as the regression test. The CircuitPython WiFi verification
    above was not re-run against 10.x - it remains a 9.2.9 result.
  - Landed alongside: `.github/workflows/ci-circuitpython.yml` - the first CircuitPython CI this
    project has ever had (a boot check plus a soft-failing `pico_w` WLAN step, mirroring
    `ci-micropython.yml`). Its absence is why the 10.x stall could go unnoticed while `README.md`
    advertised `--circuitpython` and used `--image 10.2.1` as its example.

- 2026-08-16: **Kaluma verified too - a third, independent network stack.** Asked whether Kaluma's
  Pico W WiFi works here at all (nothing in this repo had ever exercised it: no `pico_w` Kaluma
  test, no WiFi step in `ci-kaluma.yml`), and it does, end to end, against real Kaluma `1.2.1`:
  `require('wifi')` resolves, `scan()` returns the fixed fake AP
  (`{"ssid":"RP2040PY-GUEST","bssid":"42:13:37:55:AA:01","security":"OPEN","rssi":-87,"channel":6}`),
  `connect()` succeeds, and a real `net.Socket` to `1.1.1.1:80` returns a live
  `HTTP/1.1 426 Upgrade Required` with a current date. Landed as `tests/kaluma/main-cyw43.js` plus
  a soft-failing `pico_w` WLAN step in `ci-kaluma.yml`, pinned to one runtime (the step buys stack
  coverage, not interpreter coverage, and it touches the real internet - running it across the
  whole matrix would be cost without signal).
  - **What this confirms architecturally**: three network stacks - MicroPython's lwIP,
    CircuitPython's, and now Kaluma's - drive the identical emulated bus with **no changes to
    `bus.py` or `nat.py` for any of them**. That is the payoff of terminating at gSPI/SDPCM rather
    than at any firmware's own API, and it is now evidence rather than reasoning.
  - Kaluma renders the fake AP as `security: "OPEN"`, which makes one of this record's own "Known
    gaps" plainer than the other stacks do: there is no auth at all to get wrong, so the
    "a *wrong* password currently succeeds too" gap reads here as simply an open network.
  - Not covered by this check: DNS, TLS/HTTPS, and Kaluma's own `http` module. Only raw TCP to a
    fixed IP was exercised.

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

## Known gaps for full functionality (documented 2026-08-16, not designed/fixed here)

A user-requested honest inventory of what's still missing before this module could be called a
complete WiFi emulation, not just "the happy path works." Grouped by severity - this is a doc note
only, per this repo's document-vs-implement convention; none of this is designed or fixed here.

**Real correctness gaps** (not missing features - the existing code's behavior is wrong in these
cases):

- ~~A real remote RST is silently turned into a clean FIN.~~ **Fixed 2026-08-16**:
  `_pump_host_to_guest()` now catches `ConnectionResetError` specifically (before the generic
  `OSError` handler) and sends the guest a real `TCP_RST|TCP_ACK` instead of a FIN. Verified with
  a new hermetic test that forces a real kernel-level RST via `SO_LINGER`
  (`test_tcp_reflector_propagates_a_real_reset_as_rst_not_a_clean_fin`).
- ~~`disconnect()` from the guest has no effect.~~ **Fixed, see
  [0054](0054-cyw43-disassoc.md) (2026-08-16)**: `bus.py` now answers `WLC_DISASSOC` with
  `_queue_disassoc_events()` (`CYW43_EV_DISASSOC` + a link-down `CYW43_EV_LINK`), derived from
  `cyw43-driver`'s own event handlers rather than guessed.
- ~~A real connection attempt that never resolves leaks its flow-table entry forever.~~ **Fixed
  2026-08-16**: `TcpReflector` gained a `connect_timeout` (default 10s) wrapping the real connect
  in `asyncio.wait_for()` - a timeout now gets the same synthesized-RST-and-evict treatment as any
  other connect failure. Also fixed a latent bug found while making this change: the pre-existing
  UDP relay's own timeout handler caught bare `TimeoutError`, which is a *different* class from
  `asyncio.TimeoutError` on Python 3.10 (they only become the same class on 3.11+) - this project's
  own floor is `>=3.10`, so on 3.10 that except clause silently never fired at all. Both places now
  catch `asyncio.TimeoutError` explicitly. `TcpReflector` also gained an injectable `connect_fn`
  (defaults to `asyncio.open_connection`) specifically so this timeout path could be tested
  hermetically (a connect that provably never resolves) rather than needing a real black-holed
  network condition, which isn't reliably reproducible in a test environment.
- No backpressure from the real destination's write-buffer onto the guest's advertised window (see
  the paragraph immediately above this section - restated here for completeness of this inventory).
  **Still open.**

**Entirely unbuilt, not partially-done:**

- **AP mode** (`network.WLAN.IF_AP`) - not implemented at all; `tests/micropython/main-cyw43.py`
  still has it commented out.
- **Only one fixed fake AP/SSID exists** (`RP2040PY-GUEST`) - no multi-network scan results, no
  hidden-SSID case, no auth-type variation. Join is scripted unconditionally regardless of the
  password given, so a *wrong* password currently "succeeds" too - there's no negative-auth path
  to test against.
- ~~UDP beyond port 53 is dropped entirely.~~ **Fixed 2026-08-16**: `DnsRelay` generalized into
  `UdpRelay` - see the dedicated Progress log entry below. `ntptime`/mDNS/custom UDP now work the
  same way DNS itself did once 4e landed.
- **No IPv6.**
- **Single-guest-only architecture** - `GUEST_IP`/`GATEWAY_IP`/`GATEWAY_MAC` are fixed module
  constants (see the "Deferred" paragraph above); no config surface, no multi-device scenario ever
  exercised.

**Unverified, not necessarily broken:**

- ~~**CircuitPython** has never been live-booted through this bus/NAT path at all - only
  MicroPython `v1.23.0`/`v1.28.0`. Expected to work (both vendor the same `cyw43-driver`, per this
  record's own earlier reasoning), but not confirmed.~~ **Verified 2026-08-16** against
  CircuitPython `9.2.9` - scan/join/DHCP/TCP/DNS all work through `wifi`/`socketpool`, with
  `tests/circuitpython/main-cyw43.py` and a new `ci-circuitpython.yml` to keep it that way. See the
  dedicated Progress log entry above - including the separate, unrelated finding that CircuitPython
  **10.x** does not boot under this emulator at all
  (`docs/tasks/circuitpython-10x-boot-stall.md`), which is why 9.2.9 is the version used.
- ~~**Real TLS/HTTPS (`ussl`) and WebSocket** were reasoned through as transparent (the TCP splice
  is payload-agnostic) but never actually live-boot exercised end-to-end - only `mip`'s own HTTPS
  fetch inside `mip.install()` has been (that succeeded, which is at least indirect evidence for
  this).~~ **Verified 2026-08-16** on `v1.28.0` and `v1.23.0` - real TLS against a real public host
  (real certificate chain), plus RFC 6455 WebSocket and WebSocket-over-TLS against a purpose-built
  echo server on a non-loopback address. See the dedicated Progress log entry above for the
  evidence, the two MicroPython API traps hit on the way (`read(n)` blocking for exactly n;
  v1.23.0's `SSLSocket` having no `send()`), and why the WebSocket half is verified by hand rather
  than wired into CI.
