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

    @property
    def nanos_to_next_alarm(self) -> float: ...

    @property
    def has_scheduled_alarm(self) -> bool: ...
