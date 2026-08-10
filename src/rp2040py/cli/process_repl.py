"""Shared plumbing for `InteractiveRepl` subclasses that drive a `Simulator`-backed device as part
of a standalone CLI process: SIGTERM handling, and the queue-then-repeat-pump loop for forwarding
input bytes to the device when `cdc.tx_fifo` doesn't have room for all of them at once. Used by
`StdioInteractiveRepl`, `SocketInteractiveRepl`, and `PtyInteractiveRepl` - previously duplicated
verbatim between the first two before this existed, and a third copy is exactly what prompted
pulling it out here instead of copy-pasting it again for `--pty`.
"""

import signal
import threading
from collections.abc import Callable
from typing import Any

from rp2040py.device.repl_runner import InteractiveRepl
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC

__all__ = ("ProcessInteractiveRepl",)

# Matches every subclass's own previous hardcoded value: 1ms of simulated time between repeat
# pump() attempts while the tx_fifo is still full.
_PUMP_PERIOD_NANOS = 1_000_000


class ProcessInteractiveRepl(InteractiveRepl):
    """`on_quit` is called with an exit code on whatever a subclass's own transport treats as a
    quit trigger (Ctrl+X for `StdioInteractiveRepl`, nothing in-band for `SocketInteractiveRepl`/
    `PtyInteractiveRepl` - see their own docstrings) or a SIGTERM received while this instance owns
    the terminal/transport. It must only signal (e.g. `Simulator.shutdown_request.request`), never
    block or tear the process down itself - this class never calls `sys.exit()`/`os._exit()` on its
    own. Centralizing cleanup before a real `sys.exit()` is entirely the caller's job, typically
    `Simulator.wait_for_shutdown`.

    SIGTERM needs an explicit handler here at all because Python's default disposition for it is
    immediate OS-level process termination, bypassing every `finally`/context-manager exit in the
    interpreter (confirmed empirically - see CHANGELOG.md) - including `--dump-fs`'s own cleanup
    callback. Installed/restored around `start()`/`stop()`, called with `128 + signal.SIGTERM`, the
    convention the shell/`timeout` itself would otherwise report for a signal-terminated process.
    `signal.signal()` only works from the main thread - every current subclass's `start()` always
    runs there (nothing async calls it), so `_on_start()`/`_restore_sigterm_handler()` are
    unaffected by `simulator`'s own engine-room thread; `_restore_sigterm_handler()` is still
    thread-guarded rather than assuming that, since it can also run from a non-main-thread fallback
    cleanup path (see `StdioInteractiveRepl`'s own non-tty fallback).
    """

    def __init__(
        self,
        cdc: USBCDC,
        simulator: Simulator,
        on_quit: "Callable[[int], None]",
        on_data: "Callable[[bytes | bytearray], None]",
    ) -> None:
        super().__init__(cdc, on_data=on_data)
        self._simulator = simulator
        self._on_quit = on_quit
        self._old_sigterm_handler: Any = None
        self._pump_alarm: Any = None

    def _on_start(self) -> None:
        self._old_sigterm_handler = signal.signal(signal.SIGTERM, self._on_sigterm)

    def _on_stop(self) -> None:
        self._restore_sigterm_handler()

    def _on_sigterm(self, signum: int, _frame: Any) -> None:
        # Just flag it - whatever handles self._on_quit (Simulator.wait_for_shutdown, for every
        # current caller) does the actual repl.stop()/device teardown/sys.exit(); this must not
        # block or tear the process down itself.
        self._on_quit(128 + signum)

    def _restore_sigterm_handler(self) -> None:
        if self._old_sigterm_handler is not None and threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm_handler)
            except ValueError:
                pass
            self._old_sigterm_handler = None

    def _send_and_pace(self, data: bytes) -> None:
        """`send()`s `data`, scheduling a repeat-pump alarm if the tx_fifo didn't have room for all
        of it. Must only be called from `simulator`'s own engine-room thread - same requirement as
        `send()`/`pump()` themselves (they touch `cdc.tx_fifo`)."""
        if not self.send(data):
            self._schedule_pump()

    def _schedule_pump(self) -> None:
        if self._pump_alarm is None:
            self._pump_alarm = self._simulator.clock.create_alarm(self._pump_tick)
        self._pump_alarm.schedule(_PUMP_PERIOD_NANOS)

    def _pump_tick(self) -> None:
        # A clock alarm callback always fires from Clock.tick()'s own call chain, i.e. already on
        # the engine-room thread - no bridging needed here either.
        if not self.pump():
            self._schedule_pump()
