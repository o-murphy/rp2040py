# 0109 - what the Python <-> core border costs, and what a device written in a script costs: measurements

Status: **measurement note (2026-11-11), nothing decided and nothing built in the package.** Asked for by the maintainer: building one wheel per Python version and platform is very expensive, so how much slower would the core be behind a plain C ABI (ctypes) than behind Cython, and what are the alternatives, including for external devices that should be writable in any language. Everything here was measured in a throwaway tree; what was kept is under `scripts/bench/binding_overhead/` and `scripts/bench/script_devices/`, both manual benchmarks, not tests. Number 0109 because 0108 (`docs/records/0108-drop-cp310-binary-wheel.md`) is on the branch `drop-cp310-wheel`, which is not on `main` yet. Related: [0096](0096-cpp-mcu-core.md) (the split, the WASM host cost), [0102](0102-c-abi-for-the-core.md) (the C ABI), [0103](0103-wasm-backend.md) (the wasm figures this repeats on this machine).

## What was measured, and how

One machine (6 cores, Linux, CPython 3.10.22 in the project's `.venv`), one run per figure unless a "best of" says otherwise, `-O2`. The sources are in the tree; each figure below names the script that gives it.

### 1. The core's own loop is the same behind ctypes, nanobind and Cython

`synthetic.py`'s Thumb loop (8.4 M instructions, run in slices of up to a million), the final register r2 equal in every way (`0x67700000`):

| way in | Minstr/s |
|---|---|
| Cython (`scripts/bench/synthetic.py`; `wasm_core.py` gave 79.3 in the same hour) | 78.6 |
| the C++ core through ctypes (`wasm_core.py`) | 90.8 |
| the same through ctypes, `binding_overhead/nanobind_vs_ctypes.py` | 91.7 |
| the same through nanobind (same script) | 91.7 - 91.8 |
| the wasm module on JavaScriptCore (`wasm_core.py`; wasmtime, Node, Bun and wasm3 were not installed that day) | 45.6 |
| pure Python | 0.35 |

The core runs in batches, so the binding is called a few times per million instructions and its price disappears. Cython is a little behind the ctypes row; the two are built with different flags (the extension's own against bare `-O2`), which is the likely reason and was not isolated ([0103](0103-wasm-backend.md) saw a gap of the same kind between the same two rows and names the flags too).

### 2. One call across the border is where the bindings differ

(`binding_overhead/call_overhead.py`, `nanobind_vs_ctypes.py`; 2 M calls of a trivial function, a register read.)

| | ns per call |
|---|---|
| Cython attribute read (`core.pc`) | 45 - 52 |
| nanobind | 40 |
| ctypes, `argtypes` set | 280 - 300 |
| a C -> Python callback through `ctypes.CFUNCTYPE` | 218 |

`rp2040py bench` (`demo/benchmark.py`) in its synthetic mode calls the native core once per instruction from Python: 18.5 M instructions/s on the native build (~54 ns a call, the Cython row above) and 0.52 M on the pure build. CoreMark (`demo/coremark_single_thread.py`) gives 248.5 with a correct CRC in 27 s of wall time. A boot-to-banner run of `bench --expect-text MicroPython` printed "simulated 56,859 s in 0.01 s" on the native build and "0.016 s in 1.50 s" on the pure one; the two cannot both be a boot time (an idle jump is the likely reason), so that figure is **not used** and was not investigated.

### 3. A device in a script: one call from a C host

A toy device (`on_write(t, b, cb)`: a 32-bit state, an integer returned, `set_pin` called back into the host on odd bytes) in four languages, embedded in a C host; 1 M calls, best of 5, `scripts/bench/script_devices/call/`. The four results agree (checksum 250000, 1,750,000 `set_pin` calls).

| | ns per call, computes only | ns per call, with the callback |
|---|---|---|
| C (call through a pointer; the floor) | 2.3 | 2.7 |
| Lua 5.4.8 | 50 - 53 | 66 - 69 |
| QuickJS-ng 0.18.0 | 100 - 104 | 140 - 149 |
| MicroPython 1.29.0 (embed port) | 183 - 194 | 247 - 264 |

### 4. A real device: the project's ST7735S over a recorded firmware stream

`demo/lcd_run.py` (MicroPython and the vendor driver of the Waveshare RP2040-LCD-0.96) was run as it is with `St7735s` instrumented: 51,311 events (51,242 data bytes, 22 commands, 45 CS rises, 2 resets), two frames. The vendor driver paints two flat frames, so the pixel bytes were replaced by fixed pseudo-random bytes (a wrong address would otherwise go unseen); the commands, windows and CS/DC/RST sequence are the real ones. The class of the project (`external/st7735s.py`) is the reference. The same command parser and framebuffer writer (MADCTL mapping, the +26/+1 offsets, flush at the end of the window or on CS rising) was ported to C, Lua, QuickJS and MicroPython and replayed in a C host; **every port ends with the reference's frame buffer (FNV-1a 2839961155) and the same flush count**. 10 passes of the stream, `scripts/bench/script_devices/lcd/`, and `run.sh` run twice from an empty directory (the second figure of each pair):

| | ns per event | ms per pass (2 frames) | vs. the project's Python (logic only) |
|---|---|---|---|
| C | 5.1 - 5.2 | 0.26 | ~270x faster |
| Lua | 185 - 193 | 9.5 - 9.9 | 7.4 - 7.9x faster |
| QuickJS-ng | 284 - 297 | 14.6 - 15.3 | 4.8 - 5.2x faster |
| CPython 3.10, the class's `_on_command` / `_on_data` | 1425 - 1466 | 73 - 75 | 1x |
| MicroPython | 1592 - 1670 | 82 - 86 | 0.85 - 0.92x (**slower**) |
| CPython, the class's whole `_on_transmit` | 2077 - 2171 | 107 - 111 | 0.66x |
| (an empty replay loop in CPython, to take off the Python rows) | 134 - 157 | 7 - 8 | |

Read the "logic only" row, not the whole `_on_transmit`: it also computes the byte time and schedules the alarm, which the ports do not do (the alarm is the core's clock, behind a native call in the real simulator). A frame costs the project's Python device about 55 ms (37 ms logic only); the demo's whole wall time was ~2 s for its two frames, so for this workload the device is a small part of it. A display repainted at 30 frames a second would not be: ~1.1 - 1.7 s of Python per simulated second (arithmetic, not measured).

## What was read, not measured

- **Why `cp310` is not in abi3** ([setup.py](../../setup.py) docstring): typed memoryviews need `Py_buffer`'s fields, which entered the limited API in 3.11; forcing the floor to 3.10 fails to compile. Free-threaded builds cannot use `Py_LIMITED_API` at all before 3.15 (PEP 703; PEP 803 below changes that).
- **cibuildwheel 4.3.5** (latest on PyPI that day, `main` read on GitHub): CPython 3.15 and `cp315t` are built by default; the changelog never mentions `abi3t`. In `cibuildwheel/util/packaging.py` `find_compatible_wheel` reuses a wheel only for an `abi3` tag and **not for a free-threaded identifier** (`tag.abi == "abi3" and not free_threaded`); `is_abi3_wheel` (what runs `abi3audit`) looks only for `abi3`.
- **PEP 803 (Final, Python 3.15)**: the free-threaded stable ABI, tag `abi3.abi3t` (a wheel `cp315-abi3.abi3t` for 3.15 and later, GIL-enabled and free-threaded), a floor of 3.15 (no `cp314-abi3t`), `PyModExport` instead of `PyModuleDef` (PEP 793), an opaque `PyObject`, the extension file named `*.abi3t.so`. nanobind cannot adopt it yet: the PEP quotes its maintainer on a missing API (a pointer to the data of a `PyVarObject`-derived object). Taken together with the previous point, a GIL-enabled build would reuse an `abi3.abi3t` wheel as an abi3 one, a free-threaded build of a later version would not, and nobody has tried it.
- **Cython and `abi3t`**: `CHANGES.rst` (master) says the 3.15 Limited API work "does not yet cover `abi3t`"; the latest release is 3.3.1 (the project asks for `>=3.2.9`). Two open issues: [#7399](https://github.com/cython/cython/issues/7399) (the tracker; a preview branch `freethreading-limited-api-preview` merged from unmerged pull requests; its known limits: `cdef extern class` unlikely to work, **~13 % slower** than `Py_LIMITED_API=3.14` because every `cdef class` attribute access goes through an indirect lookup, some thread-safety guards missing; an mpi4py author reported the build passing its tests on a GIL and a free-threaded 3.15) and [#7905](https://github.com/cython/cython/issues/7905) (`CYTHON_USE_MODULE_STATE=1` with `Py_TARGET_ABI3T` does not compile; `cdef class` attribute access outside the GIL together with module state "probably won't be fixed soon"). `setup.py` does not set `CYTHON_USE_MODULE_STATE`; the extension has 40 `cdef class`es. Nothing from that branch was built here.
- **nanobind and abi3**: only from CPython 3.12 (the maintainer's statement; not checked against nanobind's documentation here). Cython's own abi3 build is also slower below 3.12 for want of vectorcall (`setup.py`'s docstring: ~1.9x on a one-call-per-instruction loop, nothing measurable for the batch engine); the floor is 3.11 "by decision".
- **`publish.yml`**: `aarch64`/`armv7l` build on the native `ubuntu-24.04-arm`, Windows ARM64 on `windows-11-arm`, macOS arm64 on `macos-26`; **QEMU is used for `i686` only** (`needs_qemu: true`, lines 96-99), which has no native runner. `pyproject.toml`'s `[tool.cibuildwheel] build` still lists `cp310-*` on `main` (0108 removes it).

## What was wrong on the way (kept, as a negative result)

- **First check-sums of §3 and §4 were all zero**: `b + t` is always even for `b = i & 0xFF`, `t = i`, so the state never changed and the agreement proved nothing. The formula takes `b >> 1` now.
- **MicroPython's embed port at `CONFIG_ROM_LEVEL_MINIMUM` has no `bytearray` or `max`**: the port's script raised `NameError`, and the harness then hung (an exception raised through `nlr` with no handler). Moved to `CORE_FEATURES`, with `MICROPY_PY_IO` and `MICROPY_ENABLE_EXTERNAL_IMPORT` off (the embed port has no file reader); see `script_devices/mpconfigport.h`. The call figures of §3 for MicroPython were taken with both configurations (194 and 183 ns).
- **"A wasm host call costs more than the Python border" was not shown.** It came from [0096](0096-cpp-mcu-core.md) §4 (4 us wasm3 to ~200 us Node a call), which was measured **through the Python binding `wasmhost`**, so it says what a Python host pays, not what a C++ host with an embedded wasm runtime would. The embedded cost was not measured.
- **The first reading of cibuildwheel** ("a wheel tagged `abi3t` is ignored") was corrected once PEP 803's compound tag `abi3.abi3t` was read (see above).
- A claim that QEMU still builds the `aarch64` wheel was wrong: only `i686`.

## What was not done

- The `Simulator` through ctypes or nanobind: the prototype has six functions (`boot`, `erase_flash`, `run_n`, `register`, `set_register`, `write_sram`), not the 27 shells. Callbacks into Python through nanobind were not measured.
- The real crossing of a real run: the per-byte SPI callback and the alarm of the core into `St7735s` were not counted; §4 replays the stream without the core.
- An embedded CPython in the same C host as §3 / §4 (the Python rows are the project's class driven from Python, which also pays the Python loop; the empty-loop row is the way to take that off). An embedded wasm runtime (wasmtime's C API). LuaJIT. `-O3`. A device with a feedback line (a BUSY pin, an I2C/SPI reply). Real picture data.
- No change to the package, its build, the wheel matrix or the fallback.

## What it suggests (leanings, not decisions)

1. **The price is the number of crossings and the amount of Python run, not the binding tool**: 40 - 300 ns a crossing, ~1.4 us a byte for the ST7735S in Python. nanobind removes only the first part. Lua is the cheapest script to embed (~190 ns a byte for the same device), embedded MicroPython is not a speed-up over CPython (it is a way to Python syntax without CPython's ABI).
2. **Two tiers of device** would follow the way the peripherals are already built (a C++ block and a Python reference held to it by an oracle): the devices the project ships native, a scripting or Python API kept for a user's own, slow ones; the Python `ExternalDevice` stays the contract so nothing a user wrote breaks.
3. **A device with no reply line (a display) does not need a call a byte**: the core could hand it the bytes between two CS edges in one call (the real stream is a DMA burst). Not tried; it would change `ExternalDevice`.
4. **For the wheel matrix**: nothing here shows a binding that cuts the number of wheels (nanobind has the same per-version cost below 3.12 and its abi3 floor is 3.12; a `py3-none-<platform>` wheel behind ctypes/cffi would, at 280 ns a crossing). Raising the abi3 floor to 3.12 would also give Cython vectorcall (not measured here); `cp315t` stays a separate wheel until Cython releases `abi3t` support.
5. **A silent fallback** (`rp2040.py:14` takes the pure-Python build on an `ImportError` without a word, ~35x slower on the synthetic loop) is what a thinner wheel matrix makes likely; a one-line warning there is proposed, not done.

## To repeat it

```sh
# the binding border (needs c++, cc, cmake, ninja, and `pip install nanobind` into the interpreter that runs the scripts)
c++ -std=c++17 -O2 -fno-exceptions -fno-rtti -ffp-contract=off -shared -fPIC -Isrc/rp2040py/native/core \
    src/rp2040py/native/wasm/rp2040_wasm.cpp -o /tmp/work/librp2040.so
cc -O2 -shared -fPIC scripts/bench/binding_overhead/callback.c -o /tmp/work/libcallback.so
python scripts/bench/binding_overhead/call_overhead.py /tmp/work
cmake -S scripts/bench/binding_overhead/nanobind -B /tmp/work/nb -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DPython_EXECUTABLE=$(which python) -Dnanobind_DIR=$(python -c "import nanobind; print(nanobind.cmake_dir())")
cmake --build /tmp/work/nb
RP2040PY_SKIP_CYTHON=1 python scripts/bench/binding_overhead/nanobind_vs_ctypes.py /tmp/work /tmp/work/nb

# the devices in scripts (downloads Lua and quickjs-ng; needs a MicroPython checkout; about two minutes)
scripts/bench/script_devices/run.sh /tmp/work/devices /path/to/micropython
```
