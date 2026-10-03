"""What the C++ bus (core/bus.hpp, docs/records/0096-cpp-mcu-core.md Phase 2 step 4a) leaves to Python, against the pure bus:
the warnings it raises and their exact text, the USB-DPRAM hook, a replaced PPB/SIO, and a failure inside any of those
reaching the caller of the bus access."""

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
def test_a_replaced_ppb_sees_every_access_with_its_offset(cls):
    chip = _chip(cls)
    seen = []

    class Ppb:
        def read_uint32(self, offset):
            seen.append(("r", offset))
            return 0x1234

        def write_uint32(self, offset, value):
            seen.append(("w", offset, value))

        def reset(self):
            pass

    chip.ppb = Ppb()

    assert chip.read_uint32(0xE000E100) == 0x1234
    chip.write_uint32(0xE000ED04, -3)
    chip.write_uint8(0xE000E401, 0xAB)  # a sub-word access: read-modify-write of the word

    assert seen == [("r", 0x100), ("w", 0xD04, -3), ("r", 0x400), ("w", 0x400, 0xAB34)]


@pytest.mark.parametrize("cls", CHIPS, ids=IDS)
def test_an_error_in_a_replaced_ppb_reaches_the_caller(cls):
    chip = _chip(cls)

    class Ppb:
        def read_uint32(self, offset):
            raise ValueError("ppb read")

        def write_uint32(self, offset, value):
            raise RuntimeError("ppb write")

        def reset(self):
            pass

    chip.ppb = Ppb()

    with pytest.raises(ValueError, match="ppb read"):
        chip.read_uint32(0xE000E100)
    with pytest.raises(RuntimeError, match="ppb write"):
        chip.write_uint32(0xE000E100, 1)
    assert chip.read_uint32(0x20000000) is not None  # nothing stale is left behind


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
