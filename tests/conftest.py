import gc
import struct
import threading

import pytest

from rp2040py.rp2040 import RP2040

rp2040_semaphore = threading.Semaphore(4 if struct.calcsize("P") * 8 <= 32 else 8)


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
