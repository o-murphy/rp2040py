import argparse

import pytest

from rp2040py import cli


def _mp_args(**overrides):
    defaults = {
        "image": "fixed-image.uf2",
        "expect_text": None,
        "gdb": False,
        "gdb_port": 3333,
        "bootrom": None,
        "circuitpython": False,
        "littlefs": "littlefs.img",
        "fat12": "fat12.img",
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
    booting a real emulator or touching the network - only the littlefs/fat12/circuitpython
    wiring passed into the constructor and the raw-REPL exec() result matter for these tests."""

    last_kwargs: "dict | None" = None
    last_bootrom_words: "list | None" = None

    def __init__(self, image, littlefs=None, fat12=None, circuitpython=False, bootrom_words=None, log_level=None):
        _FakeMicroPythonDevice.last_kwargs = {
            "image": image,
            "littlefs": littlefs,
            "fat12": fat12,
            "circuitpython": circuitpython,
        }
        _FakeMicroPythonDevice.last_bootrom_words = bootrom_words
        self.simulator = None

    def start(self, timeout=None):
        pass

    def exec(self, source, timeout=None):
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
    monkeypatch.setattr(cli, "retrieve", lambda spec, image=None: "fixed-image.uf2")
    return _FakeMicroPythonDevice


def test_micropython_mode_loads_littlefs_not_fat12(tmp_path, fake_device):
    (tmp_path / "littlefs.img").write_bytes(b"")
    (tmp_path / "fat12.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=False))

    assert exc_info.value.code == 0
    assert fake_device.last_kwargs == {
        "image": "fixed-image.uf2",
        "littlefs": "littlefs.img",
        "fat12": None,
        "circuitpython": False,
    }


def test_circuitpython_mode_loads_fat12_not_littlefs(tmp_path, fake_device):
    # Regression test: refactoring the littlefs/fat12 path-resolution logic into a single
    # `if not args.circuitpython` branch used to gate *both* assignments on it, so fat12.img was
    # silently never loaded in --circuitpython mode even when it existed on disk.
    (tmp_path / "littlefs.img").write_bytes(b"")
    (tmp_path / "fat12.img").write_bytes(b"")

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(circuitpython=True))

    assert exc_info.value.code == 0
    assert fake_device.last_kwargs == {
        "image": "fixed-image.uf2",
        "littlefs": None,
        "fat12": "fat12.img",
        "circuitpython": True,
    }


def test_micropython_mode_skips_missing_littlefs_image(fake_device):
    # Neither littlefs.img nor fat12.img exists in the isolated cwd - both should stay None
    # rather than erroring, matching "silently skipped if absent" from the README.
    with pytest.raises(SystemExit):
        cli._cmd_micropython(_mp_args(circuitpython=False))

    assert fake_device.last_kwargs is not None
    assert fake_device.last_kwargs["littlefs"] is None
    assert fake_device.last_kwargs["fat12"] is None


def test_circuitpython_mode_skips_missing_fat12_image(fake_device):
    with pytest.raises(SystemExit):
        cli._cmd_micropython(_mp_args(circuitpython=True))

    assert fake_device.last_kwargs is not None
    assert fake_device.last_kwargs["littlefs"] is None
    assert fake_device.last_kwargs["fat12"] is None


def test_missing_image_prints_the_requested_identifier_not_none(caplog, monkeypatch):
    # Regression test: this used to print the literal string "None" (the *result* of the failed
    # lookup) instead of what the user actually asked for.
    monkeypatch.setattr(cli, "retrieve", lambda spec, image=None: None)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(image="totally-bogus-version"))

    assert exc_info.value.code == 1
    assert "totally-bogus-version" in caplog.text
    assert "None" not in caplog.text


def test_exec_mode_writes_stdout_and_exits_zero_on_success(capsys, fake_device, monkeypatch):
    class _Device(_FakeMicroPythonDevice):
        def exec(self, source, timeout=None):
            return b"4\n", b""

    monkeypatch.setattr(cli, "MicroPythonDevice", _Device)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(command="print(2 + 2)"))

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.endswith("4\n")


def test_exec_mode_exits_nonzero_when_device_writes_to_stderr(fake_device, monkeypatch):
    class _Device(_FakeMicroPythonDevice):
        def exec(self, source, timeout=None):
            return b"", b"Traceback...\n"

    monkeypatch.setattr(cli, "MicroPythonDevice", _Device)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_micropython(_mp_args(command="raise ValueError"))

    assert exc_info.value.code == 1


def _kaluma_args(**overrides):
    defaults = {
        "image": None,
        "expect_text": None,
        "gdb": False,
        "gdb_port": 3333,
        "bootrom": None,
        "littlefs": "kaluma_littlefs.img",
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

    def __init__(self, image, littlefs=None, program=None, bootrom_words=None, log_level=None):
        _FakeKalumaDevice.last_kwargs = {"image": image, "littlefs": littlefs, "program": program}
        _FakeKalumaDevice.last_bootrom_words = bootrom_words
        _FakeKalumaDevice.last_instance = self
        self.simulator = None
        self.cdc = _FakeKalumaCdc()

    def start(self, timeout=None):
        pass

    def stop(self):
        pass


class _FakeStdioInteractiveRepl:
    """Stands in for StdioInteractiveRepl - _cmd_kaluma has no non-interactive escape hatch (no
    -c/-m/<filename> like _cmd_micropython), so the real class (raw termios, a stdin-reading
    thread, _wait_for_simulator's loop) would otherwise block these tests."""

    def __init__(self, cdc, on_data=None):
        self.cdc = cdc

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def fake_kaluma_device(monkeypatch):
    _FakeKalumaDevice.last_kwargs = None
    _FakeKalumaDevice.last_instance = None
    monkeypatch.setattr(cli, "KalumaDevice", _FakeKalumaDevice)
    monkeypatch.setattr(cli, "retrieve", lambda spec, image=None: "fixed-kaluma-image.uf2")
    monkeypatch.setattr(cli, "StdioInteractiveRepl", _FakeStdioInteractiveRepl)
    monkeypatch.setattr(cli, "_wait_for_simulator", lambda simulator, on_interrupt=None: None)
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
    monkeypatch.setattr(cli, "retrieve", lambda spec, image=None: None)

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
    from rp2040py.device.load_flash import KALUMA_FS_BLOCKCOUNT, KALUMA_FS_BLOCKSIZE

    image = tmp_path / "out.img"

    cli.main(["mklittlefs", "-o", str(image), "--target", "kaluma"])

    assert image.stat().st_size == KALUMA_FS_BLOCKSIZE * KALUMA_FS_BLOCKCOUNT


def test_mklittlefs_target_rejects_explicit_block_size_or_count(caplog, tmp_path):
    pytest.importorskip("littlefs", reason="mklittlefs needs the optional 'fs' extra")
    image = tmp_path / "out.img"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["mklittlefs", "-o", str(image), "--target", "kaluma", "--block-size", "4096"])

    assert exc_info.value.code == 1
    assert "mutually exclusive" in caplog.text
    assert not image.exists()
