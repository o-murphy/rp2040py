"""Phase 6 of docs/records/0096-cpp-mcu-core.md, the measurement of docs/records/0103-wasm-backend.md: the same synthetic Thumb program as `synthetic.py` (8 instructions per iteration, the
final registers checked) run by every way of running the core, so the same number means the same work:

    cython   the Cython build under the Python `Simulator`     (`synthetic.py`)
    python   the pure-Python build                              (`synthetic.py` with RP2040PY_SKIP_CYTHON=1)
    cpp      the C++ core of the wasm module's source, built natively and called through ctypes (the same C ABI as the wasm module - the ceiling of "the core with a thin host")
    wasm     the module built by `build_wasm.py`, on every engine `wasmhost` can start here

    python scripts/bench/wasm_core.py [--shift 20] [--slow-shift 16] [--repeat 3]

The program runs from SRAM, as in `synthetic.py`; the slice is the module's own `rp2040_run_n` (up to a million instructions, never past the program's end), so the figure includes the host's call per slice. Best of `--repeat`.
"""

import argparse
import ctypes
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic

SRAM = 0x20000000


def program_bytes(shift: int) -> "tuple[bytes, int]":
    halfwords, n_setup, n_loop, iterations = synthetic.build_program(shift)
    return b"".join(h.to_bytes(2, "little") for h in halfwords), n_setup + n_loop * iterations + 1


class Core:
    """The module's C ABI, whichever way it is reached: `call(name, *args)` and `memory_write(address, data)`."""

    def call(self, name: str, *args: int) -> int:
        raise NotImplementedError

    def write(self, address: int, data: bytes) -> None:
        raise NotImplementedError


class WasmCore(Core):
    def __init__(self, wasm: bytes):
        import wasmhost

        self.exports = wasmhost.Instance(wasmhost.Module(wasm), {}).exports
        self.exports._initialize()

    def call(self, name, *args):
        return getattr(self.exports, name)(*args)

    def write(self, address, data):
        self.exports.memory.write(address, data)


class NativeCore(Core):
    def __init__(self, library: Path):
        self.lib = ctypes.CDLL(str(library))
        for name in ("rp2040_sram", "rp2040_flash"):
            getattr(self.lib, name).restype = ctypes.c_void_p
        self.lib.rp2040_run_n.restype = ctypes.c_int64
        self.lib.rp2040_run_n.argtypes = [ctypes.c_uint32]
        self.lib.rp2040_register.restype = ctypes.c_uint32
        self.lib.rp2040_set_register.argtypes = [ctypes.c_uint32, ctypes.c_uint32]

    def call(self, name, *args):
        return getattr(self.lib, name)(*args)

    def write(self, address, data):
        ctypes.memmove(address, data, len(data))


def run_program(core: Core, shift: int) -> "tuple[float, int]":
    """(instructions per second, r2) - boots the chip, writes the program to SRAM, runs it for exactly its length (the ceiling is a million instructions, so up to a slice more)."""
    program, total = program_bytes(shift)
    core.call("rp2040_erase_flash")
    assert core.call("rp2040_boot") == 0
    core.write(core.call("rp2040_sram"), program)
    core.call("rp2040_set_register", 15, SRAM)
    core.call("rp2040_set_register", 13, SRAM + 0x40000)
    done = 0
    start = time.perf_counter()
    while done < total:
        ran = core.call(
            "rp2040_run_n", min(1000000, total - done)
        )  # exactly the program: a slice past its end would run the empty memory behind it, off the end of the SRAM
        assert ran > 0, "the core faulted"
        done += ran
    elapsed = time.perf_counter() - start
    if core.call("rp2040_register", 5) != 0:
        raise SystemExit(f"the loop did not finish: r5={core.call('rp2040_register', 5)}")
    return total / elapsed, core.call("rp2040_register", 2) & 0xFFFFFFFF  # (the wasm ABI returns i32: signed)


def build_module(directory: Path) -> bytes:
    spec = importlib.util.spec_from_file_location("build_wasm", ROOT / "build_wasm.py")
    build_wasm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_wasm)
    return build_wasm.build(directory / "rp2040.wasm").read_bytes()


def build_library(directory: Path) -> Path:
    library = directory / "librp2040.so"
    core = ROOT / "src" / "rp2040py" / "native" / "core"
    flags = ["-std=c++17", "-O2", "-fno-exceptions", "-fno-rtti", "-ffp-contract=off", "-shared", "-fPIC"]
    subprocess.run(
        ["c++", *flags, f"-I{core}", str(ROOT / "src/rp2040py/native/wasm/rp2040_wasm.cpp"), "-o", str(library)],
        check=True,
    )
    return library


def python_build(shift: int, repeat: int, skip_cython: bool) -> "tuple[float, int]":
    env = {**os.environ, "RP2040PY_SKIP_CYTHON": "1" if skip_cython else "0"}
    out = (
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/bench/synthetic.py"), "--shift", str(shift), "--repeat", str(repeat)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    rate = float(out.split()[1]) * 1e6
    return rate, int(out.split("r2=")[1], 16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--shift", type=int, default=20, help="2**shift iterations for the fast ways (default 20: ~8.4M instructions)"
    )
    parser.add_argument("--slow-shift", type=int, default=16, help="the same for the pure-Python build")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    results: list[tuple[str, float, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for label, skip, shift in (("cython", False, args.shift), ("python", True, args.slow_shift)):
            rate, r2 = python_build(shift, args.repeat, skip)
            results.append((label, rate, r2))
        library = build_library(directory)
        best = max((run_program(NativeCore(library), args.shift) for _ in range(args.repeat)), key=lambda r: r[0])
        results.append(("cpp (ctypes)", *best))
        wasm = build_module(directory)
        import wasmhost

        for name in wasmhost.BACKENDS:
            try:
                wasmhost.set_backend(name)
                best = max((run_program(WasmCore(wasm), args.shift) for _ in range(args.repeat)), key=lambda r: r[0])
            except Exception as error:  # noqa: BLE001 - this engine is not here
                print(f"wasm/{name}: not available ({type(error).__name__})", file=sys.stderr)
                continue
            results.append((f"wasm/{name}", *best))
    expected = {r2 for label, _, r2 in results if label != "python"}
    for label, rate, r2 in results:
        print(f"{label:16s} {rate / 1e6:8.2f} Minstr/s   r2={r2:#x}")
    if len({r2 for label, _, r2 in results if label not in ("python",)}) != 1:
        print(f"WARNING: the final r2 differs between the ways: {sorted(hex(v) for v in expected)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
