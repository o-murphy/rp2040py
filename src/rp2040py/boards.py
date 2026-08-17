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

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rp2040py.clock.clock import IClock
from rp2040py.external import ExternalDevice, attach_external_devices
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.cyw43 import Cyw43439
from rp2040py.external.led_mock import LEDMock
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import FirmwareSpec
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout
from rp2040py.utils.firmware_retrieve import retrieve as _retrieve

__all__ = (
    "BOARDS",
    "BoardSpec",
    "FlashLayout",
    "UnknownBoardError",
    "build_rp2040",
    "build_rp2040_from_spec",
    "resolve_board_spec",
)

# Each entry is a zero-arg factory, not a shared instance: BOARDS is built once at import time,
# and a board may be constructed more than once in the same process (e.g. two independent
# Simulators bridged over a virtual serial cable) - sharing one ExternalDevice instance's mutable
# state (GPIO listeners, etc.) across two unrelated RP2040s would be wrong, not just wasteful.
ExternalDeviceFactory = Callable[[], ExternalDevice]


@dataclass(frozen=True)
class FlashLayout:
    """Where a board's real firmware places its flash filesystem (and, for Kaluma, "user program")
    region - the resolved shape of `BoardFirmwareSpec.layout`
    (`rp2040py.utils.firmware_retrieve`), or a hand-supplied equivalent for a board with no
    `firmware_specs.json` entry at all. See docs/records/0049's "Design update" section."""

    fs_start: int
    fs_blockcount: int
    fs_blocksize: int
    prog_start: "int | None" = None  # Kaluma's separate YMODEM "user program" region only -
    # MicroPython/CircuitPython keep user code inside the FS itself.


@dataclass(frozen=True)
class BoardSpec:
    mcu: "type[RP2040]" = RP2040
    extras: tuple[ExternalDeviceFactory, ...] = field(default_factory=tuple)
    layout: "FlashLayout | None" = None  # None where no filesystem concept applies (raw `run`)
    image: "str | Path | None" = None  # an already-resolved local file path ONLY - never a version
    # tag, never a URL. `retrieve()`'s tag/URL/cache resolution stays entirely a CLI/SDK-side
    # concern that happens *before* a `BoardSpec` carries an `image`, not something a `Device`
    # class or a custom board author's file ever has to replicate.


class UnknownBoardError(ValueError):
    def __init__(self, board: str) -> None:
        super().__init__(f"Unknown board {board!r} - choices are {sorted(BOARDS)}")


# LEDMock(gpio=25): accurate for "pico" - the onboard LED is genuinely wired straight to GPIO25 on
# a plain Pico. NOT accurate for "pico_w" - its onboard LED is wired to the CYW43439 chip itself,
# not any RP2040 GPIO (see docs/CYW43_WIFI_BACKLOG.md's "Onboard LED and pin differences vs. plain
# Pico") - attached here anyway, explicitly, purely to exercise the ExternalDevice/
# attach_external_devices() plumbing identically regardless of --board (step 1 of that doc's
# "Implementation order") until Cyw43439 (external/cyw43/chip.py, step 3) grows its own LED
# handling and supersedes this entry for "pico_w" specifically.
# BootselButton is on both boards for the opposite reason to LEDMock's caveat above: it is wired
# identically on every RP2040 board that boots from QSPI flash (to GPIO_QSPI_SS, not to any
# ordinary GPIO), so there is nothing board-specific to get wrong. See 0050.
BOARDS: dict[str, BoardSpec] = {
    "pico": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton)),
    "pico_w": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton, Cyw43439)),
}


def build_rp2040(board: str, clock: "IClock | None" = None) -> RP2040:
    """Constructs the MCU for `board` and attaches its fixed extras. Raises `UnknownBoardError`
    for a name not in `BOARDS`."""
    try:
        spec = BOARDS[board]
    except KeyError:
        raise UnknownBoardError(board) from None
    return build_rp2040_from_spec(spec, clock)


def build_rp2040_from_spec(spec: BoardSpec, clock: "IClock | None" = None) -> RP2040:
    """Constructs the MCU described by an already-resolved `BoardSpec` (`spec.mcu`/`spec.extras`
    only - `spec.layout`/`spec.image` are a `Device` class's concern, not this function's) and
    attaches its extras. The shared chokepoint both `build_rp2040()` (a board-name lookup) and
    `BaseDevice` (which already has a resolved `BoardSpec` handed to it) funnel through."""
    mcu = spec.mcu(clock)
    attach_external_devices(mcu, *(factory() for factory in spec.extras))
    return mcu


def resolve_board_spec(board: str, firmware_spec: FirmwareSpec, tag: "str | None" = None) -> BoardSpec:
    """The one shared shortcut both `--board` (CLI) and an SDK caller use for a *known* board -
    `BOARDS[board]`'s `mcu`/`extras` plus that firmware family's resolved image/layout, combined
    into one ready-to-run `BoardSpec`. `tag=None` defers to the board's own `default_tag`, same as
    `retrieve()` already does. Raises `UnknownBoardError` for a name not in `BOARDS` (checked
    before `retrieve()`/`flash_layout()`, which raise/return differently for an unknown board -
    this function always fails the same way `build_rp2040()` does)."""
    if board not in BOARDS:
        raise UnknownBoardError(board)
    image = _retrieve(firmware_spec, tag, board)
    layout = _flash_layout(firmware_spec, board) if firmware_spec.boards is not None else None
    return dataclasses.replace(BOARDS[board], layout=FlashLayout(**layout) if layout else None, image=image)
