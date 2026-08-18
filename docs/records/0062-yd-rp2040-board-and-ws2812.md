# 0062. YD-RP2040 (VCC-GND Studio), and the WS2812 device it needs

- Status: **Implemented (2026-08-17); the live PIO-driven decoding it was blocked on works as of
  2026-08-18**, once [0063](0063-pio-clkdiv-and-delay-cycles.md) - the defect this work uncovered
  and measured - was fixed. See "Implemented" and the 2026-08-18 addendum at the end. (Original status, kept: *Proposed — documented, nothing implemented
  (2026-08-17). No board file, no device, no tests. This record says what adding the board would
  take and what it would be worth; building it is a separate go-ahead.*)
- Conceived: 2026-08-17
- Related: [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec.firmware` - what makes a board
  file for hardware with only *one* upstream firmware family expressible at all), 0049 (board
  authoring how-to, and its addendum that first identified this board as *not* WEACTSTUDIO),
  [0056](0056-st7735s-waveshare-lcd-board.md) (the precedent: a board whose point is its device),
  [0060](0060-external-io-bridges.md) (which ruled WS2812 out for a *host-hardware bridge* - a
  different question from emulating one, see below), [0057](0057-run-pin-reset-hook.md) (this board
  has a RESET button too), 0050/0051 (BOOTSEL), 0035 (flash offsets), 0027 (the "3g rule")

## The board

YD-RP2040 by VCC-GND Studio ([product page](https://circuitpython.org/board/vcc_gnd_yd_rp2040/),
[upstream port](https://github.com/adafruit/circuitpython/tree/main/ports/raspberrypi/boards/vcc_gnd_yd_rp2040)) -
a Pico-class RP2040 board that the vendor describes as a Pico plus five things:

| addition | RP2040-visible? |
|---|---|
| PWR power LED | no - wired to the rail, nothing to model |
| USB-C instead of micro-USB | no - indistinguishable at this level |
| RESET (NRST) button | yes, but it pulls **RUN**, which is not a GPIO - see [0057](0057-run-pin-reset-hook.md) |
| USRKEY user button on **GPIO24** | yes - a plain GPIO button |
| WS2812 RGB LED on **GPIO23** | yes - **and nothing in this project can model it** |

plus a bigger flash part: W25Q32/W25Q64/W25Q128 (4/8/16 MiB) where the Pico has a W25Q16 (2 MiB).
The user LED stays on GPIO25, same as a Pico.

**This is not WEACTSTUDIO.** [0049](0049-external-device-authoring-docs.md)'s addendum already had
to say so once, when `boards/weactstudio/` was written: different vendor, USR button on GPIO24
rather than GPIO23, and an RGB LED WeAct's board does not have. With both potentially in-tree the
distinction matters more, not less - two boards, adjacent pins, one of which is exactly the other's
missing device.

## What upstream actually says

Every number below comes from that port's own files, not from the vendor's marketing copy (0027's
"3g rule"):

- `mpconfigboard.mk`: USB VID `0x2E8A` / PID `0x102E`, `"YD-RP2040"` / `"VCC-GND Studio"`,
  `CHIP_VARIANT = RP2040`, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ,W25Q32JVxQ,W25Q128JVxQ"`,
  `CIRCUITPY__EVE = 1`. **No `CIRCUITPY_FIRMWARE_SIZE`**, and the board ships no `link.ld` of its
  own (confirmed 404 upstream), so `ports/raspberrypi/link-rp2040.ld`'s default
  `firmware_size = 1020K` applies. With `CIRCUITPY_INTERNAL_NVM_SIZE = 4 * 1024`:

      fs_start = 1020 KiB + 4 KiB = 0x100000

  the same offset as plain `pico` and as `boards/weactstudio/`. `fs_blockcount = 512` would follow
  this project's existing CircuitPython convention (0035: only the *start* has to be right).
- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "VCC-GND Studio YD RP2040"`, and
  `MICROPY_HW_NEOPIXEL = GPIO23` - i.e. CircuitPython treats that LED as its **status** indicator,
  which is why it lights up during boot with no user code involved.
- `pins.c`: `RGB` and `NEOPIXEL` -> GPIO23, `BUTTON` -> GPIO24, `LED` -> GPIO25. Matches the
  vendor's own pinout diagram exactly.
- Firmware: `adafruit-circuitpython-vcc_gnd_yd_rp2040-en_US-10.2.1.uf2`, from
  `downloads.circuitpython.org/bin/vcc_gnd_yd_rp2040/en_US/`.

## No MicroPython build exists for it - and that is a design question, not a gap to paper over

MicroPython ships **no** `ports/rp2/boards/` port for this board, so there is no equivalent of
`WEACTSTUDIO`'s four images. The generic `RPI_PICO` build does run on it (electrically it is a
Pico), but with the Pico's own flash geometry - `fs_start = 0xa0000`, `fs_blockcount = 352` - so
the filesystem sits where a 2 MiB board puts it and the extra 2/6/14 MiB is simply unused.

[0059](0059-boardspec-firmware-resolution.md) makes both spellings possible, which is exactly why
the choice has to be made deliberately:

| option | what it means |
|---|---|
| **(a) declare `circuitpython` only** (recommended) | the board file asserts only what upstream actually built for this board. Someone running MicroPython passes `--image <RPI_PICO url or path>` themselves, which 0059 made work with `--board-spec` |
| (b) also declare `micropython`, pointing at the `RPI_PICO` URL | convenient, but the file would claim a firmware nobody built for this board, and would silently freeze its filesystem at 2 MiB geometry on a 16 MiB chip |

**Proposed general rule, worth adding to 0049's how-to whichever way this goes:** a `firmware` key
means *"this firmware is built for this board"*, not *"this firmware runs here"*. Option (b) blurs
that, and the blur is invisible at the point of use - the CLI would happily boot it and report a
352-block filesystem on a 16 MiB board with no hint why. Option (a) keeps the honest version one
documented `--image` away.

## The substance: this board needs a WS2812 `ExternalDevice`, which does not exist

LED(GP25) is `LEDMock`; BOOTSEL is `BootselButton` (0050/0051); USRKEY(GP24) is `KeyMock(gpio=24,
...)` - the same generic devices `boards/weactstudio/` already reuses. RESET is unmodelled by
design (0057). **WS2812 on GPIO23 is the only genuinely new thing**, and it is what would make this
board worth shipping as an example at all - the same reason 0056's board earned its place: a board
whose point *is* its device.

### Emulating a WS2812 is not what 0060 ruled out

[0060](0060-external-io-bridges.md) names WS2812 as beyond a *host-hardware* GPIO bridge's ceiling,
because the emulator runs ~20-30x slower than real time and in bursts, so a real LED strip on a
real host's pins cannot be driven from it. That ceiling is about **wall-clock** I/O and does not
apply here. An emulated device decodes edges in **emulated** time, where `RPPIO` is cycle-coupled
to the CPU's own instruction loop ([0037](0037-pio-clock-coupled-stepping.md)) - so the 800 kHz
waveform's T0H/T1H ratio is exactly what the firmware intended, however slowly wall-clock time is
passing. Decoding is fine; driving real hardware is what is not.

### It would have something real to decode from the first second of boot

Measured today, with no board file and no new code - the stock CircuitPython image booted in the
emulator through `--board pico --image <the .uf2>`:

- it boots: `board.board_id == "vcc_gnd_yd_rp2040"`, and CIRCUITPY mounts, which independently
  confirms the `0x100000` derivation above (a wrong offset gives an unmountable drive);
- `board.NEOPIXEL` / `board.BUTTON` / `board.LED` all exist;
- **GPIO23 saw 606 edges within ~8.5 s of boot, with zero guest code** - the status NeoPixel
  `MICROPY_HW_NEOPIXEL` declares.

That is the same property that made 0056's CircuitPython twin valuable: the firmware exercises the
device by itself, so the device gets an honest test without a hand-written driver script.

### Sketch, not a design commitment

- `src/rp2040py/external/ws2812.py`: an `ExternalDevice` on one GPIO. Listen via
  `GPIOPin.add_listener()`, timestamp each edge off the simulation clock, decode the NRZ encoding
  (T0H ~0.4 µs vs. T1H ~0.8 µs; a >50 µs low latches the frame), and emit `on_pixels` with a list
  of raw `(r, g, b)` tuples per latch. GRB wire order for WS2812/WS2812B, with the colour order and
  bits-per-pixel parameterized rather than hardcoded, so SK6812/RGBW is a constructor argument.
- Boundary, unchanged from [0046](0046-epd2in9g-external-device.md)/0056: hand over raw pixel
  values, never a picture, so nothing in `src/` grows an image dependency.
- **Cost to measure, not assume.** A pin listener on a line toggling at 800 kHz is precisely the
  hot-path shape [0047](0047-cyw43-pio-gpio-hotpath.md) had to optimize for CYW43. One 24-bit pixel
  is ~48 edges - a status LED blinking at a few Hz is nothing, but a 60-LED strip at 60 fps is
  ~86k edges/s of emulated time. Coalescing at the `on_pixels` level (one callback per latch, not
  per edge) is the same "coalesced snapshot" rule 0060 already landed on.
- Deliberately **not** proposed: decoding by inspecting PIO state-machine programs instead of pin
  edges. Cheaper, but it would only work for drivers that use the PIO the way CircuitPython's does;
  pin edges also decode a bit-banged driver, which is why they are the right primitive.

## Where it would live

`boards/vcc_gnd_yd_rp2040/` - CircuitPython's own board id, case-normalized, per 0059's naming
rule. An example under `boards/`, **not** `boards.BOARDS`: it clears neither item 2 (an entry in
`firmware_specs.json`) nor item 5 (a named maintainer) of 0059's promotion checklist, and cannot
clear item 2 for MicroPython at all while no MicroPython build exists.

## Order of work, if this is taken up

1. `external/ws2812.py` + unit tests (a synthesized bit stream, and the real boot's own stream).
2. `boards/vcc_gnd_yd_rp2040/`, declaring `circuitpython` only, attaching
   `Ws2812`/`LEDMock`/`BootselButton`/`KeyMock`.
3. Live-boot verification: pixels decoded from the status LED with no guest code, plus a guest
   `neopixel` script for the general case.
4. A CI step alongside the ones 0059 added to `ci-micropython.yml`'s `test-board-spec` job.

Steps 1 and 2 are separable: the device is useful on `pico`/`pico_w` on its own (WS2812 strips are
the single most common RP2040 add-on), and the board is the thing that makes it *testable against
real firmware* rather than against a hand-written stimulus.

## Open questions

- **USRKEY's polarity and pull are not established.** WeAct's USR button is `Pin.PULL_UP`/
  active-low, and this board's is probably wired the same way, but "probably" is not the 3g rule -
  it needs its own upstream source (CircuitPython's `pins.c` only names the pin) before a
  `KeyMock(gpio=24, active_high=...)` is written down.
- **Which flash variant?** Unlike WEACTSTUDIO, upstream ships one CircuitPython image covering all
  three chips (`EXTERNAL_FLASH_DEVICES` lists them together), so there is nothing to pick - but the
  CIRCUITPY drive's real *size* does differ per chip, and this project sizes it by convention
  (512 blocks) rather than deriving it. Worth revisiting `fs_blockcount`'s convention generally,
  not just here.
- **Does `on_pixels` want the frame or the diff?** A status LED re-sends all 24 bits per update, so
  a frame is natural; a long strip re-sends everything too. Probably the frame - but the answer
  should come from the measurement above, not from taste.

## Addendum, same day: derive the timings from CircuitPython's own driver

The sketch above quoted the *datasheet-ish* generic timings (T0H ~0.4 µs, T1H ~0.8 µs, latch
>50 µs). Those are the wrong thing to build against, and the right source is the driver that will
actually be talking to the emulated device:
`ports/raspberrypi/common-hal/neopixel_write/__init__.c`. Applying 0027's 3g rule to a *device*
rather than to a board, its PIO program and clock are:

```c
const uint16_t neopixel_program[] = {
    0x6621,  // out x 1 side 0 [6]; Drive low
    0x1323,  // jmp !x do_zero side 1 [3]; Branch, drive high
    0x1400,  // jmp bitloop side 1 [4]; Continue high for one
    0xa442   // nop side 0 [4]; Drive low for zero
};
```

at **12.8 MHz**, i.e. one PIO cycle = 78.125 ns, autopull every 8 bits, *shift left to output MSB
first*. Walking the program gives, exactly:

| bit | high | low | period |
|---|---|---|---|
| `1` | 4 + 5 = 9 cycles = **703 ns** | 7 cycles = **547 ns** | 16 cycles = 1.25 µs |
| `0` | 4 cycles = **312 ns** | 5 + 7 = 12 cycles = **938 ns** | 16 cycles = 1.25 µs |

which matches the source's own comments (`<312ns hi, 936 lo>`, `<700 ns hi, 556 ns lo>`). Three
things follow for the decoder, none of them guesses any more:

1. **Classify on high-time with a threshold near 500 ns** (midway between 312 and 703), not on
   "~0.4 vs ~0.8 µs". The margin either side is ~190 ns, comfortably wider than the ±1 PIO cycle
   (78 ns) a differently-clocked driver would shift things by - so the same threshold also decodes
   MicroPython's and the pico-examples `ws2812.pio` variants, which use the same 1.25 µs period
   with slightly different splits.
2. **Latch detection is easy here**: CircuitPython does not merely respect the datasheet's ~50 µs,
   it enforces **≥300 µs** between transmissions (`next_start_raw_ticks = port_get_raw_ticks(NULL)
   + 2`). A latch threshold anywhere in 50-300 µs separates frames unambiguously for this driver;
   pick the datasheet's ~50 µs so a tighter third-party driver still works.
3. **Bit order is MSB-first within each byte, one byte at a time** - so the device decodes bytes,
   and *colour* order (GRB for WS2812/WS2812B, GRBW for SK6812) is a layer above the wire format,
   which is the constructor argument the sketch already proposed rather than something to infer.

This also settles what the first unit test should be: feed the decoder the exact edge sequence this
program produces for a known byte, and assert the byte comes back - a test written against
upstream's own numbers, not against our own decoder's behaviour.

## Addendum, same day: USRKEY's wiring, from the vendor schematic

The open question above ("USRKEY's polarity and pull are not established") is now answered from the
vendor's own schematic - **YD-2040 2022 V1.1 SCH**, the `USR-SW` net:

    GPIO24 ──[ R13 10k ]──┬── USR (ST-1185S) pin 2 ──/ ── pin 1 ── GND
                          └── C18 100n ── GND

So: **active-low, and there is no external pull-up.** R13 is a 10k resistor *in series* between the
pin and the switch node, not a pull-up to 3V3 - the only things on the switch node are the button
(to GND) and C18 (100 nF, to GND, i.e. a hardware debounce of ~1 ms against R13). That makes the
released level come **entirely from the RP2040's internal pull**:

- firmware configures `PULL_UP` -> released reads HIGH, pressed pulls the node to GND and the pin
  sees roughly 3.3 V × 10k/(10k + ~60k internal) ≈ 0.5 V, i.e. LOW. This is what MicroPython's and
  CircuitPython's own examples do, and what `KeyMock(gpio=24, active_high=False)` models;
- firmware configures **no** pull -> the line genuinely floats, and the reading is undefined on
  real hardware. This emulator already models that honestly ([0006](0006-gpio-pull-floating.md)),
  and it is precisely why `KeyMock.release()` must hand the pad back to whichever pull firmware
  configured (`GPIOPin.release_input()`) rather than driving it high - the bug 0049's addendum
  found and fixed, for the same reason 0051 gives for `BootselButton`: forcing the "released" level
  reads identically but *masks* a firmware that forgot its pull-up.

This board is a sharper test of that rule than `boards/weactstudio/` is, because here the schematic
proves there is nothing external to fall back on. Nothing new is needed to model it -
`KeyMock(gpio=24, active_high=False)` is exactly right - but the reason it is right is now cited
rather than assumed, which was the whole point of leaving it open.

For completeness, the same schematic confirms the other two nets this record cares about: the RGB
LED is an **XL-5050RGBC-WS2812B** on `RGB_CTRL` from **GPIO23** (a real WS2812B, not a lookalike),
and the RST button pulls `RUN` - see below.

### The RST net, and what it settles for 0057

    3V3 ──[ R12 10k ]──┬── RUN
                       └── RST (ST-1185S) ──/── GND

The contrast with `USR-SW` above is the useful part, and it is not incidental:

| | released level comes from | series resistance when pressed |
|---|---|---|
| `USR-SW` (GPIO24) | the **RP2040's internal pull** - nothing external | R13, 10k |
| `RUN` | an **external** 10k pull-up to 3V3 (R12) | none - the switch grounds RUN directly |

RUN is not a GPIO, so there is no internal pull for firmware to configure and the board *must*
provide one - which it does. Two things follow for
[0057](0057-run-pin-reset-hook.md), which was written from this board's sibling without a
schematic in hand:

1. **A press is a level, not a pulse.** The switch grounds RUN directly, with no series resistor,
   for exactly as long as a finger is on it. That is evidence for 0057's third option (model RUN as
   a real held-low pin, which needs a new execution state in `_execute_batch.py`) being the
   *faithful* one, whatever it costs - a hook that fires a one-shot reset models a tap, not a hold,
   and the two differ visibly if firmware is held in reset.
2. **The electrical side needs no modelling at all.** Unlike BOOTSEL (0051), where the released
   level depends on the QSPI pad's own pull-up defaults (0050) and getting that wrong masks real
   firmware bugs, RUN's released level is a board-level pull-up that is simply always there. So a
   future `ResetButton` has no pull semantics to get right - all of its difficulty is on the "what
   does a reset actually do" side, which is precisely what 0057 is about.

## Implemented, 2026-08-17 — with one part blocked on a defect this work uncovered

Built on the go-ahead this record was written for:

- **`rp2040py.external.ws2812.Ws2812`** - decodes the single-wire NRZ waveform off one GPIO and
  emits `on_pixels` per latched frame, with `color_order` (GRB/GRBW/...) a constructor argument and
  raw pixel values rather than a picture (0046's boundary). 16 unit tests
  (`tests/test_ws2812.py`), written in CircuitPython's own timings *and* in pico-examples'
  `ws2812.pio` timings, covering latch separation, mid-frame stalls, partial bytes and pixels, and
  colour-order handling.
- **`boards/vcc_gnd_yd_rp2040/`** - the board, CircuitPython-only as argued above, with
  `Ws2812(23)` / `KeyMock(24, active_high=False)` / `BootselButton` / `LEDMock(25)` and a
  `board_with(on_pixels)` helper.

One design change against the sketch: bits are classified by **duty cycle** (>=40% of the bit's own
period, measured against the shortest period in the frame) rather than against an absolute ~500 ns
threshold. It costs nothing, and it decodes any driver's clock choice - CircuitPython's 12.8 MHz,
MicroPython's, a 400 kHz slow-mode part - instead of only the one this record measured. The latch
stays absolute, because the inter-frame gap is produced by a CPU spin loop, and CPU-timed intervals
are faithful here even where PIO-timed ones are not.

**The live-boot verification this record promised does not pass, and the reason is not the device.**
Decoding the real status-LED stream produced consistent garbage; the trace explains why:

    guest wrote:  ff 00 aa
    expected:     11111111 00000000 10101010
    high times:   16,32,16,16,24,16,32,16 | 8,16,8,16,16,16,16,16 | 32,8,32,8,40,16,24,16  (ns)

`RPPIO` stores `SM_CLKDIV` and accumulates `[delay]` cycles but paces itself by neither, so 4 and 9
PIO cycles collapse into 1 and 2 CPU instructions and the two symbols *overlap* - the all-zeros
byte contains 16 ns highs, and so does the all-ones byte. No threshold can separate overlapping
populations; the information is gone before any device sees it. That is now
[0063](0063-pio-clkdiv-and-delay-cycles.md), with the trace above as its acceptance test.

So this record's own claim - "it would immediately have something real to decode from the first
second of boot" - was half right: the *edges* are there (606 of them, as measured), but their
widths are not. The device is correct against the waveform real hardware produces, which is what
its tests hold it to; the emulator producing that waveform is 0063's job. Until then, `Ws2812`
decodes a CPU-driven or bit-banged driver here, and any PIO-driven one only once 0063 lands.

Unchanged from the plan: the board still earns its place (its other three devices work, and it is a
live-verified `--board-spec` example for a real third-party board), and RESET stays unmodelled
(0057).

## Addendum, 2026-08-18: the blocked half now works - [0063](0063-pio-clkdiv-and-delay-cycles.md) landed

The one piece this record could not deliver - live decoding of the *PIO-driven* CircuitPython
driver - is done, and nothing in `Ws2812` had to change to get it. 0063 taught `RPPIO` to pace its
state machines by `SM_CLKDIV` and `[delay]`, so the waveform the emulator produces is now the one
real silicon produces: 312 ns for a `0` and ~703 ns for a `1` on a 1.25 µs period, against the 8-40
ns overlapping mush quoted above.

Replaying this record's own acceptance test - the guest writing
`neopixel_write(board.NEOPIXEL, bytearray([0xFF, 0x00, 0xAA]))` on real CircuitPython `10.2.1` -
the device decodes the frame as wire bytes `ff 00 aa`, i.e. `(r, g, b) = (0x00, 0xff, 0xaa)` through
its `GRB` order. Booting the board with **no guest code at all** decodes the status LED that
`MICROPY_HW_NEOPIXEL` declares, exactly as this record predicted it would: alternating `(0, 0, 0)`
and `(11, 11, 11)` frames from the first second of boot.

Two things this settles about the design choices above:

- **Duty-cycle classification was the right call and cost nothing.** The decoder is now being fed
  the absolute timings it was originally written against, so an absolute ~500 ns threshold would
  work too - but the measured widths carry ±40 ns of jitter from the CPU instruction each edge
  lands inside, and the duty-cycle rule absorbs that without a per-driver constant.
- **Pin edges, not PIO-program inspection, was also the right call** - and for the reason given,
  not by luck: what made this work was fixing the *waveform*, which a bit-banged driver benefits
  from identically.

The caveat this record and `external/ws2812.py` both carried ("what it decodes from a PIO-driven
driver is not what firmware wrote") is retired. 0063's own remaining ceilings - `CLKDIV=1` is still
one instruction per CPU instruction, and PIO still does not run through a CPU idle jump - are stated
there; neither is reachable by a WS2812 driver, which busy-waits rather than sleeps and runs its
state machine at 12.8 MHz.
