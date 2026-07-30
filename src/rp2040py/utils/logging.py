from datetime import datetime
from enum import IntEnum
from typing import Protocol

from rp2040py.utils.time import format_time


class Logger(Protocol):
    def debug(self, component_name: str, message: str) -> None: ...
    def warning(self, component_name: str, message: str) -> None: ...
    def error(self, component_name: str, message: str) -> None: ...
    def info(self, component_name: str, message: str) -> None: ...


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3


class ConsoleLogger:
    def __init__(self, current_log_level: LogLevel, throw_on_error: bool = True):
        self.current_log_level = current_log_level
        self._throw_on_error = throw_on_error

    def _above_log_level(self, log_level: LogLevel) -> bool:
        return log_level >= self.current_log_level

    def _format_message(self, component_name: str, message: str) -> str:
        current_time = format_time(datetime.now().astimezone())
        return f"{current_time} [{component_name}] {message}"

    def debug(self, component_name: str, message: str) -> None:
        if self._above_log_level(LogLevel.DEBUG):
            print(self._format_message(component_name, message))

    def warning(self, component_name: str, message: str) -> None:
        if self._above_log_level(LogLevel.WARN):
            print(self._format_message(component_name, message))

    def error(self, component_name: str, message: str) -> None:
        if self._above_log_level(LogLevel.ERROR):
            print(self._format_message(component_name, message))
            if self._throw_on_error:
                raise RuntimeError(f"[{component_name}] {message}")

    def info(self, component_name: str, message: str) -> None:
        if self._above_log_level(LogLevel.INFO):
            print(self._format_message(component_name, message))
