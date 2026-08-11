# CYW43439 / Pico W WiFi emulation — research notes and implementation plan

**Unblocked (2026-08-12): [docs/MAIN_THREAD_ASYNCIO_BACKLOG.md](MAIN_THREAD_ASYNCIO_BACKLOG.md)
has fully landed** (all 5 phases done and verified - `Simulator`'s engine-room loop now runs on
whichever loop the caller itself owns, via `bind_loop()`, matching upstream rp2040js's
single-threaded model for the common single-instance case; a dedicated background thread is now
the exception, not the default). Step 0 below (board-loading API - `ExternalDevice`, `boards.py`,
`schedule_threadsafe()`) was originally built and merged against the *old* background-thread
engine room on a since-abandoned branch, then **rebuilt here from scratch directly on top of the
new architecture** (`feat/board-loading-api`, branched from `refactor/main-thread-asyncio`) rather
than ported as a literal patch - `Simulator.__init__`'s `rp2040=` constructor argument,
`schedule_threadsafe()`, and `bind_loop()`'s interaction with it all needed re-verifying against
the new single-loop model, not just copying over. Step 2+ (the actual CYW43439 bus/chip/NAT work)
can now safely start on top of this, per "Implementation order" below.

Not started yet — this is the pre-work: what we confirmed about the real hardware and about how
Wokwi (closed-source, but built on the same open `rp2040js` this project ports) actually pulled it
off, plus the plan we're building toward. Goal (per discussion 2026-08-11): real internet access
from emulated firmware, SLIRP/NAT-style (see "Implementation order" below), not just a canned
"connected" stub.

This is a **new feature**, not a porting gap: upstream `rp2040js` has no CYW43439/WiFi support at
all (confirmed via `gh api search/code` against `wokwi/rp2040js` — zero hits for `cyw43`/`wifi`),
and it isn't part of the RP2040 chip itself (the WiFi chip is a separate, external Infineon part on
the Pico W board) — so it's out of scope for the file-by-file port tracked in `docs/PORTING.md`.
Lives in `src/rp2040py/` once built (it's board-level, not `demo/`-only, unlike the E-Ink virtual
display work — that one stayed demo-only because it's an arbitrary external SPI peripheral the user
wires up themselves; CYW43439 is fixed hardware on every Pico W).

**Reading this document cold, with no other context?** Skip to "Implementation order" near the end
— it's the actionable summary, in build order, and links back into everything above it. Everything
before it is either hardware/protocol reference material (needed once you're actually implementing
a given step) or the design decisions that shape *how* to build it. "Open questions" at the very
end is what's genuinely still undecided or unresearched — check it before assuming a gap is an
oversight.

## Hardware: how RP2040 talks to the CYW43439

Confirmed pinout (from the Wokwi maintainer's own research notes,
[wokwi-features#466](https://github.com/wokwi/wokwi-features/issues/466)):

| RP2040 Pin | CYW43439 Pin | Description |
|---|---|---|
| `GPIO23` | `WL_ON` | wireless power-on signal |
| `GPIO24` | `WL_D` | wireless SPI data/IRQ — **one shared, half-duplex data line** |
| `GPIO25` | `WL_CS` | wireless SPI CS |
| `GPIO29` | `WL_CLK` | wireless SPI CLK (when `WL_CS` is high, this pin instead reads VSYS via ADC3) |

This is **not** the RP2040's standard `RPSPI` peripheral — pico-sdk's `cyw43-driver` bit-bangs a
custom half-duplex "gSPI" protocol over these plain GPIOs via **PIO** (`cyw43_bus_pio_spi.pio` in
the driver source), with F0/F1/F2 backplane addressing similar in spirit to SDIO. This means the
`RPSPI.on_transmit` hook used for the E-Ink virtual display
(`demo/components/virtual_eink.py`) does **not** apply here.

**Correction after reading Wokwi's actual source (see below): the integration point does *not* need
to be PIO-instruction-level either.** They hook plain `GPIOPin`-style edge listeners on the 3 shared
electrical pins directly (clock/data/CS) and bit-bang-decode the protocol themselves from those
transitions, the same primitive (`GPIOPin.add_listener()`/`set_input_value()`) already used for the
E-Ink demo — they never inspect what PIO program is loaded or execute it specially. This is good
news: it means our existing GPIO listener/driver primitives in `gpio_pin.py` are sufficient; no new
PIO-program-recognition machinery is needed for the bus-level step (step 2 in "Implementation
order" below) after all.

Useful independent reference for the wire format (reverse-engineered without vendor NDA docs, cited
by the Wokwi maintainer as their own starting point too): <https://iosoft.blog/2022/12/06/picowi/>.

### Onboard LED and pin differences vs. plain Pico (2026-08-11)

Worth documenting up front since it affects both firmware compatibility expectations and, later,
what `Cyw43439` needs to answer for: on a plain Pico, the onboard LED is wired directly to
`GPIO25` on the RP2040 itself - `machine.Pin(25, machine.Pin.OUT)` in MicroPython, or
`machine.Pin("LED", ...)` (the board-level alias) works identically either way. On a **Pico W**,
the onboard LED is instead wired to the **CYW43439** (the wireless chip), not to any RP2040 GPIO -
real firmware must go through the wireless driver to toggle it (MicroPython:
`machine.Pin("LED", machine.Pin.OUT)` resolves to `cyw43.WL_GPIO0` internally rather than a plain
GPIO pin - `Pin(25, ...)` does **not** work on a Pico W the way it does on a plain Pico, since
GPIO25 is repurposed there). This means LED control alone already exercises the CYW43439 bus
protocol (step 2/3 in "Implementation order" below) - it's not WiFi-specific traffic, but it's
gated behind the same gSPI transaction path a real Pico W's LED blink would use, and can't be
faked with a bare GPIO listener the way it can on a plain Pico.

Also relevant to keep in mind for board-level accuracy (not yet consumed by any implementation
step above, just recorded so it isn't rediscovered later): the Pico W exposes an extra `ADC_REF`
pin not present on plain Pico, and has internal RP2040-to-CYW43439 wiring beyond the four gSPI
pins already documented above (SPI-adjacent power/GPIO lines used for the wireless chip's own
power-saving mode switching) - fixed-hardware detail belonging to a `Cyw43439`
`ExternalDevice`/board-pinout description whenever that's built, not something the generic
board-loading plumbing (step 0) needs to model.

## How Wokwi actually does it — confirmed live via Chrome DevTools (2026-08-11)

Investigated `https://wokwi.com/projects/new/micropython-pi-pico-w` directly (network panel,
console, and their own "PIO Debugger" tab) rather than guessing from the closed-source app bundle.
Findings, all reproduced live:

- **The "PIO Debugger" tab appearing is a red herring, corrected below.** Starting the simulation
  surfaces `rp2040js`'s standard PIO debug UI, which first read as "they emulate gSPI-over-PIO." Not
  so, per the actual decompiled source (see the dedicated section further down): real firmware still
  loads a genuine PIO program onto a state machine (for real-hardware compatibility) and that's what
  the debugger tab is showing, but Wokwi's chip model doesn't execute or care about that program at
  all — it taps the underlying GPIO pins directly instead. Left as a documented false lead rather
  than silently deleted, since it's an easy trap to fall into again from the same UI evidence.
- **Unmodified real firmware.** The board downloads
  `wokwi.github.io/firmware-assets/micropython/rp2-pico-w-<date>-v1.28.0.uf2` — an ordinary-looking
  official MicroPython Pico W build, not a Wokwi-patched one.
- **Two internal layers, named in their own analytics tags:** `netwi` (client-side, e.g.
  `ep.event_label=netwi-1.2.3` on a GA event) — almost certainly the in-browser network-stack
  simulation — and `hexi` — a **server-side** component: `GET https://wokwi.com/api/hexi/monitor`
  with `accept: text/event-stream` (Server-Sent Events, cookie-authenticated, same-origin). A
  browser sandbox can't open raw TCP sockets, so *some* server-side relay is the only way to reach
  the real internet — `hexi` is that relay.
- **Confirmed fake AP, real DHCP-style NAT addressing:**
  - `network.WLAN(network.STA_IF).scan()` → `[(b'Wokwi-GUEST', b'B\x137U\xaa\x01', 6, -87, 0, 1)]`
    — one fixed, fake access point.
  - `.connect('Wokwi-GUEST', '')` → `.ifconfig()` settles to
    `('10.10.0.1', '255.255.0.0', '10.0.0.1', '10.0.0.1')` — private, NAT-range addressing
    (SLIRP/user-mode-networking style), not a bridge onto the host's real LAN.
- **Confirmed genuinely real internet access, not a fake/canned response:**
  - `socket.getaddrinfo('example.com', 80)` → `[(2, 1, 0, '', ('172.66.147.243', 80))]`, a real
    routable IP.
  - `urequests.get('http://example.com/')` → HTTP 200, body starts
    `<!doctype html><html lang="en"><head><title>Example Domain</title>...` — the actual real
    `example.com` page.
  - Notably, **none of this appears as an ordinary `fetch`/`XHR`/WebSocket in the Network panel** —
    the request/response traffic most likely rides the existing `hexi` SSE stream as an ad hoc
    duplex tunnel (SSE is naturally server→client; the client→server direction is presumably a
    separate POST or multiplexed some other way — not fully confirmed, didn't chase further since
    it's their proprietary backend and not something we'd replicate 1:1 anyway).

**Bottom line for us:** Wokwi's approach is architecturally "proxy real socket I/O through our own
backend server." We don't need a backend server for the same effect — the emulator already runs
as a normal local process on the user's machine, so the equivalent move is a **SLIRP-style userspace
NAT built directly into the Python process**: translate the emulated CYW43439's TCP/UDP/DNS activity
into real `socket` calls made by `rp2040py` itself, no TUN/TAP device and no root required (this is
exactly how QEMU's `-netdev user` works under the hood).

## Wokwi's actual source, decompiled live (2026-08-11)

Not guesswork — pulled directly from their shipped (minified, not obfuscated beyond that) JS bundle
via `fetch()` inside the running page (same-origin, no CORS issue) and read the real method bodies.
Chunk was `https://wokwi.com/_next/static/chunks/9998.e029603e4fbe4cf6.js` at the time (their bundle
hashes will rot — re-search for `initCYW43` / `class W` across current chunks if revisiting this).
What follows is a description of the architecture in our own words, from having read it, not a
transcription - a saved copy of the actual decompiled source is kept locally, off-repo, in case the
protocol-level details below ever need re-checking.

**Wiring:** their runner sets up a small generic bit-banged SPI-slave helper - the same generic
class they use for real SPI0/SPI1, not anything CYW43-specific - bound to three logical signal
lines (clock/data-out/data-in/select), then connects those to the actually-emulated GPIOs purely
through plain listener registration (conceptually the same `GPIOPin.add_listener()`/
`set_input_value()` primitives this project already has): WL_CLK's listener updates the helper's
clock line; WL_D's listener feeds the helper's host-to-chip data line when the MCU is driving it,
while the helper's chip-to-host data line drives WL_D's *input* value only during the half of the
transaction where the chip is the one driving the shared pin; and WL_CS's listener flips that
driving-direction flag, resets per-transaction framing state, and tells the chip model whether it's
currently selected - importantly, when CS is *not* asserted, WL_D instead carries the chip's IRQ
level, matching real hardware's shared-pin behavior. No PIO instruction execution is involved
anywhere in this.

**Bit-bang framing (`BitBangSPI.onTransmit`, accumulated in the runner):** the bus is **32-bit-word
oriented**, MSB byte order, half-duplex: 4 bytes shift in to build a word; the *first* word after
select is the gSPI command header (written via `chip.writeUint32(word)`, which sets `chip.cmd`);
once a header is parsed (`chip.cmd` truthy) subsequent activity streams response data pulled via
`chip.readUint32()`, sent back one byte at a time as the same clock continues. This matches real
gSPI's word-oriented command/data framing, not a byte-stream protocol.

**The chip model itself (`class W` in their bundle → renamed `Cyw43Chip` here) is a real, fairly
deep emulation, not a stub:**
- Fixed fake `staMAC = "28:cd:c1:00:12:34"`.
- `busRead(addr)` answers the **gSPI/SDIO function-0 bus register block** at small fixed offsets
  (0=`ctrl`, 4=`int`, 6=`intEnable`, 8=status/event-length composite, ...) — this is the same
  low-level register set real `cyw43_ll.c` bit-bangs during bus init/IRQ handling.
- `handleControl(header)` decodes **SDPCM control/ioctl messages** and switches on real Broadcom/
  Cypress `WLC_*` ioctl IDs (its `switch` cases are literally `2, 20, 22, 64, 86, 110, 134, 142,
  165, 26, 52, 262` — real `WLC_GET_VAR`/`WLC_SET_VAR`/etc. command numbers from `cyw43_ll.h`).
  Case 26 (an `iovar`/join-related ioctl) fires a whole scripted sequence of `asyncEvent(...)` calls
  matching real `WLC_E_*` event codes (`WLC_E_LINK`, `WLC_E_SET_SSID`, `WLC_E_PSK_SUP`, etc.) — this
  is what makes MicroPython's `network.WLAN().status()` progress through the same states real
  firmware would report.
- `scanResponse(ap)` serializes a fake scan-result record in the real `wl_bss_info_t`-shaped layout
  (bssid, ssid, channel, rssi, ...) — this is where the fixed `Wokwi-GUEST` entry comes from.
- `writeFrame(data)` delivers an inbound (received-from-air) Ethernet frame to the host by wrapping
  it as an SDPCM data event.
- `get irq() { return (this.int & this.intEnable) !== 0; }` — plain level-interrupt, mask-and-status.

**Outbound path / fake network:** `chip.onTX` (host wants to transmit a frame) wraps it as an
802.11 data frame against `wifi.accessPoints[0]`'s BSSID and calls a shared
`wifi.medium.transmit({channel: 6, data})` — a broadcast-domain object every simulated radio
(including the fake AP) presumably listens on; `wifi.medium.listen(...)` filters for inbound
`Data`-type frames and feeds them back into the chip via `writeFrame()`. The actual NAT/DHCP/
internet-bridging logic (presumably on the AP object, ultimately reaching the `hexi` SSE backend
found earlier) lives in a part of the bundle not yet located — still an open item below.

**`patchWLAN()` is a minor, separate optimization, not the core mechanism:** it looks up one
specific function by a build-hash symbol ID (from a symbol database tied to their own known-good
prebuilt firmware) and — only if found — hot-patches one specific call site to return immediately
instead of executing, for one specific argument pattern (`r1==1, r3==64`, i.e. one specific ioctl/
wait call). This is almost certainly what breaks for recompiled firmware (the symbol won't match a
different build) — but the *protocol emulation itself* (`class W` above) doesn't depend on it; a
recompiled binary should still work, just without whatever this one shortcut skips (likely a slow
calibration/wait loop). Good news for us: we don't need any build-specific patching to get the core
protocol working against arbitrary compiled firmware.

## Authoritative protocol reference — local official source (2026-08-11), supersedes reverse engineering

Everything below is confirmed straight from the **actual official, BSD-3-Clause-licensed** driver
and SDK source, checked out locally — not reverse-engineered from anyone's binary or minified JS.
This is now the primary reference for implementation steps 2/3 below; the Wokwi investigation above
stays in this doc only for the *architectural* insight (bit-bang via GPIO listeners, not PIO
execution) and for the historical record of how it was found, not as a source of protocol facts
anymore.

**Locations** (this machine): pico-sdk at
`/home/murphy/pyproj/micropython/lib/pico-sdk/src/rp2_common/pico_cyw43_driver/`
(`cyw43_bus_pio_spi.c`, `cyw43_bus_pio_spi.pio`) and the driver itself at
`/home/murphy/pyproj/micropython/lib/cyw43-driver/src/` (`cyw43_spi.h`, `cyw43_ll.h`, `cyw43_ll.c`).
Note: `lib/cyw43-driver` was an *empty* git submodule placeholder until this session — populated via
`git submodule update --init lib/cyw43-driver` (from `/home/murphy/pyproj/micropython`; the real
upstream is `github.com/georgerobotics/cyw43-driver`, also BSD-3-Clause). Re-run that if it's ever
missing again in a fresh checkout.

**Command header** — `cyw43_bus_pio_spi.c`'s `make_cmd()` (quoted verbatim; it's a trivial bit-pack,
not creative expression, and BSD-3-Clause explicitly permits this anyway):

```c
static inline uint32_t make_cmd(bool write, bool inc, uint32_t fn, uint32_t addr, uint32_t sz) {
    return write << 31 | inc << 30 | fn << 28 | (addr & 0x1ffff) << 11 | sz;
}
```

I.e. a 32-bit word: bit 31 = write/¬read, bit 30 = address-auto-increment, bits 29-28 = function
number, bits 27-11 = 17-bit address, bits 10-0 = 11-bit size. Sent MSB-first as the first word of a
transaction; for reads, response data words follow starting right after (with an extra
`CYW43_BACKPLANE_READ_PAD_LEN_BYTES` of padding delay specifically for `BACKPLANE_FUNCTION` reads,
not for bus/WLAN reads — see `cyw43_bus_pio_spi.c:391-425`).

**Function numbers** (`cyw43_internal.h:42-44`): `BUS_FUNCTION=0`, `BACKPLANE_FUNCTION=1`,
`WLAN_FUNCTION=2` — standard SDIO F0/F1/F2 numbering.

**F0 bus register map** (`cyw43_spi.h:46-116`, function 0, addressed directly via `make_cmd`'s
address field): `SPI_BUS_CONTROL=0x0000`, `SPI_RESPONSE_DELAY=0x0001`, `SPI_STATUS_ENABLE=0x0002`,
`SPI_RESET_BP=0x0003`, `SPI_INTERRUPT_REGISTER=0x0004` (16-bit), `SPI_INTERRUPT_ENABLE_REGISTER=
0x0006` (16-bit), `SPI_STATUS_REGISTER=0x0008` (32-bit), `SPI_FUNCTION1_INFO=0x000C`,
`SPI_FUNCTION2_INFO=0x000E`, `SPI_READ_TEST_REGISTER=0x0014`. Key status bits on
`SPI_STATUS_REGISTER`: `STATUS_F2_RX_READY=0x20` (driver busy-polls this before a WLAN-function
bulk write - see `cyw43_write_bytes()`), `STATUS_F2_PKT_AVAILABLE=0x100` +
`STATUS_F2_PKT_LEN_MASK=0x000FFE00` (inbound packet ready + its length), `STATUS_DATA_NOT_AVAILABLE
=0x01`, `STATUS_UNDERFLOW`/`STATUS_OVERFLOW=0x02`/`0x04`. Interrupt register bits (16-bit,
`cyw43_spi.h:84-98`): `F2_PACKET_AVAILABLE=0x0020`, `F1_INTR=0x2000`, `F2_INTR=0x4000`, etc.

**Backplane (F1) windowed addressing** (`cyw43_ll.c:~133-150, ~350-390`): the F1 address field in
`make_cmd` is only 17 bits, so backplane accesses use a moving 32-bit "window": the low 15 bits
(`BACKPLANE_ADDR_MASK=0x7fff`) go directly in the command address field, while the upper bits are
written separately to three fixed F1 registers - `SDIO_BACKPLANE_ADDRESS_LOW=0x1000a`,
`_MID=0x1000b`, `_HIGH=0x1000c` (one byte each) - before the actual read/write. Standard SDIO
"SB window" pattern.

**Reset timing** (`cyw43_bus_pio_spi.c`'s `cyw43_spi_reset()`): drive `WL_REG_ON` (GPIO23) low,
delay 20ms, drive high, delay 250ms, then treat GPIO24 as the IRQ input until a transaction starts.

**Real ioctl/event IDs** (`cyw43_ll.c:191-211`, `cyw43_ll.h:67-82`) — confirms every number Wokwi's
minified bundle used, now with real names instead of bare numbers: `WLC_UP=2`,
`WLC_SET_INFRA=20`, `WLC_SET_AUTH=22`, `WLC_SET_ANTDIV=64`, `WLC_SET_PM=86`, `WLC_SET_GMODE=110`,
`WLC_SET_WSEC=134`, `WLC_SET_BAND=142`, `WLC_SET_WPA_AUTH=165`, `WLC_SET_SSID=26`,
`WLC_DISASSOC=52`, `WLC_GET_VAR=262`, `WLC_SET_VAR=263`. Events: `CYW43_EV_SET_SSID=0`,
`CYW43_EV_JOIN=1`, `CYW43_EV_AUTH=3`, `CYW43_EV_ASSOC=7`, `CYW43_EV_LINK=16`,
`CYW43_EV_PSK_SUP=46`, `CYW43_EV_ASSOC_REQ_IE=87`, `CYW43_EV_ASSOC_RESP_IE=88`,
`CYW43_EV_ESCAN_RESULT=69` (scan results).

**Bus transfer mechanics** (`cyw43_bus_pio_spi.c`'s `cyw43_spi_transfer()`): CS asserted low for the
whole transaction; PIO shifts the command word (and any TX data) out MSB-first over the single
shared data pin (`spi_gap01_sample0` is the default program - see the `.pio` file's four program
variants, all built around `out pins,1` / `set pindirs,0` (flips the pin to input mid-transaction)
/ `in pins,1`), driven by a pair of DMA channels with byte-swap enabled (`channel_config_set_bswap`)
since the wire format is big-endian-ish word order but the CPU is little-endian. All transfers are
word-aligned (asserted `& 3` checks throughout) - matches the 32-bit-word framing already inferred
from Wokwi's bundle, now confirmed from the source driving it.

## Module layout decision (2026-08-11)

Resolves "where in `src/rp2040py/` does this belong."

**Not in `peripherals/`.** Every file there (`spi.py`, `dma.py`, `pio.py`, ...) inherits
`BasePeripheral` and implements `read_uint32`/`write_uint32` for a specific memory-mapped address
(see `peripherals/peripheral.py`). The CYW43439, from the CPU's point of view, is not a
memory-mapped peripheral at all — it's driven exclusively through `gpio[23/24/25/29]` listeners.
There's a precedent for GPIO-listener-driven behavior even inside `peripherals/`:
`ssi.py:116` also hooks `rp2040.qspi[1].add_listener(...)` as a helper. But there it's a secondary
mechanism layered on top of a real register-based interface (`SSI`'s normal `read_uint32`/
`write_uint32`); for CYW43439 the GPIO-listener bus decode is the **only** mechanism — there is no
backing register block to fall back on.

**New subpackage `src/rp2040py/cyw43/`**, following the same pattern as `clock/`, `gdb/`, `usb/`,
`utils/` — a real package with an `__init__.py`, not a single file:

- `cyw43/bus.py` — bit-bang gSPI decode (step 2 in "Implementation order" below, GPIO-listener
  level: `make_cmd()` header parsing, F0 bus register block).
- `cyw43/chip.py` — the chip model itself: F0/F1 registers, backplane windowed addressing,
  `WLC_*`/`WLC_E_*` ioctl and event handling (step 3). This is also where the `Cyw43439` class
  that implements `ExternalDevice` (see "Board composition decision" next) lives.
- `cyw43/nat.py` — the SLIRP-style userspace NAT bridge (step 4).

**Wiring:** *not* baked into `RP2040.__init__()` — see "Board composition decision" next.
`RP2040` itself stays unchanged; a board-setup step calls
`attach_external_devices(rp2040, Cyw43439())` (or `Cyw43439().attach(rp2040)` directly) after
construction, once `self.gpio` already exists (`_rp2040.py:120`), so `Cyw43439.attach()` can hook
listeners onto `gpio[23]`/`gpio[24]`/`gpio[25]`/`gpio[29]`.

## Board composition decision (2026-08-11)

Resolves the earlier "opt-in mechanism" question, in light of a broader point: this project
will eventually want to emulate boards beyond Pico/Pico W too — other vendors' RP2040 boards
(Waveshare RP2040-Zero, YD-RP2040, ...) — and later other MCU silicon (RP2350/Pico 2). Two
independent axes (MCU variant, board-specific fixed extras) argue against solving this with
`RP2040` subclasses.

**Rejected: one `RP2040` subclass per board.** Most third-party RP2040 boards are the identical
die — they differ only in pin breakout and maybe one onboard extra (an RGB LED on a PIO-driven
pin, a different flash size), not in chip *behavior*. Subclassing per board crosses that with the
MCU-variant axis and the cyw43-or-not axis combinatorially (`Pico`, `PicoW`, `Pico2`, `Pico2W`,
`WaveshareZero`, `YD2040`, ...) for what's mostly metadata, not different emulation logic.

**Decision: composition, not inheritance — via an `ExternalDevice` component interface, not a
callable/string dispatch table.**

- `RP2040` itself stays **completely** unchanged — not even one new method. **Decided: a
  superstructure on top of the emulator, not a reconstruction of it.** `attach_external_devices()`
  is a plain standalone function, not an `RP2040` method, taking the MCU plus a variadic list of
  devices — named `attach_external_devices`, not the shorter `attach_devices`, specifically so
  it's unambiguous this is about `ExternalDevice`s:

  ```python
  def attach_external_devices(mcu: "RP2040", *devices: ExternalDevice) -> None:
      for device in devices:
          device.attach(mcu)
  ```

  Lives next to the `ExternalDevice` `Protocol` definition, not inside `_rp2040.py`. Callable
  anywhere, on any `RP2040` instance, board-registry-constructed or not — exactly the same
  "public extension point, no dependency on the board registry" property as calling
  `device.attach(mcu)` directly, just batched.

  **The pre-run-only constraint (see the dedicated subsection below) stays a documented contract,
  not a runtime-enforced check — deliberately.** `RP2040` has no "am I running" state today
  (`self.stopped`/`self.stopped = False` live entirely on `Simulator`, `simulator.py:63,169,184`;
  `RP2040` doesn't even hold a reference back to whichever `Simulator`, if any, owns it — a bare
  `RP2040()` isn't guaranteed to have one at all). Giving `attach_external_devices()` a real
  runtime guard would mean adding running-state to `RP2040` itself — exactly the reconstruction
  this decision avoids. So for now: call it before starting execution, documented, not asserted.
  Revisit if this ever causes real confusion in practice.
- A structural `Protocol` (mirrors the existing `Peripheral`/`BasePeripheral` pair in
  `peripherals/peripheral.py:36-45`, same idea applied outside `peripherals/`):

  ```python
  class ExternalDevice(Protocol):
      def attach(self, rp2040: "RP2040") -> None: ...
  ```

  `Cyw43439` (`cyw43/chip.py`) implements this structurally, same as peripherals implement
  `Peripheral` without explicit inheritance. Named `ExternalDevice`, deliberately not
  `Peripheral`/`PeripheralDevice` — this project's `Peripheral` already means "memory-mapped,
  `read_uint32`/`write_uint32`", and the whole point of the "Module layout decision" above is that
  CYW43439 is *not* that.
- A small board registry (e.g. `boards.py`) maps each `--board` **name** to a spec of which MCU
  class to instantiate (today only `RP2040`; later `RP2350`) and which already-constructed
  `ExternalDevice` **instances** to attach afterwards — `BoardSpec(mcu=RP2040,
  extras=[Cyw43439()])` for `pico_w`, `BoardSpec(mcu=RP2040, extras=[])` for plain `pico`. No
  separate string→class dispatch table anywhere in this: the only string involved is the one
  `--board` value itself, resolved once via a single lookup into a `BoardSpec` that already holds
  real class/instance references, not further string IDs to re-resolve per extra.
- Board setup = construct the MCU class, then `attach_external_devices(rp2040, *spec.extras)`.
- **This makes `attach()`/`attach_external_devices()` a public extension point, not just internal
  board-preset plumbing.** Any user of the library can wire up their *own* custom hardware onto a
  manually constructed `RP2040` the same way built-in boards do — either one device at a time
  (`MyCustomDevice().attach(rp2040)`) or a batch (`attach_external_devices(rp2040, ...)`) — with no
  dependency on the board registry at all.
  This also retroactively formalizes what `demo/components/virtual_eink.py` already does today, ad
  hoc and without a common interface (raw `GPIOPin.add_listener()`/`RPSPI.on_transmit` wiring): a
  future `ExternalDevice`-shaped rewrite of that demo would use the same interface as `Cyw43439`.
- **CLI:** `--board` (not `--mcu`/`--variant`), `choices=["pico", "pico_w"]` for now — it selects a
  *board*, and board is the right level even once other vendors' boards are added, since most of
  them don't change the MCU variant at all. Extends by adding more `choices` values
  (`waveshare_rp2040_zero`, `yd_rp2040`, later `pico2`/`pico2_w`) and matching registry entries —
  no new flag, no migration of `--board` itself.
- **Decided: any board customization beyond the built-in `--board` presets is API-only, not CLI,
  for now.** This covers both ends of the same spectrum: one extra device layered on top of a
  preset (`pico_w` plus something else), and an entirely custom board — a hand-picked set of
  `ExternalDevice`s that doesn't match any built-in preset at all. `--board` only ever selects from
  its fixed `choices`; there is no `--attach <arbitrary device>` CLI flag and no way to name a
  custom board from the command line, and neither is planned as part of this work. A user embedding
  `rp2040py` as a library already has the full mechanism available directly — construct `RP2040()`
  and call `attach_external_devices(rp2040, MyDevice(...), ...)` / `MyDevice(...).attach(rp2040)`
  with whatever combination of devices their custom board needs — since `attach()`/
  `attach_external_devices()` are public API regardless of how the `RP2040` was constructed or
  whether it came from the board registry at all. Exposing *that* from the CLI (naming a custom
  board or an arbitrary device as a CLI string, expressing per-device constructor kwargs, the
  arbitrary-code-execution surface of a CLI flag that dynamically imports and instantiates
  user-named code) would need real, separate design — a distinct future feature, left undesigned
  here, not something this work is blocked on or needs to solve.

**Summary of the above, stated as one design principle:** the CLI (`--board`'s fixed `choices`) is
a convenience layer, never the ceiling. `ExternalDevice.attach()`/`attach_external_devices()` (and,
later, `detach_external_devices()`) are the real mechanism underneath every built-in board too —
the board registry is just one caller of that public API, not a gate on it. So a user who finds
`--board`'s presets insufficient, or who doesn't want to go through the CLI at all, was never
actually blocked: they construct `RP2040()` directly and attach whatever combination of built-in
or custom `ExternalDevice`s they want, themselves, through the same standalone function the board
registry itself calls internally.

### `attach()`/`attach_external_devices()` timing: pre-run only, for now

**Constraint, confirmed against the actual implementation:** `attach()` (and therefore
`attach_external_devices()`) is only safe to call *before* the Simulator starts running — i.e. as
part of board setup, right after constructing `RP2040` and before `Simulator.start_execution()`.
Not a conceptual choice, a real race: `GPIOPin._listeners` is a plain unsynchronized `set()`
(`gpio_pin.py:103`), mutated by `add_listener()` (`:313-315`) and iterated on every pin value
change (`:299`) — and that iteration runs on the Simulator's dedicated engine-room thread
(`simulator.py:69-73`). Calling `add_listener()` from any other thread (CLI, test, GDB connection)
while a pin-change iteration is in flight races against it — `RuntimeError: Set changed size
during iteration` at best, undefined behavior at worst. This also matches real hardware semantics:
what's wired to the chip is fixed at power-on, not hot-pluggable mid-execution.

**Future hot-attach path, if ever needed:** not a dead end. The project already has an established,
tested bridge for exactly this class of problem — synchronous outside callers reaching safely into
engine-room state via `run_coroutine_threadsafe`/`call_soon_threadsafe`
(`simulator.py:88-116`, the same mechanism the CLI/tests/GDB connections already use).
A later hot-attach feature would marshal `attach()` onto the engine-room loop the same way, rather
than inventing new synchronization primitives. Out of scope for now.

**Confirmed (2026-08-11): not adding a `running` flag to `RP2040` now just to gate this check.**
Given `RP2040` has no such state today (see the "superstructure, not reconstruction" note above),
bolting one on purely to support a pre-run-only assertion would be the exact reconstruction this
whole design has been avoiding — for a check that's cheap to just document instead. Left as-is:
call `attach_external_devices()`/`attach()` before starting the simulator, documented, not
enforced.

**When hot-plug is actually wanted, don't just add the running-check gate — rethink attach/detach
to be safe regardless of run state.** The real fix isn't "block attach() while running," it's
making attach()/detach() genuinely safe to call at any time — most likely by routing them through
`schedule_threadsafe()` (see "Concurrency model" below) so they always execute on the engine-room
thread regardless of which thread calls them, the same way any other cross-thread engine-room
mutation already has to. That also removes the *need* for a running/not-running distinction at the
API level entirely: attach and detach both "just work," whether the simulator has started or not,
once they're marshaled onto the right thread. This is also the point where `detach_external_devices()`
design becomes genuinely useful beyond the virtual-serial-device case already noted in "Open
questions" — it lets tests inject a mid-run peripheral dropout/failure and observe how the emulated
controller (and firmware running on it) actually reacts, closer to real-world fault-injection
testing than anything possible with attach-only, pre-run-only wiring.

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

## Implementation order (revised 2026-08-11)

**Start here for a fresh implementation session.** Ordered so the general-purpose plumbing lands —
and gets proven on a low-stakes device — before building CYW43439 (the highest-complexity,
highest-risk piece) on top of it, rather than the other way around. Each step names the design
decision sections above that define what it actually means.

0. **Board-loading API — done (2026-08-12, rebuilt on `feat/board-loading-api` directly against
   the now-landed main-thread-asyncio architecture - see this doc's own header).** `boards.py`
   registry, `--board` CLI flag (`choices=["pico", "pico_w"]`), the `ExternalDevice` `Protocol`
   (`attach(rp2040)`), the standalone `attach_external_devices(mcu, *devices)` function, and
   `RP2040`/`Simulator.schedule_threadsafe()` from "Concurrency model" above — all built and
   tested (native + pure-Python), with no CYW43439-specific code yet. Also picked up a small CLI
   addition in the same pass, orthogonal to CYW43 itself: `--fetch-fw-only` on
   `run`/`micropython`/`kaluma`/`bench` downloads/caches firmware (and `--bootrom`, if a version
   tag) and exits without starting the simulator - for pre-warming the local cache. See "Board
   composition decision" and "Concurrency model" above for the full shape of step 0.
1. **Prove the `ExternalDevice` pattern on something already understood — done (2026-08-12), via
   `LEDMock` rather than the originally-planned eink retrofit.** `external/led_mock.py`'s
   `LEDMock(gpio=25)` - a minimal `ExternalDevice` that watches one GPIO pin via
   `GPIOPin.add_listener()` and tracks on/off state + toggle count - attached to *both* `pico` and
   `pico_w` in `boards.py`. Validates `attach()`/`attach_external_devices()`/`build_rp2040()` end
   to end with real (non-demo) tests (`tests/test_led_mock.py`, `tests/test_boards.py`) without
   depending on the still-unmerged `component/epd2in9g` branch at all - the eink retrofit described
   below is still worth doing eventually (it's a materially bigger/more realistic validation, real
   SPI framing instead of one GPIO), but wasn't necessary to unblock step 2. **Not hardware-accurate
   for `pico_w`** - see `led_mock.py`'s own docstring: a real Pico W's LED is wired to the CYW43439
   chip itself, not any RP2040 GPIO, so this is a placeholder there until `Cyw43439` (step 3) grows
   real LED handling.

   Eink retrofit (still deferred, not blocking, kept for whenever `component/epd2in9g` lands): the
   natural candidate is retrofitting `demo/components/virtual_eink.py`'s existing ad hoc
   `GPIOPin.add_listener()`/`RPSPI.on_transmit` wiring into the `ExternalDevice` interface. Not
   done here because that demo doesn't exist on this working branch: it lives on a separate,
   unmerged branch (`component/epd2in9g`, commit `a6d6f34` - `demo/components/virtual_eink.py`/
   `mp_eink_demo.py`, `demo/eink_run.py`), itself a few commits behind `main`. Confirmed with the
   user (2026-08-11): that branch is being kept as a plain reference for now, not pulled into this
   branch early - it gets migrated onto the `ExternalDevice` architecture as its own effort once
   `component/epd2in9g` is brought up to date and merged, not before.
2. **Bus level — next actionable step.** A generic bit-banged half-duplex "gSPI slave" watcher hooked via plain
   `GPIOPin.add_listener()`/`set_input_value()` on GPIO24/25/29 - the architecture confirmed by the
   Wokwi investigation, the bit-level details (command word layout, function numbers, F0 register
   map) from the "Authoritative protocol reference" section above, sourced directly from the local
   `pico-sdk`/`cyw43-driver` checkout. Decode `make_cmd()`'s header on the first word after select,
   then stream `BUS_FUNCTION` (F0) register reads/writes far enough for the driver's init handshake
   (`SPI_READ_TEST_REGISTER`, `SPI_STATUS_REGISTER` polling, etc.) to succeed. Synchronous, in-line
   — pure decode logic, no I/O, no need for `schedule_threadsafe()`. Lives in `cyw43/bus.py` (see
   "Module layout decision" above).
3. **Chip/backplane + SDPCM/WLC ioctl layer.** A `Cyw43439` model class: F0 bus registers, F1
   windowed backplane addressing (`SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH`), and enough of
   `handleControl()`-equivalent `WLC_*` ioctl decoding + `WLC_E_*` event delivery (exact IDs above -
   `WLC_UP`, `WLC_SET_SSID`, `WLC_SET_INFRA`, `WLC_SET_AUTH`, etc.) for
   `network.WLAN().scan()`/`.connect()` to work against real, unmodified `cyw43_driver` code, with a
   fixed fake AP (mirroring `Wokwi-GUEST`'s shape) and `.status()` progressing through the same
   codes real firmware reports. Read `cyw43_ll.c`'s ioctl dispatch and event-sending functions
   directly when implementing this - it's long (2000+ lines) but is the authoritative spec for
   exactly what each ioctl/event needs to contain. Also synchronous, in-line — still pure state
   machine, no real I/O yet. Lives in `cyw43/chip.py`; this is where `Cyw43439` implements
   `ExternalDevice.attach()`.
4. **Real network bridge.** Once firmware believes it's associated and starts moving IP packets,
   the userspace NAT/SLIRP layer described earlier — real `socket.getaddrinfo`/TCP/UDP via the host,
   DNS included — so `urequests`-style code in emulated firmware reaches the real internet. Needs
   the SDPCM data-frame envelope format (how an actual Ethernet/IP frame is wrapped for the F2/WLAN
   data path) - not yet extracted from `cyw43_ll.c`, see "Open questions" below. **This is where
   `schedule_threadsafe()` actually gets used** — real socket I/O is the first genuinely slow/
   blocking work in the whole plan. Lives in `cyw43/nat.py`.

   *Not* the actual `libslirp` C library QEMU embeds — a SLIRP-**style** userspace NAT written in
   plain Python: ordinary unprivileged `socket.connect()`/`send()`/`recv()`/`getaddrinfo()` calls
   made by the `rp2040py` process itself, no TUN/TAP interface, no raw sockets, no root. Because it
   never drops below the ordinary client-socket API, it's expected to work identically on Linux,
   macOS, and Windows — unlike QEMU's `tap` netdev mode, which needs `CAP_NET_ADMIN`/root on Linux,
   `vmnet.framework` on macOS, or an installed TAP driver on Windows. Like real SLIRP/QEMU user-mode
   networking, inbound connections don't pass through without an explicit forwarded port (see the
   WebREPL note in "Open questions" below).

## Open questions for next session

Organized by kind, so a fresh session can tell "needs a decision" from "just needs source-reading"
from "closed, kept for the record."

### Historical / closed — no action needed

- Where `wifi.medium`/the fake-AP/NAT bridge actually lives in Wokwi's bundle — not located, and no
  longer worth chasing: our own network-bridge plan (step 4 above: direct host `socket` calls, no
  fake 802.11 layer needed) diverges from their approach here anyway.
- **WebREPL is not an automatic side effect of step 4 (the network bridge).** WebREPL is an
  *inbound* connection (something outside connects *to* the device on port 8266); step 4 is an
  *outbound* SLIRP-style bridge (the device itself reaches out to the internet, like QEMU's default
  `-netdev user` mode). SLIRP does not pass inbound connections through without an explicit
  port-forward rule (QEMU's equivalent is `hostfwd=tcp::8266-:8266`). So WebREPL support is a
  separate, additional feature layered on top of step 4 — it would need an explicit forwarded
  port from the host into the NAT. A natural follow-on, but not something step 4 delivers for
  free on its own.
- Explicitly **out of scope for the `firmware_retrieve.py` work below: the `"bootrom"` entry.** The
  bootrom is a mask ROM baked into the RP2040 die itself at manufacturing time, identical across
  every board that chip ends up on — versioned only by silicon stepping (B0/B1/B2, already handled
  per issue #11/`docs/BACKLOG.md`), never by board. It has no awareness of externally-wired
  hardware like CYW43439 at all (that's purely a MicroPython/CircuitPython + `cyw43-driver`
  concern, running long after the bootrom has handed off). Only the
  `micropython`/`circuitpython`/`kaluma` entries in `firmware_specs.json` need board-variant
  resolution.

### Deferred, not designed — real future work

- **`IClock`'s protocol could eventually move to `external/`, mirroring `ExternalDevice` — not
  started, no code changed for this yet.** Raised 2026-08-11: from `RP2040`'s point of view,
  `IClock` (which alarm/timing source drives it) is arguably the same kind of externally-injectable
  dependency `ExternalDevice` formalizes for board-level hardware - both are interfaces `RP2040`
  is built against without owning a specific implementation of. If this is ever done, the split
  should follow the same pattern already established for `ExternalDevice`/`Cyw43439`: the
  **protocol** (`IClock`/`IAlarm`/`AlarmCallback`, today in `src/rp2040py/clock/clock.py`) moves to
  `src/rp2040py/external/clock.py`, while **concrete implementations** (`SimulationClock`,
  `MockClock`) stay in `src/rp2040py/clock/` - the same way a future `Cyw43439` (a concrete
  `ExternalDevice`) lives in `cyw43/`, not in `external/` itself. Not attempted yet: `IClock` is
  injected as a plain constructor argument to `RP2040.__init__()` (needed before peripheral
  construction, since several peripherals call `self.clock.create_alarm(...)` from their own
  `__init__`), unlike `ExternalDevice.attach()`, which runs *after* `RP2040` exists - so this isn't
  a pure rename, it would touch every one of `IClock`/`IAlarm`'s ~8 current import sites
  (`_rp2040.py`, `native/_rp2040.pyx`, `clock/simulation_clock.py`, `peripherals/pwm.py`,
  `peripherals/timer.py`, `peripherals/usb.py`, `utils/timer32.py`, plus this repo's own
  `boards.py`) for a naming/location change with no behavioral difference. Worth doing for
  consistency if/when someone's actively working in this area anyway; not worth a standalone PR on
  its own.

- **`ExternalDevice.detach()` / a standalone `detach_external_devices()` counterpart — not designed
  yet, may eventually be needed** as the symmetric counterpart to `attach()`/
  `attach_external_devices()`. Two concrete motivating cases, not just testing nicety:
  - **Virtual serial external devices** — something wired onto a UART/pin pair (a virtual modem,
    sensor, or other serial peripheral in the same spirit as `demo/components/virtual_eink.py`)
    that a user plugs in and back out over the course of one session, mirroring how a real serial
    device gets connected/disconnected on the fly.
  - **Fault injection** — once hot-plug is safe (see "rethink attach/detach to be safe regardless
    of run state" above), tests could inject a mid-run peripheral dropout/failure and observe how
    the emulated controller and firmware actually react.

  Both need `detach()` *and* the pre-run-only constraint lifted for hot-swap — see the dedicated
  subsection under "Board composition decision" above for why that's not just "add a running
  check" but a rethink using `schedule_threadsafe()`. Worth noting for whoever designs it:
  `GPIOPin.add_listener()` already returns an unsubscribe callable (`gpio_pin.py:315`,
  `lambda: self._listeners.discard(callback)`) — an `attach()` implementation should hang onto
  whatever `add_listener()` gives back for each pin it hooks, so a future `detach()` has something
  to call. Not needed for step 2 (bus level); flagging so `attach()` implementations don't throw
  that return value away.

- **`cli/firmware_retrieve.py` needs to grow board-awareness eventually — not now.** Its
  `firmware_specs.json` is currently hard-coded to plain-Pico assets:
  `"micropython": "RPI_PICO-{version}.uf2"`, `"circuitpython":
  "adafruit-circuitpython-raspberry_pi_pico-en_US-{version}.uf2"`. Real MicroPython/CircuitPython
  ship separate Pico-W-specific builds under different filenames
  (`RPI_PICO_W-{version}.uf2`, `..._pico_w-en_US-...`) — the ones that actually have the
  `cyw43-driver`/network stack compiled in. Once `--board pico_w` exists, firmware retrieval needs
  to resolve to the right variant per board, not just per firmware family/version.

  **Candidate redesign (2026-08-11), largely decided, two sub-items still open.** Current
  `retrieve()` resolves `image` two ways: a local file path (`Path(image).exists()`,
  `firmware_retrieve.py:114-115`), or a version tag run through `_resolve_version()` and then
  `spec.filename_template.format(...)` / `spec.url_template.format(...)` (`:117-119`) — template
  substitution against a `known_versions: dict[tag, filename-version]` map. Proposed instead:

  - **Three resolution paths, not two:** local file path (unchanged) — version tag (resolution
    kept, format simplified below) — and a direct HTTP/HTTPS URL passed straight through `--image`
    (new), which downloads from exactly that URL, caches it, and gets reused from cache on
    subsequent runs the same way a resolved tag already does.
  - **Drop `filename_template`/`url_template` entirely, and drop runtime URL generation
    entirely too.** Replace `known_versions: dict[tag, filename-version]` with a flat
    `known_versions: dict[tag, url]` — tag straight to a full download URL, no template
    substitution step in the resolution algorithm at all. Crucially, this map isn't generated at
    request time by `retrieve()` — it's **fetched at development time** (via the fetch script
    below) and committed straight into `firmware_specs.json`, so the shipped index always has
    real, verified tags and URLs for every firmware release as of whenever it was last refreshed —
    no filename-guessing or template drift at runtime, just a lookup.
  - **Per-board resolution reuses the same lookup, keyed by board too — but only for the tag
    path.** Passing a tag (e.g. `v1.28`) plus the selected `--board` (default: `pico`, once
    `--board` exists at all) resolves to *that* board's URL for MicroPython 1.28.

    **Nesting order: `dict[board][tag] -> url`, board outer, decided.** Matches how firmware is
    actually published for MicroPython and CircuitPython — you pick the board first, then see the
    versions available for it, then download. Kaluma is a partial mismatch (GitHub releases, no
    clean per-board split at the release level — firmware for different boards mixed together) but
    a minor enough one to still nest it the same way for consistency rather than special-case the
    index shape per firmware family. Board-outer also gives a clean two-step validation: check the
    board key exists first (a clear "unknown board" error if not) before even looking at the tag,
    then check the tag exists within that board's map (a clear "unknown version for this board"
    error if not) — each failure mode gets its own unambiguous error instead of one lookup that
    can fail for either reason.

    **Board only ever affects the *tag* path.** If `image` is instead a local file path or a
    direct HTTP/HTTPS URL, that file/URL is used exactly as given — no board-based resolution
    happens at all, regardless of what `--board` is set to. `--board pico_w --image ./custom.uf2`
    (or any URL) just runs `custom.uf2`; `--board` isn't consulted for firmware selection in that
    case. Board-gated resolution is purely a tag-path concern; file path and raw URL are both
    already-resolved, unconditional answers to "what to run."
  - **There's already a working prototype for *building* one of these maps:** the
    `tool/fetch-mp-firmware` branch's `scripts/fetch_mp_firmware_list.py` scrapes
    `https://micropython.org/download/{board}/` per board prefix (`RPI_PICO`, `RPI_PICO_W` are
    already separate lookups there) into a tag→filename map — this is also, incidentally, the
    concrete mechanism that would satisfy the board-awareness need above, since the per-board split
    already exists in that scraper. Remaining work: extend it to emit full URLs (not bare
    filenames) and write the equivalent scraper for CircuitPython and Kaluma. Presumably re-run
    periodically (manually, or a scheduled job) to keep the committed index current as new
    releases land — **open sub-item: exact refresh workflow not decided.**
  - **Open sub-item: how to name the cache entry for a raw URL passed via `--image`.** Today's tag
    path caches under the resolved filename (`_cache_dir() / filename`, `firmware_retrieve.py:120`)
    — there is no filename for an arbitrary URL beyond whatever its path happens to contain, which
    isn't guaranteed unique or even present (query-string-only URLs, redirects, etc.). One
    reasonable option worth considering: key the cache entry off a hash of the URL string itself
    (not its content — hashing content would require downloading first, defeating the point of
    checking the cache before fetching), e.g. `sha256(url).hexdigest()[:16]`, optionally suffixed
    with the URL's basename if it has one for human-readability while the hash guarantees
    uniqueness/correctness. Not committed to; flagged for a follow-up decision.

### Research homework — not decisions, just needs source-reading during implementation

- Full backplane *core* address map (chip-common registers, ARM core reset/halt registers, SOCSRAM
  addresses, etc.) needed for a real chip bring-up sequence, and the SDPCM data-frame envelope byte
  layout for the actual WLAN RX/TX data path (steps 3/4 concern, not step 2) — not yet dug out of
  `cyw43_ll.c` (2000+ lines); read `cyw43_ll.c`'s `cyw43_ll_bus_init()`/`cyw43_ll_wifi_pm()`-area
  functions and the `SDIO_*`/`SBSDIO_*` constants near the top of the file when step 3 starts.
  - maybe document way to use gpiozero as global external device for emulations on the RP
