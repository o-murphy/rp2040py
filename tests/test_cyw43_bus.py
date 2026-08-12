"""Unit tests for `rp2040py.external.cyw43.bus.GSPIBus` - the CYW43_WIFI_BACKLOG.md step 2 gSPI
decode.

Drives GPIO24/25/29 (WL_D/WL_CS/WL_CLK) exactly the way real `cyw43_bus_pio_spi.c`/`.pio` does
(see `bus.py`'s own module docstring for the derivation), via a small fake "gSPI master" - not the
real PIO/DMA machinery, just enough bit-banging via `machine.Pin`-equivalent SIO writes (mirrors
`test_led_mock.py`'s own `_drive_gpio_high()` pattern) to prove `GSPIBus` decodes/responds
correctly to the same wire protocol a real driver would produce.
"""

from rp2040py.external.cyw43.bus import (
    AI_IOCTRL_OFFSET,
    AI_RESETCTRL_OFFSET,
    AIRC_RESET,
    BACKPLANE_ADDR_MASK,
    BACKPLANE_FUNCTION,
    BUS_FUNCTION,
    CORE_SOCRAM,
    CORE_WLAN_ARM,
    SBSDIO_ALP_AVAIL,
    SBSDIO_ALP_AVAIL_REQ,
    SBSDIO_HT_AVAIL,
    SBSDIO_HT_AVAIL_REQ,
    SBSDIO_SB_ACCESS_2_4B_FLAG,
    SBSDIO_SLPCSR_DEVICE_ON,
    SBSDIO_SLPCSR_KEEP_SDIO_ON,
    SDIO_BACKPLANE_ADDRESS_HIGH,
    SDIO_BACKPLANE_ADDRESS_LOW,
    SDIO_BACKPLANE_ADDRESS_MID,
    SDIO_CHIP_CLOCK_CSR,
    SDIO_SLEEP_CSR,
    SICF_CLOCK_EN,
    SICF_FGC,
    SPI_BUS_CONTROL,
    SPI_READ_TEST_REGISTER,
    TEST_PATTERN,
    WORD_LENGTH_32,
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

        # Mirrors GSPIBus's own word-length mode tracking (bus.py's "Word-length/endian mode") -
        # both sides start in the same real-silicon-default 16-bit mode and flip in lockstep the
        # instant a SPI_BUS_CONTROL write's value has WORD_LENGTH_32 set, so round-trips stay
        # correct regardless of which mode is active.
        self._word_length_32 = False

    def _word(self, word: int) -> int:
        if self._word_length_32:
            return ((word & 0xFF) << 24) | ((word & 0xFF00) << 8) | ((word >> 8) & 0xFF00) | ((word >> 24) & 0xFF)
        return ((word & 0xFFFF) << 16) | (word >> 16)

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
        """Reads `size` bytes, `size` possibly spanning several 32-bit wire words (a block
        transfer, docs/CYW43_WIFI_BACKLOG.md step 3a) - one word covers 4 address-ascending bytes,
        same convention `bus.py`'s `_value_to_words()` uses, so this stays a plain inverse of it."""
        self.select()
        self.send_word(self._word(_make_cmd(write=False, inc=True, fn=function, addr=addr, size=size)))
        word_count = max(1, (size + 3) // 4)
        raw = b"".join(self._word(self.recv_word()).to_bytes(4, "little") for _ in range(word_count))
        self.deselect()
        return int.from_bytes(raw[:size], "little")

    def write_register(self, function: int, addr: int, size: int, value: int) -> None:
        """Writes `size` bytes, spanning several 32-bit wire words for a block transfer (step 3a) -
        mirrors `read_register()`'s own chunking so round-trips through `GSPIBus` stay meaningful
        regardless of size."""
        self.select()
        self.send_word(self._word(_make_cmd(write=True, inc=True, fn=function, addr=addr, size=size)))
        word_count = max(1, (size + 3) // 4)
        raw = value.to_bytes(size, "little").ljust(word_count * 4, b"\x00")
        for i in range(word_count):
            word = int.from_bytes(raw[i * 4 : i * 4 + 4], "little")
            self.send_word(self._word(word))
        self.deselect()
        if function == BUS_FUNCTION and addr == SPI_BUS_CONTROL and value & WORD_LENGTH_32:
            self._word_length_32 = True


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
    """WLAN_FUNCTION (step 4, not built yet) - must not crash an early/unexpected access."""
    _rp2040, master = _wire_up()

    value = master.read_register(2, 0x1000A, 1)

    assert value == 0


def test_alp_and_ht_avail_req_bits_are_readable_back_immediately():
    """The one poll cyw43_ll_bus_init() actually gates on beyond F0 - real silicon takes a moment
    to bring its clock up, this model skips that latency (see bus.py's module docstring)."""
    _rp2040, master = _wire_up()

    master.write_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1, SBSDIO_ALP_AVAIL_REQ | SBSDIO_HT_AVAIL_REQ)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1)
    assert value & SBSDIO_ALP_AVAIL
    assert value & SBSDIO_HT_AVAIL


def test_alp_avail_req_alone_does_not_set_ht_avail():
    _rp2040, master = _wire_up()

    master.write_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1, SBSDIO_ALP_AVAIL_REQ)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1)
    assert value & SBSDIO_ALP_AVAIL
    assert not value & SBSDIO_HT_AVAIL


def test_keep_sdio_on_makes_device_on_readable_too():
    """cyw43_kso_set() polls SDIO_SLEEP_CSR up to 64x1ms expecting both bits set together."""
    _rp2040, master = _wire_up()

    master.write_register(BACKPLANE_FUNCTION, SDIO_SLEEP_CSR, 1, SBSDIO_SLPCSR_KEEP_SDIO_ON)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_SLEEP_CSR, 1)
    assert value & SBSDIO_SLPCSR_DEVICE_ON


def test_clearing_keep_sdio_on_clears_device_on():
    _rp2040, master = _wire_up()
    master.write_register(BACKPLANE_FUNCTION, SDIO_SLEEP_CSR, 1, SBSDIO_SLPCSR_KEEP_SDIO_ON)

    master.write_register(BACKPLANE_FUNCTION, SDIO_SLEEP_CSR, 1, 0)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_SLEEP_CSR, 1)
    assert not value & SBSDIO_SLPCSR_DEVICE_ON


def test_backplane_window_redirects_writes_into_the_combined_address():
    """cyw43_write_backplane()'s own scheme: window bytes set via LOW/MID/HIGH, then a
    SBSDIO_SB_ACCESS_2_4B_FLAG-tagged address lands at (window << 15) | (addr & 0x7fff)."""
    _rp2040, master = _wire_up()
    window = 0x18003
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)

    addr = SBSDIO_SB_ACCESS_2_4B_FLAG | 0x0100
    master.write_register(BACKPLANE_FUNCTION, addr, 4, 0xDEADBEEF)

    assert master.read_register(BACKPLANE_FUNCTION, addr, 4) == 0xDEADBEEF


def test_backplane_window_low_byte_is_the_low_order_bits_of_the_window():
    """SDIO_BACKPLANE_ADDRESS_LOW/MID/HIGH build a 32-bit window value shifted left 8/16/24 -
    writing only LOW must not disturb whatever MID/HIGH already hold."""
    _rp2040, master = _wire_up()
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, 0x03)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, 0x00)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, 0x00)
    addr = SBSDIO_SB_ACCESS_2_4B_FLAG
    master.write_register(BACKPLANE_FUNCTION, addr, 1, 0xAB)

    # window = 0x030000, combined address = (0x030000 << 15) | 0 - readable only through the same
    # window, proving LOW's earlier write (0x00) didn't clobber MID's 0x03.
    assert master.read_register(BACKPLANE_FUNCTION, addr, 1) == 0xAB

    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, 0x04)
    assert master.read_register(BACKPLANE_FUNCTION, addr, 1) == 0


def test_wlan_arm_and_socram_cores_default_to_held_in_reset():
    """Real silicon holds both cores in reset at power-on; disable_device_core() (cyw43_ll.c)
    CHECKS this is already true rather than setting it - the emulated default must match."""
    _rp2040, master = _wire_up()
    for core in (CORE_WLAN_ARM, CORE_SOCRAM):
        window = core & ~BACKPLANE_ADDR_MASK
        master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
        master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
        master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)
        addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((core + AI_RESETCTRL_OFFSET) & BACKPLANE_ADDR_MASK)

        value = master.read_register(BACKPLANE_FUNCTION, addr, 1)

        assert value & AIRC_RESET


def test_f0_block_write_spans_multiple_registers():
    """Step 3a: a block transfer wider than one 32-bit word (firmware/CLM download chunks, SDPCM
    frames, ... - step 3e onward all need this) must round-trip correctly, not just the
    single-word register pokes every earlier test here exercises."""
    _rp2040, master = _wire_up()
    payload = int.from_bytes(bytes(range(8)), "little")

    master.write_register(BUS_FUNCTION, 0x0000, 8, payload)

    assert master.read_register(BUS_FUNCTION, 0x0000, 8) == payload


def _select_backplane_window(master: _FakeGSPIMaster, window: int) -> None:
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)


def test_backplane_block_write_then_read_round_trips_across_multiple_words():
    """Same as the F0 case above but through the windowed backplane path - real firmware/CLM
    download chunks and SDPCM frames both ride this route, not F0."""
    _rp2040, master = _wire_up()
    _select_backplane_window(master, 0x18000)
    addr = SBSDIO_SB_ACCESS_2_4B_FLAG | 0x0100
    payload = int.from_bytes(bytes(range(12)), "little")  # 12 bytes = 3 wire words

    master.write_register(BACKPLANE_FUNCTION, addr, 12, payload)

    assert master.read_register(BACKPLANE_FUNCTION, addr, 12) == payload


def test_block_transfer_size_not_a_multiple_of_four_still_round_trips():
    """A 6-byte block (not word-aligned) still rides two full 32-bit wire words, the second
    zero-padded - `_word_count()`'s ceiling division must cover this, not just exact multiples of
    4 (real block transfers always are word-aligned, but this proves the general case)."""
    _rp2040, master = _wire_up()
    _select_backplane_window(master, 0x18000)
    addr = SBSDIO_SB_ACCESS_2_4B_FLAG | 0x0200
    payload = int.from_bytes(bytes([1, 2, 3, 4, 5, 6]), "little")

    master.write_register(BACKPLANE_FUNCTION, addr, 6, payload)

    assert master.read_register(BACKPLANE_FUNCTION, addr, 6) == payload


def test_core_ioctrl_register_round_trips_through_the_backplane_window():
    """AI_IOCTRL_OFFSET (SICF_FGC/SICF_CLOCK_EN) is the register reset_device_core() writes to
    bring a core out of reset - just needs to hold whatever was last written, like any other
    backplane-memory address."""
    _rp2040, master = _wire_up()
    window = CORE_WLAN_ARM & ~BACKPLANE_ADDR_MASK
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)
    addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((CORE_WLAN_ARM + AI_IOCTRL_OFFSET) & BACKPLANE_ADDR_MASK)

    master.write_register(BACKPLANE_FUNCTION, addr, 1, SICF_FGC | SICF_CLOCK_EN)

    assert master.read_register(BACKPLANE_FUNCTION, addr, 1) == SICF_FGC | SICF_CLOCK_EN
