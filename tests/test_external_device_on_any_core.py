"""A device written in Python and attached through the public pin API works on whichever core is built.

docs/records/0096-cpp-mcu-core.md, "Risks and open questions" (2026-10-05): an external device that has a Python wrapper and follows
the interface must attach to any backend (pure, Cython, later wasm), whatever it is written in. The native chip hands a Python listener
the same ``(new_state, old_state)`` pair the pure chip does, through a trampoline; these tests make that a guarantee instead of a
hand-run result. On the pure build they exercise the pure pins, on the native build the native ones - same assertions.
"""

import pytest
from test_cyw43_bus import BUS_FUNCTION, SPI_READ_TEST_REGISTER, TEST_PATTERN, _FakeGSPIMaster

import rp2040py.external.cyw43.bus as cyw43_bus
from rp2040py.external.cyw43.bus import GSPIBus
from rp2040py.gpio_pin import FUNCTION_PIO0, FUNCTION_SIO, GPIOPinState
from rp2040py.utils.pio_assembler import pio_jmp, pio_set

SIO_GPIO_OUT_SET, SIO_GPIO_OUT_CLR, SIO_GPIO_OE_SET = 0xD0000014, 0xD0000018, 0xD0000024
PIO0_CTRL = 0x50200000
PIO0_INSTR_MEM0 = 0x50200048
PIO0_SM0_EXECCTRL = 0x502000CC
PIO0_SM0_PINCTRL = 0x502000DC
EXECCTRL_WRAP_TOP_SHIFT, EXECCTRL_WRAP_BOTTOM_SHIFT = 12, 7
PIO_DEST_PINS, PIO_DEST_PINDIRS = 0, 4
SET_COUNT_SHIFT, SET_BASE_SHIFT = 26, 5


def test_a_python_gspi_device_on_the_chips_own_pins_answers_a_read(rp2040_factory, monkeypatch):
    """The CYW43's gSPI decoder with its native bit shifter switched off, so it is the pure-Python listener pair that sits on whatever
    pins this build has (native `GPIOPin`s on the native build) - the path a third-party device uses."""
    monkeypatch.setattr(cyw43_bus, "_native_shifter", lambda *args, **kwargs: None)
    rp2040 = rp2040_factory()
    bus = GSPIBus()
    bus.attach_gpio(rp2040)
    assert bus._native_shifter is None  # the Python listeners, not the shifter, did the work below

    master = _FakeGSPIMaster(rp2040)
    assert master.read_register(BUS_FUNCTION, SPI_READ_TEST_REGISTER, 4) == TEST_PATTERN


def test_a_python_listener_sees_the_pin_a_pio_state_machine_drives(rp2040_factory):
    """The block's own `advance()` - what the batch loop calls - is what tells the pins; a direct `execute_instruction()` does not."""
    rp2040 = rp2040_factory()
    rp2040.simulator = (
        object()
    )  # an owner, so a CTRL write does not step the block inline: advance() below does, as the batch loop does
    pin = 7
    rp2040.gpio[pin].ctrl = (rp2040.gpio[pin].ctrl & ~0x1F) | FUNCTION_PIO0
    rp2040.gpio[pin].pad_value |= 0x40
    rp2040.gpio[pin].check_for_updates()
    seen: list[tuple[GPIOPinState, GPIOPinState]] = []
    rp2040.gpio[pin].add_listener(lambda new, old: seen.append((new, old)))

    # set pindirs, 1 ; set pins, 1 ; set pins, 0 ; jmp 1   (SET_BASE = the pin)
    program = [pio_set(PIO_DEST_PINDIRS, 1), pio_set(PIO_DEST_PINS, 1), pio_set(PIO_DEST_PINS, 0), pio_jmp(1)]
    for index, opcode in enumerate(program):
        rp2040.write_uint32(PIO0_INSTR_MEM0 + 4 * index, opcode)
    rp2040.write_uint32(PIO0_SM0_EXECCTRL, (3 << EXECCTRL_WRAP_TOP_SHIFT) | (1 << EXECCTRL_WRAP_BOTTOM_SHIFT))
    rp2040.write_uint32(PIO0_SM0_PINCTRL, (1 << SET_COUNT_SHIFT) | (pin << SET_BASE_SHIFT))
    rp2040.write_uint32(PIO0_CTRL, 0x1)  # SM0 enabled
    for _ in range(12):  # the pacing model steps at most one instruction per machine per advance() call
        rp2040.pio[0].advance(8)

    highs = [new == GPIOPinState.HIGH for new, _old in seen]
    # the first call is the pin turning from an input into a low output (SET PINDIRS), then it toggles with the program
    assert highs[:5] == [False, True, False, True, False]
    assert all(new != old for new, old in seen)  # a listener is called on changes only


@pytest.mark.parametrize("count", [1, 3])
def test_every_listener_added_is_called_for_every_change_the_chip_makes(rp2040_factory, count):
    """A listener sees the pin as the *chip* drives it (output level, or an input's pull state), so the stimulus is SIO output writes."""
    rp2040 = rp2040_factory()
    pin = rp2040.gpio[5]
    pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
    rp2040.write_uint32(SIO_GPIO_OE_SET, 1 << 5)
    pin.check_for_updates()
    calls = [0] * count
    for index in range(count):

        def listener(_new, _old, index=index):
            calls[index] += 1

        pin.add_listener(listener)
    rp2040.write_uint32(
        SIO_GPIO_OUT_SET, 1 << 5
    )  # the bus path a guest takes; poking `sio.gpio_value` directly does not notify listeners
    rp2040.write_uint32(SIO_GPIO_OUT_CLR, 1 << 5)
    assert calls == [2] * count
