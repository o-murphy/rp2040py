# The chip is its pads: a thin border and a board as data (working note, 2026-10-28 - **set aside, nothing built**)

## Outcome (2026-10-28, the maintainer's decision): set aside; what stays open

The note started from "the wrapper is still thick" and went on to a board as data (Wokwi's `diagram.json`, a registry of part types, KiCad). It ended with a narrower reading, and everything below the line is kept **as the record of what was considered and why it was set aside**:

- **The border is thin enough for embedding.** `tests/cpp/gate_boot.cpp` is a 125-line C++ host, no Python and no Cython, that boots a stock MicroPython and reads the UART; it implements 2 of the 16 slots (`warn`, `uart_byte`). Moving more of the wrapper (NAT, the CYW43 bus glue, the USB host, GDB) into the core would be over-engineering for the gain; they stay optional host-side extras.
- **Not pursued: a Wokwi/KiCad-like board simulator** (sections 3-5 and the subsections on the board file, base boards, nets and the registry). A PCB-level simulator is not a solo-maintainer task, and for CI and local testing the imperative Python API is already the better interface (a test is code, and anyone can build a system of any complexity with it). KiCad (a netlist importer, later a plugin) was discussed and is also not planned; it would only be another input to the same "parts + lines" model.
- **What can still be done, each only on its own request:** pads for UART / SPI / I2C ([0106](../records/0106-peripheral-routing-to-pads.md)) **if** a test of the official suite needs them (`machine_uart_irq_*` want TX wired to RX); step 8 of [narrow-the-border-plan.md](narrow-the-border-plan.md) (wasm to the 16 slots) for a browser or Node demo; the packaging gaps listed in [embedding-the-core.md](embedding-the-core.md) (a contract for the call order, a build target, a C ABI beyond the wasm glue).

---


Goal: the host glue must be writable **in any language, anywhere** (Python today; JS over wasm, Rust, C tomorrow), so the border must be something a host can implement in a day. After [narrow-the-border-plan.md](narrow-the-border-plan.md) and [narrow-the-input-plan.md](narrow-the-input-plan.md) it is still 16 slots out and 84 names in, which is not thin. Per `CLAUDE.md` this is a note about what *should eventually happen*, not a go-ahead; each stage needs its own "yes". Related: [0106](../records/0106-peripheral-routing-to-pads.md) (pads for UART/SPI/I2C), [0102](../records/0102-c-abi-for-the-core.md), [0103](../records/0103-wasm-backend.md), [chip-protocol-sketch.md](chip-protocol-sketch.md), [embedding-the-core.md](embedding-the-core.md), [0049](../records/0049-external-device-authoring-docs.md) / [0067](../records/0067-external-devices-and-boards-skill.md).

## 1. The problem

The border describes **blocks**, not the chip. A real RP2040 shows the world only its pads. We keep a slot or method per bus: 9 of the 16 outward slots are `uart_byte`, `uart_baud`, `spi_transmit`, `i2c_*`, `adc_*`; most of the 35 *input* names are their answers (`complete_*`, `feed_byte`); the 20 *config* names are derived from registers the core owns; the 21 *control* names are assembly that a `boot(cfg)` in the core should do. (Python package measured 2026-10-28: ~13 k lines are the pure-Python second core, 6.2 k the Cython shells, 4.1 k bootrom data, ~5.8 k the real wrapper - `simulator`, `usb/`, `gdb/`, `external/` incl. CYW43, `cli/`.)

## 2. The idea in one line

The chip is its pads. What stays on the border (about 10-15 names): `run` / `request_stop` / time, `read32` / `write32`, `snapshot`, `boot(cfg, board)`, `pin_input` / `pin_changed`, the ADC pads, the USB wire, `warn`. UART, SPI and I2C reach the world **only through pads** ([0106](../records/0106-peripheral-routing-to-pads.md)). Cost known from 0106 (2026-10-20): a Python listener per clock edge is ~4.5x the cost of `attach` for an SPI display, so fast buses need a **native consumer** in the core that hands bytes, not edges, to the host.

## 3. A board is a file (Wokwi's `diagram.json`)

Today a board is code (`BoardSpec.extras` = Python factories). It should be data, in the format Wokwi already documents (`wokwi/wokwi-docs`, `docs/diagram-format.md`, MIT; read 2026-10-28):

```
parts:        led1 (type "led"), lcd (type "st7735s")
connections:  GP25 -> led1.A,  GP18 -> lcd.SCK,  GP19 -> lcd.MOSI
```

We use `id`, `type`, `attrs` and the first two elements of a connection; `left` / `top` / `rotate` / `hide`, wire colour and path are editor layout - kept, ignored. The Pico is `wokwi-pi-pico` (pins `GP0`-`GP22`, `GP26`-`GP28`, `GND.n`, `TP4` / `TP5` = GPIO 23 / 25), Pico W / 2 / 2 W are `board-pi-pico-w` / `-2` / `-2w`, the console is the pseudo-part `$serialMonitor`. We get existing diagrams, ~70 documented parts and `wokwi-cli lint` for free, and our boards stay openable in Wokwi.

## 4. The three holes in that file (and the answers)

| Question | Answer |
|---|---|
| **Where does a part's pin list come from?** The file says `lcd.SCK`; something must know `st7735s` has SCK, MOSI, CS, DC. | A table `type -> {pins, attrs, tier}`. The existing `ExternalDevice`s already know their pins in code: copy them once. Names follow Wokwi where it has the type, `rp2040py-*` where it has not (not checked for `St7735s`, `Epd2in9G`, `BootselButton`); every pin fact checked against the datasheet (the "3g rule"). |
| **How does a user part with host code get loaded?** `"type": "my_sensor"` says *which*, not *what it does*. | `register_part("my_sensor", impl)` in the host's program before the run. Nothing else for now. Loading by path (`--board-spec path:ATTR` exists) stays as it is; Wokwi's WASM chip API (`*.chip.json` + wasm, `docs/chips-api/`) is the language-neutral answer, later and optional. |
| **Firmware, flash, onboard parts - the format has no place for them.** | A `<name>.board.json` that **defines the board as a type** (see "The board file" below): flash size/layout, firmware per family, the board's pins and its onboard parts with their fixed nets (Pico W's CYW43, the QSPI flash, an onboard LED). `diagram.json` stays pure Wokwi. |

Device tiers behind the registry (a board is a registry type too): **core** (hot and common: CYW43 gSPI, UART console, LED / button / WS2812, usual SPI/I2C slaves - mounted with no host code), **host** (niche or OS-bound: NAT, windows, viewers - the host registers them), **user** (the same registry). Replacing a device = another `type` in the file; the file's shape does not change. The present direct `attach` and `BoardSpec` stay as the low-level path and for compatibility; the data file is primary, `BoardSpec` is a layer that reads or produces it (a table test: every built-in board has an equivalent diagram).

### The board file (decided with the maintainer, 2026-10-28)

`<name>.board.json` (JSON, like `diagram.json` and Wokwi's `*.chip.json`; any host language reads it without a library) is the **definition of a board type** in the registry, next to the part types. It carries the flash size, the firmware **per family and complete, in the file itself** - `default_tag`, tag -> URL, and `layout` (where the flash filesystem is), i.e. today's `BoardFirmwareSpec` as data; the URLs are board-specific (`RPI_PICO-...` vs `RPI_PICO_W-...`), so there is no shared catalogue to point at and `utils/firmware_specs.json` goes away (its `pico` and `pico_w` entries become the built-in boards' own `board.json`; long tag lists are generated by a script, not written by hand); downloading and caching stay on the host, the core never fetches a URL - the board's own pin names (`D8`, `GP17`, ...) and its onboard parts with their fixed nets. **It does not refer to any diagram.** The reference goes the other way: a user's diagram contains the board as an ordinary part, as Wokwi's contains `wokwi-pi-pico`:

```json
{ "parts": [ {"id": "mcu", "type": "xiao-rp2040"}, {"id": "lcd", "type": "st7735s"} ],
  "connections": [ ["mcu:D8", "lcd:SCK"] ] }
```

So a user keeps their own diagram and "inheriting a base diagram" is one `type` line. `--board xiao-rp2040` stays as it is and is shorthand: the host builds a diagram holding just that one part. **Mandatory:** every named board has a `board.json` (it is its definition); only the defaults `pico` and `pico_w` (and Wokwi's `wokwi-pi-pico` / `board-pi-pico-w` in foreign diagrams) are built into the core and need no file. **Where they live:** only `pico` and `pico_w` are built in (today's `BOARDS` registry and `firmware_specs.json`, which hold exactly these two). The ~20 boards of `boards/` are *not* in the package: they stay outside it as `--board-spec` examples, one `<name>.board.json` each, loaded by an explicit path (`--board-spec path/to/x.board.json`, as `--board-spec path:ATTR` today), and graduate into the package only by the promotion checklist of [0059](../records/0059-boardspec-firmware-resolution.md).

### Base boards and custom wiring (decided with the maintainer, 2026-10-28)

**Onboard parts belong to the board type, not to the user's diagram.** A diagram holding a `pico_w` part always gets that board's CYW43, LED, BOOTSEL and their fixed nets; the user only adds on top (`pico_w` + an LED does not lose the CYW43). Board types form levels, each described as a difference: `rp2040` (the bare chip, no onboard parts - the way to a wholly custom wiring), `pico` (`rp2040` + flash, BOOTSEL, LED on GP25), `pico_w` (`pico` minus the GP25 LED, plus the CYW43), written as `"extends": "pico"` in `board.json`; every onboard part has a stable name (`wifi`, `led`, `bootsel`).

- **Add:** a new part in the diagram, as any other.
- **Remove or change an onboard part:** there is no switch in the diagram (an earlier draft had a `without` attribute; dropped 2026-10-28 - it is not Wokwi's, so the same diagram would behave differently in two tools, and the levels already cover it). Pick the level that has what you want: `pico` has no WiFi, `rp2040` has nothing onboard; anything in between is a new board type with its own `board.json` and an `extends`.
- **Pad conflicts are an error at load, not a silent override.** A pad wired both to an onboard part and to a user part is reported (`GP25 is already used by wifi of pico_w`) - on a real Pico W GP23 / 24 / 25 / 29 belong to the CYW43 and the LED is driven through it, so "pico_w + my LED on GP25" is a real conflict. (Different from the part-type override above, which silently replaces an *implementation*; this would be two sources on one pad.)

The ~20 boards of `boards/` may move into the package so that everything with a `board.json` is one model; 0059's checklist then becomes a quality gate (a named maintainer, every number sourced), not the condition for being in the package.

### Several wires on one pin: pins joined by wires are one line (decided with the maintainer, 2026-10-28)

A `diagram.json` wire joins exactly two pins; there is no "net" object in the file (two I2C sensors on one bus are two wires from `mcu:GP4`). **At load we group the pins that are joined by wires, and each group is one line** (what a schematic means); a pad conflict (two sources driving one line) is checked per group, not per wire pair, and is an error as above. What Wokwi does is an inference, not a documented fact: its docs (`diagram-format.md`, `chips-api/`, read 2026-10-28) never describe nets, several wires on a pin or contention; but a shared I2C bus is its standard example and its custom-chip I2C API has a device listen on a bus by `scl` / `sda` / address, which only works if wires on one pin are one line. How Wokwi treats two outputs on one line is **not known**.

### How a part's implementation is found

The diagram says only `"type": "st7735s"` - *what* the part is. *Who implements it* is decided by the registry when the board is loaded, first match wins: (1) what the user registered (`register_part`), (2) what the host registered, (3) what the core has built in (the core reports its built-in types at `boot`), (4) nothing found: an error naming the type, never a silent skip. A registry entry can have several implementations (`st7735s -> {pins, [core, host-python]}`), so the same file works on any host, and replacing a device needs no change to the file. **Decided (maintainer, 2026-10-28): a user or host registration overrides a built-in one silently** - no flag, no log line.

### Which types go into the core first: no decision needed now

Part types start on the host and move into the core one at a time, without touching any diagram. In stage 1 `"type": "st7735s"` resolves to the host's Python class `St7735s`; once someone ports it to the core, the registry gets a second implementation of the same type, and the lookup order above (user, host, core) picks it. **The diagram does not change by one line.** So "which types first" is an optimisation done later, per type, and it is stage 2 that makes it worthwhile (a core-side consumer needs pads to listen to). One requirement holds from stage 1: a part's interface must be the same for both implementations - pins and data streams, no Python objects - which is why the `type -> pins` table is language-neutral.

## 5. Three stages

1. **The board file.** A reader of `diagram.json` + the `type -> pins` table; it makes the same `attach` calls as today. Changes nothing on the border, breaks nothing.
2. **Buses on pads** ([0106](../records/0106-peripheral-routing-to-pads.md)): reference, oracle, native consumers; then the block-level slots and the *input* families leave the border, and `boot(cfg, board)` takes over the assembly names. This is the hard part and the one that makes the border thin.
3. **A host in a second language** (Node over the wasm module of [0103](../records/0103-wasm-backend.md)) as the test of thinness: if it is not about a day of work, the border is still too thick.

Later, optional: Wokwi `controls` (sliders while it runs) when there is an interactive host; the Wokwi WASM chip API as the language-neutral user part. Full compatibility with Wokwi *projects* (`wokwi.toml`, serial monitor) is not a goal: we read their `diagram.json`, not their tooling.

## Not measured, not decided

How many of the ~5.8 k wrapper lines survive stage 2 (NAT, the CYW43 bus glue and the USB host are the candidates to measure first); the cost of pad-level UART / SPI / I2C on the pure-Python build.
