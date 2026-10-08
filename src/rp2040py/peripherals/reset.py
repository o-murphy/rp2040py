"""Public facade for RESETS: prefers the native block in `rp2040py.native._reset` (a Cython shell over `native/core/resets.hpp`) when it is importable, falling back to the plain-Python reference in
`_reset.py` otherwise. Every caller imports `RPReset` from here - never from `_reset.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/tbman.py`; held to identical behaviour by `tests/test_psm_xosc_resets_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._reset import (
    RESET,
    RESET_ADC,
    RESET_BUSCTRL,
    RESET_DMA,
    RESET_DONE,
    RESET_I2C0,
    RESET_I2C1,
    RESET_IO_BANK0,
    RESET_IO_QSPI,
    RESET_PADS_BANK0,
    RESET_PADS_QSPI,
    RESET_PIO0,
    RESET_PIO1,
    RESET_PWM,
    RESET_RTC,
    RESET_SPI0,
    RESET_SPI1,
    RESET_SYSCFG,
    RESET_TIMER,
    RESET_UART0,
    RESET_UART1,
    RESET_USBCTRL,
    RESETS_BITS_MASK,
    WDSEL,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._reset import RPReset
except ImportError:
    from rp2040py.peripherals._reset import RPReset

__all__ = (
    "RESET",
    "RESETS_BITS_MASK",
    "RESET_ADC",
    "RESET_BUSCTRL",
    "RESET_DMA",
    "RESET_DONE",
    "RESET_I2C0",
    "RESET_I2C1",
    "RESET_IO_BANK0",
    "RESET_IO_QSPI",
    "RESET_PADS_BANK0",
    "RESET_PADS_QSPI",
    "RESET_PIO0",
    "RESET_PIO1",
    "RESET_PWM",
    "RESET_RTC",
    "RESET_SPI0",
    "RESET_SPI1",
    "RESET_SYSCFG",
    "RESET_TIMER",
    "RESET_UART0",
    "RESET_UART1",
    "RESET_USBCTRL",
    "WDSEL",
    "RPReset",
)
