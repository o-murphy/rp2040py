---
name: external-devices-and-boards
description: Execution checklist for adding a new ExternalDevice (LED, button, sensor, display, radio...) or a new --board-spec board to rp2040py, and how to test each - the attach-timing rule, the 3g rule (derive every number from real upstream firmware source), which test file to write, and live-boot CI verification. Use whenever asked to add/emulate a device or board, or to extend one of the boards/ example files.
---

# Adding an `ExternalDevice` or a board to rp2040py

This is the *execution* checklist. The design rationale and full API reference already live in
[docs/reference/external-devices-and-boards.md](../../../docs/reference/external-devices-and-boards.md)
(the how-to) and [docs/records/0049](../../../docs/records/0049-external-device-authoring-docs.md)
(the why) - read the reference doc first if this area hasn't come up yet this session. Don't
re-derive what it already states; this file is what to *do*, step by step, with pointers to real
in-tree examples for each step.

## Before writing anything: the 3g rule

Every electrical fact - a pin number, a chip name, a timing, a flash offset, a pull direction -
must come from a real, cited upstream source: the firmware's own board config
(`mpconfigboard.h`/`mpconfigboard.mk`, `pins.c`/`pins.csv`), the pico-sdk board header, or a real
vendor schematic. Never a datasheet's generic numbers, a product page's marketing copy, or
"probably the same as board X" (see [record 0027](../../../docs/records/0027-cyw43-wifi.md), where
this got its name). Cite the exact file/field in the docstring, next to the number it justifies -
that citation is what makes a device or board file checkable rather than folklore. If a fact
genuinely can't be sourced, say so and treat it as a documented "not modelled" gap, not a guess -
`boards/vcc_gnd_yd_rp2040/__init__.py`'s RESET-button section is the pattern to copy for that.

**Where a flash offset comes from, per family** - the one number in a board file that is easy to
derive from a plausible-looking wrong source, and did get derived wrongly once
([record 0085](../../../docs/records/0085-circuitpython-code-py-and-wifi-on-screen.md)'s finding 5,
which cost a silent drive reformat):

- **MicroPython**: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES`, the latter
  from the board's own `ports/rp2/boards/<BOARD>/mpconfigboard.h` (**not** `mpconfigboard.cmake`,
  which carries `PICO_BOARD` and feature switches); `fs_blockcount` is that size / 4096.
- **CircuitPython**: `fs_start = CIRCUITPY_FIRMWARE_SIZE + 4096` (the NVM), where
  `CIRCUITPY_FIRMWARE_SIZE` defaults to `1020 * 1024` in `ports/raspberrypi/mpconfigport.h` and is
  overridden **in the board's `mpconfigboard.mk`** (`CFLAGS += -DCIRCUITPY_FIRMWARE_SIZE=...`).
  **A board's `link.ld` is not the source**: it sizes the linker's section and can hold a
  *different* number - `raspberry_pi_pico_w` says `firmware_size = 1532k` there while its
  `mpconfigboard.mk` says `(1536 * 1024)`, and the drive follows the mk. Audited 2026-08-19: no
  board under `boards/` overrides it at all, so every one of them is the `0x100000` default - if a
  new board's `mpconfigboard.mk` *does* carry the flag, that is the number to use.
- **Kaluma**: `targets/rp2/boards/<board>/board.h`'s `KALUMA_BINARY_MAX` plus
  `KALUMA_PROG_SECTOR_BASE`/`_COUNT`, cross-checked against `board.js`'s own `new Flash(base,
  count)`.

Verify rather than trust: boot the board and read the filesystem back (`--dump-fs`, or
`-c "import os; print(os.statvfs('/'))"`). A wrong `fs_start` is silent - the firmware simply
formats its own drive where it expects one, and nothing errors.

For a board specifically: `scripts/fetch_firmware.py list --family <family> --slug <slug>
[--page <page>]` pulls a real firmware version history straight from the source
(micropython.org / CircuitPython's S3 bucket / Kaluma's GitHub releases) - use it instead of
hand-typing one pinned URL. `--page` is needed when one download page serves several
`BOARD_VARIANT` images sharing a filename prefix (MicroPython's `WEACTSTUDIO` page, e.g.); read
the script's own module docstring before using it on an unfamiliar board.

## Adding a new `ExternalDevice`

1. **Check whether this needs a new board file at all.** Attaching a device to an *existing* board
   (`pico`/`pico_w`) is zero library changes beyond the device class itself -
   `attach_external_devices(mcu, MyDevice())` before `start_async()`/`astart()`. Only write a new
   `boards/` file if the device is physically part of a specific piece of hardware.
2. **The whole contract is one method**: `attach(self, rp2040: RP2040) -> None`. Wire up whatever
   surface the device needs off the `rp2040` object it's handed - `rp2040.gpio[n].add_listener(cb)`
   for a plain pin, or the relevant peripheral object (`rp2040.spi[n]`, ...) for anything richer.
   Devices may only be attached **before** `Simulator.start_execution()` - see the reference doc's
   "attach-timing rule" section for why (a race, not a supported hot-plug).
3. **Pick a template by how much the device actually does**, in ascending complexity, and copy its
   shape rather than starting from a blank file:
   - [`src/rp2040py/external/led_mock.py`](../../../src/rp2040py/external/led_mock.py) -
     one GPIO listener, one state flag. The minimal case.
   - [`src/rp2040py/external/key_mock.py`](../../../src/rp2040py/external/key_mock.py) - a GPIO
     input, `release()` handing the pad back to whichever pull firmware configured (never force it
     high/low - that would mask a firmware bug the device exists to exercise).
   - [`src/rp2040py/external/bootsel_button.py`](../../../src/rp2040py/external/bootsel_button.py)
     - a button on a non-GPIO pad (`GPIO_QSPI_SS`), identical across every RP2040 board.
   - [`src/rp2040py/external/ws2812.py`](../../../src/rp2040py/external/ws2812.py) - decodes a
     pulse-width protocol from nothing but GPIO edge timestamps; the model for any one-wire/timing-
     coded protocol (DHT11/22, IR, servo PWM).
   - [`src/rp2040py/external/st7735s.py`](../../../src/rp2040py/external/st7735s.py) /
     [`epd2in9g.py`](../../../src/rp2040py/external/epd2in9g.py) - full SPI peripheral drivers, the
     richer end. Both hand the caller **raw pixels/bytes** via an `on_frame` callback and decode
     nothing themselves (no image library in `src/` - see
     [record 0046](../../../docs/records/0046-epd2in9g-external-device.md)); copy that boundary for
     any new display device rather than adding a decode dependency.
   - [`src/rp2040py/external/cyw43/`](../../../src/rp2040py/external/cyw43/) - a whole chip as a
     package (`bus.py`/`chip.py`/`nat.py`...), the only case in this project genuinely big enough to
     need one. Default to a single flat file under `external/`; only reach for a sub-package when a
     device is this kind of multi-file system in its own right (see CLAUDE.md's "Module layout").
4. **Module placement**: one flat file, `src/rp2040py/external/<name>.py`, unless step 3 says
   otherwise - **if the device is reusable or you intend the board to graduate into
   `boards.BOARDS`** (0059's promotion checklist item 4 requires this: "its devices already in
   `rp2040py.external`"). A device that is genuinely unique to one board and not meant to be
   shared may instead live alongside it under `boards/<slug>/devices/` - that board then simply
   isn't eligible for promotion without first moving the device out, which is exactly what item 4
   is there to catch. Most devices belong in `rp2040py.external` regardless, since most are
   reusable across boards by nature (an LED, a button, a display controller); this exception is for
   the genuinely one-off case, not a way to avoid the shared location by default.
5. **Write the unit test** - see "Testing" below before considering the device done.
6. **Update the surrounding docs** - see "Don't forget the surrounding docs" below; a device isn't
   done once tests pass, only once it's discoverable.

## Adding a new board

1. **Read the two scenarios in the reference doc** - "your own device mix, existing firmware" vs.
   "a fully custom board (your own firmware)" - and pick the matching one; don't hand-build a
   `BoardSpec` from scratch when `dataclasses.replace(BOARDS["pico"], extras=(...))` already covers
   what's needed.
2. **Copy the closest existing example** rather than starting blank:
   - [`boards/weactstudio/`](../../../boards/weactstudio/__init__.py) - several `BoardSpec`s (one
     per flash-size `BOARD_VARIANT`), built entirely from generic in-tree devices, no new device
     class needed. The pattern for "just a different flash chip/pin breakout on otherwise-plain
     hardware."
   - [`boards/vcc_gnd_yd_rp2040/`](../../../boards/vcc_gnd_yd_rp2040/__init__.py) - one firmware
     family declared because upstream only builds one for this board, `--image` used to run a
     *different* firmware that merely boots on the hardware without being built for it (never
     declare a `firmware` key that claims something upstream never shipped). Carries a real device
     (`Ws2812`) and documents a genuine "not modelled" gap (RESET) rather than faking it.
   - [`boards/waveshare_rp2040_lcd_0_96/`](../../../boards/waveshare_rp2040_lcd_0_96/__init__.py) -
     two firmware families (MicroPython + CircuitPython) in one file, and the `board_with(on_frame)`
     closure pattern for the one thing a bare `--board-spec` target can't do on its own: hand a
     constructed device's callback back to an SDK caller (`BoardSpec.extras` is zero-arg factories,
     so nothing else can deliver one).
3. **Directory naming**: one directory per *board*, not per firmware family, named after the
   firmware's own board id, case-normalized (`weactstudio` for MicroPython's
   `ports/rp2/boards/WEACTSTUDIO`). Where MicroPython and CircuitPython disagree on the id, pick one
   for the directory and cite both in the docstring next to where each number came from.
4. **Never set `image` by hand** in a board file - only `firmware[family].fw`/`.default_tag`/
   `.layout`. `image`/`layout` are the *resolved* slots `resolve_firmware()` fills in at use time
   (see [record 0059](../../../docs/records/0059-boardspec-firmware-resolution.md)); a board file
   that pre-resolves defeats the entire "nothing downloads at import time" property.
5. **If this board is meant to graduate into `boards.BOARDS`** (real `--board` support, ships in
   the package) rather than staying an example under `boards/`, it needs all five items on 0059's
   own promotion checklist - the 3g rule above (item 1), a `firmware_specs.json` entry with ≥1
   release URL per family (item 2), a live-boot CI check (item 3, see "Testing" below), its devices
   already living in `rp2040py.external` rather than hidden in the board file (item 4), and a named
   maintainer for the row (item 5). Most new boards should stay in `boards/` as examples - that's
   the intended steady state, not a waiting room; ask before assuming a board should be promoted.
6. **Update the surrounding docs** - see "Don't forget the surrounding docs" below; a board isn't
   done once it live-boots, only once it's discoverable.

## Testing

Match the test to what actually needs proving - these are additive, not alternatives:

1. **Device unit test** - `tests/test_<device>.py`. Construct a bare `RP2040` (the
   `rp2040_factory` fixture from `tests/conftest.py`), `attach()` the device, then drive its inputs
   directly through real memory-mapped registers the same way firmware would - **not** through
   `set_input_value()`, which models an *external* drive rather than firmware's own
   `machine.Pin(...).value()` path. Two real templates, by how much timing precision matters:
   - [`tests/test_led_mock.py`](../../../tests/test_led_mock.py) - the minimal shape: a
     `_drive_gpio_high()` helper flips SIO's `GPIO_OUT`/`GPIO_OE` bits and calls
     `pin.check_for_updates()`, then asserts the device's resulting state.
   - [`tests/test_ws2812.py`](../../../tests/test_ws2812.py) - real-timing protocols: build test
     waveforms from a **real driver's own published timings** (a specific PIO program's cycle
     counts at its real clock), never round numbers - "testing against upstream's own numbers is
     the point: a test written in the decoder's own preferred units would pass no matter how wrong
     the threshold was" (the file's own docstring). Drives through `clock.tick()` between edges.
2. **Protocol-level test** (only if touching the shared plumbing itself, not a specific device) -
   [`tests/test_external_device.py`](../../../tests/test_external_device.py): `attach_external_devices()`
   calls `attach()` on each device in order, with no device, and with the MCU passed through.
3. **Board registry/resolution test** -
   [`tests/test_boards.py`](../../../tests/test_boards.py): monkeypatch `_retrieve` (from
   `rp2040py.utils.firmware_retrieve`) rather than letting it run - these tests are about
   `resolve_firmware()`/`resolve_layout()`'s *decisions*, not about actually downloading anything.
4. **CLI `--board-spec` argument-handling test** (only for new CLI-level validation, not per-board)
   - [`tests/test_cli_board_spec.py`](../../../tests/test_cli_board_spec.py): hand-built
     `argparse.Namespace`s against `_load_board_spec_target()`/`_resolve_board()`, no real network,
     no full `cli.main()`/simulator boot.
5. **Live-boot verification - the actual gold standard, and required before calling a board file
   done.** A real firmware image, booted end to end through the board file exactly as a user would:
   ```sh
   rp2040py micropython --board-spec boards/<name>/__init__.py:BOARD -c "<a probe script>"
   rp2040py micropython --circuitpython --board-spec boards/<name>/__init__.py:BOARD -c "<probe>"
   ```
   probing something that only a correct flash layout + correct device wiring can produce - real
   examples from CI: `os.statvfs('/')` (proves flash layout), `board.DISPLAY.width` (proves the
   display device + pin map), a WS2812 write decoded back out of the PIO waveform it actually
   produced ([`tests/ws2812_boot_decode.py`](../../../tests/ws2812_boot_decode.py)). See
   [`tests/pico_spec.py`](../../../tests/pico_spec.py) for a fully-worked `--board-spec` file used
   purely as a live-boot proof, and `.github/workflows/ci-micropython.yml`'s `test-board-spec` job
   for how this project wires these into CI permanently (`timeout 5m`, `--littlefs`/`--image`
   pinned, `--expect-text`/inline `-c` probes) - a new board graduating past "local smoke test" adds
   its own step there, following the existing ones' shape.

Finish with `uv run pre-commit run --all-files` (mypy/ruff/pytest, both builds) per CLAUDE.md, same
as any other change in this repo.

## Don't forget the surrounding docs

The code + a record isn't the whole job - a new board or device that live-boots but stays invisible
everywhere else is easy to lose track of later. Every prior board addition
([0068](../../../docs/records/0068-waveshare-rp2040-zero-board.md)-
[0077](../../../docs/records/0077-pimoroni-tiny2040-board.md)) touched this same set; check each
one rather than assuming a doc is out of scope because it isn't code:

- **`CHANGELOG.md`** - a bullet under `## [Unreleased]` -> `### Added`, same shape as the existing
  board/device entries there (what was added, the key sourced facts, a link to the new record).
- **`docs/0000-TRACKER.md`** - a new row under `### Implemented` (with a `[N]: records/...` link at
  the bottom of the file) - `[0077]` for this exact board is a real example to copy. If the board/
  device was picked off an open survey record (e.g. [0066](../../../docs/records/0066-board-support-expansion.md)),
  fold the result back into *that* record's own checklist too (`[ ]` -> `[x]`, pointing at the new
  record) and update its own tracker row's summary if it still says "documented, not implemented".
- **For a new board specifically**: `docs/reference/external-devices-and-boards.md`'s "Ready-made
  examples in this repo" table needs a new row, and `README.md` has **two** separate mentions of the
  current example-board count ("N worked `--board-spec` targets"/"N worked `--board-spec` examples")
  that both need bumping - `grep -n "worked .--board-spec"  README.md` finds both.
- **For a new device specifically**: `README.md`'s "Devices already shipping in-tree this way"
  sentence (`### External devices & custom boards`) lists every device by name - add the new one.

Do this before running the final `pre-commit` pass below, not as an afterthought after - it's easy
to consider the work "done" once live-boot verification passes and stop there.

## Caveats worth re-reading before assuming something is a bug

The reference doc's own "Caveats worth knowing" section - `LEDMock` on `pico_w` being a deliberate
placeholder (not real wiring), `BoardSpec.extras` being zero-arg factories only (nothing hands a
constructed device back to a CLI caller - `board_with()` is the only escape, and only from the
SDK), PIO's one-instruction-per-CPU-instruction ceiling, and `ExternalDevice` having no `detach()`/
reset hook at all. Check there before treating any of these as something to fix.
