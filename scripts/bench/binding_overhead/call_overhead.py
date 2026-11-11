"""The cost of one call from Python into a native core, by binding: ctypes, a Cython shell, and (given the module) nanobind.

Also the cost of a C -> Python callback through ``ctypes.CFUNCTYPE``. Record 0109 (docs/records/0109-binding-and-device-runtime-measurements.md)
has the figures and what they do and do not say.

    # the C ABI of the wasm module, built natively (the same line as scripts/bench/wasm_core.py's `build_library`)
    c++ -std=c++17 -O2 -fno-exceptions -fno-rtti -ffp-contract=off -shared -fPIC -Isrc/rp2040py/native/core \
        src/rp2040py/native/wasm/rp2040_wasm.cpp -o /tmp/work/librp2040.so
    cc -O2 -shared -fPIC scripts/bench/binding_overhead/callback.c -o /tmp/work/libcallback.so
    python scripts/bench/binding_overhead/call_overhead.py /tmp/work

The Cython number is ``core.pc`` on the native build of the checkout the interpreter imports (skipped when only the pure build is installed).
"""

import ctypes
import sys
import time
from collections.abc import Callable
from pathlib import Path

CALLS = 2_000_000


def per_call_ns(function: Callable[[], object], calls: int = CALLS) -> float:
    start = time.perf_counter()
    for _ in range(calls):
        function()
    return (time.perf_counter() - start) / calls * 1e9


def main() -> None:
    work = Path(sys.argv[1])
    callback_lib = ctypes.CDLL(str(work / "libcallback.so"))
    callback_lib.noop.argtypes = [ctypes.c_int]
    callback_lib.noop.restype = ctypes.c_int
    noop = callback_lib.noop
    print(f"ctypes call, argtypes set:               {per_call_ns(lambda: noop(1)):6.0f} ns")

    core = ctypes.CDLL(str(work / "librp2040.so"))
    core.rp2040_register.argtypes = [ctypes.c_uint32]
    core.rp2040_register.restype = ctypes.c_uint32
    core.rp2040_boot()
    register = core.rp2040_register
    print(f"ctypes call into the core (a register):  {per_call_ns(lambda: register(5)):6.0f} ns")

    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    callback_lib.call_cb.argtypes = [callback_type, ctypes.c_void_p, ctypes.c_int]
    callback = callback_type(lambda _ctx, value: value & 1)
    start = time.perf_counter()
    callback_lib.call_cb(callback, None, 1_000_000)
    print(f"C -> Python callback via CFUNCTYPE:      {(time.perf_counter() - start) / 1_000_000 * 1e9:6.0f} ns")

    try:
        from rp2040py.rp2040 import RP2040
    except ImportError:
        return
    chip = RP2040()
    cpu = chip.core
    print(f"{type(cpu).__module__} attribute read (core.pc): {per_call_ns(lambda: cpu.pc):6.0f} ns")


if __name__ == "__main__":
    main()
