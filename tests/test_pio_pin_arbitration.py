"""PIO output priority and EXECCTRL.OUT_STICKY / INLINE_OUT_EN / OUT_EN_SEL (RP2040 datasheet 3.5.6.1 and SM0_EXECCTRL), through a whole chip on whichever PIO the build selects.

What the datasheet says, and all it says (docs/records/0098-datasheet-conformance-audit.md, "PIO pin-write arbitration"):
- 3.5.6.1: "For each GPIO, PIO collates the writes from all four state machines, and applies the write from the highest-numbered state machine. This occurs separately for output levels and
  output directions [...] If no state machine asserts a write to a GPIO's level or direction, the value does not change."
- OUT_STICKY: "Continuously assert the most recent OUT/SET to the pins". SM_RESTART clears "any pin write left asserted due to OUT_STICKY".
- INLINE_OUT_EN: "use a bit of OUT data as an auxiliary write enable. When used in conjunction with OUT_STICKY, writes with an enable of 0 will deassert the latest pin write. This can create
  useful masking/override behaviour due to the priority ordering of state machine pin writes (SM0 < SM1 < ...)"; OUT_EN_SEL: "Which data bit to use for inline OUT enable".
The tests build the situations from those sentences with machines running real instructions; the cycle is the PIO's own (`advance(1)`).
"""

PIO0 = 0x50200000
CTRL, TXF0, INSTR_MEM0 = 0x00, 0x10, 0x48
SM_BASE, SM_STRIDE = 0xC8, 0x18
EXECCTRL, INSTR, PINCTRL = 0x04, 0x10, 0x14
OUT_STICKY, INLINE_OUT_EN = 1 << 17, 1 << 18
PIN = 5


def sm(n, reg):
    return SM_BASE + SM_STRIDE * n + reg


def _set_pins(value):
    return 0xE000 | value


def _set_pindirs(value):
    return 0xE080 | value


JMP = lambda addr: addr


def _chip(rp2040_factory):
    chip = rp2040_factory()
    chip.simulator = object()  # an owner, so enabling a machine does not start the no-Simulator fallback loop: the tests advance the PIO themselves
    return chip


def _w(chip, offset, value):
    chip.write_uint32(PIO0 + offset, value)


def _program(chip, words):
    for index, word in enumerate(words):
        _w(chip, INSTR_MEM0 + 4 * index, word)


def _config(chip, machine, wrap, *, execctrl=0, set_base=PIN, set_count=1, out_base=PIN, out_count=1):
    top, bottom = wrap[1], wrap[0]
    _w(chip, sm(machine, EXECCTRL), execctrl | (top << 12) | (bottom << 7))
    _w(chip, sm(machine, PINCTRL), (set_base << 5) | (set_count << 26) | out_base | (out_count << 20))
    _w(chip, sm(machine, INSTR), 0x0000 | bottom)  # jmp bottom: start there


def _level(chip):
    return (chip.pio[0].snapshot()["pin_values"] >> PIN) & 1


def _run(chip, cycles=1):
    for _ in range(cycles):
        chip.pio[0].advance(1)


def _enable(chip, mask):
    _w(chip, CTRL, mask)


def test_a_write_changes_only_the_pins_it_asserts(rp2040_factory):
    """3.5.6.1: "If no state machine asserts a write to a GPIO's level or direction, the value does not change." """
    chip = _chip(rp2040_factory)
    chip.pio[0].pin_values_changed(1, 9, 1)  # pin 9 is already high when the machine starts
    _program(chip, [_set_pins(1), JMP(1)])
    _config(chip, 0, (0, 1))
    _enable(chip, 0b1)
    _run(chip, 4)
    assert _level(chip) == 1 and (chip.pio[0].snapshot()["pin_values"] >> 9) & 1 == 1


def test_the_highest_numbered_machine_wins_a_cycle(rp2040_factory):
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1))
    _config(chip, 1, (2, 3))
    _enable(chip, 0b11)
    _run(chip, 1)  # both write in the same cycle: machine 1 is higher
    assert _level(chip) == 0
    _run(chip, 6)
    assert _level(chip) == 0  # machine 0 wrote only once: nothing re-asserts it


def test_a_sticky_write_comes_back_after_a_higher_machine_overwrote_it(rp2040_factory):
    """OUT_STICKY on the lower machine: machine 1's single write wins its cycle, then the held write of machine 0 is asserted again."""
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1), execctrl=OUT_STICKY)
    _config(chip, 1, (2, 3))
    _enable(chip, 0b1)
    _run(chip, 3)
    assert _level(chip) == 1
    _enable(chip, 0b11)
    _run(chip, 1)  # machine 1's SET lands: it is the higher machine
    assert _level(chip) == 0
    _run(chip, 1)  # the next cycle machine 1 writes nothing: machine 0's held write is on the pin again
    assert _level(chip) == 1
    _run(chip, 5)
    assert _level(chip) == 1


def test_without_sticky_the_overwriting_write_stays(rp2040_factory):
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1))
    _config(chip, 1, (2, 3))
    _enable(chip, 0b1)
    _run(chip, 3)
    _enable(chip, 0b11)
    _run(chip, 6)
    assert _level(chip) == 0


def test_a_sticky_write_of_a_higher_machine_beats_a_lower_machines_write(rp2040_factory):
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(0), JMP(0), _set_pins(1), JMP(3)])
    _config(chip, 0, (0, 0))  # writes 0 on every cycle
    _config(chip, 1, (2, 3), execctrl=OUT_STICKY)
    _enable(chip, 0b10)
    _run(chip, 3)
    assert _level(chip) == 1
    _enable(chip, 0b11)
    _run(chip, 6)
    assert _level(chip) == 1  # machine 0 writes 0 every cycle; machine 1's held 1 is the higher write


def test_levels_and_directions_are_collated_separately(rp2040_factory):
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pindirs(1), JMP(3)])
    _config(chip, 0, (0, 1), execctrl=OUT_STICKY)
    _config(chip, 1, (2, 3))
    _enable(chip, 0b11)
    _run(chip, 4)
    assert _level(chip) == 1 and (chip.pio[0].snapshot()["pin_directions"] >> PIN) & 1 == 1


def test_a_block_that_stops_shows_the_held_write_it_had_overwritten(rp2040_factory):
    """Sticky writes are asserted whether or not their machine is enabled, and a stopped block gets no further cycle: the fall-back to the held write is not left pending."""
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1), execctrl=OUT_STICKY)
    _config(chip, 1, (2, 3))
    _enable(chip, 0b1)
    _run(chip, 3)
    _enable(chip, 0b11)
    _run(chip, 1)
    assert _level(chip) == 0  # machine 1's write is on the pin for its cycle
    _enable(chip, 0)  # everything off: the block stops
    assert _level(chip) == 1


def test_restart_clears_a_held_write(rp2040_factory):
    """SM_RESTART clears "any pin write left asserted due to OUT_STICKY"."""
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1), execctrl=OUT_STICKY)
    _config(chip, 1, (2, 3))
    _enable(chip, 0b1)
    _run(chip, 3)
    _w(chip, CTRL, 0b1 | (1 << 4))  # SM_RESTART of machine 0 (still enabled)
    _enable(chip, 0b10)  # machine 0 off, so it does not write again
    _run(chip, 1)
    assert _level(chip) == 0
    _run(chip, 4)
    assert _level(chip) == 0  # nothing is held any more


def test_turning_sticky_off_drops_the_held_write(rp2040_factory):
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(1), JMP(1), _set_pins(0), JMP(3)])
    _config(chip, 0, (0, 1), execctrl=OUT_STICKY)
    _config(chip, 1, (2, 3))
    _enable(chip, 0b1)
    _run(chip, 3)
    _w(chip, sm(0, EXECCTRL), (1 << 12) | 0)  # OUT_STICKY off
    _enable(chip, 0b10)
    _run(chip, 6)
    assert _level(chip) == 0


def _out_pins(count):
    return 0x6000 | count  # OUT PINS, count


def _pull():
    return 0x80A0  # PULL block


def test_inline_out_enable_gates_an_out_write(rp2040_factory):
    """INLINE_OUT_EN: bit OUT_EN_SEL of the OUT data is a write enable. OUT_COUNT is 1 and the instruction shifts out 2 bits: bit 0 is the pin's data, bit 1 the enable."""
    chip = _chip(rp2040_factory)
    _config(chip, 0, (0, 0), execctrl=INLINE_OUT_EN | (1 << 19), out_count=1)  # OUT_EN_SEL = 1
    for data, expected in ((0b11, 1), (0b00, 1), (0b10, 0), (0b01, 0), (0b11, 1)):
        _w(chip, TXF0, data)
        _w(chip, sm(0, INSTR), _pull())
        _w(chip, sm(0, INSTR), _out_pins(2))
        assert _level(chip) == expected, bin(data)


def test_inline_out_enable_off_means_every_out_writes(rp2040_factory):
    chip = _chip(rp2040_factory)
    _config(chip, 0, (0, 0), execctrl=0, out_count=1)
    _w(chip, TXF0, 0b00)
    _w(chip, sm(0, INSTR), _pull())
    _w(chip, sm(0, INSTR), _out_pins(2))
    assert _level(chip) == 0
    _w(chip, TXF0, 0b01)
    _w(chip, sm(0, INSTR), _pull())
    _w(chip, sm(0, INSTR), _out_pins(2))
    assert _level(chip) == 1


def test_a_disabled_sticky_write_stops_being_asserted(rp2040_factory):
    """ "writes with an enable of 0 will deassert the latest pin write": machine 0 holds a 1; its next OUT has enable 0, so it holds nothing and a higher machine's write stays."""
    chip = _chip(rp2040_factory)
    _program(chip, [_set_pins(0), JMP(1)])
    _config(chip, 0, (0, 0), execctrl=OUT_STICKY | INLINE_OUT_EN | (1 << 19), out_count=1)
    _config(chip, 1, (0, 1))  # the "set pins, 0" of address 0 is machine 1's too: its write is the single 0
    _w(chip, TXF0, 0b11)  # data 1, enable 1
    _w(chip, sm(0, INSTR), _pull())
    _w(chip, sm(0, INSTR), _out_pins(2))
    assert _level(chip) == 1
    _w(chip, TXF0, 0b01)  # enable 0: the held write is replaced by an empty one
    _w(chip, sm(0, INSTR), _pull())
    _w(chip, sm(0, INSTR), _out_pins(2))
    _enable(chip, 0b10)  # machine 1 runs: SET PINS 0 once, then jmp
    _run(chip, 1)
    assert _level(chip) == 0
    _run(chip, 4)
    assert _level(chip) == 0  # nothing is held to take the pin back to 1
