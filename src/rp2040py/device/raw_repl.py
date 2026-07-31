"""MicroPython raw REPL protocol (Ctrl-A/Ctrl-D/Ctrl-B), used to run a one-shot command/module/
script on the device over USB CDC and capture its result - the same protocol `mpremote run` and
`tools/pyboard.py` use. See https://docs.micropython.org/en/latest/reference/repl.html#raw-mode.
"""

import threading
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

    `free_space`, if given, is queried before each send to cap how much of `source` gets pushed at
    once - see `pump()`. Without it, `feed()` pushes the whole `source` (plus the terminating
    Ctrl-D) in one synchronous burst as soon as the raw-REPL banner arrives, same as before this
    parameter existed; fine for tests/short snippets, but see `pump()`'s docstring for why real
    callers driving an actual (bounded) USB CDC FIFO need it.
    """

    def __init__(
        self,
        source: bytes,
        send_byte: Callable[[int], None],
        free_space: "Callable[[], int] | None" = None,
    ) -> None:
        self._source = source
        self._send_byte = send_byte
        self._free_space = free_space if free_space is not None else (lambda: len(source) + 1)
        self._stage = "await_prompt"
        self._banner_tail = bytearray()
        self._ok_buffer = bytearray()
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.result: tuple[bytes, bytes] | None = None
        self._pending = b""
        # Guards self._pending: pump() is called both from feed() (on whichever thread delivers
        # incoming serial data - the simulator's own worker thread against a real USBCDC) and,
        # separately, from a caller-driven retry loop (e.g. mp_device.py's threading.Timer-based
        # one) on its own thread. Without this, two concurrent pump() calls racing on the
        # read-slice-reassign of self._pending can lose or duplicate bytes - confirmed the hard
        # way: an intermittent, real "IndentationError: unexpected indent" partway through an
        # otherwise-valid uploaded script, from bytes silently dropped at a chunk boundary.
        self._pending_lock = threading.Lock()

    def start(self) -> None:
        # Ctrl-C twice first, interrupting any program already running (e.g. an auto-run main.py
        # from a littlefs image) before entering raw REPL - the same sequence
        # tools/pyboard.py's enter_raw_repl() uses, for the same reason.
        self._send_byte(CTRL_C)
        self._send_byte(CTRL_C)
        self._send_byte(CTRL_A)

    def pump(self) -> bool:
        """Sends as much of the pending source upload as `free_space()` currently allows, and
        returns whether it's all been sent. Call again (e.g. from a `threading.Timer`) while this
        returns `False`.

        Needed because `feed()` used to dump the *entire* `source` into `send_byte` in one
        synchronous burst the instant the raw-REPL banner arrived, regardless of how much room the
        receiving end actually had. Against a real `USBCDC`, that end is a fixed-size FIFO
        (`TX_FIFO_SIZE = 512` in `usb/cdc.py`) that silently drops pushes once full rather than
        raising or blocking - so any source over ~512 bytes lost everything past that point,
        including the terminating Ctrl-D, leaving the device waiting forever for an end-of-paste
        marker that had already been dropped on the floor. Confirmed against a real image: a
        440-byte script ran fine, an otherwise-identical 890-byte one hung indefinitely with zero
        output. `feed()` can't drain the FIFO itself mid-burst either - it's invoked synchronously
        from inside the emulated CPU's own `execute_instruction()` call chain (the device writing
        to its USB TX register), so nothing else runs until it returns; pacing has to happen across
        separate `pump()` calls instead, giving control back to the simulator loop in between so it
        can actually service the USB peripheral and free up FIFO space.
        """
        with self._pending_lock:
            if self._pending:
                free = max(self._free_space(), 0)
                if free:
                    chunk, self._pending = self._pending[:free], self._pending[free:]
                    for byte in chunk:
                        self._send_byte(byte)
            return not self._pending

    def feed(self, data: bytes | bytearray) -> None:
        for byte in data:
            if self._stage == "await_prompt":
                self._banner_tail.append(byte)
                del self._banner_tail[: -len(_RAW_REPL_BANNER)]
                if self._banner_tail == _RAW_REPL_BANNER:
                    self._stage = "await_ok"
                    self._pending = bytes(self._source) + bytes([CTRL_D])
                    self.pump()
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
