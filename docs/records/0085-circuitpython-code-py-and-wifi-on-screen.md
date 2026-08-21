# 0085. CircuitPython `code.py`/`boot.py` on the LCD board, and where a WiFi screenshot has to come from

- Status: **Implemented (2026-08-19).** Both halves answered, with screenshots; a wrong
  CircuitPython `fs_start` for `pico_w` found and fixed along the way (finding 5).
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
`ExternalDevice`, with `demo/cp_wifi_lcd_demo.py` as `code.py`. It works - the panel ends up
showing a real join, with the address the emulator's own DHCP server handed out:

    wifi test
    mac ok
    connected: True
    ip 10.0.0.2

(and `gw 10.0.0.1` on the next line, once the terminal scrolls). So the answer to the original
question is yes, but only on a board that has a radio at all: the screenshot shows the connection
because the console is on the panel, exactly as it is on the Waveshare board.

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

### Finding 5: the Pico W's CIRCUITPY offset was wrong here (0x180000 -> 0x181000)

Building the WiFi demo turned up a real bug in this project's own board data. The symptom: the
Pico W run produced **zero frames**, and neither the `--code` file nor a `boot_out.txt` came back
out of `--dump-fs`. Asking the firmware itself settled it:

    rp2040py micropython --circuitpython --board pico_w --image 10.2.1 --fat12 tiny.img \
        -c "print('CODEPY:', open('/code.py').read()[:40])"

    CODEPY: print("Hello World!")

That is CircuitPython's *own* default `code.py`: the image was invisible to the firmware, which
found no filesystem where it looks and silently reformatted the drive. Scanning the whole
emulated flash after a blank boot for FAT boot sectors says where "where it looks" actually is:

    FAT boot sector at 0x181000: oem=b'MSDOS5.0' totsec16=1016 spf=3 label=b'NO NAME    '

0x181000, not the 0x180000 `firmware_specs.json` carried - and `totsec16 = 1016` is exactly
`(2 MiB - 0x181000) / 512`, so the whole geometry is consistent with that start and no other.

The cause is a genuine trap in CircuitPython's own build system, and
[0035](0035-board-aware-fs-flash-offset.md)'s audit fell into it: this board has **two** different
firmware sizes, in two different files, and the drive start is computed from the one that does not
appear in the linker script.

- `ports/raspberrypi/boards/raspberry_pi_pico_w/link.ld`: `firmware_size = 1532k` - sizes the
  linker's own section, and is what 0035 read.
- `ports/raspberrypi/boards/raspberry_pi_pico_w/mpconfigboard.mk`:
  `CFLAGS += -DCIRCUITPY_FIRMWARE_SIZE='(1536 * 1024)'` - and *this* is what
  `CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR` (`ports/raspberrypi/mpconfigport.h`:
  `CIRCUITPY_FIRMWARE_SIZE + CIRCUITPY_INTERNAL_NVM_SIZE`) is built from.

1536K + 4K = 0x181000. Fixed in `scripts/fetch_firmware.py`'s `_CIRCUITPYTHON_FLASH_LAYOUT` and in
the generated `firmware_specs.json`; `pico` is unaffected (it overrides neither, so the default
1020K + 4K = 0x100000 stands, which the Waveshare board's own runs here confirm independently).
The same command that diagnosed it confirms the fix - the firmware now reads back the image's own
file instead of one it wrote itself, and creates only `boot_out.txt` beside it rather than a whole
fresh drive:

    CODEPY: print("MARKER code.py ran")
    LISTDIR ['code.py', 'boot_out.txt']

Why it survived until now: nothing had ever put a filesystem image on a Pico W under
CircuitPython. The CYW43 tests ([0027](0027-cyw43-wifi.md)/[0048](0048-cyw43-nat-reflector.md))
push their script over the raw REPL and never touch the drive, and CI's CircuitPython jobs use
`--expect-text` on the boot banner. A wrong `fs_start` is invisible to every one of those - it only
shows up the moment something writes or reads the drive, which is what `--code`/`--dump-fs` do.

The value holds for every tag this project tracks, so one per-board number stays correct:
`mpconfigboard.mk` carries the same `(1536 * 1024)` in 8.0.2, 9.2.9, 10.0.0 and 10.2.1 (checked
individually). That also settles, for this board and this constant, the "are these stable across
version tags?" question [0035](0035-board-aware-fs-flash-offset.md) left open.

Still unverified the same way, and deliberately not touched here: MicroPython's own `pico_w`
`fs_start` (`0x12c000`). It is derived from a single source (`MICROPY_HW_FLASH_STORAGE_BYTES =
848K`, 2 MiB - 0xd4000) with no linker/C split to get wrong, and CI exercises `--littlefs` only on
plain `pico`.

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
  `demo/cp_wifi_lcd_demo.py` landed alongside them.
- 2026-08-19: the Pico W run produced no frames at all until finding 5's wrong `fs_start` was
  found and fixed; with `0x181000` it draws the join on the panel, and
  `demo/screenshots/wifi-lcd-circuitpython-connected.png` is that frame.
- 2026-08-19: `ci-circuitpython.yml`'s `10.2.1` job flipped to `wlan: true`. This record's demo
  drives CYW43 from guest `code.py` rather than from the raw REPL, so the CI script itself was run
  as its own check first - `tests/circuitpython/main-cyw43.py` passes unchanged on 10.2.1, right
  through to its `CIRCUITPYTHON CYW43 OK`. Evidence in [0048](0048-cyw43-nat-reflector.md)'s
  progress log, which is where that re-verification was asked for.

## Correction (2026-08-19, same day)

**This record's premise for why the host has to build the CIRCUITPY image is wrong.** It says
CircuitPython "deliberately refuses to write to CIRCUITPY while USB is attached
(`storage.remount()` raises), so on that side the host has to lay the bytes down itself". That is
true of a real board plugged into a real computer. It is **not** true in this emulator, and the
difference is a property of rp2040py, not of CircuitPython:

- CircuitPython's guard is not "is USB attached" but a lock:
  `shared-module/storage/__init__.c`'s `common_hal_storage_remount()` raises only
  `if (!blockdev_lock(fs_usermount))`.
- That lock is taken from `supervisor/shared/usb/usb_msc_flash.c`'s `tud_msc_is_writable_cb()`
  ("Lock the blockdev once we say we're writable") - a **TinyUSB callback**, which fires only once
  a USB host actually issues mass-storage traffic.
- rp2040py never issues any: `usb/cdc.py`'s `extract_endpoint_numbers()` walks the firmware's own
  configuration descriptor for the one interface with `interface_class == CDC_DATA_CLASS` and two
  endpoints, takes those, and ignores every other interface. The firmware exposes an MSC interface;
  nothing here ever claims it or sends it a CBW. So the lock is free.

Measured, not reasoned: `rp2040py micropython --circuitpython --board-spec
boards/waveshare_rp2040_lcd_0_96/__init__.py:BOARD -c "..."` running
`storage.remount('/', readonly=False)` answers `REMOUNT: ok`, a following `open('/probe.txt','w')`
answers `WRITE: ok`, and the file appears in `os.listdir('/')`. A second run wrote `code.py`,
`settings.toml` **and** `lib/greeter.py` and dumped the drive with `--dump-fs`: all three are in
the image, written by the firmware's own FatFS - including the LFN chain for `settings.toml` and
the `LIB` directory entry (with `DIR_NTres = 0x08`, so it reads back as `lib`) holding
`GREETER PY`.

What follows from that:

- The MicroPython trick this record said had no CircuitPython equivalent - "let the firmware write
  its own filesystem over the raw REPL", which is exactly what `demo/mklittlefs_dump.py` does -
  **does** have one here. A `demo/mkfat12_dump.py` counterpart is now the obvious thing to build
  ([0086](0086-fat12-library-and-a-mkfat12-subcommand.md) carries that, since it changes what a
  FAT12 library would even be for).
- `demo/mkfat12.py` keeps a real job that route cannot do: building an image **offline**, in
  milliseconds, with no firmware booted - which is what a test, CI, and `--code`/`--boot` need -
  and reading files back out of a dump.
- Nothing about the screenshots, the `boot.py` -> `boot_out.txt` finding, or finding 5's `fs_start`
  fix is affected. Only the justification for the builder is.
- One caveat this hands to whoever builds the REPL route: enumerating MSC later (for fidelity with
  a real board, where CIRCUITPY *is* read-only to the guest) would take that lock and break it. So
  the two are mutually exclusive by construction, and an MSC implementation has to be opt-in.

## Appendix: the demo-half rework (planned 2026-08-20, not built)

Step 3 of [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)'s sequencing is
"rework the demo half on top of whatever 0087 and 0086 settle on". Both have now settled:
[0086](0086-fat12-library-and-a-mkfat12-subcommand.md) is **rejected**, and 0087's route is
in-process: `aexec()` writes the files, then a **soft** reset (a bare Ctrl-D at the raw-REPL
prompt - firmware-side, already supported, no emulator reset and no USB re-enumeration) makes the
firmware re-run `code.py`, with `--dump-fs` optional. The mechanism and what is still missing for
it live in 0087; what changes *here* is the demo surface.

### `--code`/`--boot` keep building an offline image by default

Not a fallback and not inertia - a measurement. `demo/lcd_run.py` already raises its start timeout
above the 30s default because a **freshly formatted** CIRCUITPY has CircuitPython laying down
`boot_out.txt`/`lib`/`.fseventsd` before USB comes up ("measured past 30s in emulation"), where the
same board with an already-populated drive enumerates well inside it. The REPL route starts from
exactly that blank volume every time; `demo/mkfat12.py`'s 8.3 builder produces the populated one in
milliseconds. Default stays where the fast path is.

### What the REPL route becomes: an opt-in mode, for what 8.3 cannot express

The case it exists for is the one this record already measured - `settings.toml` (needs an LFN
chain) and `lib/greeter.py` (needs a directory) - written by the firmware's own FatFS rather than
by a host-side writer that would have to implement both. That is the whole reason
[0086](0086-fat12-library-and-a-mkfat12-subcommand.md) could be rejected, so the demo has to
actually offer the route, not just cite it.

Two pieces, in 0087's own numbering:

- `demo/mkfat12_dump.py` (0087 item 1) - generates the raw-REPL script, the counterpart of
  `demo/mklittlefs_dump.py`. Usable on its own, exactly like that one.
- a push mode in `demo/lcd_run.py` (0087 item 2) - `aexec()` the generated script, `areset()`, then
  collect frames as today.

### Unchanged by all of this

The screenshots, the `boot.py` -> `boot_out.txt` finding, finding 5's `fs_start` fix, and
`demo/wifi_lcd_run.py`. Only how a CIRCUITPY volume gets populated is in question, and only for the
cases 8.3 names cannot cover.



## Appendix: the demo-half rework, as built (2026-08-20, same day)

The section above planned it as an *opt-in* mode ("default stays where the fast path is"). It
shipped as the **only** mode, and the host-side builder is gone - `demo/mkfat12.py`, its `pyfatfs`
route and `tests/test_demo_mkfat12.py` were deleted, and item 1's `demo/mkfat12_dump.py` was never
written (rejected in [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md): the
composition of a REPL write and `--dump-fs` already covers it). What that costs is exactly what was
priced here - a format-from-blank boot per run - and `--fat12` still loads a prepared image when
that matters.

`demo/wifi_lcd_run.py` did not stay unchanged either. It is now one self-contained file: the guest
code lives in it as a string, gets pushed as `code.py` over the REPL, and the run ends with a PNG
of the panel. `demo/cp_wifi_lcd_demo.py` is gone into it.

Three things had to be measured to make that work, none of them predictable from this record:

1. **`supervisor.reload()` through `exec()` does nothing** - see 0087's closing section. The fix is
   Ctrl-B then Ctrl-D over the console.
2. **The panel appeared to paint at a crawl - and that was the demo's own fault, not the
   route's.** Two host-side mistakes, both mine, and worth naming because either one alone looks
   exactly like "the emulator is slow":
   - `on_frame` fires on the emulator's **engine-room thread**. Decoding RGB565 into a PIL image
     inside that callback - which every display demo here did - is time the emulated chip does not
     get to run.
   - The consumer fell behind, and the stop rule then read a stale frame. Measured: the panel is
     painted in a burst of **171 frames over 7.6 s** (43 ms apart, 25 in the busiest second), while
     the loop managed ~15/s - the decode is only 7.5 ms, but `text_lines()` and the change check
     ran on every frame too. Peak backlog **3.7 s**: seconds, not minutes, but half the burst, so
     what the rule examined was mid-paint and still changing. Draining to the newest frame first is
     the fix.

   Turning `auto_refresh` back on in the guest's idle loop made it worse still - 60 fps of
   full-framebuffer SPI pushes, exactly what the constructor comment warns about - and was
   reverted. With all three fixed the REPL route takes **1 m 40 s** end to end (30 s of which is a
   deliberate "has the panel gone still" wait) against **58 s** for the old image flow, and the
   measured emulation speed is the same either way: 0.069x realtime for the REPL flow, 0.063x for
   the image one. The A/B is in the record because the wrong conclusion - "the REPL route is
   slow" - survived three rewrites of the stop rule before anyone measured the two flows against
   each other.
3. **A frame caught mid-scroll is torn**, so the guest's last status line is the IP: a sixth line
   would scroll this 5-row terminal, and the run would end on a picture that reads
   `maci test / conrok / ip ected: True`. The gateway moved to a comment.

The run therefore ends when the console has printed the last line **and** consecutive frames stop
differing - not on a frame count, which is what the first version guessed at.
