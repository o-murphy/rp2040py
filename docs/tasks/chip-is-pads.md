# The chip is its pads: a thin border and a board as data (working note, 2026-10-28 - **nothing here is built**)

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
| **Firmware, flash, onboard parts - the format has no place for them.** | Firmware and flash size/layout: a small sibling file (`<board>.toml`, like Wokwi's `wokwi.toml`), so `diagram.json` stays pure Wokwi. Onboard parts (Pico W's CYW43, the QSPI flash): the **board is a type** (`rp2040py-board-xiao-rp2040`) that brings its own parts and fixed nets, as Wokwi models a Pico W as one part; nobody draws them each time. |

Device tiers behind the registry: **core** (hot and common: CYW43 gSPI, UART console, LED / button / WS2812, usual SPI/I2C slaves - mounted with no host code), **host** (niche or OS-bound: NAT, windows, viewers - the host registers them), **user** (the same registry). Replacing a device = another `type` in the file; the file's shape does not change. The present direct `attach` and `BoardSpec` stay as the low-level path and for compatibility; the data file is primary, `BoardSpec` is a layer that reads or produces it (a table test: every built-in board has an equivalent diagram).

## 5. Three stages

1. **The board file.** A reader of `diagram.json` + the `type -> pins` table; it makes the same `attach` calls as today. Changes nothing on the border, breaks nothing.
2. **Buses on pads** ([0106](../records/0106-peripheral-routing-to-pads.md)): reference, oracle, native consumers; then the block-level slots and the *input* families leave the border, and `boot(cfg, board)` takes over the assembly names. This is the hard part and the one that makes the border thin.
3. **A host in a second language** (Node over the wasm module of [0103](../records/0103-wasm-backend.md)) as the test of thinness: if it is not about a day of work, the border is still too thick.

Later, optional: Wokwi `controls` (sliders while it runs) when there is an interactive host; the Wokwi WASM chip API as the language-neutral user part. Full compatibility with Wokwi *projects* (`wokwi.toml`, serial monitor) is not a goal: we read their `diagram.json`, not their tooling.

## Not measured, not decided

How many of the ~5.8 k wrapper lines survive stage 2 (NAT, the CYW43 bus glue and the USB host are the candidates to measure first); the cost of pad-level UART / SPI / I2C on the pure-Python build; whether a net of three parts (two Wokwi wires) needs more than point-to-point; the form of the sibling file for firmware and flash.
