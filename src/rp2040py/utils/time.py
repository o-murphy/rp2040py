import time
from datetime import datetime


def get_current_microseconds() -> int:
    return int(time.perf_counter() * 1_000_000)


def _left_pad(value: str, min_length: int, pad_char: str = " ") -> str:
    if len(value) < min_length:
        value = pad_char + value
    return value


def _right_pad(value: str, min_length: int, pad_char: str = " ") -> str:
    if len(value) < min_length:
        value += pad_char
    return value


def format_time(date: datetime) -> str:
    hours = _left_pad(str(date.hour), 2, "0")
    minutes = _left_pad(str(date.minute), 2, "0")
    seconds = _left_pad(str(date.second), 2, "0")
    milliseconds = _right_pad(str(date.microsecond // 1000), 3)
    return f"{hours}:{minutes}:{seconds}.{milliseconds}"
