# cython: language_level=3, boundscheck=False, wraparound=False, freethreading_compatible=True
"""See _pending.pxd. One module-level slot: the interpreter is single-threaded for the emulation by contract, and a
parked error is taken within the same call that parked it, so there is never more than one waiting."""

cdef object _error = None


cdef void park_error(object error) noexcept:
    global _error
    _error = error


cdef bint has_pending_error() noexcept:
    return _error is not None


cdef int raise_if_pending() except -1:
    """Re-raises the parked error, if any (and clears the slot first, so a handler that catches it starts clean)."""
    global _error
    cdef object error = _error
    if error is not None:
        _error = None
        raise error
    return 0
