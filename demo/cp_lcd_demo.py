"""CircuitPython `code.py` for demo/lcd_run.py's `--circuitpython --code` mode - the
CircuitPython counterpart of demo/mp_lcd_demo.py, and deliberately a much smaller program than it.

MicroPython needs a driver script because nothing initialises the panel for it. CircuitPython
does not: `board_init()` already built a `busdisplay` over SPI1 and CircuitPython's own console
(the `circuitpython_splash` group - Blinka, the status bar, and a terminal scroll area) is
rendered onto it. So this file never touches `displayio` at all - it just `print()`s, and the
panel is where that lands, which is the whole point of running it: it shows that a *guest*
program's output reaches the LCD screenshots, not only the firmware's own boot banner.

What it prints is a probe for the thing this demo was actually written to answer - whether a
CircuitPython WiFi connection would show up on those screenshots. On this board it cannot: the
Waveshare RP2040-LCD-0.96 has no CYW43439, so its CircuitPython build ships no `wifi` module at
all and the import below fails. Booting it on a board that *does* have one (rp2040py's own
`--board pico_w`) is what makes the `else` branch run - see
docs/records/0085-circuitpython-code-py-on-lcd.md.

The panel is ~26x9 characters, so every line here is written to fit one row.
"""

import time  # type: ignore[import-not-found]

import board  # type: ignore[import-not-found]

print("board:", board.board_id)

try:
    import wifi  # type: ignore[import-not-found]
except ImportError as exc:
    print("wifi: MISSING")
    print(exc)
else:
    wifi.radio.enabled = True
    print("wifi:", ":".join(f"{b:02x}" for b in wifi.radio.mac_address[-2:]))
    wifi.radio.connect("RP2040PY-GUEST", "password")
    print("ip:", wifi.radio.ipv4_address)

# Idle here instead of returning. CircuitPython prints "Code done running." and its
# "Press any key to enter the REPL" message the moment code.py finishes, and on a 9-row terminal
# those scroll everything above off the panel - i.e. a screenshot of a *finished* code.py shows
# CircuitPython's message and none of the output the screenshot was taken for.
while True:
    time.sleep(1)
