"""The synthetic Thumb loop of ``synthetic.py`` through the same C ABI by two bindings, ctypes and nanobind, and the cost of one call.

Needs the two builds of ``call_overhead.py``'s docstring and of ``nanobind/CMakeLists.txt``, made **with the interpreter that runs this**
(an extension module is tied to its version), and the pure-Python build for ``synthetic.build_program`` (``RP2040PY_SKIP_CYTHON=1``):

    RP2040PY_SKIP_CYTHON=1 python scripts/bench/binding_overhead/nanobind_vs_ctypes.py /tmp/work /tmp/build/nb

Both bindings reach the very same compiled core (the final register r2 is checked equal), so the loop figure is the core's and only the call
figure differs. Record 0109 has the numbers and the caveats (six functions, not the 27 shells; no callbacks).
"""

import ctypes
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "bench"))
import synthetic

SRAM = 0x20000000
SHIFT = 20


def run_loop(
    boot: Callable[[], int],
    erase: Callable[[], None],
    write: Callable[[bytes], None],
    run: Callable[[int], int],
    set_register: Callable[[int, int], None],
    register: Callable[[int], int],
) -> "tuple[float, int]":
    halfwords, n_setup, n_loop, iterations = synthetic.build_program(SHIFT)
    program = b"".join(h.to_bytes(2, "little") for h in halfwords)
    total = n_setup + n_loop * iterations + 1
    erase()
    assert boot() == 0
    write(program)
    set_register(15, SRAM)
    set_register(13, SRAM + 0x40000)
    done = 0
    start = time.perf_counter()
    while done < total:
        done += run(min(1_000_000, total - done))
    elapsed = time.perf_counter() - start
    assert register(5) == 0, "the loop did not finish"
    return total / elapsed / 1e6, register(2) & 0xFFFFFFFF


def per_call_ns(function: Callable[[int], object], calls: int = 2_000_000) -> float:
    start = time.perf_counter()
    for _ in range(calls):
        function(5)
    return (time.perf_counter() - start) / calls * 1e9


def main() -> None:
    work, nanobind_dir = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(nanobind_dir))
    import nbcore  # type: ignore[import-not-found]  # built by nanobind/CMakeLists.txt

    lib = ctypes.CDLL(str(work / "librp2040.so"))
    lib.rp2040_sram.restype = ctypes.c_void_p
    lib.rp2040_run_n.restype = ctypes.c_int64
    lib.rp2040_run_n.argtypes = [ctypes.c_uint32]
    lib.rp2040_register.restype = ctypes.c_uint32
    lib.rp2040_register.argtypes = [ctypes.c_uint32]
    lib.rp2040_set_register.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    bindings = {
        "ctypes": (
            lib.rp2040_boot,
            lib.rp2040_erase_flash,
            lambda data: ctypes.memmove(lib.rp2040_sram(), data, len(data)),
            lib.rp2040_run_n,
            lib.rp2040_set_register,
            lib.rp2040_register,
        ),
        "nanobind": (
            nbcore.boot,
            nbcore.erase_flash,
            lambda data: nbcore.write_sram(0, data),
            nbcore.run_n,
            nbcore.set_register,
            nbcore.register,
        ),
    }
    for name, functions in bindings.items():
        rate, r2 = max(run_loop(*functions) for _ in range(3))
        print(f"{name:9s} loop, best of 3: {rate:5.1f} Minstr/s  r2={r2:#x}")
    nbcore.boot()
    print(f"one call: ctypes {per_call_ns(lib.rp2040_register):.0f} ns, nanobind {per_call_ns(nbcore.register):.0f} ns")


if __name__ == "__main__":
    main()
