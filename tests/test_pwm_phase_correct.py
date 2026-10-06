"""PWM against the datasheet (docs/records/0098-datasheet-conformance-audit.md, the PWM section): what a firmware sees from a slice, counted in clk_sys cycles.

RP2040 datasheet, 4.5.2: the counter counts 0 .. TOP and wraps (trailing-edge), or in phase-correct mode counts 0 .. TOP and back down, "the output frequency is halved"; the output is high
while the counter is below CC ("When the input value is higher than the counter, the output is driven high"); a CC of 0 is a constant low and a CC of TOP + 1 a constant high; "period = (TOP + 1) x
(CSR_PH_CORRECT + 1) x (DIV_INT + DIV_FRAC / 16)"; the latched copies of CC and TOP are updated when the counter wraps (in phase-correct mode "on the 0 to 0 count transition"), and the
interrupt comes each time the counter wraps ("each time the counter returns to 0" in phase-correct mode).
"""

from rp2040py.rp2040 import RP2040

PWM_BASE = 0x40050000
IO_BANK0 = 0x40014000
CSR, DIV, CTR, CC, TOP = 0x00, 0x04, 0x08, 0x0C, 0x10
EN_BIT, PH_CORRECT = 1, 2
CYCLE_NS = 8.0  # clk_sys = 125 MHz, DIV = 1


def _chip() -> RP2040:
    chip = RP2040()
    chip.write_uint32(IO_BANK0 + 4, 4)  # GPIO0 -> PWM0 A
    chip.write_uint32(IO_BANK0 + 8 + 4, 4)  # GPIO1 -> PWM0 B
    return chip


def _trace(chip: RP2040, cycles: int) -> tuple[str, str, list[int]]:
    """Pin A, pin B and CTR, sampled once per clk_sys cycle."""
    a, b, ctr = [], [], []
    for _ in range(cycles):
        a.append(str(int(chip.gpio[0].value)))
        b.append(str(int(chip.gpio[1].value)))
        ctr.append(chip.read_uint32(PWM_BASE + CTR))
        chip.clock.tick(CYCLE_NS)
    return "".join(a), "".join(b), ctr


def _run(top: int, cc_a: int, cc_b: int, phase_correct: bool, cycles: int, *, skip: int = 0):
    chip = _chip()
    chip.write_uint32(PWM_BASE + TOP, top)
    chip.write_uint32(PWM_BASE + CC, (cc_b << 16) | cc_a)
    chip.write_uint32(PWM_BASE + CSR, (PH_CORRECT if phase_correct else 0) | EN_BIT)
    chip.clock.tick(skip * CYCLE_NS)
    return chip, _trace(chip, cycles)


def test_the_trailing_edge_counter_wraps_and_the_output_is_high_below_cc():
    """The datasheet's hello_pwm example: TOP 3, A = 1, B = 3 - high for 1 cycle in 4 and 3 cycles in 4, rising edges aligned."""
    _, (a, b, ctr) = _run(
        top=3, cc_a=1, cc_b=3, phase_correct=False, cycles=16, skip=4
    )  # past the first, partial period
    assert ctr == [0, 1, 2, 3] * 4
    assert a == "1000" * 4
    assert b == "1110" * 4


def test_phase_correct_counts_up_and_back_down_with_the_period_doubled():
    """period = (TOP + 1) x 2 = 8 cycles for TOP = 3; the 0 and the TOP are each held for two counts (the 0 to 0 transition)."""
    _, (a, b, ctr) = _run(top=3, cc_a=2, cc_b=1, phase_correct=True, cycles=16, skip=8)
    assert ctr == [0, 1, 2, 3, 3, 2, 1, 0] * 2
    # high while the count is below CC: the pulse is centred on the 0 whatever the duty cycle
    assert a == "11000011" * 2
    assert b == "10000001" * 2


def test_phase_correct_duty_cycle_is_cc_over_top_plus_one():
    for cc in range(6):
        _, (a, *_rest) = _run(top=4, cc_a=cc, cc_b=0, phase_correct=True, cycles=10, skip=10)
        assert a.count("1") == 2 * min(cc, 5), (cc, a)  # period 10, 2 * CC cycles high; CC = TOP + 1 = 5 is 100 %


def test_cc_zero_is_constant_low_and_cc_top_plus_one_constant_high_in_both_modes():
    for phase_correct in (False, True):
        _, (a, b, *_rest) = _run(top=5, cc_a=0, cc_b=6, phase_correct=phase_correct, cycles=24, skip=12)
        assert set(a) == {"0"} and set(b) == {"1"}, (phase_correct, a, b)


def test_phase_correct_interrupt_comes_once_per_period_at_the_zero_to_zero_transition():
    chip = _chip()
    chip.write_uint32(PWM_BASE + TOP, 3)
    chip.write_uint32(PWM_BASE + CSR, PH_CORRECT | EN_BIT)
    intr = PWM_BASE + 0xA4
    chip.clock.tick(7 * CYCLE_NS)  # counts 0 .. 3, 3 .. 1: not back at 0 yet
    assert chip.read_uint32(intr) == 0
    chip.clock.tick(1 * CYCLE_NS)  # the 0 after the down count: the period is over
    assert chip.read_uint32(intr) == 1
    chip.write_uint32(intr, 1)
    chip.clock.tick(7 * CYCLE_NS)
    assert chip.read_uint32(intr) == 0
    chip.clock.tick(1 * CYCLE_NS)
    assert chip.read_uint32(intr) == 1


def test_cc_and_top_take_effect_at_the_wrap_not_when_written():
    """ "the changes are not captured by the PWM output until the next wrap" (datasheet, 4.5.2.3)."""
    chip = _chip()
    chip.write_uint32(PWM_BASE + TOP, 3)
    chip.write_uint32(PWM_BASE + CC, 1)
    chip.write_uint32(PWM_BASE + CSR, EN_BIT)
    chip.clock.tick(6 * CYCLE_NS)  # mid-period of the second period
    chip.write_uint32(PWM_BASE + CC, 3)
    chip.write_uint32(PWM_BASE + TOP, 7)
    _a, _b, ctr = _trace(chip, 8)
    assert ctr[:2] == [2, 3]  # the old TOP of 3 holds until the wrap
    assert ctr[2:] == [0, 1, 2, 3, 4, 5]  # then TOP 7: the period is 8


def test_the_period_is_top_plus_one_times_phase_correct_plus_one_times_the_divider():
    chip = _chip()
    chip.write_uint32(PWM_BASE + DIV, (2 << 4) | 8)  # 2.5
    chip.write_uint32(PWM_BASE + TOP, 3)
    chip.write_uint32(PWM_BASE + CC, 1)
    chip.write_uint32(PWM_BASE + CSR, PH_CORRECT | EN_BIT)
    intr = PWM_BASE + 0xA4
    period_cycles = (3 + 1) * 2 * 2.5  # 20 clk_sys cycles
    chip.clock.tick((period_cycles - 1) * CYCLE_NS)
    assert chip.read_uint32(intr) == 0
    chip.clock.tick(1 * CYCLE_NS)
    assert chip.read_uint32(intr) == 1


def test_ph_adv_needs_a_divider_above_one():
    """ "PH_ADV: ... Counter must be running at less than full speed (div_int + div_frac / 16 > 1)": at full speed there is no gap to insert a pulse into."""
    chip = _chip()
    chip.write_uint32(PWM_BASE + TOP, 100)
    chip.write_uint32(PWM_BASE + CSR, EN_BIT)
    chip.clock.tick(10 * CYCLE_NS)
    before = chip.read_uint32(PWM_BASE + CTR)
    chip.write_uint32(PWM_BASE + CSR, EN_BIT | (1 << 7))
    assert chip.read_uint32(PWM_BASE + CTR) == before  # DIV is 1: ignored
    chip.write_uint32(PWM_BASE + DIV, 2 << 4)
    before = chip.read_uint32(PWM_BASE + CTR)
    chip.write_uint32(PWM_BASE + CSR, EN_BIT | (1 << 7))
    assert chip.read_uint32(PWM_BASE + CTR) == before + 1


def test_csr_and_div_have_no_reserved_bits():
    chip = RP2040()
    chip.write_uint32(PWM_BASE + CSR, 0xFFFFFFFF)
    assert (
        chip.read_uint32(PWM_BASE + CSR) == 0x3F
    )  # DIVMODE, B_INV, A_INV, PH_CORRECT, EN; PH_ADV and PH_RET are strobes
    chip.write_uint32(PWM_BASE + DIV, 0xFFFFFFFF)
    assert chip.read_uint32(PWM_BASE + DIV) == 0xFFF  # INT 11:4, FRAC 3:0
