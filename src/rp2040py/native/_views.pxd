# Python views of C++ state shared by several shells: see `_views.pyx`.

from libc.stdint cimport uint32_t

from rp2040py.native._pwm cimport Timer32, Timer32PeriodicAlarm

cdef class Timer32View:
    cdef object _owner  # keeps the block's shell (and so the chip) alive
    cdef Timer32* _timer


cdef class Timer32AlarmView:
    cdef object _owner  # keeps the block's shell (and so the chip) alive
    cdef Timer32PeriodicAlarm* _alarm


cdef class WordsView:
    cdef uint32_t* _words
    cdef int _n
