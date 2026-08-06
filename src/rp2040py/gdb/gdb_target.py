from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from rp2040py.rp2040 import RP2040


__all__ = ("IGDBTarget",)


class IGDBTarget(Protocol):
    rp2040: "RP2040"

    @property
    def executing(self) -> bool: ...

    def start_execution(self) -> None: ...
    def stop(self) -> None: ...

    async def acall(self, coro: "Coroutine[Any, Any, Any]") -> Any: ...
