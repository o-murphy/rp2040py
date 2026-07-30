from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040


__all__ = ("IGDBTarget",)


class IGDBTarget(Protocol):
    rp2040: "RP2040"

    @property
    def executing(self) -> bool: ...

    def execute(self) -> None: ...
    def stop(self) -> None: ...
