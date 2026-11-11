/* Derived from MicroPython's examples/embedding/mpconfigport.h (docs/records/0109-binding-and-device-runtime-measurements.md).
 * This file is part of the MicroPython project, http://micropython.org/
 * The MIT License (MIT)
 * Copyright (c) 2022-2023 Damien P. George
 */

// Include common MicroPython embed configuration.
#include <port/mpconfigport_common.h>

// CORE_FEATURES, not MINIMUM: the minimum level has no `bytearray`/`max`, which the device scripts use.
#define MICROPY_CONFIG_ROM_LEVEL                (MICROPY_CONFIG_ROM_LEVEL_CORE_FEATURES)

// MicroPython configuration.
#define MICROPY_ENABLE_COMPILER                 (1)
#define MICROPY_ENABLE_GC                       (1)
#define MICROPY_PY_GC                           (1)
#define MICROPY_PY_SYS                          (0)

// The embed port has no file reader or `open`; CORE_FEATURES turns both on, so switch them off (the link fails otherwise).
#define MICROPY_PY_IO                           (0)
#define MICROPY_ENABLE_EXTERNAL_IMPORT          (0)
