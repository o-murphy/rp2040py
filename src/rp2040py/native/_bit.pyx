# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Real, fully-typed Cython port of rp2040py.utils.bit - the same public API, genuinely typed
this time (not the field-only .py+.pxd approach the main package briefly carried)."""


cpdef inline long long bit(long long n) noexcept:
    return (<long long>1) << n


cpdef inline unsigned int u32(long long n) noexcept:
    return <unsigned int>(n & 0xFFFFFFFFU)


cpdef inline int s32(long long n) noexcept:
    cdef unsigned int u = <unsigned int>(n & 0xFFFFFFFFU)
    if u & 0x80000000U:
        return <int>(u - 0x100000000)
    return <int>u


cpdef inline unsigned int urshift(long long n, long long shift) noexcept:
    return u32(n) >> (shift & 31)


cpdef inline unsigned short read_uint16_le(const unsigned char[:] buffer, Py_ssize_t offset) noexcept:
    return buffer[offset] | (buffer[offset + 1] << 8)


cpdef inline void write_uint16_le(unsigned char[:] buffer, Py_ssize_t offset, unsigned int value) noexcept:
    buffer[offset] = value & 0xFF
    buffer[offset + 1] = (value >> 8) & 0xFF


cpdef inline unsigned int read_uint32_le(const unsigned char[:] buffer, Py_ssize_t offset) noexcept:
    return (buffer[offset] | (buffer[offset + 1] << 8) | (buffer[offset + 2] << 16)
            | (<unsigned int>buffer[offset + 3] << 24))


cpdef inline void write_uint32_le(unsigned char[:] buffer, Py_ssize_t offset, unsigned long long value) noexcept:
    buffer[offset] = value & 0xFF
    buffer[offset + 1] = (value >> 8) & 0xFF
    buffer[offset + 2] = (value >> 16) & 0xFF
    buffer[offset + 3] = (value >> 24) & 0xFF
