from rp2040py.rp2040 import RP2040

from .cortex_test_driver import ICortexTestDriver
from .rp2040_test_driver import RP2040TestDriver


def create_test_driver() -> ICortexTestDriver:
    # NOTE: upstream rp2040js also supports a TEST_GDB_SERVER env var to run this same test
    # suite against a real GDB server (gdbclient.ts / test-driver-gdb.ts), for testing against
    # real hardware or QEMU. That path isn't ported here - only the direct in-process RP2040 one.
    return RP2040TestDriver(RP2040())
