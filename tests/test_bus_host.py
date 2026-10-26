"""What the C++ bus (core/bus.hpp, docs/records/0096-cpp-mcu-core.md Phase 2 step 4a) leaves to Python, against the pure bus:
the warnings it raises and their exact text, the USB-DPRAM hook, and a failure inside any of those
reaching the caller of the bus access. (A Python block behind a window - what the tests of a replaced PPB used to cover - is `test_peripheral_windows`.)"""

import pytest
from utils.chip_pair import Native, PurePython, make_chip

CHIPS = [PurePython, Native]
IDS = ["pure", "native"]


class Recorder:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error"):
            return lambda component, message: self.messages.append((name, component, message))
        raise AttributeError(name)


def _chip(cls):
    chip = make_chip(cls)
    chip.logger = Recorder()
    return chip


def test_the_warnings_and_their_texts_are_the_same():
    logs = []
    for cls in CHIPS:
        chip = _chip(cls)
        chip.read_uint32(0x20000001)  # unaligned
        chip.read_uint32(0x60000000)  # unmapped
        chip.write_uint32(0x60000000, 1)  # unmapped
        chip.write_uint8(0x60000001, 1)  # unmapped sub-word: read-modify-write through both
        logs.append(chip.logger.messages)

    assert logs[0] == logs[1]
    assert len(logs[0]) == 5


@pytest.mark.parametrize("cls", CHIPS, ids=IDS)
def test_a_logger_that_raises_surfaces_from_the_access(cls):
    chip = make_chip(cls)

    class Boom(Exception):
        pass

    class Angry:
        def warning(self, component, message):
            raise Boom(message)

    chip.logger = Angry()

    with pytest.raises(Boom):
        chip.read_uint32(0x60000000)
    with pytest.raises(Boom):
        chip.write_uint32(0x60000000, 1)


@pytest.mark.parametrize("cls", CHIPS, ids=IDS)
def test_the_dpram_hook_gets_the_offset_and_the_unmasked_value_and_may_raise(cls):
    chip = make_chip(cls)
    calls = []

    def hook(offset, value):
        calls.append((offset, value))
        if value == 99:
            raise KeyError("dpram")

    chip.usb_ctrl.dpram_updated = hook
    chip.write_uint32(0x50100008, 0xFFFFFFFB)

    assert calls == [(8, 0xFFFFFFFB)]
    with pytest.raises(KeyError):
        chip.write_uint32(0x5010000C, 99)
