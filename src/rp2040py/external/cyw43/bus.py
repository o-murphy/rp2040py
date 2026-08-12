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
`CONTROL_HEADER` frames queues a zero-length success response echoing the request's own id
(`CDCF_IOC_ID_MASK`) with a monotonically increasing `bus_data_credit` - satisfies the bulk of the
real `WLC_*`/iovar vocabulary bring-up sends without per-ioctl content (step 3g still owns the
handful - `WLC_SET_SSID`/join - that need real scripted behavior instead of this generic ack).
`DATA_HEADER` outbound Ethernet frames (step 4's NAT bridge) and anything malformed are silently
ignored, matching this class's existing no-op-rather-than-raise stance for unimplemented paths.
Real firmware/CLM downloads (step 3e) don't touch `WLAN_FUNCTION` at all -
`cyw43_download_resource()` writes through `BACKPLANE_FUNCTION` instead, already covered
generically by the F1 block-transfer path above.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.gpio_pin import GPIOPin
    from rp2040py.rp2040 import RP2040

__all__ = (
    "AIRC_RESET",
    "AI_IOCTRL_OFFSET",
    "AI_RESETCTRL_OFFSET",
    "BACKPLANE_ADDR_MASK",
    "BACKPLANE_FUNCTION",
    "BUS_FUNCTION",
    "CDCF_IOC_ID_MASK",
    "CDCF_IOC_ID_SHIFT",
    "CONTROL_HEADER",
    "CORE_SOCRAM",
    "CORE_WLAN_ARM",
    "CYW43_BACKPLANE_READ_PAD_LEN_BYTES",
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
# sdpcm_header_t.channel_and_flags low nibble - only CONTROL_HEADER (ioctl request/response) is
# actually handled by _write_wlan() (everything else, including DATA_HEADER, falls through its
# `kind != CONTROL_HEADER` check and is silently ignored). DATA_HEADER is still named here rather
# than left a bare magic number, since it's the concrete "not yet built" case worth naming
# (outbound Ethernet, step 4's NAT bridge). ASYNCEVENT_HEADER (step 3g, chip-to-host only) isn't
# needed on this side yet.
CONTROL_HEADER = 0
DATA_HEADER = 2
# ioctl_header_t.flags: the requesting id lives in the top 16 bits - sdpcm_process_rx_packet()
# (cyw43_ll.c) drops any response whose echoed id doesn't match the driver's own last-sent id.
CDCF_IOC_ID_SHIFT = 16
CDCF_IOC_ID_MASK = 0xFFFF0000


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
        self._rx_packet = b""
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
        """Stages `data` as the next inbound F2 packet - the generic delivery mechanism step
        3f/3g's SDPCM ioctl responses and async events will use, not anything protocol-specific
        itself. Sets `SPI_STATUS_REGISTER`'s `STATUS_F2_PKT_AVAILABLE` bit + length field and
        `SPI_INTERRUPT_REGISTER`'s `F2_PACKET_AVAILABLE` bit - what real firmware's
        `cyw43_ll_sdpcm_poll_device()` actually polls for (cyw43_ll.c, SPI variant) - and, if CS is
        currently deasserted, immediately raises the shared `WL_D` pin's own IRQ level too, since
        that's a separate GPIO-level signal (`cyw43_cb_read_host_interrupt_pin()`, real hardware's
        `CYW43_PIN_WL_HOST_WAKE`) a real driver's interrupt handler can notice without any SPI
        transaction happening at all - `_on_cs_change()` alone wouldn't reflect a packet queued
        while already idle."""
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
        consuming them; once the queue is empty, clears `STATUS_F2_PKT_AVAILABLE`/
        `F2_PACKET_AVAILABLE` - the shared IRQ pin itself drops on the next `_on_cs_change()`
        deselect, which by then sees an empty `_rx_packet`, so nothing needs touching here."""
        data, self._rx_packet = self._rx_packet[:size], self._rx_packet[size:]
        if not self._rx_packet:
            status = self._read_f0(SPI_STATUS_REGISTER, 4) & ~(STATUS_F2_PKT_AVAILABLE | STATUS_F2_PKT_LEN_MASK)
            self._write_f0(SPI_STATUS_REGISTER, 4, status)
            self._write_f0(SPI_INTERRUPT_REGISTER, 2, self._read_f0(SPI_INTERRUPT_REGISTER, 2) & ~F2_PACKET_AVAILABLE)
        return int.from_bytes(data, "little")

    # -- F2 (WLAN_FUNCTION) outbound SDPCM/ioctl (step 3f) --------------------------------------

    def _build_ioctl_success_response(self, request_id: int) -> bytes:
        """Generic zero-length "success" SDPCM+ioctl response - satisfies the bulk of the real
        `WLC_*`/iovar vocabulary `cyw43_ll_wifi_on()`/`cyw43_ll_wifi_join()` send during bring-up
        without needing per-ioctl content (step 3g is where the handful that actually need
        scripted behavior - `WLC_SET_SSID`/join - get real responses instead of this).

        Two things `sdpcm_process_rx_packet()`/`cyw43_sdpcm_send_common()` (cyw43_ll.c) actually
        enforce on whatever we send back, both handled here:
        - The response's `ioctl_header_t.flags` must echo the request's own id
          (`CDCF_IOC_ID_MASK`) - `sdpcm_process_rx_packet()` silently drops anything whose id
          doesn't match the driver's last-sent one.
        - `bus_data_credit` must end up strictly ahead of the driver's own send count, or
          `cyw43_sdpcm_send_common()`'s STALL check blocks the *next* host send forever - the
          driver increments its send count by one per send, so incrementing this by one per
          response keeps exactly one ahead, matching this chip model's synchronous
          one-request-one-response shape. `wireless_flow_control` must also stay 0 - any nonzero
          value has the same stalling effect, unconditionally, on every later send."""
        ioctl_header = (
            (0).to_bytes(4, "little")  # cmd - not inspected by the driver on a response
            + (0).to_bytes(4, "little")  # len (output length) - zero-length success
            + ((request_id << CDCF_IOC_ID_SHIFT) & CDCF_IOC_ID_MASK).to_bytes(4, "little")  # flags
            + (0).to_bytes(4, "little")  # status = success
        )
        size = SDPCM_HEADER_LEN + len(ioctl_header)
        self._bus_data_credit = (self._bus_data_credit + 1) & 0xFF
        sdpcm_header = (
            size.to_bytes(2, "little")
            + (~size & 0xFFFF).to_bytes(2, "little")
            # sequence(0, unchecked on receive) / channel_and_flags / next_length(0, control-only)
            # / header_length / wireless_flow_control(0) / bus_data_credit:
            + bytes([0, CONTROL_HEADER, 0, SDPCM_HEADER_LEN, 0, self._bus_data_credit])
            + bytes(2)  # reserved
        )
        return sdpcm_header + ioctl_header

    def _write_wlan(self, data: bytes) -> None:
        """Real firmware's `cyw43_sdpcm_send_common()` sends the whole SDPCM(+ioctl+payload) blob
        as one F2 block write (`cyw43_write_bytes(WLAN_FUNCTION, 0, ...)`), so a single
        `write_register()` call already has the complete frame - no reassembly across calls
        needed. Validates the SDPCM header's own `size`/`~size_com` check, and - for
        `CONTROL_HEADER` (ioctl) frames only - queues a generic zero-length success response
        echoing the request's id (`_build_ioctl_success_response()`). Anything else (`DATA_HEADER`
        outbound Ethernet frames - step 4's NAT bridge - or a malformed/too-short frame) is
        silently ignored for now, matching `WLAN_FUNCTION`'s existing no-op-rather-than-raise
        stance elsewhere in this class."""
        if len(data) < SDPCM_HEADER_LEN:
            return
        size = int.from_bytes(data[0:2], "little")
        size_com = int.from_bytes(data[2:4], "little")
        if size != (~size_com & 0xFFFF):
            return
        kind = data[5] & 0x0F  # channel_and_flags, low nibble
        if kind != CONTROL_HEADER or len(data) < SDPCM_HEADER_LEN + IOCTL_HEADER_LEN:
            return
        flags = int.from_bytes(data[SDPCM_HEADER_LEN + 8 : SDPCM_HEADER_LEN + 12], "little")
        request_id = (flags & CDCF_IOC_ID_MASK) >> CDCF_IOC_ID_SHIFT
        self.queue_rx_packet(self._build_ioctl_success_response(request_id))

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
