# The interrupt-line adapters of core/mcu.hpp (docs/records/0096-cpp-mcu-core.md, step 2b), for the shells whose C++ block raises a line straight on the chip.
#
# Kept apart from _mcu.pxd on purpose: that one declares the whole `Mcu` and so cimports the PIO block, and the PIO shell cimports these adapters - one file for both would be a cycle.
# Here `Mcu` is opaque (the adapters hold a pointer to it and nothing else), and a module that needs the real thing cimports it from _mcu.pxd.

from libc.stdint cimport uint32_t

from libcpp cimport bool

from rp2040py.native._pin cimport PinBank

cdef extern from "mcu.hpp" namespace "rp2040core":
    cdef cppclass Mcu:
        pass

    cdef cppclass McuIrqLine:
        McuIrqLine() noexcept
        Mcu* mcu
        uint32_t line

    cdef cppclass McuIrqLines:
        McuIrqLines() noexcept
        Mcu* mcu
        uint32_t base

    cdef cppclass McuDreqPair:
        McuDreqPair() noexcept
        Mcu* mcu
        uint32_t if_true
        uint32_t if_false

    cdef cppclass McuIoIrq:
        McuIoIrq() noexcept
        Mcu* mcu
        PinBank* bank
        uint32_t line

    bool mcu_dreq_pair(void* ctx, bool which, bool asserted) noexcept
    bool mcu_io_interrupt(void* ctx) noexcept
    bool mcu_irq_line(void* ctx, bool level) noexcept
    bool mcu_irq_lines(void* ctx, uint32_t line, bool level) noexcept


cdef inline Mcu* native_mcu(object chip) except? NULL:
    """The C++ `Mcu` of `chip`, if it is a native chip (it answers `_native_mcu_address`; a stand-in or the pure-Python chip does not): what a shell binds a line to."""
    address = getattr(chip, "_native_mcu_address", None)
    if address is None:
        return NULL
    return <Mcu*> <size_t> address()
