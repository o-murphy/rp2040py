"""CircuitPython `code.py` for demo/wifi_lcd_run.py: joins the emulated CYW43439's network and
prints the result onto an ST7735S panel wired to a Pico W.

This is the demo that answers "would a WiFi connection show up in one of the LCD screenshots?" -
which demo/cp_lcd_demo.py can only answer negatively, because the Waveshare RP2040-LCD-0.96 has
no radio and its CircuitPython build ships no `wifi` module at all. No RP2040 board exists with
both an onboard CYW43439 and an onboard display CircuitPython auto-initialises, so this one wires
the panel to a Pico W instead: the radio comes from the board, the display from this file.

Two consequences of the panel not being an onboard one, both deliberate:

- **Nothing is on screen until this file runs.** CircuitPython's console is mirrored to the
  terminal of whatever display exists (`supervisor/shared/serial.c`'s `serial_write_substring()`
  writes to `supervisor_terminal` before the console), and on a board whose `board_init()` builds
  no display there is none until the `BusDisplay` below is constructed. So the boot banner and
  anything `boot.py` printed are already gone by the time the panel lights up - the screenshots
  start at the first `print()` after that constructor.
- **`auto_refresh=False`.** An auto-refreshing display repaints at 60 fps, and every one of those
  frames is real SPI traffic the emulator has to execute *interleaved with* the CYW43's own PIO/
  gSPI hot path - which is already the slowest thing in this emulator (docs/records/0047).
  Refreshing explicitly, once per status line, is what keeps this demo minutes rather than tens
  of minutes: the panel is a status display here, not an animation.

The pins are the ones the Waveshare board uses for the same panel (SPI1 CLK=GP10, MOSI=GP11,
DC=GP8, CS=GP9, RST=GP12), because `rp2040py.external.st7735s.St7735s` defaults to that wiring -
none of them collide with the CYW43439's own GP23/24/25/29. The backlight does not carry over:
GP25 is the radio's chip-select on a Pico W, so `backlight_pin` stays unset here.
"""

import time  # type: ignore[import-not-found]

import board  # type: ignore[import-not-found]
import busdisplay  # type: ignore[import-not-found]
import busio  # type: ignore[import-not-found]
import displayio  # type: ignore[import-not-found]
import fourwire  # type: ignore[import-not-found]
import wifi  # type: ignore[import-not-found]

# Byte-for-byte CircuitPython's own `display_init_sequence` for this panel
# (ports/raspberrypi/boards/waveshare_rp2040_lcd_0_96/board.c, itself taken from
# Adafruit_CircuitPython_ST7735R), in the packed format `BusDisplay` documents: command byte,
# then a parameter count whose 0x80 bit means "a delay byte follows the parameters".
# Transcribed rather than invented - the same 3g rule the board files follow.
_ST7735S_INIT = (
    b"\x01\x80\x96"  # SWRESET, 150 ms
    b"\x11\x80\xff"  # SLPOUT, 500 ms (0xff means "extra long" to BusDisplay)
    b"\xb1\x03\x01\x2c\x2d"  # FRMCTR1
    b"\xb3\x06\x01\x2c\x2d\x01\x2c\x2d"  # FRMCTR3
    b"\xb4\x01\x07"  # INVCTR, line inversion
    b"\xc0\x03\xa2\x02\x84"  # PWCTR1
    b"\xc1\x01\xc5"  # PWCTR2
    b"\xc2\x02\x0a\x00"  # PWCTR3
    b"\xc3\x02\x8a\x2a"  # PWCTR4
    b"\xc4\x02\x8a\xee"  # PWCTR5
    b"\xc5\x01\x0e"  # VMCTR1
    b"\x20\x00"  # INVOFF
    b"\x36\x01\x18"  # MADCTL, bottom-to-top refresh
    b"\x3a\x01\x05"  # COLMOD, 16-bit colour
    b"\xe0\x10\x02\x1c\x07\x12\x37\x32\x29\x2d\x29\x25\x2b\x39\x00\x01\x03\x10"  # GMCTRP1
    b"\xe1\x10\x03\x1d\x07\x06\x2e\x2c\x29\x2d\x2e\x2e\x37\x3f\x00\x00\x02\x10"  # GMCTRN1
    b"\x13\x80\x0a"  # NORON, 10 ms
    b"\x29\x80\x64"  # DISPON, 100 ms
    b"\x36\x01\xc8"  # MADCTL again: default rotation, RGB encoding
    b"\x21\x00"  # INVON
)

displayio.release_displays()
spi = busio.SPI(clock=board.GP10, MOSI=board.GP11)
display_bus = fourwire.FourWire(spi, command=board.GP8, chip_select=board.GP9, reset=board.GP12, baudrate=40_000_000)
display = busdisplay.BusDisplay(
    display_bus,
    _ST7735S_INIT,
    width=160,
    height=80,
    colstart=26,
    rowstart=1,
    rotation=90,
    auto_refresh=False,  # see the module docstring
)


def show(line):
    """Print one status line and push the panel once - the manual half of `auto_refresh=False`."""
    print(line)
    display.refresh()


# The panel is ~26x9 characters, so every line below is written to fit one row.
show("wifi test")
wifi.radio.enabled = True
show("mac ok")

# "RP2040PY-GUEST" is the fixed SSID the emulated chip answers scans with, and the passphrase's
# only requirement is WPA2's own 8-64 character rule - CircuitPython checks the length client-side
# before anything reaches the radio, while the emulator scripts the join unconditionally. Same
# call, and the same reasoning, as tests/circuitpython/main-cyw43.py.
wifi.radio.connect("RP2040PY-GUEST", "password")
show(f"connected: {wifi.radio.connected}")
show(f"ip {wifi.radio.ipv4_address}")
show(f"gw {wifi.radio.ipv4_gateway}")

# Idle rather than return: CircuitPython prints "Code done running." and its "Press any key to
# enter the REPL" message the moment code.py finishes, which on a 9-row terminal scrolls the
# lines above off the panel - i.e. off the screenshot this demo exists to produce.
while True:
    time.sleep(1)
