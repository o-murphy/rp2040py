"""Integration test: a real, unmodified `mpremote` (the `fs cp`/`--tcp-port` motivation - see
README.md's "mpremote" section) talking to `SocketInteractiveRepl` over a real `socket://` TCP
connection, via pySerial's own `serial_for_url()` - nothing in this test or in `socket_repl.py`
patches `mpremote`/pySerial internals.

There's no real MicroPython firmware behind the socket here - `_FakeRawReplDevice` below
implements just enough of the raw-REPL wire protocol (see `mpremote`'s own `transport_serial.py`)
to answer `mpremote`'s exec/raw-paste-probe/soft-reset handshake, running received code through a
real CPython `exec()` with a small MicroPython-flavored `os` shim (plain-tuple `stat()`, matching
what `ast.literal_eval` - which `mpremote`'s own `fs_stat()` relies on - can actually parse; real
`os.stat_result`'s repr can't). This isolates what's new here (the TCP transport, wired through a
real mpremote client) from the raw-REPL protocol implementation itself, which is already exercised
elsewhere (tests/test_raw_repl.py, tests/test_device.py) against this project's own real emulated
MicroPython firmware - something this sandboxed test run can't download (see this repo's own
network policy notes; not reproducible here without a firmware image already cached locally).
"""

import asyncio
import contextlib
import errno
import io
import os as _real_os
import subprocess
import sys
from pathlib import Path

import pytest

mpremote = pytest.importorskip("mpremote", reason="needs the optional mpremote dev dependency")

# Every test here shells out to a real `mpremote` via [sys.executable, "-m", "mpremote", ...]
# (see _run_mpremote() below) - on Android's testbed sys.executable is "" (there's no standalone
# python binary; it's embedded in the app), so Popen([""...]) fails with PermissionError before
# mpremote even runs. Nothing about the TCP transport under test is Android-specific, just this
# subprocess-spawning mechanism, so skip the whole module rather than the "real mpremote" premise.
is_android = hasattr(sys, "getandroidapilevel") or "android" in sys.platform
if is_android:
    pytest.skip("mpremote-subprocess tests need sys.executable, which is empty on Android", allow_module_level=True)

from rp2040py.cli.socket_repl import SocketInteractiveRepl
from rp2040py.simulator import Simulator

_CTRL_A = 1
_CTRL_C = 3
_CTRL_D = 4
_RAW_PASTE_QUERY = b"\x05A\x01"


class _FakeFifo:
    def __init__(self, size: int) -> None:
        self.size = size
        self.item_count = 0


class _FakeRawReplDevice:
    """Implements the subset of MicroPython's raw-REPL wire protocol `mpremote` actually needs
    for `exec`/`fs cp`: Ctrl-C interrupt (no-op), Ctrl-A raw-REPL entry, a Ctrl-D-triggered soft
    reset banner, a raw-paste capability probe (answered "not supported" to keep this simple -
    exercises mpremote's real fallback path to plain raw REPL, not a shortcut around it), and
    command execution (256-bytes-at-a-time upload terminated by Ctrl-D, "OK" then
    stdout+EOF+stderr+EOF in reply).

    Executes received source through a real `exec()` - MicroPython-flavored just enough for
    `mpremote fs cp`/`exec` to work against it: `os.stat()` returns a plain tuple (like
    MicroPython's, unlike CPython's `os.stat_result` - `mpremote.fs_stat()` reprs it back through
    `ast.literal_eval()`, which only plain tuples/literals survive)."""

    def __init__(self, workdir: Path) -> None:
        self.on_serial_data = None
        self.on_device_connected = None
        self.tx_fifo = _FakeFifo(1_000_000)
        self._workdir = workdir
        self._at_prompt = False
        self._collecting = False
        self._command = bytearray()
        self._paste_probe = bytearray()
        self._globals = self._make_globals()

    def _make_globals(self) -> dict:
        # Remote paths mpremote sends (e.g. "uploaded.py", never a host path) are POSIX-flavored,
        # like a real MicroPython device's own filesystem - resolved here against self._workdir
        # rather than the real process cwd, so relative remote paths land in the test's own
        # tmp_path regardless of what this host process's actual cwd happens to be.
        workdir = self._workdir
        real_open = open

        def _resolve(path):
            return str(path) if _real_os.path.isabs(path) else str(workdir / path)

        def _fake_open(path, *args, **kwargs):
            return real_open(_resolve(path), *args, **kwargs)

        class _FakeOsModule:
            @staticmethod
            def stat(path):
                st = _real_os.stat(_resolve(path))
                return (st.st_mode, 0, 0, 0, 0, 0, st.st_size, 0, 0, 0)

            def __getattr__(self, name):
                return getattr(_real_os, name)

        return {"open": _fake_open, "os": _FakeOsModule(), "__name__": "__main__"}

    def _reply(self, data: bytes) -> None:
        if self.on_serial_data is not None:
            self.on_serial_data(data)

    def send_serial_byte(self, byte: int) -> None:
        self.tx_fifo.item_count += 1

        if byte == _CTRL_C:
            return

        if byte == _CTRL_A:
            self._collecting = False
            self._command.clear()
            self._paste_probe.clear()
            self._at_prompt = True
            self._reply(b"raw REPL; CTRL-B to exit\r\n>")
            return

        if self._at_prompt and not self._collecting and byte == _CTRL_D and not self._paste_probe:
            # Ctrl-D right at the raw-REPL prompt, before any raw-paste probe/command bytes -
            # mpremote's soft-reset request (enter_raw_repl(soft_reset=True), the default).
            self._reply(b"soft reboot\r\n")
            self._reply(b"raw REPL; CTRL-B to exit\r\n>")
            return

        if self._at_prompt and not self._collecting:
            self._paste_probe.append(byte)
            if bytes(self._paste_probe) == _RAW_PASTE_QUERY:
                # "Understood the raw-paste query, but don't support raw-paste" - forces
                # mpremote's real fallback to plain raw-REPL command upload, same as a real
                # older-firmware device that predates raw-paste mode.
                self._reply(b"R\x00")
                self._paste_probe.clear()
                self._collecting = True
            elif not _RAW_PASTE_QUERY.startswith(bytes(self._paste_probe)):
                # Not (a prefix of) the raw-paste probe - must be plain command bytes instead
                # (raw-paste disabled/unavailable client-side across the whole connection).
                self._collecting = True
                self._command.extend(self._paste_probe)
                self._paste_probe.clear()
            return

        if self._collecting:
            if byte == _CTRL_D:
                self._reply(b"OK")
                stdout, stderr = self._run(bytes(self._command))
                self._reply(stdout + b"\x04")
                self._reply(stderr + b"\x04>")
                self._command.clear()
                self._collecting = False
            else:
                self._command.append(byte)

    def _run(self, source: bytes) -> "tuple[bytes, bytes]":
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                # Stands in for a real device executing raw-REPL code - the whole point here.
                exec(compile(source, "<fake-device>", "exec"), self._globals)  # noqa: S102
        except OSError as exc:
            # mpremote's own _convert_filesystem_error() (transport.py) greps the exec error text
            # for "OSError" plus an errno *name* (e.g. "ENOENT") to turn a failed fs_stat()/etc.
            # back into a real OSError client-side - matching MicroPython's own OSError str(),
            # which names the errno rather than CPython's full strerror() text ("No such file or
            # directory"). Formatting it that way here is what lets e.g. `fs cp`'s "does the
            # destination already exist" check see a real ENOENT instead of an unrecognized error.
            name = errno.errorcode.get(exc.errno, exc.errno)
            return stdout.getvalue().encode(), f"OSError: [Errno {exc.errno}] {name}\n".encode()
        except Exception as exc:  # noqa: BLE001 - relayed to the client as the command's stderr
            return stdout.getvalue().encode(), f"{type(exc).__name__}: {exc}\n".encode()
        return stdout.getvalue().encode(), b""


async def _run_mpremote(port: int, *args: str, cwd: Path, timeout: float = 15.0) -> "subprocess.CompletedProcess[str]":
    # Via asyncio.to_thread(), not a plain blocking subprocess.run(): this test's own event loop
    # doubles as `repl`'s engine-room loop (per docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target
    # shape" - see _run_test()'s own docstring below for why it has to be the main thread), so a
    # plain blocking call here would starve that same loop of the chance to actually accept the
    # connection/serve the raw-REPL exchange mpremote is about to make - moving the blocking
    # subprocess wait to a worker thread keeps the loop free to do that concurrently.
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "mpremote", "connect", f"socket://127.0.0.1:{port}", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )


def _run_test(body) -> None:
    """Runs an async test body via `asyncio.run()` on this (the main) thread - not a background
    loop thread: `ProcessInteractiveRepl._on_start()`/`_on_stop()` call `signal.signal()`, which
    CPython restricts to the main thread of the main interpreter, matching how a real caller only
    ever awaits `start()`/`stop()` from `cli/__init__.py`'s own main-thread `asyncio.run()`."""
    asyncio.run(body())


def test_mpremote_exec_round_trips_through_the_tcp_transport(tmp_path):
    async def _body() -> None:
        device = _FakeRawReplDevice(tmp_path)
        repl = SocketInteractiveRepl(device, Simulator(), on_quit=lambda code: None, port=0)
        await repl.start()
        try:
            result = await _run_mpremote(repl.port, "exec", "print(1 + 1)", cwd=tmp_path)

            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "2"
        finally:
            await repl.stop()

    _run_test(_body)


def test_mpremote_fs_cp_uploads_a_file_through_the_tcp_transport(tmp_path):
    """fs_writefile()/fs_stat() (mpremote/transport.py) are themselves just chained exec() calls
    over the same raw-REPL connection - if the single exec() round trip above works, this mostly
    exercises that mpremote's higher-level `fs cp` command drives that chain correctly through
    this transport, not anything new about the transport itself."""

    async def _body() -> None:
        device = _FakeRawReplDevice(tmp_path)
        repl = SocketInteractiveRepl(device, Simulator(), on_quit=lambda code: None, port=0)
        await repl.start()
        try:
            local = tmp_path / "hello.py"
            local.write_text("print('hello from device')\n")
            # A bare relative filename, like a real remote MicroPython path (e.g. "/uploaded.py") -
            # never the host's own absolute path, which on Windows contains backslashes that would
            # corrupt the Python source mpremote embeds it into (fs_writefile()'s
            # "f=open('%s','wb')" % dest). _FakeRawReplDevice resolves it against tmp_path itself
            # (see _make_globals()).
            remote_name = "uploaded.py"

            result = await _run_mpremote(repl.port, "fs", "cp", str(local), f":{remote_name}", cwd=tmp_path)

            assert result.returncode == 0, result.stderr
            assert (tmp_path / remote_name).read_text() == "print('hello from device')\n"
        finally:
            await repl.stop()

    _run_test(_body)
