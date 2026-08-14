"""Public facade: prefers the native Cython port in `rp2040py.native._gpio_pin` (compiled at build
time when Cython and a C compiler are available - see `setup.py`) when it's importable, falling
back to the plain-Python reference implementation in `_gpio_pin.py` otherwise. Every caller imports
`GPIOPin` from here (or from `rp2040py`, which re-exports it) - never from `_gpio_pin.py` or
`rp2040py.native` directly. Mirrors `cortex_m0_core.py`/`peripherals/state_machine.py`.

`GPIOPinState`/`FUNCTION_*`/`GPIOPinListener` are value/type definitions with no compiled variant -
always sourced from `_gpio_pin.py` so there's a single definition every module shares (the native
`GPIOPin` returns those same `GPIOPinState` members). Only the `GPIOPin` class has a native port.
"""

from rp2040py._gpio_pin import (
    FUNCTION_PIO0,
    FUNCTION_PIO1,
    FUNCTION_PWM,
    FUNCTION_SIO,
    GPIOPinListener,
    GPIOPinState,
)
from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._gpio_pin import GPIOPin
except ImportError:
    from rp2040py._gpio_pin import GPIOPin

__all__ = (
    "FUNCTION_PIO0",
    "FUNCTION_PIO1",
    "FUNCTION_PWM",
    "FUNCTION_SIO",
    "GPIOPin",
    "GPIOPinListener",
    "GPIOPinState",
)
