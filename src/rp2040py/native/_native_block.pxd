# The common base of the Cython shells over the C++ register blocks (docs/records/0096-cpp-mcu-core.md, Phase 5b): see `_native_block.pyx`.

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "register_ops.hpp" namespace "rp2040core":

    ctypedef uint32_t (*OpsReadFn)(void* block, uint32_t offset)
    ctypedef void (*OpsWriteFn)(void* block, uint32_t offset, int64_t value)
    ctypedef void (*OpsWriteAtomicFn)(void* block, uint32_t offset, int64_t raw, uint32_t atomic_type)
    ctypedef int64_t (*OpsRawFn)(void* block)
    ctypedef void (*OpsResetFn)(void* block)
    ctypedef WindowHandler (*OpsWindowFn)(void* block)

    cdef cppclass RegisterOps:
        RegisterOps() noexcept
        void* block
        OpsReadFn read
        OpsWriteFn write
        OpsWriteAtomicFn write_atomic
        OpsRawFn raw_write_value
        OpsResetFn reset
        OpsWindowFn window

    RegisterOps register_ops[Block](Block* block) noexcept

cdef class NativeBlock:
    cdef RegisterOps _ops  # filled by the shell: `self._ops = register_ops(self._block)`
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef public object rp2040
    cdef public object name
