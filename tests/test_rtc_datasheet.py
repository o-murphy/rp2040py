"""RTC against the RP2040 datasheet (docs/records/0098-datasheet-conformance-audit.md). Run on both builds.

Sections: RTC 4.8.1 (storage format), 4.8.2 (leap year), 4.8.3 (interrupts), 4.8.4 (reference clock), 4.8.5 (programmer's model, with pico-sdk `hardware_rtc/rtc.c`) and the register
tables 552-564; CLOCKS 2.15 for clk_rtc (tables 234, 235).
"""

from utils.tick import start_rtc_clock

CLOCKS = 0x40008000
RTC = 0x4005C000
CLKDIV_M1, SETUP_0, SETUP_1, CTRL, IRQ_SETUP_0, IRQ_SETUP_1, RTC_1, RTC_0, INTR, INTE, INTF, INTS = (
    0x00,
    0x04,
    0x08,
    0x0C,
    0x10,
    0x14,
    0x18,
    0x1C,
    0x20,
    0x24,
    0x28,
    0x2C,
)
ENABLE, ACTIVE, LOAD, FORCE_NOTLEAPYEAR = 1, 2, 1 << 4, 1 << 8
MATCH_ACTIVE, MATCH_ENA = 1 << 29, 1 << 28
SECOND = 1_000_000_000
RTC_IRQ = 1 << 25


def setup(year, month, day, dotw, hour, minute, sec):
    return (year << 12 | month << 8 | day), (dotw << 24 | hour << 16 | minute << 8 | sec)


def set_datetime(chip, *fields, enable=True):
    """`rtc_set_datetime()`: disable, write SETUP_0/1, LOAD, then ENABLE."""
    s0, s1 = setup(*fields)
    chip.write_uint32(RTC + CTRL, 0)
    chip.write_uint32(RTC + SETUP_0, s0)
    chip.write_uint32(RTC + SETUP_1, s1)
    chip.write_uint32(RTC + CTRL, LOAD)
    if enable:
        chip.write_uint32(RTC + CTRL, ENABLE)


def get_datetime(chip):
    """`rtc_get_datetime()`: RTC_0 first, then RTC_1."""
    rtc_0 = chip.read_uint32(RTC + RTC_0)
    rtc_1 = chip.read_uint32(RTC + RTC_1)
    return (
        rtc_1 >> 12,
        (rtc_1 >> 8) & 0xF,
        rtc_1 & 0x1F,
        (rtc_0 >> 24) & 7,
        (rtc_0 >> 16) & 0x1F,
        (rtc_0 >> 8) & 0x3F,
        rtc_0 & 0x3F,
    )


def running_chip(rp2040_factory):
    chip = rp2040_factory()
    start_rtc_clock(chip)
    chip.write_uint32(RTC + CLKDIV_M1, 46874)  # rtc_init(): clk_rtc - 1
    return chip


def test_clk_rtc_is_the_aux_source_over_the_divider(rp2040_factory):
    chip = rp2040_factory()
    assert chip.clocks.rtc_freq == 0  # ENABLE resets to 0
    start_rtc_clock(chip)
    assert chip.clocks.rtc_freq == 46875  # XOSC 12 MHz / 256
    chip.write_uint32(CLOCKS + 0x6C, (1 << 11) | (2 << 5))  # ROSC_CLKSRC_PH
    assert chip.clocks.rtc_freq == 6_500_000 / 256
    chip.write_uint32(CLOCKS + 0x6C, (1 << 11) | (1 << 10) | (3 << 5))  # KILL
    assert chip.clocks.rtc_freq == 0
    chip.write_uint32(CLOCKS + 0x6C, (1 << 11) | (4 << 5))  # GPIN0 is not modelled
    assert chip.clocks.rtc_freq == 0
    chip.write_uint32(CLOCKS + 0x6C, (1 << 11) | (3 << 5))
    chip.write_uint32(CLOCKS + 0x70, 0)  # INT = 0 divides by 2**16
    assert chip.clocks.rtc_freq == 12_000_000 / 65536


def test_register_resets_and_widths(rp2040_factory):
    chip = rp2040_factory()
    for offset in (CLKDIV_M1, SETUP_0, SETUP_1, CTRL, IRQ_SETUP_0, IRQ_SETUP_1, INTR, INTE, INTF, INTS):
        assert chip.read_uint32(RTC + offset) == 0, hex(offset)
    for offset, kept in (
        (CLKDIV_M1, 0xFFFF),
        (SETUP_0, 0x00FFFF1F),
        (SETUP_1, 0x071F3F3F),
        (IRQ_SETUP_0, 0x17FFFF1F),  # MATCH_ACTIVE (29) is read only
        (IRQ_SETUP_1, 0xF71F3F3F),
        (INTE, 1),
        (INTF, 1),
    ):
        chip.write_uint32(RTC + offset, 0xFFFFFFFF)
        value = chip.read_uint32(RTC + offset)
        assert value & ~(MATCH_ACTIVE if offset == IRQ_SETUP_0 else 0) == kept, hex(offset)
    assert chip.read_uint32(RTC + CTRL) == 0
    chip.write_uint32(
        RTC + CTRL, 0xFFFFFFFF
    )  # FORCE_NOTLEAPYEAR and ENABLE are RW, LOAD is SC and reads 0, ACTIVE is RO
    assert chip.read_uint32(RTC + CTRL) == FORCE_NOTLEAPYEAR | ENABLE  # clk_rtc is stopped: not ACTIVE
    for offset in (RTC_1, RTC_0, INTR, INTS):  # read only
        before = chip.read_uint32(RTC + offset)
        chip.write_uint32(RTC + offset, 0xFFFFFFFF)
        assert chip.read_uint32(RTC + offset) == before, hex(offset)


def test_nothing_counts_without_clk_rtc_and_active_follows_enable_and_the_clock(rp2040_factory):
    chip = rp2040_factory()
    set_datetime(chip, 2024, 1, 1, 1, 0, 0, 0)
    assert chip.read_uint32(RTC + CTRL) == ENABLE  # RTC_ACTIVE waits for clk_rtc, as the SDK's rtc_set_datetime() would
    chip.clock.tick(5 * SECOND)
    assert get_datetime(chip) == (2024, 1, 1, 1, 0, 0, 0)
    start_rtc_clock(chip)
    chip.write_uint32(RTC + CLKDIV_M1, 46874)
    assert chip.read_uint32(RTC + CTRL) == ENABLE | ACTIVE
    chip.clock.tick(3 * SECOND)
    assert get_datetime(chip)[-1] == 3
    chip.write_uint32(RTC + CTRL, 0)
    assert chip.read_uint32(RTC + CTRL) == 0
    chip.clock.tick(5 * SECOND)
    assert get_datetime(chip)[-1] == 3  # stopped, not lost
    chip.write_uint32(RTC + CTRL, ENABLE)
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[-1] == 4  # and it goes on from there


def test_the_second_is_clkdiv_m1_plus_one_periods_of_clk_rtc(rp2040_factory):
    chip = running_chip(rp2040_factory)
    chip.write_uint32(RTC + CLKDIV_M1, 46874 // 3)  # 15625 periods of 46875 Hz: a third of a second
    set_datetime(chip, 2024, 5, 5, 0, 0, 0, 0)
    chip.clock.tick(2 * SECOND)
    assert get_datetime(chip)[-1] == 6
    chip.write_uint32(RTC + CLKDIV_M1, 1)  # two periods: 46875 / 2 = 23437.5 seconds per real second
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[3:] == _after(23437, (0, 0, 0, 6))


def _after(seconds, start):
    dotw, hour, minute, second = start
    total = ((hour * 60 + minute) * 60 + second) + seconds
    days, rest = divmod(total, 86400)
    return ((dotw + days) % 7, rest // 3600, rest % 3600 // 60, rest % 60)


def test_load_works_while_disabled_and_is_not_read_back(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2031, 7, 14, 1, 12, 34, 56, enable=False)
    assert chip.read_uint32(RTC + CTRL) == 0  # LOAD (SC) reads 0
    assert get_datetime(chip) == (2031, 7, 14, 1, 12, 34, 56)
    chip.clock.tick(3 * SECOND)
    assert get_datetime(chip)[-1] == 56  # not enabled: not counting
    chip.write_uint32(RTC + SETUP_1, 0)  # a new setup is not the counter until it is loaded
    assert get_datetime(chip)[-1] == 56
    chip.write_uint32(RTC + CTRL, ENABLE)
    chip.write_uint32(RTC + CTRL, ENABLE | LOAD)  # "It is possible to change the current time while the RTC is running"
    assert get_datetime(chip) == (2031, 7, 14, 0, 0, 0, 0)


def test_fields_carry_second_minute_hour_day_month_year_and_the_day_of_week_only_increments(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2025, 12, 31, 3, 23, 59, 58)
    chip.clock.tick(SECOND)
    assert get_datetime(chip) == (2025, 12, 31, 3, 23, 59, 59)
    chip.clock.tick(SECOND)
    assert get_datetime(chip) == (2026, 1, 1, 4, 0, 0, 0)
    set_datetime(chip, 2025, 3, 31, 6, 23, 59, 59)  # Sat -> Sun
    chip.clock.tick(SECOND)
    assert get_datetime(chip) == (2025, 4, 1, 0, 0, 0, 0)
    set_datetime(chip, 2025, 4, 30, 1, 23, 59, 59)  # April has 30 days
    chip.clock.tick(SECOND)
    assert get_datetime(chip) == (2025, 5, 1, 2, 0, 0, 0)
    set_datetime(
        chip, 2025, 1, 1, 4, 10, 20, 30
    )  # the weekday is not computed from the date: 1 Jan 2025 was a Wednesday
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[3] == 4
    set_datetime(chip, 4095, 12, 31, 0, 23, 59, 59)
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[:3] == (0, 1, 1)  # the year is 12 bits


def test_leap_year_unless_forced_off(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 2, 28, 3, 23, 59, 59)
    chip.clock.tick(SECOND)
    assert get_datetime(chip) == (2024, 2, 29, 4, 0, 0, 0)  # divisible by 4: Feb 28th is followed by Feb 29th
    chip.clock.tick(86400 * SECOND)
    assert get_datetime(chip)[:3] == (2024, 3, 1)
    set_datetime(chip, 2025, 2, 28, 3, 23, 59, 59)
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[:3] == (2025, 3, 1)  # not divisible by 4
    set_datetime(chip, 2100, 2, 28, 3, 23, 59, 59, enable=False)  # a century year that is not a leap year (4.8.2)
    chip.write_uint32(RTC + CTRL, FORCE_NOTLEAPYEAR)
    chip.write_uint32(RTC + CTRL, FORCE_NOTLEAPYEAR | ENABLE)
    chip.clock.tick(SECOND)
    assert get_datetime(chip)[:3] == (2100, 3, 1)
    assert chip.read_uint32(RTC + CTRL) == FORCE_NOTLEAPYEAR | ENABLE | ACTIVE


def test_reading_rtc_0_latches_rtc_1(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 12, 31, 2, 23, 59, 59)
    assert chip.read_uint32(RTC + RTC_1) == 0  # nothing latched yet
    assert chip.read_uint32(RTC + RTC_0) == 2 << 24 | 23 << 16 | 59 << 8 | 59
    chip.clock.tick(SECOND)  # the date rolls over between the two reads
    assert chip.read_uint32(RTC + RTC_1) == 2024 << 12 | 12 << 8 | 31  # the date as it was at the RTC_0 read
    assert chip.read_uint32(RTC + RTC_0) == 3 << 24
    assert chip.read_uint32(RTC + RTC_1) == 2025 << 12 | 1 << 8 | 1


def test_illegal_values_are_not_checked_and_never_raise(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(
        chip, 0, 0, 0, 7, 31, 63, 63
    )  # 4.8.1: "The RTC does not check that the programmed values are in range"
    chip.clock.tick(3 * SECOND)
    year, month, day, dotw, hour, minute, second = get_datetime(chip)
    assert (year, month, day, dotw, hour, minute) == (
        0,
        0,
        1,
        0,
        0,
        0,
    )  # a field past its limit wraps at its next carry (the model's own rule: the datasheet gives none)
    assert second == 2


def test_alarm_is_a_level_while_the_enabled_fields_match(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 6, 6, 4, 10, 20, 57)
    # the SDK's rtc_set_alarm(): sec == 0 of every minute
    chip.write_uint32(RTC + IRQ_SETUP_0, 0)
    chip.write_uint32(RTC + IRQ_SETUP_1, 1 << 28 | 0)  # SEC_ENA, SEC 0
    chip.write_uint32(RTC + INTE, 1)
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)
    assert chip.read_uint32(RTC + IRQ_SETUP_0) == MATCH_ENA | MATCH_ACTIVE
    assert chip.read_uint32(RTC + INTR) == 0
    assert chip.core.pending_interrupts & RTC_IRQ == 0
    chip.clock.tick(3 * SECOND)  # 10:21:00
    assert chip.read_uint32(RTC + INTR) == 1 and chip.read_uint32(RTC + INTS) == 1
    assert chip.core.pending_interrupts & RTC_IRQ
    chip.write_uint32(RTC + IRQ_SETUP_0, 0)  # the handler: rtc_disable_alarm() clears the interrupt
    assert chip.read_uint32(RTC + IRQ_SETUP_0) == 0
    assert chip.read_uint32(RTC + INTR) == 0
    assert chip.core.pending_interrupts & RTC_IRQ == 0
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)  # re-enabled during that same second: it matches still
    assert chip.read_uint32(RTC + INTR) == 1
    chip.write_uint32(RTC + IRQ_SETUP_0, 0)
    chip.clock.tick(SECOND)  # 10:21:01
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)
    assert chip.read_uint32(RTC + INTR) == 0
    chip.clock.tick(59 * SECOND)  # and once a minute
    assert chip.read_uint32(RTC + INTR) == 1


def test_alarm_matches_each_enabled_field_and_none_at_all_matches_every_second(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 6, 6, 4, 10, 20, 30)
    fields = {
        "year": (IRQ_SETUP_0, 1 << 26, 2024 << 12),
        "month": (IRQ_SETUP_0, 1 << 25, 6 << 8),
        "day": (IRQ_SETUP_0, 1 << 24, 6),
        "dotw": (IRQ_SETUP_1, 1 << 31, 4 << 24),
        "hour": (IRQ_SETUP_1, 1 << 30, 10 << 16),
        "min": (IRQ_SETUP_1, 1 << 29, 20 << 8),
        "sec": (IRQ_SETUP_1, 1 << 28, 30),
    }
    for name, (register, enable, match) in fields.items():
        for value, expected in ((match, 1), (0 if match == 0 else match ^ (match & -match), 0)):
            chip.write_uint32(RTC + IRQ_SETUP_0, 0)
            chip.write_uint32(RTC + IRQ_SETUP_1, 0)
            chip.write_uint32(RTC + register, enable | value)
            chip.write_uint32(RTC + IRQ_SETUP_0, chip.read_uint32(RTC + IRQ_SETUP_0) | MATCH_ENA)
            assert chip.read_uint32(RTC + INTR) == expected, (name, value)
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)
    chip.write_uint32(RTC + IRQ_SETUP_1, 0)
    assert chip.read_uint32(RTC + INTR) == 1  # no field enabled: every second matches
    chip.write_uint32(RTC + IRQ_SETUP_0, 0)
    chip.write_uint32(RTC + IRQ_SETUP_1, 0)
    assert chip.read_uint32(RTC + INTR) == 0  # MATCH_ENA clear: nothing


def test_the_interrupt_is_masked_by_inte_and_forced_by_intf(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 6, 6, 4, 10, 20, 30)
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)  # every second matches
    assert chip.read_uint32(RTC + INTR) == 1 and chip.read_uint32(RTC + INTS) == 0
    assert chip.core.pending_interrupts & RTC_IRQ == 0
    chip.write_uint32(RTC + INTE, 1)
    assert chip.read_uint32(RTC + INTS) == 1 and chip.core.pending_interrupts & RTC_IRQ
    chip.write_uint32(RTC + INTE, 0)
    assert chip.core.pending_interrupts & RTC_IRQ == 0
    chip.write_uint32(RTC + IRQ_SETUP_0, 0)
    chip.write_uint32(RTC + INTF, 1)  # forced
    assert chip.read_uint32(RTC + INTR) == 0 and chip.read_uint32(RTC + INTS) == 0
    chip.write_uint32(RTC + INTE, 1)
    assert chip.read_uint32(RTC + INTS) == 1 and chip.core.pending_interrupts & RTC_IRQ


def test_a_reset_of_the_block_clears_it_and_keeps_the_clock(rp2040_factory):
    chip = running_chip(rp2040_factory)
    set_datetime(chip, 2024, 6, 6, 4, 10, 20, 30)
    chip.write_uint32(RTC + IRQ_SETUP_0, MATCH_ENA)
    chip.write_uint32(RTC + INTE, 1)
    assert chip.core.pending_interrupts & RTC_IRQ
    chip.rtc.reset()  # RESETS.RESET_RTC
    for offset in (CLKDIV_M1, SETUP_0, SETUP_1, CTRL, IRQ_SETUP_0, IRQ_SETUP_1, RTC_1, RTC_0, INTR, INTE, INTF, INTS):
        assert chip.read_uint32(RTC + offset) == 0, hex(offset)
    assert chip.core.pending_interrupts & RTC_IRQ == 0
    chip.clock.tick(5 * SECOND)
    assert get_datetime(chip) == (0, 0, 0, 0, 0, 0, 0)  # disabled
    assert chip.clocks.rtc_freq == 46875  # clk_rtc is the CLOCKS block's, not the RTC's
