# Devices written in a scripting language, measured

The measurement behind [record 0109](../../../docs/records/0109-binding-and-device-runtime-measurements.md): what it costs
for a C/C++ host (the shape of the C++ core) to call a device written in Lua, QuickJS or MicroPython, against the same device
in C and in the project's own Python.

```sh
scripts/bench/script_devices/run.sh /tmp/work /path/to/micropython
```

| path | what |
|---|---|
| `call/` | a toy device (`on_write(t, b, cb)`, a 32-bit state, an optional callback into the host); ns per call |
| `lcd/` | the `St7735s` command parser and framebuffer writer, ported, replayed over the recorded stream; checked against the reference |
| `record_lcd_trace.py` | records the SPI/DC/CS/RST events `demo/lcd_run.py` drives into `St7735s` |
| `lcd_reference.py` | the project's `St7735s` over that stream: the expected result and the Python timings |
| `mpconfigport.h` | the MicroPython embed configuration |

Not part of the test suite or CI: a manual benchmark. It downloads Lua and quickjs-ng and needs a MicroPython checkout.
`../binding_overhead/` is its companion for the Python <-> core border (ctypes, nanobind, Cython).
