# Declaration file paired with _gpio_pin.pyx. See that file's module docstring for the port
# rationale.
#
# native/_pio.pyx cimports GPIOPin from here so RPPIO.check_changed_pins() can call
# check_for_updates() as a direct C call (once per changed pin, millions of times during a CYW43
# boot) instead of an object-typed method dispatch. This forms a mutual cimport with _pio.pxd
# (_gpio_pin.pyx cimports RPPIO for its pin_values/pin_directions reads); the paired .pxd files are
# what let Cython resolve the cycle.

cdef class GPIOPin:
    cdef public object rp2040
    cdef public unsigned int index
    cdef public str name
    cdef public bint _always_output_enabled
    cdef public bint _raw_input_value
    cdef public bint _driven
    cdef public unsigned int ctrl
    cdef public unsigned int pad_value
    cdef public unsigned int irq_enable_mask
    cdef public unsigned int irq_force_mask
    cdef public unsigned int irq_status
    # The last emitted pin state, stored as its GPIOPinState *integer* value (0-5) rather than the
    # boxed enum: check_for_updates() compares it as a plain C int per call and only materialises a
    # GPIOPinState object on an actual change (for the listeners).
    cdef int _last_state
    cdef public object _listeners

    cdef bint _raw_output_enable(self, unsigned int fsel)
    cdef bint _raw_output_value(self, unsigned int fsel)
    cdef bint _eff_raw_input(self)
    cdef int _state_code(self)
    cdef object _value(self)
    cpdef check_for_updates(self)
