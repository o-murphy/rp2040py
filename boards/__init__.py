"""Community/example `--board-spec` targets - not part of the installed `rp2040py` package (lives
at the repo root, outside `src/`), not part of the built-in `--board` registry either. See
docs/reference/external-devices-and-boards.md for what this is and how to load a board from here:

    PYTHONPATH=. rp2040py micropython --board-spec boards.weactstudio:BOARD ...

(`PYTHONPATH=.` makes this importable as a dotted module path, the same way `python -m`/`-c`
already put the current directory on `sys.path` for free - a plain `--board-spec
boards/weactstudio/__init__.py:BOARD` file-path form also works, without needing `PYTHONPATH` at
all, for a board that's a single file.)

**One directory per board, not per firmware family** (docs/records/0059). A `BoardSpec.firmware`
is keyed by family, so a board that exists for both MicroPython and CircuitPython is one file
declaring both - the devices, the pin map, the flash geometry and the vendor documentation are all
properties of the board, not of whichever firmware happens to be flashed. Directory names are the
firmware's own upstream board id, case-normalized (`weactstudio` for MicroPython's
`ports/rp2/boards/WEACTSTUDIO`); where two firmwares genuinely disagree on the id, pick one and
cite both in the docstring, next to the upstream files each number came from. That citation is
what makes a board file checkable rather than folklore.

Importing a board here downloads nothing: every file declares its firmware as data, and resolution
happens when something actually boots it.
"""
