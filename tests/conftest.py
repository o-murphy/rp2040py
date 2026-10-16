import collections
import gc
import os
import threading

import pytest
from utils.is32bit import IS32BIT

from rp2040py.rp2040 import RP2040

rp2040_semaphore = threading.Semaphore(4 if IS32BIT else 8)


@pytest.fixture
def rp2040_factory():
    created_instances = []

    def _factory(*args, **kwargs):
        rp2040_semaphore.acquire()
        try:
            rp = RP2040(*args, **kwargs)
            created_instances.append(rp)
            return rp
        except Exception:
            rp2040_semaphore.release()
            raise

    yield _factory

    for _ in created_instances:
        rp2040_semaphore.release()

    created_instances.clear()
    gc.collect()


@pytest.fixture(autouse=True)
def _collect_between_tests_on_a_32_bit_build():
    """A chip is ~16 MB of flash and lives in reference cycles, so it is freed by the cyclic collector, not when the last name goes. On a 64-bit build the few that wait for the next automatic
    collection cost nothing; in a 32-bit address space (CI's ARMv7 and Windows x86 wheel tests) a test that builds hundreds of chips directly - the lockstep oracles do, bypassing
    `rp2040_factory` and its limit - fragments it until a 16 MB allocation fails with a MemoryError. Collecting after each test keeps the peak at one test's chips."""
    yield
    if IS32BIT:
        gc.collect()


# -- the lockstep oracles, in tiers ------------------------------------------------------------------------------------------------------------------------------------------------
# The differential oracles and parity tests (`*_diff.py`, `*parity*`) are most of the suite's time (~87 %): they hold a native block to its pure-Python reference over many random seeds, and
# prove their own sensitivity with mutants. They are decided where the code is written and checked on every push; a built wheel only needs to show that *its* compiled code, on *its* platform,
# still agrees with the reference. `--oracle` (or RP2040PY_ORACLE) picks the tier: `full` (the default: everything), `smoke` (the first parameter set of every oracle test: one seed, one
# mutant - minutes become seconds) or `off` (none, for a platform that cannot run them at all, such as Pyodide and Android).
_ORACLE_MODES = ("full", "smoke", "off")


def pytest_addoption(parser):
    parser.addoption(
        "--oracle",
        choices=_ORACLE_MODES,
        default=os.environ.get("RP2040PY_ORACLE", "full"),
        help="how much of the lockstep differential oracles and parity tests to run: full, smoke (the first parameter set of each test) or off",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "oracle: a lockstep differential oracle or a parity test (see --oracle)")


def _is_oracle(item) -> bool:
    name = os.path.basename(str(item.path))
    return name.endswith("_diff.py") or "parity" in name


def pytest_collection_modifyitems(config, items):
    mode = config.getoption("--oracle")
    for item in items:
        if _is_oracle(item):
            item.add_marker(pytest.mark.oracle)
    if mode == "full":
        return
    kept, dropped, first = [], [], collections.Counter()
    for item in items:
        if not _is_oracle(item):
            kept.append(item)
            continue
        function = item.nodeid.split("[", 1)[0]  # the test function, whatever its parameters
        if mode == "smoke" and first[function] == 0:
            first[function] += 1
            kept.append(item)
        else:
            dropped.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
