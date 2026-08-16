"""Community/example `--board-spec` targets - not part of the installed `rp2040py` package (lives
at the repo root, outside `src/`), not part of the built-in `--board` registry either. See
docs/reference/external-devices-and-boards.md for what this is and how to load a board from here:

    PYTHONPATH=. rp2040py micropython --board-spec boards.micropython.WEACTSTUDIO:BOARD ...

(`PYTHONPATH=.` makes this importable as a dotted module path, the same way `python -m`/`-c`
already put the current directory on `sys.path` for free - a plain `--board-spec
boards/micropython/WEACTSTUDIO/__init__.py:BOARD` file-path form also works, without needing
`PYTHONPATH` at all, for a board that's a single file.)
"""
