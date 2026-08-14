# Declaration file paired with _pio.pyx. See that file's module docstring for the port rationale.
#
# native/_gpio_pin.pyx cimports RPPIO from here so its raw_output_enable/raw_output_value hot path
# can read `pin_values`/`pin_directions` as direct C field reads (after a checked cast) instead of
# Python attribute lookups. Only the field layout is exposed; methods stay .pyx-local (nothing
# cimports them - the native _execute_batch loop and the bus reach RPPIO through ordinary
# object-typed calls, same as the pure class).

cdef class RPPIO:
    cdef public object rp2040
    cdef public str name
    cdef public unsigned int raw_write_value
    cdef public unsigned int first_irq
    cdef public unsigned int index
    cdef public list instructions
    cdef public object dreq_rx
    cdef public object dreq_tx
    cdef public list machines
    cdef public object _run_task
    cdef public bint stopped
    cdef public unsigned int fdebug
    cdef public unsigned int tx_stall
    cdef public unsigned int rx_stall
    cdef public unsigned int input_sync_bypass
    cdef public unsigned int irq
    cdef public unsigned int pin_values
    cdef public unsigned int pin_directions
    cdef public unsigned int old_pin_values
    cdef public unsigned int old_pin_directions
    cdef public unsigned int irq0_int_enable
    cdef public unsigned int irq0_int_force
    cdef public unsigned int irq1_int_enable
    cdef public unsigned int irq1_int_force

    cdef _warn(self, msg)
    cpdef step(self)
    cpdef check_changed_pins(self)
