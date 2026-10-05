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
    chip.write_uint32(
        _ch(0, CSR), CSR_PH_ADV
    )  # also switches the channel off: the strobe acts on a running counter only
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


def test_the_en_register_reads_the_enable_bit_of_every_channel():
    chip = RP2040()
    assert chip.read_uint32(PWM_BASE + EN) == 0
    chip.write_uint32(_ch(0, CSR), CSR_EN)
    chip.write_uint32(_ch(5, CSR), CSR_EN | (2 << 4))
    assert chip.read_uint32(PWM_BASE + EN) == 0b0010_0001
    chip.write_uint32(PWM_BASE + EN, 0b1000_0010)  # EN writes every channel's bit at once: 0 and 5 off, 1 and 7 on
    assert chip.read_uint32(PWM_BASE + EN) == 0b1000_0010
    assert chip.read_uint32(_ch(1, CSR)) & CSR_EN and not chip.read_uint32(_ch(0, CSR)) & CSR_EN


def test_a_csr_write_without_the_enable_switches_the_channel_off_in_en():
    chip = RP2040()
    chip.write_uint32(PWM_BASE + EN, 0xFF)
    assert chip.read_uint32(PWM_BASE + EN) == 0xFF
    chip.write_uint32(_ch(3, CSR), 0)
    assert chip.read_uint32(PWM_BASE + EN) == 0xFF & ~(1 << 3)


IO_BANK0 = 0x40014000
PADS_BANK0 = 0x4001C000
FUNC_PWM = 4


def _b_input_on_pin_1(chip: RP2040, mode: int) -> None:
    """Channel 0 counting the edges of the B input (pin 1) in the given divider mode, the pin on the PWM function with its input enabled."""
    chip.write_uint32(IO_BANK0 + 8 * 1 + 4, FUNC_PWM)
    chip.write_uint32(PADS_BANK0 + 4 + 4 * 1, 0x56)
    chip.write_uint32(_ch(0, TOP), 100)
    chip.write_uint32(_ch(0, CSR), CSR_EN | (mode << 4))


def test_a_b_input_edge_is_counted_in_the_edge_modes_after_a_plain_reset():
    chip = RP2040()
    _b_input_on_pin_1(chip, mode=2)  # rising edges
    for _ in range(3):
        chip.gpio[1].set_input_value(True)
        chip.gpio[1].set_input_value(False)
    assert chip.read_uint32(_ch(0, CTR)) == 3


def test_falling_edges_are_counted_in_the_falling_edge_mode():
    chip = RP2040()
    _b_input_on_pin_1(chip, mode=3)
    chip.gpio[1].set_input_value(True)
    assert chip.read_uint32(_ch(0, CTR)) == 0
    chip.gpio[1].set_input_value(False)
    assert chip.read_uint32(_ch(0, CTR)) == 1


def test_an_edge_on_a_pin_that_is_an_output_is_ignored():
    chip = RP2040()
    _b_input_on_pin_1(chip, mode=2)
    chip.pwm.gpio_direction |= 1 << 1  # pin 1 driven as an output
    chip.gpio[1].set_input_value(True)
    chip.gpio[1].set_input_value(False)
    assert chip.read_uint32(_ch(0, CTR)) == 0
    chip.pwm.gpio_direction &= ~(1 << 1)  # an input again: counted
    chip.gpio[1].set_input_value(True)
    assert chip.read_uint32(_ch(0, CTR)) == 1
