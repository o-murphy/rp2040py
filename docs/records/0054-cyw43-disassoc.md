# 0054. CYW43 `disconnect()`: answer `WLC_DISASSOC` and script the link-down events

- Status: Implemented — verified (2026-08-16)
- Conceived: 2026-08-16
- Related: 0048 (this closes the first of its three remaining "Known gaps" - the only one that was
  a real correctness bug rather than an unbuilt feature), 0027 (step 3g, whose
  `_queue_join_events()` this mirrors)

## The bug

Only link-*up* was ever scripted. `bus.py` had no handling for the disassociate ioctl at all, so a
guest calling `disconnect()` got the generic ioctl ack, no events, and went on believing it was
still associated: `isconnected()` stayed `True` and `status()` stayed `3` (`CYW43_LINK_UP`)
forever.

## Derivation, from the driver source rather than guessed

0048 flagged that this "needs the same kind of protocol research 4a-4c needed", and 0027's own 3g
note is explicit about not guessing event sequences. So, from `georgerobotics/cyw43-driver`:

- **Which ioctl.** `network_cyw43.c`'s `disconnect()` → `cyw43_wifi_leave()` (`cyw43_ctrl.c:666`)
  → `cyw43_ioctl(self, CYW43_IOCTL_SET_DISASSOC, 0, NULL, itf)`. `CYW43_IOCTL_SET_DISASSOC` is
  `0x69` (`cyw43_ll.h:58`), and `cyw43_ll_ioctl()` (`cyw43_ll.c:1187-1190`) splits that as
  `cmd & 1 ? SDPCM_SET : SDPCM_GET` with `cmd >> 1` as the command - so `0x69` is a **SET of WLC
  command `0x34` = 52 = `WLC_DISASSOC`**. The constants encode the direction bit; the raw number
  on the wire is 52.
- **Which events.** `cyw43_cb_process_async_event()` (`cyw43_ctrl.c`) has two handlers that take a
  station link down, and either alone is sufficient:
  - `CYW43_EV_DISASSOC` (11) calls `cyw43_cb_tcpip_set_link_down()` **and** sets
    `wifi_join_state = 0x0000`. That second half is what actually flips the guest's own view:
    `cyw43_wifi_link_status()` (`cyw43_ctrl.c:613`) returns `CYW43_LINK_DOWN` exactly when
    `wifi_join_state & WIFI_JOIN_STATE_KIND_MASK` is zero, and that is what `isconnected()` reads.
  - `CYW43_EV_LINK` (16) with `status == 0` and bit 0 of `flags` **clear** takes the "Link is down"
    branch and calls `cyw43_cb_tcpip_set_link_down()`.

## Fix

`WLC_DISASSOC` (52) now gets the same treatment `WLC_SET_SSID` does: the generic ack first, then a
scripted event pair queued behind it - `CYW43_EV_DISASSOC` then `CYW43_EV_LINK` with `flags = 0`.
Both are sent, mirroring the join sequence's own shape (which ends with `_LINK`, `flags = 1`), and
because the handlers are idempotent a second `set_link_down()` costs nothing. `DISASSOC` goes
first so the join state is already cleared by the time the link event lands.

## Verified

- Unit (`tests/test_cyw43_bus.py`): the ack carries the request's own id, the two events follow it
  in order with `status == 0`, the link event's flags bit 0 is *clear*, and both are addressed to
  `CYW43_ITF_STA`. A second test runs join-then-disassoc so the up/down pair is exercised as one
  sequence, the way real traffic produces it.
- **Live boot, both tracked firmware versions.** Connect, poll, then `disconnect()`:

  | | v1.23.0 | v1.28.0 |
  | --- | --- | --- |
  | after `connect()` | `isconnected=True status=3 ip=10.0.0.2` | same |
  | after `disconnect()` | `isconnected=False status=0` | same |
  | wall clock | 15s | 19s |

- `uv run pre-commit run --all-files` clean, both builds.

## The bridge's own state, and a deferral that did not survive review

The first draft of this record deferred tearing down NAT state on disassociation, on the grounds
that it "is also not observable". **That was wrong, and worth recording as such**, because the
reasoning failed in a specific, checkable way rather than a vague one.

`TcpReflector.maybe_handle()` looks up `(src_port, dst_ip, dst_port)` and, when a flow exists,
routes the segment into it - *including a SYN*. Only the no-flow branch treats a SYN as a new
connection. So a flow left behind by a dead association swallows the guest's new SYN the moment it
reconnects and reuses that triple: no SYN-ACK is ever synthesized, and the guest's `connect()`
hangs until its own timeout. Stale state that outlives the thing that created it, with a concrete
failure - not a theoretical tidiness point.

So `TcpReflector.reset()` (cancel each pump task, close its writer, clear the table) and
`NatBridge.reset()` now exist, and `WLC_DISASSOC` calls the latter before queueing its events.
Cancel-then-close ordering matters: a pump task parked on `window_opened.wait()` or on a socket
read must be cancelled before its writer goes away, or it wakes into a closed transport.

Only the TCP reflector holds per-association state. The UDP relay is one-shot per datagram, and
the DHCP lease is a fixed module constant rather than an allocation (0048's "Deferred" section),
so the next join is handed the same address regardless.

`tests/test_cyw43_nat.py` covers the actual regression rather than just the bookkeeping: establish
a flow, reset, then re-SYN **on the same triple** and assert a SYN-ACK comes back acknowledging the
new sequence number. `tests/test_cyw43_bus.py` covers the wiring - joining resets nothing,
disassociating resets exactly once, and a bus with no `nat_bridge` attached still answers the
ioctl.

## Still not done

A real per-association lifecycle - fresh lease per join, per-guest state, AP-side teardown - is
part of the multi-guest/config-surface work in 0048's "Known gaps", not this record.
