from rp2040py._native_gate import raise_import_error_on_native_disabled

try:
    raise_import_error_on_native_disabled()
    from rp2040py.native._rp2040 import (
        APB_START_ADDRESS,
        DPRAM_START_ADDRESS,
        FLASH_END_ADDRESS,
        FLASH_START_ADDRESS,
        RAM_START_ADDRESS,
        RP2040,
        SIO_START_ADDRESS,
    )
except ImportError:
    from rp2040py._rp2040 import (
        APB_START_ADDRESS,
        DPRAM_START_ADDRESS,
        FLASH_END_ADDRESS,
        FLASH_START_ADDRESS,
        RAM_START_ADDRESS,
        RP2040,
        SIO_START_ADDRESS,
    )


__all__ = (
    "APB_START_ADDRESS",
    "DPRAM_START_ADDRESS",
    "FLASH_END_ADDRESS",
    "FLASH_START_ADDRESS",
    "RAM_START_ADDRESS",
    "RP2040",
    "SIO_START_ADDRESS",
)
