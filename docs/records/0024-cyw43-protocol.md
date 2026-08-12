# 0024. Note — CYW43439 protocol reverse-engineering & authoritative reference

- Status: Note (research)
- Recorded: 2026-08-11
- Related: 0027 (CYW43 epic), 0028, 0029, 0030

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 73-476 -->

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

