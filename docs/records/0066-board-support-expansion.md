# 0066. Board support expansion: which upstream RP2040 boards are addable, and what each still needs

- Status: **Proposed — documented, not implemented (2026-08-18).** A checklist of concrete
  candidate boards, not a commitment to add any of them.
- Conceived: 2026-08-18
- Related: 0059 (`BoardSpec` firmware resolution - the mechanism any of these boards would use),
  0049 (external device authoring docs / the promotion checklist a board file clears), 0062
  (YD-RP2040 + `Ws2812` - the template these follow), 0056 (`St7735s` + Waveshare LCD board), 0046
  (`Epd2in9G`), 0065 (unrelated, but the tracker row immediately above this one)

## The question

Which boards from MicroPython's `ports/rp2/boards/` and CircuitPython's `ports/raspberrypi/boards/`
could become a new `boards/<name>/__init__.py` `--board-spec` target (the `vcc_gnd_yd_rp2040`/
`weactstudio`/`waveshare_rp2040_lcd_0_96` pattern) using only `ExternalDevice`s this project
**already has** - `LEDMock`, `KeyMock`, `BootselButton`, `Ws2812`, `Cyw43439`, `St7735s`,
`Epd2in9G` - versus which need genuinely new device emulation first.

## Method

Two passes, since MicroPython's and CircuitPython's board lists only partially overlap and a board
that exists in only one of them is still a valid target (this project already ships MicroPython-
only and CircuitPython-only boards side by side - `GARATRONIC_PYBSTICK26_RP2040`/`MACHDYNE_WERKZEUG`
below are MicroPython-only, `vcc_gnd_yd_rp2040` is CircuitPython-only).

**Pass 1 (2026-08-18, boards with a MicroPython port):** surveyed all 36 board directories under a
local `micropython/ports/rp2/boards/` checkout, excluded boards already covered by this project
(`pico`, `pico_w`, `weactstudio`, `vcc_gnd_yd_rp2040`, `waveshare_rp2040_lcd_0_96`) and every
**RP2350** board (this project emulates the RP2040 chip only - a different core/peripheral set, out
of scope regardless of how simple the board's peripherals are; confirmed per-board via
`board.json`/`mpconfigboard.cmake`/the pico-sdk board header, not by name alone -
`SPARKFUN_XRP_CONTROLLER` in particular doesn't say "2350" in its name but is one). For each
remaining candidate, read `mpconfigboard.h` + `pins.csv` + its underlying pico-sdk board header to
enumerate onboard peripherals, then cross-referenced against CircuitPython's
`ports/raspberrypi/boards` listing (`gh api repos/adafruit/circuitpython/contents/…`) for whether a
same-named port exists there too - a nice-to-have (a dual-family example, like `vcc_gnd_yd_rp2040`),
not a requirement.

**Pass 2 (2026-08-18, CircuitPython-only boards - no MicroPython counterpart at all):** fetched the
full `ports/raspberrypi/boards/` listing (144 entries) via `gh api`, removed every board with a
MicroPython equivalent (both what pass 1 already covered, addable and needs-device alike, plus the
already-covered boards above) and every RP2350 board (checked per-board via `mpconfigboard.mk`'s
`CHIP_VARIANT`), leaving 100 CircuitPython-only board-ids across 94 RP2040 + 6 RP2350. For the 94
RP2040 ones, read `pins.c` (+ `board.c`/`mpconfigboard.h` where a chip needed identifying - in a few
cases the exact display-controller init-sequence register bytes in `board.c`, to distinguish
ST7735/ST7789/ILI9341/SSD1306/UC8151/SPD1656 from each other rather than guessing from a vendor
product name alone, same "read the actual source" standard as pass 1 and this project's own
records generally).

## Checklist: addable now, has a MicroPython port

- [ ] `ADAFRUIT_FEATHER_RP2040` - LED (GPIO13) + `Ws2812` (GPIO16, power not switchable); CircuitPython port exists (`adafruit_feather_rp2040`)
- [ ] `ADAFRUIT_ITSYBITSY_RP2040` - LED (GPIO11) + `Ws2812` (GPIO17, power GPIO16) + a separate boot pushbutton (GPIO13); CircuitPython port exists
- [ ] `ADAFRUIT_QTPY_RP2040` - `Ws2812` (GPIO12, power GPIO11) + boot pushbutton (GPIO21), no plain LED; CircuitPython port exists
- [ ] `GARATRONIC_PYBSTICK26_RP2040` - LED (GPIO23) only; MicroPython-only, no CircuitPython port
- [ ] `MACHDYNE_WERKZEUG` - two plain LEDs, green (GPIO20) + red (GPIO21); MicroPython-only
- [ ] `NULLBITS_BIT_C_PRO` - RGB LED as three plain active-low GPIO LEDs (GPIO16/17/18, i.e.
      3×`LEDMock`, not a `Ws2812`); CircuitPython port exists
- [ ] `PIMORONI_PICOLIPO` - LED (GPIO25) + `USER_SW` pushbutton (GPIO23); CircuitPython port exists
- [ ] `PIMORONI_TINY2040` - RGB LED as three plain active-low GPIO LEDs (GPIO18/19/20) + `USER_SW`
      pushbutton (GPIO23); CircuitPython port exists
- [ ] `SEEED_XIAO_RP2040` - `Ws2812` (GPIO12, power GPIO11) + a separate RGB LED as three plain GPIO
      LEDs (GPIO16/17/25); CircuitPython port exists (`seeeduino_xiao_rp2040`)
- [ ] `SPARKFUN_PROMICRO` - `Ws2812` only (GPIO25), no plain LED; CircuitPython port exists
- [ ] `WAVESHARE_RP2040_PLUS` - LED (GPIO25) only, no NeoPixel despite the board's own "Battery
      Charging" feature tag (that circuit is analog-only, not addressable); CircuitPython port exists
- [ ] `WAVESHARE_RP2040_ZERO` - `Ws2812`/NeoPixel only (GPIO16); CircuitPython port exists. Flagged
      by the user as the one to pick up first, off
      [the board's own `board.json`](https://github.com/micropython/micropython/blob/master/ports/rp2/boards/WAVESHARE_RP2040_ZERO/board.json) -
      the smallest of this whole list (one `Ws2812`, nothing else)

## Checklist: needs exactly one new device, has a MicroPython port

- [ ] `SIL_RP2040_SHIM` (Silicognition RP2040-Shim) - Wiznet **W5500** Ethernet PHY (the board's own
      `Ws2812` + LED are both already covered)
- [ ] `W5100S_EVB_PICO` - Wiznet **W5100S** Ethernet PHY
- [ ] `W5500_EVB_PICO` - Wiznet **W5500** Ethernet PHY

Three boards, two chips (W5100S and W5500 are the same vendor's SPI Ethernet family, close enough
in register shape that one `ExternalDevice` may cover both, or may not - not investigated). If an
Ethernet device is ever built, this unblocks three boards at once, not one.

## Checklist: needs 2+ new devices, has a MicroPython port

Theoretically supportable - the RP2040 chip itself is never the blocker here, only the count of
device emulators still missing - so kept as real checklist items rather than dropped. Each is
further out than the two lists above simply because more has to be built first; none of this is a
"never".

- [ ] `ARDUINO_NANO_RP2040_CONNECT` - a NINA-W102 wifi/BT module (a different chip from `Cyw43439`
      entirely - Pico W's onboard chip is a CYW43439, this board's is a u-blox NINA-W102, ESP32-based),
      an IMU (LSM6DSOX), a microphone (MP34DT06J PDM mic), and a proximity/light/gesture sensor
      (APDS9960) - four missing devices
- [ ] `CYTRON_NANOXRP_CONTROLLER` - a motor driver, a buzzer, an ultrasonic distance sensor, and
      line sensors - four missing devices (its `Cyw43439` half is already covered by this project)
- [ ] `POLOLU_3PI_2040_ROBOT` - motor drivers, an IMU, a buzzer, and various onboard sensors -
      several missing devices, exact count/parts not itemized this pass
- [ ] `POLOLU_ZUMO_2040_ROBOT` - motor drivers, an IMU, a buzzer, and various onboard sensors -
      same shape as `POLOLU_3PI_2040_ROBOT`, exact count/parts not itemized this pass
- [ ] `SPARKFUN_THINGPLUS` - a MAX17048 battery fuel gauge and a microSD slot - two missing devices
- [ ] `SPARKFUN_XRP_CONTROLLER_BETA` - a motor driver, a distance sensor, and line sensors - three
      missing devices (its `Cyw43439` half is already covered)

## Checklist: addable now, CircuitPython-only (no MicroPython port)

`board.c`/`pins.c`-confirmed onboard peripherals are a subset of {nothing extra, `LEDMock`,
`KeyMock`, `Ws2812`} for every one of these:

- [ ] `0xcb_gemini` - NeoPixel
- [ ] `0xcb_helios` - LED + a single-wire RGB pin that looks WS2812-compatible (not fully confirmed)
- [ ] `42keebs_frood` - LED
- [ ] `8086_rp2040_interfacer` - button + 3 plain LEDs
- [ ] `8086_usb_interposer` - button + 5 plain LEDs (the USB-host pins are passthrough/ADC only, no
      chip)
- [ ] `adafruit_feather_rp2040_scorpio` - LED + 8 NeoPixel outputs
- [ ] `adafruit_feather_rp2040_usb_host` - LED + NeoPixel + boot button
- [ ] `adafruit_kb2040` - button + NeoPixel
- [ ] `adafruit_qt2040_trinkey` - button + NeoPixel
- [ ] `boardsource_blok` - NeoPixel
- [ ] `bwshockley_figpi` - button + LED + NeoPixel
- [ ] `cosmo_pico` - bare GPIO breakout
- [ ] `cytron_maker_nano_rp2040` - button + LED + NeoPixel
- [ ] `cytron_maker_pi_rp2040` - LED + NeoPixel
- [ ] `datanoise_picoadk` - NeoPixel
- [ ] `e_fidget` - NeoPixel
- [ ] `elecfreaks_picoed` - 2 buttons + LED
- [ ] `electrolama_minik` - LED + NeoPixel
- [ ] `maple_elite_pi` - bare breakout
- [ ] `melopero_shake_rp2040` - LED + NeoPixel
- [ ] `odt_bread_2040` - NeoPixel
- [ ] `odt_cast_away_rp2040` - NeoPixel
- [ ] `orpheus_pico` - button + LED + NeoPixel
- [ ] `pcbcupid_glyph_mini_2040` - LED
- [ ] `pimoroni_interstate75` - RGB status LED + user switch (the HUB75 pins are just a header - no
      panel is a fixed onboard part)
- [ ] `pimoroni_pga2040` - bare breakout
- [ ] `rfguru_rp2040` - bare breakout
- [ ] `solderparty_rp2040_stamp` - NeoPixel
- [ ] `splitkb_liatris` - NeoPixel + power LED
- [ ] `takayoshiotake_octave_rp2040` - NeoPixel
- [ ] `waveshare_rp2040_one` - NeoPixel
- [ ] `waveshare_rp2040_tiny` - NeoPixel
- [ ] `weenoisemakers_noisenugget` - NeoPixel
- [ ] `wisdpi_ardu2040m` - LED + NeoPixel
- [ ] `wisdpi_tiny_rp2040` - LED + NeoPixel
- [ ] `wk-50` - NeoPixel
- [ ] `zrichard_rp2.65-f` - LED

## Checklist: needs new device(s), CircuitPython-only (no MicroPython port)

Grouped by missing chip where several boards share one - building that one device unblocks all of
them at once, same as the Ethernet-PHY case above. Board count and split (single-device vs. 2+)
comes from re-counting this record's own list against the source survey, not restated from the
agent's own summary tally verbatim - the two differ by a few (53 vs. a stated 56 here, 37 vs. 38 in
the addable list above), almost certainly a grouped-row counting slip on one side or the other, not
re-verified against source. Not a blocker for using this as a checklist, since every row is still an
independently checkable claim.

Shared across 2+ boards:

- [ ] **SD card slot** (5): `adafruit_feather_rp2040_adalogger`, `adafruit_metro_rp2040`,
      `cytron_edu_pico_w` (its `Cyw43439` half already covered), `pimoroni_pico_dv_base_w`,
      `waveshare_rp2040_pizero`
- [ ] **UART AT-command ESP WiFi/BT co-processor** (3, not `Cyw43439`): `challenger_nb_rp2040_wifi`,
      `challenger_rp2040_wifi`, `challenger_rp2040_wifi_ble`
- [ ] **Buzzer** (6): `cytron_maker_uno_rp2040`, `jpconstantineau_encoderpad_rp2040`,
      `jpconstantineau_pykey18`, `jpconstantineau_pykey44`, `jpconstantineau_pykey60`,
      `jpconstantineau_pykey87`
- [ ] **ST7789 TFT** (2, confirmed via init-sequence bytes): `lilygo_t_display_rp2040`,
      `pimoroni_picosystem`
- [ ] **UC8151 EPD controller** (2, named in source; ≠ `Epd2in9G`): `pimoroni_badger2040`,
      `pimoroni_badger2040w`

Single board, single device:

- [ ] `adafruit_feather_rp2040_can` - MCP2515 CAN controller
- [ ] `adafruit_feather_rp2040_prop_maker` - accelerometer (LIS3DH-class)
- [ ] `adafruit_feather_rp2040_rfm` - RFM95/RFM69 radio module (footprint only, exact part not
      populated in source)
- [ ] `adafruit_feather_rp2040_thinkink` - e-paper controller (chip unspecified in source)
- [ ] `challenger_rp2040_lora` - RFM95W LoRa radio
- [ ] `challenger_rp2040_lte` - u-blox SARA cellular modem
- [ ] `challenger_rp2040_subghz` - RFM69HCW sub-GHz radio
- [ ] `hack_club_sprig` - an "ST7735R"-family TFT (Adafruit's own library name) - possibly the same
      silicon as this project's existing `St7735s` (Sitronix ST7735S), possibly a distinct part;
      worth checking exact compatibility before assuming either way
- [ ] `hxr_sao_dmm` - I2C OLED (chip unconfirmed, likely SSD1306-family)
- [ ] `odt_rpga_feather` - Lattice iCE40-family FPGA
- [ ] `pimoroni_motor2040` - motor driver (same class the robot boards above need)
- [ ] `pimoroni_servo2040` - analog multiplexer chip (shared current/voltage sense)
- [ ] `pimoroni_tinyfx` - audio amplifier
- [ ] `pimoroni_keybow2040` - IS31FL3731-class I2C LED-matrix driver
- [ ] `solderparty_bbq20kbd` - trackpad/keyboard controller chip
- [ ] `sparkfun_micromod_rp2040` - APA102/DotStar driver (2-wire, ≠ WS2812)
- [ ] `ugame22` - ILI9341 TFT (confirmed via init byte signature `0xEF`/`0xCF`)

Needs 2+:

- [ ] `adafruit_floppsy_rp2040` - TFT (chip unclear), AW9523 I2C GPIO expander, floppy motor driver,
      SD card - 4
- [ ] `archi` - IMU (MPU-class), PDM mic, buzzer - 3
- [ ] `bradanlanestudio_explorer_rp2040` - SSD1608/SSD1681-class EPD, IR transceiver, speaker - 3
- [ ] `breadstick_raspberry` - APA102 driver (2-wire, ≠ WS2812), IMU - 2
- [ ] `challenger_rp2040_sdrtc` - SD card, RTC chip - 2
- [ ] `heiafr_picomo_v2` / `heiafr_picomo_v3` - ST7789 TFT (confirmed via init-sequence registers
      `PORCTRL`/`GCTRL`/`VCOMS`), temperature sensor - 2 each
- [ ] `pajenicko_picopad` - ST7789 (confirmed via init sequence), SD card - 2
- [ ] `pimoroni_inky_frame_5_7` / `pimoroni_inky_frame_7_3` - SPD1656 EPD controller (7-color ACeP;
      named in source), SD card - 2 each
- [ ] `proveskit_rp2040_v4` - RF radio module, external watchdog chip - 2
- [ ] `tinycircuits_thumby` - SSD1306-family OLED (confirmed via init byte sequence), speaker - 2
- [ ] `waveshare_rp2040_geek` - ST7789 (named in source), SD card - 2
- [ ] `waveshare_rp2040_lcd_1_28` / `waveshare_rp2040_touch_lcd_1_28` - round-LCD controller + IMU
      (commonly documented by Waveshare as GC9A01 + QMI8658, but not encoded in this C source since
      the display isn't auto-initialized) - 2 each

## Flagged: not a device-count problem at all

Three CircuitPython-only boards that don't fit "board = plain RP2040 + `ExternalDevice`s" cleanly -
noted separately rather than folded into either checklist above, since building more devices
wouldn't unblock them:

- **`adafruit_feather_rp2040_dvi` / `pimoroni_pico_dv_base`** output raw DVI/HDMI via a PIO-driven
  framebuffer, with no external controller chip at all - not a missing `ExternalDevice` so much as a
  different, currently-unsupported *output modality*. (`pimoroni_pico_dv_base` also needs an SD
  card, which stays a normal single-device gap independent of the DVI question.)
- **`wiznet_w55rp20_evb_pico`** has its Ethernet MAC integrated on the same die as the RP2040 cores
  (a W55RP20 SoC), not a bus-attached peripheral chip like the W5500/W5100S boards above - the
  `ExternalDevice` model (an external chip talking to an otherwise-unmodified `RP2040`) doesn't
  describe this board's actual architecture.

## Explicitly excluded, not tracked further here

- **RP2350 (17 boards, out of scope - different chip):** 11 with a MicroPython port -
  `CYTRON_MOTION_2350_PRO`, `RPI_PICO2`, `RPI_PICO2_W`, `SEEED_XIAO_RP2350`,
  `SPARKFUN_IOTNODE_LORAWAN_RP2350`, `SPARKFUN_IOTREDBOARD_RP2350`, `SPARKFUN_PROMICRO_RP2350`,
  `SPARKFUN_THINGPLUS_RP2350`, `SPARKFUN_XRP_CONTROLLER`, `WAVESHARE_RP2350B_CORE`,
  `WEACTSTUDIO_RP2350B_CORE`; 6 more, CircuitPython-only - `adafruit_fruit_jam`,
  `cytron_edu_v2_pico_2w`, `cytron_iriv_io_controller`, `datanoise_picoadk_v2`,
  `studiolab_picoexpander`, `tinycircuits_thumby_color`. This project emulates the RP2040 chip only
  (a different core/peripheral set), so all 17 stay out of scope regardless of how simple their
  peripherals are, unlike the "needs device(s)" checklists above.

## Not decided here

- Which board (if any) to actually add first, or whether to add several at once. The "addable now"
  checklist above is ordered as the survey found the boards, not by priority.
- Whether `NULLBITS_BIT_C_PRO`/`PIMORONI_TINY2040`'s three-GPIO RGB LEDs are better modelled as
  three separate `LEDMock` instances or deserve a small `RGBLEDMock` combinator - not designed here.
- Whether one Ethernet-PHY `ExternalDevice` can cover both W5500 and W5100S, or whether they need
  two - not investigated at the register level.
