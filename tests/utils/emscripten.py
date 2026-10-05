import sys

import pytest

IS_EMSCRIPTEN = sys.platform == "emscripten"

# Emscripten (the Pyodide wheel test) has no real network: opening a listening or connecting socket ends in an error inside the runtime that is not an exception
# the test can catch - it kills the whole pytest process. So the tests that talk to a real socket are skipped there, by name and with the reason, rather than left
# out of the test command (pyproject.toml's pyodide section).
needs_sockets = pytest.mark.skipif(IS_EMSCRIPTEN, reason="Emscripten has no real sockets")
