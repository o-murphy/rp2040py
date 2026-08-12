# CYW43439 / Pico W WiFi emulation — research notes and implementation plan

**Current step (2026-08-12): 3g — async events + scripted scan/join.** Everything before it in
"Implementation order" below is done: step 0 (board-loading API), step 1 (`ExternalDevice` proven
via `LEDMock`), step 2 (F0 bus-level `GSPIBus` decode - real firmware boots past the F0 handshake),
and step 3's sub-steps 3a (generic word-aligned block transfers -
`GSPIBus._on_clock_rising()`/`_start_response()` handle any `size`, not just one 32-bit word, via
`_word_count()`/`_words_to_value()`/`_value_to_words()`), 3b (ALP/HT/KSO clock handshake), 3c (F1
windowed backplane addressing), 3d (ARM core reset/enable registers), 3e (firmware/CLM download
acceptance - free via 3a/3c's generic F1 block writes - plus F2 packet delivery:
`GSPIBus.queue_rx_packet()`, `SPI_STATUS_REGISTER`/`SPI_INTERRUPT_REGISTER` plumbing, and the
shared `WL_D` IRQ pin reflecting real pending-packet state instead of a hardcoded LOW placeholder),
and 3f (SDPCM framing + generic ioctl request/response: `GSPIBus._write_wlan()` parses inbound F2
ioctl requests and answers with a generic zero-length success response via
`_build_ioctl_success_response()`, echoing the request id and tracking `bus_data_credit` so the
driver's own flow-control never stalls - corrected the SDPCM header size from an originally
estimated 14 bytes to the real 12 along the way) - all unit-tested in `tests/test_cyw43_bus.py` (29
tests). Real per-ioctl content and events (`WLC_SET_SSID`/join's scripted `WLC_E_*` sequence, scan
results) still aren't built - that's 3g, next. See "Implementation
order"'s step 3 for the full sub-step breakdown and status detail.

[docs/MAIN_THREAD_ASYNCIO_BACKLOG.md](MAIN_THREAD_ASYNCIO_BACKLOG.md) (all 5 phases, engine-room
concurrency model) landed first and unblocked this whole effort - `Simulator`'s engine-room loop
now runs on whichever loop the caller itself owns, via `bind_loop()`, matching upstream rp2040js's
single-threaded model for the common single-instance case; a dedicated background thread is now
the exception, not the default. Step 0 below (board-loading API - `ExternalDevice`, `boards.py`,
`schedule_threadsafe()`) was originally built and merged against the *old* background-thread
engine room on a since-abandoned branch, then rebuilt from scratch directly on top of the new
architecture (`feat/board-loading-api`, branched from `refactor/main-thread-asyncio`) rather than
ported as a literal patch.

Goal (per discussion 2026-08-11): real internet access from emulated firmware, SLIRP/NAT-style (see
"Implementation order" below), not just a canned "connected" stub.

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

## Real bringup sequence beyond F0 — read from source (2026-08-12)

**Supersedes step 3's original one-paragraph estimate in "Implementation order" below.** That
estimate ("F0 bus registers, F1 windowed backplane addressing, enough `WLC_*`/`WLC_E_*` handling
... also synchronous, in-line — still pure state machine, no real I/O yet") undersold this
significantly - read the rest of `cyw43_ll.c` (2435 lines total, not just the ~100-line
`cyw43_ll_bus_init()` excerpt step 2 needed) end to end before writing any step-3 code. What
follows is what real, unmodified firmware actually does between "F0 handshake done" (step 2) and
"`network.WLAN().scan()`/`.connect()` works" - each subsection names the step (below, in the
revised "Implementation order") it belongs to.

### Structural gap step 2 didn't anticipate: block transfers

**Every real gSPI transaction from here on is multi-byte, not the single 4-byte register
reads/writes step 2's `GSPIBus` handles.** `make_cmd()`'s own `size` field is 11 bits (up to 2047
bytes) for exactly this reason - firmware download writes chunks up to `CYW43_BUS_MAX_BLOCK_SIZE`,
SDPCM/ioctl frames are tens to low hundreds of bytes, and F2 packet reads use whatever
`SPI_STATUS_REGISTER`'s pending-length field reports. `GSPIBus._on_clock_rising()`/
`_on_clock_falling()` today only ever drive/sample exactly one 32-bit word per read/write - this
needs generalizing to arbitrary byte counts (still MSB-first per word, still word-aligned per the
real driver's own `assert(!(len & 3))` checks) before any of the sub-steps below can do anything
beyond single-register pokes. This is genuinely step 2's own scope creeping forward, not step 3 -
listed as step 3a below for exactly that reason.

### ALP/HT clock handshake + KSO (`BACKPLANE_FUNCTION`, `SDIO_CHIP_CLOCK_CSR`/`SDIO_SLEEP_CSR`)

Every `cyw43_ll_bus_sleep(false)` call (which `cyw43_sdpcm_send_common()` - i.e. *every* SDPCM
send - calls first) goes through `cyw43_kso_set()` (`USE_KSO` is unconditionally `1`, not an
SDIO-only path): writes `SBSDIO_SLPCSR_KEEP_SDIO_ON` to `SDIO_SLEEP_CSR` (`0x1001f`), then polls
reading it back (up to 64 attempts, 1ms apart) until both `KEEP_SDIO_ON`/`DEVICE_ON` bits read back
set. Separately, `cyw43_ll_bus_sleep_helper()`'s own HT-clock request writes
`SBSDIO_HT_AVAIL_REQ` to `SDIO_CHIP_CLOCK_CSR` (`0x1000e`) and polls (up to 1000×, 1ms apart) for
`SBSDIO_HT_AVAIL` to read back set; the earlier ALP handshake in `cyw43_ll_bus_init()` (already
partially read for step 2) does the same dance with `SBSDIO_ALP_AVAIL_REQ`/`SBSDIO_ALP_AVAIL`. No
real clock startup latency needs modeling - a register model where writing the `*_REQ` bit makes
the corresponding `*_AVAIL` bit immediately readable satisfies every poll loop above trivially and
correctly (this project has no reason to make these loops spin for real).

### Backplane (F1) windowed addressing (`cyw43_set_backplane_window()`/`cyw43_read_backplane()`/`cyw43_write_backplane()`)

The F1 address field in `make_cmd()` is 17 bits; backplane addresses are 32-bit, so the driver
maintains a movable "window": `SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH` (`0x1000a`/`0x1000b`/`0x1000c`,
each 1 byte, written only when that byte of the target address actually changed vs.
`self->cur_backplane_window` - a driver-side optimization, not something the chip model needs to
replicate) select the upper bits; the low 15 bits (`BACKPLANE_ADDR_MASK = 0x7fff`) go directly in
the F1 read/write's own address field, OR'd with `SBSDIO_SB_ACCESS_2_4B_FLAG` (`0x08000`) - always
set for SPI (`CYW43_USE_SPI`), regardless of transfer size. A plain byte-addressable "backplane
memory" model (a big enough `bytearray`, or a sparse dict if a full 4GB space is wasteful) with
generic read/write dispatch - the same shape as `GSPIBus`'s own F0 register space (step 2) - covers
this; the *meaning* of specific backplane addresses (core control registers, SOCSRAM, ...) is
layered on top, not part of the windowing mechanism itself.

### ARM core reset/enable (`disable_device_core()`/`reset_device_core()`/`device_core_is_up()`)

Real bringup resets then re-enables two of the chip's own internal cores - `CORE_WLAN_ARM`
(`WLAN_ARMCM3_BASE_ADDRESS = 0x18003000`) and `CORE_SOCRAM` (`SOCSRAM_BASE_ADDRESS = 0x18004000`),
each with a `WRAPPER_REGISTER_OFFSET = 0x100000` added, then `AI_IOCTRL_OFFSET = 0x408`/
`AI_RESETCTRL_OFFSET = 0x800` added on top for the two registers actually touched
(`SICF_FGC|SICF_CLOCK_EN|SICF_CPUHALT` bits at `AI_IOCTRL_OFFSET`, `AIRC_RESET` bit at
`AI_RESETCTRL_OFFSET`) - all via the backplane windowed access above. **This does not require
emulating a second CPU core running real ARM code** - `rp2040py` only needs these specific backplane
registers to reflect "reset" then "clocked and out of reset" when read back, matching
`device_core_is_up()`'s own check (`AI_IOCTRL_OFFSET & (SICF_FGC|SICF_CLOCK_EN) == SICF_CLOCK_EN`
and `AI_RESETCTRL_OFFSET & AIRC_RESET == 0`) - the WLAN core's own firmware execution is opaque to
the host driver already (it only ever talks to it through SDPCM), so nothing downstream can tell
the difference between "real ARM core executing real firmware" and "a register model that just
remembers it was told to start."

### Firmware + CLM blob download (`cyw43_download_resource()`/`cyw43_clm_load()`)

The driver writes the actual compiled WiFi-chip firmware image (`wifi_firmware.S`/`.ld`,
hundreds of KB, embedded in the host binary) plus a separate CLM (Country Locale Matrix,
regulatory config) blob into backplane/SOCSRAM memory via chunked `cyw43_write_bytes()` calls.
**`cyw43_check_valid_chipset_firmware()` (checks for a `"Version: "` string near the blob's own
end) runs entirely on the driver/host side, against the driver's own in-memory copy of the
firmware, before any of it is written to the bus** - the chip model never sees this check and
doesn't need to satisfy it; it only ever needs to accept the resulting write transactions (ignore
the actual bytes, or store them somewhere inert - there's no second CPU to execute them against,
per the previous subsection). **Real, separate concern this raises: performance.** `GSPIBus`
today is driven by genuine per-bit `GPIOPin.add_listener()` callbacks - hundreds of KB of firmware
downloaded this way is potentially millions of individual Python callback invocations during boot.
Worth profiling once step 3a (block transfers) lands, before assuming real-firmware boot is
practically fast enough to use interactively; a batched/short-circuited write path for large
block transfers (still correct from the driver's point of view - it never inspects the response to
a pure write) may be needed regardless of the bit-level protocol fidelity kept for reads/small
transfers.

### SDPCM framing + ioctl request/response (`cyw43_sdpcm_send_common()`/`cyw43_send_ioctl()`/`sdpcm_process_rx_packet()`)

Once the chip is "up," every control/data exchange rides one common 14-byte SDPCM header
(`size`/`size_com` - a redundant `~size` checksum, not compression - `sequence`, `channel_and_flags`
selecting `CONTROL_HEADER=0`/`DATA_HEADER=2`/`ASYNCEVENT_HEADER=1`, `header_length`,
`wireless_flow_control`, `bus_data_credit`, 2 reserved bytes) written via
`cyw43_write_bytes(WLAN_FUNCTION, 0, ...)` - i.e. as an ordinary F2 block write, using the
block-transfer machinery from step 3a. An ioctl request adds its own 16-byte header on top (`cmd`,
`len` - packed input/output lengths - `flags` - includes a per-request ID the response must echo
back exactly, `CDCF_IOC_ID_MASK`/`_SHIFT` - `status`) plus the payload. `sdpcm_process_rx_packet()`
(response side) validates the `size`/`~size_com` pair, tracks `bus_data_credit` (a simple flow-
control counter - the model doesn't need real backpressure, just something that increments
consistently so the driver's own stall-detection loop in `cyw43_sdpcm_send_common()` never
triggers), and for `CONTROL_HEADER` responses checks the echoed ID matches the last request sent
before treating the payload as that ioctl's actual response. A minimal chip model can satisfy the
*bulk* of real firmware's own `WLC_*` ioctl vocabulary (see the ID list in the "Authoritative
protocol reference" section above, now confirmed larger than first listed -
`WLC_GET_BSSID`/`WLC_GET_SSID`/`WLC_SET_CHANNEL`/`WLC_GET_ANTDIV`/`WLC_SET_DTIMPRD`/`WLC_GET_PM`/
`WLC_GET_ASSOCLIST`/`WLC_SET_WSEC_PMK` all appear in `cyw43_ll.c` too) with one generic
"echo a zero-length success response" handler, reserving real per-ioctl behavior for the small
subset that actually needs it (`WLC_SET_SSID`/`WLC_SET_VAR "escan"`/join-related ones - see below).

### F2 "packet available" polling (`cyw43_ll_sdpcm_poll_device()`, SPI variant)

The driver has no separate interrupt-line wiring on Pico W beyond the shared `WL_D` pin's own
IRQ-when-CS-deasserted behavior (already noted in "Hardware: how RP2040 talks to the CYW43439"
above - confirmed here as `CYW43_PIN_WL_HOST_WAKE == WL_DATA_IN`, the exact same GPIO24). Polling
for an inbound packet reads `SPI_INTERRUPT_REGISTER` (`F2_PACKET_AVAILABLE` bit) then
`SPI_STATUS_REGISTER` (`GSPI_PACKET_AVAILABLE` bit + pending-byte-count in bits 19:9,
`STATUS_F2_PKT_LEN_MASK`/`_SHIFT`), then reads exactly that many bytes via
`cyw43_read_bytes(WLAN_FUNCTION, 0, bytes_pending, ...)`. So delivering an ioctl response or async
event to the driver, from the chip model's side, means: stage the encoded SDPCM+payload bytes,
set `SPI_STATUS_REGISTER`'s pending-length field and `GSPI_PACKET_AVAILABLE`, set
`SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE`, then hand those exact bytes back on the next F2
read of that length.

### Async event delivery (`cyw43_ll_parse_async_event()`, `cyw43_async_event_t`)

An async event (channel `ASYNCEVENT_HEADER`) is delivered as a fake inbound Ethernet frame: the
BDC-header-stripped payload must have EtherType `0x886c` at the conventional offset and the
Broadcom OUI (`00:10:18`) right after it - `sdpcm_process_rx_packet()` rejects anything else before
the event struct is even looked at. The actual `cyw43_async_event_t` (`cyw43_ll.h`) starts 24 bytes
past that OUI check: 2 reserved bytes, `flags` (u16, big-endian on the wire - `cyw43_be16toh()`),
`event_type`/`status`/`reason` (u32 each, big-endian), 30 reserved bytes, `interface`, 1 reserved
byte, then a union whose only current member is `cyw43_ev_scan_result_t` (bssid/ssid/channel/
auth_mode/rssi, with several `uint32_t`/`uint16_t` reserved gaps matching real firmware's own
struct padding - field offsets matter here, this can't be a natural Python dataclass layout without
explicit packing). `WLC_E_*` event-type IDs are already listed in "Authoritative protocol
reference" above - confirmed complete against this header (`CYW43_EV_SET_SSID=0`, `_JOIN=1`,
`_AUTH=3`, `_DEAUTH=5`, `_DEAUTH_IND=6`, `_ASSOC=7`, `_DISASSOC=11`, `_DISASSOC_IND=12`, `_LINK=16`,
`_PRUNE=23`, `_PSK_SUP=46`, `_ICV_ERROR=49`, `_ESCAN_RESULT=69`, `_CSA_COMPLETE_IND=80`,
`_ASSOC_REQ_IE=87`, `_ASSOC_RESP_IE=88`) alongside `CYW43_STATUS_*` codes events carry
(`SUCCESS=0`, `FAIL=1`, `TIMEOUT=2`, `NO_NETWORKS=3`, `ABORT=4`, `NO_ACK=5`, `UNSOLICITED=6`,
`ATTEMPT=7`, `PARTIAL=8` - scan results specifically arrive with this status - `NEWSCAN=9`,
`NEWASSOC=10`).

### Scan (`cyw43_ll_wifi_scan()`) and join (`cyw43_ll_wifi_join()`)

`scan()` is just one `WLC_SET_VAR "escan"` iovar write (a fixed-shape options struct - version/
action/bssid/bss_type/probe+active+passive+home timing/channel list, all driver-chosen constants,
nothing the chip model needs to branch on) - the *response* is what matters: one or more
`ASYNCEVENT_HEADER` deliveries with `event_type=CYW43_EV_ESCAN_RESULT`, `status=CYW43_STATUS_PARTIAL`
per result found, each carrying a populated `cyw43_ev_scan_result_t` (a fixed fake AP, mirroring
`Wokwi-GUEST`'s shape per the original step-3 plan, is exactly this - one canned event). `join()`
is a materially longer sequence than the doc's original step-3 estimate implied: `ampdu_ba_wsize`
iovar, `WLC_SET_WSEC`, three separate `bsscfg:sup_*` iovars, optionally `WLC_SET_WSEC_PMK` (WPA
PSK) or a `sae_password` iovar (WPA3), `WLC_SET_INFRA`, `WLC_SET_AUTH`, an `mfp` iovar,
`WLC_SET_WPA_AUTH`, an event-mask iovar, *then* (not yet read in full - see "Research homework"
below) the actual `WLC_SET_SSID` that triggers the join and the scripted `WLC_E_*` sequence
(`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`/...) real firmware fires in response, which is what makes
`network.WLAN().status()` progress through the same codes real hardware would. Every ioctl/iovar
in this sequence *before* that final SSID write can use the generic "echo success" handler from
the SDPCM subsection above without inspecting its payload at all - only the SSID-triggered join
itself needs real scripted behavior.

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

**New subpackage `src/rp2040py/external/cyw43/`** (relocated 2026-08-12 from a first-draft
top-level `src/rp2040py/cyw43/` - `Cyw43439` is a concrete `ExternalDevice` implementation, same
reasoning `external/led_mock.py` already follows, just big enough - bus/chip/nat - to want its own
subpackage under `external/` instead of a single sibling file), following the same
"real package with an `__init__.py`, not a single file" pattern as `clock/`, `gdb/`, `usb/`,
`utils/`:

- `external/cyw43/bus.py` — bit-bang gSPI decode (step 2 in "Implementation order" below, GPIO-listener
  level: `make_cmd()` header parsing, F0 bus register block).
- `external/cyw43/chip.py` — the chip model itself: F0/F1 registers, backplane windowed addressing,
  `WLC_*`/`WLC_E_*` ioctl and event handling (step 3). This is also where the `Cyw43439` class
  that implements `ExternalDevice` (see "Board composition decision" next) lives.
- `external/cyw43/nat.py` — the SLIRP-style userspace NAT bridge (step 4).

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

  `Cyw43439` (`external/cyw43/chip.py`) implements this structurally, same as peripherals implement
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
2. **Bus level — done (2026-08-12), unit-tested; real-firmware boot not yet attempted.**
   `external/cyw43/bus.py`'s `GSPIBus`: a generic bit-banged half-duplex gSPI watcher hooked via plain
   `GPIOPin.add_listener()`/`set_input_value()` on GPIO24/25/29, decoding `make_cmd()`'s header on
   the first 32-bit word after select and dispatching `BUS_FUNCTION` (F0) register reads/writes -
   `SPI_READ_TEST_REGISTER` fixed at `TEST_PATTERN=0xFEEDBEAD`, everything else a plain
   byte-addressable register file. Synchronous, in-line - pure decode logic, no I/O, no
   `schedule_threadsafe()` needed, as planned.

   **Exact wire timing derived from the real source, not guessed**: read
   `cyw43_bus_pio_spi.c`/`.pio` directly (both checked out locally - see "Authoritative protocol
   reference" above for the paths) rather than relying on the Wokwi-investigation architecture
   alone. Confirmed from the PIO program (`spi_gap01_sample0`, the driver's own default): every
   real bit action (drive a TX bit, sample an RX bit) happens while `WL_CLK` is low, immediately
   before it's raised - so this decodes as sample-on-rising-edge (host drove the bit during the
   preceding low phase), drive-on-falling-edge (chip's own turn, so it's stable in time for the
   host's next low-phase sample). `bus.py`'s own module docstring has the full derivation.

   **Correction (2026-08-12), found by booting real firmware, not by reading source closer:**
   the original claim here - that the C driver's `SWAP32()`/DMA-`bswap` calls cancel out, so the
   wire always carries the natural `make_cmd()`/register value - is **wrong** for the two
   `_swap`-suffixed accessors (`read_reg_u32_swap()`/`write_reg_u32_swap()`) that
   `cyw43_ll_bus_init()` uses for the `SPI_READ_TEST_REGISTER` poll and the one `SPI_BUS_CONTROL`
   write that switches modes. `SWAP32()` there is actually `__swap16x2()`/`REV16` (swaps bytes
   *within* each 16-bit half, not a full 32-bit reversal) - composed with the DMA engine's own
   *full* 32-bit byte-swap (applied unconditionally to every gSPI DMA transfer, regardless of
   caller), the net wire transform for these two calls is "swap the two 16-bit halves of the word,
   bytes in-order within each half" - confirmed by capturing a real word off a live Pico W boot:
   the first test-register-poll header arrived as `0xa0044000`, exactly `0x4000a004` (the real
   `make_cmd()` value) with its halves swapped, not the identity. Every *other* accessor (used
   after that one `SPI_BUS_CONTROL` write) skips the C-level swap and relies on the DMA engine's
   full byte-swap plus the chip's own `ENDIAN_BIG` config (set by that same write) to net out to a
   *different* transform: a full 32-bit byte reversal instead. This is real, stateful gSPI hardware
   behavior gated by `SPI_BUS_CONTROL`'s `WORD_LENGTH_32` bit (0 = 16-bit word length, the chip's
   own power-on default; 1 = 32-bit) - not an implementation detail to paper over. `bus.py` now
   models it directly: `GSPIBus._word_length_32` starts `False`, `_word()` applies the matching
   self-inverse transform to every decoded/encoded 32-bit word, and `_write_f0()` flips the flag
   the instant a `SPI_BUS_CONTROL` write's value has `WORD_LENGTH_32` set - see `bus.py`'s module
   docstring for the full derivation and `_swap_halves()`/`_swap_bytes()` for the two transforms.
   `tests/test_cyw43_bus.py`'s `_FakeGSPIMaster` mirrors the same mode-tracking so its round-trip
   tests stay meaningful (both sides start in sync and flip in lockstep on the same rule, so
   correctness holds regardless of which mode is active at any given moment in a test).

   **Verified end-to-end against a synthetic gSPI master** (`tests/test_cyw43_bus.py`'s
   `_FakeGSPIMaster`, bit-banging GPIO24/25/29 via the same SIO-write pattern
   `tests/test_led_mock.py` established) - 13 tests (5 from step 2, 8 added for step 3b/3c/3d's
   ALP/HT/KSO/backplane-window/core-reset-default coverage), all passing.

   **Booted real, unmodified MicroPython Pico W firmware against this (2026-08-12) - the
   `[CYW43] Failed to start CYW43` warning the user's own live test first surfaced (before any of
   step 3's work, and before `Cyw43439`/`bus.py` were even wired into `boards.py`'s `pico_w`
   extras) is now gone entirely** on both `v1.28.0` and `v1.21.0`, confirming `cyw43_ll_bus_init()`
   (the F0-only handshake: test-register poll, the `SPI_BUS_CONTROL` word-length switch, and the
   interrupt-register writes) completes successfully end-to-end for the first time. This needed
   only the wire word-length/endian fix above, not step 3b/3c/3d's F1 work directly -
   `cyw43_ll_bus_init()` itself never touches `BACKPLANE_FUNCTION`. Not yet confirmed live: whether
   `cyw43_ll_wifi_on()` (the *later* call that actually exercises 3b/3c/3d's ALP/HT/KSO/backplane/
   core-reset registers) succeeds - `v1.28.0`'s `network_cyw43_active()` unconditionally sets its
   active flag regardless of `cyw43_wifi_set_up()`'s return code (confirmed by reading
   `extmod/network_cyw43.c` directly), so `nic.active(True)` "succeeding" there is silent either
   way; `v1.21.0` returned `active() == False` with no warning printed at all, which is *consistent
   with* (not proof of) an older port version's `active()` actually propagating a `cyw43_ll_wifi_on()`
   failure - plausible since 3e/3f (firmware/CLM download, SDPCM+ioctl) aren't built yet and
   `cyw43_ll_wifi_on()` needs both. Real, not yet closed, risk to revisit once 3e/3f land.
3. **Chip/backplane + SDPCM/WLC ioctl layer - revised into sub-steps (2026-08-12), see "Real
   bringup sequence beyond F0" above for the full derivation.** The original one-paragraph
   estimate here undersold the real scope (firmware/CLM blob download, ARM core reset registers,
   ALP/HT clock handshake, SDPCM framing all sit *between* F0 and the first `WLC_*` ioctl) - broken
   up so each piece can land and get tested independently, in dependency order:

   1. **3a. Block transfers in `GSPIBus` (step 2's own scope creeping forward).** Generalize
      `_on_clock_rising()`/`_on_clock_falling()` from exactly-one-32-bit-word to an arbitrary,
      word-aligned byte count (`make_cmd()`'s `size` field is 11 bits for exactly this reason).
      Nothing past this point is reachable without it - every SDPCM/ioctl/firmware-download
      transfer is multi-byte. **Done (2026-08-12)**, unit-tested (`bus.py` gained
      `_word_count()`/`_words_to_value()`/`_value_to_words()`; multi-word backplane and F0 block
      round-trips, plus a non-word-aligned 6-byte block, in `tests/test_cyw43_bus.py`). Existing
      single-register accesses (size<=4) are unaffected - `_word_count()` floors to one word, the
      same wire shape they always used, so this is a strict generalization, not a rewrite. Not yet
      exercised against real firmware (that needs 3e's firmware-download acceptance to actually
      trigger a block-sized transfer over the bus).
   2. **3b. ALP/HT clock handshake + KSO** on `BACKPLANE_FUNCTION`'s `SDIO_CHIP_CLOCK_CSR`
      (`0x1000e`) / `SDIO_SLEEP_CSR` (`0x1001f`) - a register model where writing a `*_REQ`/
      `KEEP_SDIO_ON` bit makes the corresponding `*_AVAIL`/`DEVICE_ON` bit immediately readable
      satisfies every poll loop in `cyw43_ll_bus_sleep()`/`cyw43_kso_set()` correctly, no real
      clock-startup latency needs modeling. **Done (2026-08-12)**, unit-tested (round-trip on both
      registers). Not yet confirmed against real firmware's `cyw43_ll_wifi_on()` specifically (see
      step 2's own progress-log entry above for why that's still open).
   3. **3c. Backplane (F1) windowed read/write.** A generic byte-addressable "backplane memory"
      model plus the `SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH` window-select registers on
      `BACKPLANE_FUNCTION` - same shape as `GSPIBus`'s own F0 register space (step 2), just a much
      bigger address space. Specific backplane *addresses'* meaning (core control registers,
      SOCSRAM, ...) layers on top in later sub-steps, not part of the windowing mechanism itself.
      **Done (2026-08-12)**, unit-tested (window-select bytes route a flagged address to the
      correct combined `(window << 15) | (addr & 0x7fff)` slot). Found and fixed a real bug along
      the way: `_f1`'s register-bank addresses (`0x1000a`-`0x1001f`) were being indexed directly
      into a from-zero 32-byte array, so every F1 register access silently fell out of bounds and
      no-op'd - fixed with a `_F1_REGISTER_BASE` offset.
   4. **3d. ARM core reset/enable registers** (`CORE_WLAN_ARM`/`CORE_SOCRAM`'s `AI_IOCTRL_OFFSET`/
      `AI_RESETCTRL_OFFSET`, reached via 3c's windowed access) - reflecting "reset" then "clocked,
      out of reset" on readback is enough; **no second CPU core needs emulating** - the host driver
      never talks to the WLAN core's own firmware except through SDPCM (3f), so nothing downstream
      can tell a real running core from a register model that remembers it was told to start.
      **Done (2026-08-12)**, unit-tested (both cores default to `AIRC_RESET` set; `AI_IOCTRL_OFFSET`
      round-trips through the window like any other backplane-memory address).

   **`Cyw43439` (`external/cyw43/chip.py`, 2026-08-12).** A minimal `ExternalDevice` that just owns
   a `GSPIBus` and calls `attach_gpio()` from its own `attach()` - enough to wire 3b/3c/3d (and
   step 2's F0 work) onto `boards.py`'s `pico_w` extras (alongside the existing placeholder
   `LEDMock`) so real firmware boots actually exercise this code, not just unit tests. 3e (below)
   also landed entirely in `bus.py`, not `chip.py` - continuing the precedent 3b/3c/3d already set
   (the doc's original plan to move "3d onward" into `chip.py` wasn't followed in practice; register/
   wire-level mechanics have all stayed in `GSPIBus` so far, `Cyw43439` remains a thin wrapper).
   Everything past this point (SDPCM/ioctl framing/events, 3f onward) still needs to land before
   `Cyw43439`/`GSPIBus` does anything beyond bus-level register bookkeeping.
   5. **3e. Firmware/CLM blob download acceptance + F2 packet-available status plumbing.** Accept
      (don't need to validate or store meaningfully - `cyw43_check_valid_chipset_firmware()` runs
      entirely driver-side, never reaches the chip) the `cyw43_write_bytes()` block writes real
      firmware/CLM download does; wire up `SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE` +
      `SPI_STATUS_REGISTER`'s `GSPI_PACKET_AVAILABLE`/pending-length field, the mechanism every
      later sub-step's own responses/events get delivered through. **Done (2026-08-12)**, split
      into two independently-confirmed halves:
      - **Firmware/CLM download acceptance turned out to need no new code at all.** Confirmed
        directly from source (`cyw43_ll.c:cyw43_download_resource()`): it writes through
        `cyw43_write_bytes(BACKPLANE_FUNCTION, dest_addr | SBSDIO_SB_ACCESS_2_4B_FLAG, sz, src)` in
        `CYW43_BUS_MAX_BLOCK_SIZE` (64 bytes for SPI, `cyw43_ll.h`) chunks, re-selecting the
        backplane window per chunk - exactly the generic F1 block-write path step 3a/3c already
        built. Payload content is simply stored (and ignored) in `GSPIBus`'s existing sparse
        `_backplane_memory` dict - real firmware download addresses land in SOCSRAM
        (`CORE_SOCRAM`), a normal backplane-window target, nothing WLAN-function-specific.
        Unit-tested (`test_firmware_download_shaped_block_writes_round_trip`) by replaying the real
        chunking algorithm across several sequential 64-byte writes.
      - **F2 packet delivery is genuinely new**: `GSPIBus.queue_rx_packet(data)` stages `data`,
        sets `SPI_STATUS_REGISTER`'s `STATUS_F2_PKT_AVAILABLE` bit + length field (bits 19:9 -
        confirmed the actual field real firmware's SPI-variant `cyw43_ll_sdpcm_poll_device()`
        trusts, `cyw43_ll.c:~1080`) and `SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE` bit (the
        earlier, cheaper gate the same function checks first, `cyw43_ll.c:~1008`), and - if CS is
        currently deasserted - immediately drives the shared `WL_D` pin's own IRQ level high too,
        since real firmware's `cyw43_cb_read_host_interrupt_pin()` (`cyw43_ctrl.c`) polls that pin
        directly and independently of any SPI transaction (confirmed via
        `CYW43_PIN_WL_HOST_WAKE`'s wiring in `pico_cyw43_driver/cyw43_driver.c` - same GPIO as
        `WL_D`). `GSPIBus._on_cs_change()`'s deselect handler, previously a hardcoded LOW placeholder
        (step 2's own note that this was deferred), now reflects `bool(self._rx_packet)` instead. A
        new `_read_wlan()` handles `WLAN_FUNCTION` reads (always fixed-address, `addr=0`, matching
        `cyw43_read_bytes(WLAN_FUNCTION, 0, ...)` - the `addr` argument is unused, mirroring real
        hardware's own FIFO semantics), draining the queue and clearing both status/interrupt bits
        once fully consumed. `WLAN_FUNCTION` *writes* (host-to-chip SDPCM/ioctl content) were still
        a no-op at the time this landed - step 3f (below) fills that in. Unit-tested (5 tests:
        delivery, status/length field, consume-clears-status, IRQ raised while idle, IRQ drops
        once consumed).
      **Flag, still not solved (unchanged from the original estimate):** profile whether bit-level
      `GPIOPin.add_listener()` simulation of a real (hundreds-of-KB) firmware image is practically
      fast enough now that this actually lands and could be exercised against real firmware - may
      need a batched/short-circuited path for large pure-write block transfers specifically. Not
      yet profiled against a real boot (3e's own tests use small synthetic payloads, not a real
      firmware-sized download).
   6. **3f. SDPCM framing + generic ioctl request/response.** **Done (2026-08-12)**, unit-tested (7
      tests in `tests/test_cyw43_bus.py`, 29 total). Landed entirely in `bus.py`, same as every
      prior sub-step:
      - **Correction while implementing: the SDPCM header is 12 bytes, not the 14 originally
        estimated here.** `struct sdpcm_header_t` (`cyw43_ll.c`) is 9 plain uint8/uint16 fields
        (`size`, `size_com`, `sequence`, `channel_and_flags`, `next_length`, `header_length`,
        `wireless_flow_control`, `bus_data_credit`, `reserved[2]`) - `2+2+1+1+1+1+1+1+2 = 12`, no
        compiler padding possible (no uint32 members, already 2-byte aligned). The original
        estimate had also missed the `next_length` field entirely. `SDPCM_HEADER_LEN = 12` in
        `bus.py`, confirmed against the struct's own field list, not assumed. The 16-byte ioctl
        header (`cmd`/`len`/`flags`/`status`, all `uint32_t`) was correct as originally estimated.
      - `GSPIBus._write_wlan()` parses an inbound F2 block write (already a single call thanks to
        step 3a - real firmware sends the whole SDPCM+ioctl+payload blob as one
        `cyw43_write_bytes(WLAN_FUNCTION, 0, ...)`): validates `size`/`~size_com`, and for
        `CONTROL_HEADER` frames only, calls `_build_ioctl_success_response()` and delivers it via
        step 3e's `queue_rx_packet()`. `DATA_HEADER` (outbound Ethernet, step 4) and anything
        malformed are silently ignored, not raised.
      - `_build_ioctl_success_response()` builds a zero-length "success" response echoing the
        request's own id (`ioctl_header_t.flags`'s `CDCF_IOC_ID_MASK` bits -
        `sdpcm_process_rx_packet()` drops any response whose id doesn't match the driver's last
        sent one), with `wireless_flow_control` always `0` and a `bus_data_credit` byte
        (`GSPIBus._bus_data_credit`, starting at 1 to match the driver's own initial
        `wwd_sdpcm_last_bus_data_credit`) incremented once per response. Both are real
        correctness requirements, not cosmetic: a nonzero `wireless_flow_control` or a
        `bus_data_credit` that doesn't stay strictly ahead of the driver's own send count makes
        `cyw43_sdpcm_send_common()`'s own STALL check block every later host send forever
        (confirmed by reading that function, not just the receive side).
      - One generic "echo a zero-length success response" handler - no branching on `cmd` at all -
        already satisfies the bulk of the real `WLC_*`/iovar vocabulary `cyw43_ll_wifi_on()`/
        `cyw43_ll_wifi_join()` send during bring-up, exactly as originally planned. Real per-ioctl
        content (`WLC_SET_SSID`/join's own scripted event sequence, `escan` results) is step 3g.
      - Not yet confirmed against real firmware booting through this far - 3f's own tests drive
        `GSPIBus` directly via the same fake-master wire-bang pattern every other test here uses,
        not an actual MicroPython boot.
   7. **3g. Async events + scripted scan/join.** `cyw43_async_event_t`'s exact field layout
      (byte offsets matter - real firmware struct padding, not a natural dataclass shape),
      delivered as a fake-Ethernet-framed (`0x886c` + Broadcom OUI) SDPCM async packet. A fixed
      fake AP (mirroring `Wokwi-GUEST`'s shape) answers `scan()`'s single `escan` iovar with one
      `CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` event. `join()`'s own scripted `WLC_E_*`
      sequence (`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`/...) is the one piece not yet fully read from
      source (see "Research homework" below - `cyw43_ll_wifi_join()`'s tail end, past the SSID
      write) - this is the sub-step that actually makes `.status()` progress through real codes.

   Lives in `external/cyw43/chip.py` (3d onward - 3a is `external/cyw43/bus.py`, 3b/3c could
   reasonably live in either, judgment call when implementing); this is where `Cyw43439`
   implements `ExternalDevice.attach()`. Every sub-step stays synchronous, in-line - still a pure
   state machine, no real I/O (`schedule_threadsafe()`) until step 4.
4. **Real network bridge.** Once firmware believes it's associated and starts moving IP packets,
   the userspace NAT/SLIRP layer described earlier — real `socket.getaddrinfo`/TCP/UDP via the host,
   DNS included — so `urequests`-style code in emulated firmware reaches the real internet. Needs
   the SDPCM data-frame envelope format (how an actual Ethernet/IP frame is wrapped for the F2/WLAN
   data path) - not yet extracted from `cyw43_ll.c`, see "Open questions" below. **This is where
   `schedule_threadsafe()` actually gets used** — real socket I/O is the first genuinely slow/
   blocking work in the whole plan. Lives in `external/cyw43/nat.py`.

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
- Explicitly **out of scope for the `firmware_retrieve.py` board-awareness work (done - see
  "Deferred, not designed" below): the `"bootrom"` entry.** The
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

- **Done (2026-08-12): `utils/firmware_retrieve.py` (moved from `cli/` - it's a generic tag/URL/
  path resolver with no argparse involvement, not CLI-specific) is now board-aware**, per the
  "Candidate redesign" this bullet originally proposed - implemented essentially as decided, both
  formerly-open sub-items resolved along the way:

  - `FirmwareSpec` dropped `filename_template`/`url_template` entirely - `boards: dict[board,
    dict[tag, url]]` (MicroPython/CircuitPython/Kaluma - genuinely different per-board builds) or a
    flat `known_versions: dict[tag, url]` (BOOTROM only - board-agnostic, a silicon-stepping-only
    mask ROM, deliberately *not* nested by board - see the "Historical/closed" section above for
    why). `retrieve(spec, image, board="pico")` resolves three ways: existing local path (unchanged),
    a direct `http(s)://` URL (new - downloads and caches it under its own basename, or a
    `sha256(url)[:16]` hash when the URL has no usable path component - the "how to name the cache
    entry" sub-item's resolution), or a version tag looked up in `spec.boards[board]` (an unknown
    board or unknown tag is now a clear, distinct logged error instead of the old silent
    "fall back to using the raw tag as a literal filename suffix" behavior). `board` is consulted
    only for the tag path against a `boards`-shaped spec, exactly as decided - a local path or raw
    URL is used exactly as given regardless of `--board`, and `board` is ignored entirely for
    `known_versions`-shaped BOOTROM.
  - **Refresh workflow (the other open sub-item), decided while implementing:** one script,
    `scripts/fetch_firmware.py`, not one per firmware family - fetches and writes all four
    families' real data in a single pass (run `uv run scripts/fetch_firmware.py`, diff, commit).
    Sources actually used, each confirmed live (2026-08-12), correcting a couple of assumptions
    from the original proposal along the way:
    - MicroPython: `https://micropython.org/download/{RPI_PICO,RPI_PICO_W}/`, scraped HTML - as
      originally proposed (the `tool/fetch-mp-firmware` branch's own prototype scraper, since
      superseded by this script - extended to emit full URLs instead of bare filenames).
    - CircuitPython: **not** the board pages (`circuitpython.org/board/<slug>/`, which - confirmed
      live - only ever show the *current* stable + prerelease, no history at all). The public S3
      bucket's own REST listing API instead (`?prefix=...` on the bucket root - not to be confused
      with `/index.html?prefix=...`, a JS-rendered page not scrapable without a JS engine) returns
      the *full* version history as a plain XML `ListBucketResult` - filtered to drop CI nightly/
      PR-preview builds that live in the same prefix (named `<8-digit-date>-<branch>-PR<n>-<hash>`,
      not a real version).
    - Kaluma: **the original "no clean per-board split" assumption was wrong** - confirmed directly
      against the GitHub releases API that Kaluma *does* publish separate
      `kaluma-rp2-pico-<version>.uf2`/`kaluma-rp2-pico-w-<version>.uf2` release assets, on every
      release since 1.1.0. No compromise/special-casing needed after all.
    - Bootrom: the GitHub releases API (`raspberrypi/pico-bootrom-rp2040`) - one `<tag>.elf` asset
      per release (b0/b1/b2) - fetched by the same script for consistency (one place the
      board-aware-vs-not distinction lives), written to the flat `known_versions` shape.
  - `firmware_specs.json` (now `utils/firmware_specs.json`) populated with real data from all four
    sources as of 2026-08-12 - 23/17 MicroPython pico/pico_w versions, 160/105 CircuitPython
    (limited to non-nightly releases), 17/9 Kaluma, 3 bootrom.

### Research homework — not decisions, just needs source-reading during implementation

- **Resolved (2026-08-12), see "Real bringup sequence beyond F0" above:** the backplane *core*
  address map (chip-common registers, ARM core reset/halt registers, SOCSRAM addresses) needed for
  bring-up, SDPCM header layout, and async-event framing are now all documented directly from
  `cyw43_ll.c`/`cyw43_ll.h` - no longer just "not yet dug out."
- **Still open: `cyw43_ll_wifi_join()`'s tail end** (past the point read for the "Real bringup
  sequence" section above - the actual `WLC_SET_SSID` call that triggers the join, and the
  `WLC_E_*` event sequence real firmware fires in response) - needed for step 3g specifically.
  Read the rest of `cyw43_ll_wifi_join()` (starts at `cyw43_ll.c:2051`) and
  `cyw43_ll_parse_async_event()`'s callers when implementing that sub-step.
- The SDPCM data-frame envelope byte layout for the actual WLAN RX/TX *data* path (as opposed to
  control/ioctl, already covered above) - step 4's concern, not step 3 - not yet dug out.
  - maybe document way to use gpiozero as global external device for emulations on the RP
