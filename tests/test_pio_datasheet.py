"""PIO behaviours the datasheet pins down that the reference used to get wrong (docs/records/0098-datasheet-conformance-audit.md). Each test runs on the facade's state machine
and on the pure-Python one."""

import pytest
from utils.pio_diff import Rig

from rp2040py.rp2040 import RP2040
from rp2040py.utils.pio_assembler import PIO_DEST_NULL, PIO_DEST_X, PIO_SRC_X, pio_in, pio_out, pio_set

PIO0 = 0x50200000
CTRL, TXF0, SM0_EXECCTRL, SM0_SHIFTCTRL, SM0_INSTR = 0x00, 0x10, 0xCC, 0xD0, 0xD8
PUSH = 0x8000  # PUSH, block 0, iffull 0
PUSH_IFFULL = PUSH | (1 << 6)
PULL = 0x8000 | 0x80
PULL_IFEMPTY = PULL | (1 << 6)

KINDS = ["default", "pure"]


class _Chip:
    """A chip whose PIO0 is the facade's (kind "default") or the pure-Python reference (kind "pure"), driven only through the bus and read through `snapshot()`."""

    def __init__(self, kind: str) -> None:
        self.chip = Rig(
            kind
        ).chip  # (the rig gives the chip an owner, so a CTRL write does not run the no-Simulator fallback)

    def write(self, offset: int, value: int) -> None:
        self.chip.write_uint32(PIO0 + offset, value)

    def execute(self, opcode: int) -> None:
        self.write(SM0_INSTR, opcode)

    def machine(self) -> dict:
        return self.chip.pio[0].snapshot()["machines"][0]


@pytest.mark.parametrize("kind", KINDS)
def test_push_iffull_tests_the_threshold_with_autopush_off(kind):
    sm = _Chip(kind)
    sm.write(SM0_SHIFTCTRL, 8 << 20)  # PUSH_THRESH 8, AUTOPUSH off
    sm.execute(pio_set(PIO_DEST_X, 21))
    sm.execute(pio_in(PIO_SRC_X, 3))  # three bits in the ISR
    before = sm.machine()
    assert before["input_shift_count"] == 3 and before["input_shift_reg"] != 0
    sm.execute(PUSH_IFFULL)
    after = sm.machine()
    assert after["rx"] == () and after["input_shift_reg"] == before["input_shift_reg"]  # 3 < 8: "do nothing"
    sm.execute(pio_in(PIO_SRC_X, 5))  # eight now
    assert sm.machine()["input_shift_count"] == 8
    sm.execute(PUSH_IFFULL)
    after = sm.machine()
    assert len(after["rx"]) == 1 and after["input_shift_reg"] == 0


@pytest.mark.parametrize("kind", KINDS)
def test_pull_ifempty_tests_the_threshold_with_autopull_off(kind):
    sm = _Chip(kind)
    sm.write(SM0_SHIFTCTRL, 16 << 25)  # PULL_THRESH 16, AUTOPULL off
    sm.write(TXF0, 0xAB)
    sm.write(TXF0, 0xCD)
    sm.execute(PULL)  # the OSR holds 0xAB, nothing shifted out yet
    sm.execute(pio_out(PIO_DEST_NULL, 8))
    sm.execute(PULL_IFEMPTY)
    assert sm.machine()["tx"] == (0xCD,)  # 8 < 16
    sm.execute(pio_out(PIO_DEST_NULL, 8))
    sm.execute(PULL_IFEMPTY)
    machine = sm.machine()
    assert machine["tx"] == () and machine["output_shift_reg"] == 0xCD and machine["output_shift_count"] == 0


@pytest.mark.parametrize("kind", KINDS)
def test_pull_is_a_no_op_behind_an_autopull_while_the_osr_is_full(kind):
    sm = _Chip(kind)
    sm.write(TXF0, 0x77)
    sm.write(TXF0, 0x11)
    sm.execute(PULL)  # the OSR holds 0x77 and is full
    sm.write(SM0_SHIFTCTRL, (1 << 17) | (8 << 25))  # AUTOPULL, PULL_THRESH 8
    sm.execute(PULL)
    machine = sm.machine()
    assert machine["tx"] == (0x11,) and machine["output_shift_reg"] == 0x77  # "a no-op if the OSR is full"
    sm.execute(pio_out(PIO_DEST_NULL, 3))
    sm.execute(PULL)
    machine = sm.machine()
    assert machine["tx"] == () and machine["output_shift_reg"] == 0x11


@pytest.mark.parametrize("kind", KINDS)
def test_out_32_empties_the_osr_and_a_reset_machine_starts_with_an_empty_osr(kind):
    sm = _Chip(kind)
    assert sm.machine()["output_shift_count"] == 32  # "At reset ... OSR to 32 (nothing left to be shifted out)"
    sm.write(TXF0, 0xDEADBEEF)
    sm.execute(PULL)
    assert sm.machine()["output_shift_reg"] == 0xDEADBEEF
    sm.execute(pio_out(PIO_DEST_NULL, 32))
    machine = sm.machine()
    assert machine["output_shift_reg"] == 0 and machine["output_shift_count"] == 32


@pytest.mark.parametrize("kind", KINDS)
def test_execctrl_and_shiftctrl_reserved_bits_are_not_stored(kind):
    sm = _Chip(kind)
    sm.write(SM0_EXECCTRL, 0xFFFFFFFF)
    sm.write(SM0_SHIFTCTRL, 0xFFFFFFFF)
    machine = sm.machine()
    assert machine["exec_ctrl"] == 0x7FFFFF9F
    assert machine["shift_ctrl"] == 0xFFFF0000


@pytest.mark.parametrize("kind", KINDS)
def test_pin_mappings_wrap_after_gpio31(kind):
    """Datasheet 3.5.6: an OUT or SET mapping "continues for COUNT bits, wrapping after GPIO31"."""
    pio = Rig(kind).chip.pio[0]
    pio.pin_values_changed(
        0xF0, 28, 8
    )  # 8 bits at pin 28: the low 4 data bits are pins 28-31 (30 and 31 do not exist), the high 4 wrap to pins 0-3
    assert pio.snapshot()["pin_values"] == 0xF
    pio.pin_directions_changed(
        0xFFFFFFFF, 4, 32
    )  # a full-width mapping starting at pin 4 still reaches every pin (a rotation, not a shift)
    assert pio.snapshot()["pin_directions"] == 0x3FFFFFFF


def test_irq_force_has_eight_bits_and_the_read_only_registers_ignore_writes_silently():
    """PIO tables 371-387: IRQ and IRQ_FORCE are 7:0 (the rest reserved), and FSTAT, FLEVEL, RXFn, DBG_PADOUT, DBG_PADOE, DBG_CFGINFO, INTR and IRQ0/1_INTS are read only - a write is ignored, not logged
    as an unimplemented register. Through the bus, so it runs on whichever implementation the build gives."""
    chip = RP2040()
    warnings: list[str] = []
    chip.logger.warning = lambda _name, message: warnings.append(message)  # type: ignore[method-assign]
    pio = 0x50200000
    chip.write_uint32(pio + 0x34, 0xFFFFFFFF)  # IRQ_FORCE
    assert chip.read_uint32(pio + 0x30) == 0xFF  # IRQ: eight flags
    chip.write_uint32(pio + 0x30, 0xFFFFFFFF)  # write 1 to clear
    assert chip.read_uint32(pio + 0x30) == 0
    before = {
        offset: chip.read_uint32(pio + offset)
        for offset in (0x04, 0x0C, 0x20, 0x24, 0x28, 0x2C, 0x3C, 0x40, 0x44, 0x128, 0x134, 0x140)
    }
    for offset in before:
        chip.write_uint32(pio + offset, 0xFFFFFFFF)
    assert {offset: chip.read_uint32(pio + offset) for offset in before} == before
    assert warnings == []
