<!-- Reference (living how-to). OS × feature compatibility matrix. -->

# OS compatibility

What works where. Columns are host operating systems; rows are rp2040py features. This is a
living reference — update a cell when a platform gets confirmed (or breaks). Sources are inline:
the CLI's own platform gates (`src/rp2040py/cli/`), `setup.py`'s wheel logic, the README's
"Tested" list, and [reference/mpremote.md](mpremote.md).

Legend: ✅ works · ⚠️ works with a caveat · ❌ not supported · ❓ untested / unverified · — not applicable

Desktop columns are the bare OS; mobile columns name the app runtime as `<OS> (<app>)`, since on
phones the app sandbox — not the OS alone — decides what works. Android gets one column (Termux and
Pydroid 3 behave identically); iOS is split per app because they differ. Bracketed `[N]` markers
sit **in the specific cell they explain** and link to the matching note below; numbers run in
strict reading order (top-to-bottom, left-to-right). The same note is reused (same number) wherever
the same reason applies.

| Feature | Linux | macOS | Windows | Android (Termux / Pydroid 3) | iOS (Pythonista) | iOS (PythonIDE) |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Core emulation (`run` / `micropython` / `circuitpython` / `kaluma`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `rp2040py.native` compiled Cython core (~7×) | ✅ | ✅ | ✅ | ✅ | ❌ <sup>[[1]](#fn1)</sup> | ❌ <sup>[[1]](#fn1)</sup> |
| Pure-Python fallback (no compiled extension) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| PyPy fast path (~22×) | ✅ | ✅ | ✅ | ❓ <sup>[[2]](#fn2)</sup> | ❌ <sup>[[3]](#fn3)</sup> | ❌ <sup>[[3]](#fn3)</sup> |
| MockLED — onboard LED (`external/led_mock.py`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| WiFi (Pico W / CYW43439, `--board pico_w`) | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> |
| Waveshare 2.9″ e-Paper (`Epd2in9G` external device) | ✅ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> |
| GDB server (`run`, TCP port 3333) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `--tcp-port` (socket REPL, `socket://host:port`) | ✅ | ✅ | ✅ | ✅ | ✅ <sup>[[6]](#fn6)</sup> | ✅ <sup>[[6]](#fn6)</sup> |
| `--pty` (real POSIX pseudo-terminal) | ✅ | ✅ | ❌ <sup>[[7]](#fn7)</sup> | ✅ | ❌ <sup>[[7]](#fn7)</sup> | ❌ <sup>[[7]](#fn7)</sup> |
| Interactive REPL, raw-mode Ctrl+X without Enter | ✅ | ✅ | ⚠️ <sup>[[8]](#fn8)</sup> | ✅ | ⚠️ <sup>[[8]](#fn8)</sup> | ⚠️ <sup>[[8]](#fn8)</sup> |
| `mpremote` over `--tcp-port` via `rp2040py mpremote` proxy ([mpremote.md](mpremote.md)) | ✅ | ✅ | ✅ | ✅ | ⚠️ <sup>[[9]](#fn9)</sup> | ❌ <sup>[[10]](#fn10)</sup> |
| Real `mpremote` binary, bare interactive REPL over `--tcp-port` | ❌ <sup>[[11]](#fn11)</sup> | ❌ <sup>[[11]](#fn11)</sup> | ❌ <sup>[[11]](#fn11)</sup> | ❌ <sup>[[11]](#fn11)</sup> | ❌ <sup>[[11]](#fn11)</sup> | ❌ <sup>[[11]](#fn11)</sup> |
| Real `mpremote` binary, bare interactive REPL over `--pty` | ✅ <sup>[[12]](#fn12)</sup> | ✅ <sup>[[12]](#fn12)</sup> | — | ✅ <sup>[[12]](#fn12)</sup> | — | — |
| Run emulator **and** `mpremote` at the same time (two processes) | ✅ | ✅ | ✅ | ✅ | ⚠️ <sup>[[9]](#fn9)</sup> | ❌ <sup>[[10]](#fn10)</sup> |
| `--expect-text` / `--expect-regex` scripted exit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Ctrl+C (SIGINT) shutdown | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Graceful shutdown on `SIGTERM` (`kill <pid>`) | ✅ | ✅ | ❌ <sup>[[13]](#fn13)</sup> | ✅ | — | — |
| `[fs]` littlefs image tooling (`--littlefs`, `mklittlefs`) | ✅ | ✅ | ✅ | ✅ <sup>[[14]](#fn14)</sup> | ❌ <sup>[[1]](#fn1)</sup> | ❌ <sup>[[1]](#fn1)</sup> |
| `--dump-fs` (littlefs-python-free FS dump) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `--fat12 <image>` (consume a pre-built FAT12 image) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Notes

<a id="fn1"></a>
**[1]** iOS app runtimes (Pythonista/PythonIDE) can't load compiled extensions, so the native
Cython core **and** `littlefs-python` (the `[fs]` extra) aren't available there — `pip install
rp2040py` resolves to the pure-Python `py3-none-any` wheel (`setup.py`'s `IS_IOS` branch).
Everything still runs, just via the pure-Python path.

<a id="fn2"></a>
**[2]** PyPy on Android is untested — worth trying under Termux; ❓ until confirmed.

<a id="fn3"></a>
**[3]** There's no PyPy build for iOS.

<a id="fn4"></a>
**[4]** In progress — **not a finished feature**. Scripted scan/join is live-boot-verified against
real MicroPython Pico W firmware (v1.23.0/v1.28.0), but the real-network bridge (step 4) isn't
implemented yet — see [records/0027-cyw43-wifi.md](../records/0027-cyw43-wifi.md) and
[records/0045-cyw43-nat-libslirp-cython.md](../records/0045-cyw43-nat-libslirp-cython.md). The
emulation is host-independent, so the ⚠️ is about feature-completeness, not the platform.

<a id="fn5"></a>
**[5]** `external/epd2in9g.py`'s `Epd2in9G` — wire-protocol emulation of the Waveshare 2.9″
e-Paper (G). Verified on Linux, with a Tkinter/`ttk` demo viewer (`demo/eink_run.py`; see
[records/0046-epd2in9g-external-device.md](../records/0046-epd2in9g-external-device.md)). The
device emulation is host-independent and should run wherever the core does, but only Linux is
verified and the viewer needs a Tkinter display — hence ❓ elsewhere.

<a id="fn6"></a>
**[6]** The `--tcp-port` server runs on iOS in both apps; you connect to it with `mpremote` **from
another device** (or another machine on the network), since the app sandbox can't run a second
`mpremote` process next to the emulator on the same device (see [9]/[10]). On desktop and Android
you can also connect locally from the same machine.

<a id="fn7"></a>
**[7]** `--pty` needs a real POSIX pty (`pty.openpty()` / `os.ttyname()`), which Windows has no
equivalent for — `rp2040py micropython --pty` exits with a clear error there rather than crashing
(`cli/__init__.py` gates on `pty is None`) — and which iOS app sandboxes lack too. Use `--tcp-port`
on those platforms instead.

<a id="fn8"></a>
**[8]** Raw terminal mode (Ctrl+X quits without Enter) needs `termios`, which is POSIX-only. On
Windows — and anywhere stdin isn't a real tty (the iOS app consoles) — the REPL falls back to
line-buffered mode: it still works and Ctrl+X still quits, but only after Enter. See
`cli/stdio_repl.py`.

<a id="fn9"></a>
**[9]** iOS Pythonista: works only from inside [StaSh](https://github.com/ywangd/stash) — with
StaSh running and `rp2040py` launched from a script — and additionally needs `mpremote`'s stdio
patched to StaSh's `ShIO`.

<a id="fn10"></a>
**[10]** iOS PythonIDE: runs one Python process per app instance with no real subprocess support,
so it can't host the proxy / a second process alongside the emulator — confirmed not working by
hand.

<a id="fn11"></a>
**[11]** The plain, unpatched `mpremote` binary's bare interactive REPL crashes over `--tcp-port`'s
`socket://` transport on every OS — an upstream `mpremote`/pySerial bug (missing `.fd`), filed at
micropython/micropython#18660. Use `rp2040py mpremote` (patches it) or `--pty` (see [12]). Every
other `mpremote` subcommand (`exec`/`run`/`fs`/…) works fine over `--tcp-port` with the plain binary.

<a id="fn12"></a>
**[12]** Over `--pty` the plain binary's bare REPL **does** work, because a pty slave is a real
POSIX tty exposing `.fd` (unlike `socket://` — that's the whole reason `--pty` exists). Available
wherever `--pty` exists (POSIX: Linux, macOS, Termux); n/a on Windows/iOS, which have no `--pty`
(see [7]).

<a id="fn13"></a>
**[13]** Graceful `SIGTERM` handling (so `--dump-fs` cleanup still runs on `kill`) uses
`loop.add_signal_handler(SIGTERM, …)`, unavailable on Windows' ProactorEventLoop — Windows falls
back to Ctrl+C only, matching this command's pre-asyncio behavior. See `cli/__init__.py` and
[records/0021-shutdown-coordinator.md](../records/0021-shutdown-coordinator.md).

<a id="fn14"></a>
**[14]** The `[fs]` extra pulls in `littlefs-python`, a compiled C dependency. Confirmed working by
hand on Android under **both** Termux and Pydroid 3. Where `[fs]` is unavailable (iOS — see [1]),
`--dump-fs` is the littlefs-python-free alternative for reading a filesystem back out.
