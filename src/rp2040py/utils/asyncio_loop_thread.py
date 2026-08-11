"""Shared "background thread hosting one persistent asyncio event loop" pattern - the fallback
"engine room" `Simulator._ensure_loop()` spins up lazily for a caller that never registered its
own loop via `bind_loop()` (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "What still needs a real
thread"): one dedicated thread, one loop, alive for as long as the owner needs it. Every caller
reaches the loop via `asyncio.run_coroutine_threadsafe()`/`call_soon_threadsafe()`, not by sharing
state directly - that's what makes a single-threaded loop like this safe to have multiple
external, non-loop threads talk to. Also useful directly for any other component that wants this
same pattern (e.g. a plain, synchronous caller hosting `GDBTCPServer` outside of any `asyncio.run()`
of its own).
"""

import asyncio
import threading

__all__ = ("start_loop_thread",)


def start_loop_thread(*, daemon: bool = True) -> "tuple[asyncio.AbstractEventLoop, threading.Thread]":
    """Creates a new event loop and starts it running forever on a new thread. Returns both - the
    loop for scheduling work onto (`run_coroutine_threadsafe`/`call_soon_threadsafe`), the thread
    in case a caller wants to know it's alive/join it during its own teardown.

    `daemon=True` (the default) matches most owners (e.g. `Simulator`): the thread just dies with
    the process, nothing needs to explicitly stop it first. `daemon=False` is for an owner that
    must keep the process alive by itself until explicitly told to stop (matching Node's
    `net.Server.listen()` semantics in upstream rp2040js) - that owner is responsible for calling
    `loop.call_soon_threadsafe(loop.stop)` and joining the thread itself during its own teardown,
    or interpreter shutdown will hang forever joining it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=daemon)
    thread.start()
    return loop, thread
