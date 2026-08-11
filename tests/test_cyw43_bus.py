"""Unit tests for `rp2040py.external.cyw43.bus.GSPIBus` - the CYW43_WIFI_BACKLOG.md step 2 gSPI
decode.

Drives GPIO24/25/29 (WL_D/WL_CS/WL_CLK) exactly the way real `cyw43_bus_pio_spi.c`/`.pio` does
(see `bus.py`'s own module docstring for the derivation), via a small fake "gSPI master" - not the
real PIO/DMA machinery, just enough bit-banging via `machine.Pin`-equivalent SIO writes (mirrors
`test_led_mock.py`'s own `_drive_gpio_high()` pattern) to prove `GSPIBus` decodes/responds
correctly to the same wire protocol a real driver would produce.
"""

from rp2040py.external.cyw43.bus import (
    BACKPLANE_FUNCTION,
    BUS_FUNCTION,
    SPI_BUS_CONTROL,
    SPI_READ_TEST_REGISTER,
    TEST_PATTERN,
    GSPIBus,
)
from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.rp2040 import RP2040


def _make_cmd(write: bool, inc: bool, fn: int, addr: int, size: int) -> int:
    return (int(write) << 31) | (int(inc) << 30) | ((fn & 0x3) << 28) | ((addr & 0x1FFFF) << 11) | (size & 0x7FF)


class _FakeGSPIMaster:
    """Bit-bangs the same wire protocol `cyw43_bus_pio_spi.c` does, driving GPIO24/25/29 via
    plain SIO register writes - see `bus.py`'s module docstring for the exact edge timing this
    matches (data actions on the low phase, `WL_CLK` toggled around them)."""

    def __init__(self, rp2040: RP2040, clk: int = 29, data: int = 24, cs: int = 25) -> None:
        self.rp2040 = rp2040
        self.clk_pin = rp2040.gpio[clk]
        self.data_pin = rp2040.gpio[data]
        self.cs_pin = rp2040.gpio[cs]
        self._clk_bit = 1 << clk
        self._data_bit = 1 << data
        self._cs_bit = 1 << cs

        for pin in (self.clk_pin, self.data_pin, self.cs_pin):
            pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
        rp2040.sio.gpio_output_enable |= self._clk_bit | self._data_bit | self._cs_bit
        rp2040.sio.gpio_value |= self._cs_bit  # CS idle high (deselected)
        # Real gpio_set_function() enables input as a side effect (pico-sdk) - mirrored here
        # directly since this test drives pins itself rather than running real firmware.
        self.data_pin.pad_value |= 0x40

    def _set(self, bitmask: int, high: bool) -> None:
        if high:
            self.rp2040.sio.gpio_value |= bitmask
        else:
            self.rp2040.sio.gpio_value &= ~bitmask

    def select(self) -> None:
        self._set(self._cs_bit, False)
        self.cs_pin.check_for_updates()

    def deselect(self) -> None:
        self._set(self._cs_bit, True)
        self.cs_pin.check_for_updates()

    def _clk_high(self) -> None:
        self._set(self._clk_bit, True)
        self.clk_pin.check_for_updates()

    def _clk_low(self) -> None:
        self._set(self._clk_bit, False)
        self.clk_pin.check_for_updates()

    def send_word(self, word: int) -> None:
        self.rp2040.sio.gpio_output_enable |= self._data_bit
        for i in range(32):
            bit = (word >> (31 - i)) & 1
            self._clk_low()
            self._set(self._data_bit, bool(bit))
            self._clk_high()
        self._clk_low()  # trailing falling edge - lets a read response's first bit get driven

    def recv_word(self) -> int:
        self.rp2040.sio.gpio_output_enable &= ~self._data_bit
        word = 0
        for _ in range(32):
            bit = 1 if self.data_pin.input_value else 0
            word = ((word << 1) | bit) & 0xFFFFFFFF
            self._clk_high()
            self._clk_low()
        return word

    def read_register(self, function: int, addr: int, size: int) -> int:
        self.select()
        self.send_word(_make_cmd(write=False, inc=True, fn=function, addr=addr, size=size))
        value = self.recv_word()
        self.deselect()
        return value

    def write_register(self, function: int, addr: int, size: int, value: int) -> None:
        self.select()
        self.send_word(_make_cmd(write=True, inc=True, fn=function, addr=addr, size=size))
        self.send_word(value)
        self.deselect()


def _wire_up() -> tuple[RP2040, _FakeGSPIMaster]:
    rp2040 = RP2040()
    bus = GSPIBus()
    bus.attach_gpio(rp2040)
    return rp2040, _FakeGSPIMaster(rp2040)


def test_read_test_register_returns_the_fixed_pattern():
    """The one value real firmware's init handshake actually gates on - cyw43_ll_bus_init()
    polls SPI_READ_TEST_REGISTER up to 10 times expecting exactly this."""
    _rp2040, master = _wire_up()

    value = master.read_register(BUS_FUNCTION, SPI_READ_TEST_REGISTER, 4)

    assert value == TEST_PATTERN == 0xFEEDBEAD


def test_bus_control_write_then_read_round_trips():
    """Mirrors cyw43_ll_bus_init()'s own write_reg_u32_swap(SPI_BUS_CONTROL, val) followed by a
    plain readback."""
    _rp2040, master = _wire_up()
    written = 0x00_0004_11  # low byte = bus-control flags, next byte = response delay, matching
    # cyw43_ll_bus_init()'s own packed-byte-registers layout (see bus.py's module docstring).

    master.write_register(BUS_FUNCTION, SPI_BUS_CONTROL, 4, written)
    read_back = master.read_register(BUS_FUNCTION, SPI_BUS_CONTROL, 4)

    assert read_back == written


def test_single_byte_write_only_touches_its_own_byte():
    _rp2040, master = _wire_up()
    master.write_register(BUS_FUNCTION, SPI_BUS_CONTROL, 4, 0xAABBCCDD)

    master.write_register(BUS_FUNCTION, SPI_BUS_CONTROL + 1, 1, 0xFF)

    assert master.read_register(BUS_FUNCTION, SPI_BUS_CONTROL, 4) == 0xAABBFFDD


def test_deselecting_mid_word_discards_the_partial_transaction():
    """A transaction aborted (CS deasserted) before a full 32-bit word arrives must not leave
    stale bits around to corrupt the *next* transaction."""
    _rp2040, master = _wire_up()
    master.select()
    master.send_word(_make_cmd(write=False, inc=True, fn=BUS_FUNCTION, addr=SPI_READ_TEST_REGISTER, size=4))
    # Deselect mid-response instead of finishing the read - the bus never gets a clean recv_word().
    master.deselect()

    # A fresh, complete transaction afterward must work normally - no leftover shift-register
    # state or "still driving a response" state bleeding through.
    value = master.read_register(BUS_FUNCTION, SPI_READ_TEST_REGISTER, 4)
    assert value == TEST_PATTERN


def test_unimplemented_function_reads_zero_instead_of_raising():
    """BACKPLANE_FUNCTION (step 3, not built yet) - must not crash an early/unexpected access."""
    _rp2040, master = _wire_up()

    value = master.read_register(BACKPLANE_FUNCTION, 0x1000A, 1)

    assert value == 0
