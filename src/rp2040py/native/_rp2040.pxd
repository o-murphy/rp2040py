# Declaration file paired with _rp2040.pyx. Lets other .pyx modules in this package (namely
# _cortex_m0_core.pyx) `cimport RP2040` and type CortexM0Core.rp2040 as the concrete class
# instead of a generic `object` - so a hot-path call like `core.rp2040.read_uint32(addr)` resolves
# to a direct C-level cpdef call instead of a Python attribute lookup + method call.
#
# RP2040.core is now typed too (a genuine mutual .pxd cimport with _cortex_m0_core.pxd, which
# cimports RP2040 the other way for the same reason) - `public` is required, not just `cdef`:
# rp2040.core is read from plain Python outside this module (Simulator.execute(),
# peripherals/ppb.py's `core = self.rp2040.core`), and a non-public cdef field has no Python-level
# attribute at all, typed or not - `public` is what generates the __get__/__set__ slot that makes
# `rp2040.core` resolve as an attribute from outside Cython in the first place.

from rp2040py.native._cortex_m0_core cimport CortexM0Core


cdef class RP2040:
    # Not `public`: a `cdef public X[:]` field's auto-generated Python getter returns Cython's
    # own typed-memoryview-slice object, which (unlike a real builtin memoryview) does not
    # support content-based `==` against bytes/bytearray - it fell back to identity comparison,
    # breaking callers like tests/test_kaluma_device.py that compare a flash slice directly
    # against a bytes literal. sram/flash/usb_dpram/bootrom are still real live views into the
    # same backing buffers (correctness unaffected) - see the `property` blocks in _rp2040.pyx,
    # which wrap them in a real memoryview() for Python-facing access.
    cdef public CortexM0Core core
    cdef unsigned char[:] _sram
    cdef unsigned char[:] _flash
    cdef unsigned char[:] _usb_dpram
    cdef unsigned int[:] _bootrom
    cdef unsigned int bootrom_byte_size
    cdef unsigned int ram_byte_size
    cdef unsigned int flash_byte_size
    cdef unsigned int dpram_byte_size
    cdef dict __dict__

    # `address`/`value` typed `long long` (not left as plain `object`, the implicit default for
    # an untyped cpdef parameter): every call site on the hot path (execute_instruction's opcode
    # fetch, every op_* load/store handler in _cortex_m0_core.pyx) passes an already-C-typed
    # `unsigned int` local or memoryview element. Against an `object` parameter, Cython has no
    # choice but to box that C value into a real PyLong before the call (and the `& 0xFFFFFFFF`
    # masking done on entry below then runs as a Python-level bigint op on the boxed value,
    # rather than a single C AND) - confirmed by reading the generated C, same class of bug the
    # module docstring's "why the first attempt underperformed" already covers for method bodies,
    # just one layer further out at this cross-module call boundary. `long long` (not `unsigned
    # int`) matches _bit.pyx's u32/s32 convention: it round-trips negative/oversized Python ints
    # the same way the removed `object` parameter did (via a plain C `&`), so out-of-range test/
    # peripheral callers keep working, whereas `unsigned int` would raise OverflowError on a
    # negative argument instead of wrapping it.
    cpdef unsigned int read_uint32(self, long long address) except? 0
    cpdef unsigned int read_uint16(self, long long address)
    cpdef unsigned int read_uint8(self, long long address)
    cpdef write_uint32(self, long long address, long long value)
    cpdef write_uint8(self, long long address, long long value)
    cpdef write_uint16(self, long long address, long long value)
