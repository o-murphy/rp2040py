import argparse
from pathlib import Path

import pytest

from rp2040py import cli
from rp2040py.boards import BoardSpec
from rp2040py.simulator import Simulator


def _mp_args(**overrides):
    defaults = {
        "image": "fixed-image.uf2",
        "board": "pico",
        "board_spec": None,
        "fetch_fw_only": False,
        "expect_text": None,
        "expect_regex": False,
        "gdb": False,
        "gdb_port": 3333,
        "bootrom": None,
        "circuitpython": False,
        "littlefs": None,
        "fat12": None,
        "dump_fs": None,
        "tcp_port": None,
        "pty": False,
        # -c mode, so _cmd_micropython exits right after exec() instead of dropping into an
        # interactive REPL that would block the test.
        "command": "pass",
        "module": None,
        "filename": None,
        "log_level": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _FakeMicroPythonDevice:
    """Stands in for MicroPythonDevice so _cmd_micropython can be driven end-to-end without
    booting a real emulator or touching the network - only the filesystem/circuitpython wiring
    passed into the constructor and the raw-REPL exec() result matter for these tests."""

    last_kwargs: "dict | None" = None
    last_bootrom_words: "list | None" = None

    def __init__(self, *, board, filesystem=None, circuitpython=False, bootrom_words=None, log_level=None):
        _FakeMicroPythonDevice.last_kwargs = {
            "image": board.image,
            "filesystem": filesystem,
            "circuitpython": circuitpython,
        }
        _FakeMicroPythonDevice.last_bootrom_words = bootrom_words
        self.simulator = None

    async def astart(self, timeout=None):
        pass

    async def aexec(self, source, timeout=None):
        return b"", b""

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fake_device(monkeypatch):
    _FakeMicroPythonDevice.last_kwargs = None
    monkeypatch.setattr(cli, "MicroPythonDevice", _FakeMicroPythonDevice)
    monkeypatch.setattr(cli, "resolve_firmware", lambda spec, family, image=None: BoardSpec(image="fixed-image.uf2"))
    return _FakeMicroPythonDevice


def test_micropython_mode_loads_littlefs_not_fat12(tmp_path, fake_device):
    (tmp_path / "littlefs.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=False, littlefs="littlefs.img"))

    assert exc_info.value.code == 0
    assert fake_device.last_kwargs == {
        "image": "fixed-image.uf2",
        "filesystem": Path("littlefs.img"),
        "circuitpython": False,
    }


def test_circuitpython_mode_loads_fat12_not_littlefs(tmp_path, fake_device):
    # Regression test: refactoring the littlefs/fat12 path-resolution logic into a single
    # `if not args.circuitpython` branch used to gate *both* assignments on it, so fat12.img was
    # silently never loaded in --circuitpython mode even when it existed on disk. Still worth
    # asserting now that the two collapsed into one `filesystem` argument picked by family.
    (tmp_path / "fat12.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=True, fat12="fat12.img"))

    assert exc_info.value.code == 0
    assert fake_device.last_kwargs == {
        "image": "fixed-image.uf2",
        "filesystem": Path("fat12.img"),
        "circuitpython": True,
    }


def test_micropython_mode_skips_missing_littlefs_image(fake_device):
    # littlefs.img doesn't exist in the isolated cwd - should stay None rather than erroring,
    # matching "silently skipped if absent" from the README.
    with pytest.raises(SystemExit):
        cli._cmd_micropython(_mp_args(circuitpython=False, littlefs="littlefs.img"))

    assert fake_device.last_kwargs is not None
    assert fake_device.last_kwargs["filesystem"] is None


def test_circuitpython_mode_skips_missing_fat12_image(fake_device):
    with pytest.raises(SystemExit):
        cli._cmd_micropython(_mp_args(circuitpython=True, fat12="fat12.img"))

    assert fake_device.last_kwargs is not None
    assert fake_device.last_kwargs["filesystem"] is None


def test_micropython_littlefs_and_fat12_are_mutually_exclusive(caplog, fake_device):
    # The bug this guards against: which flag "won" used to be decided implicitly by
    # --circuitpython, silently dropping the other one instead of erroring
    # (docs/records/0036-littlefs-fat12-exclusivity.md).
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=False, littlefs="littlefs.img", fat12="fat12.img"))

    assert exc_info.value.code == 1
    assert "mutually exclusive" in caplog.text
    assert fake_device.last_kwargs is None


def test_littlefs_is_rejected_in_circuitpython_mode(caplog, fake_device):
    """The filesystem format follows the firmware family (MicroPython reads littlefs,
    CircuitPython reads FAT12), so the wrong flag is an error rather than something quietly
    ignored - which is how a --fat12 run without --circuitpython used to boot with no filesystem
    at all and no hint as to why."""
    (Path.cwd() / "littlefs.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=True, littlefs="littlefs.img"))

    assert exc_info.value.code == 1
    assert "--littlefs is a MicroPython filesystem" in caplog.text
    assert fake_device.last_kwargs is None


def test_fat12_is_rejected_without_circuitpython(caplog, fake_device):
    (Path.cwd() / "fat12.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=False, fat12="fat12.img"))

    assert exc_info.value.code == 1
    assert "--fat12 is a CircuitPython filesystem" in caplog.text
    assert fake_device.last_kwargs is None


def test_micropython_littlefs_and_fat12_are_mutually_exclusive_even_with_circuitpython(caplog, fake_device):
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=True, littlefs="littlefs.img", fat12="fat12.img"))

    assert exc_info.value.code == 1
    assert "mutually exclusive" in caplog.text
    assert fake_device.last_kwargs is None


def test_missing_image_prints_the_requested_identifier_not_none(caplog, monkeypatch):
    # Regression test: this used to print the literal string "None" (the *result* of the failed
    # lookup) instead of what the user actually asked for.
    monkeypatch.setattr(cli, "resolve_firmware", lambda spec, family, image=None: BoardSpec())

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(image="totally-bogus-version"))

    assert exc_info.value.code == 1
    assert "totally-bogus-version" in caplog.text
    assert "None" not in caplog.text


def test_exec_mode_writes_stdout_and_exits_zero_on_success(capsys, fake_device, monkeypatch):
    class _Device(_FakeMicroPythonDevice):
        async def aexec(self, source, timeout=None):
            return b"4\n", b""

    monkeypatch.setattr(cli, "MicroPythonDevice", _Device)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(command="print(2 + 2)"))

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.endswith("4\n")


def test_exec_mode_exits_nonzero_when_device_writes_to_stderr(fake_device, monkeypatch):
    class _Device(_FakeMicroPythonDevice):
        async def aexec(self, source, timeout=None):
            return b"", b"Traceback...\n"

    monkeypatch.setattr(cli, "MicroPythonDevice", _Device)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(command="raise ValueError"))

    assert exc_info.value.code == 1


def test_tcp_port_rejects_combination_with_exec_mode(caplog):
    # --tcp-port replaces the interactive console with a headless TCP one - it has no defined
    # meaning combined with -c/-m/<filename> (run-once-then-exit mode), so this must error out
    # clearly instead of e.g. silently ignoring --tcp-port or the exec source.
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(tcp_port=0, command="pass"))

    assert exc_info.value.code == 1
    assert "--tcp-port" in caplog.text


def test_tcp_port_rejects_combination_with_module_mode(caplog):
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(tcp_port=0, command=None, module="sys"))

    assert exc_info.value.code == 1
    assert "--tcp-port" in caplog.text


def test_tcp_port_rejects_combination_with_filename_mode(caplog):
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(tcp_port=0, command=None, filename="script.py"))

    assert exc_info.value.code == 1
    assert "--tcp-port" in caplog.text


def test_expect_text_rejects_combination_with_exec_mode(caplog):
    # -c/-m/<filename> runs one device.exec() call and exits based on its own stdout/stderr - it
    # never reaches the console loop --expect-text's on_data watcher is wired into, so it used to
    # be silently ignored rather than doing anything a caller passing it would expect.
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(expect_text=["ready"], command="pass"))

    assert exc_info.value.code == 1
    assert "--expect-text" in caplog.text


def test_expect_text_rejects_combination_with_module_mode(caplog):
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(expect_text=["ready"], command=None, module="sys"))

    assert exc_info.value.code == 1
    assert "--expect-text" in caplog.text


def test_expect_text_rejects_combination_with_filename_mode(caplog):
    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(expect_text=["ready"], command=None, filename="script.py"))

    assert exc_info.value.code == 1
    assert "--expect-text" in caplog.text


def test_expect_text_tracker_requires_every_repeated_pattern_before_reporting_found():
    tracker = cli._ExpectTextTracker(["foo", "bar"], expect_regex=False)

    tracker.feed_line("line has foo only")
    assert not tracker.found

    tracker.feed_line("line has bar only")
    assert tracker.found


def test_expect_text_tracker_matches_are_order_independent():
    tracker = cli._ExpectTextTracker(["second", "first"], expect_regex=False)

    tracker.feed_line("this line has first")
    tracker.feed_line("this line has second")

    assert tracker.found


def test_expect_text_tracker_plain_substring_by_default():
    tracker = cli._ExpectTextTracker(["a.b"], expect_regex=False)

    tracker.feed_line("a.b literally")

    assert tracker.found


def test_expect_text_tracker_regex_mode_uses_re_search():
    tracker = cli._ExpectTextTracker([r"error: \d+"], expect_regex=True)

    tracker.feed_line("error: not-a-number")
    assert not tracker.found

    tracker.feed_line("error: 42")
    assert tracker.found


def test_expect_text_tracker_regex_mode_does_not_match_plain_substring_only():
    tracker = cli._ExpectTextTracker(["a.b"], expect_regex=True)

    # In regex mode "." matches any character, so this should NOT require a literal dot.
    tracker.feed_line("axb")

    assert tracker.found


def test_expect_text_tracker_inactive_without_any_pattern():
    tracker = cli._ExpectTextTracker(None, expect_regex=False)

    tracker.feed_line("anything at all")

    assert not tracker.active
    assert not tracker.found


def test_make_expect_text_watcher_requests_shutdown_once_all_patterns_are_found():
    from rp2040py.simulator import ShutdownRequest

    shutdown = ShutdownRequest()
    watcher = cli._make_expect_text_watcher(["foo", "bar"], False, shutdown)

    watcher(b"line with foo\n")
    assert not shutdown.event.is_set()

    watcher(b"line with bar\n")
    assert shutdown.event.is_set()


def test_tcp_port_starts_a_socket_repl_instead_of_the_stdio_one(fake_device, monkeypatch):
    started = {}

    class _FakeSocketRepl:
        def __init__(self, cdc, simulator, on_quit, port=0, on_data=None):
            started["port"] = port
            self.port = 12345

        async def start(self):
            started["started"] = True

        async def stop(self):
            started["stopped"] = True

    class _FakeCdc:
        def send_serial_byte(self, byte):
            pass

    class _Device(_FakeMicroPythonDevice):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cdc = _FakeCdc()
            self.simulator = Simulator()

    monkeypatch.setattr(cli, "MicroPythonDevice", _Device)
    monkeypatch.setattr(cli, "SocketInteractiveRepl", _FakeSocketRepl)
    # No wait_for_shutdown() to fake anymore - _await_shutdown() (the async replacement) returns
    # immediately on its own here, since this fake device's real-but-never-started Simulator has
    # `executing == False` from construction (start_execution() is never actually called).

    cli._cmd_micropython(_mp_args(tcp_port=54321, command=None, module=None, filename=None))

    assert started == {"port": 54321, "started": True, "stopped": True}


def _kaluma_args(**overrides):
    defaults = {
        "image": None,
        "board": "pico",
        "board_spec": None,
        "fetch_fw_only": False,
        "expect_text": None,
        "expect_regex": False,
        "gdb": False,
        "gdb_port": 3333,
        "bootrom": None,
        "littlefs": "kaluma_littlefs.img",
        "dump_fs": None,
        "tcp_port": None,
        "pty": False,
        "filename": None,
        "log_level": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class _FakeKalumaCdc:
    def __init__(self):
        self.sent = bytearray()
        self.on_serial_data = None
        self.on_device_connected = None

    def send_serial_byte(self, byte):
        self.sent.append(byte)


class _FakeKalumaDevice:
    """Stands in for KalumaDevice so _cmd_kaluma can be driven end-to-end without booting a real
    emulator or touching the network - only the littlefs wiring passed into the constructor and
    the nudge bytes sent to `.cdc` matter for these tests."""

    last_kwargs: "dict | None" = None
    last_instance: "_FakeKalumaDevice | None" = None
    last_bootrom_words: "list | None" = None

    def __init__(self, *, board, littlefs=None, program=None, bootrom_words=None, log_level=None):
        _FakeKalumaDevice.last_kwargs = {"image": board.image, "littlefs": littlefs, "program": program}
        _FakeKalumaDevice.last_bootrom_words = bootrom_words
        _FakeKalumaDevice.last_instance = self
        # A real Simulator (cheap to construct - no thread starts until .execute()) rather than
        # None: _cmd_kaluma reads device.simulator.shutdown_request, and _await_shutdown() reads
        # device.simulator.executing - both real Simulator behavior now. `executing` stays False
        # (start_execution() is never actually called here), so _await_shutdown() returns
        # immediately on its own - no need to fake it out.
        self.simulator = Simulator()
        self.cdc = _FakeKalumaCdc()

    async def astart(self, timeout=None):
        pass

    def stop(self):
        pass


class _FakeStdioInteractiveRepl:
    """Stands in for StdioInteractiveRepl - _cmd_kaluma has no non-interactive escape hatch (no
    -c/-m/<filename> like _cmd_micropython), so the real class (raw termios, an add_reader()
    callback on the engine-room loop, _await_shutdown()'s loop) would otherwise block these
    tests."""

    def __init__(self, cdc, simulator, on_data=None, on_quit=None):
        self.cdc = cdc
        self.simulator = simulator

    async def start(self):
        pass

    async def stop(self):
        pass


@pytest.fixture
def fake_kaluma_device(monkeypatch):
    _FakeKalumaDevice.last_kwargs = None
    _FakeKalumaDevice.last_instance = None
    monkeypatch.setattr(cli, "KalumaDevice", _FakeKalumaDevice)
    monkeypatch.setattr(
        cli, "resolve_firmware", lambda spec, family, image=None: BoardSpec(image="fixed-kaluma-image.uf2")
    )
    monkeypatch.setattr(cli, "StdioInteractiveRepl", _FakeStdioInteractiveRepl)
    return _FakeKalumaDevice


def test_kaluma_mode_loads_littlefs_image_when_present(tmp_path, fake_kaluma_device):
    (tmp_path / "kaluma_littlefs.img").write_bytes(b"")

    cli._cmd_kaluma(_kaluma_args())

    assert fake_kaluma_device.last_kwargs == {
        "image": "fixed-kaluma-image.uf2",
        "littlefs": "kaluma_littlefs.img",
        "program": None,
    }


def test_kaluma_mode_skips_missing_littlefs_image(fake_kaluma_device):
    cli._cmd_kaluma(_kaluma_args())

    assert fake_kaluma_device.last_kwargs == {"image": "fixed-kaluma-image.uf2", "littlefs": None, "program": None}


def test_kaluma_mode_stages_program_when_filename_given(fake_kaluma_device):
    cli._cmd_kaluma(_kaluma_args(filename="index.js"))

    assert fake_kaluma_device.last_kwargs == {
        "image": "fixed-kaluma-image.uf2",
        "littlefs": None,
        "program": "index.js",
    }


def test_kaluma_sends_no_nudge_bytes_after_start(fake_kaluma_device):
    # Unlike micropython, kaluma doesn't send anything proactively after connecting - a staged
    # <script.js>'s own auto-run output arrives on its own a few seconds later regardless (no
    # nudge needed, confirmed against real firmware). Kaluma's boot-time "Welcome to Kaluma"
    # banner is separately racy and would need an explicit ".hi" to reprint on demand, but nothing
    # here depends on seeing it.
    cli._cmd_kaluma(_kaluma_args())

    assert fake_kaluma_device.last_instance is not None
    assert bytes(fake_kaluma_device.last_instance.cdc.sent) == b""


def test_kaluma_missing_image_prints_the_requested_identifier(caplog, monkeypatch):
    monkeypatch.setattr(cli, "resolve_firmware", lambda spec, family, image=None: BoardSpec())

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_kaluma(_kaluma_args(image="totally-bogus-version"))

    assert exc_info.value.code == 1
    assert "totally-bogus-version" in caplog.text


def test_kaluma_end_to_end_through_argparse(fake_kaluma_device):
    cli.main(["kaluma", "--image", "1.2.1"])

    assert fake_kaluma_device.last_kwargs == {"image": "fixed-kaluma-image.uf2", "littlefs": None, "program": None}


def test_micropython_omitted_bootrom_uses_the_bundled_default(fake_device):
    from rp2040py.device.bootrom import BOOTROM_B1

    with pytest.raises(SystemExit):
        cli.main(["micropython", "--image", "1.2.1", "-c", "1"])

    assert fake_device.last_bootrom_words is BOOTROM_B1


def test_micropython_bootrom_flag_reaches_the_device(fake_device, tmp_path, monkeypatch):
    words = [0xAAAAAAAA, 0xBBBBBBBB]
    monkeypatch.setattr(cli, "_resolve_bootrom_words", lambda source: words if source == "b2" else None)

    with pytest.raises(SystemExit):
        cli.main(["micropython", "--image", "1.2.1", "--bootrom", "b2", "-c", "1"])

    assert fake_device.last_bootrom_words == words


def test_kaluma_bootrom_flag_reaches_the_device(fake_kaluma_device, monkeypatch):
    words = [0xCCCCCCCC]
    monkeypatch.setattr(cli, "_resolve_bootrom_words", lambda source: words if source == "b0" else None)

    cli.main(["kaluma", "--image", "1.2.1", "--bootrom", "b0"])

    assert fake_kaluma_device.last_bootrom_words == words


class TestRawReplSource:
    def test_command_takes_priority(self):
        args = _mp_args(command="1 + 1", module="sys", filename="script.py")
        assert cli._raw_repl_source(args) == "1 + 1"

    def test_module_becomes_an_import_statement(self):
        args = _mp_args(command=None, module="sys", filename=None)
        assert cli._raw_repl_source(args) == "import sys"

    def test_filename_is_read_from_disk(self, tmp_path):
        script = tmp_path / "script.py"
        script.write_text("print('hi')\n")
        args = _mp_args(command=None, module=None, filename=str(script))
        assert cli._raw_repl_source(args) == "print('hi')\n"

    def test_none_of_the_three_means_interactive_repl(self):
        args = _mp_args(command=None, module=None, filename=None)
        assert cli._raw_repl_source(args) is None


def test_load_image_rejects_unsupported_extensions():
    with pytest.raises(SystemExit):
        cli._load_image("firmware.bin", rp2040=None)  # type: ignore[arg-type]


def test_version_flag_prints_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["-V"])
    assert exc_info.value.code == 0
    assert "rp2040py" in capsys.readouterr().out


def test_mklittlefs_rejects_unknown_disk_version(capsys):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["mklittlefs", "-o", "out.img", "--disk-version", "9.9", "main.py"])
    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_mklittlefs_end_to_end_through_argparse(tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    main_src = tmp_path / "main.py"
    main_src.write_text("print('hi')\n")
    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image), str(main_src)])

    assert image.exists()


def test_mklittlefs_rejects_main_not_in_files_cleanly(caplog, tmp_path):
    # Regression test: build_littlefs_image's ValueError for an unmatched --main used to bubble
    # up as a raw Python traceback instead of a clean logged error + exit(1).
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    app_src = tmp_path / "app.py"
    app_src.write_text("pass\n")
    image = tmp_path / "out.img"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["mklittlefs", "-o", str(image), str(app_src), "--main", "not_in_files.py"])

    assert exc_info.value.code == 1
    assert "not_in_files.py" in caplog.text


def test_mklittlefs_rejects_existing_output_without_force(caplog, tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    app_src = tmp_path / "app.py"
    app_src.write_text("pass\n")
    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image), str(app_src)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["mklittlefs", "-o", str(image), str(app_src)])

    assert exc_info.value.code == 1
    assert "already exists" in caplog.text


def test_mklittlefs_force_overwrites_existing_output(tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    app_src = tmp_path / "app.py"
    app_src.write_text("pass\n")
    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image), str(app_src)])
    cli.main(["mklittlefs", "-o", str(image), "--force", str(app_src)])

    assert image.exists()


def test_mklittlefs_allows_no_files(tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image)])

    assert image.exists()


def test_mklittlefs_target_presets_block_size_and_count(tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    from rp2040py.utils.firmware_retrieve import KALUMA
    from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout

    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image), "--target", "kaluma"])

    layout = _flash_layout(KALUMA, "pico")
    assert image.stat().st_size == layout["fs_blocksize"] * layout["fs_blockcount"]


def test_mklittlefs_target_rejects_explicit_block_size_or_count(caplog, tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    image = tmp_path / "out.img"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["mklittlefs", "-o", str(image), "--target", "kaluma", "--block-size", "4096"])

    assert exc_info.value.code == 1
    assert "mutually exclusive" in caplog.text
    assert not image.exists()
