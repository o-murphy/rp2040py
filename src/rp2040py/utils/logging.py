import logging
from enum import IntEnum
from typing import Protocol

__all__ = (
    "ConsoleLogger",
    "LogLevel",
    "Logger",
)


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


# Maps this project's own LogLevel (mirrors upstream rp2040js's Logger interface, see the Logger
# Protocol above) onto stdlib logging's levels - a separate enum, not stdlib's, because callers
# throughout the codebase already depend on LogLevel's exact members (DEBUG/INFO/WARN/ERROR) and
# IntEnum ordering for comparisons.
_STDLIB_LEVEL = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARN: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}

# "%(component_name)s" is not one of logging's built-in LogRecord attributes - it's supplied per
# call via the `extra={"component_name": ...}` kwarg on the stdlib logger calls below, same
# mechanism logging's own docs recommend for custom per-record fields.
_FORMATTER = logging.Formatter("%(asctime)s.%(msecs)03d [%(component_name)s] %(message)s", datefmt="%H:%M:%S")


class ConsoleLogger:
    def __init__(self, current_log_level: LogLevel, throw_on_error: bool = True):
        self.current_log_level = current_log_level
        self._throw_on_error = throw_on_error
        # A dedicated logger per ConsoleLogger instance (name includes id() to keep it unique),
        # not the root logger or a shared module-level one: current_log_level/throw_on_error are
        # per-instance (e.g. tests construct a fresh ConsoleLogger(LogLevel.ERROR) to quiet a
        # specific device), and stdlib logging.Logger instances are otherwise process-global
        # singletons keyed by name - reusing one name across instances would let one instance's
        # setLevel() calls affect another's output.
        self._logger = logging.getLogger(f"{__name__}.ConsoleLogger.{id(self)}")
        self._logger.setLevel(_STDLIB_LEVEL[current_log_level])
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(_FORMATTER)
            self._logger.addHandler(handler)

    def debug(self, component_name: str, message: str) -> None:
        self._logger.debug(message, extra={"component_name": component_name})

    def warning(self, component_name: str, message: str) -> None:
        self._logger.warning(message, extra={"component_name": component_name})

    def error(self, component_name: str, message: str) -> None:
        self._logger.error(message, extra={"component_name": component_name})
        if self._throw_on_error:
            raise RuntimeError(f"[{component_name}] {message}")

    def info(self, component_name: str, message: str) -> None:
        self._logger.info(message, extra={"component_name": component_name})
