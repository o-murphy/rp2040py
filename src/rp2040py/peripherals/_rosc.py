import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.clock._simulation_clock import TICKS_PER_NANO
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPROSC",)

# Register offsets (RP2040 datasheet 2.17.8, table 264)
ROSC_CTRL = 0x00
ROSC_FREQA = 0x04
ROSC_FREQB = 0x08
ROSC_DORMANT = 0x0C
ROSC_DIV = 0x10
ROSC_PHASE = 0x14
ROSC_STATUS = 0x18
ROSC_RANDOMBIT = 0x1C
ROSC_COUNT = 0x20

# CTRL (table 265): 31:24 reserved; ENABLE 23:12 "On power-up this field is initialised to ENABLE"; FREQ_RANGE 11:0 resets to 0xAA0
CTRL_MASK = 0x00FFFFFF
CTRL_ENABLE_LSB = 12
CTRL_ENABLE_BITS = 0x00FFF000
CTRL_FREQ_RANGE_BITS = 0x00000FFF
CTRL_ENABLE_DISABLE = 0xD1E
CTRL_ENABLE_ENABLE = 0xFAB
CTRL_RESET = (CTRL_ENABLE_ENABLE << CTRL_ENABLE_LSB) | 0xAA0
FREQ_RANGE_RESET = 0xAA0
FREQ_RANGE_LOW, FREQ_RANGE_MEDIUM, FREQ_RANGE_HIGH, FREQ_RANGE_TOOHIGH = 0xFA4, 0xFA5, 0xFA7, 0xFA6

# FREQA / FREQB (tables 266, 267): PASSWD 31:16 must be 0x9696 or "all drive strengths" are set to 0; DSn are 3 bits at 14:12, 10:8, 6:4, 2:0
FREQ_PASSWD = 0x9696
FREQ_DS_BITS = 0x7777

# DORMANT (table 268)
DORMANT_VALUE = 0x636F6D61
WAKE_VALUE = 0x77616B65

# DIV (table 269): 0xAA0 + div, "div = 0 divides by 32, div = 1-31 divides by div, any other value sets div=31"; resets to div=16
DIV_BITS = 0x00000FFF
DIV_PASS = 0xAA0
DIV_RESET = DIV_PASS + 16
DIV_INVALID = DIV_PASS + 31

# PHASE (table 270): PASSWD 11:4 = 0xAA, ENABLE 3 (resets to 1), FLIP 2, SHIFT 1:0
PHASE_PASSWD = 0xAA
PHASE_RESET = 0x8

# STATUS (table 271)
STATUS_STABLE = 0x80000000
STATUS_BADWRITE = 0x01000000
STATUS_DIV_RUNNING = 0x00010000
STATUS_ENABLED = 0x00001000

COUNT_MASK = 0xFF

# "During boot the ROSC runs at a nominal 6.5MHz" (2.17.1), with the divider at its default of 16 and FREQ_RANGE at LOW
NOMINAL_HZ = 6.5e6
NOMINAL_DIV = 16.0
# 2.17.3: "the MEDIUM range will be approximately 1.33 times the LOW RANGE, the HIGH range will be 2 times the LOW range and the TOOHIGH range will be 4 times the LOW range"
RANGE_RATIO = {
    FREQ_RANGE_RESET: 1.0,
    FREQ_RANGE_LOW: 1.0,
    FREQ_RANGE_MEDIUM: 1.33,
    FREQ_RANGE_HIGH: 2.0,
    FREQ_RANGE_TOOHIGH: 4.0,
}


def _divisor(div_value: int) -> int:
    """The divisor a DIV register value stands for: `0xAA0 + div` with "div = 0 divides by 32"."""
    div = div_value - DIV_PASS
    return div if div else 32


def _frequency(div_value: int, freq_range: int) -> float:
    """The output frequency for a DIV register value and a FREQ_RANGE: the nominal 6.5 MHz scaled by the divider (16 -> 1) and the range's ratio. The drive strengths (FREQA / FREQB) have no
    effect: the datasheet says only that they raise the frequency "not linearly", with no figures."""
    return NOMINAL_HZ * NOMINAL_DIV / _divisor(div_value) * RANGE_RATIO[freq_range]


class RPROSC(BasePeripheral):
    """The ring oscillator (datasheet 2.17): control, drive strengths, dormant, divider, phase, status, random bit and the down counter.

    What is modelled, and what is not (docs/records/0098-datasheet-conformance-audit.md, "ROSC"):

    - the registers as the tables lay them out, with `STATUS.BADWRITE` for every invalid write the table lists (CTRL.ENABLE / FREQ_RANGE, FREQA, FREQB, DIV, PHASE, DORMANT);
    - the frequency: `rp2040.rosc_freq` follows DIV and FREQ_RANGE (nominal 6.5 MHz at div 16 / LOW; the divider and the range ratios are the datasheet's, the drive strengths do nothing) and
      the clock tree is told, so a firmware that runs from the ROSC and raises its speed gets it;
    - RANDOMBIT, from `random_bit` (a hook; the default is a seeded generator, so a simulation is reproducible - replace it for real entropy); COUNT, which counts down at the ROSC
      frequency while the oscillator is enabled and stops at zero;
    - **not modelled:** the oscillator really stopping (a disabled ROSC keeps clocking the chip, as the datasheet says the chip would lock up if it was the system clock), DORMANT (stored; see
      XOSC), STABLE as a start-up time (it follows ENABLE), the phase-shifted output (PHASE is stored and validated).

    Not independently sourced, kept as the model's own choice: that a stored register reads back what was written (FREQA / FREQB / PHASE keep their passwords; an invalid FREQ_RANGE leaves the
    previous range; an invalid DIV reads back as div = 31), `DIV_RUNNING` following ENABLED, RANDOMBIT holding its last value while the oscillator is disabled, and COUNT counting at the *output*
    frequency (the datasheet says "the ROSC frequency")."""

    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        # A seeded generator rather than the OS's: the same firmware sees the same "random" bits on every run of a simulation. Replace `random_bit` to feed it something else.
        self._rng = random.Random(0x2040)
        self.random_bit: Callable[[], int] = lambda: self._rng.getrandbits(1)
        # The frequency this block last gave the chip (its own copy: the clock tree is told, it is not asked)
        self._hz = NOMINAL_HZ
        self._load_power_on_state()
        rp2040.clock.add_rebase_listener(
            self._rebased
        )  # COUNT's epoch is an absolute tick count: it follows the clock when its origin moves

    def _rebased(self, delta: int) -> None:
        self._count_epoch -= delta
        self._rebase_count()  # folded into COUNT: the epoch stays at the clock's time instead of drifting away from it by `delta` every move (it would overflow in C++)

    def _load_power_on_state(self) -> None:
        self._ctrl = CTRL_RESET
        self._freqa = 0
        self._freqb = 0
        self._dormant = WAKE_VALUE
        self._div = DIV_RESET
        self._phase = PHASE_RESET
        self._badwrite = False
        self._enabled = True  # "started automatically during RP2040 power up"
        self._last_bit = 1  # RANDOMBIT resets to 1
        self._count = 0
        self._count_epoch = 0  # in ticks of 1/16 ns

    def reset(self) -> None:
        """Back to power-on: enabled, div 16, LOW range, no drive. Gated on `PSM.WDSEL`'s ROSC bit by `RP2040.reset()`: `watchdog_reboot()` clears it ("everything apart from ROSC and XOSC"),
        a power-on / RUN-pin reset sets it."""
        self._load_power_on_state()
        self._apply_frequency()

    @property
    def frequency(self) -> float:
        return _frequency(self._div, self._ctrl & CTRL_FREQ_RANGE_BITS)

    def _apply_frequency(self) -> None:
        """Tells the chip when the output frequency changed (its clock tree re-derives everything that runs from the ROSC)."""
        frequency = self.frequency
        if frequency != self._hz:
            self._rebase_count()
            self._hz = frequency
            self.rp2040.rosc_freq = frequency
            self.rp2040.update_clocks()

    def _rebase_count(self) -> None:
        """Folds the time that passed into COUNT before anything that changes its rate (frequency, ENABLE) or its value."""
        self._count = self._count_now()
        self._count_epoch = self.rp2040.clock.ticks

    def _count_now(self) -> int:
        if self._count and self._enabled:
            elapsed = (
                self.rp2040.clock.ticks - self._count_epoch
            ) / TICKS_PER_NANO  # the difference is taken in ticks: exact however far into the run
            done = int(elapsed * self._hz / 1e9)
            return max(self._count - done, 0)
        return self._count

    def _bad_write(self, message: str) -> None:
        self._badwrite = True
        self.warn(message)

    def snapshot(self) -> dict[str, int | float | bool]:
        """The block's state as plain values: the registers, the flags, the COUNT bookkeeping and what is derived from the clock (`frequency`, the counter as it reads now)."""
        return {
            "ctrl": self._ctrl,
            "freqa": self._freqa,
            "freqb": self._freqb,
            "dormant": self._dormant,
            "div": self._div,
            "phase": self._phase,
            "badwrite": self._badwrite,
            "enabled": self._enabled,
            "last_bit": self._last_bit,
            "count": self._count,
            "count_epoch": self._count_epoch,
            "hz": self._hz,
            "frequency": self.frequency,
            "count_now": self._count_now(),
        }

    def read_uint32(self, offset: int) -> int:
        if offset == ROSC_CTRL:
            return self._ctrl
        if offset == ROSC_FREQA:
            return self._freqa
        if offset == ROSC_FREQB:
            return self._freqb
        if offset == ROSC_DORMANT:
            return self._dormant
        if offset == ROSC_DIV:
            return self._div
        if offset == ROSC_PHASE:
            return self._phase
        if offset == ROSC_STATUS:
            running = STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0
            return running | (STATUS_BADWRITE if self._badwrite else 0)
        if offset == ROSC_RANDOMBIT:
            if self._enabled:
                self._last_bit = self.random_bit() & 1
            return self._last_bit
        if offset == ROSC_COUNT:
            return self._count_now()
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == ROSC_CTRL:
            value &= CTRL_MASK
            freq_range = value & CTRL_FREQ_RANGE_BITS
            if freq_range not in RANGE_RATIO:
                self._bad_write(f"Invalid FREQ_RANGE value written: 0x{freq_range:x}")
                freq_range = self._ctrl & CTRL_FREQ_RANGE_BITS  # the range stays as it was
            enable_value = (value & CTRL_ENABLE_BITS) >> CTRL_ENABLE_LSB
            if enable_value not in (CTRL_ENABLE_ENABLE, CTRL_ENABLE_DISABLE):
                # "An invalid setting will enable the oscillator" (table 265), and STATUS.BADWRITE lists CTRL_ENABLE
                self._bad_write(f"Invalid ENABLE value written: 0x{enable_value:x}")
            enabled = enable_value != CTRL_ENABLE_DISABLE
            if enabled != self._enabled:
                self._rebase_count()
                self._enabled = enabled
            self._ctrl = (value & CTRL_ENABLE_BITS) | freq_range
            self._apply_frequency()

        elif offset in (ROSC_FREQA, ROSC_FREQB):
            drive = value & FREQ_DS_BITS
            if (value >> 16) & 0xFFFF != FREQ_PASSWD:
                # "Any other value in this field will set all drive strengths to 0"
                self._bad_write(f"Invalid FREQ password written: 0x{(value >> 16) & 0xFFFF:x}")
                drive = 0
            stored = (value & 0xFFFF0000) | drive
            if offset == ROSC_FREQA:
                self._freqa = stored
            else:
                self._freqb = stored

        elif offset == ROSC_DORMANT:
            if value not in (DORMANT_VALUE, WAKE_VALUE):
                # "An invalid write will also select WAKE"
                self._bad_write(f"Invalid DORMANT value written: 0x{value:x}")
                value = WAKE_VALUE
            self._dormant = value

        elif offset == ROSC_DIV:
            value &= DIV_BITS
            if not DIV_PASS <= value <= DIV_PASS + 31:
                self._bad_write(f"Invalid DIV value written: 0x{value:x}")
                value = DIV_INVALID  # "any other value sets div=31"
            self._div = value
            self._apply_frequency()

        elif offset == ROSC_PHASE:
            value &= DIV_BITS
            enable = (value >> 3) & 1
            shift = value & 3
            if (value >> 4) & 0xFF != PHASE_PASSWD:
                # "any other value enables the output with shift=0"
                self._bad_write(f"Invalid PHASE password written: 0x{(value >> 4) & 0xFF:x}")
                enable, shift = 1, 0
            self._phase = (value & 0xFF0) | (enable << 3) | (value & 4) | shift

        elif offset == ROSC_STATUS:
            if value & STATUS_BADWRITE:
                self._badwrite = False  # write 1 to clear

        elif offset == ROSC_COUNT:
            self._count = value & COUNT_MASK  # "To start the counter write a non-zero value"
            self._count_epoch = self.rp2040.clock.ticks

        elif offset == ROSC_RANDOMBIT:
            return  # read only

        else:
            super().write_uint32(offset, value)
