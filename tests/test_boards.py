"""Unit tests for `rp2040py.boards` - the `--board` registry from CYW43_WIFI_BACKLOG.md's step 0
(Board-loading API): maps a board name to an MCU class plus fixed `ExternalDevice` extras. Also
covers `resolve_board_spec()`/`build_rp2040_from_spec()` - the custom-board-authoring API from
docs/records/0049's "Accepted design" section - and `resolve_firmware()`/`resolve_layout()`, the
single firmware-resolution path docs/records/0059 moved into `BoardSpec` itself.

`_retrieve` is monkeypatched throughout rather than left to run: resolution's *decisions* are what
these test, and a real `retrieve()` would download firmware."""

import pytest

import rp2040py.boards as boards_module
from rp2040py.boards import (
    BOARDS,
    BoardSpec,
    FlashLayout,
    UnknownBoardError,
    UnknownFirmwareFamilyError,
    build_rp2040,
    build_rp2040_from_spec,
    resolve_board_spec,
    resolve_firmware,
    resolve_layout,
)
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import MICROPYTHON, BoardFirmwareSpec, FirmwareSpec
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


@pytest.fixture
def fake_retrieve(monkeypatch):
    """Replaces `retrieve()` with a recorder returning a fixed path, and hands back the list of
    `(version_map, image, board)` triples it was called with - the tag/URL/cache resolution itself
    is `firmware_retrieve.py`'s own tested job, not this module's."""
    calls: list[tuple[dict[str, str] | None, str | None, str | None]] = []

    def _retrieve(spec, image=None, board=None):
        version_map = spec.boards[board].fw if spec.boards and board in spec.boards else None
        calls.append((version_map, image, board))
        return "resolved.uf2"

    monkeypatch.setattr(boards_module, "_retrieve", _retrieve)
    return calls


def test_resolve_board_spec_combines_board_mcu_extras_with_resolved_image_and_layout(fake_retrieve):
    resolved = resolve_board_spec("pico", MICROPYTHON, "1.21.0")

    assert resolved.mcu is BOARDS["pico"].mcu
    assert resolved.extras is BOARDS["pico"].extras
    assert resolved.image == "resolved.uf2"
    assert resolved.layout == FlashLayout(**_flash_layout(MICROPYTHON, "pico"))
    assert fake_retrieve == [(MICROPYTHON.boards["pico"].fw, "1.21.0", "micropython")]


def test_resolve_board_spec_unknown_board_raises():
    with pytest.raises(UnknownBoardError, match=r"pico.*pico_w|pico_w.*pico"):
        resolve_board_spec("not_a_real_board", MICROPYTHON)


def test_resolve_board_spec_board_agnostic_firmware_spec_leaves_layout_none(fake_retrieve):
    # Mirrors BOOTROM's shape (known_versions, no boards): no per-board data to pick from, so it
    # adapts into one board-agnostic declaration with no layout at all.
    agnostic_spec = FirmwareSpec(boards=None, default_tag="b1", known_versions={"b1": "https://example.invalid/b1"})

    resolved = resolve_board_spec("pico", agnostic_spec)

    assert resolved.image == "resolved.uf2"
    assert resolved.layout is None
    assert fake_retrieve == [({"b1": "https://example.invalid/b1"}, None, "firmware")]


def test_resolve_board_spec_caller_supplied_firmware_spec_overrides_only_that_family(fake_retrieve):
    """A `FirmwareSpec` this project doesn't ship still resolves through the same path, and only
    displaces the family it stands in for - the board's other declarations survive."""
    custom = FirmwareSpec(boards={"pico": BoardFirmwareSpec(default_tag="9.9.9", fw={"9.9.9": "https://x.invalid/f"})})

    resolved = resolve_board_spec("pico", custom, None)

    assert fake_retrieve == [({"9.9.9": "https://x.invalid/f"}, None, "firmware")]
    assert resolved.firmware is not None
    assert set(resolved.firmware) == {"micropython", "circuitpython", "kaluma", "firmware"}


def test_builtin_boards_declare_every_family_firmware_specs_json_lists_them_under():
    for board in ("pico", "pico_w"):
        assert BOARDS[board].firmware is not None
        assert set(BOARDS[board].firmware) == {"micropython", "circuitpython", "kaluma"}
        assert BOARDS[board].firmware["micropython"] is MICROPYTHON.boards[board]


class TestResolveLayout:
    """`resolve_layout()` - the image-free half of resolution (docs/records/0059), which is what
    lets `mklittlefs --board-spec` size an image with no network at all."""

    def test_explicit_layout_wins_over_the_declaration(self):
        override = FlashLayout(fs_start=0x180000, fs_blockcount=1, fs_blocksize=4096)
        spec = BoardSpec(layout=override, firmware=BOARDS["pico"].firmware)

        assert resolve_layout(spec, "micropython") is override

    def test_comes_from_the_selected_family(self):
        assert resolve_layout(BOARDS["pico_w"], "micropython") == FlashLayout(**_flash_layout(MICROPYTHON, "pico_w"))

    def test_a_family_the_spec_does_not_declare_raises_naming_what_it_does(self):
        spec = BoardSpec(firmware={"micropython": MICROPYTHON.boards["pico"]})

        with pytest.raises(UnknownFirmwareFamilyError, match=r"circuitpython.*\['micropython'\]"):
            resolve_layout(spec, "circuitpython")

    def test_a_spec_with_neither_resolves_to_none(self):
        assert resolve_layout(BoardSpec(), "micropython") is None


class TestResolveFirmware:
    """`resolve_firmware()` - the one resolution path both `--board` and `--board-spec` come
    through (docs/records/0059)."""

    def test_declared_family_resolves_image_and_layout_at_its_own_default_tag(self, fake_retrieve):
        resolved = resolve_firmware(BOARDS["pico_w"], "micropython")

        assert fake_retrieve == [(MICROPYTHON.boards["pico_w"].fw, None, "micropython")]
        assert resolved.image == "resolved.uf2"
        assert resolved.layout == FlashLayout(**_flash_layout(MICROPYTHON, "pico_w"))

    def test_an_explicit_tag_is_resolved_against_the_selected_family(self, fake_retrieve):
        resolve_firmware(BOARDS["pico"], "circuitpython", "8.0.2")

        assert fake_retrieve == [(BOARDS["pico"].firmware["circuitpython"].fw, "8.0.2", "circuitpython")]

    def test_an_already_resolved_image_wins_and_is_not_re_resolved(self, fake_retrieve):
        spec = BoardSpec(image="already.uf2", firmware=BOARDS["pico"].firmware)

        resolved = resolve_firmware(spec, "micropython")

        assert resolved.image == "already.uf2"
        assert fake_retrieve == []
        # ... and the layout still comes off the declaration, which is the half that wasn't set.
        assert resolved.layout == FlashLayout(**_flash_layout(MICROPYTHON, "pico"))

    def test_an_explicit_image_argument_overrides_an_already_resolved_one(self, fake_retrieve, tmp_path):
        local = tmp_path / "mine.uf2"
        local.write_bytes(b"")
        spec = BoardSpec(image="already.uf2", firmware=BOARDS["pico"].firmware)

        resolve_firmware(spec, "micropython", str(local))

        assert fake_retrieve == [(None, str(local), None)]

    def test_a_local_path_needs_no_declaration_at_all(self, fake_retrieve, tmp_path):
        local = tmp_path / "mine.uf2"
        local.write_bytes(b"")

        resolved = resolve_firmware(BoardSpec(), "micropython", str(local))

        assert resolved.image == "resolved.uf2"
        assert fake_retrieve == [(None, str(local), None)]

    def test_a_url_needs_no_declaration_at_all(self, fake_retrieve):
        resolve_firmware(BoardSpec(), "micropython", "https://example.invalid/fw.uf2")

        assert fake_retrieve == [(None, "https://example.invalid/fw.uf2", None)]

    def test_a_family_the_spec_does_not_declare_raises_naming_what_it_does(self, fake_retrieve):
        spec = BoardSpec(firmware={"micropython": MICROPYTHON.boards["pico"]})

        with pytest.raises(UnknownFirmwareFamilyError, match=r"circuitpython.*\['micropython'\]"):
            resolve_firmware(spec, "circuitpython")

    def test_a_tag_against_a_spec_declaring_no_firmware_raises_saying_so(self, fake_retrieve):
        with pytest.raises(UnknownFirmwareFamilyError, match="no `firmware` at all"):
            resolve_firmware(BoardSpec(), "micropython", "1.28.0")

    def test_a_spec_with_nothing_to_resolve_is_returned_unchanged(self, fake_retrieve):
        """Rule 5: no image, no `firmware` - the spec stays unresolved and the caller's own "no
        image" failure (`BaseDevice`'s assert, the CLI's own error) is what reports it."""
        spec = BoardSpec()

        assert resolve_firmware(spec, "micropython") is spec
        assert fake_retrieve == []
