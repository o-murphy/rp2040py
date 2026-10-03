# Cython view of core/batch.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4c): the simulator's batch loop. Header-only C++17.
# (`volatile` is not expressible here; the header declares the stop byte and the PIO flags volatile, which a plain pointer converts to.)

from libc.stdint cimport int64_t, uint8_t

from libcpp cimport bool

from rp2040py.native._clock cimport Clock
from rp2040py.native._cpu cimport Cpu

cdef extern from "batch.hpp" namespace "rp2040core":
    ctypedef bool (*BatchPioAdvanceFn)(void* ctx, int index, int64_t cycles)
    ctypedef double (*BatchMonotonicFn)(void* ctx)

    cdef const int kBatchFault

    cdef cppclass BatchHost:
        const uint8_t* stopped
        int pio_count
        const int* pio_stopped[4]
        BatchPioAdvanceFn pio_advance
        BatchMonotonicFn monotonic
        void* ctx

    cdef cppclass BatchParams:
        int tick_batch
        double cycle_nanos
        long instruction_ceiling
        double yield_budget_seconds
        int check_interval

    int run_batch(Cpu& cpu, Clock& clock, const BatchHost& host, const BatchParams& params) noexcept
