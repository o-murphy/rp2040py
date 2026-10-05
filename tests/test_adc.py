"""Regression tests for the ADC reference (docs/records/0096-cpp-mcu-core.md, Phase 4): behaviours the lockstep differential pins as a pair but a firmware depends on as a fact."""

from rp2040py.rp2040 import RP2040

ADC_BASE = 0x4004C000
CS, RESULT, FCS, FIFO_REG, DIV = 0x00, 0x04, 0x08, 0x0C, 0x10
CS_ERR_STICKY = 1 << 10
FCS_UNDER = 1 << 10


def test_a_cs_write_clears_the_sticky_error_in_cs_not_in_fcs():
    chip = RP2040()
    adc = chip.adc
    adc.complete_adc_read(1, True)
    assert chip.read_uint32(ADC_BASE + CS) & CS_ERR_STICKY
    chip.write_uint32(ADC_BASE + FCS, 1)  # FIFO enabled
    chip.read_uint32(ADC_BASE + FIFO_REG)  # an empty read: UNDER
    assert chip.read_uint32(ADC_BASE + FCS) & FCS_UNDER
    chip.write_uint32(ADC_BASE + CS, CS_ERR_STICKY)  # write-clear
    assert not chip.read_uint32(ADC_BASE + CS) & CS_ERR_STICKY
    assert chip.read_uint32(ADC_BASE + FCS) & FCS_UNDER  # FCS is not what the bit belongs to


def _ainsel(chip) -> int:
    return (chip.read_uint32(ADC_BASE + CS) >> 12) & 0x7


def test_round_robin_can_select_every_channel_not_only_0_and_4():
    chip = RP2040()
    adc = chip.adc
    chip.write_uint32(ADC_BASE + CS, 1 | (0x1F << 16))  # enabled, all five channels in the round-robin mask
    seen = [_ainsel(chip)]
    for _ in range(3):
        adc.complete_adc_read(1, False)
        seen.append(_ainsel(chip))
    assert seen == [0, 1, 2, 3]
    assert (chip.read_uint32(ADC_BASE + CS) >> 15) & 1 == 0  # no stray bit above the field


def test_round_robin_wraps_from_the_last_channel_to_channel_0():
    chip = RP2040()
    chip.write_uint32(ADC_BASE + CS, 1 | (0x11 << 16) | (4 << 12))  # channels 0 and 4 in the mask, sampling 4
    chip.adc.complete_adc_read(1, False)
    assert _ainsel(chip) == 0  # (4 + 1) % 5: it used to skip 0 and come back to 4
    chip.adc.complete_adc_read(1, False)
    assert _ainsel(chip) == 4


class _DmaRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def set_dreq(self, channel: int) -> None:
        self.calls.append(("set", int(channel)))

    def clear_dreq(self, channel: int) -> None:
        self.calls.append(("clear", int(channel)))


def test_an_fcs_write_republishes_the_dreq_and_switching_dreq_en_off_takes_it_down():
    chip = RP2040()
    chip.dma = recorder = _DmaRecorder()  # type: ignore[assignment]
    chip.write_uint32(ADC_BASE + FCS, 1 | (1 << 3))  # FIFO and DREQ enabled, threshold 0: the level (0) is at it
    assert recorder.calls[-1][0] == "set"
    chip.write_uint32(ADC_BASE + FCS, 1)  # DREQ_EN off: the request that was up must go down (it used to stay up)
    assert recorder.calls[-1][0] == "clear"
    chip.write_uint32(ADC_BASE + FCS, 1 | (1 << 3) | (3 << 24))  # threshold 3 above the level 0
    assert recorder.calls[-1][0] == "clear"


def test_start_one_is_self_clearing():
    chip = RP2040()
    chip.write_uint32(ADC_BASE + CS, 1 | (1 << 2))  # enabled, START_ONE
    assert not chip.read_uint32(ADC_BASE + CS) & (1 << 2)  # it used to read back as set for ever
    chip.write_uint32(ADC_BASE + CS, 1 | (1 << 3))  # START_MANY is not self-clearing
    assert chip.read_uint32(ADC_BASE + CS) & (1 << 3)
