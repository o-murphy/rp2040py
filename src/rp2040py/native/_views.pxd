# The Python view of a C++ `Timer32` shared by several shells: see `_views.pyx`.

from rp2040py.native._pwm cimport Timer32

cdef class Timer32View:
    cdef object _owner  # keeps the block's shell (and so the chip) alive
    cdef Timer32* _timer
