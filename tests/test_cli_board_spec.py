"""Tests for `--board-spec`/`RP2040PY_BOARD_SPEC` (docs/records/0049's "Accepted design" section):
`_load_board_spec_target()`'s `target:attr` resolution, and each subcommand's own
`_resolve_board()`/`_resolve_run_mcu()`/`_resolve_mklittlefs_layout()` mutual-exclusion/validation
logic - exercised directly against hand-built `argparse.Namespace`s, same philosophy as
`test_cli_bootrom.py` (no real network, no full `cli.main()`/simulator boot needed).
"""

import argparse

import pytest

from rp2040py import cli
from rp2040py.boards import BOARDS, BoardSpec
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import KALUMA, MICROPYTHON
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _write_board_file(tmp_path, source: str) -> str:
    path = tmp_path / "my_board.py"
    path.write_text(source)
    return f"{path}:BOARD"


_PLAIN_PICO_SOURCE = "from rp2040py.boards import BOARDS\nBOARD = BOARDS['pico']\n"


class TestLoadBoardSpecTarget:
    def test_loads_from_a_file_path(self, tmp_path):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)

        assert cli._load_board_spec_target(target) is BOARDS["pico"]

    def test_loads_from_a_dotted_module_path(self, tmp_path, monkeypatch):
        monkeypatch.syspath_prepend(str(tmp_path))
        (tmp_path / "my_dotted_board.py").write_text(_PLAIN_PICO_SOURCE)

        assert cli._load_board_spec_target("my_dotted_board:BOARD") is BOARDS["pico"]

    def test_malformed_target_without_a_colon_returns_none(self, caplog):
        assert cli._load_board_spec_target("no_colon_here") is None
        assert "target:attr" in caplog.text

    def test_nonexistent_file_and_unimportable_module_returns_none(self, caplog):
        assert cli._load_board_spec_target("definitely_not_a_real_module_xyz:BOARD") is None
        assert "definitely_not_a_real_module_xyz" in caplog.text

    def test_missing_attribute_returns_none(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, "X = 1\n")

        assert cli._load_board_spec_target(target) is None
        assert "BOARD" in caplog.text

    def test_attr_not_a_boardspec_instance_returns_none(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, "BOARD = 'not a BoardSpec'\n")

        assert cli._load_board_spec_target(target) is None
        assert "BoardSpec" in caplog.text

    def test_error_while_executing_the_board_file_returns_none(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, "raise ValueError('boom')\n")

        assert cli._load_board_spec_target(target) is None
        assert "boom" in caplog.text


class TestResolveBoard:
    """`_resolve_board()` - the `micropython`/`kaluma` `--board`/`--board-spec`/
    `RP2040PY_BOARD_SPEC` resolver."""

    def test_board_spec_is_used_instead_of_resolve_board_spec(self, tmp_path, monkeypatch):
        target = _write_board_file(
            tmp_path,
            "import dataclasses\nfrom rp2040py.boards import BOARDS\n"
            "BOARD = dataclasses.replace(BOARDS['pico'], image='custom.uf2')\n",
        )
        monkeypatch.setattr(
            cli, "resolve_board_spec", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called"))
        )
        args = argparse.Namespace(board=None, board_spec=target, image=None, fetch_fw_only=False)

        board = cli._resolve_board(args, MICROPYTHON)

        assert board.image == "custom.uf2"

    def test_board_and_board_spec_together_is_rejected(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board="pico", board_spec=target, image=None, fetch_fw_only=False)

        assert cli._resolve_board(args, MICROPYTHON) is None
        assert "--board" in caplog.text

    def test_board_spec_with_image_flag_exits(self, tmp_path):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board=None, board_spec=target, image="1.21.0", fetch_fw_only=False)

        with pytest.raises(SystemExit):
            cli._resolve_board(args, MICROPYTHON)

    def test_board_spec_with_fetch_fw_only_exits(self, tmp_path):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board=None, board_spec=target, image=None, fetch_fw_only=True)

        with pytest.raises(SystemExit):
            cli._resolve_board(args, MICROPYTHON)

    def test_board_spec_env_var_used_when_flag_omitted(self, tmp_path, monkeypatch):
        target = _write_board_file(
            tmp_path,
            "import dataclasses\nfrom rp2040py.boards import BOARDS\n"
            "BOARD = dataclasses.replace(BOARDS['pico'], image='env.uf2')\n",
        )
        monkeypatch.setenv("RP2040PY_BOARD_SPEC", target)
        args = argparse.Namespace(board=None, board_spec=None, image=None, fetch_fw_only=False)

        board = cli._resolve_board(args, MICROPYTHON)

        assert board.image == "env.uf2"

    def test_board_spec_with_no_image_set_is_rejected(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board=None, board_spec=target, image=None, fetch_fw_only=False)

        assert cli._resolve_board(args, MICROPYTHON) is None
        assert "image" in caplog.text.lower()

    def test_no_board_spec_falls_back_to_resolve_board_spec_with_the_board_name_and_tag(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cli,
            "resolve_board_spec",
            lambda board, firmware_spec, tag=None: (
                calls.append((board, firmware_spec, tag)) or BoardSpec(image="x.uf2")
            ),
        )
        args = argparse.Namespace(board="pico_w", board_spec=None, image="1.21.0", fetch_fw_only=False)

        board = cli._resolve_board(args, MICROPYTHON)

        assert calls == [("pico_w", MICROPYTHON, "1.21.0")]
        assert board.image == "x.uf2"

    def test_omitted_board_defaults_to_pico(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cli,
            "resolve_board_spec",
            lambda board, firmware_spec, tag=None: calls.append(board) or BoardSpec(image="x"),
        )
        args = argparse.Namespace(board=None, board_spec=None, image=None, fetch_fw_only=False)

        cli._resolve_board(args, MICROPYTHON)

        assert calls == ["pico"]


class TestResolveRunMcu:
    """`_resolve_run_mcu()` - `run`'s own `--board`/`--board-spec`/`RP2040PY_BOARD_SPEC` resolver.
    Simpler than `_resolve_board()`: `run`'s `--image` is a raw local program, never routed
    through a `BoardSpec` at all, so the only conflict is `--board`+`--board-spec` together."""

    def test_board_spec_builds_the_mcu_from_the_boardspec(self, tmp_path):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board=None, board_spec=target)

        mcu = cli._resolve_run_mcu(args)

        assert isinstance(mcu, RP2040)

    def test_board_and_board_spec_together_is_rejected(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board="pico", board_spec=target)

        assert cli._resolve_run_mcu(args) is None
        assert "--board" in caplog.text

    def test_no_board_spec_uses_build_rp2040_with_the_board_name(self, monkeypatch, rp2040_factory):
        calls = []
        monkeypatch.setattr(cli, "build_rp2040", lambda board: calls.append(board) or rp2040_factory())
        args = argparse.Namespace(board="pico_w", board_spec=None)

        mcu = cli._resolve_run_mcu(args)

        assert calls == ["pico_w"]
        assert isinstance(mcu, RP2040)

    def test_omitted_board_defaults_to_pico(self, monkeypatch, rp2040_factory):
        calls = []
        monkeypatch.setattr(cli, "build_rp2040", lambda board: calls.append(board) or rp2040_factory())
        args = argparse.Namespace(board=None, board_spec=None)

        cli._resolve_run_mcu(args)

        assert calls == ["pico"]


class TestResolveMklittlefsLayout:
    """`_resolve_mklittlefs_layout()` - `mklittlefs`'s own `--board`/`--board-spec`/`--target`/
    `--block-size`/`--block-count` resolver. `mklittlefs` never constructs a `Device`/`RP2040` at
    all, so unlike `_resolve_board()` there's no `--image`/`--fetch-fw-only` to reject - the only
    real conflicts are `--board-spec` combined with `--board` or `--target`."""

    def _layout_board_file(self, tmp_path):
        return _write_board_file(
            tmp_path,
            "import dataclasses\nfrom rp2040py.boards import BOARDS, FlashLayout\n"
            "BOARD = dataclasses.replace(BOARDS['pico'], "
            "layout=FlashLayout(fs_start=0x180000, fs_blockcount=100, fs_blocksize=4096))\n",
        )

    def test_board_spec_layout_used_when_no_explicit_block_size_or_count(self, tmp_path):
        target = self._layout_board_file(tmp_path)
        args = argparse.Namespace(board=None, board_spec=target, target=None, block_size=None, block_count=None)

        assert cli._resolve_mklittlefs_layout(args) == (4096, 100)

    def test_explicit_block_size_and_count_override_board_spec_layout(self, tmp_path):
        target = self._layout_board_file(tmp_path)
        args = argparse.Namespace(board=None, board_spec=target, target=None, block_size=512, block_count=8)

        assert cli._resolve_mklittlefs_layout(args) == (512, 8)

    def test_board_spec_with_no_layout_and_no_explicit_size_is_rejected(self, tmp_path, caplog):
        target = _write_board_file(tmp_path, _PLAIN_PICO_SOURCE)
        args = argparse.Namespace(board=None, board_spec=target, target=None, block_size=None, block_count=None)

        assert cli._resolve_mklittlefs_layout(args) is None
        assert "layout" in caplog.text.lower()

    def test_board_spec_with_target_is_rejected(self, tmp_path, caplog):
        target = self._layout_board_file(tmp_path)
        args = argparse.Namespace(
            board=None, board_spec=target, target="micropython", block_size=None, block_count=None
        )

        assert cli._resolve_mklittlefs_layout(args) is None
        assert "--target" in caplog.text

    def test_board_spec_with_board_is_rejected(self, tmp_path, caplog):
        target = self._layout_board_file(tmp_path)
        args = argparse.Namespace(board="pico", board_spec=target, target=None, block_size=None, block_count=None)

        assert cli._resolve_mklittlefs_layout(args) is None
        assert "--board" in caplog.text

    def test_no_board_spec_falls_back_to_target_lookup(self):
        args = argparse.Namespace(board=None, board_spec=None, target="kaluma", block_size=None, block_count=None)
        expected = _flash_layout(KALUMA, "pico")

        assert cli._resolve_mklittlefs_layout(args) == (expected["fs_blocksize"], expected["fs_blockcount"])

    def test_no_target_and_no_board_spec_defaults_to_micropython_layout(self):
        args = argparse.Namespace(board=None, board_spec=None, target=None, block_size=None, block_count=None)
        expected = _flash_layout(MICROPYTHON, "pico")

        assert cli._resolve_mklittlefs_layout(args) == (expected["fs_blocksize"], expected["fs_blockcount"])

    def test_no_board_spec_explicit_block_size_and_count_override_the_micropython_default(self):
        args = argparse.Namespace(board=None, board_spec=None, target=None, block_size=1, block_count=2)

        assert cli._resolve_mklittlefs_layout(args) == (1, 2)
