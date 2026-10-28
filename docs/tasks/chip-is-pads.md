# The chip is its pads: a thin border and a declarative board (working note, 2026-10-28 - **nothing here is built**)

Written after a conversation with the maintainer about why the wrapper is still thick, although the border has been narrowed to 16 slots out and 84 names in ([narrow-the-border-plan.md](narrow-the-border-plan.md), [narrow-the-input-plan.md](narrow-the-input-plan.md)). The goal stated there: the host glue must be writable **in any language, anywhere** (Python today; JS over wasm, Rust, C, ... tomorrow), so the border must be something a host can implement in a day, not a hundred-name Python API. Per `CLAUDE.md` this is a note about what *should eventually happen*, not a go-ahead; every step below needs its own "yes". Related: [0106](../records/0106-peripheral-routing-to-pads.md) (pads for UART/SPI/I2C), [0102](../records/0102-c-abi-for-the-core.md) (the C ABI), [0103](../records/0103-wasm-backend.md), [chip-protocol-sketch.md](chip-protocol-sketch.md), [embedding-the-core.md](embedding-the-core.md), [0049](../records/0049-external-device-authoring-docs.md) / [0067](../records/0067-external-devices-and-boards-skill.md) (devices and boards today).

## 1. Why the wrapper is still thick

What the Python package holds (`src/rp2040py`, measured 2026-10-28, ~16 k lines of Python next to ~14 k of C++):

| Part | Lines | What it really is |
|---|---|---|
| `peripherals/`, `_cortex_m0_core`, `_rp2040`, `_sio`, `clock/` | ~13 k | the **second core** (pure-Python reference for the twin tests) - not wrapper |
| `native/` (Cython shells) | ~6.2 k | the glue Python <-> C++; what the border replaces |
| `device/bootrom.py` | ~4.1 k | data (the bootrom image) |
| `simulator.py`, `usb/`, `gdb/`, `external/` (CYW43 bus ~1.3 k + NAT ~0.5 k), `cli/` | ~5.8 k | the real wrapper |

And the border itself is thick in a specific way: **it describes blocks, not the chip.** A real RP2040 shows the world only its pads (GPIO, QSPI, USB D+/D-, XIN, RUN, SWD). We keep a slot or a method per bus:

- Outward, 9 of the 16 slots are block-level shortcuts: `uart_byte`, `uart_baud`, `spi_transmit`, `i2c_start` / `connect` / `write` / `read` / `stop`, `adc_read` (+ `adc_channel_value`). Inward, most of the 35 *input* names are the answers to those slots (`complete_transmit`, `complete_connect/read/start/stop/write`, `feed_byte`, `complete_adc_read`): one exchange cut in two.
- The 20 *config* names (`num_channels`, `sample_time`, `data_bits`, `master_bits`, `frequency`, ...) are derived from registers the core owns; the host should neither ask for nor set them.
- The 21 *control* names (`bind_uart_peri_sinks`, `set_irq_hook`, `set_interrupt_unhooked`, `set_memory`, `use_clock`, `build`, `retune`, ...) are assembly, which a `boot(cfg)` in the core should do ([embedding-the-core.md](embedding-the-core.md)).

## 2. The proposal: the chip is its pads

What stays on the border (about 10-15 names, one language-neutral table):

- `run` / `request_stop` / time, `read32` / `write32` (the bus), `snapshot`, `boot(cfg, board)`;
- `pin_input` / `pin_changed` (the pads), the ADC pads as analog inputs, the USB wire, `warn`.

Everything else is **a device on pads**. UART, SPI and I2C reach the world only through their pads (that is [0106](../records/0106-peripheral-routing-to-pads.md), which becomes the main road to a thin border instead of a nice-to-have). The cost is known from 0106's measurements of 2026-10-20: a Python listener per clock edge is ~4.5x the cost of `attach` for an SPI display, so a pad-level device on a fast bus needs a **native consumer** (a sampler in the core that hands bytes or events, not edges, to the host). The reference-first order of the project applies: pure-Python reference, oracle, then C++.

## 3. Devices: three tiers, one registry

We cannot put every device into the core, and not every board has every device. A device has a **type** (a name in a registry) with a **pin list**, and an **implementation tier**:

1. **Core** - hot and common: the CYW43 gSPI (partly in `core/gspi.hpp` already), the UART console, LED / button / WS2812, the usual SPI/I2C slaves. A host mounts them with no code of its own.
2. **Host** - niche or OS-bound (NAT, window, web viewer, anything that needs files or a network): the host registers the type (`register_part("my_sensor", impl)` in Python; the equivalent in another language) and implements only "pins in / pins out" plus the bus byte streams.
3. **User** - the same registry under another name. **Replacing a device on a board = another `type` in the board file (or overriding the registry entry); the board file itself is not touched.**

The present direct `attach` (`rp2040.spi[n].on_transmit = ...`, `BoardSpec.extras` = Python factories) stays as the low-level path and for compatibility; it is what the tiers are built on until [0106](../records/0106-peripheral-routing-to-pads.md) moves the buses to pads.

## 4. The board as data: Wokwi's `diagram.json`

Today a board is code (`BoardSpec.extras` is a tuple of Python factories), so it cannot be read from another language, cannot be partly replaced, and cannot be handed to the core. A board should be data, and the format need not be invented: Wokwi's `diagram.json` is documented in `wokwi/wokwi-docs` (`docs/diagram-format.md`, MIT licence; read 2026-10-28).

**What it gives us as it is:**

- `{"version": 1, "author", "editor", "parts": [...], "connections": [...]}`; a part is `{id, type, attrs, left, top, rotate, hide}` (`id` and `type` required); a connection is `["partId:pin", "partId:pin", colour, [wire-path]]`. We need `id`, `type`, `attrs` and the first two elements of a connection; `left` / `top` / `rotate` / `hide`, the colour and the wire path are editor layout, which we **keep and ignore**.
- The Pico is a part, `wokwi-pi-pico`, with pins `GP0`-`GP22`, `GP26`-`GP28`, `GND.1`-`GND.8`, and `TP4` / `TP5` (GPIO 23 and 25, the latter with the LED). Pico W, Pico 2 and Pico 2 W are `board-pi-pico-w`, `board-pi-pico-2`, `board-pi-pico-2w`. The serial console is the pseudo-part `$serialMonitor` (`$serialMonitor:RX` / `TX` wired to `pico:GP0` / `GP1`).
- Existing diagrams, the documentation of about 70 parts, `wokwi-cli lint` (unknown part types, bad pins, duplicate ids) and the editor come for free, and a board of ours stays openable in Wokwi. The format has a place for our own fields (`"editor"`, extra top-level keys).
- Wokwi's **custom chips** (`<name>.chip.json` with `name`, `author`, `pins`, `controls`, plus a WASM module using the chip API: `gpio` / `i2c` / `spi` / `uart` / `analog` / `time`, in `docs/chips-api/`) are an existing answer to "a user part in any language", with an API very close to the thin border above. Supporting it is a **later, separate step**, not a condition for the rest.

**What we have to do ourselves (the real work):**

1. **The registry of part types is the work, not the format.** Map `type` to an implementation with its pin list and `attrs`: `wokwi-led`, `wokwi-neopixel` (-> `Ws2812`), `wokwi-pushbutton` (-> the button), `wokwi-ili9341`, `board-ssd1306`, `wokwi-mpu6050`, ... Start with the types we already have a device for; an unknown type is an honest error naming the type, not a silent skip. Some of ours may have no Wokwi type (`St7735s`, `Epd2in9G`, `BootselButton` - not checked against the part list): they would get `rp2040py-*` names.
2. **Data the format does not carry:** the firmware, the flash size and the filesystem layout (`BoardSpec.firmware` / `layout`), the QSPI flash and CYW43 that are *the board's* parts. Wokwi keeps the firmware in `wokwi.toml`, outside `diagram.json`. Ours would be a sibling file or a section Wokwi ignores. **Open: which.**
3. **Boards with parts on them.** For our `boards/*.py` the board must be a `type` too (`rp2040py-board-xiao-rp2040`, say) that brings its onboard parts and fixed nets, or a nested diagram. **Open: which.** Wokwi itself models a Pico W as one part.
4. **Pin names:** Wokwi's `GPn` and the pads of the core need one table, checked against the datasheet like every other pin fact (the "3g rule" of `.claude/skills/external-devices-and-boards`).

## 5. Open questions (the maintainer's to decide)

1. Is the data file the primary form and `BoardSpec` a layer that produces it (my leaning), or do both stay equal?
2. Where does a part's pin list come from? (The existing `ExternalDevice`s already know their pins - a one-time extraction.)
3. How is a user part with host code loaded: registered in-process, or by path like `--board-spec path:ATTR` today?
4. The firmware / flash / onboard-parts data: a sibling file or an extension section; a board as a part-type or a nested diagram?
5. How far to follow Wokwi: only the connection format and the part names, or also its `controls` (sliders and buttons the user moves while it runs) and the chip API?

## 6. Order (proposal only)

1. This note, then the agreed decisions on section 5.
2. The registry and a reader of `diagram.json` that builds today's `attach` calls (no change on the border; boards still run through `BoardSpec`). A table test: every built-in board has an equivalent diagram.
3. [0106](../records/0106-peripheral-routing-to-pads.md) as the road to a thin border: reference, oracle, the native consumers; the block-level slots and the *input* families leave the border as their buses become pads.
4. `boot(cfg, board)` and the core-tier parts; the *config* names and the assembly *control* names leave the host.
5. Only then a host in a second language (a Node host over the wasm module of [0103](../records/0103-wasm-backend.md)) as the test of thinness: if it is not about a day of work, the border is still too thick.
6. Wokwi chip-API (WASM user parts) - last, optional.

## Not measured, not decided

How many of the 5.8 k lines of the wrapper survive a pad-level border (the NAT, the CYW43 bus glue and the USB host are the candidates to measure first); what the pad-level UART / SPI / I2C cost on the pure-Python build; whether `diagram.json` nets need more than point-to-point (a Wokwi wire is point-to-point, a net of three parts is two wires).
