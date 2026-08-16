"""PADS_QSPI reset values (RP2040 datasheet 2.19.6.3).

`GPIOPin` constructs every pad with PADS_BANK0's reset value, which is right for the 30 ordinary
GPIOs and wrong for the six QSPI pads - and not in a cosmetic way. Only `GPIO_QSPI_SS` has its
pull-up enabled at reset; that pull-up is what holds the flash chip deselected when nothing drives
the line, and it is the whole mechanism behind reading the BOOTSEL button (the button shorts SS to
ground, so firmware reads the pad and treats low as "pressed").

With the bank0 default instead (pull-*down*, input disabled), an undriven SS reads low forever.
Firmware that merely samples it sees a permanently-pressed BOOTSEL button; firmware that *waits*
for it to go high - as CircuitPython 10.x does, from a RAM-resident routine during boot - hangs.
See docs/records/0050-qspi-pad-reset-values.md.
"""

__all__ = ("QSPI_PAD_RESET_VALUES",)

# IE | DRIVE=4mA | SCHMITT, plus the per-pad pull:
_SD = 0x52  # SD0-SD3: no pull
_SCLK = 0x56  # SCLK: pull-down
_SS = 0x5A  # SS: pull-up - the one that matters, see above

# Index order matches `RP2040.qspi`: SCLK, SS, SD0, SD1, SD2, SD3.
QSPI_PAD_RESET_VALUES = (_SCLK, _SS, _SD, _SD, _SD, _SD)
