"""MicroPython raw REPL protocol (Ctrl-A/Ctrl-D/Ctrl-B), used to run a one-shot command/module/
script on the device over USB CDC and capture its result - the same protocol `mpremote run` and
`tools/pyboard.py` use. See https://docs.micropython.org/en/latest/reference/repl.html#raw-mode.
"""

from collections.abc import Callable

__all__ = ("CTRL_A", "CTRL_B", "CTRL_C", "CTRL_D", "RawReplError", "RawReplRunner")

CTRL_A = 1  # enter raw REPL
CTRL_B = 2  # exit raw REPL, back to the friendly REPL
CTRL_C = 3  # interrupt
CTRL_D = 4  # execute pasted code; also the end-of-stdout/end-of-stderr marker in the reply

# The exact banner MicroPython/CircuitPython print in response to Ctrl-A. Matching the full string
# (not just its trailing ">") matters: the friendly REPL's own idle prompt is "\r\n>>> ", which
# also contains ">" and can otherwise be mistaken for raw-REPL readiness - e.g. if a Ctrl-A arrives
# while the device is still flushing its normal boot banner.
_RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"


class RawReplError(RuntimeError):
    """The device didn't speak the raw-REPL protocol as expected (e.g. garbled/unexpected bytes
    where the literal b"OK" execution acknowledgement was expected)."""


class RawReplRunner:
    """Drives one raw-REPL exec: call `start()` once the device is connected, then `feed()` with
    every chunk of incoming serial data. `result` is set to `(stdout, stderr)` once the device has
    finished executing `source` and sent both terminating Ctrl-D markers.
    """

    def __init__(self, source: bytes, send_byte: Callable[[int], None]) -> None:
        self._source = source
        self._send_byte = send_byte
        self._stage = "await_prompt"
        self._banner_tail = bytearray()
        self._ok_buffer = bytearray()
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.result: tuple[bytes, bytes] | None = None

    def start(self) -> None:
        # Ctrl-C twice first, interrupting any program already running (e.g. an auto-run main.py
        # from a littlefs image) before entering raw REPL - the same sequence
        # tools/pyboard.py's enter_raw_repl() uses, for the same reason.
        self._send_byte(CTRL_C)
        self._send_byte(CTRL_C)
        self._send_byte(CTRL_A)

    def feed(self, data: bytes | bytearray) -> None:
        for byte in data:
            if self._stage == "await_prompt":
                self._banner_tail.append(byte)
                del self._banner_tail[: -len(_RAW_REPL_BANNER)]
                if self._banner_tail == _RAW_REPL_BANNER:
                    self._stage = "await_ok"
                    for source_byte in self._source:
                        self._send_byte(source_byte)
                    self._send_byte(CTRL_D)
            elif self._stage == "await_ok":
                # The device echoes exactly b"OK" once it accepts the Ctrl-D and starts executing.
                self._ok_buffer.append(byte)
                if len(self._ok_buffer) == 2:
                    if bytes(self._ok_buffer) != b"OK":
                        raise RawReplError(f"expected b'OK' after Ctrl-D, got {bytes(self._ok_buffer)!r}")
                    self._stage = "stdout"
            elif self._stage == "stdout":
                if byte == CTRL_D:
                    self._stage = "stderr"
                else:
                    self._stdout.append(byte)
            elif self._stage == "stderr":
                if byte == CTRL_D:
                    self.result = (bytes(self._stdout), bytes(self._stderr))
                else:
                    self._stderr.append(byte)
