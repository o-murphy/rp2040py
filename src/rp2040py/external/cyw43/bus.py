"""gSPI bus-level decode for the CYW43439 (docs/CYW43_WIFI_BACKLOG.md's "Implementation order"
step 2). Watches the three shared electrical pins (`WL_CLK`/GPIO29, `WL_D`/GPIO24, `WL_CS`/GPIO25)
via plain `GPIOPin.add_listener()`/`set_input_value()` - the same primitive `ssi.py`/`led_mock.py`
already use - and reconstructs the 32-bit-word-oriented, half-duplex protocol real
`cyw43_bus_pio_spi.c`/`.pio` bit-bang over PIO, without executing (or caring about) any PIO
program itself. Confirmed against the actual official BSD-3-Clause `pico-sdk`/`cyw43-driver`
source, not reverse-engineered - see docs/CYW43_WIFI_BACKLOG.md's "Authoritative protocol
reference" and "Hardware: how RP2040 talks to the CYW43439" sections for the citations.

**Wire framing, derived from `cyw43_bus_pio_spi.pio`'s default `spi_gap01_sample0` program**
(`out pins,1 side 0` / `jmp x-- lp side 1` for the write loop; `in pins,1 side 0` / `jmp y--
lp2 side 1` for the read loop, i.e. every actual bit action - driving a new TX bit, or sampling an
RX bit - happens while `WL_CLK` is *low*, immediately before it's raised again):

- CS (`WL_CS`) is active-low, matching `cs_set()`/`start_spi_comms()`/`stop_spi_comms()` in the C
  source: a transaction starts when the RP2040 drives it low, ends when driven high again.
- Data is sampled on `WL_CLK`'s *rising* edge (captures whatever the host set during the preceding
  low phase) and driven (by us, when it's our turn) on `WL_CLK`'s *falling* edge (so it's stable
  through the low phase for the host's own `in pins,1 side 0` sample right before the next rise) -
  a single consistent rule that covers both transfer directions of one shared half-duplex line.
- The first 32-bit word of a transaction is always the command header (`make_cmd()`'s bit layout -
  see `_decode_header()`). A `write` header is followed by `_word_count(size)` more host-driven
  32-bit words (the value, or block data for a multi-byte transfer - step 3a, `_words_to_value()`);
  a `read` header switches the data line to chip-driven and we shift that many words of the
  requested register's value back out, MSB-first per word, immediately after the header completes
  (`_start_response()`/`_value_to_words()`). `size` (the header's own 11-bit field, in bytes) is
  always word-aligned for genuine block transfers (real driver asserts `!(len & 3)`) but a plain
  1-3 byte register poke still rides exactly one full word either way - `_word_count()` covers both
  uniformly via ceiling division with a floor of one word.

**Word-length/endian mode (`SPI_BUS_CONTROL` bit 0, `WORD_LENGTH_32`).** The physical gSPI
interface itself - not any one function's registers - operates in one of two word-transfer modes,
and what actually appears on the wire for the *same* logical 32-bit command/value differs between
them. Confirmed empirically against a real Pico W: `cyw43_ll_bus_init()`'s very first transactions
(`read_reg_u32_swap()`/`write_reg_u32_swap()`, used only to poll `SPI_READ_TEST_REGISTER` and to
perform the *one* `SPI_BUS_CONTROL` write that switches modes) additionally apply a C-level
`SWAP32()` - which despite the name is `REV16` (byte-swap *within* each 16-bit half, not a full
32-bit reversal) - on top of the DMA engine's own unconditional full 32-bit byte-swap
(`channel_config_set_bswap(true)`, applied to every gSPI DMA transfer regardless of caller). Composing
a 16-bit-lane byte-swap with a full 32-bit byte-swap nets out to swapping the *two 16-bit halves*
of the word (bytes stay in-order within each half, the halves trade places) - confirmed by decoding
a real firmware capture: the observed first wire word for the test-register read was
`0xa0044000`, exactly `0x4000a004` (the real `make_cmd()` value) with its halves swapped. Every
*other* accessor (`cyw43_read_reg_u32`/`_cyw43_write_reg`/etc., used for everything past that one
`SPI_BUS_CONTROL` write) skips the C-level `SWAP32()` and relies solely on the DMA engine's full
32-bit byte-swap plus the *chip's own* `ENDIAN_BIG` config (set by that same `SPI_BUS_CONTROL`
write) to net out correctly - i.e. once configured, the wire carries a full-byte-reversal of the
natural value instead. This is real, stateful gSPI hardware behavior (`WORD_LENGTH_32`: 0 = 16-bit
word length, the chip's own power-on default; 1 = 32-bit), not an implementation quirk - `_word()`
applies the matching (self-inverse) transform in both decode and encode directions, and
`_write_f0()` flips `_word_length_32` the moment a `SPI_BUS_CONTROL` write's value has bit 0 set,
same as real silicon reconfiguring itself the instant that command lands.

**Scope.** `BUS_FUNCTION` (F0, step 2 - "far enough for the driver's init handshake to succeed")
plus `BACKPLANE_FUNCTION` (F1, step 3b/3c/3d - see docs/CYW43_WIFI_BACKLOG.md's "Real bringup
sequence beyond F0" for the full derivation), plus arbitrary word-aligned block transfers on top of
either function (step 3a - `_word_count()`/`_words_to_value()`/`_value_to_words()`, needed before
anything past this point - firmware/CLM download, SDPCM framing, step 3e onward - can move real
multi-byte data at all): the ALP/HT clock handshake and KSO sleep-CSR
(`SDIO_CHIP_CLOCK_CSR`/`SDIO_SLEEP_CSR`) - the *only* two steps in `cyw43_ll_bus_init()` that
actually check their own return value and abort the whole sequence on failure, confirmed by
reading the rest of that function; the backplane windowed-memory redirect
(`SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH` + the `SBSDIO_SB_ACCESS_2_4B_FLAG`-tagged address range);
and the ARM-core reset/enable registers reachable through that window
(`device_core_is_up()`/etc. only ever warn on a mismatch, never fail the bringup - lower-risk to
get exactly right, included anyway since it's the same generic mechanism). F0's address space is a
plain byte-addressable `bytearray` (real SDIO/gSPI F0 registers ARE just consecutive byte
registers - e.g. `SPI_BUS_CONTROL`/`SPI_RESPONSE_DELAY`/`SPI_STATUS_ENABLE`/`SPI_RESET_BP` are
bytes 0x0000-0x0003, and a 4-byte `make_cmd(..., size=4)` write starting at `SPI_BUS_CONTROL` -
exactly what `cyw43_ll_bus_init()` does - covers all four in one transfer) - so read/write dispatch
is generic, not a hand-written case per register, with `SPI_READ_TEST_REGISTER` as the one fixed,
read-only exception real firmware polls for a known-good value before trusting anything else on
the bus. F1 is *two* address ranges in the real chip's own addressing scheme, not two different
mechanisms on this side: `0x8000-0xffff` (`SBSDIO_SB_ACCESS_2_4B_FLAG` set) redirects into a
separate, sparse "backplane memory" store indexed by `(window << 15) | (addr & 0x7fff)` - `window`
being whatever was last written across `SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH` - while everything
else is F1's own small register bank (the window-select bytes themselves, `SDIO_CHIP_CLOCK_CSR`,
`SDIO_SLEEP_CSR`, ...), same generic byte-addressable shape as F0's. `WLAN_FUNCTION` (F2) reads
deliver whatever `queue_rx_packet()` staged (step 3e: `_read_wlan()`,
`STATUS_F2_PKT_AVAILABLE`/`F2_PACKET_AVAILABLE` plumbing, and the shared `WL_D` pin's own IRQ level
when idle) - the generic inbound-delivery mechanism step 3f's own ioctl responses now use, and 3g's
async events will too. F2 writes parse SDPCM+ioctl requests generically (step 3f, `_write_wlan()`/
`_build_ioctl_success_response()`): validates the SDPCM header's `size`/`~size_com` check, and for
`CONTROL_HEADER` frames queues a success response echoing the request's own id
(`CDCF_IOC_ID_MASK`), a monotonically increasing `bus_data_credit`, and - critically, see
`_build_ioctl_success_response()`'s own docstring - a response payload zero-filled to the same
length as the request's own payload (not a bare zero-length ack, which left `SDPCM_GET` callers
reading their own unmodified request buffer, iovar-name prefix included, as if it were real
response data) - satisfies the bulk of the real `WLC_*`/iovar vocabulary bring-up sends without
per-ioctl content (step 3g still owns the handful - `WLC_SET_SSID`/join - that need real scripted
behavior instead of this generic zero-fill).
`DATA_HEADER` outbound Ethernet frames (step 4's NAT bridge) and anything malformed are silently
ignored, matching this class's existing no-op-rather-than-raise stance for unimplemented paths.
Real firmware/CLM downloads (step 3e) don't touch `WLAN_FUNCTION` at all -
`cyw43_download_resource()` writes through `BACKPLANE_FUNCTION` instead, already covered
generically by the F1 block-transfer path above.

**Async events + scripted scan/join (step 3g).** Two of `_write_wlan()`'s ioctl/iovar requests get
real scripted behavior on top of the generic ack every other request already gets: a `WLC_SET_VAR`
whose payload's iovar name is `"escan"` (`cyw43_ll_wifi_scan()`) and `WLC_SET_SSID` itself
(`cyw43_ll_wifi_join()`'s own tail end, `cyw43_ll.c:2051`, read in full for this step - the
ioctl/iovar sequence *before* it, `ampdu_ba_wsize`/`WLC_SET_WSEC`/the `bsscfg:sup_*` iovars/
`WLC_SET_INFRA`/`WLC_SET_AUTH`/`mfp`/`WLC_SET_WPA_AUTH`/the event-mask iovar, all still use the
plain generic ack - only the SSID write itself needs scripted follow-up). Both queue their normal
generic ack first, then queue one or more `ASYNCEVENT_HEADER` frames behind it via the same
`queue_rx_packet()` mechanism (step 3e) - now a real FIFO (`_rx_queue`), not a single slot, since
the ack and its follow-on event(s) must be delivered as separate, independently-framed reads, not
concatenated into one.

An async event is a fake *inbound* Ethernet frame wrapped in the same BDC header ordinary data
frames use (`sdpcm_process_rx_packet()`'s `ASYNCEVENT_HEADER` case, `cyw43_ll.c`): EtherType
`0x886c` at the conventional offset, the Broadcom OUI (`00:10:18`) right after it, then a
`bcmeth_hdr_t`-shaped 10-byte header (subtype/length/version/oui/usr_subtype - only the OUI is
actually checked). The real `cyw43_async_event_t` (`cyw43_ll.h`) starts right after that: 2
reserved bytes, `flags` (u16, big-endian on the wire), `event_type`/`status`/`reason` (u32 each,
big-endian), 30 reserved bytes, `interface`, 1 reserved byte, then a union whose only member is
`cyw43_ev_scan_result_t` for `CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` events - confirmed
field-by-field from `cyw43_ll_parse_async_event()`'s own alignment-fixup copy (it relocates the
struct 2 bytes to satisfy Cortex-M0's alignment fault, but the *byte offsets* it ends up reading
are identical to the un-relocated wire buffer's own offsets - `ev->flags` is wire byte 2,
`ev->event_type` is wire byte 4, and so on).

`escan`'s response needs *two* events, not the one the original plan estimated: one
`CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` carrying the populated `cyw43_ev_scan_result_t` (a
fixed fake AP, `RP2040PY-GUEST` - the rest of the shape mirrors Wokwi's own real captured AP
(`bssid=42:13:37:55:aa:01`, `channel=6`, `rssi=-87`, open/no-privacy - see
docs/records/0024-cyw43-protocol.md) since this project isn't Wokwi and shouldn't claim to be one
over the air), *then* a second
`CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_SUCCESS` completion event with no scan-result payload -
`cyw43_cb_process_async_event()` (`cyw43_ctrl.c`) only sets `wifi_scan_state = 2` (scan done) on
that `status == 0` event; without it `network_cyw43_scan()`'s own `mp_event_wait_ms()` loop
(`extmod/network_cyw43.c`) blocks for its full 10s timeout on every `scan()` call instead of
returning as soon as the one fake result is in.

`cyw43_ev_scan_result_t`'s bytes are also read through a second, richer struct
(`cyw43_ll_wifi_parse_scan_result()`'s own `_scan_result_t`/`cyw43_scan_result_internal_t`, an
overlapping reinterpretation of the *same* memory, not a separate structure) that computes
`auth_mode` from an RSN/WPA information-element scan and writes it back through the public struct -
`_build_scan_result_bytes()` builds bytes valid under *both* interpretations at once (real firmware
struct padding computed by hand for each field, not a natural dataclass layout): setting
`ie_length = 0` skips the IE scan entirely (open network, `auth_mode` computes to 0, matching the
real captured value this fake AP's shape is otherwise based on) without needing to fabricate real
802.11 IEs.

`WLC_SET_SSID`'s scripted sequence is `WLC_E_SET_SSID`/`_AUTH`/`_ASSOC`/`_PSK_SUP`/`_LINK`, all
`status=0` except `_PSK_SUP` (`status=6`, `WLC_SUP_KEYED` - `cyw43_ctrl.c`) and `_LINK` (`flags=1`,
"link up", `interface=CYW43_ITF_STA`) - sent unconditionally regardless of the auth type actually
requested (confirmed safe by reading `cyw43_cb_process_async_event()`: every one of these is a
plain OR into `self->wifi_join_state`'s bitmask, so an extra `_PSK_SUP` for an open network that
already got its `KEYED` bit set synchronously by `cyw43_wifi_join()` itself is a harmless no-op,
not a correctness risk - avoids needing to track auth type across the whole ioctl/iovar sequence
just to decide which events to send). **Ordering relative to the `WLC_SET_SSID` ack itself matters
and was confirmed by reading `cyw43_wifi_join()` (`cyw43_ctrl.c`), not assumed:** that function does
`self->wifi_join_state = WIFI_JOIN_STATE_ACTIVE;` - a plain *assignment*, not an OR - the instant
`cyw43_ll_wifi_join()` returns (i.e. the instant the SSID ack itself is received via
`cyw43_do_ioctl()`'s own response-wait loop). Any join event delivered *before* that ack would have
its bits wiped out by this assignment moments later, so the whole scripted sequence is queued
*behind* the ack, not interleaved with or ahead of it - picked up on whatever later poll drains the
queue (`cyw43_ll_process_packets()`/`cyw43_poll_func()`), the same way real firmware's own
over-the-air join handshake genuinely completes some time after the SSID command itself is
acknowledged, not synchronously with it.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.external.cyw43.nat import NatBridge
    from rp2040py.gpio_pin import GPIOPin
    from rp2040py.rp2040 import RP2040

__all__ = (
    "AIRC_RESET",
    "AI_IOCTRL_OFFSET",
    "AI_RESETCTRL_OFFSET",
    "ASYNCEVENT_HEADER",
    "BACKPLANE_ADDR_MASK",
    "BACKPLANE_FUNCTION",
    "BDC_HEADER_LEN",
    "BUS_FUNCTION",
    "CDCF_IOC_ID_MASK",
    "CDCF_IOC_ID_SHIFT",
    "CONTROL_HEADER",
    "CORE_SOCRAM",
    "CORE_WLAN_ARM",
    "CYW43_BACKPLANE_READ_PAD_LEN_BYTES",
    "CYW43_EV_ASSOC",
    "CYW43_EV_AUTH",
    "CYW43_EV_DISASSOC",
    "CYW43_EV_ESCAN_RESULT",
    "CYW43_EV_LINK",
    "CYW43_EV_PSK_SUP",
    "CYW43_EV_SET_SSID",
    "CYW43_STATUS_PARTIAL",
    "CYW43_STATUS_SUCCESS",
    "DATA_HEADER",
    "F2_PACKET_AVAILABLE",
    "IOCTL_HEADER_LEN",
    "SBSDIO_ALP_AVAIL",
    "SBSDIO_ALP_AVAIL_REQ",
    "SBSDIO_HT_AVAIL",
    "SBSDIO_HT_AVAIL_REQ",
    "SBSDIO_SB_ACCESS_2_4B_FLAG",
    "SBSDIO_SLPCSR_DEVICE_ON",
    "SBSDIO_SLPCSR_KEEP_SDIO_ON",
    "SDIO_BACKPLANE_ADDRESS_HIGH",
    "SDIO_BACKPLANE_ADDRESS_LOW",
    "SDIO_BACKPLANE_ADDRESS_MID",
    "SDIO_CHIP_CLOCK_CSR",
    "SDIO_FUNCTION2_WATERMARK",
    "SDIO_SLEEP_CSR",
    "SDPCM_HEADER_LEN",
    "SICF_CLOCK_EN",
    "SICF_CPUHALT",
    "SICF_FGC",
    "SOCSRAM_BASE_ADDRESS",
    "SPI_BUS_CONTROL",
    "SPI_FUNCTION1_INFO",
    "SPI_FUNCTION2_INFO",
    "SPI_FUNCTION3_INFO",
    "SPI_INTERRUPT_ENABLE_REGISTER",
    "SPI_INTERRUPT_REGISTER",
    "SPI_READ_TEST_REGISTER",
    "SPI_RESET_BP",
    "SPI_RESPONSE_DELAY",
    "SPI_RESP_DELAY_F0",
    "SPI_RESP_DELAY_F1",
    "SPI_RESP_DELAY_F2",
    "SPI_RESP_DELAY_F3",
    "SPI_STATUS_ENABLE",
    "SPI_STATUS_REGISTER",
    "STATUS_F2_PKT_AVAILABLE",
    "STATUS_F2_PKT_LEN_MASK",
    "STATUS_F2_PKT_LEN_SHIFT",
    "STATUS_F2_RX_READY",
    "STATUS_F3_RX_READY",
    "TEST_PATTERN",
    "WLAN_ARMCM3_BASE_ADDRESS",
    "WLAN_FUNCTION",
    "WLC_DISASSOC",
    "WLC_GET_VAR",
    "WLC_SET_SSID",
    "WLC_SET_VAR",
    "WORD_LENGTH_32",
    "WRAPPER_REGISTER_OFFSET",
    "GSPIBus",
)

# SDIO/gSPI standard F0/F1/F2 function numbering (cyw43_internal.h).
BUS_FUNCTION = 0
BACKPLANE_FUNCTION = 1
WLAN_FUNCTION = 2

# SPI_BUS_CONTROL bits (cyw43_spi.h) - only WORD_LENGTH_32 affects wire decode/encode (see module
# docstring's "Word-length/endian mode" section); the rest of the register is just storage.
WORD_LENGTH_32 = 0x01  # 0/1 = 16/32-bit word length
ENDIAN_BIG = 0x02  # 0/1 = little/big endian - real silicon's own compensation, not modeled here

# F0 bus register map (cyw43_spi.h) - byte addresses into GSPIBus's own `_f0` space.
SPI_BUS_CONTROL = 0x0000
SPI_RESPONSE_DELAY = 0x0001
SPI_STATUS_ENABLE = 0x0002
SPI_RESET_BP = 0x0003
SPI_INTERRUPT_REGISTER = 0x0004  # 16-bit
SPI_INTERRUPT_ENABLE_REGISTER = 0x0006  # 16-bit
SPI_STATUS_REGISTER = 0x0008  # 32-bit
SPI_FUNCTION1_INFO = 0x000C  # 16-bit
SPI_FUNCTION2_INFO = 0x000E  # 16-bit
SPI_FUNCTION3_INFO = 0x0010  # 16-bit
SPI_READ_TEST_REGISTER = 0x0014  # 32-bit, fixed - see TEST_PATTERN
SPI_RESP_DELAY_F0 = 0x001C
SPI_RESP_DELAY_F1 = 0x001D
SPI_RESP_DELAY_F2 = 0x001E
SPI_RESP_DELAY_F3 = 0x001F

_F0_SPACE_SIZE = 0x20  # covers every address above; out-of-range accesses are simply ignored/0.

# The fixed value real firmware polls SPI_READ_TEST_REGISTER for (up to 10 times,
# cyw43_ll_bus_init()) before trusting the bus is up at all.
TEST_PATTERN = 0xFEEDBEAD

# Real hardware needs extra turnaround time to actually fetch backplane-sourced data, so every
# BACKPLANE_FUNCTION *read* (both F1's own small register bank and windowed SB_ACCESS backplane
# memory - gated purely on `fn == BACKPLANE_FUNCTION`, confirmed from `cyw43_bus_pio_spi.c`'s
# `_cyw43_read_reg()`) clocks this many dummy padding bytes *before* the real answer; the driver
# reads the *last* word of the total response as the actual value, discarding everything before
# it. BUS_FUNCTION/WLAN_FUNCTION reads get none of this. We don't model the real delay, just the
# extra word count, so the driver's own fixed-offset "answer is the last word" indexing lines up -
# see `_start_response()`.
CYW43_BACKPLANE_READ_PAD_LEN_BYTES = 16

# SPI_STATUS_REGISTER bits GSPIBus actually sets by default (cyw43_spi.h has the full set - only
# the ones this module's default value uses are named here).
STATUS_F2_RX_READY = 0x00000020
STATUS_F3_RX_READY = 0x00000040

# SPI_INTERRUPT_REGISTER bit (16-bit, cyw43_spi.h) - the very first thing
# cyw43_ll_sdpcm_poll_device() checks before opening any SPI transaction at all
# (cyw43_ll.c:~1008: `if (!(spi_int & F2_PACKET_AVAILABLE)) return -1;`).
F2_PACKET_AVAILABLE = 0x0020

# SPI_STATUS_REGISTER bits (32-bit) - what the driver actually trusts for "is a packet ready, and
# how big" (cyw43_ll.c's SPI `cyw43_ll_sdpcm_poll_device()`: `bus_gspi_status = read(SPI_STATUS_REGISTER);
# bytes_pending = (bus_gspi_status >> 9) & 0x7FF`). Numerically the same bit position as
# F2_PACKET_AVAILABLE above - confirmed from source, not a typo here: the driver's own
# `cyw43_spi.h` names this field `GSPI_PACKET_AVAILABLE` when read from SPI_STATUS_REGISTER even
# though the constant is defined once, shared with the differently-scoped interrupt-register bit.
STATUS_F2_PKT_AVAILABLE = 0x00000100
STATUS_F2_PKT_LEN_MASK = 0x000FFE00
STATUS_F2_PKT_LEN_SHIFT = 9

# F1 (BACKPLANE_FUNCTION) register bank addresses (cyw43_ll.c's own #defines - not in cyw43_spi.h,
# these are internal to the driver, not part of the public API header).
SDIO_FUNCTION2_WATERMARK = 0x10008
SDIO_BACKPLANE_ADDRESS_LOW = 0x1000A
SDIO_BACKPLANE_ADDRESS_MID = 0x1000B
SDIO_BACKPLANE_ADDRESS_HIGH = 0x1000C
SDIO_CHIP_CLOCK_CSR = 0x1000E
SDIO_SLEEP_CSR = 0x1001F

# The register bank above is sparse and starts well above address 0 (unlike F0's) - `_f1` is
# indexed relative to this base rather than from 0, or every one of these addresses would fall
# straight past a from-zero array's bounds and silently no-op. Base is SDIO_FUNCTION2_WATERMARK,
# not SDIO_BACKPLANE_ADDRESS_LOW - it's 2 bytes lower and is real firmware's *lowest* F1 register
# access (a Bluetooth-gated write-then-read-back check in cyw43_ll_bus_init(), only present when
# the firmware build has Bluetooth compiled in - confirmed live via a real MicroPython Pico W
# boot: with the base at SDIO_BACKPLANE_ADDRESS_LOW, this address silently fell outside `_f1`'s
# bounds and read back 0 instead of what was just written, failing the driver's own equality
# check and aborting cyw43_ll_bus_init() immediately after the ALP handshake - long before
# firmware download ever starts. No special dispatch needed for this address itself, same
# generic byte-addressable storage as the rest of the bank.
_F1_REGISTER_BASE = SDIO_FUNCTION2_WATERMARK
_F1_REGISTER_SPACE_SIZE = SDIO_SLEEP_CSR - _F1_REGISTER_BASE + 1  # covers every address above.

# SDIO_CHIP_CLOCK_CSR bits: writing a *_REQ bit makes the corresponding *_AVAIL bit immediately
# readable - no real clock-startup latency to model (see module docstring / docs/
# CYW43_WIFI_BACKLOG.md's "Real bringup sequence beyond F0" for why this is safe to simplify).
SBSDIO_ALP_AVAIL_REQ = 0x08
SBSDIO_HT_AVAIL_REQ = 0x10
SBSDIO_ALP_AVAIL = 0x40
SBSDIO_HT_AVAIL = 0x80

# SDIO_SLEEP_CSR bits: writing KEEP_SDIO_ON makes DEVICE_ON immediately readable too (cyw43_kso_set()
# polls for both set together to conclude "awake").
SBSDIO_SLPCSR_KEEP_SDIO_ON = 0x01
SBSDIO_SLPCSR_DEVICE_ON = 0x02

# Backplane windowed-memory addressing (cyw43_set_backplane_window()/cyw43_read_backplane()/
# cyw43_write_backplane()) - an F1 address with this bit set (0x8000-0xffff) redirects into the
# separate `_backplane_memory` store instead of the small F1 register bank above.
SBSDIO_SB_ACCESS_2_4B_FLAG = 0x8000
BACKPLANE_ADDR_MASK = 0x7FFF

# ARM core reset/enable (disable_device_core()/reset_device_core()/device_core_is_up()) - real
# addresses within `_backplane_memory`, reached through the window above. No second CPU core needs
# emulating - see docs/CYW43_WIFI_BACKLOG.md's own note on why a register model that just
# remembers "reset" vs "clocked, not reset" is indistinguishable from a real running core to the
# host driver, which never talks to the WLAN core directly except through SDPCM (step 3f/g).
WLAN_ARMCM3_BASE_ADDRESS = 0x18003000
SOCSRAM_BASE_ADDRESS = 0x18004000
WRAPPER_REGISTER_OFFSET = 0x100000
CORE_WLAN_ARM = WLAN_ARMCM3_BASE_ADDRESS + WRAPPER_REGISTER_OFFSET
CORE_SOCRAM = SOCSRAM_BASE_ADDRESS + WRAPPER_REGISTER_OFFSET
AI_IOCTRL_OFFSET = 0x408
AI_RESETCTRL_OFFSET = 0x800
SICF_CLOCK_EN = 0x0001
SICF_FGC = 0x0002
SICF_CPUHALT = 0x0020
AIRC_RESET = 1

# SDPCM + ioctl framing (cyw43_ll.c - internal to the driver, not in cyw43_spi.h) - step 3f.
# `struct sdpcm_header_t` is 9 plain uint8/uint16 fields (no uint32 members forcing extra
# alignment), so its real size is the naive field sum, not a rounder-sounding guess: 2+2+1+1+1+1+
# 1+1+2 = 12 bytes - confirmed against cyw43_ll.c's own field list, not assumed.
SDPCM_HEADER_LEN = 12
# `struct ioctl_header_t` is 4 uint32 fields (cmd/len/flags/status) = 16 bytes.
IOCTL_HEADER_LEN = 16
# sdpcm_header_t.channel_and_flags low nibble - CONTROL_HEADER (ioctl request/response) and
# DATA_HEADER (outbound Ethernet, step 4's NAT bridge - docs/records/0048-cyw43-nat-reflector.md)
# are both handled by _write_wlan(); ASYNCEVENT_HEADER (step 3g, chip-to-host only) isn't needed on
# this side.
CONTROL_HEADER = 0
DATA_HEADER = 2
# BDC (Broadcom Device Control) header - precedes the raw Ethernet frame in every DATA_HEADER
# payload, both directions (cyw43_ll.c's sdpcm_bdc_header_t: flags/priority/flags2/data_offset).
BDC_HEADER_LEN = 4
# ioctl_header_t.flags: the requesting id lives in the top 16 bits - sdpcm_process_rx_packet()
# (cyw43_ll.c) drops any response whose echoed id doesn't match the driver's own last-sent one.
CDCF_IOC_ID_SHIFT = 16
CDCF_IOC_ID_MASK = 0xFFFF0000

# Async events + scripted scan/join (step 3g) - cyw43_ll.c's own #defines (WLC_* ioctl cmd
# numbers, cyw43_ll.h's CYW43_EV_*/CYW43_STATUS_* event constants).
WLC_SET_SSID = 26  # cyw43_ll_wifi_join()'s own final ioctl, past the generic-ack-only prefix.
# cyw43_wifi_leave() -> cyw43_ioctl(CYW43_IOCTL_SET_DISASSOC = 0x69). cyw43_ll_ioctl() splits that
# as `cmd & 1 ? SET : GET` / `cmd >> 1`, so 0x69 is a SET of WLC command 0x34 - this one.
WLC_DISASSOC = 52
WLC_SET_VAR = 263  # iovar dispatch (name-prefixed payload) - only "escan" gets scripted follow-up.
WLC_GET_VAR = 262  # GET-side counterpart of WLC_SET_VAR - only "cur_etheraddr" (step 4a) is scripted.
ASYNCEVENT_HEADER = 1  # sdpcm_header_t.channel_and_flags low nibble - chip-to-host only.

# Step 4a (docs/records/0048-cyw43-nat-reflector.md) - a fixed, plausible MAC answered for the
# cur_etheraddr GET (cyw43_ll_wifi_get_mac() only reads the first 6 bytes of the response payload).
_GUEST_MAC = b"\x00\x10\x18\x00\x00\x02"

CYW43_EV_SET_SSID = 0
CYW43_EV_AUTH = 3
CYW43_EV_ASSOC = 7
CYW43_EV_PSK_SUP = 46
CYW43_EV_LINK = 16
CYW43_EV_ESCAN_RESULT = 69
CYW43_EV_DISASSOC = 11

CYW43_STATUS_SUCCESS = 0
CYW43_STATUS_PARTIAL = 8

# Fixed fake AP (module docstring's "Async events + scripted scan/join" section) - this project's
# own SSID (not Wokwi's - this isn't Wokwi's emulator), but the rest of the shape mirrors Wokwi's
# own real captured AP (docs/records/0024-cyw43-protocol.md): open/no-privacy, so
# cyw43_ll_wifi_parse_scan_result()'s computed auth_mode comes out 0 without needing real 802.11
# IEs (see _build_scan_result_bytes()).
_FAKE_AP_SSID = b"RP2040PY-GUEST"
_FAKE_AP_BSSID = b"\x42\x13\x37\x55\xaa\x01"
_FAKE_AP_CHANNEL = 6
_FAKE_AP_RSSI = -87

# Async event Ethernet framing (sdpcm_process_rx_packet()'s ASYNCEVENT_HEADER case) - only the
# EtherType and OUI are actually checked by the driver; dest/src MAC and the bcmeth_hdr_t's own
# subtype/version/usr_subtype fields are never inspected, so these are realistic placeholders, not
# derived from a specific real capture.
_EVENT_ETHERTYPE = b"\x88\x6c"
_BROADCOM_OUI = b"\x00\x10\x18"
_EVENT_DEST_MAC = b"\xff\xff\xff\xff\xff\xff"
_EVENT_SRC_MAC = b"\x00\x10\x18\x00\x00\x00"


@dataclass(frozen=True)
class GSPICommand:
    """A decoded `make_cmd()` header - see `_decode_header()`."""

    write: bool
    increment: bool
    function: int
    address: int
    size: int  # bytes


def _swap_halves(word: int) -> int:
    """16-bit word-length mode's wire transform (see module docstring) - swaps the upper/lower
    16-bit halves of a 32-bit word, byte order within each half unchanged. Self-inverse."""
    return ((word & 0xFFFF) << 16) | (word >> 16)


def _swap_bytes(word: int) -> int:
    """32-bit word-length mode's wire transform (see module docstring) - full 32-bit byte
    reversal, matching the DMA engine's own unconditional bswap. Self-inverse."""
    return ((word & 0xFF) << 24) | ((word & 0xFF00) << 8) | ((word >> 8) & 0xFF00) | ((word >> 24) & 0xFF)


def _word_count(size: int) -> int:
    """How many 32-bit wire words a `size`-byte value/block spans - at least one even for a
    sub-word register access, since gSPI is word-oriented (see module docstring's "Wire framing"):
    a 1-3 byte register poke still rides one full 32-bit word, only its low bytes meaningful."""
    return max(1, (size + 3) // 4)


def _words_to_value(words: list[int], size: int) -> int:
    """Reassembles `size` meaningful bytes (address-ascending, the same little-endian-byte-stream
    convention `read_register()`/`write_register()` already use for size<=4) from the sequence of
    decoded (word-transform already undone) wire words a multi-word write accumulated. Inverse of
    `_value_to_words()`."""
    raw = b"".join(word.to_bytes(4, "little") for word in words)
    return int.from_bytes(raw[:size], "little")


def _value_to_words(value: int, size: int) -> list[int]:
    """Splits a `size`-byte register/block value into the 32-bit wire words a read response
    streams out, one word per 4 address-ascending bytes - zero-padded if `size` isn't a multiple
    of 4 (real block transfers always are, per `cyw43_bus_pio_spi.c`'s own `assert(!(len & 3))`,
    but small sub-word register reads aren't and still ride one full word). Inverse of
    `_words_to_value()`."""
    word_count = _word_count(size)
    raw = value.to_bytes(size, "little").ljust(word_count * 4, b"\x00")
    return [int.from_bytes(raw[i * 4 : i * 4 + 4], "little") for i in range(word_count)]


def _decode_header(word: int) -> GSPICommand:
    return GSPICommand(
        write=bool(word & 0x8000_0000),
        increment=bool(word & 0x4000_0000),
        function=(word >> 28) & 0x3,
        address=(word >> 11) & 0x1FFFF,
        size=word & 0x7FF,
    )


class GSPIBus:
    """One CYW43439's worth of gSPI bus state: the F0 register block plus the wire-level word/
    header decode driving it. `attach_gpio()` wires it to real `GPIOPin`s; the decode logic itself
    (`_on_cs_change`/`_on_clock_edge`) takes plain booleans so it's testable without any GPIO
    involved at all.
    """

    def __init__(self) -> None:
        self._f0 = bytearray(_F0_SPACE_SIZE)
        self._write_f0(SPI_STATUS_REGISTER, 4, STATUS_F2_RX_READY | STATUS_F3_RX_READY)

        self._f1 = bytearray(_F1_REGISTER_SPACE_SIZE)
        self._backplane_window = 0
        # Sparse - the addressable range is a full 32 bits (core-control registers alone span
        # 0x18003000-0x18104000+), a flat bytearray would be wasteful for the handful of distinct
        # addresses anything actually touches. Missing addresses read as 0.
        self._backplane_memory: dict[int, int] = {}
        # Real silicon holds both cores in reset at power-on - disable_device_core() (cyw43_ll.c)
        # starts by *checking* this is already true (not setting it itself) before proceeding, so
        # it must be the state before any core-reset register write ever happens.
        for core in (CORE_WLAN_ARM, CORE_SOCRAM):
            self._backplane_memory[core + AI_RESETCTRL_OFFSET] = AIRC_RESET

        # gSPI hardware word-length mode - real silicon's own power-on default (see module
        # docstring's "Word-length/endian mode"). Flips to True the instant a SPI_BUS_CONTROL
        # write's value has WORD_LENGTH_32 set (_write_f0()), same as real hardware.
        self._word_length_32 = False

        self._selected = False
        self._shift_reg = 0
        self._bits_in_word = 0
        self._pending_command: GSPICommand | None = None
        # Accumulates the decoded (word-transform already undone) value words of a write still in
        # progress - a block write (step 3a) spans several 32-bit words, not just one.
        self._pending_write_words: list[int] = []
        self._response_bytes = b""
        self._response_bit_index = 0
        # F2 (WLAN_FUNCTION) inbound packet queue (step 3e) - staged by queue_rx_packet(), drained
        # by _read_wlan(). Empty means "nothing pending", matching STATUS_F2_PKT_AVAILABLE unset.
        # _rx_packet is the one frame currently being read out over F2; _rx_queue holds later
        # frames not yet visible on the bus (step 3g - a scripted ioctl response followed by one or
        # more async events must be delivered as separate, independently-framed reads, not
        # concatenated - see queue_rx_packet()). Invariant: _rx_queue is only ever non-empty while
        # _rx_packet is too (queue_rx_packet() only appends instead of activating immediately when
        # something is already active), so _rx_packet alone remains a correct "anything pending at
        # all" check wherever that's needed (e.g. _on_cs_change()'s IRQ-pin reflection).
        self._rx_packet = b""
        self._rx_queue: list[bytes] = []
        # SDPCM bus_data_credit (step 3f) - matches the real driver's own initial
        # wwd_sdpcm_last_bus_data_credit (cyw43_ll_init(), cyw43_ll.c). Incremented once per ioctl
        # response we send (_build_ioctl_success_response()) - see that method for why it must
        # stay strictly ahead of the driver's own send count.
        self._bus_data_credit = 1
        # SDIO_CHIP_CLOCK_CSR's ALP/HT availability, sticky once achieved (see _write_f1()'s
        # SDIO_CHIP_CLOCK_CSR branch and _maybe_mark_ht_available() for why: real availability
        # reflects actual clock-lock state, not the last-written request byte, and HT specifically
        # can become available with no *_REQ write at all - see _maybe_mark_ht_available()).
        self._alp_available = False
        self._ht_available = False
        # Set by attach_gpio() - the pin this bus drives its response bits onto/samples host bits
        # from. None until attached (mirrors every other ExternalDevice-adjacent component here).
        self._data_pin: GPIOPin | None = None
        # Set by Cyw43439.attach() (step 4, docs/records/0048-cyw43-nat-reflector.md). A public
        # attribute, not a bare private one poked from outside - None here preserves today's exact
        # drop-the-payload DATA_HEADER behavior for any caller that constructs a bare GSPIBus().
        self.nat_bridge: NatBridge | None = None

    def power_off(self) -> None:
        """Drop the chip back to its power-on state, as pulling `WL_ON` low does on real hardware.

        Every field below is restored to exactly what `__init__` sets, and the *wiring* is not
        touched: `_data_pin` (the pin this bus samples/drives) and `nat_bridge` survive, because
        cutting the chip's regulator does not unsolder it from the RP2040 or take the host's
        network away. Same "registers, not wiring" rule the chip-level resets follow
        (docs/records/0089-one-reset-for-every-trigger.md, Phase 5).

        Why this exists at all: 0089's D7 says an external chip only ever sees a reset through the
        GPIO firmware drives, and `WL_ON` is that GPIO. Before this, a chip reset released the pad
        - so the driver's own re-init sequence started over - while this bus still held every
        register, credit and clock-availability flag from the *previous* session, and CircuitPython
        on a Pico W could never bring WiFi back up after a reset (measured: 0089's Appendix,
        point 5).
        """
        self._f0 = bytearray(_F0_SPACE_SIZE)
        self._write_f0(SPI_STATUS_REGISTER, 4, STATUS_F2_RX_READY | STATUS_F3_RX_READY)
        self._f1 = bytearray(_F1_REGISTER_SPACE_SIZE)
        self._backplane_window = 0
        self._backplane_memory = {}
        for core in (CORE_WLAN_ARM, CORE_SOCRAM):
            self._backplane_memory[core + AI_RESETCTRL_OFFSET] = AIRC_RESET
        self._word_length_32 = False
        self._selected = False
        self._shift_reg = 0
        self._bits_in_word = 0
        self._pending_command = None
        self._pending_write_words = []
        self._response_bytes = b""
        self._response_bit_index = 0
        self._rx_packet = b""
        self._rx_queue = []
        self._bus_data_credit = 1
        self._alp_available = False
        self._ht_available = False

    # -- wire word-length/endian transform ------------------------------------------------------

    def _word(self, word: int) -> int:
        """Applies (or undoes - it's self-inverse) the current word-length mode's wire transform
        (see module docstring's "Word-length/endian mode") - the *only* place this module models
        the gSPI interface's own byte/word ordering quirks, used both to decode an incoming 32-bit
        wire word into its natural `make_cmd()`/value meaning and to encode an outgoing register
        value back onto the wire the same way real silicon would."""
        return _swap_bytes(word) if self._word_length_32 else _swap_halves(word)

    # -- F0 register space ---------------------------------------------------------------------

    def _read_f0(self, addr: int, size: int) -> int:
        if addr == SPI_READ_TEST_REGISTER and size == 4:
            return TEST_PATTERN
        if addr < 0 or addr + size > len(self._f0):
            return 0
        return int.from_bytes(bytes(self._f0[addr : addr + size]), "little")

    def _write_f0(self, addr: int, size: int, value: int) -> None:
        if addr == SPI_BUS_CONTROL and value & WORD_LENGTH_32:
            self._word_length_32 = True
        if addr < 0 or addr + size > len(self._f0):
            return
        self._f0[addr : addr + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    # -- F1 register bank + windowed backplane memory -------------------------------------------

    def _read_backplane_memory(self, addr: int, size: int) -> int:
        return int.from_bytes(bytes(self._backplane_memory.get(addr + i, 0) for i in range(size)), "little")

    def _write_backplane_memory(self, addr: int, size: int, value: int) -> None:
        for i, byte in enumerate((value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")):
            self._backplane_memory[addr + i] = byte

    def _set_backplane_window_byte(self, shift: int, byte: int) -> None:
        self._backplane_window = (self._backplane_window & ~(0xFF << shift)) | (byte << shift)

    def _read_f1(self, addr: int, size: int) -> int:
        offset = addr - _F1_REGISTER_BASE
        if offset < 0 or offset + size > len(self._f1):
            return 0
        value = int.from_bytes(bytes(self._f1[offset : offset + size]), "little")
        if addr == SDIO_CHIP_CLOCK_CSR:
            # _alp_available/_ht_available can flip true with no write to this register at all
            # (_maybe_mark_ht_available() - the WLAN ARM core coming up) - _write_f1()'s own
            # OR-in only refreshes the stored byte as a side effect of a write to *this* address,
            # so a read that happens without one in between must recompute from the sticky flags
            # too, or a real driver's read-only poll loop would never see the bit.
            value |= (SBSDIO_ALP_AVAIL if self._alp_available else 0) | (SBSDIO_HT_AVAIL if self._ht_available else 0)
        return value

    def _write_f1(self, addr: int, size: int, value: int) -> None:
        if addr == SDIO_CHIP_CLOCK_CSR:
            # Writing a *_REQ bit makes the corresponding *_AVAIL bit immediately readable - see
            # module docstring for why modeling real clock-startup latency isn't needed here.
            # Sticky (self._alp_available/_ht_available - _read_f1() re-applies these on every
            # read too, not just here), not just OR'd into this one write's value: real
            # availability reflects actual clock-lock state, which doesn't drop the instant the
            # request byte is cleared - cyw43_ll_bus_init() clears SBSDIO_ALP_AVAIL_REQ right
            # after achieving ALP (see alp_set: in that function), and a non-sticky model would
            # silently un-set SBSDIO_ALP_AVAIL on that very write. _ht_available can also flip
            # true with no write to this register at all - see _maybe_mark_ht_available() - which
            # is exactly why _read_f1() needs its own copy of this OR-in, not just this write path.
            if value & SBSDIO_ALP_AVAIL_REQ:
                self._alp_available = True
            if value & SBSDIO_HT_AVAIL_REQ:
                self._ht_available = True
            value |= (SBSDIO_ALP_AVAIL if self._alp_available else 0) | (SBSDIO_HT_AVAIL if self._ht_available else 0)
        elif addr == SDIO_SLEEP_CSR:
            # cyw43_kso_set() polls for KEEP_SDIO_ON *and* DEVICE_ON to read back together.
            if value & SBSDIO_SLPCSR_KEEP_SDIO_ON:
                value |= SBSDIO_SLPCSR_DEVICE_ON
            else:
                value &= ~SBSDIO_SLPCSR_DEVICE_ON
        elif addr == SDIO_BACKPLANE_ADDRESS_LOW:
            self._set_backplane_window_byte(8, value & 0xFF)
        elif addr == SDIO_BACKPLANE_ADDRESS_MID:
            self._set_backplane_window_byte(16, value & 0xFF)
        elif addr == SDIO_BACKPLANE_ADDRESS_HIGH:
            self._set_backplane_window_byte(24, value & 0xFF)
        offset = addr - _F1_REGISTER_BASE
        if offset < 0 or offset + size > len(self._f1):
            return
        self._f1[offset : offset + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    # -- F2 (WLAN_FUNCTION) inbound packet queue (step 3e) --------------------------------------

    def queue_rx_packet(self, data: bytes) -> None:
        """Stages `data` as an inbound F2 packet - the generic delivery mechanism step 3f/3g's
        SDPCM ioctl responses and async events use, not anything protocol-specific itself. If
        nothing is currently pending, activates `data` immediately (see `_activate_rx_packet()`);
        otherwise appends it to `_rx_queue` to be activated once every earlier packet has been
        fully read out - step 3g's scripted responses (an ioctl ack followed by one or more async
        events) call this several times in a row for exactly this reason, and each call must stay
        a distinct, separately-framed F2 read, not get concatenated into one."""
        if self._rx_packet:
            self._rx_queue.append(data)
            return
        self._activate_rx_packet(data)

    def queue_rx_ethernet_frame(self, ethernet_frame: bytes, *, interface: int = 0) -> None:
        """The `DATA_HEADER` counterpart to `queue_rx_packet()` - `nat_bridge`'s (step 4) only
        entry point back into this bus, so `nat.py` never has to touch SDPCM/BDC framing itself."""
        self.queue_rx_packet(self._build_data_frame(ethernet_frame, interface=interface))

    def _build_data_frame(self, ethernet_frame: bytes, *, interface: int = 0) -> bytes:
        """Inbound `DATA_HEADER` envelope for a synthesized reply (ARP reply, DHCP OFFER/ACK, TCP
        SYN-ACK/data/FIN) - same 12-byte SDPCM header + BDC-header shape `_build_async_event()`
        uses for its own inbound frames, but `kind=DATA_HEADER` and `header_length=
        SDPCM_HEADER_LEN+2=14` (the 2-byte pad real `DATA_HEADER` frames carry, that
        `ASYNCEVENT_HEADER` frames don't - see docs/records/0045-cyw43-nat-libslirp-cython.md's
        "Resolved research finding" for the derivation, confirmed against `cyw43_ll.c`'s own
        `sdpcm_process_rx_packet()` DATA_HEADER case)."""
        bdc_header = bytes([0x20, 0, interface, 0])  # flags/priority/flags2(itf)/data_offset
        payload = bytes(2) + bdc_header + ethernet_frame  # 2-byte pad, then BDC header, then the frame
        size = SDPCM_HEADER_LEN + len(payload)
        self._bus_data_credit = (self._bus_data_credit + 1) & 0xFF
        sdpcm_header = (
            size.to_bytes(2, "little")
            + (~size & 0xFFFF).to_bytes(2, "little")
            + bytes([0, DATA_HEADER, 0, SDPCM_HEADER_LEN + 2, 0, self._bus_data_credit])
            + bytes(2)  # reserved
        )
        return sdpcm_header + payload

    def _activate_rx_packet(self, data: bytes) -> None:
        """Makes `data` the current F2-visible packet: sets `SPI_STATUS_REGISTER`'s
        `STATUS_F2_PKT_AVAILABLE` bit + length field and `SPI_INTERRUPT_REGISTER`'s
        `F2_PACKET_AVAILABLE` bit - what real firmware's `cyw43_ll_sdpcm_poll_device()` actually
        polls for (cyw43_ll.c, SPI variant) - and, if CS is currently deasserted, immediately
        raises the shared `WL_D` pin's own IRQ level too, since that's a separate GPIO-level signal
        (`cyw43_cb_read_host_interrupt_pin()`, real hardware's `CYW43_PIN_WL_HOST_WAKE`) a real
        driver's interrupt handler can notice without any SPI transaction happening at all -
        `_on_cs_change()` alone wouldn't reflect a packet queued while already idle."""
        self._rx_packet = data
        status = self._read_f0(SPI_STATUS_REGISTER, 4) | STATUS_F2_PKT_AVAILABLE
        status = (status & ~STATUS_F2_PKT_LEN_MASK) | ((len(data) << STATUS_F2_PKT_LEN_SHIFT) & STATUS_F2_PKT_LEN_MASK)
        self._write_f0(SPI_STATUS_REGISTER, 4, status)
        self._write_f0(SPI_INTERRUPT_REGISTER, 2, self._read_f0(SPI_INTERRUPT_REGISTER, 2) | F2_PACKET_AVAILABLE)
        if not self._selected and self._data_pin is not None:
            self._data_pin.set_input_value(bool(data))

    def _read_wlan(self, size: int) -> int:
        """F2 reads are always against a fixed FIFO address on real hardware - `cyw43_read_bytes()`
        always passes `addr=0` (see `cyw43_ll_sdpcm_poll_device()`), so `addr` itself is unused
        here, mirroring that. Delivers up to `size` bytes of whatever `queue_rx_packet()` staged,
        consuming them; once the current packet is empty, activates the next queued one (step 3g)
        if there is one, keeping `STATUS_F2_PKT_AVAILABLE`/`F2_PACKET_AVAILABLE`/the shared IRQ
        pin continuously asserted across the transition - only once `_rx_queue` is also empty do
        those get cleared. The shared IRQ pin itself drops on the next `_on_cs_change()` deselect,
        which by then sees an empty `_rx_packet`, so nothing needs touching here for that part."""
        data, self._rx_packet = self._rx_packet[:size], self._rx_packet[size:]
        if not self._rx_packet:
            if self._rx_queue:
                self._activate_rx_packet(self._rx_queue.pop(0))
            else:
                status = self._read_f0(SPI_STATUS_REGISTER, 4) & ~(STATUS_F2_PKT_AVAILABLE | STATUS_F2_PKT_LEN_MASK)
                self._write_f0(SPI_STATUS_REGISTER, 4, status)
                self._write_f0(
                    SPI_INTERRUPT_REGISTER, 2, self._read_f0(SPI_INTERRUPT_REGISTER, 2) & ~F2_PACKET_AVAILABLE
                )
        return int.from_bytes(data, "little")

    # -- F2 (WLAN_FUNCTION) outbound SDPCM/ioctl (step 3f) --------------------------------------

    def _build_ioctl_success_response(
        self, request_id: int, response_len: int = 0, *, response_prefix: bytes = b""
    ) -> bytes:
        """Generic "success" SDPCM+ioctl response, its payload `response_len` bytes of zeros -
        satisfies the bulk of the real `WLC_*`/iovar vocabulary `cyw43_ll_wifi_on()`/
        `cyw43_ll_wifi_join()` send during bring-up without needing per-ioctl content (step 3g is
        where the handful that actually need scripted behavior - `WLC_SET_SSID`/join - get real
        responses instead of this).

        `response_prefix` (step 4a) overlays its bytes onto the start of the otherwise-zero
        payload, still zero-padded/truncated to exactly `response_len` - used for `cur_etheraddr`
        GET responses, whose only real content is a 6-byte MAC at offset 0
        (`cyw43_ll_wifi_get_mac()`'s own `memcpy(addr, buf, 6)`). Keeps the zero-fill-to-
        `response_len` correctness contract intact either way (see the next paragraph).

        **Must not stay zero-length unconditionally** (an earlier version of this did) - found by
        booting real firmware (2026-08-13), not by reading source alone. `cyw43_do_ioctl()`
        (`cyw43_ll.c`) does `memmove(buf, res_buf, min(len, res_len))` on every response
        regardless of GET/SET, where `res_len` is this response's own payload length
        (`sdpcm_process_rx_packet()` computes it as `header->size` minus the fixed header
        overhead - confirmed directly from that function) and `buf` is the *same* buffer the
        request itself was built in. A zero-length response leaves that buffer completely
        untouched by the `memmove()`, so a `SDPCM_GET` caller reads back its own *request*
        content as if it were the answer. For `cyw43_ll_wifi_update_multicast_filter()`'s
        `mcast_list` query this manifested as `n = cyw43_get_le32(buf)` reading the literal ASCII
        bytes of the iovar name `"mcast_list"` itself (the first 4 bytes of the un-overwritten
        request buffer) as a ~1.9 billion-entry loop count, which then walked `memcmp()` off the
        end of SRAM for the remainder of the run - looked exactly like a raw
        interpretation-throughput ceiling (CPU genuinely executing varied, real instructions the
        whole time) until live-traced with register-level detail against a symbol-matched build;
        see docs/records/0027-cyw43-wifi.md's dated entry for the full derivation.

        **The response payload must be freshly zeroed bytes, not the request's own payload echoed
        back verbatim** - a first attempt at this fix tried the latter and did not resolve the
        bug: real iovar responses overwrite `buf` starting at offset 0 with *only* the answer
        value, with no iovar-name prefix (confirmed from `cyw43_ll_wifi_get_mac()`, which reads
        its answer via `memcpy(addr, buf, 6)` - straight from offset 0, even though the request
        buffer it reuses starts with the 14-byte string `"cur_etheraddr\\0"`) - so an echo of the
        verbatim request bytes reproduces the exact same `"mcast_list"`-as-garbage-length bug,
        just via a different code path (a real, confirmed dead end, not a hypothetical one).
        Zero-filling instead correctly overwrites *any* iovar-name prefix a GET request happens
        to have, at any offset, without needing to know that prefix's length - `response_len`
        equal to the request's own payload length is enough to guarantee the whole thing gets
        overwritten (`min(len, res_len)` in the driver's own copy is then just `len`), and an
        all-zero answer is a safe, generic "empty"/"unset" value for every `WLC_GET_VAR` query
        this bus answers generically during bring-up. Also a no-op for `SDPCM_SET` calls (their
        response payload isn't meaningfully used beyond this same `memmove()`, and a `SET`
        request's `len` here is usually 0 anyway).

        Two more things `sdpcm_process_rx_packet()`/`cyw43_sdpcm_send_common()` (cyw43_ll.c)
        actually enforce on whatever we send back, both handled here:
        - The response's `ioctl_header_t.flags` must echo the request's own id
          (`CDCF_IOC_ID_MASK`) - `sdpcm_process_rx_packet()` silently drops anything whose id
          doesn't match the driver's last-sent one.
        - `bus_data_credit` must end up strictly ahead of the driver's own send count, or
          `cyw43_sdpcm_send_common()`'s STALL check blocks the *next* host send forever - the
          driver increments its send count by one per send, so incrementing this by one per
          response keeps exactly one ahead, matching this chip model's synchronous
          one-request-one-response shape. `wireless_flow_control` must also stay 0 - any nonzero
          value has the same stalling effect, unconditionally, on every later send."""
        payload = (response_prefix + bytes(response_len))[:response_len]
        ioctl_header = (
            (0).to_bytes(4, "little")  # cmd - not inspected by the driver on a response
            + response_len.to_bytes(4, "little")  # len (output length)
            + ((request_id << CDCF_IOC_ID_SHIFT) & CDCF_IOC_ID_MASK).to_bytes(4, "little")  # flags
            + (0).to_bytes(4, "little")  # status = success
        )
        size = SDPCM_HEADER_LEN + len(ioctl_header) + len(payload)
        self._bus_data_credit = (self._bus_data_credit + 1) & 0xFF
        sdpcm_header = (
            size.to_bytes(2, "little")
            + (~size & 0xFFFF).to_bytes(2, "little")
            # sequence(0, unchecked on receive) / channel_and_flags / next_length(0, control-only)
            # / header_length / wireless_flow_control(0) / bus_data_credit:
            + bytes([0, CONTROL_HEADER, 0, SDPCM_HEADER_LEN, 0, self._bus_data_credit])
            + bytes(2)  # reserved
        )
        return sdpcm_header + ioctl_header + payload

    def _build_async_event(
        self,
        event_type: int,
        status: int,
        *,
        reason: int = 0,
        interface: int = 0,
        flags: int = 0,
        scan_result: bytes = b"",
    ) -> bytes:
        """A scripted `ASYNCEVENT_HEADER` frame (step 3g) - see the module docstring's "Async
        events + scripted scan/join" section for the full byte-offset derivation. `scan_result`,
        when given, must already be `_build_scan_result_bytes()`'s own shape - only
        `CYW43_EV_ESCAN_RESULT`/`CYW43_STATUS_PARTIAL` events carry one."""
        event_core = (
            bytes(2)  # 2 reserved bytes - never read by cyw43_ll_parse_async_event()
            + flags.to_bytes(2, "big")
            + event_type.to_bytes(4, "big")
            + status.to_bytes(4, "big")
            + reason.to_bytes(4, "big")
            + bytes(30)  # reserved
            + bytes([interface, 0])  # interface, then 1 reserved byte
            + scan_result
        )
        bcmeth_header = (
            (0x8000).to_bytes(2, "big")  # subtype: BCMILCP_SUBTYPE_VENDOR_LONG
            + len(event_core).to_bytes(2, "big")
            + bytes([0])  # version
            + _BROADCOM_OUI
            + (1).to_bytes(2, "big")  # usr_subtype: BCMILCP_BCM_SUBTYPE_EVENT
        )
        ethernet_frame = _EVENT_DEST_MAC + _EVENT_SRC_MAC + _EVENT_ETHERTYPE + bcmeth_header + event_core
        bdc_header = bytes([0x20, 0, interface, 0])  # flags/priority/flags2(itf)/data_offset
        payload = bdc_header + ethernet_frame
        size = SDPCM_HEADER_LEN + len(payload)
        self._bus_data_credit = (self._bus_data_credit + 1) & 0xFF
        sdpcm_header = (
            size.to_bytes(2, "little")
            + (~size & 0xFFFF).to_bytes(2, "little")
            + bytes([0, ASYNCEVENT_HEADER, 0, SDPCM_HEADER_LEN, 0, self._bus_data_credit])
            + bytes(2)  # reserved
        )
        return sdpcm_header + payload

    def _build_scan_result_bytes(self) -> bytes:
        """The fixed fake AP's `cyw43_ev_scan_result_t` payload (module docstring's "Async events
        + scripted scan/join" section has the full field-by-field derivation, including why this
        must also satisfy `cyw43_ll_wifi_parse_scan_result()`'s own overlapping
        `_scan_result_t`/`cyw43_scan_result_internal_t` reinterpretation of the same bytes).
        Everything not set explicitly stays zero, which is exactly what's needed:
        `bss.capability`/`bss.ie_offset`/`bss.ie_length` all zero means an open network (no RSN/WPA
        IE, no privacy bit) - `auth_mode` computes to 0, matching the real captured value this fake
        AP's shape is otherwise based on - without needing to fabricate real 802.11 information
        elements."""
        buf = bytearray(140)  # 12-byte outer header + 128-byte cyw43_scan_result_internal_t
        buf[16:20] = (128).to_bytes(4, "little")  # bss.length (validates ie_offset+ie_length<=it)
        buf[20:26] = _FAKE_AP_BSSID  # bss.bssid == the public struct's own bssid offset
        buf[30] = len(_FAKE_AP_SSID)  # bss.ssid_len == the public struct's own ssid_len offset
        buf[31 : 31 + len(_FAKE_AP_SSID)] = _FAKE_AP_SSID  # bss.ssid == the public struct's ssid
        buf[84:86] = _FAKE_AP_CHANNEL.to_bytes(2, "little")  # bss.chanspec == public "channel"
        buf[90:92] = _FAKE_AP_RSSI.to_bytes(2, "little", signed=True)  # bss.rssi == public "rssi"
        return bytes(buf)

    def _queue_scan_events(self) -> None:
        """`escan`'s response (step 3g) - see the module docstring for why this is two events, not
        the one events originally estimated: a `CYW43_STATUS_PARTIAL` result carrying the fixed
        fake AP, then a `CYW43_STATUS_SUCCESS` completion with no scan-result payload, needed to
        end `network_cyw43_scan()`'s own wait loop promptly instead of blocking for its full
        timeout."""
        self.queue_rx_packet(
            self._build_async_event(
                CYW43_EV_ESCAN_RESULT, CYW43_STATUS_PARTIAL, scan_result=self._build_scan_result_bytes()
            )
        )
        self.queue_rx_packet(self._build_async_event(CYW43_EV_ESCAN_RESULT, CYW43_STATUS_SUCCESS))

    def _queue_join_events(self) -> None:
        """`WLC_SET_SSID`'s scripted `WLC_E_*` sequence (step 3g) - see the module docstring for
        why this fires unconditionally (regardless of the auth type actually requested) and why it
        must queue *behind* the SSID ack rather than ahead of or interleaved with it."""
        for event_type, status, flags in (
            (CYW43_EV_SET_SSID, CYW43_STATUS_SUCCESS, 0),
            (CYW43_EV_AUTH, CYW43_STATUS_SUCCESS, 0),
            (CYW43_EV_ASSOC, CYW43_STATUS_SUCCESS, 0),
            (CYW43_EV_PSK_SUP, 6, 0),  # WLC_SUP_KEYED (cyw43_ctrl.c)
            (CYW43_EV_LINK, CYW43_STATUS_SUCCESS, 1),  # flags bit 0 = link up
        ):
            self.queue_rx_packet(self._build_async_event(event_type, status, flags=flags))

    def _queue_disassoc_events(self) -> None:
        """`WLC_DISASSOC`'s teardown counterpart to `_queue_join_events()` - what `disconnect()`
        gets back.

        Both events are answered from the driver's own handlers (`cyw43_ctrl.c`'s
        `cyw43_cb_process_async_event()`), and either alone would be enough:

        - `CYW43_EV_DISASSOC` (11) calls `cyw43_cb_tcpip_set_link_down()` **and** clears
          `wifi_join_state` outright. That second part is what actually flips the guest's view:
          `cyw43_wifi_link_status()` reports `CYW43_LINK_DOWN` precisely when
          `wifi_join_state & WIFI_JOIN_STATE_KIND_MASK` is 0, which is what `isconnected()` reads.
        - `CYW43_EV_LINK` (16) with `status == 0` and bit 0 of `flags` *clear* takes the "Link is
          down" branch, calling `cyw43_cb_tcpip_set_link_down()` too.

        Both are sent anyway, mirroring the join sequence's own shape (which ends with `_LINK`,
        flags=1) and because the handlers are idempotent - a second `set_link_down()` costs
        nothing. `DISASSOC` goes first so the join state is already cleared by the time the link
        event lands.
        """
        for event_type, status, flags in (
            (CYW43_EV_DISASSOC, CYW43_STATUS_SUCCESS, 0),
            (CYW43_EV_LINK, CYW43_STATUS_SUCCESS, 0),  # flags bit 0 clear = link down
        ):
            self.queue_rx_packet(self._build_async_event(event_type, status, flags=flags))

    def _build_flow_control_response(self) -> bytes:
        """A bare SDPCM header carrying no ioctl/payload - `sdpcm_process_rx_packet()`'s own named
        "flow control packet with no data" case (`cyw43_ll.c`: `if (header->size ==
        SDPCM_HEADER_LEN) { ... Ignoring flow control packet ... }`, checked purely on size, before
        the `channel_and_flags` switch - real hardware genuinely sends these when it has nothing
        else to say but still needs to keep credit flowing).

        **Needed for outbound `DATA_HEADER` (Ethernet) writes - found live-booting real firmware
        (2026-08-13), not by reading source alone.** This bus doesn't answer them with real content
        (step 4's NAT bridge, not built), but `cyw43_sdpcm_send_common()`'s STALL pre-send check
        (`cyw43_ll.c:648-691`) shares the *same* `bus_data_credit`/`wwd_sdpcm_packet_transmit_
        sequence_number` channel between ioctl *and* data sends - answering a data send with
        nothing at all (the original step 3f/g behavior) never advances the driver's own
        `last_bus_data_credit` for that send, while `packet_transmit_sequence_number` still
        advances regardless (the send itself still went out). One such gap is enough to eventually
        desync the two counters into permanent equality (confirmed via a direct reproduction, not
        just the live boot: 20 ordinary ioctls track 1-credit-ahead forever as expected, then one
        unanswered data send makes them exactly equal, and every ioctl after that stalls out
        forever) - `cyw43_cb_tcpip_init()` sends at least one real outbound Ethernet frame (DHCP/ARP)
        during `nic.active(True)` itself, so without this fix the whole SDPCM channel deadlocks
        before `scan()`/`connect()` ever get a chance to run at all, regardless of anything this
        step scripts for them."""
        self._bus_data_credit = (self._bus_data_credit + 1) & 0xFF
        size = SDPCM_HEADER_LEN
        return (
            size.to_bytes(2, "little")
            + (~size & 0xFFFF).to_bytes(2, "little")
            + bytes([0, CONTROL_HEADER, 0, SDPCM_HEADER_LEN, 0, self._bus_data_credit])
            + bytes(2)
        )

    def _write_wlan(self, data: bytes) -> None:
        """Real firmware's `cyw43_sdpcm_send_common()` sends the whole SDPCM(+ioctl+payload) blob
        as one F2 block write (`cyw43_write_bytes(WLAN_FUNCTION, 0, ...)`), so a single
        `write_register()` call already has the complete frame - no reassembly across calls
        needed. Validates the SDPCM header's own `size`/`~size_com` check, and - for
        `CONTROL_HEADER` (ioctl) frames - queues a generic success response that echoes the
        request's id and zero-fills a response payload the same length as the request's own
        payload (`_build_ioctl_success_response()` - see its own docstring for why a zero-filled
        response, not just a zero-length ack, is required). `DATA_HEADER` outbound Ethernet frames
        always get a bare flow-control-only response first, unconditionally
        (`_build_flow_control_response()` - see its own docstring for why *some* response is
        required even with no real content to answer with) - `nat_bridge` (step 4, docs/records/
        0048-cyw43-nat-reflector.md), if attached, then gets the frame's Ethernet payload (BDC
        header stripped) via `handle_outbound_ethernet_frame()`. Any real reply (ARP, DHCP,
        TCP SYN-ACK/data/FIN) always arrives as a separate, later, independently-framed inbound
        packet via `queue_rx_ethernet_frame()`/`queue_rx_packet()`'s FIFO - never synchronously
        tied to the triggering write, matching 3g's own scripted-event shape. A malformed/too-short
        frame is silently ignored, matching `WLAN_FUNCTION`'s existing no-op-rather-than-raise
        stance elsewhere in this class.

        Two `CONTROL_HEADER` requests get real scripted behavior queued *behind* their own generic
        ack (step 3g, see the module docstring's "Async events + scripted scan/join" section): a
        `WLC_SET_VAR` whose payload's iovar name is `"escan"`, and `WLC_SET_SSID` itself."""
        if len(data) < SDPCM_HEADER_LEN:
            return
        size = int.from_bytes(data[0:2], "little")
        size_com = int.from_bytes(data[2:4], "little")
        if size != (~size_com & 0xFFFF):
            return
        kind = data[5] & 0x0F  # channel_and_flags, low nibble
        if kind == DATA_HEADER:
            self.queue_rx_packet(self._build_flow_control_response())
            frame_offset = SDPCM_HEADER_LEN + 2 + BDC_HEADER_LEN  # 2-byte pad + BDC header
            if self.nat_bridge is not None and len(data) >= frame_offset:
                self.nat_bridge.handle_outbound_ethernet_frame(data[frame_offset:])
            return
        if kind != CONTROL_HEADER or len(data) < SDPCM_HEADER_LEN + IOCTL_HEADER_LEN:
            return
        cmd = int.from_bytes(data[SDPCM_HEADER_LEN : SDPCM_HEADER_LEN + 4], "little")
        flags = int.from_bytes(data[SDPCM_HEADER_LEN + 8 : SDPCM_HEADER_LEN + 12], "little")
        request_id = (flags & CDCF_IOC_ID_MASK) >> CDCF_IOC_ID_SHIFT
        request_payload = data[SDPCM_HEADER_LEN + IOCTL_HEADER_LEN :]
        response_prefix = _GUEST_MAC if cmd == WLC_GET_VAR and request_payload.startswith(b"cur_etheraddr\x00") else b""
        self.queue_rx_packet(
            self._build_ioctl_success_response(request_id, len(request_payload), response_prefix=response_prefix)
        )
        if cmd == WLC_SET_VAR and request_payload.startswith(b"escan\x00"):
            self._queue_scan_events()
        elif cmd == WLC_SET_SSID:
            self._queue_join_events()
        elif cmd == WLC_DISASSOC:
            if self.nat_bridge is not None:
                # The association is over, so the flows it carried are too - see nat.py's
                # TcpReflector.reset() for the stale-flow collision this avoids on reconnect.
                self.nat_bridge.reset()
            self._queue_disassoc_events()

    def read_register(self, function: int, addr: int, size: int) -> int:
        """Returns whatever a real chip would answer for a `size`-byte read of `addr` on
        `function`. `WLAN_FUNCTION` (F2) delivers whatever `queue_rx_packet()` staged (step 3e) -
        including `_write_wlan()`'s own generic ioctl responses (step 3f)."""
        if function == BUS_FUNCTION:
            return self._read_f0(addr, size)
        if function == BACKPLANE_FUNCTION:
            if addr & SBSDIO_SB_ACCESS_2_4B_FLAG:
                return self._read_backplane_memory(self._backplane_window | (addr & BACKPLANE_ADDR_MASK), size)
            return self._read_f1(addr, size)
        if function == WLAN_FUNCTION:
            return self._read_wlan(size)
        return 0

    def write_register(self, function: int, addr: int, size: int, value: int) -> None:
        """Applies a `size`-byte write of `value` to `addr` on `function`. `WLAN_FUNCTION` (F2)
        parses SDPCM+ioctl requests generically (step 3f, `_write_wlan()`) - real firmware/CLM
        downloads (step 3e) don't go through here at all, since `cyw43_download_resource()` writes
        through `BACKPLANE_FUNCTION` instead (`cyw43_ll.c`), already covered by step 3a/3c."""
        if function == BUS_FUNCTION:
            if addr == SPI_INTERRUPT_REGISTER:
                # SPI_INTERRUPT_REGISTER is real hardware's own interrupt/error-status register -
                # a genuine write-1-to-clear (W1C) register, unlike every other F0 register
                # (SPI_BUS_CONTROL/SPI_STATUS_ENABLE/SPI_INTERRUPT_ENABLE_REGISTER/etc., all plain
                # read-write storage). `cyw43_spi.h`'s own comments on the individual bits
                # ("Clear by writing a 1" on DATA_UNAVAILABLE, "Cleared by writing 1" on
                # COMMAND_ERROR/DATA_ERROR) say so directly, and `cyw43_ll_sdpcm_poll_device()`'s
                # own ack pattern confirms it applies to the whole register, not just those three
                # bits: `if (spi_int) { cyw43_write_reg_u16(self, BUS_FUNCTION,
                # SPI_INTERRUPT_REGISTER, spi_int); }` - echoing back exactly whatever bits it just
                # read, specifically to clear them (the driver would never intentionally *set* its
                # own already-observed status bits back onto the chip).
                #
                # Found live-booting real firmware (2026-08-13) printing a spurious "[CYW43] Bus
                # error condition detected 0xb9" right after "Initializing...", traced to this
                # exact write: `cyw43_ll_bus_init()` does `cyw43_write_reg_u8(BUS_FUNCTION,
                # SPI_INTERRUPT_REGISTER, DATA_UNAVAILABLE | COMMAND_ERROR | DATA_ERROR |
                # F1_OVERFLOW)` right after the word-length/endian switch, with the comment "Make
                # sure error interrupt bits are clear" - i.e. 0x99, meant to *clear* those bits.
                # Treated as a plain store (the bug, before this fix), the register was left
                # holding 0x99 - genuinely set, the opposite of the driver's intent - and the very
                # next `_activate_rx_packet()` OR-ing in F2_PACKET_AVAILABLE (0x20) on top (for the
                # first real ioctl response) produced exactly 0xb9, which then tripped
                # `cyw43_ll_sdpcm_poll_device()`'s `spi_int & BUS_OVERFLOW_UNDERFLOW` check via the
                # F1_OVERFLOW (0x80) bit - a spurious warning over a register nothing had actually
                # overflowed. `_read_f0()`/plain `size`-width masking below still applies, so a
                # sub-word write (like this real 1-byte one, touching only the low byte) only
                # clears bits within the byte(s) actually written, leaving the rest of the register
                # untouched - matching real W1C hardware.
                #
                # This only rewrites the *host's* own writes (routed through here, the real
                # `write_register()` wire-write entry point) - `_activate_rx_packet()`/
                # `_read_wlan()` still call `_write_f0()` directly to set/clear
                # `F2_PACKET_AVAILABLE` themselves, representing the chip's own internal status
                # changes rather than a host command, so those stay plain sets/clears, not W1C.
                value = self._read_f0(addr, size) & ~value
            self._write_f0(addr, size, value)
        elif function == BACKPLANE_FUNCTION:
            if addr & SBSDIO_SB_ACCESS_2_4B_FLAG:
                combined = self._backplane_window | (addr & BACKPLANE_ADDR_MASK)
                self._write_backplane_memory(combined, size, value)
                self._maybe_mark_ht_available(combined)
            else:
                self._write_f1(addr, size, value)
        elif function == WLAN_FUNCTION:
            self._write_wlan(value.to_bytes(size, "little"))

    def _maybe_mark_ht_available(self, written_addr: int) -> None:
        """Real HT clock genuinely becomes available as a side effect of the WLAN ARM core being
        taken out of reset and starting to run its own firmware - `cyw43_ll_bus_init()` calls
        `reset_device_core(CORE_WLAN_ARM, ...)` immediately before its own HT-available wait loop
        (`cyw43_ll.c:~1655-1667`), with no explicit `SDIO_CHIP_CLOCK_CSR` HT-request write in
        between (unlike the earlier ALP handshake, which does request first) - so a driver-observed
        HT clock genuinely depends on the ARM core actually running, on real hardware. Since this
        project deliberately doesn't emulate a second CPU core (see docs/CYW43_WIFI_BACKLOG.md's
        own reasoning for `AI_IOCTRL_OFFSET`/`AI_RESETCTRL_OFFSET`), the closest honest trigger is
        the same "core is up" condition `device_core_is_up()` itself checks - once
        `CORE_WLAN_ARM`'s own registers reach that state, treat HT as available from then on, the
        same sticky-forever simplification already used for `SDIO_CHIP_CLOCK_CSR`'s own `*_REQ`
        bits. Scoped to `CORE_WLAN_ARM` specifically, not `CORE_SOCRAM` - only a running core would
        plausibly request its own clock; SOCRAM is just memory."""
        if written_addr not in (CORE_WLAN_ARM + AI_IOCTRL_OFFSET, CORE_WLAN_ARM + AI_RESETCTRL_OFFSET):
            return
        ioctrl = self._backplane_memory.get(CORE_WLAN_ARM + AI_IOCTRL_OFFSET, 0)
        resetctrl = self._backplane_memory.get(CORE_WLAN_ARM + AI_RESETCTRL_OFFSET, 0)
        if (ioctrl & (SICF_FGC | SICF_CLOCK_EN)) == SICF_CLOCK_EN and not (resetctrl & AIRC_RESET):
            self._ht_available = True

    # -- wire-level decode, GPIO-independent (see attach_gpio() for the real wiring) ------------

    def _on_cs_change(self, selected: bool) -> None:
        self._selected = selected
        self._shift_reg = 0
        self._bits_in_word = 0
        self._pending_command = None
        self._pending_write_words = []
        self._response_bytes = b""
        self._response_bit_index = 0
        if not selected and self._data_pin is not None:
            # "when CS is not asserted, WL_D instead carries the chip's IRQ level" (Wokwi
            # investigation, docs/CYW43_WIFI_BACKLOG.md) - matches real hardware's
            # CYW43_PIN_WL_HOST_WAKE (cyw43_cb_read_host_interrupt_pin(), cyw43_ctrl.c), which the
            # driver polls directly, independent of any SPI transaction. Reflects whether a packet
            # is still pending (step 3e's queue_rx_packet()/_read_wlan()) rather than an always-LOW
            # placeholder - real SDPCM ioctl responses/async events (step 3f/3g) will ride this.
            self._data_pin.set_input_value(bool(self._rx_packet))

    def _on_clock_rising(self, sampled_bit: bool) -> None:
        if not self._selected or self._response_bytes:
            # Either idle, or we're the one driving right now (response phase) - nothing to
            # sample on this edge.
            return
        self._shift_reg = ((self._shift_reg << 1) | int(sampled_bit)) & 0xFFFFFFFF
        self._bits_in_word += 1
        if self._bits_in_word < 32:
            return
        word, self._shift_reg, self._bits_in_word = self._shift_reg, 0, 0
        word = self._word(word)  # undo the current word-length mode's wire transform first

        if self._pending_command is None:
            command = _decode_header(word)
            if command.write:
                # A write header is followed by `_word_count(command.size)` more host-driven
                # words (the value/block data) - stay in sampling mode, decode once they've all
                # arrived instead.
                self._pending_command = command
                self._pending_write_words = []
            else:
                self._start_response(command)
        else:
            command = self._pending_command
            self._pending_write_words.append(word)
            if len(self._pending_write_words) < _word_count(command.size):
                return  # a block write spans several words - still waiting on the rest.
            self._pending_command = None
            value = _words_to_value(self._pending_write_words, command.size)
            self._pending_write_words = []
            self.write_register(command.function, command.address, command.size, value)

    def _start_response(self, command: GSPICommand) -> None:
        value = self.read_register(command.function, command.address, command.size)
        words = _value_to_words(value, command.size)
        if command.function == BACKPLANE_FUNCTION:
            # See CYW43_BACKPLANE_READ_PAD_LEN_BYTES's own comment - the driver discards this many
            # leading words and takes the real answer from the end.
            words = [0] * (CYW43_BACKPLANE_READ_PAD_LEN_BYTES // 4) + words
        self._response_bytes = b"".join(self._word(word).to_bytes(4, "big") for word in words)
        self._response_bit_index = 0

    def _on_clock_falling(self) -> "bool | None":
        """Returns the next bit to drive onto the data line, or `None` if we have nothing to
        drive right now (host's turn, or idle)."""
        if not self._selected or not self._response_bytes:
            return None
        byte_index, bit_in_byte = divmod(self._response_bit_index, 8)
        if byte_index >= len(self._response_bytes):
            return None
        bit = bool(self._response_bytes[byte_index] & (0x80 >> bit_in_byte))
        self._response_bit_index += 1
        if self._response_bit_index >= len(self._response_bytes) * 8:
            self._response_bytes = b""
            self._response_bit_index = 0
        return bit

    # -- GPIO wiring ----------------------------------------------------------------------------

    def attach_gpio(self, rp2040: "RP2040", *, clk: int = 29, data: int = 24, cs: int = 25) -> None:
        """Hooks this bus onto `rp2040.gpio[clk]`/`[data]`/`[cs]` (defaults: `WL_CLK`/`WL_D`/
        `WL_CS`'s real Pico W pin numbers - docs/CYW43_WIFI_BACKLOG.md's pinout table). Only safe
        pre-run, same contract as every `ExternalDevice.attach()` (`external/device.py`) - this
        class isn't one itself (see module docstring: it's a decode engine a future `Cyw43439`,
        step 3, will own and call this from its own `attach()`)."""
        clk_pin = rp2040.gpio[clk]
        data_pin = rp2040.gpio[data]
        cs_pin = rp2040.gpio[cs]
        self._data_pin = data_pin

        def _cs_listener(new_state: GPIOPinState, _old_state: GPIOPinState) -> None:
            # Active-low: CS is asserted (selected) when the RP2040 drives it LOW.
            self._on_cs_change(new_state != GPIOPinState.HIGH)

        def _clk_listener(new_state: GPIOPinState, old_state: GPIOPinState) -> None:
            if new_state == GPIOPinState.HIGH and old_state != GPIOPinState.HIGH:
                self._on_clock_rising(data_pin.value == GPIOPinState.HIGH)
            elif new_state != GPIOPinState.HIGH and old_state == GPIOPinState.HIGH:
                bit = self._on_clock_falling()
                if bit is not None:
                    data_pin.set_input_value(bit)

        cs_pin.add_listener(_cs_listener)
        clk_pin.add_listener(_clk_listener)
