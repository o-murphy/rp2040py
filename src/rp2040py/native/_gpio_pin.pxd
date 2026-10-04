# Declaration file paired with _gpio_pin.pyx. See that file's module docstring for the port
# rationale.
#
# native/_pio.pyx cimports GPIOPin from here so RPPIO.check_changed_pins() can call
# check_for_updates() as a direct C call (once per changed pin, millions of times during a CYW43
# boot) instead of an object-typed method dispatch.
#
# The pin's own state lives in a C++ `PinBank` of one pin (core/pin.hpp); everything below the two
# public objects is that bank and a pointer to its only pin.

from rp2040py.native._pin cimport Pin, PinBank

cdef class GPIOPin:
    cdef public object rp2040
    cdef public str name
    cdef public object _listeners
    cdef PinBank _bank
    cdef Pin* _pin

    cpdef check_for_updates(self)
