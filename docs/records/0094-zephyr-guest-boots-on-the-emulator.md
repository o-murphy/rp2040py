# 0094. A Zephyr guest boots here: MicroPython's `ports/zephyr` on `rpi_pico`, and where its console went

- Status: **Note - measured end to end (2026-08-22), nothing built.** First known Zephyr-based
  image to reach its console on this emulator. Written up because it answers, for one guest, the
  question [0066](0066-board-support-expansion.md)'s 2026-08-19 note left open ("Does such an image
  run here? Unresolved") - and because the answer turned out to be about *where the console is*,
  not about whether the image runs.
- Conceived: 2026-08-22
- Related: [0066](0066-board-support-expansion.md) (the open Zephyr question this feeds; its
  CircuitPython-on-Zephyr measurement is the symptom this record offers a lead for),
  [0026](0026-main-thread-asyncio.md) (`Simulator.bind_loop()`/`execute()` - the API path used
  directly here after the `micropython` subcommand hung), [0091](0091-esp32-c3-port-feasibility.md)
  (whose "Confidence, stated first" section this borrows, inverted - everything below was run),
  [0090](0090-post-boot-nudge-is-a-newline.md) (REPL-driving mechanics, not yet applied here),
  [0032](0032-docs-restructure.md) (why this is a record and not a chat message)

## Confidence, stated first

Unlike [0091](0091-esp32-c3-port-feasibility.md), which was written from general knowledge, every
claim in the next three sections was produced by running the thing on 2026-08-22 and is
reproducible from the commands quoted. The one exception is flagged in "A lead for
[0066](0066-board-support-expansion.md)" below, which is explicitly a hypothesis: **CircuitPython's
Zephyr builds were not run in this session at all.**

The guest was built out of a MicroPython v1.28.0 release tarball against Zephyr v4.2.0, for
`-b rpi_pico`. The emulator was rp2040py 0.3.1 from PyPI, unmodified.

## It boots

```
*** Booting Zephyr OS build v4.2.0 ***
MicroPython v1.28.0 on 2026-08-22; zephyr-rpi_pico with rp2040
Type "help()" fo...
```

Sampling `mcu.core.pc` once a second for ten seconds afterwards gives the same address every time,
`0x10024ede`, which `arm-none-eabi-objdump` shows as `wfi` / `cpsie i` inside `__enable_irq` -
Zephyr's idle loop. So the guest is not stuck or faulted: it booted, printed, and is sitting in
the scheduler waiting for console input. Nothing in the emulator needed changing for that.

## Three things it took, and only one of them is the emulator's

1. **No Zephyr SDK.** `ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb` with `GNUARMEMB_TOOLCHAIN_PATH=/usr`
   builds the image with a distro `gcc-arm-none-eabi`, which saves the ~1.5 GB SDK download. The
   west workspace itself is still ~5.5 GB (`west update --narrow -o=--depth=1`).

2. **A MicroPython-side patch, unrelated to Zephyr.** `ports/zephyr` sits at
   `MICROPY_CONFIG_ROM_LEVEL_BASIC_FEATURES` and never sets `MICROPY_EMIT_THUMB`. Since
   `MICROPY_PERSISTENT_CODE_LOAD_NATIVE` derives from `MICROPY_EMIT_MACHINE_CODE`
   (`py/mpconfig.h:416`) and `MPY_FEATURE_ARCH` comes from the compiler's own `__thumb__`
   predefines (`py/persistentcode.h:56`), a native `.mpy` is rejected with `incompatible .mpy
   arch` until the thumb emitter is switched on. Noted here only so the next person does not
   mistake it for an emulator problem.

3. **The console is not on a UART.** This is the one that cost the time, and the one that matters
   to this project.

## Where the console went

`rp2040py micropython --image zephyr.uf2 script.py` hangs with no output. Driving the simulator
through the API instead - `build_rp2040("pico")`, `load_uf2`, `Simulator.bind_loop()`/`execute()`,
`mcu.core.pc = 0x10000000`, per [0026](0026-main-thread-asyncio.md) and the shape `cli/__init__.py`
uses for `run` - and hooking `mcu.uart[0].on_byte` gives **0 bytes**. So does `uart[1]`.

The reason is in MicroPython's own board overlay,
`ports/zephyr/boards/rpi_pico.overlay:10`:

```dts
chosen {
    /* Use USB CDC ACM as the console. */
    zephyr,console = &cdc_acm_uart0;
};
```

The banner above appears only after adding an overlay that puts it back on the hardware UART:

```dts
/ { chosen { zephyr,console = &uart0; }; };
```

built with `-DEXTRA_DTC_OVERLAY_FILE=...`. Nothing else changed.

## A lead for [0066](0066-board-support-expansion.md)

[0066](0066-board-support-expansion.md)'s note of 2026-08-19 measured
`adafruit-circuitpython-raspberrypi_rpi_pico_zephyr-...-10.2.1.uf2` as loading and running but
emitting "**no console output at all within 7 minutes**", and left "slow-but-progressing versus
genuinely stuck" as the open question.

This session hit that exact symptom, and it was neither: the guest was running normally and the
console bytes were going somewhere the harness was not listening. **Hypothesis, not measured
here:** CircuitPython's `zephyr-cp` builds route their console the same way MicroPython's port
does - through Zephyr's USB CDC ACM - so `--expect-text` watching a UART would see silence
indefinitely, with no amount of extra timeout changing it. Confirming it needs the CircuitPython
image actually run, which this session did not do; a cheap first check is its devicetree's
`zephyr,console` node rather than any tracing.

If that holds, 0066's question has a third answer alongside "slow" and "stuck": **fine, but
inaudible.**

## What this says about the emulator

- The RP2040 model, bootrom and flash loading carried a Zephyr guest with **no changes at all**.
  That is the substantive result; nothing about Zephyr's startup, its clock/timer init, or its XIP
  use needed anything this project does not already do.
- What did not carry is the *device layer's assumption about where a console is*. `BaseDevice`
  and the `micropython` subcommand are built around firmware whose REPL arrives over one known
  path; a guest that chooses USB CDC through Zephyr's own stack is silent to them, and silent in
  a way that looks identical to a hang.

## Open, if this is ever picked up

- [ ] Nothing here is committed to the emulator; this is a note.
- [ ] `rp2040py micropython` cannot drive a Zephyr guest today. Whether that should be a
      `--console uart0|usb` selector, a `BaseDevice` that discovers the console, or simply a
      documented "use the API" is an open design question, not decided here.
- [ ] Reading the console over Zephyr's USB CDC (rather than sidestepping it with an overlay) is
      untried. The emulator does model USB, and stock MicroPython/CircuitPython REPLs already
      arrive that way - so the gap may be Zephyr's device stack under emulation rather than the
      transport.
- [ ] Driving the REPL far enough to load a native `.mpy` and run code was not reached in this
      session. [0090](0090-post-boot-nudge-is-a-newline.md) is the relevant prior art.
- [ ] The CircuitPython-on-Zephyr hypothesis above is unverified.
