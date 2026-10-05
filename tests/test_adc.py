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
