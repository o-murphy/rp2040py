"""WATCHDOG against the RP2040 datasheet (docs/records/0098-datasheet-conformance-audit.md). Run on both builds.

Sections: WATCHDOG 4.7.2 (tick generation), 4.7.6 (tables 546-550), errata RP2040-E1 (the counter decrements twice per tick).
"""

from rp2040py.rp2040 import RP2040

WATCHDOG = 0x40058000
CTRL, LOAD, REASON, SCRATCH0, SCRATCH7, TICK = 0x00, 0x04, 0x08, 0x0C, 0x28, 0x2C
ENABLE, RUNNING, TICK_ENABLE = 1 << 30, 1 << 10, 1 << 9


def test_the_tick_register_keeps_cycles_and_reports_running():
    chip = RP2040()
    assert chip.read_uint32(WATCHDOG + TICK) == RUNNING | TICK_ENABLE  # ENABLE resets to 1, CYCLES to 0
    chip.write_uint32(WATCHDOG + TICK, 12 | TICK_ENABLE)  # the SDK's watchdog_start_tick(XOSC_MHZ)
    assert chip.read_uint32(WATCHDOG + TICK) == 12 | RUNNING | TICK_ENABLE
    chip.write_uint32(WATCHDOG + TICK, 0xFFFFFFFF)
    assert (
        chip.read_uint32(WATCHDOG + TICK) == 0x1FF | RUNNING | TICK_ENABLE
    )  # CYCLES is 8:0; RUNNING and COUNT are read-only
    chip.write_uint32(WATCHDOG + TICK, 0x1FF)  # ENABLE clear: the generator stops
    assert chip.read_uint32(WATCHDOG + TICK) == 0x1FF


def test_ctrl_resets_paused_and_disabled_and_load_counts_two_per_tick():
    chip = RP2040()
    assert chip.read_uint32(WATCHDOG + CTRL) == (1 << 24) | (1 << 25) | (1 << 26)  # PAUSE_* reset to 1, ENABLE to 0
    chip.write_uint32(WATCHDOG + LOAD, 0xFFFFFF)
    chip.write_uint32(WATCHDOG + CTRL, ENABLE)
    chip.clock.tick(1_000_000)  # 1 ms: 1000 ticks, decremented twice each (RP2040-E1)
    assert chip.read_uint32(WATCHDOG + CTRL) & 0xFFFFFF == 0xFFFFFF - 2000


def test_the_scratch_registers_hold_32_bits_and_reason_starts_clear():
    chip = RP2040()
    assert chip.read_uint32(WATCHDOG + REASON) == 0
    for offset in range(SCRATCH0, SCRATCH7 + 1, 4):
        chip.write_uint32(WATCHDOG + offset, 0xA5A5A5A5 ^ offset)
    assert [chip.read_uint32(WATCHDOG + o) for o in range(SCRATCH0, SCRATCH7 + 1, 4)] == [
        0xA5A5A5A5 ^ o for o in range(SCRATCH0, SCRATCH7 + 1, 4)
    ]


def test_a_write_to_the_read_only_reason_register_changes_nothing_and_logs_nothing():
    chip = RP2040()
    messages: list[str] = []
    chip.logger.warning = lambda name, message: messages.append(message)  # type: ignore[method-assign]
    chip.write_uint32(WATCHDOG + REASON, 0xFFFFFFFF)
    assert chip.read_uint32(WATCHDOG + REASON) == 0 and messages == []
