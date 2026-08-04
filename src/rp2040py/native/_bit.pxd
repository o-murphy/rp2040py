# Cython declarations for rp2040py.native._bit, so other .pyx modules in this package (e.g.
# _cortex_m0_core.pyx, _rp2040.pyx) can `cimport` these as real C-level calls instead of going
# through Python attribute lookup. `inline` is only repeated on the definitions in _bit.pyx, not
# here - Cython warns if a plain declaration also says `inline`.

cpdef long long bit(long long n) noexcept
cpdef unsigned int u32(long long n) noexcept
cpdef int s32(long long n) noexcept
cpdef unsigned int urshift(long long n, long long shift) noexcept
cpdef unsigned short read_uint16_le(const unsigned char[:] buffer, Py_ssize_t offset) noexcept
cpdef void write_uint16_le(unsigned char[:] buffer, Py_ssize_t offset, unsigned int value) noexcept
cpdef unsigned int read_uint32_le(const unsigned char[:] buffer, Py_ssize_t offset) noexcept
cpdef void write_uint32_le(unsigned char[:] buffer, Py_ssize_t offset, unsigned long long value) noexcept
