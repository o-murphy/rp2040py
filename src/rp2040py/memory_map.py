"""RP2040 address map constants.

Kept in a standalone module (rather than defined in rp2040.py) so that cortex_m0_core.py can
import them without creating a circular import: RP2040 owns/constructs a CortexM0Core, and
CortexM0Core needs these addresses for its cyclesIO() timing model.
"""

__all__ = (
    "APB_START_ADDRESS",
    "DPRAM_START_ADDRESS",
    "FLASH_END_ADDRESS",
    "FLASH_START_ADDRESS",
    "RAM_START_ADDRESS",
    "SIO_START_ADDRESS",
)

FLASH_START_ADDRESS = 0x10000000
FLASH_END_ADDRESS = 0x14000000
RAM_START_ADDRESS = 0x20000000
APB_START_ADDRESS = 0x40000000
DPRAM_START_ADDRESS = 0x50100000
SIO_START_ADDRESS = 0xD0000000
