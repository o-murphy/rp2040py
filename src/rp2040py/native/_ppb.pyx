# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PPB (the Cortex-M0's private peripheral bus: SysTick, NVIC, SCB registers): a Python-facing shell over the C++ `PpbBlock` of `core/ppb.hpp`
(docs/records/0096-cpp-mcu-core.md, the PPB design note). `peripherals/_ppb.py` is the pure-Python reference, kept as the oracle (tests/test_ppb_diff.py); `peripherals/ppb.py` is the
facade that picks between them, as for the other native ports.

The PPB is not a peripheral-table window (its range is 0xE000E000), so the chip learns about this block through its `ppb` property: when the object assigned has a `_native_window`
on its type, the bus calls the block's C++ functions directly - a NVIC enable or a SysTick read never touches Python on the access path. The block works on the C++ `Cpu` of the
chip's native core (the pending and enabled words, the priority bitmaps, VTOR are the CPU's own fields), so it needs the native core, and SysTick's alarm is a node of the chip's C++
clock, so it needs the native `SimulationClock` as well - a running SysTick costs no Python. The only thing that stays Python is the logger (the warning for an offset that is not a
register), reached through a trampoline whose failures are parked in the shared slot of `_pending.pyx`.

The API is the reference's: `systick_count_flag`, `systick_clk_source`, `systick_int_enable`, `systick_enable`, `systick_reload`, `systick_timer` (a view of the C++ `Timer32` with the reference's
attributes: `frequency` is what `update_clocks` retunes), `systick_alarm`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._cpu cimport Cpu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._views cimport Timer32View
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPPB",)


cdef class RPPPB(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        cdef Timer32View timer
        cdef Cpu* cpu
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native PPB schedules SysTick's alarm on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_ppb.RPPPB with it)"
            )
        native_cpu_address = getattr(rp2040.core, "_native_cpu_address", None)
        if native_cpu_address is None:
            raise TypeError(
                "the native PPB works on the native core's C++ CPU state; "
                f"{type(rp2040).__name__} with {type(rp2040.core).__name__} is not that "
                "(use the pure-Python peripherals/_ppb.RPPPB with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        self._core = rp2040.core
        cpu = <Cpu*> (<size_t> native_cpu_address())
        self.on_system_reset_request = self._default_system_reset_request
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already and wired it (its window, its lines, the clock tree, the watchdog's tick) - this object is the Python view of it
            self._block = <PpbBlock*> <size_t> rp2040._native_ppb_address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPPPB is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        timer = Timer32View.__new__(Timer32View)
        timer._owner = self
        timer._timer = &self._block.timer
        self.systick_timer = timer

    def _default_system_reset_request(self):
        # AIRCR.SYSRESETREQ: "Reset the core only" (datasheet 2.4.2.9)
        self.rp2040.core.reset()
        self.reset()
        self.rp2040.leave_reset()

    def _world_system_reset(self):
        """AIRCR.SYSRESETREQ: whatever `on_system_reset_request` is now (the chip's host calls this for the block it built)."""
        self.on_system_reset_request()

    def snapshot(self):
        """The block's state in one call: SysTick (flags, reload, the countdown and its alarm, the clocks it follows) and the system-control registers (SCR, the MPU)."""
        cdef PpbBlock* b = self._block
        return {
            "systick_count_flag": bool(b.count_flag),
            "systick_clk_source": bool(b.clk_source),
            "systick_int_enable": bool(b.int_enable),
            "systick_enable": bool(b.systick_enable),
            "tick_hz": b.tick_hz(),
            "systick_reload": b.reload,
            "timer_enable": bool(b.timer.enable()),
            "timer_mode": b.timer.mode_raw(),
            "clk_sys": b.clk_sys(),
            "timer_frequency": b.timer.frequency(),
            "timer_prescaler": b.timer.prescaler(),
            "timer_top": b.timer.top(),
            "timer_raw_counter": b.timer.raw_counter(),
            "timer_counter": b.timer.counter(),
            "alarm_target": b.alarm.target(),
            "alarm_enable": bool(b.alarm.enable()),
            "scr": b.scr,
            "mpu_ctrl": b.mpu_ctrl,
            "mpu_rnr": b.mpu_rnr,
            "mpu_rbar": tuple([b.mpu_rbar[i] for i in range(8)]),
            "mpu_rasr": tuple([b.mpu_rasr[i] for i in range(8)]),
        }

    def tick_changed(self, tick_hz):
        """The watchdog's tick is now `tick_hz` (0: stopped): SysTick follows it when its CLKSOURCE says reference clock."""
        self._block.tick_changed(<double> tick_hz)

    def clk_sys_changed(self, clk_sys):
        """clk_sys is now `clk_sys` (called by `update_clocks`): SysTick follows it when its CLKSOURCE says processor clock."""
        self._block.clk_sys_changed(<double> clk_sys)

    def reset(self):
        """SysTick back to stopped, reload and counter 0xFFFFFF (0089 Phase 5)."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
