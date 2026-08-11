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
  see `_decode_header()`). A `write` header is followed by one more host-driven 32-bit word (the
  value); a `read` header switches the data line to chip-driven and we shift the requested
  register's value back out, MSB-first, immediately after the header completes.

**Scope, matching step 2's own bar ("far enough for the driver's init handshake to succeed"):**
only `BUS_FUNCTION` (F0) - the bus/status register block a real bring-up sequence polls before
anything backplane/WLAN-related is even reachable (`cyw43_ll_bus_init()`, `cyw43_ll.c`). F0's
address space here is a plain byte-addressable `bytearray` (real SDIO/gSPI F0 registers ARE just
consecutive byte registers - e.g. `SPI_BUS_CONTROL`/`SPI_RESPONSE_DELAY`/`SPI_STATUS_ENABLE`/
`SPI_RESET_BP` are bytes 0x0000-0x0003, and a 4-byte `make_cmd(..., size=4)` write starting at
`SPI_BUS_CONTROL` - exactly what `cyw43_ll_bus_init()` does - covers all four in one transfer) -
so read/write dispatch is generic, not a hand-written case per register, with `SPI_READ_TEST_REGISTER`
as the one fixed, read-only exception real firmware polls for a known-good value before trusting
anything else on the bus. `BACKPLANE_FUNCTION`/`WLAN_FUNCTION` (steps 3/4) answer a harmless `0`/
no-op rather than raising, so an unexpected early access during bring-up doesn't crash the whole
emulator - not a claim that those functions are implemented.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.gpio_pin import GPIOPin
    from rp2040py.rp2040 import RP2040

__all__ = (
    "BACKPLANE_FUNCTION",
    "BUS_FUNCTION",
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
    "STATUS_F2_RX_READY",
    "STATUS_F3_RX_READY",
    "TEST_PATTERN",
    "WLAN_FUNCTION",
    "GSPIBus",
)

# SDIO/gSPI standard F0/F1/F2 function numbering (cyw43_internal.h).
BUS_FUNCTION = 0
BACKPLANE_FUNCTION = 1
WLAN_FUNCTION = 2

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

# SPI_STATUS_REGISTER bits GSPIBus actually sets by default (cyw43_spi.h has the full set - only
# the ones this module's default value uses are named here).
STATUS_F2_RX_READY = 0x00000020
STATUS_F3_RX_READY = 0x00000040


@dataclass(frozen=True)
class GSPICommand:
    """A decoded `make_cmd()` header - see `_decode_header()`."""

    write: bool
    increment: bool
    function: int
    address: int
    size: int  # bytes


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

        self._selected = False
        self._shift_reg = 0
        self._bits_in_word = 0
        self._pending_command: GSPICommand | None = None
        self._response_bytes = b""
        self._response_bit_index = 0
        # Set by attach_gpio() - the pin this bus drives its response bits onto/samples host bits
        # from. None until attached (mirrors every other ExternalDevice-adjacent component here).
        self._data_pin: GPIOPin | None = None

    # -- F0 register space ---------------------------------------------------------------------

    def _read_f0(self, addr: int, size: int) -> int:
        if addr == SPI_READ_TEST_REGISTER and size == 4:
            return TEST_PATTERN
        if addr < 0 or addr + size > len(self._f0):
            return 0
        return int.from_bytes(bytes(self._f0[addr : addr + size]), "little")

    def _write_f0(self, addr: int, size: int, value: int) -> None:
        if addr < 0 or addr + size > len(self._f0):
            return
        self._f0[addr : addr + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def read_register(self, function: int, addr: int, size: int) -> int:
        """Returns whatever a real chip would answer for a `size`-byte read of `addr` on
        `function`. `BACKPLANE_FUNCTION`/`WLAN_FUNCTION` (steps 3/4, not built yet) answer `0`
        rather than raising."""
        if function == BUS_FUNCTION:
            return self._read_f0(addr, size)
        return 0

    def write_register(self, function: int, addr: int, size: int, value: int) -> None:
        """Applies a `size`-byte write of `value` to `addr` on `function`. `BACKPLANE_FUNCTION`/
        `WLAN_FUNCTION` (steps 3/4, not built yet) are a no-op."""
        if function == BUS_FUNCTION:
            self._write_f0(addr, size, value)

    # -- wire-level decode, GPIO-independent (see attach_gpio() for the real wiring) ------------

    def _on_cs_change(self, selected: bool) -> None:
        self._selected = selected
        self._shift_reg = 0
        self._bits_in_word = 0
        self._pending_command = None
        self._response_bytes = b""
        self._response_bit_index = 0
        if not selected and self._data_pin is not None:
            # "when CS is not asserted, WL_D instead carries the chip's IRQ level" (Wokwi
            # investigation, docs/CYW43_WIFI_BACKLOG.md) - real interrupt delivery is a step 3/4
            # concern (SPI_INTERRUPT_REGISTER/WLC_E_* events); drive a known "nothing pending" LOW
            # rather than leaving the line floating/stale between transactions.
            self._data_pin.set_input_value(False)

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

        if self._pending_command is None:
            command = _decode_header(word)
            if command.write:
                # A write header is followed by one more host-driven word (the value) - stay in
                # sampling mode, decode on the *next* full word instead.
                self._pending_command = command
            else:
                value = self.read_register(command.function, command.address, command.size)
                self._response_bytes = value.to_bytes(4, "big")
                self._response_bit_index = 0
        else:
            command = self._pending_command
            self._pending_command = None
            self.write_register(command.function, command.address, command.size, word)

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
