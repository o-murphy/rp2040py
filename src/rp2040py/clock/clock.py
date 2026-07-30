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
