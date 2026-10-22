# 0106 - routing the UART, SPI and I2C functions to the pads

Status: **proposed (2026-10-18), documentation only - nothing built.** Found by the `extmod_hardware` group of MicroPython's suite ([0098](0098-datasheet-conformance-audit.md), progress log of 2026-10-18) and sharpened by reading how Wokwi does it (below). Companion to [0096](0096-cpp-mcu-core.md) (the pin banks and the C++ core), [0049](0049-external-device-authoring-docs.md) / [0067](0067-external-devices-and-boards-skill.md) (external devices) and [0104](0104-second-core-seams-and-architecture.md) (the per-core GPIO lines are a neighbour of this: both are about what a pad shows and who sees it). Per `CLAUDE.md` a note about what should eventually happen is not a go-ahead to implement it: each phase below needs its own "yes".

## What the model does today

A pad's level is a function of its function select (FUNCSEL) and of the block that owns the function: `GPIOPin.raw_output_enable` / `raw_output_value` (and the C++ pin bank's sources, `kSrcSio*`, `kSrcPio0/1*`, `kSrcPwm*`) know **four** functions - SIO, PWM, PIO0, PIO1. For **UART, SPI and I2C the pad shows nothing**: the function is selectable (FUNCSEL is stored and read back), but nothing drives the pad from the block and nothing feeds the block from the pad.

What reaches the outside instead is the blocks' own **callbacks**, inherited from rp2040js: the UART's byte stream (`on_byte` / `feed_byte`, in the CLI it is the console), the SPI's `on_transmit` / `complete_transmit`, the I2C's `on_start` / `on_connect` / `on_write_byte` / `on_read_byte` / `on_stop`. An external device takes the bus from the block (`rp2040.spi[n].on_transmit = ...`, as `St7735s` and `Epd2in9G` do) and the pins it listens to are only the *control* lines (CS, DC, RST) - which are real pads, SIO outputs.

## What that costs

1. **No electrical connection between the functions.** A UART's TX cannot reach an RX pad, so `machine_uart_irq_break.py`, `machine_uart_irq_rx.py` and `machine_uart_irq_rxidle.py` of the official suite cannot run (they want TX wired to RX, `tests/target_wiring/rp2.py`), and the same for any loopback test of SPI or I2C.
2. **A device is tied to a block, not to a wire.** If firmware puts the SPI on other pins than the device expects (the pin table allows several), the device does not notice: it is wired to `spi[0]`, whatever the pads are.
3. **Nothing is visible at the pad.** A pin-level observer (the `Jumper`, a logic-analyser-like listener, `Ws2812`'s decoder on a pin that firmware drives with a hardware SPI or UART) sees an idle pad.
4. **FUNCSEL changes mean nothing for these three.** A pad switched from SIO to UART keeps showing the SIO level.

None of this matters to a firmware that talks to a device through the block, which is nearly all of what runs today; it matters for tests that wire pins, for pin-level devices, and for anything that wants the model to be the board rather than the chip.

## How rp2040js and Wokwi do it

rp2040js (the model this project's reference started from) is exactly what is described above: UART, SPI and I2C expose callbacks and know no pads (read from `src/peripherals/uart.ts` and `i2c.ts`).

Wokwi's simulator wraps rp2040js and adds the missing layer. What follows is what its page's own client bundle (`wokwi.com/_next/static/chunks/9998.*.js`) shows when loaded in a headless Chromium - the architecture only, no code was taken, and the bundle is Wokwi's:

- Per peripheral, **named net pins** (`$uart0$tx`, `$uart0$rx`, `$i2c0$scl` / `$i2c0$sda`, `$spi0$sck` / `mosi` / `miso` / `cs`).
- Between the block's callbacks and those nets, **bit-level models**: a UART transmitter and receiver that follow the block's baud rate (they are re-timed when the divisors change), an I2C master and an SPI master that drive and sample the nets bit by bit; the I2C master's SCL frequency is `clk_sys / (SCL high period + SCL low period)` from the block's own registers.
- Each GPIO pad is wrapped: when its **FUNCSEL changes**, the wrapper detaches the old function's net pin from the pad's board net and attaches the new function's, using a static pin -> (uart | spi | i2c | pwm) table (the datasheet's GPIO function table). The pad also forwards the chip's own drive (low, high, pull, high-Z, bus keeper) to the board net and applies the net's level back as the pad's input.
- Parts of a Wokwi circuit are wired to the *board* nets, so an SPI display there reads real waveforms on SCK/MOSI, the way a real one does.

So Wokwi too builds the electrical layer outside the blocks, as a wrapper over callbacks, and it routes by FUNCSEL.

## What would be built

A **pad-function layer** with three parts, in the order they would be built:

1. **Pad sources for the three functions.** FUNCSEL = UART / SPI / I2C makes the pad's output enable and value come from a *peripheral line* (UART TX; SPI SCK / MOSI / CSn as outputs; I2C SDA / SCL as open-drain: low or released), and the pad's input level feed the matching input line (UART RX; SPI MISO / RX of a slave; I2C SDA / SCL read-back). The function table (which pad carries which line) is the datasheet's (2.19.2), checked against the SVD / pico-sdk `gpio.h` like every other pin fact here (the "3g rule" of `.claude/skills/external-devices-and-boards`).
2. **Bit-level models between the block and its lines**, one per bus: the UART's TX shift (start, 5-8 data bits, parity, 1-2 stop bits at the block's baud, a break), its RX sampler (16x oversampling is not needed: sample mid-bit), the SPI's SCK / MOSI / MISO / CS timing from `SSPCLK` and the prescale, the I2C's open-drain bus with its SCL period and clock stretching. These have to take their rate from the block's registers, so a change of baud or divisors re-times them.
3. **Compatibility with the callbacks.** Nothing that works today may stop: a device attached through `on_transmit` / `feed_byte` must keep working with no pads involved. The layer is therefore **opt-in per bus** (or per run), off by default; when it is on, the block's callbacks are fed from, and drive, the lines. How the two coexist (a bus owned by the pads vs by a callback, and what a device attached the old way sees) is the first open question below.

Where it lives is the main design decision (below). Either way it is an *external* layer in the sense of the threading model: it runs on the engine's thread, driven by the clock's alarms, and costs nothing for a chip that does not use it.

## Open questions (none decided)

1. **Layer or pin-bank sources?** (a) A Python layer over the blocks' callbacks, like Wokwi's: the pure-Python chip and the native chip share it, the C++ core is untouched, but a bit-level model in Python costs wall time per bit on every byte of a busy bus (a console UART at 115200 baud is ~11.5 k bits/s of simulated time - fine; an SPI display at tens of MHz is not, and would stay on the callbacks). (b) Pad sources in the C++ pin bank (new `kSrcUart*` / `kSrcSpi*` / `kSrcI2c*` sources next to the PIO's) with the shifters in C++, held to a lockstep oracle like every other block: fast, but a large piece of work in two builds. (c) Both: (a) first as the reference and the test vehicle, (b) only for the buses that need speed. The order "reference, oracle, C++" is the project's usual one.
2. **Coexistence with the callback devices** (point 3 above): per bus a mode - `callbacks` (today) or `pads` - chosen by the board (`BoardSpec`) or an option, so that `St7735s` keeps working on `spi[0]` unchanged and a new `Jumper`-style test board turns the UART to `pads`?
3. **Timing fidelity.** How exactly must a UART bit be placed - on the clock's tick (the integer ticks of 0100 make this exact) - and what is sampled: a receiver that samples mid-bit at the *receiver's* baud, so that a baud mismatch between the two ends shows up as framing errors (what `machine_uart_irq_*` partly checks)?
4. **The pin-level external-device API.** Today an `ExternalDevice` gets `rp2040.spi[n]` or pads; with the layer on, a device could attach to the *pads* of a bus. Whether that is a second way (alongside the block hooks) or replaces them for new devices is a question for 0049's reference doc, not for this record.
5. **The neighbour in 0104:** the per-core GPIO interrupt lines and `PROC1_*` registers of IO_BANK0 are about who sees a pad's event; routing is about what a pad shows. They touch the same classes (`GPIOPin`, the bank) and should not be built blind to each other.

## Acceptance (when it is built)

- `machine_uart_irq_break.py`, `machine_uart_irq_rx.py`, `machine_uart_irq_rxidle.py` pass in the `extmod_hardware` group with GPIO0 wired to GPIO1 (they are excluded today); `machine_i2c_target.py` stays out until an I2C slave mode exists (a separate gap).
- A UART TX -> `Jumper` -> UART RX loopback round-trips bytes at several baud rates and shows framing errors when the two ends differ.
- A logic-analyser-style listener on an SPI pad decodes the bytes `St7735s`'s driver sends (the bytes on MOSI equal the bytes the block's callback saw).
- With the layer off, the instruction-count benchmark and every existing test are unchanged; with it on and a bus idle, the cost is zero.
- The lockstep oracle holds the pure-Python and C++ implementations equal if (b) or (c) is taken.

## Not in scope

An electrical model (drive strength, slew, contention beyond "the lower index wins", capacitance) - the pads stay logic-level, as 0098 already says. An I2C slave mode (the block's own gap). Analog functions (ADC pads) and the USB pads.

## Progress log

- 2026-10-18: written (documentation only), on the maintainer's call that routing and pads is a record of its own. The Wokwi note is moved here from 0098's log.
- 2026-10-20: **Experiment: what a pad-level SPI display costs, against `attach` (measurements only, nothing built in the repo).** Asked by the maintainer before any design is chosen: the cost of the present path (a device attached through the block's `on_transmit`) against devices that sit on the pads, with a real SPI display (`St7735s`).
  - *Setup.* MicroPython 1.28.0 on the Pico, native build on CPython 3.10, the batch engine, one 160x80 RGB565 frame = 25,600 bytes = 204,800 bits, 10 frames per run, 3 runs of each mode taken in turn, the best time kept (spread between rounds about 15 %). **`attach`**: `machine.SPI(1)` `spi.write()` of the frame with `St7735s` attached as today (the block calls `on_transmit` per byte; the time includes the guest's own `spi.write` loop). **Pads**: no SPI block drives the pads, so the pad traffic is made by a PIO program (`out pins,1 side 0` / `nop side 1`, SCK and MOSI on GPIO 10/11, 10 MHz) fed by DMA, i.e. native pins in the native bank driven by the native PIO; the consumer is varied.
  - *Results (ms of wall time per frame; two sessions of the same experiment, the first without the sampler rows).*

    | Path | ms / frame |
    |---|---|
    | `attach` (block callback, device logic in Python) | 75-77 |
    | pads, native producer, **no consumer** | 194-221 |
    | pads + a Python listener on SCK and MOSI that does nothing (5.1 M calls over the 10 frames) | 278 |
    | pads + a Python SPI decoder on the SCK rising edge, bytes only | 313 |
    | pads + the same decoder feeding `St7735s`'s pixel logic | 349-382 |
    | pads + a **native sampler** (C++ direct listener of SCK, reads MOSI, shifts a byte) calling Python once per byte, feeding `St7735s` | 257 |
    | the same, Python called once per 64 bytes / once per 4096 bytes | 244 / 255 (noise) |
    | the native sampler only counting bytes | 222 |

    The decoded stream was checked (255,998 of the 256,000 bytes arrive, the first bytes equal the ones sent; the last bits are cut off when the state machine is stopped).
  - *What it shows.* (1) A Python listener per clock edge on native pads is ~4.5x the cost of `attach` for a SPI display (349-382 against 75-77 ms per frame), which is the "layer (a)" of the open question 1 taken literally: it is not viable for a display. The crossing into Python is ~0.16 us per event, and 4 million events per frame-sequence is what hurts. (2) A **native consumer removes nearly all of that**: with the sampler the consumer costs ~35 ms per frame over the producer (257 against 221), and the size of the chunk handed to Python does not matter. This is the same lesson as the CYW43 gSPI shifter ([0096](0096-cpp-mcu-core.md), 2026-10-05: 7.1 s -> 3.4 s on the Pico W scan). (3) What is left of the gap to `attach` (221 against 75 ms) is the **producer**: the PIO program costs two emulated instructions per bit plus the DMA. That is an upper bound for a pad-driving SPI block, not its cost.
  - *What it does not show.* There is no pad-driving SPI block, so the producer of the real pad path was not measured; a dedicated C++ shifter would be cheaper than the PIO. The prototype sampler (`core/pin_sampler.hpp`, about 60 lines, a direct listener of the clock pin that samples the data pin with `state_code()`, plus a Cython shell; it is a generalisation of the first half of `core/gspi.hpp`) is **not** in the repository. The numbers were taken in one container; the spread is 15 %, so a difference below that is not a finding. Nothing was measured on the pure-Python build or under wasm.
  - *Wokwi, looked at again (their simulator chunk, read as data).* The glue code keeps the block's callback and puts the bit-level model behind it: `attachSPI(n)` sets `cpu.spi[n].onTransmit = byte => spiMaster.transmit(byte, ...)`, an `SPIMaster` with named net pins (`$spi0$sck`, `$spi0$mosi`, ...) builds the waveform, and a GPIO wrapper detaches and attaches the function's net pin when FUNCSEL changes. That is the "layer over the callbacks" of question 1 (a), in JavaScript; the page shows no native or lazily elided model. The internals of `SPIMaster` (whether it toggles per bit or shifts in time) were not read.
  - *Consequences for the open questions, as a leaning, not a decision.* Question 1: a Python layer is out for fast buses; the producer and a native consumer (a sampler) are the pieces that make a pad-level device affordable, and the consumer is the small one. **A possible way to keep the byte speed of `attach` under a pad contract, not tried:** the block synthesises edges on the pads only while a bit-level observer is attached and hands bytes straight to a byte-level consumer otherwise - the principle of the lazy clock (0096, Phase 5b), with its cost: every reader of a pad would have to settle the deferred waveform first, and that invariant needs the same kind of randomised equivalence test the clock got.
  - *Risks recorded with it.* (a) **Over-building.** Phase 5b of [0096](0096-cpp-mcu-core.md) exists to remove layers (the shells' trampolines, `BlockPort` / `ChipPorts`); a pad layer for all three buses, with modes, a coexistence rule and a lazy waveform, adds one, in two builds under an oracle. It should be built only as far as a concrete failing test or device needs: the first candidate is the UART TX -> RX loopback that `machine_uart_irq_break.py`, `machine_uart_irq_rx.py` and `machine_uart_irq_rxidle.py` need. (b) **The CYW43 gSPI shifter must not move.** It does not use the SPI block at all: it is a direct listener pair on CLK and CS that samples DATA and drives it in the same cycle, fed by the PIO. A pad layer touches it only through the pin bank's event order and timing, the number of direct listeners a pin takes (`kMaxDirectListeners`), the arbitration of what drives a pad, and the same-cycle answer on the falling edge. **Acceptance for any change here:** the order hash of the Pico W scan's pin events is unchanged (`scripts/bench/pin_events.py picow-scan`, 4,798,482 events), the scan result is unchanged, `tests/test_external_device_on_any_core.py` passes on both builds, and with the layer off every existing test and the instruction-count benchmark are unchanged. (c) `attach` and the block callbacks stay for as long as a device needs them; whether they are ever removed is a question for [0049](0049-external-device-authoring-docs.md)'s reference, after a pad path exists that is no slower.
