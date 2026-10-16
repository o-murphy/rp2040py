"""The logger's words for what a C++ block says about a register access it does not implement (docs/records/0096-cpp-mcu-core.md, the clean-up before wasm).

A block tells its host `(block, kind, offset, value)` as data (core/core_host.hpp). The three kinds every block shares - `kRegWarnRead`, `kRegWarnReadAtomicArea`, `kRegWarnWrite` - are worded here, once, with
the messages of `BasePeripheral` (`peripherals/peripheral.py`), instead of by a `_warn_kind` method in every shell. A block with kinds of its own (the XOSC's and ROSC's invalid writes, the XIP's bus
faults, the SIO's FIFO and divide failure, the PIO's state-machine errors) keeps a `_warn_kind(kind, offset, value)` on its shell for those and leaves the shared ones to `register_warning`.
"""

READ, READ_ATOMIC_AREA, WRITE = 0, 1, 2  # core/core_host.hpp: kRegWarnRead, kRegWarnReadAtomicArea, kRegWarnWrite


def register_warning(kind, offset, value):
    """The message of a shared kind, or None for a kind the block defines itself."""
    if kind == READ:
        return f"Unimplemented peripheral read from 0x{offset:x}"
    if kind == READ_ATOMIC_AREA:
        return "Unimplemented read from peripheral in the atomic operation region"
    if kind == WRITE:
        return f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}"
    return None


def warn_register(view, kind, offset, value):
    """Word a block's report for the logger through its Python view: the view's own `_warn_kind` when it has one (it handles its own kinds and calls `register_warning` for the rest), the shared words otherwise."""
    special = getattr(view, "_warn_kind", None)
    if special is not None:
        special(kind, offset, value)
        return
    text = register_warning(kind, offset, value)
    warn = getattr(view, "warn", None)
    if text is not None and warn is not None:
        warn(text)
