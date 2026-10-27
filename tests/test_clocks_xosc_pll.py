"""The PLL's power bits, LOCK, ENABLED0/1 and the crystal as the tree's input, against the datasheet (2.15, 2.18) and the SDK that drives them (docs/tasks/clocks-port.md has the findings and their sources).

Every test says which table or section it follows. They run on both builds: the clock tree is Python on both until its C++ port, and after it the oracle holds the native block to this reference.
"""

import pytest

from rp2040py.peripherals import _clocks as C
from rp2040py.peripherals import _pll as P
from rp2040py.rp2040 import RP2040

CLOCKS_BASE = 0x40008000
PLL_SYS_BASE = 0x40028000
PLL_USB_BASE = 0x4002C000
XOSC_BASE = 0x40024000
XOSC_CTRL_ENABLE = (0xFAB << 12) | 0xAA0
XOSC_CTRL_DISABLE = (0xD1E << 12) | 0xAA0


@pytest.fixture
def mcu() -> RP2040:
    chip = RP2040()
    chip.logger.warning = lambda *_: None  # type: ignore[method-assign]
    return chip


def pll(mcu: RP2040, base: int, offset: int) -> int:
    return mcu.read_uint32(base + offset)


def alias(mcu: RP2040, address: int, mask: int, kind: int) -> None:
    """A write through the bus's atomic alias of a register: 2 = SET (+0x2000), 3 = CLR (+0x3000), like the SDK's `hw_set_bits` / `hw_clear_bits`."""
    mcu.write_uint32(address + {2: 0x2000, 3: 0x3000}[kind], mask)


def start_crystal(mcu: RP2040) -> None:
    mcu.write_uint32(XOSC_BASE + 0x00, XOSC_CTRL_ENABLE)


def stop_crystal(mcu: RP2040) -> None:
    mcu.write_uint32(XOSC_BASE + 0x00, XOSC_CTRL_DISABLE)


def configure_like_pll_init(mcu: RP2040, base: int, refdiv: int, fbdiv: int, postdiv1: int, postdiv2: int) -> None:
    """pico-sdk `pll_init` (hardware_pll/pll.c): program REFDIV and FBDIV, clear PD and VCOPD, wait for LOCK, program the post dividers, clear POSTDIVPD."""
    mcu.write_uint32(base + P.PLL_CS, refdiv)
    mcu.write_uint32(base + P.PLL_FBDIV_INT, fbdiv)
    alias(mcu, base + P.PLL_PWR, P.PLL_PWR_PD | P.PLL_PWR_VCOPD, 3)  # CLR alias
    assert mcu.read_uint32(base + P.PLL_CS) & P.PLL_CS_LOCK
    mcu.write_uint32(base + P.PLL_PRIM, (postdiv1 << 16) | (postdiv2 << 12))
    alias(mcu, base + P.PLL_PWR, P.PLL_PWR_POSTDIVPD, 3)


# ---- the PLL (2.18) ----


def test_a_pll_out_of_reset_is_powered_down_and_not_locked(mcu):
    """Tables 275, 276: PWR resets to PD, DSMPD, POSTDIVPD and VCOPD set (0x2D); CS.LOCK ("PLL is locked", RO) resets to 0."""
    assert pll(mcu, PLL_SYS_BASE, P.PLL_PWR) == 0x2D
    assert not pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 0


def test_the_sdk_pll_init_sequence_locks_and_gives_125_mhz(mcu):
    """2.18.3: "Turn on the main power and VCO, wait for the VCO to lock, set up post dividers and turn them on" - 12 MHz / 1 * 125 = 1500 MHz, / 6 / 2 = 125 MHz."""
    start_crystal(mcu)
    configure_like_pll_init(mcu, PLL_SYS_BASE, 1, 125, 6, 2)
    assert mcu.pll_sys.frequency == 125e6
    configure_like_pll_init(mcu, PLL_USB_BASE, 1, 100, 5, 5)
    assert mcu.pll_usb.frequency == 48e6


def test_the_output_waits_for_the_post_dividers(mcu):
    """Table 276 POSTDIVPD: "PLL post divider powerdown". With the VCO running and the post dividers still powered down there is no output."""
    start_crystal(mcu)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_CS, 1)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_FBDIV_INT, 125)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PRIM, (6 << 16) | (2 << 12))
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_PD | P.PLL_PWR_VCOPD, 3)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 0
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_POSTDIVPD, 3)
    assert mcu.pll_sys.frequency == 125e6


def test_powerdown_stops_the_output_and_the_lock(mcu):
    """Table 276 PD: "PLL powerdown"; VCOPD: "PLL VCO powerdown" - either one stops the VCO, so LOCK drops."""
    start_crystal(mcu)
    configure_like_pll_init(mcu, PLL_SYS_BASE, 1, 125, 6, 2)
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_VCOPD, 2)  # SET
    assert not pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 0
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_VCOPD, 3)
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_PD, 2)
    assert not pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 0


def test_bypass_passes_the_reference_even_with_the_vco_powered_down(mcu):
    """Table 275 BYPASS: "Passes the reference clock to the output instead of the divided VCO"; VCOPD / POSTDIVPD: "set high when PLL output not required or bypass=1". PD still stops the output."""
    start_crystal(mcu)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_CS, P.PLL_CS_BYPASS | 1)
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_PD, 3)  # PD clear, VCOPD and POSTDIVPD set
    assert mcu.pll_sys.frequency == 12e6
    alias(mcu, PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_PD, 2)
    assert mcu.pll_sys.frequency == 0


def test_the_pll_has_no_reference_until_the_crystal_runs(mcu):
    """2.18.1: "the FREF (reference) input is connected to the crystal oscillator's XI input": a PLL with its VCO powered but the XOSC disabled has no lock and no output."""
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_FBDIV_INT, 125)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PRIM, (6 << 16) | (2 << 12))
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PWR, P.PLL_PWR_DSMPD)
    assert not pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 0
    start_crystal(mcu)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK
    assert mcu.pll_sys.frequency == 125e6
    stop_crystal(mcu)
    assert not pll(mcu, PLL_SYS_BASE, P.PLL_CS) & P.PLL_CS_LOCK


def test_the_pll_registers_keep_their_defined_bits(mcu):
    """Tables 275-278: CS keeps BYPASS and REFDIV; PWR keeps VCOPD, POSTDIVPD, DSMPD and PD; FBDIV_INT 11:0; PRIM POSTDIV1 and POSTDIV2."""
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_CS, 0xFFFFFFFF)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_CS) & ~P.PLL_CS_LOCK == (P.PLL_CS_BYPASS | 0x3F)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PWR, 0xFFFFFFFF)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_PWR) == 0x2D
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_FBDIV_INT, 0xFFFFFFFF)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_FBDIV_INT) == 0xFFF
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PRIM, 0xFFFFFFFF)
    assert pll(mcu, PLL_SYS_BASE, P.PLL_PRIM) == 0x77000


# ---- CLOCKS (2.15) ----


def test_enabled0_and_enabled1_exist_and_are_read_only(mcu):
    """Tables 251, 252: ENABLED0 (0xB0) and ENABLED1 (0xB4), "indicates the state of the clock enable", read only, reset 0x0 (the per-destination enables are not modelled)."""
    for offset in (C.ENABLED0, C.ENABLED1):
        assert mcu.read_uint32(CLOCKS_BASE + offset) == 0
        mcu.write_uint32(CLOCKS_BASE + offset, 0xFFFFFFFF)
        assert mcu.read_uint32(CLOCKS_BASE + offset) == 0


def test_a_clock_sourced_from_a_stopped_crystal_does_not_run(mcu):
    """2.15.2.2 / table 220: clk_ref's SRC = 2 selects the crystal oscillator. Disabled, it delivers no clock; enabled, 12 MHz."""
    mcu.write_uint32(CLOCKS_BASE + C.CLK_REF_CTRL, C.CLK_REF_CTRL_SRC_XOSC)
    assert mcu.clocks.snapshot()["ref_freq"] == 0
    start_crystal(mcu)
    assert mcu.clocks.snapshot()["ref_freq"] == 12e6
    stop_crystal(mcu)
    assert mcu.clocks.snapshot()["ref_freq"] == 0


def test_the_frequency_counter_sees_a_stopped_crystal_as_dead(mcu):
    """2.15.4: a count of a clock that is not running ends with DIED (and FAIL)."""
    mcu.write_uint32(CLOCKS_BASE + C.FC0_REF_KHZ, 12000)
    mcu.write_uint32(CLOCKS_BASE + C.FC0_SRC, C.FC0_SRC_XOSC)
    mcu.clock.tick(5_000_000)
    assert mcu.read_uint32(CLOCKS_BASE + C.FC0_STATUS) & C.FC0_STATUS_DIED
    start_crystal(mcu)
    mcu.write_uint32(CLOCKS_BASE + C.FC0_SRC, C.FC0_SRC_XOSC)
    mcu.clock.tick(5_000_000)
    status = mcu.read_uint32(CLOCKS_BASE + C.FC0_STATUS)
    assert status & C.FC0_STATUS_DONE and not status & C.FC0_STATUS_DIED
    assert mcu.read_uint32(CLOCKS_BASE + C.FC0_RESULT) >> C.FC0_RESULT_FRAC_BITS == 12000


def test_starting_the_crystal_after_the_tree_is_configured_retunes_what_runs_from_it(mcu):
    """Datasheet 2.16: nothing is clocked from XOSC until it runs. A PLL and a clk_sys already set up on it start delivering the moment the crystal is enabled (the chip's tree hears of it
    - not only of the next write to a CLOCKS or PLL register)."""
    mcu.write_uint32(
        CLOCKS_BASE + C.CLK_SYS_CTRL, 1 | (C.CLK_SYS_CTRL_AUXSRC_PLL_SYS << C.CLK_SYS_CTRL_AUXSRC_SHIFT)
    )  # clk_sys on PLL_SYS first: a bare chip's clk_sys follows the ring oscillator (clk_ref's reset source) at the first write that finds nothing better
    # pll_init() without the wait for LOCK (it would never come): 12 MHz * 100 / 6 = 200 MHz, once there is a 12 MHz
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_CS, 1)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_FBDIV_INT, 100)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PWR, 0)
    mcu.write_uint32(PLL_SYS_BASE + P.PLL_PRIM, (6 << 16) | (1 << 12))
    seen: list[tuple[float, float]] = []
    mcu.add_clock_listener(lambda new, old: seen.append((new, old)))
    assert mcu.pll_sys.frequency == 0.0 and mcu.clk_sys == 125e6  # no crystal yet: 0 keeps the last value
    seen.clear()
    start_crystal(mcu)
    assert mcu.pll_sys.frequency == 200e6
    assert mcu.clk_sys == 200e6 and seen == [(200e6, 125e6)]
