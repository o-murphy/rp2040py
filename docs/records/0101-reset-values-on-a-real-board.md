# 0101 - the chip's reset values, read on a real board

Status: proposed (2026-10-16), **nothing built, waiting for a real RP2040 board** in the maintainer's hands. A companion to [0096](0096-cpp-mcu-core.md) (gate G3) and [0098](0098-datasheet-conformance-audit.md).

## Why this record exists

Gate G3 of 0096 asks that a reset leaves the chip as the datasheet says. `tests/test_reset_values_svd.py` holds a fresh chip, in both builds, to Raspberry Pi's `RP2040.svd` (pico-sdk, BSD-3-Clause)
register by register, and `tests/test_reset_state.py` holds a reset chip to a fresh one. The first pass found two real defects (DMA channel registers and PWM `INTR/INTE/INTF` survived a reset; the DMA
`CHAIN_TO` reset was the channel's own number where the datasheet and the SVD say 0 - all fixed, 0096 progress log of 2026-10-16) and left **56 registers on which the chip is meant to read otherwise**,
listed with a reason in `tests/data/rp2040_reset_deviations.json`. Those reasons are a reading of the datasheet and of pico-sdk, **not measurements**: the SVD's reset column is partly a placeholder (a live
status bit, a constant of the silicon), partly describes power-on where the chip starts after the boot ROM, and in four places contradicts the datasheet's own text.

## The decision of 2026-10-16

The maintainer's call: the list stays as it is, nothing is bent towards the SVD for now, and the open questions are settled **on a real board** - measure the register, then either the list entry gets its
measured reason or the chip changes. (The alternative that was weighed - align the stored registers to the SVD first and compare with silicon only if firmware breaks - was set aside: the SVD and the datasheet
disagree on XOSC/ROSC, and the constants and statuses cannot be aligned without breaking firmware that reads them.) Nothing in this record authorises a change to the chip.

## What to do with the board

1. Power-cycle the board and read each register below **before** anything configures it where that is possible, and after a plain boot (MicroPython `machine.mem32[addr]`, or a pico-sdk program that prints the
   words over the UART), noting the boot ROM stepping (`SYSINFO.CHIP_ID` REVISION, ROM version byte at `0x13`) and the board.
2. Where a register is read-only or a status, note the conditions (pins floating or wired, peripherals idle, USB attached).
3. For each row below write down the measured value and either confirm the reason or replace it; if the chip's value is wrong, fix the reference and the C++ together (the usual route: oracle mutant, test) and
   update `rp2040_reset_deviations.json`.
4. The four write-only registers (`UARTICR`, `SSPICR`) were meant to read 0 in the chip (they read back the last raw write today); the maintainer wants to see the silicon first.
5. Also worth reading while the board is there, as they feed the other half of G3 (what each reset trigger keeps and what it clears): SRAM and the watchdog scratch registers after a watchdog reset, a RUN-pin
   reset and a power cycle; `VREG_AND_CHIP_RESET.CHIP_RESET` after each; PLL/XOSC/ROSC state after each; `RESETS.RESET` at the first instruction of the boot ROM's hand-over.

## The registers

`the chip reads` is the value of a fresh chip (masked to the readable bits), as the test pins it.

### Model choices (3)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `SYSINFO.CHIP_ID` | `0x10002927` | `0x20002927` | REVISION follows the boot ROM that is loaded (pico-sdk: ROM version byte 3 = B2 -> revision 2, otherwise 1); the chip ships the B1 image and reads revision 1, the SVD's 2 is B2 (record 0098) |
| `PPB.SYST_RVR` | `0xffffff` | `0x0` | ARMv6-M leaves SysTick RVR/CVR unknown at reset; the model starts them at 0xFFFFFF (the SVD says 0) |
| `PPB.SYST_CVR` | `0xffffff` | `0x0` | ARMv6-M leaves SysTick RVR/CVR unknown at reset; the model starts them at 0xFFFFFF (the SVD says 0) |

### Where the datasheet text and the SVD disagree (4)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `XOSC.CTRL` | `0xaa0` | `0x0` | datasheet 2.16: FREQ_RANGE resets to 0xAA0 and STARTUP.DELAY to 0xC4 (record 0098); the SVD's reset column is 0 |
| `XOSC.DORMANT` | `0x77616b65` | `0x0` | datasheet 2.16.7: 'On power-up this field is initialised to WAKE' (0x77616b65); the SVD's reset column is 0 |
| `XOSC.STARTUP` | `0xc4` | `0x0` | datasheet 2.16: FREQ_RANGE resets to 0xAA0 and STARTUP.DELAY to 0xC4 (record 0098); the SVD's reset column is 0 |
| `ROSC.DORMANT` | `0x77616b65` | `0x0` | datasheet 2.17.7: 'On power-up this field is initialised to WAKE' (0x77616b65); the SVD's reset column is 0 |

### The state the boot ROM leaves (6)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `RESETS.RESET` | `0x0` | `0x1ffffff` | the chip starts where the boot ROM leaves it, at the flash entry with every block out of reset and the power-on sequence done (the SVD describes power-on) |
| `RESETS.RESET_DONE` | `0x1ffffff` | `0x0` | the chip starts where the boot ROM leaves it, at the flash entry with every block out of reset and the power-on sequence done (the SVD describes power-on) |
| `PSM.DONE` | `0x1ffff` | `0x0` | the chip starts where the boot ROM leaves it, at the flash entry with every block out of reset and the power-on sequence done (the SVD describes power-on) |
| `ROSC.CTRL` | `0xfabaa0` | `0xaa0` | the ring oscillator is running after the boot ROM (datasheet: ENABLE is initialised to ENABLE on power-up; the divider is the boot ROM's); the SVD's reset column is a placeholder 0 |
| `ROSC.DIV` | `0xab0` | `0x0` | the ring oscillator is running after the boot ROM (datasheet: ENABLE is initialised to ENABLE on power-up; the divider is the boot ROM's); the SVD's reset column is a placeholder 0 |
| `ROSC.STATUS` | `0x80011000` | `0x0` | the ring oscillator is running after the boot ROM (datasheet: ENABLE is initialised to ENABLE on power-up; the divider is the boot ROM's); the SVD's reset column is a placeholder 0 |

### Constants of the silicon (8)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `SYSINFO.PLATFORM` | `0x2` | `0x0` | a constant of the silicon (ASIC=1, FPGA=0); the SVD's reset column is a placeholder 0 |
| `SYSINFO.GITREF_RP2040` | `0xe0c912e8` | `0x0` | the git hash of the RP2040 RTL, a constant of the silicon; the SVD's reset column is a placeholder 0 |
| `DMA.N_CHANNELS` | `0xc` | `0x0` | 12 channels, a constant of the silicon; the SVD's reset column is a placeholder 0 |
| `PIO0.DBG_CFGINFO` | `0x200404` | `0x0` | FIFO depth 4, 4 state machines, 32 instruction words: constants of the PIO block; the SVD's reset column is a placeholder 0 |
| `PIO1.DBG_CFGINFO` | `0x200404` | `0x0` | FIFO depth 4, 4 state machines, 32 instruction words: constants of the PIO block; the SVD's reset column is a placeholder 0 |
| `PPB.SYST_CALIB` | `0x270f` | `0x0` | a constant of the SysTick of this chip (TENMS); the SVD's reset column is a placeholder 0 |
| `PPB.AIRCR` | `0xfa050000` | `0x0` | ARMv6-M: VECTKEYSTAT reads 0xFA05 in the upper half; the SVD's reset column is a placeholder 0 |
| `PPB.CCR` | `0x208` | `0x0` | ARMv6-M: UNALIGN_TRP (bit 3) and STKALIGN (bit 9) read as 1; the SVD's reset column is a placeholder 0 |

### Live status bits (28)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `XIP_CTRL.STAT` | `0x3` | `0x2` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `SSI.SR` | `0x6` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `SSI.RISR` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `IO_BANK0.INTR0` | `0x11111111` | `0x0` | raw interrupt status of the pins' levels (an undriven input reads low: LEVEL_LOW set); the SVD's reset column is a placeholder 0 |
| `IO_BANK0.INTR1` | `0x11111111` | `0x0` | raw interrupt status of the pins' levels (an undriven input reads low: LEVEL_LOW set); the SVD's reset column is a placeholder 0 |
| `IO_BANK0.INTR2` | `0x11111111` | `0x0` | raw interrupt status of the pins' levels (an undriven input reads low: LEVEL_LOW set); the SVD's reset column is a placeholder 0 |
| `IO_BANK0.INTR3` | `0x111111` | `0x0` | raw interrupt status of the pins' levels (an undriven input reads low: LEVEL_LOW set); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SCLK_STATUS` | `0x3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SS_STATUS` | `0xa3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SD0_STATUS` | `0x3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SD1_STATUS` | `0x3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SD2_STATUS` | `0x3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.GPIO_QSPI_SD3_STATUS` | `0x3000` | `0x0` | live status of the QSPI pins (INFROMPAD, OUTTOPAD...); the SVD's reset column is a placeholder 0 |
| `IO_QSPI.INTR` | `0x111121` | `0x0` | raw interrupt status of the pins' levels (an undriven input reads low: LEVEL_LOW set); the SVD's reset column is a placeholder 0 |
| `BUSCTRL.BUS_PRIORITY_ACK` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C0.IC_INTR_STAT` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C0.IC_RAW_INTR_STAT` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C0.IC_CLR_INTR` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C1.IC_INTR_STAT` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C1.IC_RAW_INTR_STAT` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `I2C1.IC_CLR_INTR` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `ADC.FCS` | `0x100` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `ADC.INTR` | `0x1` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `ROSC.RANDOMBIT` | `0x0` | `0x1` | the live bit of the ring oscillator's random generator |
| `VREG_AND_CHIP_RESET.CHIP_RESET` | `0x100` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `PIO0.INTR` | `0xf0` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `PIO1.INTR` | `0xf0` | `0x0` | live status of an empty FIFO / idle block / the reset cause (HAD_POR), not a configured value; the SVD's reset column is a placeholder |
| `SIO.GPIO_HI_IN` | `0x2` | `0x0` | the QSPI pins' input levels (SS is pulled up); the SVD's reset column is a placeholder 0 |

### Not implemented (3)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `DMA.SNIFF_CTRL` | `0xfff` | `0x0` | not implemented (the DMA sniffer; the inter-core FIFO, which needs core 1): the read warns and returns all ones |
| `DMA.SNIFF_DATA` | `0xffffffff` | `0x0` | not implemented (the DMA sniffer; the inter-core FIFO, which needs core 1): the read warns and returns all ones |
| `SIO.FIFO_ST` | `0xf` | `0x2` | not implemented (the DMA sniffer; the inter-core FIFO, which needs core 1): the read warns and returns all ones |

### Write-only registers (4)

| register | the chip reads | the SVD says | why they differ (today's reading) |
|---|---|---|---|
| `UART0.UARTICR` | `0x7ff` | `0x0` | write-only register that reads back all ones (the last raw write); the datasheet gives no read value |
| `UART1.UARTICR` | `0x7ff` | `0x0` | write-only register that reads back all ones (the last raw write); the datasheet gives no read value |
| `SPI0.SSPICR` | `0x3` | `0x0` | write-only register that reads back all ones (the last raw write); the datasheet gives no read value |
| `SPI1.SSPICR` | `0x3` | `0x0` | write-only register that reads back all ones (the last raw write); the datasheet gives no read value |

## Not covered by this list

The 49 registers a read changes (FIFOs, spinlocks, `TIMELR`, `RTC_0`, ...) are not in the SVD comparison at all. The SIO block and the PPB registers are in it, but the USB DPRAM and the SVD's `VIRTUAL` placeholder are not.

## Progress log

- 2026-10-16: proposed; the list is the one of `tests/data/rp2040_reset_deviations.json` at `f1cb626`.
