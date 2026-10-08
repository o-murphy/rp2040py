"""The clock tree (docs/records/0096-cpp-mcu-core.md, "Clocks"): PLL_SYS/PLL_USB and the CLOCKS registers derive `clk_sys`/`clk_peri`, and what runs from them follows.

The sixteen cases of rp2040js 1.4.0's `clocks.spec.ts` are ported one to one, with the same register values and expectations; a few of our own follow (the PLL's registers, a chip
reset). Each runs on the chip the facade gives, so it holds for the pure-Python and the native chip alike.
"""

from utils.tick import start_tick

from rp2040py.rp2040 import RP2040

MHZ = 1_000_000

CLOCKS_BASE = 0x40008000
CLK_REF_CTRL = CLOCKS_BASE + 0x30
CLK_REF_DIV = CLOCKS_BASE + 0x34
CLK_SYS_CTRL = CLOCKS_BASE + 0x3C
CLK_SYS_DIV = CLOCKS_BASE + 0x40
CLK_PERI_CTRL = CLOCKS_BASE + 0x48

PLL_SYS_BASE = 0x40028000
PLL_USB_BASE = 0x4002C000
PLL_CS, PLL_PWR, PLL_FBDIV_INT, PLL_PRIM = 0x00, 0x04, 0x08, 0x0C

UART0_BASE = 0x40034000
UARTIBRD = UART0_BASE + 0x24
UARTFBRD = UART0_BASE + 0x28

CLK_REF_SRC_XOSC = 0x2  # CLK_REF_CTRL.SRC = xosc_clksrc
CLK_SYS_SRC_AUX = 0x1  # CLK_SYS_CTRL.SRC = clksrc_clk_sys_aux
CLK_SYS_AUXSRC_PLL_SYS = 0x0 << 5
CLK_SYS_AUXSRC_XOSC = 0x3 << 5
CLK_SYS_AUXSRC_GPIN0 = 0x4 << 5
CLK_PERI_ENABLE = 1 << 11
CLK_PERI_AUXSRC_CLK_SYS = 0x0 << 5
CLK_PERI_AUXSRC_PLL_USB = 0x2 << 5

WATCHDOG_RESET_ALL = {"preserve_flash": True}


def _init_pll(chip: RP2040, base: int, refdiv: int = 1, fbdiv: int = 125, postdiv1: int = 6, postdiv2: int = 2) -> None:
    """Configures a PLL the way pico-sdk's `pll_init()` does."""
    chip.write_uint32(base + PLL_CS, refdiv)
    chip.write_uint32(base + PLL_FBDIV_INT, fbdiv)
    chip.write_uint32(base + PLL_PRIM, (postdiv1 << 16) | (postdiv2 << 12))


def _set_sys_clock(chip: RP2040, **pll) -> None:
    """PLL_SYS, then clk_ref on the crystal and clk_sys on PLL_SYS, as `clocks_init()`/`set_sys_clock_khz()` do."""
    _init_pll(chip, PLL_SYS_BASE, **pll)
    chip.write_uint32(CLK_REF_CTRL, CLK_REF_SRC_XOSC)
    chip.write_uint32(CLK_SYS_CTRL, CLK_SYS_SRC_AUX | CLK_SYS_AUXSRC_PLL_SYS)


def _arduino_pico_200() -> dict:
    return {"fbdiv": 100, "postdiv1": 6, "postdiv2": 1}  # 12 MHz / 1 * 100 = 1200 MHz, / (6 * 1) = 200 MHz


def test_clk_sys_defaults_to_125_mhz_until_the_firmware_configures_the_clock_tree():
    assert RP2040().clk_sys == 125 * MHZ


def test_a_200_mhz_clk_sys_the_arduino_pico_default():
    chip = RP2040()
    _set_sys_clock(chip, **_arduino_pico_200())
    assert chip.clk_sys == 200 * MHZ


def test_clk_sys_div_integer_and_fractional_divisor():
    chip = RP2040()
    _set_sys_clock(chip)
    chip.write_uint32(CLK_SYS_DIV, (2 << 8) | 128)  # divide by 2.5
    assert chip.clk_sys == 50 * MHZ


def test_a_clk_sys_div_integer_part_of_zero_divides_by_two_to_the_sixteen():
    chip = RP2040()
    _set_sys_clock(chip)
    chip.write_uint32(CLK_SYS_DIV, 0)
    assert chip.clk_sys == (125 * MHZ) / 0x10000


def test_clk_sys_runs_straight_off_the_crystal_when_auxsrc_selects_xosc():
    chip = RP2040()
    chip.write_uint32(CLK_SYS_CTRL, CLK_SYS_SRC_AUX | CLK_SYS_AUXSRC_XOSC)
    assert chip.clk_sys == 12 * MHZ


def test_clk_sys_follows_clk_ref_when_src_selects_it():
    chip = RP2040()
    chip.write_uint32(CLK_REF_CTRL, CLK_REF_SRC_XOSC)
    chip.write_uint32(CLK_REF_DIV, 2 << 8)
    chip.write_uint32(CLK_SYS_CTRL, 0)  # SRC = clk_ref
    assert chip.clk_sys == 6 * MHZ


def test_the_last_good_clk_sys_is_kept_for_a_source_that_is_not_modelled():
    chip = RP2040()
    _set_sys_clock(chip, **_arduino_pico_200())
    chip.write_uint32(CLK_SYS_CTRL, CLK_SYS_SRC_AUX | CLK_SYS_AUXSRC_GPIN0)
    assert chip.clk_sys == 200 * MHZ


def test_clock_listeners_are_told_when_clk_sys_changes():
    chip = RP2040()
    calls: list[tuple[float, float]] = []
    chip.add_clock_listener(lambda clk_sys, old: calls.append((clk_sys, old)))
    _set_sys_clock(chip, **_arduino_pico_200())
    assert calls[-1] == (200 * MHZ, 12 * MHZ)


def test_clock_listeners_are_not_told_when_clk_sys_is_unchanged():
    chip = RP2040()
    _set_sys_clock(chip, **_arduino_pico_200())
    calls = []
    chip.add_clock_listener(lambda *args: calls.append(args))
    chip.write_uint32(CLK_SYS_CTRL, CLK_SYS_SRC_AUX | CLK_SYS_AUXSRC_PLL_SYS)  # re-selects the same source
    assert calls == []


def test_a_clock_listener_that_unsubscribed_is_not_told():
    chip = RP2040()
    calls = []
    unsubscribe = chip.add_clock_listener(lambda *args: calls.append(args))
    unsubscribe()
    _set_sys_clock(chip, **_arduino_pico_200())
    assert calls == []


SYST_CSR = 0xE000E010


def test_systick_and_the_pwm_counters_are_retuned_when_clk_sys_changes():
    chip = RP2040()
    chip.write_uint32(
        SYST_CSR, 1 << 2
    )  # CLKSOURCE = processor clock; 0 is the 1 MHz reference clock, which clk_sys does not move
    _set_sys_clock(chip, **_arduino_pico_200())
    assert chip.ppb.systick_timer.frequency == 200 * MHZ
    assert chip.pwm.channels[0].timer.frequency == 200 * MHZ


def test_clk_peri_follows_clk_sys_when_auxsrc_selects_it():
    chip = RP2040()
    _set_sys_clock(chip, **_arduino_pico_200())
    chip.write_uint32(CLK_PERI_CTRL, CLK_PERI_ENABLE | CLK_PERI_AUXSRC_CLK_SYS)
    assert chip.clk_peri == 200 * MHZ


def test_clk_peri_runs_at_48_mhz_off_the_usb_pll_as_arduino_pico_does_above_125_mhz():
    chip = RP2040()
    _init_pll(chip, PLL_USB_BASE, fbdiv=40, postdiv1=5, postdiv2=2)  # 12 / 1 * 40 / (5 * 2)
    _set_sys_clock(chip, **_arduino_pico_200())
    chip.write_uint32(CLK_PERI_CTRL, CLK_PERI_ENABLE | CLK_PERI_AUXSRC_PLL_USB)
    assert chip.clk_peri == 48 * MHZ


def test_the_last_good_clk_peri_is_kept_while_the_clock_generator_is_stopped():
    chip = RP2040()
    chip.write_uint32(CLK_PERI_CTRL, CLK_PERI_AUXSRC_PLL_USB)  # ENABLE clear
    assert chip.clk_peri == 125 * MHZ


def test_the_uart_is_told_again_when_clk_peri_changes_after_its_divider_is_set():
    chip = RP2040()
    _init_pll(chip, PLL_USB_BASE, fbdiv=40, postdiv1=5, postdiv2=2)
    # uart_init(uart0, 115200) against a 48 MHz clk_peri: 48e6 / (16 * 115200) = 26.0417
    chip.write_uint32(UARTIBRD, 26)
    chip.write_uint32(UARTFBRD, 3)
    seen: list[int] = []
    chip.uart[0].on_baud_rate_change = seen.append
    chip.write_uint32(CLK_PERI_CTRL, CLK_PERI_ENABLE | CLK_PERI_AUXSRC_PLL_USB)
    assert seen == [115177]  # 48e6 / (26.046875 * 16), rounded


def test_a_uart_whose_divider_is_not_set_is_not_told():
    chip = RP2040()
    _init_pll(chip, PLL_USB_BASE, fbdiv=40, postdiv1=5, postdiv2=2)
    seen: list[int] = []
    chip.uart[0].on_baud_rate_change = seen.append
    chip.write_uint32(CLK_PERI_CTRL, CLK_PERI_ENABLE | CLK_PERI_AUXSRC_PLL_USB)
    assert seen == []


# --- ours, not upstream's ---------------------------------------------------------------------------------------------------


def test_the_pll_registers_read_back_and_always_report_locked():
    chip = RP2040()
    assert chip.read_uint32(PLL_SYS_BASE + PLL_CS) == 0x8000_0001  # REFDIV = 1, LOCK
    assert chip.read_uint32(PLL_SYS_BASE + PLL_PWR) == 0x2D
    assert chip.read_uint32(PLL_SYS_BASE + PLL_PRIM) == 0x77000
    _init_pll(chip, PLL_SYS_BASE, refdiv=2, fbdiv=100, postdiv1=3, postdiv2=2)
    assert chip.read_uint32(PLL_SYS_BASE + PLL_CS) == 0x8000_0002
    assert chip.read_uint32(PLL_SYS_BASE + PLL_FBDIV_INT) == 100
    assert chip.read_uint32(PLL_SYS_BASE + PLL_PRIM) == (3 << 16) | (2 << 12)


def test_a_bypassed_pll_outputs_the_reference_divided_by_refdiv_alone():
    chip = RP2040()
    chip.write_uint32(PLL_SYS_BASE + PLL_FBDIV_INT, 100)
    chip.write_uint32(PLL_SYS_BASE + PLL_CS, (1 << 8) | 2)  # BYPASS, REFDIV = 2
    assert chip.pll_sys.frequency == 6 * MHZ


def test_a_chip_reset_puts_the_clock_tree_back_to_its_defaults_and_retunes_what_ran_from_it():
    chip = RP2040()
    start_tick(chip)  # clk_ref from the crystal, the watchdog tick at 1 us
    chip.write_uint32(SYST_CSR, 1 << 2)
    _set_sys_clock(chip, **_arduino_pico_200())
    seen: list[tuple[float, float]] = []
    chip.add_clock_listener(lambda clk_sys, old: seen.append((clk_sys, old)))
    chip.reset(**WATCHDOG_RESET_ALL)
    assert chip.clk_sys == 125 * MHZ and chip.clk_peri == 125 * MHZ
    assert seen == [(125 * MHZ, 200 * MHZ)]
    assert chip.ppb.clk_sys == 125 * MHZ
    # SysTick's CLKSOURCE is back to 0: the reference clock, the watchdog's tick. The reset put CLK_REF back on the ring oscillator (6.5 MHz) while the watchdog - which a
    # watchdog reset does not reset - still divides by the 12 cycles the firmware set, so the tick is no longer 1 MHz: the same as on silicon until the firmware reconfigures it.
    assert chip.ppb.systick_timer.frequency == 6.5 * MHZ / 12
    assert chip.read_uint32(PLL_SYS_BASE + PLL_PRIM) == 0x77000
    assert chip.read_uint32(CLK_SYS_CTRL) == 0
