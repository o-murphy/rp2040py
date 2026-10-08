"""The watchdog's tick as the chip's timer reference (RP2040 datasheet 4.7.2, 4.6.4, 2.4.5.1.1; docs/records/0098-datasheet-conformance-audit.md, the WATCHDOG section).

The tick generator divides clk_ref by TICK.CYCLES while TICK.ENABLE is set. The TIMER counts on it ("The Watchdog tick must be running for the timer to start counting"), SysTick's reference clock
is it, and the watchdog's own countdown runs at twice its rate (RP2040-E1). So a bare chip's TIMER does not count until firmware has started the tick, as bare-metal code finds on silicon, and a
tick derived from the ring oscillator is not 1 MHz. Run on the pure-Python and the native chip alike.
"""

from utils.tick import CLK_REF_CTRL, TICK_ENABLE, WATCHDOG_TICK, start_tick

from rp2040py.rp2040 import RP2040

TIMER = 0x40054000
TIMERAWL, ALARM0, ARMED, INTR, INTE = TIMER + 0x28, TIMER + 0x10, TIMER + 0x20, TIMER + 0x34, TIMER + 0x38
WATCHDOG = 0x40058000
W_CTRL, W_LOAD, W_REASON = WATCHDOG + 0x00, WATCHDOG + 0x04, WATCHDOG + 0x08
SYST_CSR, SYST_RVR, SYST_CVR = 0xE000E010, 0xE000E014, 0xE000E018
ROSC, XOSC = 0, 2  # CLK_REF_CTRL.SRC
MS = 1_000_000  # ns


def _count(chip: RP2040) -> int:
    return chip.read_uint32(TIMERAWL)


def test_a_bare_chips_timer_does_not_count_until_the_tick_is_started():
    chip = RP2040()
    chip.clock.tick(5 * MS)
    assert _count(chip) == 0
    start_tick(chip)
    chip.clock.tick(MS)
    assert _count(chip) == 1000  # one count per microsecond: 12 cycles of the 12 MHz crystal per tick


def test_a_tick_from_the_ring_oscillator_is_not_one_megahertz():
    chip = RP2040()
    chip.write_uint32(WATCHDOG_TICK, 12 | TICK_ENABLE)  # clk_ref is still on the ring oscillator (6.5 MHz)
    chip.clock.tick(MS)
    assert _count(chip) == 541  # 6.5 MHz / 12 = 541.67 kHz


def test_switching_clk_ref_to_the_crystal_speeds_the_count_up_without_a_jump():
    chip = RP2040()
    chip.write_uint32(WATCHDOG_TICK, 12 | TICK_ENABLE)
    chip.clock.tick(MS)
    before = _count(chip)
    chip.write_uint32(CLK_REF_CTRL, XOSC)  # what clocks_init does after xosc_init()
    assert _count(chip) == before  # continuous
    chip.clock.tick(MS)
    assert _count(chip) - before == 1000


def test_stopping_the_tick_freezes_the_timer_and_an_armed_alarm_waits_for_it():
    chip = RP2040()
    start_tick(chip)
    chip.write_uint32(INTE, 1)
    chip.clock.tick(100_000)  # 100 us
    chip.write_uint32(ALARM0, _count(chip) + 500)  # 500 us from now
    chip.write_uint32(WATCHDOG_TICK, 12)  # ENABLE clear
    frozen = _count(chip)
    chip.clock.tick(10 * MS)
    assert _count(chip) == frozen and chip.read_uint32(INTR) == 0 and chip.read_uint32(ARMED) == 1
    chip.write_uint32(WATCHDOG_TICK, 12 | TICK_ENABLE)
    chip.clock.tick(499_000)
    assert chip.read_uint32(INTR) == 0
    chip.clock.tick(1_000)
    assert chip.read_uint32(INTR) == 1 and chip.read_uint32(ARMED) == 0


def test_an_alarm_is_set_in_counts_so_a_faster_tick_brings_it_nearer():
    chip = RP2040()
    chip.write_uint32(CLK_REF_CTRL, XOSC)
    chip.write_uint32(WATCHDOG_TICK, 6 | TICK_ENABLE)  # 2 MHz: two counts per microsecond
    chip.write_uint32(INTE, 1)
    chip.write_uint32(ALARM0, 1000)  # 1000 counts
    chip.clock.tick(499_000)
    assert chip.read_uint32(INTR) == 0
    chip.clock.tick(1_000)
    assert chip.read_uint32(INTR) == 1  # 500 us


def test_systick_counts_on_the_tick_only_while_it_runs():
    chip = RP2040()
    chip.write_uint32(SYST_RVR, 9)
    chip.write_uint32(SYST_CVR, 0)
    chip.write_uint32(SYST_CSR, 1)  # ENABLE, CLKSOURCE 0: the reference clock, which is stopped
    chip.clock.tick(MS)
    assert chip.read_uint32(SYST_CSR) & 0x10001 == 1  # enabled as written, no COUNTFLAG: it has not counted
    start_tick(chip)
    chip.clock.tick(15_000)  # 15 reference ticks of 1 us: one period of 10 and 5 into the next
    assert chip.read_uint32(SYST_CSR) & 0x10000  # COUNTFLAG


def test_the_countdown_is_twice_the_tick_and_waits_for_it():
    chip = RP2040()
    fired: list[float] = []
    chip.watchdog.on_watchdog_trigger = lambda: fired.append(chip.clock.nanos)
    chip.write_uint32(W_LOAD, 100_000)  # the SDK's 50 ms: delay_ms * 1000 * 2
    chip.write_uint32(W_CTRL, 1 << 30)  # ENABLE
    chip.clock.tick(200 * MS)
    assert fired == []  # no tick, no countdown
    start_tick(chip)
    chip.write_uint32(W_LOAD, 100_000)
    chip.clock.tick(49 * MS)
    assert fired == []
    chip.clock.tick(MS)
    assert fired == [chip.clock.nanos] and chip.read_uint32(W_REASON) == 1
    # the same countdown on a tick from the ring oscillator takes 92.3 ms: 100000 counts at 2 * 541.67 kHz
    slow = RP2040()
    fired_slow: list[float] = []
    slow.watchdog.on_watchdog_trigger = lambda: fired_slow.append(slow.clock.nanos)
    slow.write_uint32(WATCHDOG_TICK, 12 | TICK_ENABLE)
    slow.write_uint32(W_LOAD, 100_000)
    slow.write_uint32(W_CTRL, 1 << 30)
    slow.clock.tick(92 * MS)
    assert fired_slow == []
    slow.clock.tick(MS)
    assert len(fired_slow) == 1


def test_a_chip_reset_restarts_the_count_and_a_power_on_reset_stops_the_tick():
    chip = RP2040()
    start_tick(chip)
    chip.clock.tick(5 * MS)
    assert _count(chip) == 5000
    chip.reset(preserve_flash=True)  # the TIMER is in the reset domain: the count restarts from zero...
    assert _count(chip) == 0
    chip.clock.tick(MS)
    # ...and goes on counting: a chip reset does not touch the watchdog, so its tick still divides by 12. The CLOCKS domain was reset too, which puts clk_ref back on the ring oscillator:
    # the tick is 6.5 MHz / 12 until firmware switches it again.
    assert _count(chip) == 541
    chip.watchdog.reset()  # what a RUN-pin / power-on reset does to the watchdog ("reset by rst_n_run", datasheet 4.7.1)
    frozen = _count(chip)
    chip.clock.tick(MS)
    assert (
        _count(chip) == frozen
    )  # TICK is back to ENABLE set, CYCLES 0: not running, so the TIMER waits for the firmware to start it
    assert chip.read_uint32(WATCHDOG_TICK) == TICK_ENABLE
