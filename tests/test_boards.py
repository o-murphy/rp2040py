"""Unit tests for `rp2040py.boards` - the `--board` registry from CYW43_WIFI_BACKLOG.md's step 0
(Board-loading API): maps a board name to an MCU class plus fixed `ExternalDevice` extras."""

import pytest

from rp2040py.boards import BOARDS, BoardSpec, UnknownBoardError, build_rp2040
from rp2040py.rp2040 import RP2040


def test_known_boards_are_registered():
    assert set(BOARDS) == {"pico", "pico_w"}


def test_build_rp2040_returns_a_real_rp2040_for_each_known_board():
    for board in BOARDS:
        mcu = build_rp2040(board)
        assert isinstance(mcu, RP2040)


def test_build_rp2040_unknown_board_raises_with_choices_listed():
    with pytest.raises(UnknownBoardError, match=r"pico.*pico_w|pico_w.*pico"):
        build_rp2040("not_a_real_board")


def test_build_rp2040_attaches_each_extra_exactly_once(monkeypatch):
    attached = []

    class _FakeExtra:
        def attach(self, rp2040):
            attached.append(rp2040)

    monkeypatch.setitem(BOARDS, "test_board", BoardSpec(mcu=RP2040, extras=(_FakeExtra,)))

    mcu = build_rp2040("test_board")

    assert attached == [mcu]


def test_build_rp2040_extras_are_fresh_instances_per_call(monkeypatch):
    """extras are zero-arg factories, not shared instances - constructing the same board twice
    must not wire one ExternalDevice instance's mutable state onto two different RP2040s."""
    built = []

    class _FakeExtra:
        def __init__(self):
            self.rp2040 = None
            built.append(self)

        def attach(self, rp2040):
            self.rp2040 = rp2040

    monkeypatch.setitem(BOARDS, "test_board", BoardSpec(mcu=RP2040, extras=(_FakeExtra,)))

    mcu_a = build_rp2040("test_board")
    mcu_b = build_rp2040("test_board")

    assert len(built) == 2
    assert built[0] is not built[1]
    assert built[0].rp2040 is mcu_a
    assert built[1].rp2040 is mcu_b
