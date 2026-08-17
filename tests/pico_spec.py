"""Host-side `BoardSpec` file, for CI/manual proof that `--board-spec`/`RP2040PY_BOARD_SPEC`
(docs/records/0049-external-device-authoring-docs.md's "Accepted design" section) really boot a
real device end-to-end, driven entirely through the public `BoardSpec` API instead of the
built-in `BOARDS` registry `--board pico` reads from.

Usage: rp2040py micropython --board-spec tests/pico_spec.py:BOARD --littlefs littlefs.img ...
(or set RP2040PY_BOARD_SPEC=tests/pico_spec.py:BOARD instead of the flag - same target:attr
syntax either way). Which MicroPython version to boot is picked here, via
`RP2040PY_MICROPYTHON_TAG` - this file's own convention, not part of the core API; a custom board
file being an ordinary Python module, free to read whatever it wants to decide how to build its
`BoardSpec`, is exactly the point.

This one resolves eagerly, through the `resolve_board_spec()` board-name shortcut, which is why it
is a *host*-side spec for a board the registry already knows. A board file for hardware this
project doesn't ship declares `firmware` instead and lets the CLI resolve it at use time (see
docs/records/0059 and `boards/`) - and `--image` works alongside `--board-spec` either way now, so
the env var above is this file's convenience, not a workaround.
"""

import os

from rp2040py.boards import resolve_board_spec
from rp2040py.utils.firmware_retrieve import MICROPYTHON

BOARD = resolve_board_spec("pico", MICROPYTHON, os.environ.get("RP2040PY_MICROPYTHON_TAG"))
