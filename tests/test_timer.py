from rp2040py.clock.mock_clock import MockClock
from rp2040py.rp2040 import RP2040

ALARM1 = 0x40054014
ALARM2 = 0x40054018
ALARM3 = 0x4005401C
ARMED = 0x40054020
INTR = 0x40054034
INTR_CLEAR = INTR | 0x3000
INTE = 0x40054038
INTF = 0x4005403C
INTS = 0x40054040


def test_alarm1_armed_on_write():
    rp2040 = RP2040(MockClock())
    rp2040.write_uint32(ALARM1, 0x1000)
    assert rp2040.read_uint32(ARMED) == 0x2


def test_disarm_alarm2_via_armed_register():
    rp2040 = RP2040()
    rp2040.write_uint32(ALARM2, 0x1000)
    assert rp2040.read_uint32(ARMED) == 0x4
    rp2040.write_uint32(ARMED, 0xFF)
    assert rp2040.read_uint32(ARMED) == 0


def test_alarm3_fires_irq3():
    clock = MockClock()
    rp2040 = RP2040(clock)
    # Arm the alarm
    rp2040.write_uint32(ALARM3, 1000)
    assert rp2040.read_uint32(ARMED) == 0x8
    assert rp2040.read_uint32(INTR) == 0
    # Advance time so that the alarm will fire
    clock.advance(2000)
    assert rp2040.read_uint32(ARMED) == 0
    assert rp2040.read_uint32(INTR) == 0x8
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.core.pending_interrupts == 0
    # Enable the interrupts for all alarms
    rp2040.write_uint32(INTE, 0xFF)
    assert rp2040.read_uint32(INTS) == 0x8
    assert rp2040.core.pending_interrupts == 0x8
    assert rp2040.core.interrupts_updated is True
    # Clear the alarm's interrupt
    rp2040.write_uint32(INTR_CLEAR, 0x8)
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.core.pending_interrupts == 0


def test_intf_forces_interrupt_even_when_inte_is_zero():
    clock = MockClock()
    rp2040 = RP2040(clock)
    assert rp2040.read_uint32(INTS) == 0
    assert rp2040.read_uint32(INTE) == 0
    rp2040.write_uint32(INTF, 0x4)
    # The corresponding interrupt bit should be 1
    assert rp2040.read_uint32(INTS) == 0x4
