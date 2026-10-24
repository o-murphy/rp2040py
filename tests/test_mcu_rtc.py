"""The RTC built by the chip's C++ `Mcu` (docs/records/0096-cpp-mcu-core.md, step 3a, block 1): on the native chip the `Mcu` initialises the block, maps its window and binds its line, and the Python RTC is a
view of it; its warnings go through the one host interface of the `Mcu`. The pure-Python chip is the reference, so the same checks run on both.
"""

import pytest
from utils.chip_pair import Native, PurePython, make_chip

from rp2040py.irq import IRQ

RTC = 0x4005C000
CLKDIV_M1, SETUP_0, INTE, INTF = 0x00, 0x04, 0x24, 0x28


class Recorder:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error"):
            return lambda component, message: self.messages.append((name, component, message))
        raise AttributeError(name)


@pytest.fixture(params=[PurePython, Native], ids=["pure", "native"])
def chip(request):
    chip = make_chip(request.param)
    chip.logger = Recorder()
    return chip


def test_a_register_that_does_not_exist_warns_through_the_logger(chip):
    chip.read_uint32(RTC + 0x3C)
    assert chip.logger.messages == [("warning", "RTC_BASE", "Unimplemented peripheral read from 0x3c")]


def test_a_write_to_a_register_that_does_not_exist_warns_with_its_value(chip):
    chip.write_uint32(RTC + 0x3C, 0x1234)
    assert chip.logger.messages == [("warning", "RTC_BASE", "Unimplemented peripheral write to 0x3c: 0x1234")]


def test_the_force_register_raises_the_line_on_the_cpu(chip):
    assert not chip.core.pending_interrupts & (1 << IRQ.RTC)
    chip.write_uint32(RTC + INTF, 1)
    assert chip.core.pending_interrupts & (1 << IRQ.RTC)
    chip.write_uint32(RTC + INTF, 0)
    assert not chip.core.pending_interrupts & (1 << IRQ.RTC)


def test_the_python_rtc_is_a_view_of_the_block_the_bus_reaches(chip):
    chip.rtc.write_uint32(CLKDIV_M1, 46874)
    assert chip.read_uint32(RTC + CLKDIV_M1) == 46874
    chip.write_uint32(RTC + SETUP_0, (2024 << 12) | (2 << 8) | 29)
    assert chip.rtc.snapshot()["setup0"] == (2024 << 12) | (2 << 8) | 29


def test_a_replaced_rtc_takes_the_window_over(chip):
    class Stand:
        def read_uint32(self, offset):
            return 0xABCD

        def write_uint32(self, offset, value):
            pass

        def write_uint32_atomic(self, offset, value, atomic_type):
            pass

    chip.peripherals[0x4005C] = Stand()
    assert chip.read_uint32(RTC + CLKDIV_M1) == 0xABCD


TBMAN = 0x4006C000


def test_tbman_built_by_the_chip_says_asic_and_warns_for_a_register_that_does_not_exist(chip):
    assert chip.read_uint32(TBMAN) == 1
    chip.write_uint32(TBMAN, 0xFFFFFFFF)  # read-only: no effect, no warning
    assert chip.logger.messages == []
    chip.read_uint32(TBMAN + 0x10)
    assert chip.logger.messages == [("warning", "TBMAN_BASE", "Unimplemented peripheral read from 0x10")]


@pytest.mark.parametrize(
    "base, name",
    [
        (0x40030000, "BUSCTRL_BASE"),
        (0x40064000, "VREG_AND_CHIP_RESET_BASE"),
        (0x40000000, "SYSINFO_BASE"),
        (0x40004000, "SYSCFG"),
    ],
)
def test_the_register_blocks_the_chip_built_warn_under_their_own_names(chip, base, name):
    chip.read_uint32(base + 0x3C)  # no such register in any of them
    assert chip.logger.messages == [("warning", name, "Unimplemented peripheral read from 0x3c")]


def test_sysinfo_reads_the_revision_off_the_bootrom_the_chip_holds(chip):
    chip.bootrom[4] = (chip.bootrom[4] & 0x00FFFFFF) | (3 << 24)  # ROM address 0x13: rp2040_rom_version() = 3 (B2)
    assert chip.read_uint32(0x40000000) >> 28 == 2
    chip.bootrom[4] = (chip.bootrom[4] & 0x00FFFFFF) | (1 << 24)
    assert chip.read_uint32(0x40000000) >> 28 == 1


def test_syscfg_nmi_mask_is_the_cpus_own(chip):
    chip.write_uint32(0x40004000, 0x6)
    assert chip.core.interrupt_nmi_mask == 0x6


@pytest.mark.parametrize(
    "base, name",
    [
        (0x40054000, "TIMER_BASE"),
        (0x4004C000, "ADC"),
        (0x40034000, "UART0"),
        (0x40038000, "UART1"),
        (0x4003C000, "SPI0"),
        (0x40040000, "SPI1"),
        (0x40044000, "I2C0"),
        (0x40048000, "I2C1"),
    ],
)
def test_the_blocks_with_lines_and_devices_warn_under_their_own_names(chip, base, name):
    chip.read_uint32(base + 0x200)  # no such register in any of them
    assert chip.logger.messages == [("warning", name, "Unimplemented peripheral read from 0x200")]


def test_an_adc_conversion_asks_the_device_the_host_attached(chip):
    seen: list[int] = []
    chip.adc.on_adc_read = seen.append  # type: ignore[method-assign]
    chip.write_uint32(0x4004C000, 0b101)  # CS: EN | START_ONCE
    chip.clock.tick(1e6)
    assert seen == [0]


def test_an_spi_word_on_the_wire_goes_to_the_device_the_host_attached(chip):
    words: list[int] = []
    chip.spi[0].on_transmit = words.append  # type: ignore[method-assign]
    chip.write_uint32(0x4003C000 + 0x00, 0x7)  # SSPCR0: 8-bit frames
    chip.write_uint32(0x4003C000 + 0x04, 0x2)  # SSPCR1: SSE
    chip.write_uint32(0x4003C000 + 0x08, 0x5A)  # SSPDR
    assert words == [0x5A]


@pytest.mark.parametrize(
    "base, name",
    [
        (0x40008000, "CLOCKS_BASE"),
        (0x40028000, "PLL_SYS_BASE"),
        (0x4002C000, "PLL_USB_BASE"),
        (0x40024000, "XOSC_BASE"),
        (0x40060000, "ROSC_BASE"),
    ],
)
def test_the_clock_tree_and_the_oscillators_warn_under_their_own_names(chip, base, name):
    chip.read_uint32(base + 0x200)
    assert chip.logger.messages == [("warning", name, "Unimplemented peripheral read from 0x200")]


def test_the_peripheral_clock_is_the_trees_and_can_be_fixed_by_the_host(chip):
    assert chip.clk_peri == 125e6
    chip.clk_peri = 48e6
    assert chip.clk_peri == 48e6


@pytest.mark.parametrize(
    "address, name, offset",
    [
        (0xE000EF00, "PPB", "0xf00"),
        (0x40058200, "WATCHDOG_BASE", "0x200"),
        (0x50000600, "DMA", "0x600"),
    ],
)
def test_the_ppb_the_watchdog_and_the_dma_warn_under_their_own_names(chip, address, name, offset):
    chip.read_uint32(address)
    assert chip.logger.messages == [("warning", name, f"Unimplemented peripheral read from {offset}")]


def test_the_watchdog_tick_reaches_the_timer_and_systick_and_then_the_hosts_listeners(chip):
    heard: list[float] = []
    chip.watchdog.add_tick_listener(heard.append)
    chip.watchdog.clk_ref_changed(12e6)
    chip.write_uint32(0x40058000 + 0x2C, 12 | (1 << 9))  # WATCHDOG.TICK: 12 cycles of clk_ref, enabled
    assert chip.timer.tick_hz == 1e6 and chip.ppb.tick_hz == 1e6
    assert heard == [1e6]


def test_a_watchdog_trigger_is_the_chips_reset_whatever_the_hook_is(chip):
    calls: list[str] = []
    chip.watchdog.on_watchdog_trigger = lambda: calls.append("reset")  # type: ignore[method-assign]
    chip.write_uint32(0x40058000 + 0x00, 1 << 31)  # WATCHDOG.CTRL.TRIGGER
    assert calls == ["reset"]


@pytest.mark.parametrize(
    "base, name",
    [
        (0x40014000, "IO_BANK0_BASE"),
        (0x40018000, "IO_QSPI_BASE"),
        (0x4001C000, "PADS_BANK0_BASE"),
        (0x40020000, "PADS_QSPI_BASE"),
    ],
)
def test_the_io_and_pads_blocks_on_the_pin_banks_warn_under_their_own_names(chip, base, name):
    chip.read_uint32(base + 0x600)
    assert chip.logger.messages == [("warning", name, "Unimplemented peripheral read from 0x600")]


def test_a_pin_driven_by_the_firmware_tells_its_listeners(chip):
    heard: list[tuple] = []
    chip.gpio[5]._listeners.add(lambda new, old: heard.append((new.name, old.name)))
    chip.write_uint32(
        0x40014000 + 0x04 + 8 * 5, 5 | (3 << 8) | (3 << 12)
    )  # GPIO5_CTRL: SIO, OUTOVER = high, OEOVER = enable
    assert heard and heard[-1][0] == "HIGH"


def test_the_io_bank0_line_follows_a_forced_pin_irq_and_comes_down_with_it(chip):
    assert not chip.core.pending_interrupts & (1 << IRQ.IO_BANK0)
    chip.write_uint32(0x40014000 + 0x110, 1)  # PROC0_INTF0: force pin 0's level-low interrupt
    assert chip.core.pending_interrupts & (1 << IRQ.IO_BANK0)
    chip.write_uint32(0x40014000 + 0x110, 0)
    assert not chip.core.pending_interrupts & (1 << IRQ.IO_BANK0)


def test_sio_warns_under_its_own_name_for_an_address_it_does_not_have(chip):
    chip.read_uint32(0xD0000000 + 0x300)
    assert chip.logger.messages and chip.logger.messages[0][1] == "SIO"


def test_sio_drives_a_pin_and_reads_the_inputs_of_the_banks_the_chip_holds(chip):
    heard: list[str] = []
    chip.gpio[7]._listeners.add(lambda new, old: heard.append(new.name))
    chip.gpio[3].set_input_value(True)
    assert chip.read_uint32(0xD0000004) & (1 << 3)  # GPIO_IN
    chip.write_uint32(0x40014000 + 0x04 + 8 * 7, 5)  # GPIO7_CTRL: SIO
    chip.write_uint32(0xD0000000 + 0x24, 1 << 7)  # GPIO_OE_SET
    chip.write_uint32(0xD0000000 + 0x14, 1 << 7)  # GPIO_OUT_SET
    assert heard[-1] == "HIGH"
    chip.write_uint32(0xD0000000 + 0x18, 1 << 7)  # GPIO_OUT_CLR
    assert heard[-1] == "LOW"
