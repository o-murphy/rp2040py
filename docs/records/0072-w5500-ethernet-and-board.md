# 0072. W5500 Ethernet PHY `ExternalDevice` + `W5500_EVB_PICO` board (epic)

- Status: **Proposed — documented, phased plan only, nothing implemented (2026-08-18).** This
  record is the go-ahead's *first* deliverable per CLAUDE.md's "document vs. implement" rule -
  writing the plan down does not itself authorize building any phase; each phase gets its own
  explicit go-ahead, and this record's own status line tracks which phases have actually landed.
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey that named `W5500_EVB_PICO`/
  `W5100S_EVB_PICO`/`SIL_RP2040_SHIM` as "needs exactly one new device"), [0048](0048-cyw43-nat-reflector.md)
  (`NatBridge`/`ArpResponder`/`DhcpServer`/`TcpReflector`/`UdpRelay` - the machinery this record
  proposes reusing, not rebuilding), [0045](0045-cyw43-nat-libslirp-cython.md) (**the cautionary
  precedent**: a full third-party TCP/IP stack was tried for CYW43 and superseded by 0048's lighter
  reflector - the same lesson applies here, argued below), [0027](0027-cyw43-wifi.md) (the epic
  shape/phased-record precedent this record follows), 0059 (`BoardSpec` firmware resolution), 0027's
  "3g rule"

## The question

Can `W5500_EVB_PICO` (and, once the register-level work exists, `W5100S_EVB_PICO`/
`SIL_RP2040_SHIM` per 0066's own note that they're "close enough in register shape") become a real
`--board-spec` example, and how much work is a W5500 `ExternalDevice` actually - given the chip has
a **fully hardwired hardware TCP/IP stack**, is that something to emulate faithfully, or is a
reflector (mapping the chip's own protocol onto real host sockets, the same shape [0048](0048-cyw43-nat-reflector.md)
already built for CYW43) the better fit?

**Short answer, argued below: reflector, and the CYW43 reflector's own machinery reuses almost
directly for the MACRAW half.** The chip's SPI register protocol still needs a real, new
`ExternalDevice` (nothing existing covers it) - the reuse is at the networking-semantics layer, not
the register layer.

## The chip: SPI register protocol, sourced from WIZnet's own `ioLibrary_Driver`

Not the wiznet5k git submodule MicroPython vendors (present in this repo's local checkout as an
empty, uninitialized submodule directory, `lib/wiznet5k/`) but the same source that submodule
would pin - WIZnet's own public `Wiznet/ioLibrary_Driver` (`Ethernet/W5500/w5500.h`/`.c`), the
reference HAL every W5500 driver (MicroPython's, CircuitPython's `adafruit_wiznet5k`, Arduino's)
is built against. Every number below is a `#define` or a documented behavior from that source
(0027's "3g rule").

**SPI framing** (`WIZCHIP_READ()`/`WIZCHIP_WRITE()` in `w5500.c`): three address-phase bytes, then
N data bytes, one register access per SPI transaction (chip-select framed) -

    byte 0: bits 15-8 of a 16-bit offset within the addressed block
    byte 1: bits  7-0 of that offset
    byte 2: control byte = (BSB << 3) | (RWB << 2) | OM
              BSB = 5-bit Block Select Bits (which register/buffer block, below)
              RWB = 1 = write, 0 = read
              OM  = operation mode (00 = VDM, variable data length - the common case)
    byte 3..: the register/buffer bytes themselves, MSB-first for multi-byte registers

**Block Select Bits** (`WIZCHIP_CREG_BLOCK`/`WIZCHIP_SREG_BLOCK(N)`/`WIZCHIP_TXBUF_BLOCK(N)`/
`WIZCHIP_RXBUF_BLOCK(N)` in `w5500.h`): block 0 is the one shared common-register block; each of
the 8 sockets (0-7) gets three blocks of its own - `1+4N` (socket registers), `2+4N` (that socket's
TX buffer, a real memory region, not a register - 2 KiB by default), `3+4N` (its RX buffer, same
size default) - so the *same* 16-bit offset can mean a register or a buffer byte purely depending
on which BSB selected it.

**Common registers actually needed** (offsets are the `w5500.h` `#define`s' literal hex, all in
block 0): `MR` (`0x0000`, mode - `MR_RST = 0x80` self-clears after a soft reset), `GAR`/`SUBR`/
`SHAR`/`SIPR` (`0x0001`/`0x0005`/`0x0009`/`0x000F` - gateway/subnet/MAC/IP, 4-6 bytes each,
firmware always writes these at init regardless of which socket mode it uses), `PHYCFGR` (`0x002E`
- bit 0 `PHYCFGR_LNK_ON = 1` is the link-up bit guest code polls), `VERSIONR` (`0x0039`, always
reads back a fixed chip-identity constant - real hardware's read-only "yes, I am a W5500" signal).

**Per-socket registers actually needed** (offsets relative to that socket's own SREG block, same
for every socket N): `Sn_MR` (`0x0000` - mode: `Sn_MR_CLOSE=0x00`, `Sn_MR_TCP=0x01`,
`Sn_MR_UDP=0x02`, `Sn_MR_MACRAW=0x04`), `Sn_CR` (`0x0001` - command, guest writes one of
`Sn_CR_OPEN=0x01`/`LISTEN=0x02`/`CONNECT=0x04`/`DISCON=0x08`/`CLOSE=0x10`/`SEND=0x20`/`RECV=0x40`;
real hardware self-clears this register back to `0x00` once the command completes, which is how a
driver polls "did my command finish"), `Sn_IR` (`0x0002` - `Sn_IR_CON=0x01`/`DISCON=0x02`/
`RECV=0x04`/`TIMEOUT=0x08`/`SENDOK=0x10`, write-1-to-clear same shape [0042](0042-cyw43-interrupt-register-w1c-fix.md)
already fixed for CYW43), `Sn_SR` (`0x0003` - status: `SOCK_CLOSED=0x00`, `SOCK_INIT=0x13`,
`SOCK_LISTEN=0x14`, `SOCK_ESTABLISHED=0x17`, `SOCK_UDP=0x22`, `SOCK_MACRAW=0x42`, ...), `Sn_PORT`/
`Sn_DIPR`/`Sn_DPORT` (`0x0004`/`0x000C`/`0x0010` - local port, destination IP/port for TCP/UDP
modes), `Sn_TX_FSR`/`Sn_TX_RD`/`Sn_TX_WR` (`0x0020`/`0x0022`/`0x0024` - free-size and read/write
pointers into that socket's TX ring buffer) and `Sn_RX_RSR`/`Sn_RX_RD`/`Sn_RX_WR` (`0x0026`/
`0x0028`/`0x002A` - same shape for RX).

## Two very different consumption patterns for the same chip - both real, both need support eventually

This is the finding that most shapes the plan below. The W5500's own onboard hardware TCP/UDP
socket engine (`Sn_MR_TCP`/`Sn_MR_UDP` + `Sn_CR` OPEN/CONNECT/SEND/RECV) is what the chip's
marketing describes ("fully hardwired TCP/IP controller") - but it is **not** what this board's own
stock MicroPython firmware uses:

- **MicroPython** (`ports/rp2/boards/W5500_EVB_PICO/mpconfigboard.cmake`):
  `set(MICROPY_PY_LWIP 1)`, `set(MICROPY_PY_NETWORK_WIZNET5K W5500)`. `extmod/network_wiznet5k.c`'s
  own top comment names two build modes - `WIZNET5K_WITH_LWIP_STACK` (`MICROPY_PY_LWIP`) vs.
  `WIZNET5K_PROVIDED_STACK` (`!MICROPY_PY_LWIP`) - and this board's own cmake picks the **first**:
  MicroPython's bundled lwIP does all TCP/IP in software, and the W5500 is driven in **MACRAW**
  mode (`Sn_MR_MACRAW`, raw Ethernet frames only) as a dumb frame-in/frame-out MAC+PHY. The chip's
  own hardware socket engine (TCP/UDP modes) is **not exercised at all** by this board's own
  shipped firmware.
- **CircuitPython**: no core Ethernet network stack at all (`ports/raspberrypi/boards/wiznet_w5500_evb_pico/`'s
  `board.c`/`pins.c` only expose `board.W5K_SPI`/`W5K_CS`/`W5K_RST`/`W5K_INT` as plain pins/an SPI
  bus - no Wiznet driver code compiled in). The real consumption path is Adafruit's downloadable,
  pure-Python `adafruit_wiznet5k` library (not bundled in the stock `.uf2` - a user adds it to
  `CIRCUITPY/lib/` themselves), which drives the chip's **hardware TCP/UDP socket engine directly**
  (`Sn_MR_TCP`/`UDP` + `Sn_CR` OPEN/CONNECT/SEND/RECV/CLOSE, polling `Sn_SR` for state
  transitions) - CircuitPython has no lwIP-equivalent to fall back on the way MicroPython's rp2
  port does.

So: MicroPython needs **MACRAW passthrough**; CircuitPython (once a guest stages the library) needs
the **hardware socket engine emulated**. Neither is optional if both firmware families are meant to
work the way every other board file in this project already does - but they are architecturally
different problems, which is why the plan below splits them into separate phases rather than one
"implement TCP" phase.

**The split is per-socket-mode, not per-firmware-family - `rp2040py run` makes this concrete.**
`run` loads a raw image (any firmware - Arduino/bare C++ against WIZnet's own `ioLibrary_Driver`,
TinyGo, ...) with no firmware-family resolution at all (docs/0000-TRACKER.md's own "test
TinyGo-compiled firmware" row notes `run` already does this for any `.uf2`/`.hex`). Such a guest is
entirely free to put any socket into `Sn_MR_TCP`/`UDP` and drive it with the chip's own hardware
socket engine directly - and arguably *more* likely to, since that is the chip's advertised,
default mode and what most non-MicroPython example code (including WIZnet's own) actually uses;
MicroPython's LWIP+MACRAW choice is the unusual one, not the norm, among firmwares for this chip.
The device therefore cannot gate phase 2 vs. phase 3 logic on which subcommand booted it - it has
to watch each socket's own `Sn_MR` at runtime and dispatch per socket, since a single firmware
could legitimately run one socket in MACRAW and another in TCP mode simultaneously. One useful
consequence: phase 3 does not need CircuitPython + a staged `adafruit_wiznet5k` to exercise at
all - any small W5500-EVB-Pico C/Arduino example run via `rp2040py run` hits the identical code
path, and needs no guest-side library staging.

## Reuse: 0048's NAT/reflector machinery already speaks the MACRAW half's exact language

`rp2040py.external.cyw43.nat.NatBridge` (0048) is not CYW43-specific in what it actually operates
on - its entry point is `handle_outbound_ethernet_frame(frame: bytes) -> None`, called
synchronously on every outbound **raw Ethernet frame**, with replies delivered later through a
`queue_ethernet_frame: Callable[[bytes], None]` callback. That is *exactly* the granularity W5500's
MACRAW mode operates at: a `Sn_CR_SEND` on a MACRAW socket hands over one complete Ethernet frame
from that socket's TX buffer, and an inbound frame is written into its RX buffer plus `Sn_IR_RECV`
set. `ArpResponder`, `DhcpServer`, `TcpReflector` (spoofs the guest-facing TCP handshake, real
reachability is the host socket's job) and `UdpRelay` (DNS relay + opaque one-shot relay) were all
written against "guest sends/receives whole Ethernet frames," not against anything CYW43-specific
in their bodies - `GSPIBus._write_wlan()`/`queue_rx_ethernet_frame()` are CYW43's own integration
points, not part of `NatBridge`'s own contract.

**Open design question, not decided here**: `nat.py` currently lives under `external/cyw43/`, a
sub-package this project's own module-layout convention (CLAUDE.md) reserves for "a device
genuinely big enough to need one" - i.e. CYW43-specific. Reusing it for W5500 means either (a)
promoting `NatBridge` and friends to a shared, non-cyw43-specific module (`external/nat.py` or
similar) that both chips' devices import, or (b) something else not yet considered. This is exactly
the kind of "new module's layout isn't obvious, ask rather than guess" case CLAUDE.md calls out by
name - flagged here for the actual go-ahead conversation, not decided unilaterally.

**This reuse is phase 2's only - phase 3 needs almost none of it, because the guest never
constructs a wire protocol in hardware-socket-engine mode.** `ArpResponder` answers ARP requests
that only exist because a MACRAW guest builds real Ethernet frames - there is no ARP concept at
the `Sn_MR_TCP`/`UDP` register API at all (the guest writes `Sn_DIPR` and trusts the chip to
resolve it; nothing about that is visible to fake). `TcpReflector`'s entire job (per its own
docstring, quoted above) is "handshake spoofing, seq/ack bookkeeping, ... FIN/RST propagation" -
all of that exists solely because a MACRAW/lwIP guest builds and expects to see real TCP segments.
A hardware-socket-engine guest never constructs a SYN, never sees a seq number, never sees a FIN -
it writes `Sn_CR_CONNECT` and polls `Sn_SR` for `SOCK_ESTABLISHED`. So phase 3's reflector is not
"the same reflector, reused" but a **much thinner byte-pipe shim**: `Sn_CR` commands map close to
1:1 onto real `socket()`/`connect()`/`send()`/`recv()`/`close()` calls, `Sn_SR` mirrors that real
socket's own lifecycle, and `Sn_TX_FSR`/`Sn_RX_RSR` are just free-space bookkeeping on the ring
buffers either side of a real, unmodified host TCP connection - no protocol bytes to fake at all,
because the host socket's own real TCP stack already produces them. `Sn_MR_UDP` is simpler still -
no connection state, each `Sn_CR_SEND` names its own destination (closer to `sendto()` than
`send()`). Whether phase 3 needs *any* `DhcpServer`-shaped component of its own is a real open
question, not resolved here: a guest that opens a broadcast UDP socket expecting a DHCP OFFER back
is now, in a genuinely-networked reflector, a case that could be *actually* relayed to a real DHCP
server on the host's own network rather than spoofed the way 0048's fixed-lease `DhcpServer`
does for CYW43's fully-synthetic AP - a materially different tradeoff, deferred to phase 3 itself.

## Why not a real TCP/IP stack (the 0045-vs-0048 lesson, applied here before being relearned)

[0045](0045-cyw43-nat-libslirp-cython.md) tried wiring a full third-party stack (gVisor's
`pkg/tcpip`) in for CYW43 and it was **superseded** by 0048's much lighter reflector - real
destination reachability (retransmission, congestion control, ARP-for-real-hosts) is the *host
OS's* job via a real socket, and the guest-facing leg only needs handshake spoofing and
seq/ack/window bookkeeping, because `GSPIBus.queue_rx_packet()`/its W5500 equivalent is an
in-process, lossless, in-order delivery path, not a real lossy link. There is no reason to expect
the tradeoff to come out differently for W5500 - if anything it favors the reflector *more*
here, since MACRAW mode's guest-facing side is lwIP itself (a real, independent TCP/IP stack
already running in the guest), so the emulator's job is even more purely "deliver frames," with
essentially none of TCP's own semantics needing to be understood at the Ethernet-frame layer at
all (unlike CYW43, where the reflector has to parse TCP headers to spoof handshakes because the
*chip* is what's supposed to be dumb-radio-only). The phase-3 (hardware-socket-engine) case is a
socket-granularity reflector instead of a frame-granularity one, and is if anything simpler again:
the guest never constructs its own TCP headers at all in that mode (the chip is nominally doing
that internally), so there is no guest-facing handshake to spoof - `Sn_CR_CONNECT` maps
essentially 1:1 onto a real `socket.connect()`, `Sn_SR` transitions onto that socket's own
lifecycle.

## The board: `W5500_EVB_PICO`

Sourced from `ports/rp2/boards/W5500_EVB_PICO/` (MicroPython) and
`ports/raspberrypi/boards/wiznet_w5500_evb_pico/` (CircuitPython), both already read in this
session (the same session that gathered the register facts above):

- `mpconfigboard.h` (MicroPython): `MICROPY_HW_FLASH_STORAGE_BYTES (1408 * 1024)`,
  `MICROPY_HW_WIZNET_SPI_ID=0`, `_SCK=18`/`_MOSI=19`/`_MISO=16`, `MICROPY_HW_WIZNET_PIN_CS=17`/
  `_RST=20`/`_INTN=21`, `MICROPY_HW_WIZNET_SPI_BAUDRATE = 20 MHz`.
- `mpconfigboard.cmake` (MicroPython): `set(PICO_BOARD wiznet_w5100s_evb_pico)` - **not** a typo or
  a stale copy-paste: the pico-sdk board header for that id
  (`lib/pico-sdk/src/boards/include/boards/wiznet_w5100s_evb_pico.h`) is shared by both the W5500
  and W5100S eval boards on purpose, since they are electrically identical apart from which Ethernet
  chip is populated - same header confirms `PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)` (matching a
  plain Pico exactly) and every pin above.
- CircuitPython's `pins.c`: `W5K_MISO`/`W5K_CS`/`W5K_SCK`/`W5K_MOSI`/`W5K_RST`/`W5K_INT` on GPIO16/
  17/18/19/20/21 respectively (agreeing with MicroPython's pins), `LED` -> GPIO25, `W5K_SPI` bus
  singleton. `mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x1029`, `EXTERNAL_FLASH_DEVICES =
  "W25Q16JVxQ"` (16 Mbit = 2 MiB, agreeing with the pico-sdk header), `CIRCUITPY_SSL = 1`.

Flash geometry is **identical to a plain Pico** (same derivation as
`boards/waveshare_rp2040_zero/`'s own): `fs_start = 0xa0000`, `fs_blockcount = 352` for
MicroPython; the generic CircuitPython `fs_start = 0x100000`, `fs_blockcount = 512` convention for
CircuitPython (no board-specific `link.ld` - not yet confirmed absent for this specific board's
CircuitPython directory listing, a Phase 4 task, not asserted here).

## Phased plan

Each phase is a separate go-ahead (CLAUDE.md's "document vs. implement" rule) and gets its own
exit criterion, not just a task list - mirroring how [0027](0027-cyw43-wifi.md) tracked steps 0-4
with dated progress markers rather than one undifferentiated blob.

1. **SPI register-protocol core.** A new `ExternalDevice` (flat file,
   `src/rp2040py/external/w5500.py` per this project's module-layout default) implementing the SPI
   framing and register/buffer addressing above: common registers (`MR`/`GAR`/`SUBR`/`SHAR`/
   `SIPR`/`PHYCFGR`/`VERSIONR` at minimum), per-socket registers and TX/RX buffer memory for all 8
   sockets, `Sn_CR` self-clear-on-completion semantics, `Sn_IR` write-1-to-clear (0042's pattern).
   No networking traffic yet - purely "the chip answers register reads/writes correctly." **Exit
   criterion**: a unit test suite (`tests/test_w5500.py`, following `tests/test_ws2812.py`'s
   template of driving through real memory-mapped registers) asserting every register offset above
   against its cited value, plus a live boot where MicroPython's own `network.WIZNET5K` init
   sequence (or CircuitPython's `adafruit_wiznet5k`, once staged) completes without hanging and
   reports `PHYCFGR_LNK_ON`.
2. **MACRAW passthrough**, using `NatBridge` (promoted per the open question above, or reused as-is
   if that question resolves toward keeping it where it is and importing across). Triggered
   per-socket, whenever a guest puts that socket into `Sn_MR_MACRAW` (not gated on which
   subcommand booted the firmware - see "the split is per-socket-mode" above): `Sn_CR_SEND` hands a
   full frame to `handle_outbound_ethernet_frame()`; the `queue_ethernet_frame` callback writes an
   inbound frame into the RX buffer, advances `Sn_RX_WR`, sets `Sn_IR_RECV`. **This is the
   highest-leverage phase** - it is what makes the board's own *stock, unmodified* MicroPython
   firmware (`MICROPY_PY_LWIP=1` by default) actually reach a real network with zero guest-side
   setup, the same way booting `vcc_gnd_yd_rp2040`/`waveshare_rp2040_zero` needs no guest code to
   see WS2812 activity. **Exit criterion**: real MicroPython W5500-EVB-Pico firmware, live-booted,
   guest code does a real DNS lookup + TCP fetch (or the closest equivalent CI can run headless)
   and gets a real response back through the reflector.
3. **Hardware TCP/UDP socket-engine mode**, triggered per-socket whenever a guest puts that socket
   into `Sn_MR_TCP`/`Sn_MR_UDP` instead - CircuitPython's `adafruit_wiznet5k` path, any MicroPython
   build with `MICROPY_PY_LWIP=0`, and (per the same per-socket-mode point) any raw C/Arduino
   firmware booted via `rp2040py run` that drives the chip's advertised default mode directly -
   likely the easiest way to exercise this phase in practice, since it needs no guest-side library
   staged onto a filesystem at all. Socket-granularity reflector: `Sn_CR_CONNECT` -> real
   `socket.connect()`, `Sn_CR_SEND`/`RECV` shuttle raw payload bytes (no frame parsing needed -
   argued above, this phase is architecturally simpler than phase 2's). **Exit criterion**: a
   small W5500-EVB-Pico C/Arduino example (or, if none is readily available, `adafruit_wiznet5k`
   staged into a CircuitPython image's filesystem) live-booted via `rp2040py run`/`--circuitpython`,
   guest does a real socket round-trip through the reflector.
4. **`boards/wiznet_w5500_evb_pico/`** - the board file itself, `LEDMock(gpio=25)` +
   `BootselButton` + the new device on the pins cited above, both firmware families declared,
   live-boot-verified - the same shape 0068-0071 already established. Gated on phase 1+2 for a
   meaningful MicroPython story; phase 3 can land after if CircuitPython support slips (staging an
   external library is real extra setup regardless of device readiness, so it is not blocking in
   the same way phase 1/2 are).
5. **Deferred, explicitly out of scope for now** (same shape as 0048's own "Known gaps"): whether
   one device covers `W5100S`/`SIL_RP2040_SHIM` too (0066's own "not investigated" note - worth
   revisiting once phase 1 exists, since the two chips' register maps are reportedly close but this
   has not been checked against real W5100S source the way W5500's was here), multi-socket
   concurrency beyond socket 0, PPPoE, IPv6, and the W6100/dual-stack chip family (a different chip
   entirely, out of scope regardless).

## Not decided here

- The `nat.py` module-promotion question above.
- Whether phase 3's socket-granularity reflector can share any code with phase 2's TCP reflector
  logic (`TcpReflector`) or is cleanly separate - not analyzed at this level of detail; a phase-3
  task, not a phase-0 one.
- Whether this graduates past `boards/` into `boards.BOARDS` (0059's promotion checklist) - not
  discussed, same as every other board this project has added so far except the two shipped in
  `boards.BOARDS` from the start.
