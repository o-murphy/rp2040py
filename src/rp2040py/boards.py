"""Board registry: maps a `--board` name to which MCU class to construct and which fixed
`ExternalDevice`s (see `external/device.py`) to attach afterwards.

Composition, not inheritance (see docs/CYW43_WIFI_BACKLOG.md's "Board composition decision"):
most third-party RP2040 boards are the identical die, differing only in pin breakout or one
onboard extra - not in chip behavior - so a subclass-per-board would cross that axis with the
MCU-variant axis (RP2040 vs. RP2350, ...) combinatorially for what's mostly metadata. `RP2040`
itself stays completely unchanged by this module.

A `BoardSpec` also says *how to get* its firmware, not just which devices to attach: `firmware`
maps a family name (`"micropython"`/`"circuitpython"`/`"kaluma"`) to a `BoardFirmwareSpec`, and
`resolve_firmware()` turns that into a concrete `image`/`layout` at use time. Built-in boards and
custom `--board-spec` board files share that one field and that one function (docs/records/0059) -
which is why a board file declares data instead of downloading anything when it is imported, and
why one file can serve several firmware families for the same piece of hardware.

`--board`'s fixed `choices` (see `cli/__init__.py`) are a convenience layer, never the ceiling: a
library caller who wants a custom combination of `ExternalDevice`s that doesn't match any built-in
board can just construct `RP2040()` and call `attach_external_devices()` directly, with no
dependency on this registry at all.
"""

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rp2040py.clock.clock import IClock
from rp2040py.external import ExternalDevice, attach_external_devices
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.cyw43 import Cyw43439
from rp2040py.external.led_mock import LEDMock
from rp2040py.rp2040 import RP2040
from rp2040py.utils.firmware_retrieve import SPECS, BoardFirmwareSpec, FirmwareSpec
from rp2040py.utils.firmware_retrieve import board_flash_layout as _board_flash_layout
from rp2040py.utils.firmware_retrieve import family_of as _family_of
from rp2040py.utils.firmware_retrieve import retrieve as _retrieve

__all__ = (
    "BOARDS",
    "BoardSpec",
    "FlashLayout",
    "UnknownBoardError",
    "UnknownFirmwareFamilyError",
    "build_rp2040",
    "build_rp2040_from_spec",
    "resolve_board_spec",
    "resolve_firmware",
    "resolve_layout",
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
    layout: "FlashLayout | None" = None  # resolved, or an explicit override. None where no
    # filesystem concept applies at all (raw `run`), or simply not resolved yet.
    image: "str | Path | None" = None  # an already-resolved local file path ONLY - never a version
    # tag, never a URL. `retrieve()`'s tag/URL/cache resolution stays entirely a CLI/SDK-side
    # concern that happens *before* a `BoardSpec` carries an `image`, not something a `Device`
    # class ever has to replicate. A board *author* never sets this: it is the resolved-value slot
    # (and `--image <path>`'s override), while `firmware` below is the authoring surface.
    firmware: "dict[str, BoardFirmwareSpec] | None" = None  # how to resolve the two above, keyed
    # by firmware family (`"micropython"`/`"circuitpython"`/`"kaluma"` - `firmware_specs.json`'s
    # own top-level names). Declarative data, deliberately not a `.retrieve()` method: a frozen
    # field is inspectable, comparable and testable without running anything, where a method
    # invites exactly the import-time side effects this replaced. See docs/records/0059, and
    # `resolve_firmware()` below for the resolution order.


class UnknownBoardError(ValueError):
    def __init__(self, board: str) -> None:
        super().__init__(f"Unknown board {board!r} - choices are {sorted(BOARDS)}")


class UnknownFirmwareFamilyError(ValueError):
    """A `BoardSpec` was asked to resolve a firmware family it doesn't declare - raised naming what
    it *does* declare, rather than silently falling back to whatever single entry it happens to
    have: the family selects the flash loader and the console behavior as well as the image
    (`--circuitpython` picks FAT12 and a different post-boot handshake), so a `--circuitpython` run
    quietly booting a lone MicroPython declaration would be a trap (docs/records/0059)."""

    def __init__(self, family: str, declared: "Iterable[str]", image: "str | None" = None) -> None:
        wanted = f"the firmware tag {image!r}" if image is not None else "a firmware image"
        declared = sorted(declared)
        if declared:
            super().__init__(
                f"This BoardSpec declares no {family!r} firmware, so there is nothing to resolve "
                f"{wanted} against - it declares {declared}"
            )
        else:
            super().__init__(
                f"This BoardSpec declares no `firmware` at all, so there is nothing to resolve "
                f"{wanted} against for {family!r}"
            )


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
def _builtin_firmware(board: str) -> "dict[str, BoardFirmwareSpec]":
    """Every family `firmware_specs.json` declares `board` under, keyed by that file's own
    top-level names - what puts a built-in board on the exact same `firmware` field, and through
    the exact same `resolve_firmware()`, a `--board-spec` board file uses (docs/records/0059).
    The one thing that stays different is who maintains the data: this comes from
    `scripts/fetch_firmware.py`, a board file's comes from its author."""
    return {family: spec.boards[board] for family, spec in SPECS.items() if spec.boards and board in spec.boards}


BOARDS: dict[str, BoardSpec] = {
    "pico": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton), firmware=_builtin_firmware("pico")),
    "pico_w": BoardSpec(
        extras=(lambda: LEDMock(gpio=25), BootselButton, Cyw43439), firmware=_builtin_firmware("pico_w")
    ),
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


def _layout_of(board_firmware: "BoardFirmwareSpec | None") -> "FlashLayout | None":
    if board_firmware is None:
        return None
    layout = _board_flash_layout(board_firmware)
    return FlashLayout(**layout) if layout else None


def resolve_layout(spec: BoardSpec, family: str) -> "FlashLayout | None":
    """`spec`'s flash layout for `family`: an explicit `spec.layout` override first, else
    `spec.firmware[family].layout`. The half of `resolve_firmware()` that needs no image at all,
    which is what lets `mklittlefs --board-spec` size an image without touching the network
    (docs/records/0059). `None` when the spec declares no `firmware` and carries no `layout`;
    raises `UnknownFirmwareFamilyError` when it declares firmware but not for `family`."""
    if spec.layout is not None:
        return spec.layout
    declared = spec.firmware or {}
    if family not in declared:
        if not declared:
            return None
        raise UnknownFirmwareFamilyError(family, declared)
    return _layout_of(declared[family])


def _is_direct_image(image: "str | None") -> bool:
    """True for an `--image` value `retrieve()` uses *as given* - an existing local file path, or a
    direct URL - as opposed to a version tag, which needs a `BoardFirmwareSpec` to resolve
    against."""
    return image is not None and (image.startswith(("http://", "https://")) or Path(image).exists())


def resolve_firmware(spec: BoardSpec, family: str, image: "str | None" = None) -> BoardSpec:
    """Resolves `spec` into one carrying a concrete `image`/`layout` for `family`, at **use** time
    (a CLI invocation, an SDK call) rather than at the moment a board file is imported - the whole
    point of docs/records/0059. The one resolution path: `--board`'s registry entries and a
    `--board-spec` board file both come through here.

    `image` is `--image`'s own value - a version tag, a local file path, or a direct URL, exactly
    as `retrieve()` already accepts. Resolution order, in full:

    1. an explicit `image` argument wins outright - a local path/URL is used as given, with no
       family declaration consulted at all; a *tag* is resolved against `spec.firmware[family]`;
    2. otherwise an `image` the spec already carries (someone resolved it, or hand-pinned it) wins;
    3. otherwise `spec.firmware[family]` resolves through `retrieve()` at that entry's own
       `default_tag` - a URL through the usual `~/.cache/rp2040py` download-and-cache, a local path
       used as-is with no copy;
    4. `layout` follows the same order: an explicit `spec.layout` override, else
       `spec.firmware[family].layout`;
    5. neither an image nor any `firmware` at all: the spec is returned unresolved, and the
       caller's own "no image" failure (`BaseDevice`'s assert, the CLI's own error) stands.

    Raises `UnknownFirmwareFamilyError` when the spec declares firmware, but not for `family`."""
    declared = spec.firmware or {}
    board_firmware = declared.get(family)
    layout = spec.layout if spec.layout is not None else _layout_of(board_firmware)

    if image is None and spec.image is not None:
        return dataclasses.replace(spec, layout=layout)

    if _is_direct_image(image):
        # FirmwareSpec(): nothing to look a tag up in, and nothing needs looking up - retrieve()
        # coerces a local path/URL on its own, which is exactly what makes a board file able to
        # declare a local `.uf2` path and be fully offline by construction.
        return dataclasses.replace(spec, image=_retrieve(FirmwareSpec(), image), layout=layout)

    if board_firmware is None:
        if image is None and not declared:
            return spec
        raise UnknownFirmwareFamilyError(family, declared, image)

    resolved = _retrieve(FirmwareSpec(boards={family: board_firmware}), image, family)
    return dataclasses.replace(spec, image=resolved, layout=layout)


def _as_board_firmware(firmware_spec: FirmwareSpec, board: str) -> "BoardFirmwareSpec | None":
    """Adapts a whole-family `FirmwareSpec` into the one `BoardFirmwareSpec` `BoardSpec.firmware`
    holds for `board`. A `known_versions`-shaped spec (BOOTROM, and any caller-built equivalent)
    has no per-board data to pick from, so its board-agnostic `default_tag`/`known_versions` become
    that one declaration directly - identical for resolution purposes, and it keeps
    `resolve_board_spec()` a single path rather than a fork per spec shape."""
    if firmware_spec.boards is not None:
        return firmware_spec.boards.get(board)
    if firmware_spec.default_tag is None or firmware_spec.known_versions is None:
        return None
    return BoardFirmwareSpec(default_tag=firmware_spec.default_tag, fw=firmware_spec.known_versions)


def resolve_board_spec(board: str, firmware_spec: FirmwareSpec, tag: "str | None" = None) -> BoardSpec:
    """The one shared shortcut both `--board` (CLI) and an SDK caller use for a *known* board -
    `BOARDS[board]`'s `mcu`/`extras` plus that firmware family's resolved image/layout, combined
    into one ready-to-run `BoardSpec`. `tag=None` defers to the board's own `default_tag`, same as
    `retrieve()` already does. Raises `UnknownBoardError` for a name not in `BOARDS` (checked
    before anything is resolved, so this function always fails the same way `build_rp2040()` does).

    A thin wrapper over `resolve_firmware()` since docs/records/0059: it only has to say *which*
    declaration to resolve. A `firmware_spec` this project ships resolves under its own family name
    (`family_of()`); a caller-built one overrides that family's entry for this call only, so a
    custom `FirmwareSpec` keeps working exactly as it did before the `firmware` field existed."""
    if board not in BOARDS:
        raise UnknownBoardError(board)
    # A caller-built FirmwareSpec has no family name of its own; the key is only ever read back by
    # the resolve_firmware() call on the next line, so any stable placeholder does.
    family = _family_of(firmware_spec) or "firmware"
    firmware = dict(BOARDS[board].firmware or {})
    board_firmware = _as_board_firmware(firmware_spec, board)
    if board_firmware is not None:
        firmware[family] = board_firmware
    return resolve_firmware(dataclasses.replace(BOARDS[board], firmware=firmware or None), family, tag)
