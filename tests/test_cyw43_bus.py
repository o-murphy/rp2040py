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
    CDCF_IOC_ID_MASK,
    CDCF_IOC_ID_SHIFT,
    CONTROL_HEADER,
    CORE_SOCRAM,
    CORE_WLAN_ARM,
    CYW43_BACKPLANE_READ_PAD_LEN_BYTES,
    DATA_HEADER,
    F2_PACKET_AVAILABLE,
    IOCTL_HEADER_LEN,
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
    SDIO_FUNCTION2_WATERMARK,
    SDIO_SLEEP_CSR,
    SDPCM_HEADER_LEN,
    SICF_CLOCK_EN,
    SICF_FGC,
    SPI_BUS_CONTROL,
    SPI_INTERRUPT_REGISTER,
    SPI_READ_TEST_REGISTER,
    SPI_STATUS_REGISTER,
    STATUS_F2_PKT_AVAILABLE,
    STATUS_F2_PKT_LEN_MASK,
    STATUS_F2_PKT_LEN_SHIFT,
    TEST_PATTERN,
    WLAN_FUNCTION,
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
        same convention `bus.py`'s `_value_to_words()` uses, so this stays a plain inverse of it.
        `BACKPLANE_FUNCTION` reads clock `CYW43_BACKPLANE_READ_PAD_LEN_BYTES` of leading dummy
        words first (real `cyw43_bus_pio_spi.c`'s own `_cyw43_read_reg()` behavior, step 3g-era
        fix) - discarded here exactly like the real driver discards them, the real answer is
        whatever comes after."""
        self.select()
        self.send_word(self._word(_make_cmd(write=False, inc=True, fn=function, addr=addr, size=size)))
        if function == BACKPLANE_FUNCTION:
            for _ in range(CYW43_BACKPLANE_READ_PAD_LEN_BYTES // 4):
                self.recv_word()
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
    rp2040, _bus, master = _wire_up_with_bus()
    return rp2040, master


def _wire_up_with_bus() -> tuple[RP2040, GSPIBus, _FakeGSPIMaster]:
    """Like `_wire_up()`, but also returns the `GSPIBus` itself - needed by tests that drive
    `queue_rx_packet()` directly (step 3e), which has no wire-level equivalent to trigger it
    (real firmware/step 3f/3g would call it from inside SDPCM handling, not built yet)."""
    rp2040 = RP2040()
    bus = GSPIBus()
    bus.attach_gpio(rp2040)
    return rp2040, bus, _FakeGSPIMaster(rp2040)


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


def test_firmware_download_shaped_block_writes_round_trip():
    """Step 3e's 'accept the cyw43_write_bytes() block writes real firmware/CLM download does'
    turns out to already 'just work' via step 3a/3c's generic backplane block-write path - no
    bus.py change was needed for this half of 3e. Mirrors cyw43_download_resource()'s own
    algorithm (cyw43_ll.c): chunk into CYW43_BUS_MAX_BLOCK_SIZE (64 bytes for SPI) pieces, each a
    BACKPLANE_FUNCTION block write at a window-relative, SBSDIO_SB_ACCESS_2_4B_FLAG-tagged
    address, re-selecting the window per chunk exactly like the real driver does - proves multiple
    sequential chunks don't clobber each other, not just a single block transfer in isolation."""
    _rp2040, master = _wire_up()
    block_size = 64
    base = CORE_SOCRAM  # real firmware download's actual target range
    payload = bytes((i * 7 + 3) % 256 for i in range(block_size * 4))

    for offset in range(0, len(payload), block_size):
        dest_addr = base + offset
        _select_backplane_window(master, dest_addr & ~BACKPLANE_ADDR_MASK)
        addr = SBSDIO_SB_ACCESS_2_4B_FLAG | (dest_addr & BACKPLANE_ADDR_MASK)
        chunk = payload[offset : offset + block_size]
        master.write_register(BACKPLANE_FUNCTION, addr, len(chunk), int.from_bytes(chunk, "little"))

    for offset in range(0, len(payload), block_size):
        dest_addr = base + offset
        _select_backplane_window(master, dest_addr & ~BACKPLANE_ADDR_MASK)
        addr = SBSDIO_SB_ACCESS_2_4B_FLAG | (dest_addr & BACKPLANE_ADDR_MASK)
        value = master.read_register(BACKPLANE_FUNCTION, addr, block_size)
        assert value == int.from_bytes(payload[offset : offset + block_size], "little")


def test_queued_rx_packet_is_delivered_via_f2_read():
    """Step 3e's other half: queue_rx_packet() stages the generic inbound-delivery mechanism step
    3f/3g's SDPCM ioctl responses and async events will use - proven here directly against a
    plain F2 (WLAN_FUNCTION) read, without any SDPCM framing involved yet."""
    _rp2040, bus, master = _wire_up_with_bus()
    payload = b"\x01\x02\x03\x04\x05\x06"
    bus.queue_rx_packet(payload)

    value = master.read_register(WLAN_FUNCTION, 0, len(payload))

    assert value == int.from_bytes(payload, "little")


def test_status_register_reports_pending_packet_availability_and_length():
    """The actual field real firmware's cyw43_ll_sdpcm_poll_device() (SPI variant) trusts for
    "is a packet ready, and how big" - STATUS_F2_PKT_AVAILABLE + the length packed into bits
    19:9, not the interrupt register (that's only the earlier "worth even checking" gate)."""
    _rp2040, bus, master = _wire_up_with_bus()
    payload = b"\xaa\xbb\xcc"
    bus.queue_rx_packet(payload)

    status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)

    assert status & STATUS_F2_PKT_AVAILABLE
    assert (status & STATUS_F2_PKT_LEN_MASK) >> STATUS_F2_PKT_LEN_SHIFT == len(payload)


def test_f2_read_consumes_the_packet_and_clears_pending_status():
    _rp2040, bus, master = _wire_up_with_bus()
    payload = b"\xde\xad\xbe\xef"
    bus.queue_rx_packet(payload)

    master.read_register(WLAN_FUNCTION, 0, len(payload))

    status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
    assert not status & STATUS_F2_PKT_AVAILABLE
    interrupt = master.read_register(BUS_FUNCTION, SPI_INTERRUPT_REGISTER, 2)
    assert not interrupt & F2_PACKET_AVAILABLE


def test_queuing_a_packet_while_idle_raises_the_shared_irq_pin():
    """Real firmware's cyw43_cb_read_host_interrupt_pin() (cyw43_ctrl.c) polls WL_D's own level
    directly, independent of any SPI transaction - the mechanism that lets a real driver's GPIO
    interrupt handler notice a pending event without the host having to keep clocking the bus at
    all. Must fire the instant queue_rx_packet() is called while CS is already deasserted, not
    only the next time a transaction happens to start."""
    _rp2040, bus, master = _wire_up_with_bus()
    assert not master.data_pin.input_value  # idle, deselected, nothing pending yet

    bus.queue_rx_packet(b"\x01")

    assert master.data_pin.input_value


def test_irq_pin_drops_once_the_packet_is_fully_consumed():
    _rp2040, bus, master = _wire_up_with_bus()
    bus.queue_rx_packet(b"\x01\x02")

    master.read_register(WLAN_FUNCTION, 0, 2)

    assert not master.data_pin.input_value


def _build_ioctl_request(request_id: int, cmd: int = 2, payload: bytes = b"") -> bytes:
    """Mirrors cyw43_send_ioctl()/cyw43_sdpcm_send_common()'s real wire framing (cyw43_ll.c) -
    used to drive GSPIBus's F2 write parsing (step 3f) the same way real firmware would, rather
    than only testing _build_ioctl_success_response() in isolation."""
    ioctl_header = (
        cmd.to_bytes(4, "little")
        + (len(payload) & 0xFFFF).to_bytes(4, "little")
        + ((request_id << CDCF_IOC_ID_SHIFT) & CDCF_IOC_ID_MASK).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    size = SDPCM_HEADER_LEN + len(ioctl_header) + len(payload)
    sdpcm_header = (
        size.to_bytes(2, "little")
        + (~size & 0xFFFF).to_bytes(2, "little")
        + bytes([0, CONTROL_HEADER, 0, SDPCM_HEADER_LEN, 0, 0])
        + bytes(2)
    )
    return sdpcm_header + ioctl_header + payload


def _send_wlan_frame(master: _FakeGSPIMaster, frame: bytes) -> None:
    master.write_register(WLAN_FUNCTION, 0, len(frame), int.from_bytes(frame, "little"))


def _read_f2_response(master: _FakeGSPIMaster) -> bytes:
    """Drains whatever F2 packet is pending the same way cyw43_ll_sdpcm_poll_device() does: check
    SPI_STATUS_REGISTER's own pending-length field first, then read exactly that many bytes."""
    status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
    length = (status & STATUS_F2_PKT_LEN_MASK) >> STATUS_F2_PKT_LEN_SHIFT
    return master.read_register(WLAN_FUNCTION, 0, length).to_bytes(length, "little")


def test_ioctl_request_produces_a_matching_id_zero_length_response():
    _rp2040, master = _wire_up()
    _send_wlan_frame(master, _build_ioctl_request(request_id=7))

    response = _read_f2_response(master)

    assert len(response) == SDPCM_HEADER_LEN + IOCTL_HEADER_LEN
    flags = int.from_bytes(response[SDPCM_HEADER_LEN + 8 : SDPCM_HEADER_LEN + 12], "little")
    echoed_id = (flags & CDCF_IOC_ID_MASK) >> CDCF_IOC_ID_SHIFT
    assert echoed_id == 7
    status = int.from_bytes(response[SDPCM_HEADER_LEN + 12 : SDPCM_HEADER_LEN + 16], "little")
    assert status == 0


def test_ioctl_response_size_and_checksum_are_self_consistent():
    _rp2040, master = _wire_up()
    _send_wlan_frame(master, _build_ioctl_request(request_id=1))

    response = _read_f2_response(master)

    size = int.from_bytes(response[0:2], "little")
    size_com = int.from_bytes(response[2:4], "little")
    assert size == len(response)
    assert size == (~size_com & 0xFFFF)


def test_ioctl_response_keeps_wireless_flow_control_zero():
    """A nonzero wireless_flow_control stalls every later host send
    (cyw43_sdpcm_send_common(), cyw43_ll.c) - must always be 0 for a chip model that never
    actually wants to throttle the driver."""
    _rp2040, master = _wire_up()
    _send_wlan_frame(master, _build_ioctl_request(request_id=1))

    response = _read_f2_response(master)

    assert response[8] == 0  # wireless_flow_control byte


def test_malformed_size_checksum_request_is_ignored():
    _rp2040, master = _wire_up()
    request = bytearray(_build_ioctl_request(request_id=1))
    request[2] ^= 0xFF  # corrupt size_com's low byte

    _send_wlan_frame(master, bytes(request))

    status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
    assert not status & STATUS_F2_PKT_AVAILABLE


def test_data_header_frame_is_ignored_not_answered_with_an_ioctl_response():
    """DATA_HEADER (outbound Ethernet, step 4's NAT bridge) isn't built yet - must not be
    mistaken for a CONTROL_HEADER ioctl and answered."""
    _rp2040, master = _wire_up()
    request = bytearray(_build_ioctl_request(request_id=1))
    request[5] = DATA_HEADER  # override channel_and_flags's low nibble

    _send_wlan_frame(master, bytes(request))

    status = master.read_register(BUS_FUNCTION, SPI_STATUS_REGISTER, 4)
    assert not status & STATUS_F2_PKT_AVAILABLE


def test_bus_data_credit_increments_across_successive_ioctl_responses():
    """Must strictly exceed the driver's own send count or cyw43_sdpcm_send_common()'s STALL
    check on the *next* send blocks forever (cyw43_ll.c) - see _build_ioctl_success_response()."""
    _rp2040, master = _wire_up()
    _send_wlan_frame(master, _build_ioctl_request(request_id=1))
    first_credit = _read_f2_response(master)[9]

    _send_wlan_frame(master, _build_ioctl_request(request_id=2))
    second_credit = _read_f2_response(master)[9]

    assert second_credit == (first_credit + 1) & 0xFF


def test_ioctl_request_raises_the_shared_irq_pin_while_idle():
    """The response is queued synchronously from inside the F2 write itself, while CS is still
    asserted - proves the pin still ends up high once the transaction completes and CS deselects,
    via _on_cs_change()'s own pending-packet check, not just when queue_rx_packet() is called
    directly while already idle (already covered by the step 3e IRQ tests above)."""
    _rp2040, master = _wire_up()

    _send_wlan_frame(master, _build_ioctl_request(request_id=1))

    assert master.data_pin.input_value


# -- Fixes found booting real MicroPython Pico W firmware against this bus (2026-08-12) --------
# cyw43_ll_bus_init() goes well beyond the F0/ALP/backplane-window/core-reset surface steps
# 2/3b/3c/3d modeled - these three bugs each silently aborted real firmware's own bring-up before
# it ever reached firmware download, with no visible symptom besides a plain OSError: EPERM many
# calls later (cyw43_ll_bus_init()'s own CYW43_WARN() diagnostics are compiled out of release
# firmware). Found by tracing every GSPIBus register access against a real boot, not by reading
# source alone - see docs/CYW43_WIFI_BACKLOG.md's step 3 progress log for the full narrative.


def test_backplane_read_clocks_pad_words_before_the_real_answer():
    """cyw43_bus_pio_spi.c's _cyw43_read_reg(): every BACKPLANE_FUNCTION read (not just windowed
    SB_ACCESS ones) clocks CYW43_BACKPLANE_READ_PAD_LEN_BYTES/4 dummy words before the real
    answer - the driver reads the *last* word as the value, discarding the rest. Drives the wire
    directly (bypassing _FakeGSPIMaster.read_register()'s own padding-skip, which assumes this
    already works) to prove the padding is actually there, not just assumed by the test helper."""
    _rp2040, master = _wire_up()
    master.write_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1, SBSDIO_ALP_AVAIL_REQ)

    master.select()
    master.send_word(
        master._word(_make_cmd(write=False, inc=True, fn=BACKPLANE_FUNCTION, addr=SDIO_CHIP_CLOCK_CSR, size=1))
    )
    pad_words = [master._word(master.recv_word()) for _ in range(CYW43_BACKPLANE_READ_PAD_LEN_BYTES // 4)]
    answer_word = master._word(master.recv_word())
    master.deselect()

    assert pad_words == [0] * (CYW43_BACKPLANE_READ_PAD_LEN_BYTES // 4)
    assert answer_word & SBSDIO_ALP_AVAIL


def test_bus_and_wlan_function_reads_get_no_padding():
    """Only BACKPLANE_FUNCTION reads get the extra padding - BUS_FUNCTION/WLAN_FUNCTION reads
    (e.g. SPI_READ_TEST_REGISTER itself) must still be answered in exactly one word, or the very
    first thing real firmware does (the test-register poll) would break."""
    _rp2040, master = _wire_up()

    master.select()
    master.send_word(
        master._word(_make_cmd(write=False, inc=True, fn=BUS_FUNCTION, addr=SPI_READ_TEST_REGISTER, size=4))
    )
    answer_word = master._word(master.recv_word())
    master.deselect()

    assert answer_word == TEST_PATTERN


def test_alp_available_survives_a_later_clear_to_zero_write():
    """cyw43_ll_bus_init() clears SDIO_CHIP_CLOCK_CSR to 0 immediately after achieving ALP (real
    source's own `alp_set:` label) - a non-sticky model would silently un-set SBSDIO_ALP_AVAIL on
    that very write, even though real hardware's availability reflects actual clock-lock state,
    not the last-written request byte."""
    _rp2040, master = _wire_up()
    master.write_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1, SBSDIO_ALP_AVAIL_REQ)

    master.write_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1, 0)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1)
    assert value & SBSDIO_ALP_AVAIL


def test_ht_available_becomes_readable_once_the_wlan_arm_core_is_up_with_no_ht_request_sent():
    """cyw43_ll_bus_init() (cyw43_ll.c:~1655-1667) calls reset_device_core(CORE_WLAN_ARM, false)
    then polls SDIO_CHIP_CLOCK_CSR for SBSDIO_HT_AVAIL - with no SDIO_CHIP_CLOCK_CSR HT-request
    write anywhere in between (unlike the earlier ALP handshake, which does request first). Real
    hardware brings HT up as a side effect of the ARM core actually running its own firmware;
    since this project doesn't emulate a second CPU core, the core reaching device_core_is_up()'s
    own "up" condition is the trigger instead - reproduces reset_device_core()'s exact write
    sequence, not just the end state, since _maybe_mark_ht_available() checks after every write."""
    _rp2040, master = _wire_up()
    window = CORE_WLAN_ARM & ~BACKPLANE_ADDR_MASK
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)
    ioctrl_addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((CORE_WLAN_ARM + AI_IOCTRL_OFFSET) & BACKPLANE_ADDR_MASK)
    resetctrl_addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((CORE_WLAN_ARM + AI_RESETCTRL_OFFSET) & BACKPLANE_ADDR_MASK)

    # disable_device_core() (real CORE_WLAN_ARM default: AIRC_RESET already set - no write needed)
    # reset_device_core()'s own sequence:
    master.write_register(BACKPLANE_FUNCTION, ioctrl_addr, 1, SICF_FGC | SICF_CLOCK_EN)
    assert not master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1) & SBSDIO_HT_AVAIL
    master.write_register(BACKPLANE_FUNCTION, resetctrl_addr, 1, 0)
    assert not master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1) & SBSDIO_HT_AVAIL
    master.write_register(BACKPLANE_FUNCTION, ioctrl_addr, 1, SICF_CLOCK_EN)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1)
    assert value & SBSDIO_HT_AVAIL


def test_socram_core_reaching_up_state_does_not_trigger_ht_available():
    """Scoped to CORE_WLAN_ARM specifically - only a running core would plausibly request its own
    clock; CORE_SOCRAM is just memory, bringing it "up" must not be mistaken for the ARM core."""
    _rp2040, master = _wire_up()
    window = CORE_SOCRAM & ~BACKPLANE_ADDR_MASK
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_LOW, 1, (window >> 8) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_MID, 1, (window >> 16) & 0xFF)
    master.write_register(BACKPLANE_FUNCTION, SDIO_BACKPLANE_ADDRESS_HIGH, 1, (window >> 24) & 0xFF)
    ioctrl_addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((CORE_SOCRAM + AI_IOCTRL_OFFSET) & BACKPLANE_ADDR_MASK)
    resetctrl_addr = SBSDIO_SB_ACCESS_2_4B_FLAG | ((CORE_SOCRAM + AI_RESETCTRL_OFFSET) & BACKPLANE_ADDR_MASK)

    master.write_register(BACKPLANE_FUNCTION, ioctrl_addr, 1, SICF_FGC | SICF_CLOCK_EN)
    master.write_register(BACKPLANE_FUNCTION, resetctrl_addr, 1, 0)
    master.write_register(BACKPLANE_FUNCTION, ioctrl_addr, 1, SICF_CLOCK_EN)

    value = master.read_register(BACKPLANE_FUNCTION, SDIO_CHIP_CLOCK_CSR, 1)
    assert not value & SBSDIO_HT_AVAIL


def test_sdio_function2_watermark_register_round_trips():
    """A Bluetooth-gated check in cyw43_ll_bus_init() writes then reads back this exact register,
    failing the whole bring-up on a mismatch - it sits 2 bytes below SDIO_BACKPLANE_ADDRESS_LOW,
    the F1 register bank's previous (too-narrow) lower bound, so real firmware silently aborted
    right after the ALP handshake, before ever reaching firmware download."""
    _rp2040, master = _wire_up()

    master.write_register(BACKPLANE_FUNCTION, SDIO_FUNCTION2_WATERMARK, 1, 0x10)

    assert master.read_register(BACKPLANE_FUNCTION, SDIO_FUNCTION2_WATERMARK, 1) == 0x10
