from collections.abc import Callable
from typing import Protocol

__all__ = (
    "AlarmCallback",
    "IAlarm",
    "IClock",
)

AlarmCallback = Callable[[], None]


class IAlarm(Protocol):
    def schedule(self, delta_nanos: float) -> None: ...
    def schedule_ticks(self, delta_ticks: int) -> None: ...
    def cancel(self) -> None: ...


class IClock(Protocol):
    @property
    def nanos(self) -> float: ...

    def create_alarm(self, callback: AlarmCallback) -> IAlarm: ...

    # Driven once per simulated CPU instruction by the execute loop; part of the interface every
    # `clock: IClock` caller relies on (SimulationClock/MockClock and the native port all provide
    # them). Declared here so the native .pyi stubs type-check their callers instead of falling back
    # to Any - see native/_simulation_clock.pyx's module docstring.
    def tick(self, delta_nanos: float) -> None: ...

    # Time is an integer count of ticks of 1/16 ns (docs/records/0100-integer-clock.md); `nanos` is derived from it.
    @property
    def ticks(self) -> int: ...

    def tick_ticks(self, delta_ticks: int) -> None: ...

    def defer_ticks(self, delta_ticks: int) -> None: ...

    # The origin of `ticks` moves when an idle chip has fast-forwarded far enough; a holder of an absolute tick count subtracts the delta it is told.
    def add_rebase_listener(self, callback: Callable[[int], None]) -> Callable[[], None]: ...

    def advance_cycles(self, cycles: int, ticks_per_cycle: float) -> None: ...

    @property
    def ticks_to_next_alarm(self) -> int: ...

    @property
    def nanos_to_next_alarm(self) -> float: ...

    @property
    def has_scheduled_alarm(self) -> bool: ...
