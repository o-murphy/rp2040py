# 0085. CircuitPython `code.py`/`boot.py` on the LCD board, and where a WiFi screenshot has to come from

- Status: **Implemented (2026-08-19)** for the `code.py`/`boot.py` half; the WiFi-on-screen half is
  recorded below with its own result.
- Conceived: 2026-08-19
- Related: [0056](0056-st7735s-waveshare-lcd-board.md) (the `St7735s` device and the LCD board file
  these screenshots come from), [0027](0027-cyw43-wifi.md) and
  [0048](0048-cyw43-nat-reflector.md) (the emulated CYW43439 and its NAT bridge - and the
  CircuitPython WiFi test that already existed), [0036](0036-littlefs-fat12-exclusivity.md)
  (`--littlefs` vs. `--fat12`), [0035](0035-board-aware-fs-flash-offset.md) (where each board's
  filesystem region starts), [0066](0066-board-support-expansion.md) (which boards could host this
  combination, and what they'd need first)

## The question

Would a `--circuitpython` LCD screenshot show a WiFi connection, if the run were given an ordinary
CircuitPython setup - a `code.py`, or a `boot.py`?

Two separable halves, and they have different answers:

1. does *guest* code reach those screenshots at all, or do they only ever show what the firmware
   itself paints at boot (which is all [0056](0056-st7735s-waveshare-lcd-board.md) ever
   demonstrated)?
2. can that guest code join a network on this board?

## What was already true, and what was missing

`--circuitpython` runs had no way to supply `code.py`/`boot.py` at all. `--fat12` (and
`MicroPythonDevice(fat12=...)`) has always existed, but nothing in this repo *built* such an
image - the flag could only load one somebody else produced. MicroPython's side has two builders
(the `mklittlefs` subcommand and `demo/mklittlefs_dump.py`); CircuitPython's had none.

The gap is not symmetric with MicroPython's, either. `demo/mklittlefs_dump.py` gets away with
letting the *firmware* write its own filesystem over the raw REPL, because MicroPython's
filesystem is writable from the guest. CircuitPython deliberately refuses to write to CIRCUITPY
while USB is attached (`storage.remount()` raises), so on that side the host has to lay the bytes
down itself.

## What landed

- **`demo/mkfat12.py`** - a dependency-free FAT12 builder/reader: format a fresh volume, put
  8.3-named files in its root directory (with the `DIR_NTres` lowercase flags real firmware uses,
  so `code.py` reads back as `code.py`), or patch files into an image dumped out of a previous
  run, and read a file back out (`--read boot_out.txt`). It writes no LFN entry chains, so
  `settings.toml` is out of reach; `boot.py`/`code.py`/`main.py`/`lib` all fit 8.3.
- **`demo/lcd_run.py --code/--boot/--fat12/--dump-fs`** - the flags that put one on the emulated
  drive and read it back afterwards.
- **`demo/cp_lcd_demo.py`** - the CircuitPython counterpart of `demo/mp_lcd_demo.py`, and a much
  smaller file: it never touches `displayio`, because the panel already *is* the console here.
- **`demo/wifi_lcd_run.py` + `demo/cp_wifi_lcd_demo.py`** - the WiFi half; see below.

## Findings

### 1. Guest `code.py` output does reach the screenshots

The panel is CircuitPython's console on this board, so anything `code.py` prints is drawn on it.
The status bar changes to `code.py` while it runs, which is a second, independent confirmation
that the file was found and executed rather than merely present:

    uv run --with pillow python demo/lcd_run.py --circuitpython --code demo/cp_lcd_demo.py \
        --screenshot out --frames 0 --timeout 200

### 2. `boot.py` runs, but never appears on screen

`boot.py` executes - and its output goes to `boot_out.txt` on the drive, not to the console, so it
is invisible to every screenshot by construction. Reading the drive back out is what shows it:

    python demo/lcd_run.py --circuitpython --boot boot.py --dump-fs after.img ...
    python demo/mkfat12.py --output /dev/null --base after.img --read boot_out.txt

    Adafruit CircuitPython 10.2.1 on 2026-05-13; Waveshare RP2040-LCD-0.96 with rp2040
    Board ID:waveshare_rp2040_lcd_0_96
    UID:FFFFFFFFFFFFFFFF
    boot.py output:
    rp2040py: boot.py ran

So "add a standard boot.py" is precisely the case that *cannot* be answered from a screenshot.

### 3. This board's firmware has no `wifi` module at all

The Waveshare RP2040-LCD-0.96 has no CYW43439, and its CircuitPython build reflects that: the
UF2's payload contains no `wifi`, `socketpool`, `ssl` or `ipaddress` module name anywhere (checked
by scanning the decoded UF2 payload of 10.2.1 for each string - zero occurrences, against 1 for
`displayio`). `import wifi` therefore raises, and *that* is what a screenshot of a WiFi attempt on
this board shows - `demo/cp_lcd_demo.py` prints it deliberately rather than hiding it:

    board: waveshare_rp2040_lc
    d_0_96
    wifi: MISSING
    no module named 'wifi'

### 4. A freshly formatted CIRCUITPY delays USB enumeration past the 30s default

`MicroPythonDevice.start_async()`'s 30s default is enough for a board whose drive is already
populated, and not enough for one booting a just-formatted volume: CircuitPython writes its own
`boot_out.txt`, `lib/`, `.fseventsd` and friends *before* USB comes up, and in emulation that runs
past 30s. The symptom is a bare `TimeoutError: device did not enumerate over USB within 30.0s`,
which reads like a hang rather than the slow-but-fine path it is. Both display runners therefore
pass an explicit, much larger timeout - it costs a fast run nothing, since nothing waits on the
ceiling when the device is quicker.

## The WiFi half: it needs a different board

No RP2040 board has both an onboard CYW43439 and an onboard display that CircuitPython
auto-initialises, so there is nothing to just *point* `--code` at. What does exist:

- `--board pico_w` already carries a `Cyw43439` ([0027](0027-cyw43-wifi.md)), with a live-verified
  CircuitPython WiFi path (`tests/circuitpython/main-cyw43.py`, [0048](0048-cyw43-nat-reflector.md),
  verified against 9.2.9);
- `St7735s` ([0056](0056-st7735s-waveshare-lcd-board.md)) is an ordinary `ExternalDevice` that can
  be attached to any board, on pins the radio doesn't use (its constructor defaults - SPI1,
  CS=GP9, DC=GP8, RST=GP12 - are already clear of the CYW43439's GP23/24/25/29);
- the Pico W's own CircuitPython build ships `displayio`/`busdisplay`/`fourwire`/`terminalio`
  alongside `wifi`/`socketpool` (same UF2-payload scan as finding 3), so guest code can build the
  display itself.

`demo/wifi_lcd_run.py` composes exactly that: the built-in `pico_w` spec plus one more
`ExternalDevice`, with `demo/cp_wifi_lcd_demo.py` as `code.py`.

### Why guest-built displays still get the console

The interesting question this raised: on a board whose `board_init()` builds no display, does
anything reach the panel at all after `code.py` constructs one? Upstream (10.2.1) says yes, in two
places:

- `shared-module/displayio/display_core.c`'s `displayio_display_core_construct()` calls
  `supervisor_start_terminal(width, height)` - *any* display construction sizes and starts the
  supervisor terminal, not just a board's own;
- `shared-module/busdisplay/BusDisplay.c` ends the constructor with
  `if (!circuitpython_splash.in_group) { common_hal_busdisplay_busdisplay_set_root_group(self,
  &circuitpython_splash); }` - the same Blinka + status bar + scroll area group the LCD board's
  own display shows.

and `supervisor/shared/serial.c`'s `serial_write_substring()` writes to `supervisor_terminal`
before the console, so it is genuinely every write, not just `print()`.

The consequence for screenshots: nothing is on the panel until the guest's `BusDisplay`
constructor runs, so the boot banner (and `boot.py`, per finding 2) is already past by then. The
first frame is the first `print()` after that line.

### `auto_refresh` is not affordable here

A `busdisplay` with `auto_refresh=True` repaints at 60 fps, and every repaint is real SPI traffic
the emulator executes *interleaved with* the CYW43's PIO/gSPI hot path - the heaviest thing in
this emulator ([0047](0047-cyw43-pio-gpio-hotpath.md)). `demo/cp_wifi_lcd_demo.py` builds its
display with `auto_refresh=False` and calls `display.refresh()` once per status line: the panel is
a status display in this demo, not an animation. For scale, `.github/workflows/ci-circuitpython.yml`
budgets 5 minutes for the CYW43 WLAN test with no display at all (33s measured on a developer
machine, 2026-08-16).

### Incidentally: CircuitPython 10.2.1's WiFi path works

[0048](0048-cyw43-nat-reflector.md) verified CircuitPython WiFi against **9.2.9** and said moving
that to 10.x "needs a live re-verification first, not just a matrix edit" -
`.github/workflows/ci-circuitpython.yml` accordingly runs 10.2.1 with `wlan: false`. Building this
demo needed that answer, so the existing script was run against 10.2.1 as-is:

    uv run rp2040py micropython --circuitpython --board pico_w --image 10.2.1 \
        tests/circuitpython/main-cyw43.py

    scan: [('RP2040PY-GUEST', -87, 6)]
    connected: True
    ipv4_address: 10.0.0.2
    ipv4_gateway: 10.0.0.1
    Received 151 bytes: b'HTTP/1.1 426 Upgrade Required...'
    getaddrinfo: ('176.58.119.26', 80)
    CIRCUITPYTHON CYW43 OK

Scan, join, DHCP, a real TCP connection and a real DNS query, all through the emulated chip and
the NAT bridge, on 10.2.1. That is the live re-verification 0048 asked for; flipping the CI
matrix's `wlan` flag for 10.2.1 is a separate, deliberate change and is **not** made here.

## Progress log

- 2026-08-19: `demo/mkfat12.py`, `demo/lcd_run.py`'s `--code`/`--boot`/`--fat12`/`--dump-fs`,
  `demo/cp_lcd_demo.py` and findings 1-4 landed, with a screenshot of guest `code.py` output on
  the panel and a `boot_out.txt` read back out of a dumped drive. `demo/wifi_lcd_run.py` +
  `demo/cp_wifi_lcd_demo.py` landed alongside them, verified at the console level (the guest code
  runs cleanly on a Pico W under 10.2.1 - display constructed, no traceback, `connected: True`,
  `ip 10.0.0.2`), with the on-panel screenshot itself still outstanding.
