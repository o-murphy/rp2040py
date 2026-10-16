# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 ROSC: a Python-facing shell over the C++ `RoscBlock` of `core/rosc.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_rosc.py` is the pure-Python reference, kept as the oracle (tests/test_rosc_diff.py); `peripherals/rosc.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a register
access never enters Python. What stays Python is the edge: the logger, the chip's clock tree (`rosc_freq` and `update_clocks()`, told when DIV or FREQ_RANGE move the frequency) and the random
bit source `random_bit` (a plain attribute, the reference's hook). Their failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in. COUNT counts against the chip's
C++ `Clock`, so the block needs the native `SimulationClock`.
"""

import random

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPROSC",)


cdef cppbool _frequency_trampoline(void* ctx, double hz) noexcept:
    cdef RPROSC block = <RPROSC> ctx
    try:
        block.rp2040.rosc_freq = hz
        block.rp2040.update_clocks()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _bit_trampoline(void* ctx, uint32_t* bit) noexcept:
    cdef RPROSC block = <RPROSC> ctx
    try:
        bit[0] = <uint32_t> (int(block._random_bit()) & 1)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RPROSC:
    def __init__(self, rp2040, name, mcu_index=-1):
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native ROSC counts COUNT against the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_rosc.RPROSC with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        self._random_bit = None
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            # (its RANDOMBIT comes from the block's own seeded generator until a source is set: `random_bit = fn`)
            self._block = <RoscBlock*> <size_t> rp2040._native_rosc_address()
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPROSC is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _rejoin_the_python_tree(self, tree):
        """`tree` is going away (the chip's clock tree was replaced): tell the chip through Python again, if this block was telling that tree."""
        if self._bound_tree is not tree:
            return
        self._bound_tree = None
        self._block.bind_frequency_listener(_frequency_trampoline, <void*> self)

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kRoscWarnRead:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kRoscWarnReadAtomicArea:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        elif kind == kRoscWarnInvalidFreqRange:
            self.warn(f"Invalid FREQ_RANGE value written: 0x{value:x}")
        elif kind == kRoscWarnInvalidEnable:
            self.warn(f"Invalid ENABLE value written: 0x{value:x}")
        elif kind == kRoscWarnInvalidFreqPasswd:
            self.warn(f"Invalid FREQ password written: 0x{value:x}")
        elif kind == kRoscWarnInvalidDormant:
            self.warn(f"Invalid DORMANT value written: 0x{value:x}")
        elif kind == kRoscWarnInvalidDiv:
            self.warn(f"Invalid DIV value written: 0x{value:x}")
        elif kind == kRoscWarnInvalidPhasePasswd:
            self.warn(f"Invalid PHASE password written: 0x{value:x}")
        else:
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    def read_uint32(self, offset):
        cdef uint32_t value = 0
        if not self._block.read(<uint32_t> offset, &value):
            raise_if_pending()
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    @property
    def frequency(self):
        return self._block.frequency()

    def _count_now(self):
        return self._block.count_now()

    @property
    def _ctrl(self):
        return self._block.ctrl

    @_ctrl.setter
    def _ctrl(self, value):
        self._block.ctrl = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _freqa(self):
        return self._block.freqa

    @_freqa.setter
    def _freqa(self, value):
        self._block.freqa = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _freqb(self):
        return self._block.freqb

    @_freqb.setter
    def _freqb(self, value):
        self._block.freqb = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _dormant(self):
        return self._block.dormant

    @_dormant.setter
    def _dormant(self, value):
        self._block.dormant = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _div(self):
        return self._block.div

    @_div.setter
    def _div(self, value):
        self._block.div = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _phase(self):
        return self._block.phase

    @_phase.setter
    def _phase(self, value):
        self._block.phase = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _badwrite(self):
        return bool(self._block.badwrite)

    @_badwrite.setter
    def _badwrite(self, value):
        self._block.badwrite = bool(value)

    @property
    def _enabled(self):
        return bool(self._block.enabled)

    @_enabled.setter
    def _enabled(self, value):
        self._block.enabled = bool(value)

    @property
    def _last_bit(self):
        return self._block.last_bit

    @_last_bit.setter
    def _last_bit(self, value):
        self._block.last_bit = <uint32_t> (value & 1)

    @property
    def _count(self):
        return self._block.count

    @_count.setter
    def _count(self, value):
        self._block.count = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _count_epoch(self):
        return self._block.count_epoch

    @_count_epoch.setter
    def _count_epoch(self, value):
        self._block.count_epoch = <int64_t> value

    @property
    def _hz(self):
        return self._block.hz

    @_hz.setter
    def _hz(self, value):
        self._block.hz = <double> value

    @property
    def random_bit(self):
        """The source of RANDOMBIT (the reference's hook). On the chip's own block, until one is set, the block uses its own seeded generator and this is None."""
        return self._random_bit

    @random_bit.setter
    def random_bit(self, value):
        self._random_bit = value
        # hand the chip's block the source (or take it away)
        if value is None:
            self._block.bind_random_source(NULL, NULL)
        else:
            self._block.bind_random_source(_bit_trampoline, <void*> self)

    def reset(self):
        """Back to power-on (enabled, div 16, LOW range). Gated on `PSM.WDSEL`'s ROSC bit by `RP2040.reset()` (see the reference)."""
        if not self._block.reset():
            raise_if_pending()

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's
        own read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder,
        a profiler) cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
