# Type stub for the compiled Cython module: it mirrors the pure-Python reference
# (rp2040py._rp2040) exactly, so the types live there (single source of truth) and are re-exported here.
# Lets mypy/IDEs see the native backend's API instead of the ignore_missing_imports fallback.

from rp2040py._rp2040 import (
    APB_START_ADDRESS as APB_START_ADDRESS,
)
from rp2040py._rp2040 import (
    DPRAM_START_ADDRESS as DPRAM_START_ADDRESS,
)
from rp2040py._rp2040 import (
    FLASH_END_ADDRESS as FLASH_END_ADDRESS,
)
from rp2040py._rp2040 import (
    FLASH_START_ADDRESS as FLASH_START_ADDRESS,
)
from rp2040py._rp2040 import (
    RAM_START_ADDRESS as RAM_START_ADDRESS,
)
from rp2040py._rp2040 import (
    RP2040 as RP2040,
)
from rp2040py._rp2040 import (
    SIO_START_ADDRESS as SIO_START_ADDRESS,
)
