"""Unit tests for `rp2040py.boards` - the `--board` registry from CYW43_WIFI_BACKLOG.md's step 0
(Board-loading API): maps a board name to an MCU class plus fixed `ExternalDevice` extras. Also
covers `resolve_board_spec()`/`build_rp2040_from_spec()` - the custom-board-authoring API from
docs/records/0049's "Accepted design" section."""

import pytest

import rp2040py.boards as boards_module
from rp2040py.boards import (
    BOARDS,
    BoardSpec,
    FlashLayout,
    UnknownBoardError,
    build_rp2040,
    build_rp2040_from_spec,
    resolve_board_spec,
)
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import MICROPYTHON, FirmwareSpec
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout


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


def test_build_rp2040_from_spec_attaches_each_extra_exactly_once():
    attached = []

    class _FakeExtra:
        def attach(self, rp2040):
            attached.append(rp2040)

    mcu = build_rp2040_from_spec(BoardSpec(mcu=RP2040, extras=(_FakeExtra,)))

    assert attached == [mcu]


def test_build_rp2040_from_spec_returns_a_real_rp2040_for_each_known_board():
    for spec in BOARDS.values():
        mcu = build_rp2040_from_spec(spec)
        assert isinstance(mcu, RP2040)


def test_build_rp2040_delegates_to_build_rp2040_from_spec(monkeypatch):
    """`build_rp2040()` is now a thin board-name-lookup wrapper - confirm it actually delegates,
    rather than duplicating `build_rp2040_from_spec()`'s own mcu-construction logic."""
    calls = []
    monkeypatch.setattr(
        boards_module, "build_rp2040_from_spec", lambda spec, clock=None: calls.append((spec, clock)) or RP2040()
    )

    mcu = build_rp2040("pico")

    assert calls == [(BOARDS["pico"], None)]
    assert isinstance(mcu, RP2040)


def test_resolve_board_spec_combines_board_mcu_extras_with_resolved_image_and_layout(monkeypatch):
    monkeypatch.setattr(boards_module, "_retrieve", lambda spec, tag, board: "resolved.uf2")
    monkeypatch.setattr(
        boards_module,
        "_flash_layout",
        lambda spec, board: {"fs_start": 0x180000, "fs_blockcount": 352, "fs_blocksize": 4096},
    )

    resolved = resolve_board_spec("pico", MICROPYTHON, "1.21.0")

    assert resolved.mcu is BOARDS["pico"].mcu
    assert resolved.extras is BOARDS["pico"].extras
    assert resolved.image == "resolved.uf2"
    assert resolved.layout == FlashLayout(fs_start=0x180000, fs_blockcount=352, fs_blocksize=4096)


def test_resolve_board_spec_unknown_board_raises():
    with pytest.raises(UnknownBoardError, match=r"pico.*pico_w|pico_w.*pico"):
        resolve_board_spec("not_a_real_board", MICROPYTHON)


def test_resolve_board_spec_board_agnostic_firmware_spec_leaves_layout_none(monkeypatch):
    # Mirrors BOOTROM's shape (known_versions, no boards) - flash_layout() has nothing to resolve
    # against a spec like this, so resolve_board_spec() must not call it at all.
    monkeypatch.setattr(boards_module, "_retrieve", lambda spec, tag, board: "resolved.elf")
    agnostic_spec = FirmwareSpec(boards=None, default_tag="b1", known_versions={"b1": "https://example.invalid/b1"})

    resolved = resolve_board_spec("pico", agnostic_spec)

    assert resolved.image == "resolved.elf"
    assert resolved.layout is None


def test_resolve_board_spec_layout_matches_flash_layout_for_a_real_firmware_spec(monkeypatch):
    """Only image resolution is mocked (no network) - flash_layout() itself runs for real against
    the committed firmware_specs.json, so this also catches a `resolve_board_spec()`/
    `flash_layout()` shape drift, not just resolve_board_spec()'s own wiring."""
    monkeypatch.setattr(boards_module, "_retrieve", lambda spec, tag, board: "resolved.uf2")

    resolved = resolve_board_spec("pico", MICROPYTHON, "1.21.0")

    assert resolved.layout == FlashLayout(**_flash_layout(MICROPYTHON, "pico"))
