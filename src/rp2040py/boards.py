"""Board registry: maps a `--board` name to which MCU class to construct and which fixed
`ExternalDevice`s (see `external/device.py`) to attach afterwards.

Composition, not inheritance (see docs/CYW43_WIFI_BACKLOG.md's "Board composition decision"):
most third-party RP2040 boards are the identical die, differing only in pin breakout or one
onboard extra - not in chip behavior - so a subclass-per-board would cross that axis with the
MCU-variant axis (RP2040 vs. RP2350, ...) combinatorially for what's mostly metadata. `RP2040`
itself stays completely unchanged by this module.

`--board`'s fixed `choices` (see `cli/__init__.py`) are a convenience layer, never the ceiling: a
library caller who wants a custom combination of `ExternalDevice`s that doesn't match any built-in
board can just construct `RP2040()` and call `attach_external_devices()` directly, with no
dependency on this registry at all.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from rp2040py.clock.clock import IClock
from rp2040py.external.device import ExternalDevice, attach_external_devices
from rp2040py.external.led_mock import LEDMock
from rp2040py.rp2040 import RP2040

__all__ = ("BOARDS", "BoardSpec", "UnknownBoardError", "build_rp2040")

# Each entry is a zero-arg factory, not a shared instance: BOARDS is built once at import time,
# and a board may be constructed more than once in the same process (e.g. two independent
# Simulators bridged over a virtual serial cable) - sharing one ExternalDevice instance's mutable
# state (GPIO listeners, etc.) across two unrelated RP2040s would be wrong, not just wasteful.
ExternalDeviceFactory = Callable[[], ExternalDevice]


@dataclass(frozen=True)
class BoardSpec:
    mcu: "type[RP2040]" = RP2040
    extras: tuple[ExternalDeviceFactory, ...] = field(default_factory=tuple)


class UnknownBoardError(ValueError):
    def __init__(self, board: str) -> None:
        super().__init__(f"Unknown board {board!r} - choices are {sorted(BOARDS)}")


# LEDMock(gpio=25): accurate for "pico" - the onboard LED is genuinely wired straight to GPIO25 on
# a plain Pico. NOT accurate for "pico_w" - its onboard LED is wired to the CYW43439 chip itself,
# not any RP2040 GPIO (see docs/CYW43_WIFI_BACKLOG.md's "Onboard LED and pin differences vs. plain
# Pico") - attached here anyway, explicitly, purely to exercise the ExternalDevice/
# attach_external_devices() plumbing identically regardless of --board (step 1 of that doc's
# "Implementation order") until Cyw43439 (external/cyw43/chip.py, step 3) grows its own LED
# handling and supersedes this entry for "pico_w" specifically. "pico_w" has no CYW43439-specific
# extras yet.
BOARDS: dict[str, BoardSpec] = {
    "pico": BoardSpec(extras=(lambda: LEDMock(gpio=25),)),
    "pico_w": BoardSpec(extras=(lambda: LEDMock(gpio=25),)),
}


def build_rp2040(board: str, clock: "IClock | None" = None) -> RP2040:
    """Constructs the MCU for `board` and attaches its fixed extras. Raises `UnknownBoardError`
    for a name not in `BOARDS`."""
    try:
        spec = BOARDS[board]
    except KeyError:
        raise UnknownBoardError(board) from None

    mcu = spec.mcu(clock)
    attach_external_devices(mcu, *(factory() for factory in spec.extras))
    return mcu
