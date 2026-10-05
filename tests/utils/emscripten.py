import sys

import pytest

IS_EMSCRIPTEN = sys.platform == "emscripten"

# Emscripten (the Pyodide wheel test) has no real network: opening a listening or connecting socket ends in an error inside the runtime that is not an exception
# the test can catch - it kills the whole pytest process. So the tests that talk to a real socket are skipped there, by name and with the reason, rather than left
# out of the test command (pyproject.toml's pyodide section).
needs_sockets = pytest.mark.skipif(IS_EMSCRIPTEN, reason="Emscripten has no real sockets")
needs_threads = pytest.mark.skipif(IS_EMSCRIPTEN, reason="Emscripten has no threads (can't start new thread)")
needs_processes = pytest.mark.skipif(IS_EMSCRIPTEN, reason="Emscripten does not support processes")
# A synchronous call that waits for the simulation (`Simulator.call()`/`submit().result()`, `pump()`, a device's blocking start/exec) has to run the event loop from
# the caller's thread; the WebLoop can do that only when the entry point is a JSPI-promising call, which a plain pytest run is not. Pyodide users await instead.
needs_blocking_loop = pytest.mark.skipif(
    IS_EMSCRIPTEN, reason="a synchronous wait cannot run the WebLoop from a plain entry point - await instead"
)
# Wall-clock assertions at the granularity of a real timer (tens of microseconds to a second): the Emscripten clock is too coarse and too jittery for them.
needs_fine_clock = pytest.mark.skipif(
    IS_EMSCRIPTEN, reason="Emscripten's timer is too coarse for a wall-clock assertion"
)
