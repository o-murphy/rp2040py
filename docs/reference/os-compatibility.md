<!-- Reference (living how-to). OS × feature compatibility matrix. -->

# OS compatibility

What works where. Columns are host operating systems; rows are rp2040py features. This is a
living reference — update a cell when a platform gets confirmed (or breaks). Sources are inline:
the CLI's own platform gates (`src/rp2040py/cli/`), `setup.py`'s wheel logic, the README's
"Tested" list, and [reference/mpremote.md](mpremote.md). The speed multipliers in the rows below
are the README's own re-measured 2026-08-14 figures (MicroPython 1.28.0 boot, best-of-N) — they
are not per-OS measurements, just a reminder of what each row buys you.

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
| `rp2040py.native` compiled Cython core (~11.8×) | ✅ | ✅ | ✅ | ✅ | ❌ <sup>[[1]](#fn1)</sup> | ❌ <sup>[[1]](#fn1)</sup> |
| Pure-Python fallback (no compiled extension) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| PyPy fast path (~15×) | ✅ | ✅ | ✅ | ❓ <sup>[[2]](#fn2)</sup> | ❌ <sup>[[3]](#fn3)</sup> | ❌ <sup>[[3]](#fn3)</sup> |
| MockLED — onboard LED (`external/led_mock.py`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| WiFi (Pico W / CYW43439, `--board pico_w`) — scan/join against the built-in fake AP | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> | ⚠️ <sup>[[4]](#fn4)</sup> |
| …and real network access through its NAT bridge (guest TCP/UDP → host sockets) | ✅ <sup>[[5]](#fn5)</sup> | ⚠️ <sup>[[5]](#fn5)</sup> | ⚠️ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> | ❓ <sup>[[5]](#fn5)</sup> |
| BOOTSEL button (`external/bootsel_button.py`, both boards) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Waveshare 2.9″ e-Paper (`Epd2in9G` external device) | ✅ <sup>[[6]](#fn6)</sup> | ❓ <sup>[[6]](#fn6)</sup> | ❓ <sup>[[6]](#fn6)</sup> | ❓ <sup>[[6]](#fn6)</sup> | ❓ <sup>[[6]](#fn6)</sup> | ❓ <sup>[[6]](#fn6)</sup> |
| GDB server (`run`, TCP port 3333) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `--tcp-port` (socket REPL, `socket://host:port`) | ✅ | ✅ | ✅ | ✅ | ✅ <sup>[[7]](#fn7)</sup> | ✅ <sup>[[7]](#fn7)</sup> |
| `--pty` (real POSIX pseudo-terminal) | ✅ | ✅ | ❌ <sup>[[8]](#fn8)</sup> | ✅ | ❌ <sup>[[8]](#fn8)</sup> | ❌ <sup>[[8]](#fn8)</sup> |
| Interactive REPL, raw-mode Ctrl+X without Enter | ✅ | ✅ | ⚠️ <sup>[[9]](#fn9)</sup> | ✅ | ⚠️ <sup>[[9]](#fn9)</sup> | ⚠️ <sup>[[9]](#fn9)</sup> |
| `mpremote` over `--tcp-port` via `rp2040py mpremote` proxy ([mpremote.md](mpremote.md)) | ✅ | ✅ | ✅ | ✅ | ⚠️ <sup>[[10]](#fn10)</sup> | ❌ <sup>[[11]](#fn11)</sup> |
| Real `mpremote` binary, bare interactive REPL over `--tcp-port` | ❌ <sup>[[12]](#fn12)</sup> | ❌ <sup>[[12]](#fn12)</sup> | ❌ <sup>[[12]](#fn12)</sup> | ❌ <sup>[[12]](#fn12)</sup> | ❌ <sup>[[12]](#fn12)</sup> | ❌ <sup>[[12]](#fn12)</sup> |
| Real `mpremote` binary, bare interactive REPL over `--pty` | ✅ <sup>[[13]](#fn13)</sup> | ✅ <sup>[[13]](#fn13)</sup> | — | ✅ <sup>[[13]](#fn13)</sup> | — | — |
| Run emulator **and** `mpremote` at the same time (two processes) | ✅ | ✅ | ✅ | ✅ | ⚠️ <sup>[[10]](#fn10)</sup> | ❌ <sup>[[11]](#fn11)</sup> |
| `--expect-text` / `--expect-regex` scripted exit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Ctrl+C (SIGINT) shutdown | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Graceful shutdown on `SIGTERM` (`kill <pid>`) | ✅ | ✅ | ❌ <sup>[[14]](#fn14)</sup> | ✅ | — | — |
| `[fs]` littlefs image tooling (`--littlefs`, `mklittlefs`) | ✅ | ✅ | ✅ | ✅ <sup>[[15]](#fn15)</sup> | ❌ <sup>[[1]](#fn1)</sup> | ❌ <sup>[[1]](#fn1)</sup> |
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
**[4]** **Not a finished feature**, though the ⚠️ is about feature-completeness, not the platform —
this half is pure protocol emulation with no host dependency at all. Scan/join is live-boot-verified
against real MicroPython Pico W firmware (v1.23.0/v1.28.0), but the AP is a fixture: one fake
`"RP2040PY-GUEST"` network, any password "succeeds," no hidden-SSID or auth-failure path,
and there's no AP mode (`network.WLAN.IF_AP`), no IPv6, and one guest only. (`disconnect()` *was*
a no-op here; fixed in [0054](../records/0054-cyw43-disassoc.md).) See [records/0027-cyw43-wifi.md](../records/0027-cyw43-wifi.md) (and
[0048](../records/0048-cyw43-nat-reflector.md)'s "Known gaps" for the full inventory).

<a id="fn5"></a>
**[5]** Unlike everything else in this table's WiFi rows, this one genuinely **does** depend on the
host: the bridge terminates the guest's TCP/UDP on real `asyncio` sockets on your machine, so it
needs real outbound network access (and inherits whatever the host's firewall/sandbox allows). The
bridge's own hermetic test suite (`tests/test_cyw43_nat.py`, real loopback sockets) runs in CI on
Linux, macOS **and** Windows — that's how two Windows-specific bugs were caught — so the code path
is exercised on all three desktop platforms. What is Linux-only is the *live* verification: the
`pico_w` jobs that boot real firmware and open a real TCP connection to `1.1.1.1:80` run on
`ubuntu-latest` only - `ci-micropython.yml` (MicroPython: TCP, DNS via `mip.install()`, NTP, TLS),
`ci-circuitpython.yml` (CircuitPython's `wifi`/`socketpool`) and `ci-kaluma.yml` (Kaluma's
`require('wifi')`/`net`). All three stacks drive the same emulated bus; that is also where each has
been run by hand. Hence ✅ on Linux, ⚠️ (works in tests, never live-booted)
on macOS/Windows, ❓ on mobile, where app-sandbox network behavior is untested. See
[records/0048-cyw43-nat-reflector.md](../records/0048-cyw43-nat-reflector.md).

<a id="fn6"></a>
**[6]** `external/epd2in9g.py`'s `Epd2in9G` — wire-protocol emulation of the Waveshare 2.9″
e-Paper (G). Verified on Linux, with a Tkinter/`ttk` demo viewer (`demo/eink_run.py`; see
[records/0046-epd2in9g-external-device.md](../records/0046-epd2in9g-external-device.md)). The
device emulation is host-independent and should run wherever the core does, but only Linux is
verified and the viewer needs a Tkinter display — hence ❓ elsewhere.

<a id="fn7"></a>
**[7]** The `--tcp-port` server runs on iOS in both apps; you connect to it with `mpremote` **from
another device** (or another machine on the network), since the app sandbox can't run a second
`mpremote` process next to the emulator on the same device (see [10]/[11]). On desktop and Android
you can also connect locally from the same machine.

<a id="fn8"></a>
**[8]** `--pty` needs a real POSIX pty (`pty.openpty()` / `os.ttyname()`), which Windows has no
equivalent for — `rp2040py micropython --pty` exits with a clear error there rather than crashing
(`cli/__init__.py` gates on `pty is None`) — and which iOS app sandboxes lack too. Use `--tcp-port`
on those platforms instead.

<a id="fn9"></a>
**[9]** Raw terminal mode (Ctrl+X quits without Enter) needs `termios`, which is POSIX-only. On
Windows — and anywhere stdin isn't a real tty (the iOS app consoles) — the REPL falls back to
line-buffered mode: it still works and Ctrl+X still quits, but only after Enter. See
`cli/stdio_repl.py`.

<a id="fn10"></a>
**[10]** iOS Pythonista: works only from inside [StaSh](https://github.com/ywangd/stash) — with
StaSh running and `rp2040py` launched from a script — and additionally needs `mpremote`'s stdio
patched to StaSh's `ShIO`.

<a id="fn11"></a>
**[11]** iOS PythonIDE: runs one Python process per app instance with no real subprocess support,
so it can't host the proxy / a second process alongside the emulator — confirmed not working by
hand.

<a id="fn12"></a>
**[12]** The plain, unpatched `mpremote` binary's bare interactive REPL crashes over `--tcp-port`'s
`socket://` transport on every OS — an upstream `mpremote`/pySerial bug (missing `.fd`), filed at
micropython/micropython#18660. Use `rp2040py mpremote` (patches it) or `--pty` (see [13]). Every
other `mpremote` subcommand (`exec`/`run`/`fs`/…) works fine over `--tcp-port` with the plain binary.

<a id="fn13"></a>
**[13]** Over `--pty` the plain binary's bare REPL **does** work, because a pty slave is a real
POSIX tty exposing `.fd` (unlike `socket://` — that's the whole reason `--pty` exists). Available
wherever `--pty` exists (POSIX: Linux, macOS, Termux); n/a on Windows/iOS, which have no `--pty`
(see [8]).

<a id="fn14"></a>
**[14]** Graceful `SIGTERM` handling (so `--dump-fs` cleanup still runs on `kill`) uses
`loop.add_signal_handler(SIGTERM, …)`, unavailable on Windows' ProactorEventLoop — Windows falls
back to Ctrl+C only, matching this command's pre-asyncio behavior. See `cli/__init__.py` and
[records/0021-shutdown-coordinator.md](../records/0021-shutdown-coordinator.md).

<a id="fn15"></a>
**[15]** The `[fs]` extra pulls in `littlefs-python`, a compiled C dependency. Confirmed working by
hand on Android under **both** Termux and Pydroid 3. Where `[fs]` is unavailable (iOS — see [1]),
`--dump-fs` is the littlefs-python-free alternative for reading a filesystem back out.
