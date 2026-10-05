"""Regression tests for the PWM reference (docs/records/0096-cpp-mcu-core.md, Phase 4): behaviours the lockstep differential pins as a pair (tests/test_pwm_diff.py) but a firmware depends
on as a fact. Each one is on the chip the facade gives, so it holds for the pure-Python and the native PWM alike."""

from rp2040py.rp2040 import RP2040

PWM_BASE = 0x40050000
CSR, DIV, CTR, CC, TOP = 0x00, 0x04, 0x08, 0x0C, 0x10
EN = 0xA0
CSR_EN = 1 << 0
CSR_PH_ADV = 1 << 7
CSR_PH_RET = 1 << 6


def _ch(channel: int, register: int) -> int:
    return PWM_BASE + channel * 0x14 + register


def _running_channel_0(top: int = 100) -> RP2040:
    chip = RP2040()
    chip.write_uint32(_ch(0, TOP), top)
    chip.write_uint32(_ch(0, CSR), CSR_EN)
    return chip


def test_ph_adv_advances_a_running_counter_by_one_each_time():
    chip = _running_channel_0()
    assert chip.read_uint32(_ch(0, CTR)) == 0
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV)
    assert chip.read_uint32(_ch(0, CTR)) == 1
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV)
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV)
    assert chip.read_uint32(_ch(0, CTR)) == 3


def test_ph_ret_retards_the_counter_and_wraps_past_zero_to_top():
    chip = _running_channel_0(top=9)
    chip.write_uint32(_ch(0, CTR), 5)
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_RET)
    assert chip.read_uint32(_ch(0, CTR)) == 4
    chip.write_uint32(_ch(0, CTR), 0)
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_RET)
    assert chip.read_uint32(_ch(0, CTR)) == 9


def test_both_strobes_in_one_write_cancel():
    chip = _running_channel_0()
    chip.write_uint32(_ch(0, CTR), 10)
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV | CSR_PH_RET)
    assert chip.read_uint32(_ch(0, CTR)) == 10


def test_the_strobes_do_nothing_without_the_enable_in_the_same_write():
    chip = _running_channel_0()
    chip.write_uint32(_ch(0, CTR), 10)
    chip.write_uint32(_ch(0, CSR), CSR_PH_ADV)  # also switches the channel off: the strobe acts on a running counter only
    chip.write_uint32(_ch(0, CSR), CSR_PH_RET)
    assert chip.read_uint32(_ch(0, CTR)) == 10


def test_the_strobes_are_self_clearing_and_never_read_back():
    chip = _running_channel_0()
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV | CSR_PH_RET)
    assert chip.read_uint32(_ch(0, CSR)) == CSR_EN


def test_an_advance_moves_the_next_alarm_one_cycle_closer():
    chip = _running_channel_0(top=9)  # CC is 0: every compare alarm and the wrap are one wrap (10 cycles of 8 ns) away
    before = chip.clock.nanos_to_next_alarm
    chip.write_uint32(_ch(0, CSR), CSR_EN | CSR_PH_ADV)
    assert chip.clock.nanos_to_next_alarm == before - 8.0
