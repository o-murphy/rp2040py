# 0098 - the ported blocks against the RP2040 datasheet

Status: in progress (opened 2026-10-06). Companion to [0096](0096-cpp-mcu-core.md): the C++ ports are held to the pure-Python *reference* by lockstep oracles, which proves the C++ is the
reference - not that the reference is the chip. The references came from rp2040js and from reading, in sessions that could not reach the manuals; the egress was opened for
`*.arm.com` and `*.raspberrypi.com` on 2026-10-06, so this record is where every block that is ported on the 0096 branch is read against the datasheet (`RP-008371-DS-1`).

## Method

1. **Register level, mechanical** - `scripts/audit/datasheet_conformance.py <datasheet text>`. The datasheet is turned into text with `pdftotext -layout`; the tool parses each "BLOCK: NAME Register"
   table (offset, bits, type, reset) and, on a fresh pure-Python chip, reads the register and writes it all ones and reads it back. Its categories and its blind spots are in its docstring; the short
   version: it sees resets of *writable* fields, reserved or read-only bits that stick, plain RW bits that do not, and registers the reference does not have. It does not see behaviour, the registers
   after the first of a `CH0_..., CH1_...` header, or read side effects. A finding is a question for the datasheet.
2. **Behaviour, by reading** - per block, the datasheet's description of what the block does against the reference's code. Each block gets its own section below when it is done.
3. **Fixes** - one commit per fix or per same-class group inside a block, in the reference and the C++ together (the oracles keep them equal), with an oracle mutant and a test; both builds green.

Where the datasheet is silent or defers to the ARM manual (as for SysTick in 0096), the note says so and what is the model's own.

## Register-level findings (first pass, before triage)

*Re-run after the fixes below (end of 2026-10-06, the tool's parser now also reads the per-slice and per-channel tables): 296 registers checked, 47 with findings, `{'unimplemented': 19, 'rw-not-sticking': 16, 'unimpl-wo': 3, 'reset': 5, 'raises': 1}`. What is left is the tool's own blind spots (write-only and status registers, placeholder resets, PIO CTRL needing an event loop) and the features named "left" or "not implemented" in each section: the DMA sniffer, the clock frequency counter FC0_*, CLOCKS ENABLED0/1, the inter-core FIFO, the I2C and SSI DMA/interrupt machinery. The list below is the first pass as it stood before any fix.*

Checked: 282 registers of TIMER, PWM, ADC, UART, SPI, I2C, DMA, PIO, SSI, SIO, CLOCKS, PLL. Findings by category: {'reserved-or-ro-sticks': 24, 'unimplemented': 57, 'reset': 11, 'rw-not-sticking': 20, 'unimpl-wo': 1, 'raises': 1}.
Known tool weaknesses seen in this pass: write-only registers whose table has a single unnamed field are not recognised as write-only (TIMER TIMEHW/TIMELW, UART UARTICR, SPI SSPICR show as "unimplemented"
though nothing reads them on the chip); the PCELLID/PERIPHID rows are read-only IDs the tool judges as writable; PIO CTRL needs an event loop, which the tool does not provide.


### TIMER  (17 registers, 3 with findings)

- `TIMER.TIMEHW` 0x00
  - unimplemented (read warns)
- `TIMER.TIMELW` 0x04
  - unimplemented (read warns)
- `TIMER.DBGPAUSE` 0x2c
  - unimplemented (read warns)

### PWM  (5 registers, 0 with findings)

No register-level findings.


### ADC  (9 registers, 4 with findings)

- `ADC.CS` 0x00
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000100, datasheet 0x00000000
  - reserved or read-only bits changed by a write of ones: 0x00000100 (read 0x00000100 -> 0x001f700b; datasheet writable mask 0x001f740f)
- `ADC.FCS` 0x08
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000100, datasheet 0x00000000
- `ADC.DIV` 0x10
  - reserved or read-only bits changed by a write of ones: 0xff000000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x00ffffff)
- `ADC.INTR` 0x14
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000001, datasheet 0x00000000

### UART  (21 registers, 8 with findings)

- `UART.UARTRSR` 0x004
  - unimplemented (read warns)
- `UART.UARTILPR` 0x020
  - unimplemented (read warns)
- `UART.UARTLCR_H` 0x02c
  - reserved or read-only bits changed by a write of ones: 0xffffff00 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x000000ff)
- `UART.UARTCR` 0x030
  - reserved or read-only bits changed by a write of ones: 0xffff0078 (read 0x00000300 -> 0xffffffff; datasheet writable mask 0x0000ff87)
- `UART.UARTIFLS` 0x034
  - unimplemented (read warns)
- `UART.UARTICR` 0x044
  - unimplemented (read warns)
- `UART.UARTDMACR` 0x048
  - unimplemented (read warns)
- `UART.UARTPCELLID3` 0xffc
  - reset: read 0x000000b1, datasheet 0x00000065, differing writable bits 0x000000d4
  - RW bits that read 0 after a write of ones: 0x0000034e (read back 0x000000b1)

### SPI  (18 registers, 8 with findings)

- `SPI.SSPCR0` 0x000
  - reserved or read-only bits changed by a write of ones: 0xffff0000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x0000ffff)
- `SPI.SSPCR1` 0x004
  - reserved or read-only bits changed by a write of ones: 0xfffffff0 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x0000000f)
- `SPI.SSPCPSR` 0x010
  - RW bits that read 0 after a write of ones: 0x00000001 (read back 0x000000fe)
- `SPI.SSPIMSC` 0x014
  - reserved or read-only bits changed by a write of ones: 0xfffffff0 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x0000000f)
- `SPI.SSPRIS` 0x018
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000000, datasheet 0x00000008
- `SPI.SSPICR` 0x020
  - unimplemented (read warns)
- `SPI.SSPDMACR` 0x024
  - reserved or read-only bits changed by a write of ones: 0xfffffffc (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x00000003)
- `SPI.SSPPCELLID3` 0xffc
  - reset: read 0x000000b1, datasheet 0x0000ffff, differing writable bits 0x0000ff4e
  - RW bits that read 0 after a write of ones: 0xffffff4e (read back 0x000000b1)

### I2C  (40 registers, 14 with findings)

- `I2C.IC_TAR` 0x04
  - RW bits that read 0 after a write of ones: 0x00000c00 (read back 0x000003ff)
- `I2C.IC_DATA_CMD` 0x10
  - RW bits that read 0 after a write of ones: 0x000000ff (read back 0x00000000)
- `I2C.IC_RX_TL` 0x38
  - RW bits that read 0 after a write of ones: 0x000000ef (read back 0x00000010)
- `I2C.IC_TX_TL` 0x3c
  - RW bits that read 0 after a write of ones: 0x000000ef (read back 0x00000010)
- `I2C.IC_ENABLE` 0x6c
  - reserved or read-only bits changed by a write of ones: 0xfffffff8 (read 0x00000000 -> 0xfffffffd; datasheet writable mask 0x00000007)
  - RW bits that read 0 after a write of ones: 0x00000002 (read back 0xfffffffd)
- `I2C.IC_SDA_HOLD` 0x7c
  - RW bits that read 0 after a write of ones: 0x00fffffe (read back 0x00000001)
- `I2C.IC_SLV_DATA_NACK_ONLY` 0x84
  - unimplemented (read warns)
- `I2C.IC_DMA_CR` 0x88
  - unimplemented (read warns)
- `I2C.IC_DMA_TDLR` 0x8c
  - unimplemented (read warns)
- `I2C.IC_DMA_RDLR` 0x90
  - unimplemented (read warns)
- `I2C.IC_SDA_SETUP` 0x94
  - unimplemented (read warns)
- `I2C.IC_ACK_GENERAL_CALL` 0x98
  - unimplemented (read warns)
- `I2C.IC_CLR_RESTART_DET` 0xa8
  - unimplemented (read warns)
- `I2C.IC_COMP_PARAM_1` 0xf4
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000000, datasheet 0x3230312a

### DMA  (13 registers, 7 with findings)

- `DMA.INTS1` 0x41c
  - RW bits that read 0 after a write of ones: 0xffffffff (read back 0x00000000)
- `DMA.MULTI_CHAN_TRIGGER` 0x430
  - unimplemented (read warns)
- `DMA.SNIFF_CTRL` 0x434
  - unimplemented (read warns)
- `DMA.SNIFF_DATA` 0x438
  - unimplemented (read warns)
- `DMA.FIFO_LEVELS` 0x440
  - unimplemented (read warns)
- `DMA.CHAN_ABORT` 0x444
  - unimplemented (read warns)
- `DMA.N_CHANNELS` 0x448
  - reset: read 0x0000000c, datasheet 0x00000000, differing writable bits 0x0000000c
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x0000000c, datasheet 0x00000000

### PIO  (17 registers, 3 with findings)

- `PIO.CTRL` 0x000
  - write/read-back raised RuntimeError: no running event loop
- `PIO.DBG_CFGINFO` 0x044
  - reset: read 0x00200404, datasheet 0x140df000, differing writable bits 0x142df404
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00200404, datasheet 0x00000000
  - RW bits that read 0 after a write of ones: 0xffdffbfb (read back 0x00200404)
- `PIO.INTR` 0x128
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x000000f0, datasheet 0x00000000

### SSI  (28 registers, 23 with findings)

- `SSI.CTRLR0` 0x00
  - reserved or read-only bits changed by a write of ones: 0xfe800000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x017fffff)
- `SSI.CTRLR1` 0x04
  - reserved or read-only bits changed by a write of ones: 0xffff0000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x0000ffff)
- `SSI.SSIENR` 0x08
  - reserved or read-only bits changed by a write of ones: 0xfffffffe (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x00000001)
- `SSI.MWCR` 0x0c
  - unimplemented (read warns)
- `SSI.SER` 0x10
  - unimplemented (read warns)
- `SSI.BAUDR` 0x14
  - reserved or read-only bits changed by a write of ones: 0xffff0000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x0000ffff)
- `SSI.TXFTLR` 0x18
  - unimplemented (read warns)
- `SSI.RXFTLR` 0x1c
  - unimplemented (read warns)
- `SSI.TXFLR` 0x20
  - reserved or read-only bits changed by a write of ones: 0xffffffff (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x00000000)
- `SSI.SR` 0x28
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000006, datasheet 0x00000000
- `SSI.IMR` 0x2c
  - unimplemented (read warns)
- `SSI.ISR` 0x30
  - unimplemented (read warns)
- `SSI.RISR` 0x34
  - unimplemented (read warns)
- `SSI.TXOICR` 0x38
  - unimplemented (read warns)
- `SSI.RXOICR` 0x3c
  - unimplemented (read warns)
- `SSI.RXUICR` 0x40
  - unimplemented (read warns)
- `SSI.MSTICR` 0x44
  - unimplemented (read warns)
- `SSI.ICR` 0x48
  - unimplemented (read warns)
- `SSI.DMACR` 0x4c
  - unimplemented (read warns)
- `SSI.DMATDLR` 0x50
  - unimplemented (read warns)
- `SSI.DMARDLR` 0x54
  - unimplemented (read warns)
- `SSI.DR0` 0x60
  - RW bits that read 0 after a write of ones: 0xffffffff (read back 0x00000000)
- `SSI.SPI_CTRLR0` 0xf4
  - reset: read 0x00000000, datasheet 0x03000000, differing writable bits 0x03000000
  - reserved or read-only bits changed by a write of ones: 0x00f804c0 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0xff07fb3f)

### SIO  (62 registers, 15 with findings)

- `SIO.GPIO_HI_IN` 0x008
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000002, datasheet 0x00000000
- `SIO.GPIO_HI_OUT` 0x030
  - reserved or read-only bits changed by a write of ones: 0x3fffffc0 (read 0x00000000 -> 0x3fffffff; datasheet writable mask 0x0000003f)
- `SIO.GPIO_HI_OE` 0x040
  - reserved or read-only bits changed by a write of ones: 0x3fffffc0 (read 0x00000000 -> 0x3fffffff; datasheet writable mask 0x0000003f)
- `SIO.FIFO_ST` 0x050
  - unimplemented (read warns)
- `SIO.FIFO_WR` 0x054
  - unimplemented (read warns)
- `SIO.FIFO_RD` 0x058
  - unimplemented (read warns)
- `SIO.DIV_UDIVISOR` 0x064
  - reset: read 0x00000001, datasheet 0x00000000, differing writable bits 0x00000001
- `SIO.DIV_SDIVISOR` 0x06c
  - reset: read 0x00000001, datasheet 0x00000000, differing writable bits 0x00000001
- `SIO.DIV_CSR` 0x078
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00000000, datasheet 0x00000001
- `SIO.INTERP0_ACCUM0_ADD` 0x0b4
  - RW bits that read 0 after a write of ones: 0x00fffffe (read back 0x00000001)
- `SIO.INTERP0_ACCUM1_ADD` 0x0b8
  - RW bits that read 0 after a write of ones: 0x00fffffe (read back 0x00000001)
- `SIO.INTERP0_BASE_1AND0` 0x0bc
  - unimplemented (read warns) - write-only per datasheet
- `SIO.INTERP1_ACCUM0_ADD` 0x0f4
  - RW bits that read 0 after a write of ones: 0x00fffffe (read back 0x00000001)
- `SIO.INTERP1_ACCUM1_ADD` 0x0f8
  - RW bits that read 0 after a write of ones: 0x00fffffe (read back 0x00000001)
- `SIO.INTERP1_BASE_1AND0` 0x0fc
  - unimplemented (read warns)

### CLOCKS  (48 registers, 22 with findings)

- `CLOCKS.CLK_REF_DIV` 0x34
  - reset: read 0x00000000, datasheet 0x00000100, differing writable bits 0x00000100
  - reserved or read-only bits changed by a write of ones: 0x00000030 (read 0x00000000 -> 0x00000030; datasheet writable mask 0x00000300)
  - RW bits that read 0 after a write of ones: 0x00000300 (read back 0x00000030)
- `CLOCKS.CLK_USB_DIV` 0x58
  - reserved or read-only bits changed by a write of ones: 0xfffffcff (read 0x00000100 -> 0xffffffff; datasheet writable mask 0x00000300)
- `CLOCKS.CLK_ADC_DIV` 0x64
  - reset: read 0x00000000, datasheet 0x00000100, differing writable bits 0x00000100
  - reserved or read-only bits changed by a write of ones: 0x00000030 (read 0x00000000 -> 0x00000030; datasheet writable mask 0x00000300)
  - RW bits that read 0 after a write of ones: 0x00000300 (read back 0x00000030)
- `CLOCKS.CLK_RTC_DIV` 0x70
  - reset: read 0x00000000, datasheet 0x00000100, differing writable bits 0x00000100
  - RW bits that read 0 after a write of ones: 0xffffffcf (read back 0x00000030)
- `CLOCKS.CLK_SYS_RESUS_CTRL` 0x78
  - RW bits that read 0 after a write of ones: 0x00011100 (read back 0x000000ff)
- `CLOCKS.FC0_REF_KHZ` 0x80
  - unimplemented (read warns)
- `CLOCKS.FC0_MIN_KHZ` 0x84
  - unimplemented (read warns)
- `CLOCKS.FC0_MAX_KHZ` 0x88
  - unimplemented (read warns)
- `CLOCKS.FC0_DELAY` 0x8c
  - unimplemented (read warns)
- `CLOCKS.FC0_INTERVAL` 0x90
  - unimplemented (read warns)
- `CLOCKS.FC0_SRC` 0x94
  - unimplemented (read warns)
- `CLOCKS.FC0_STATUS` 0x98
  - unimplemented (read warns)
- `CLOCKS.WAKE_EN0` 0xa0
  - unimplemented (read warns)
- `CLOCKS.WAKE_EN1` 0xa4
  - unimplemented (read warns)
- `CLOCKS.SLEEP_EN0` 0xa8
  - unimplemented (read warns)
- `CLOCKS.SLEEP_EN1` 0xac
  - unimplemented (read warns)
- `CLOCKS.ENABLED0` 0xb0
  - unimplemented (read warns)
- `CLOCKS.ENABLED1` 0xb4
  - unimplemented (read warns)
- `CLOCKS.INTR` 0xb8
  - unimplemented (read warns)
- `CLOCKS.INTE` 0xbc
  - unimplemented (read warns)
- `CLOCKS.INTF` 0xc0
  - unimplemented (read warns)
- `CLOCKS.INTS` 0xc4
  - unimplemented (read warns)

### PLL  (4 registers, 4 with findings)

- `PLL.CS` 0x0
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x80000001, datasheet 0x00000000
  - reserved or read-only bits changed by a write of ones: 0x7ffffec0 (read 0x80000001 -> 0xffffffff; datasheet writable mask 0x0000013f)
- `PLL.PWR` 0x4
  - reserved or read-only bits changed by a write of ones: 0xffffffd2 (read 0x0000002d -> 0xffffffff; datasheet writable mask 0x0000002d)
- `PLL.FBDIV_INT` 0x8
  - reserved or read-only bits changed by a write of ones: 0xfffff000 (read 0x00000000 -> 0xffffffff; datasheet writable mask 0x00000fff)
- `PLL.PRIM` 0xc
  - reset: read 0x00077000, datasheet 0x0007701f, differing writable bits 0x0000001f
  - note (read-only bits, the datasheet's reset may be a placeholder): read 0x00077000, datasheet 0x00000000
  - reserved or read-only bits changed by a write of ones: 0xcff88ce0 (read 0x00077000 -> 0xffffffff; datasheet writable mask 0x3007731f)

## Triage and the behavioural audit

Per block, in the order of the 0096 ports. Each section says what the datasheet says, what the reference did, what was changed (reference and C++ together) and what is left.

### TIMER (datasheet 4.6)

Read: 4.6.1-4.6.4 and the register tables. The reference implemented the counter, the latch (`TIMELR`/`TIMEHR`), `TIMERAWL/H`, the four alarms with `ARMED`, `INTR/INTE/INTF/INTS`, and stored `PAUSE`.

- **`PAUSE` did nothing but store its bit and warn "Unimplemented Timer Pause".** The datasheet: "Set high to pause the timer". Now the count freezes, no alarm can come due, an alarm written while paused is armed and waits, and clearing `PAUSE` resumes from the frozen count with the armed alarms re-timed.
- **`TIMEHW`/`TIMELW` (write the time) were unimplemented** (a write warned and was dropped). The datasheet: "always write timelw before timehw"; "writes do not get copied to time until timehw is written". `TIMELW` is now a latch and the `TIMEHW` write sets the 64-bit time; the armed alarms are re-timed against the new count (an alarm matches the counter, not a moment of simulated time).
- **`DBGPAUSE` was unimplemented.** A 2-bit RW register (DBG1, DBG0), 1 at reset (`0x6`). Stored; no debugger is ever attached, so it changes nothing.
- **The 64-bit count was not bounded:** with the time now settable, `TIMERAWH`/`TIMEHR` could exceed 32 bits (the bus raised) and the C++ `to_uint32(double)` was undefined beyond int64. The high word is now masked to 32 bits (the counter wraps, as a 64-bit one does) and `to_uint32` is Python's `int(x) & 0xFFFFFFFF` exactly, read from the double's bits. The parity test found it the first time the new registers were in its stream.
- **Left, deliberately:** the timer counts whether or not the watchdog's 1 us tick is running (the datasheet: "The Watchdog tick must be running for the timer to start counting"). The tick belongs to the WATCHDOG, which is not ported yet; it is on that block's list. Reading `TIMEHW`/`TIMELW` (write-only) warns as an unimplemented read.
- Tests: `tests/test_timer.py` (the four behaviours), `tests/test_timer_parity.py` (the new offsets are in the random stream, 60 seeds), `tests/cpp/test_timer.cpp`.

### ADC (datasheet 4.9)

Read: 4.9.1-4.9.2.7 and the register tables.

- **The FIFO had 4 entries; the datasheet says "Eight element receive sample FIFO"** (and `FCS.LEVEL` is 4 bits wide). The reference and the C++ block now hold 8, so an overflow comes at the ninth sample and `LEVEL` reaches 8. The oracle's old mutant "the depth is 8" is now "the depth is 4".
- **`CS.READY` was 1 whenever no conversion was running, including with the ADC disabled.** The datasheet: "writing a 1 to CS.EN will start a short internal power-up sequence ... After a few clock cycles, CS.READY will go high"; reset value 0. READY is now `EN && !busy` (the power-up is not timed). pico-sdk's `adc_init` waits for it after setting EN, so a firmware that does is unaffected.
- **`DIV` stored the whole word;** bits 31:24 are reserved (INT is 23:8, FRAC 7:0). Masked to 24 bits.
- **Checked and consistent:** one conversion = 96 cycles of a 48 MHz clk_adc = 2 us; the pacing divider "once per n + 1 cycles" (`1 + INT + FRAC/256`) with the next start measured from the start of the previous one; `START_ONCE` self-clearing; the FIFO word (`ERR` bit 15, 12-bit value, `SHIFT` to bits 11:4); `OVER`/`UNDER` write-1-to-clear; the threshold for IRQ and DREQ is `level >= THRESH`; the round-robin order; `INTS`.
- **Left, deliberately:** the clk_adc frequency is a fixed 48 MHz (the datasheet: the clock "must be set up correctly before enabling the ADC"; a model that followed `CLK_ADC` would never convert for a firmware that does not program it, which the tests do not); `DIV.FRAC` is an average, not the first-order delta-sigma; "the divider is reset when either of these fields are written" is not modelled; `INTR` is 1 at `THRESH` 0 because `level >= 0` always holds (the datasheet's reset value is 0 - unresolved: it does not say what the comparator does at a threshold of 0).
- Tests: `tests/test_adc.py` (FIFO depth, READY, DIV mask), `tests/cpp/test_adc.cpp`, and the oracle's mutants (`fifo_depth_4`, `cs_ready_without_en`, `div_unmasked`; two of the old ones turned over).

### PWM (datasheet 4.5)

Read: 4.5.1-4.5.3 and the CSR/DIV/CTR/CC/TOP/EN/INT* tables. The period formula is an image in the PDF (the text extraction drops it); it was read from the rendered page: **period = (TOP + 1) x (CSR_PH_CORRECT + 1) x (DIV_INT + DIV_FRAC / 16)**. The tool had not checked the per-slice registers at all (their tables are headed `CH0_CSR, CH1_CSR, ..., CH7_CSR` and `Offsets: 0x00, 0x14, ...`); it does now.

- **Phase-correct mode was wrong in two ways - this resolves the "bug 4" that 0096 left in the backlog for the datasheet.** (1) The output: the wrap did not drive A/B in phase-correct mode and the compare alarms only ever *cleared* them, so a phase-correct output went low at the first compare and never rose again. (2) The period: the reference ran the counter as a zigzag of `2 * TOP` counts; the datasheet's period is `2 * (TOP + 1)` (the 0 and the TOP are each held for two counts - "the 0 to 0 count transition"). Both are fixed by changing the model, not by patching the symptoms: the slice's counter is **always an INCREMENT timer of one period of counts** (`TOP + 1`, or `2 * (TOP + 1)` in phase-correct mode), the output is **high while the counter is below CC** (Figure 104: "When the input value is higher than the counter, the output is driven high"), so an output *falls* when the counter reaches CC and, in phase-correct mode only, *rises* again on the way down through CC - 1; the wrap (the 0 to 0 transition) raises it and updates the latched copies. Two new compare alarms per slice (`alarm_a_rise`, `alarm_b_rise`) carry the rising events; a CC above TOP is never reached (100 %), a CC of 0 is never above the count (0 %). `CTR` shows the position on the triangle (0..TOP, TOP..0). The `Timer32` ZIGZAG mode is no longer used by the PWM (it stays in `Timer32`, tested on its own).
- **The internal copies of CC and TOP are explicit now** (`latched_cc_a/b`, `latched_top`), updated at the wrap or the enable ("updated from the first register at the instant the counter wraps"), so a change of PH_CORRECT while running recomputes the period and the events from the latched values.
- **`CSR` stored bits 31:8** (reserved) and **`DIV` bits 19:12**: now 0x3F (DIVMODE, B_INV, A_INV, PH_CORRECT, EN) and 0xFFF (INT 11:4, FRAC 3:0).
- **`PH_ADV` at full speed:** "Counter must be running at less than full speed (div_int + div_frac / 16 > 1)" - ignored now when the divider is 1 (there is no gap in the clock enable to insert a pulse into). The three `tests/test_pwm.py` tests that used it at DIV 1 now run at DIV 2 (rp2040js 1.4.0's fix was tested at full speed).
- **Checked and consistent:** the pin mapping (GPIO n to slice (n/2) mod 8, A/B by parity; both pins of the pair 16 apart carry the same signal; B inputs OR-ed), `EN` as an alias of the CSR_EN bits, `INTR` write-1-to-clear, one IRQ per wrap and the DREQ pulse, `DIV_INT` 0 meaning 256, the 100 % / 0 % rules, double buffering at the wrap in free-running mode.
- **Left, deliberately:** the fractional divider is an average, not the first-order delta-sigma; the exact counting of edges against the divider in the level/edge modes is as before; the A pin of a slice that is disabled keeps its last level.
- Tests: `tests/test_pwm_phase_correct.py` (nine datasheet-derived scenarios counted in clk_sys cycles: the hello_pwm example, the triangle, duty, 0/100 %, the IRQ at the 0 to 0 transition, double buffering at the wrap, the period formula with a fractional divider, PH_ADV, reserved bits), the oracle's mutants (the 16 that referred to the old text rewritten, about 20 new for the phase-correct events and the masks), `tests/cpp/test_pwm.cpp` against regenerated vectors (`tests/utils/pwm_cpp_vectors.py`).

### UART (datasheet 4.2, the PL011)

Read: the UARTDR, UARTRSR, UARTFR, UARTLCR_H, UARTCR, UARTIFLS, UARTIMSC, UARTRIS, UARTICR, UARTDMACR tables and the ID registers (the PERIPHID/PCELLID values the reference returns are right: `0x11 0x10 0x34 0x00`, `0x0D 0xF0 0x05 0xB1`; the tool's PCELLID3 "reset" finding is its own misparse).

- **Reserved bits stuck:** `UARTLCR_H` kept bits 31:8 and `UARTCR` bits 31:16 and 6:3. Now 0xFF and 0xFF87.
- **Missing registers, now there:** `UARTIFLS` (6 bits, reset 0x12), `UARTILPR` (8 bits), `UARTDMACR` (3 bits), `UARTRSR`/`UARTECR` (reads the error flags, a write clears them). `IFLS` and `ILPR` are **stored and not acted on**: the RX interrupt still comes with every byte (a superset of every trigger level: a driver that drains the FIFO in its handler behaves the same, and the receive-timeout interrupt that a trigger level would need is never needed), the TX FIFO never fills, there is no IrDA.
- **The DMA requests were wrong.** The reference raised the TX DREQ when UARTEN was set (whatever DMACR said) and never the RX one, so a DMA channel paced by `DREQ_UARTn_RX` could not run. Now TX asks while UARTEN, TXE and `DMACR.TXDMAE` are set (the FIFO never fills), and RX while UARTEN, RXE and `RXDMAE` are set and the FIFO holds a byte, unless `DMAONERR` is set and an error interrupt is up ("the DMA receive request outputs ... are disabled when the UART error interrupt is asserted"). Both are re-announced (TX then RX) on every change of CR, DMACR, the FIFO's emptiness, the error interrupts and at reset (a reset used to leave a stale request up). pico-sdk's `uart_init` writes `DMACR` with both enables (`src/rp2_common/hardware_uart/uart.c`, unless `PICO_UART_NO_DMACR_ENABLE`), so real firmware is unaffected.
- **A disabled UART still sent and received.** The datasheet: "TXE: ... If this bit is set to 1, the transmit section of the UART is enabled", RXE likewise, UARTEN gates both. A `UARTDR` write now sends only with UARTEN and TXE, and a byte fed from the wire is taken only with UARTEN and RXE (UARTEN is 0 after a reset until the firmware sets it; the tests that feed or send now enable the UART as a firmware does).
- **Overrun:** a byte that arrives with the 32-entry FIFO full is dropped as before, and now also sets `UARTRSR.OE` and the raw OE interrupt (bit 10 of `UARTRIS`), cleared by `UARTICR` / `UARTECR` ("no more data is written when the FIFO is full").
- **Checked and consistent:** the baud divisors (`IBRD` 16 bits, `FBRD` 6 bits, `baud = clk_peri / (16 x (IBRD + FBRD / 64))`), the interrupt mask/status/clear layout, `UARTFR` (TXFE and RXFE at reset; TXFF and BUSY never set because the transmitter is instant), the ID registers.
- **Left, deliberately:** the RX FIFO is 32 deep whatever `FEN` says (the datasheet: with FIFOs disabled it is a 1-byte holding register; but bytes arrive here in instant bursts that a real line would space at the baud rate, so a 1-deep register would drop what hardware would not); no break/parity/framing errors, no modem lines, no loopback, no timing of transmission.
- Tests: `tests/test_uart.py` (seven datasheet cases), `tests/cpp/test_uart.cpp` rewritten around the new DREQ rule and the gating, the oracle (`tests/utils/uart_diff.py`: the new registers in the stream, an enabling scenario so that traffic passes, 47 mutants - the three obsolete CR-DREQ ones replaced by 14 DREQ/gating/overrun/register mutants).

### SPI (datasheet 4.4, the PL022)

Read: 4.4.2 (the block), 4.4.3.2-4.4.3.6 (configuring, enabling, clock ratios, SSPCR0/SSPCR1, bit rate), the DMA interface text (4.4.3.16) and every register table (SSPCR0 .. SSPDMACR, the ID registers; the values the reference returns for those, `0x22 0x10 0x34 0x00` and `0x0D 0xF0 0x05 0xB1`, are right - the tool's PCELLID3 "reset" finding is its own misparse).

- **Reserved bits stuck:** SSPCR0 kept bits 31:16, SSPCR1 and SSPIMSC bits 31:4, SSPDMACR bits 31:2. Now 0xFFFF, 0xF, 0xF, 0x3. (SSPCPSR's bit 0 "not sticking" is right: "the least significant bit always returns zero on reads"; the reference already masked 0xFE.)
- **A disabled SSP sent.** "You can prime the transmit FIFO ... when the PrimeCell SSP is disabled ... Once enabled, transmission or reception of data begins" (4.4.3.3): the reference pushed a word to the device as soon as it was written, whatever SSE said. Now nothing leaves while SSE is 0 and a primed FIFO starts when SSPCR1 enables it.
- **The DMA requests ignored SSE and SSPDMACR.** "All request signals are deasserted if the PrimeCell SSP is disabled, or the DMA enable signal is cleared" (4.4.3.16): the reference kept the TX request up whenever the FIFO had room and the RX one whenever it had a word. Now TX needs SSE and TXDMAE, RX needs SSE and RXDMAE, and both are re-published on every write of SSPCR1 and SSPDMACR as well as on every FIFO change. pico-sdk's `spi_init` sets both SSPDMACR enables ("Always enable DREQ signals -- harmless if DMA is not listening") and then SSE - read from `src/rp2_common/hardware_spi/spi.c` (master) - so real firmware is unaffected.
- **Loop back (SSPCR1.LBM) was not implemented.** "Output of transmit serial shifter is connected to input of receive serial shifter internally": the word written to SSPDR now comes straight back into the RX FIFO and does not reach the device.
- **`SSPRIS` reset:** the datasheet has TXRIS = 1 at reset (the TX FIFO is empty, "half empty or less"); the reference started at 0 and only set it after the first FIFO change. Fixed at power-on and at `reset()`.
- **Checked and consistent:** the FIFOs (16 bits wide, 8 deep), the interrupt thresholds (TX at 4 or fewer, RX at 4 or more), the overrun interrupt, SSPICR clearing only RT and ROR, `SSPSR` (BSY while a frame is in flight or the TX FIFO is not empty), the bit-rate formula `clk_peri / (CPSDVSR x (1 + SCR))`, data size = DSS + 1.
- **Left, deliberately:** the frame format (FRF, TI/Microwire), the receive-timeout interrupt (RT never rises), slave mode (MS and SOD are stored; the block still masters), the burst DMA requests (the chip has one request per direction), the rule that MS can be changed only with SSE = 0, and the DSS values 0-2 (reserved, "undefined operation": treated as 1-3 bits).
- Tests: `tests/test_spi.py` (four datasheet cases), `tests/cpp/test_spi.cpp` (the three new behaviours, the old checks now run with the SSP enabled as a firmware has it), the oracle (`tests/utils/spi_diff.py`: SSPDMACR values in the stream, 13 new mutants for the masks, the gating, the loop back, the start on enable and the reset value; `imsc_masked`, which the fix made equal to the reference, became `imsc_unmasked`).

### I2C (datasheet 4.3, the DW_apb_i2c)

Read: every register table (IC_CON .. IC_COMP_TYPE) and the description text in them; **not** read: the protocol chapters (10-bit addressing sequences, the abort procedures, spike suppression, the clock-count formulas) - the master's bus sequencing is as before.

- **`FIRST_DATA_BYTE` was bit 10; the datasheet has it at bit 11** of what IC_DATA_CMD reads (bit 10 is RESTART, of a write). A driver testing the flag saw the wrong bit.
- **The configuration registers were writable while enabled.** IC_CON, IC_TAR, IC_SAR, the four SCL counts, IC_SDA_HOLD, IC_FS_SPKLEN: "can be written only when the DW_apb_i2c is disabled ... Writes at other times have no effect". Now gated on IC_ENABLE[0]. pico-sdk's `i2c_init`, `i2c_set_baudrate` and `i2c_write_blocking` all clear `enable` before writing `con`/`tar`/the counts (`src/rp2_common/hardware_i2c/i2c.c`, master), so real firmware is unaffected.
- **Widths and minima:** IC_CON 9:0 (bit 10 is read only), IC_TAR 11:0 (SPECIAL and GC_OR_START are stored; the general-call/START-byte behaviour they select is not modelled), IC_SDA_HOLD 23:0 (was a constant 1 with a warning), IC_ENABLE 2:0, IC_DATA_CMD 10:0 of a write. The SCL counts have minima - "hardware prevents values less than this being written, and if attempted results in 6 (8) being set" - and IC_FS_SPKLEN's is 1 (a 0 used to be ignored, it now sets 1).
- **Registers that were unimplemented, now stored** with their datasheet reset values and masks and **not acted on**: IC_SDA_SETUP (0x64), IC_ACK_GENERAL_CALL (1), IC_SLV_DATA_NACK_ONLY, IC_DMA_CR, IC_DMA_TDLR, IC_DMA_RDLR; IC_CLR_RESTART_DET clears RESTART_DET (nothing in a master raises it). There is no slave and no I2C DREQ in the chip, so the DMA registers are inert: **DMA-driven I2C is a feature, in the backlog**.
- **RX_FULL is a level:** "set when the receive buffer reaches or goes above the RX_TL threshold ... automatically cleared by hardware when buffer level goes below the threshold". The reference cleared it on *any* read of IC_DATA_CMD. Now re-evaluated on every push, pull, flush and IC_RX_TL write. **TX_EMPTY** is cleared by a write only when the level goes above IC_TX_TL.
- **A transmit abort flushes the RX FIFO as well** ("flushes/resets/empties the TX_FIFO and RX_FIFO whenever there is a transmit abort"; IC_RXFLR "is cleared ... whenever there is a transmit abort").
- **ACTIVITY was never raised.** Now set when the master starts, cleared by IC_CLR_ACTIVITY / IC_CLR_INTR "if the I2C is not active anymore" (not while it is), and by disabling.
- **Checked and consistent:** the FIFO depths (16), IC_STATUS composition, IC_TXFLR/IC_RXFLR, the abort-source bits and the accumulate-until-read rule, IC_INTR_MASK reset 0x8FF, IC_COMP_VERSION/TYPE.
- **Left, deliberately:** slave mode (RD_REQ, RX_DONE, GEN_CALL, RESTART_DET never rise), the general call / START byte / high-speed sequences, TX_EMPTY_CTRL, RX_FIFO_FULL_HLD_CTRL, STOP_DET_IF_MASTER_ACTIVE, the "FIFOs stay flushed until IC_CLR_TX_ABRT is read" rule, TX_OVER/RX_OVER/RX_UNDER keeping their level until idle, IC_ENABLE_STATUS bits 2:1.
- Tests: `tests/test_i2c.py` (three datasheet cases), `tests/cpp/test_i2c.cpp` (one directed test for the new behaviour, the old checks reordered to configure before enabling), the oracle (`tests/utils/i2c_diff.py`: the bring-up stream now disables, configures and enables as pico-sdk does, the new registers in the snapshot, 24 new mutants).

### SIO (datasheet 2.3.1 and 2.3.1.7)

Read: 2.3.1.1-2.3.1.6 (CPUID, GPIO, spinlocks, FIFOs, divider, interpolator including the lane, blend and clamp descriptions) and every register table. The interpolator's worked examples (pico-examples `hello_interp`: the times table, the moving mask with and without sign extension, the lane cross-over, blend 500..998, clamp) are now tests with the output the datasheet prints, and **the reference already produces all of it** - the lane arithmetic is right.

- **`GPIO_HI_OUT` / `GPIO_HI_OE` were 30 bits;** the QSPI bank has six pins (5:0). Now 0x3F.
- **The divider's reset state:** `DIV_xDIVISOR` reset to 1 and `DIV_CSR` to 0 (READY low, so a driver polling READY before its first division would never see it); the datasheet has the divisor at 0 and READY at 1.
- **`INTERPx_ACCUMy_ADD` took 32 bits;** the register is 23:0 (datasheet; `SIO_INTERP0_ACCUM0_ADD_BITS 0x00ffffff` in pico-sdk's `sio.h`, `interp_add_accumulator` just stores a `uint32_t`).
- **`FORCE_MSB` was taken from lane 0's CTRL for both lanes,** and the forced bits were written back into the accumulators by a POP. Each lane has its own field ("ORed into bits 29:28 of the lane result presented to the processor on the bus. No effect on the internal 32-bit datapath"), so lane 1 now uses CTRL_LANE1 and the accumulators take the datapath value.
- **Checked and consistent:** spinlocks (read claims, any write releases, SPINLOCK_ST), the GPIO SET/CLR/XOR aliases, the divider's signed/unsigned truncation and the quotient/remainder signs, the 8-cycle charge, DIRTY/READY handling, the three lane results, blend/clamp availability per interpolator, the CTRL layout.
- **Not sourced, left as is:** the quotient and remainder of a **divide by zero** (the datasheet does not say; the reference tests the unmasked dividend with `> 0`, so a negative signed dividend and a zero dividend probably differ from silicon), what a read of a `SET`/`CLR`/`XOR` alias returns (0, "TODO verify with silicon").
- **Left, deliberately:** the inter-core FIFO and core 1 (record 0053), CPUID is always 0, the two cores' simultaneous writes.

### DMA (datasheet 2.5)

Read: 2.5.2 (aliases, triggers, chaining, null triggers), 2.5.3.1 (the DREQ table - matches the enum), 2.5.4 and the controller-level register tables (INTR .. N_CHANNELS); **not** read: the per-channel CTRL table text beyond the field names, 2.5.5 (the DMA cycle model: FIFOs, bus arbitration, priority), 2.5.6 (the examples), the sniffer's description.

- **The pacing timers' period was inverted:** the datasheet has "TREQ assertions at a rate set by ((X/Y) * sys_clk) ... can only generate TREQs at a rate of 1 per sys_clk (i.e. permanent TREQ) or less", i.e. a period of Y/X cycles, at least one; the reference used X/Y cycles, so `dma_timer_set_fraction(t, 1, 32768)` ran 32768 times too fast. A timer with X or Y at 0 asserts nothing.
- **TIMER3's X was read from bit 4** (a JavaScript shift-by-36 inherited from rp2040js, flagged in the code as "almost certainly an upstream bug"); X is bits 31:16 of all four timers.
- **`CHAN_ABORT`, `MULTI_CHAN_TRIGGER` (self-clearing) and `FIFO_LEVELS` (debug) read as unimplemented** - all ones and a warning. The datasheet says of CHAN_ABORT "After writing, this register must be polled until it returns all-zero. Until this point, it is unsafe to restart the channel", so a firmware that follows it would spin on 0xFFFFFFFF. (pico-sdk's own `dma_channel_abort` does not do that: it polls `CTRL_TRIG.BUSY` of the channel, which the model clears at once - checked in `hardware_dma/dma.h`; an earlier version of this note said the SDK polled CHAN_ABORT, from memory, and was wrong.) They read 0 now (an abort is flushed at once here, and there are no address/data FIFOs to fill).
- **Checked and consistent:** the trigger rules (no start when disabled, already running or a zero write), the chain rules (no self-chain, immediate re-trigger with TRANS_COUNT reload), IRQ_QUIET and the null trigger, INTR/INTS write-1-to-clear, INTE/INTF 16 bits and `INTS = (INTR & INTE) | INTF`, the DREQ numbering, EN cleared while BUSY pausing the channel, N_CHANNELS = 12.
- **Not implemented, in the backlog:** the sniffer (SNIFF_CTRL, SNIFF_DATA, CTRL.SNIFF_EN), HIGH_PRIORITY and the arbitration, the read/write/AHB error flags, the DBG_CTDREQ counter (never counts), a transfer taking bus cycles (one transfer per alarm).
- Tests: `tests/test_sio_datasheet.py` (the interpolator examples and the new widths), `tests/cpp/test_sio.cpp`, `tests/cpp/test_dma.cpp` (the timers now follow Y/X), the DMA oracle (`tests/utils/dma_diff.py`: `timer3_shifts_16` became three mutants, `timer3_shifts_4`, `timer_period_inverted` and `timer_rate_uncapped`, plus `abort_read_warns`).

### SSI (datasheet 4.10, the XIP SSI)

Read: every register table of the SSI (CTRLR0 .. TXD_DRIVE_EDGE) and the field lists; **not** read: the operation chapters (the FIFO and interrupt behaviour of the DW_apb_ssi, the XIP streaming and its DREQs, the dual/quad formats). The model is a **flash-command emulation** driven by the bootrom through DR0 (framed by QSPI_SS, not by SER/SSIENR, as the code explains); its register file was never the point, which is why most of the findings are widths.

- **Reserved bits stuck:** CTRLR0 (23 and 31:25), CTRLR1 (31:16), SSIENR (31:1), BAUDR (31:16) and SPI_CTRLR0 (31:24 is XIP_CMD, but 23:19, 10 and 7:6 are reserved) kept whatever was written. Now 0x017FFFFF, 0xFFFF, 0x1, 0xFFFF and 0xFF07FB3F.
- **`SPI_CTRLR0` reset value** is 0x03000000 (XIP_CMD = 0x03, the READ command); it was 0.
- **`TXFLR` was writable** (it is read only); a write is ignored now (it always reads 0: there is no TX FIFO to fill).
- **Unimplemented, now stored and not acted on:** MWCR, SER, TXFTLR, RXFTLR, IMR, DMACR, DMATDLR, DMARDLR (their widths, reset 0). ISR, RISR and the clear-on-read registers (TXOICR, RXOICR, RXUICR, MSTICR, ICR) read 0: nothing is raised.
- **Checked and consistent:** IDR 0x51535049 and SSI_VERSION_ID 0x3430312A, SR's TFE/TFNF/RFNE composition (the reset column of a status register is a placeholder), RXFLR as the length of the model's RX queue.
- **Left, deliberately:** the SSI's FIFOs (the RX queue is unbounded, TX is instant, so TFE/TFNF are constant and RFF/BUSY/TXE/DCOL never set), the interrupts, DMA and XIP streaming, the dual/quad formats and the DDR bits (SPI_CTRLR0 is stored only), CTRLR0.DFS/TMOD and the rest of the frame setup (the model shifts 8 bits whatever they say).
- Tests: `tests/test_ssi.py` (one datasheet case), `tests/cpp/test_ssi.cpp`, the oracle (`tests/utils/ssi_diff.py`: the new registers in the write stream, 10 new mutants for the masks, the reset values, TXFLR and the stored registers).

### CLOCKS and PLL (datasheet 2.15 and 2.18)

Read: the register tables of CLOCKS (CLK_GPOUT0_CTRL .. INTS) and PLL, the AUXSRC/SRC encodings of REF, SYS and PERI against the code (they match); **not** read: the clock-generator chapters (glitchless mux behaviour, the frequency counter's operation, the resus), and the PLL's VCO limits and start-up. Both are plain Python blocks (no C++ twin, no oracle) - the tests are `tests/test_clocks_datasheet.py`.

- **`CLK_REF_DIV`, `CLK_ADC_DIV` read through a mask of bits 5:4** (`& 0x30`) where the INT field is bits 9:8, so they read 0 after reset (the datasheet: 0x100) and lost the written value; `CLK_USB_DIV` was not masked at all and `CLK_RTC_DIV` (a full 24.8 divider) read through the same wrong mask. Now REF/USB/ADC keep bits 9:8 (written and read) and RTC reads whole.
- **`CLK_SYS_RESUS_CTRL` read a constant 0xFF and ignored writes;** it is CLEAR 16, FRCE 12, ENABLE 8, TIMEOUT 7:0 (reset 0xFF) and is stored now (the resus itself is not modelled).
- **Unimplemented, now stored and not acted on:** WAKE_EN0/1 and SLEEP_EN0/1 (reset all ones, widths 32 and 15 bits), INTE/INTF (the resus interrupt's one bit); INTR reads 0 and INTS is INTF (nothing raises INTR; the CLOCKS IRQ line is not driven).
- **PLL registers stored every bit written:** CS keeps BYPASS and REFDIV (LOCK is read only and always set), PWR 0x2D, FBDIV_INT 11:0, PRIM POSTDIV1/POSTDIV2 (0x77000). (The tool's PRIM "reset" finding is its misparse of a later table.)
- **Checked and consistent:** the SRC/AUXSRC encodings of CLK_REF, CLK_SYS and CLK_PERI, the divider decoding (INT 0 = 2^16), the *_SELECTED one-hot values, the PLL output formula `f_ref / REFDIV x FBDIV / (POSTDIV1 x POSTDIV2)` and the bypass case.
- **Not sourced / left, deliberately:** `CLK_PERI_DIV` (0x4C) exists in the model and not in the datasheet (clk_peri has no divider; the register is stored and ignored); the PLL is always locked, even with PD or VCOPD set (no start-up time; pico-sdk only waits for LOCK after powering the PLL up); the frequency counter FC0_* (a firmware that measures a clock would wait forever: it is a feature, in the backlog) and ENABLED0/1 stay unimplemented; the clock gating of WAKE_EN/SLEEP_EN is not modelled.

### PIO (datasheet 3.4 and 3.5, and the register tables of 3.7)

Read: the instruction set (3.4.2-3.4.10: every encoding and operation), 3.5.1 (side-set, skimmed), 3.5.3 (FIFO joining), 3.5.4 (autopush and autopull with their pseudocode), 3.5.5 (clock dividers, skimmed), 3.5.7 (forced and EXEC'd instructions) and the SMx_CLKDIV, SMx_EXECCTRL, SMx_SHIFTCTRL, SMx_PINCTRL tables; **not** read: the rest of 3.5.6 (GPIO mapping and output priority), the programming examples, the system-level registers other than the tool's pass (CTRL, FSTAT, FDEBUG, FLEVEL, IRQ*, DBG_*, INTR/INTE/INTF/INTS: the tool found nothing but its own artefacts - `DBG_CFGINFO` 0x00200404 is FIFO_DEPTH 4, SM_COUNT 4, IMEM_SIZE 32, right). The instruction decoding, the JMP conditions, WAIT sources and polarity, the IRQ index decoding, MOV operations, the side-set field split and the wrap rules all match.

- **`PUSH IFFULL` and `PULL IFEMPTY` only tested the threshold with autopush / autopull on.** The datasheet: "IfFull: do nothing unless the total input shift count has reached its threshold ... (the same as for autopush)" and "PUSH IFFULL helps to make programs more compact, like autopush. It is useful in cases where the IN would stall at an inappropriate time **if autopush were enabled**"; likewise IfEmpty. The shift counts are kept whether or not auto-transfer is on, so a program using `pull ifempty` without autopull pulled every time. Now the threshold is always tested.
- **With autopull on, a `PULL` is a no-op while the OSR is full** ("it becomes a no-op if the OSR is full ... a fence"; the OSR is full when its shift count is 0). It pulled a second word - one that is then lost - right behind an autopull.
- **`OUT x, 32` left the OSR as it was;** all 32 bits are shifted out and the OSR shifts in zeroes, so it is empty. A `MOV x, OSR` afterwards read the stale word.
- **A reset state machine had an OSR shift count of 0 (full);** "At reset, or upon CTRL_SM_RESTART assertion, ISR shift counter is set to 0 ..., and OSR to 32 (nothing left to be shifted out)". `restart()` already did; construction and `reset()` did not (pico-sdk's `pio_sm_init` restarts every machine, which hid it).
- **`CTRL.SM_RESTART` left EXEC_STALLED set** ("any stalled instruction written to SMx_INSTR or run by OUT/MOV EXEC" is cleared).
- **Reserved bits stuck:** SMx_EXECCTRL 6:5 and SMx_SHIFTCTRL 15:0 were stored (now 0x7FFFFF9F and 0xFFFF0000; EXEC_STALLED stays read only).
- **Not implemented, in the backlog (features, not corrections):** **`SHIFTCTRL_FJOIN_TX` / `FJOIN_RX`** (the joined 8-deep FIFO; MicroPython's `PIO.JOIN_TX` / `JOIN_RX` need it - the bits are stored and ignored today), **`EXECCTRL_OUT_STICKY`, `INLINE_OUT_EN`, `OUT_EN_SEL`** (the existing TODO), the input synchroniser bypass effect.
- **Left, deliberately:** autopull is done when the next OUT starts, not right after the OUT that reaches the threshold or "at any point between two OUTs when data arrives" (3.5.4.2), so the TX FIFO drains later than on silicon by up to one OUT; an OUT that finds the OSR exhausted pulls and shifts in the same cycle, where the datasheet stalls that cycle ("it cannot fill an empty OSR and OUT it on the same cycle"). The cycle counts of streaming programs are the same, FIFO levels read slightly high, and a change needs a design of its own (the pacing of `due` times, 0063).
- Tests: `tests/test_pio_datasheet.py` (five cases on both implementations), `tests/cpp/test_pio.cpp` (one directed test, the register-mask checks updated). The PIO oracle has no logic mutants (it compares the two implementations only), so there are none to add.

### PPB (SysTick, NVIC, SCB)

Audited before this record was opened, as part of the C++ port, and written up in the [0096](0096-cpp-mcu-core.md) progress log (the entry "The PPB is C++"): four reference quirks fixed against the datasheet and the M0PLUS tables (`SYST_RVR` is 24 bits, the SysTick period is RELOAD + 1 with the first period after a CVR write, `CLKSOURCE` = 0 runs from the 1 MHz reference clock, and the fourth listed there), with pico-sdk's `m0plus.h`. The ARM Architecture Reference Manual page was not readable (JavaScript-rendered), so the parts of SysTick that only the ARM manual defines stay marked as the model's own: the step "the next tick after a CVR write loads RELOAD" and the reset values (the datasheet says UNKNOWN; the model resets RVR and the counter to 0xFFFFFF).

## Second pass: the operation chapters of the closed blocks

Read after the register tables, 2026-10-06 (SPI 4.4.3.7-4.4.3.16, I2C 4.3.5-4.3.10 and 4.3.16, DMA 2.5.1-2.5.5, CLOCKS 2.15.3, PIO 3.5.6). Corrections made, in the reference and the C++ together, each with a test:

- **I2C: a write to `IC_DATA_CMD` while the block is disabled is lost** ("If the IC_DATA_CMD register is written before the DW_apb_i2c is enabled, the data and commands are lost as the buffers are kept cleared", 4.3.10.2.1). The reference queued it. pico-sdk enables before it writes commands.
- **DMA: `CHAN_ABORT` clears the transfer counter** ("This clears the transfer counter and forces the channel into an inactive state", 2.5.5.3); the reference cleared BUSY and kept TRANS_COUNT. (RP2040-E13 - an abort of a running channel may raise a completion IRQ - is an erratum of the silicon and is not modelled.)
- **PIO: OUT / SET / side-set pin mappings wrap after GPIO31** ("this mapping continues for PINCTRL_OUT_COUNT bits, wrapping after GPIO31", 3.5.6); the reference shifted the data and dropped what left the word (a "TODO: wrapping after pin 31" in both implementations). They rotate now.

Read and found consistent, or left as what the model does not do:

- **SPI** (frame formats): the wire-level behaviour - clock and frame-select timing, the Texas Instruments and Microwire formats (Microwire is half-duplex with an 8-bit control word), slave select and the pad-enable lines - is below the model's word-level `on_transmit`; nothing in it contradicts the block, and the formats are in the "left" list. Words go out most-significant bit first and the receive side right-justifies, as modelled.
- **I2C**: the master's TX FIFO management (no STOP unless IC_DATA_CMD.STOP is set, SCL held low on an empty FIFO, RESTART by bit) matches; **not modelled**: with `IC_RESTART_EN` = 0 a RESTART must become a STOP followed by a START, and with it set a change of direction between two queued commands issues a RESTART by itself ("If the direction of this transfer differs from the previous transfer, the combined format is used", 4.3.5.2) - the block restarts only on the RESTART bit, which is what pico-sdk sets; disabling is instantaneous (the datasheet lets a master disable only after a command with STOP, and has IC_ENABLE_STATUS report it); table 451 confirms TX_EMPTY and RX_FULL are levels "set and cleared by hardware", the other interrupts are cleared by software (TX_EMPTY is still raised by a command being taken, not as a pure level).
- **DMA**: the credit-based DREQ scheme (a counter per channel, one request in flight per credit) is modelled as a level (transfer while the DREQ is up), the transfer FIFOs and bus arbitration are not; TRANS_COUNT reload and the "trigger by TRANS_COUNT uses the value just written" rules, the alignment caution and the chain and null-trigger rules match.
- **CLOCKS**: glitchless mux and SELECTED, the aux mux, KILL and ENABLE (only clk_peri's is modelled), the divider range (1 or 2.0 to 2^24-0.01, jittery) and the duty-cycle correction are hardware detail the model collapses to "the clock has this frequency now"; the frequency counter (2.15.4) and resus (2.15.5) remain in the backlog.
- **PIO** (3.5.6): the output priority between state machines (the highest-numbered wins per GPIO, separately for levels and directions) comes out right because the machines are stepped in order within a cycle and a later write wins; a side-set overlapping an OUT/SET of the same cycle takes precedence (it is applied after); the 2-cycle register stage and the 2-flip-flop input synchroniser (4 cycles with `INPUT_SYNC_BYPASS` clear) are **not modelled** - a state machine sees its own outputs and the inputs at once.
- **SSI** (4.10.3, 4.10.5): **disabling the SSI (SSIENR = 0) clears the FIFOs** ("The transmit and receive FIFO buffers are cleared when the DW_apb_ssi is disabled", 4.10.5) - the model's RX queue survived; now cleared (a correction, with a test). Read and left: the three IP modifications of 4.10.3 (XIP accesses byte-swapped, the XIP instruction appended after the address when `SPI_CTRLR0.INST_L` is 0, DMARDLR); the FIFOs are 16 entries of 32 bits and the thresholds work as in the tables (TX: at or below, RX: at or above threshold + 1) - none of it is modelled, the SSI being the bootrom's flash-command path. **A conflict inside the datasheet:** 4.10.3 says "The reset value of DMARDLR is increased from 0 to 4", while the register table (and pico-sdk's `SSI_DMARDLR_RESET`) say 0; the model follows the table and the SDK.

## Not audited yet

Everything ported on the 0096 branch has a section above. What has **not** been read against the datasheet:

- **Blocks that are not ported yet**, audited as they are ported, in parallel with the implementation (the maintainer's decision, 2026-10-06): watchdog, RTC, PSM, RESETS, VREG_AND_CHIP_RESET, SYSCFG, SYSINFO, TBMAN, BUSCTRL, XOSC, USB; and the pure-Python blocks that no C++ port is planned for yet: ROSC (a frequency attribute only), IO_BANK0 and PADS_BANK0 (`peripherals/io.py`, `pads.py`), XIP_CTRL. The tool's register-level pass can be pointed at any of them (`BASES` in the script lists their addresses) but it has not been run on them for this record.
- **Parts of the audited blocks still skimmed or skipped**: the SSI operation chapter (XIP streaming, the DW_apb_ssi FIFO and interrupt behaviour), the I2C slave-mode and bus-clear chapters (4.3.10.1, 4.3.12-4.3.15), the DMA example chapters and the sniffer, the CLOCKS frequency counter and resus chapters, the PIO system-level registers beyond the tool's pass, the UART and ADC operation chapters. The operation chapters of the rest are in the second pass above.
- **What the datasheet does not say**, so no source exists to check against: the quotient and remainder of a signed divide by zero, what a read of a SIO `SET`/`CLR`/`XOR` alias returns, the PPB's reset values.

## Open features found by the audit (backlog)

None of these is a correction of the reference; each is missing behaviour, left for its own decision (CLAUDE.md: documenting is not implementing).

| Block | Missing | Why it matters |
|---|---|---|
| PIO | `SHIFTCTRL_FJOIN_TX` / `FJOIN_RX` (the bits are stored and ignored) | MicroPython's `PIO.JOIN_TX` / `JOIN_RX`; FSTAT/FLEVEL and the DREQs of the joined FIFO |
| PIO | `EXECCTRL_OUT_STICKY`, `INLINE_OUT_EN`, `OUT_EN_SEL` (the existing TODO) | programs that rely on a sticky OUT or an inline output enable |
| PIO | autopull at the OUT that reaches the threshold and between OUTs (3.5.4.2); today it happens when the next OUT starts | TX FIFO levels and DREQ timing read slightly high |
| DMA | the sniffer (`SNIFF_CTRL`, `SNIFF_DATA`, `CTRL.SNIFF_EN`), `HIGH_PRIORITY` and arbitration, the error flags, the `DBG_CTDREQ` counter | CRC over a transfer; contention timing |
| DMA | I2C (and SSI) DREQs: the I2C block has no DREQ output | DMA-driven I2C |
| CLOCKS | the frequency counter `FC0_*`, `ENABLED0/1`, the resus, the clock gating of WAKE_EN/SLEEP_EN | firmware that measures a clock waits forever |
| SIO | the inter-core FIFO and core 1 (record 0053) | `_thread`, multicore |
| I2C | slave mode, the general call and START byte, `TX_EMPTY_CTRL`, `RX_FIFO_FULL_HLD_CTRL`, the "FIFOs stay flushed until IC_CLR_TX_ABRT" rule | slave devices |
| SSI / SPI | the SSI's own FIFOs, interrupts and DMA; SPI slave mode, the receive timeout, TI and Microwire frames | XIP streaming, SPI slaves |

## What was checked against pico-sdk rather than the datasheet

Each of these is a statement about what real firmware does, read from the SDK source (`raspberrypi/pico-sdk`, master) and not from memory: `spi_init` sets both SSPDMACR enables then SSE (`hardware_spi/spi.c`); `uart_init` writes DMACR with both enables unless `PICO_UART_NO_DMACR_ENABLE` (`hardware_uart/uart.c`); `i2c_init`, `i2c_set_baudrate` and `i2c_write_blocking` clear `enable` before writing `con`, `tar` and the counts (`hardware_i2c/i2c.c`); `SIO_INTERP0_ACCUM0_ADD_BITS` is 0x00ffffff and `interp_add_accumulator` stores a `uint32_t` (`hardware_regs/sio.h`, `hardware_interp/interp.h`); `dma_channel_abort` writes `abort` and polls the channel's `CTRL_TRIG.BUSY`, **not** `CHAN_ABORT` (`hardware_dma/dma.h`) - the datasheet's instruction to poll CHAN_ABORT is what justifies its read value, not the SDK. Anything in the sections above that cites the SDK without being in this list was cited from the datasheet's embedded SDK excerpts.

## Progress log

- 2026-10-06: opened. Tool and the first-pass register-level findings above; nothing fixed yet.
- 2026-10-06: **TIMER read against the datasheet and fixed** (PAUSE, TIMEHW/TIMELW, DBGPAUSE, the 64-bit wrap and the C++ int64 undefined behaviour it exposed). Next: ADC.
- 2026-10-06: **ADC read against the datasheet and fixed** (8-entry FIFO, READY needs EN, DIV 24 bits). Next: PWM.
- 2026-10-06: **PWM read against the datasheet and fixed** - the phase-correct output and period (the 0096 "bug 4"), CSR/DIV reserved bits, PH_ADV at full speed. Next: UART.
- 2026-10-06: **UART read against the datasheet and fixed (reserved bits, IFLS/ILPR/DMACR/RSR/ECR, DREQ rule, TXE/RXE/UARTEN gating, overrun)**; reference, C++, oracle and tests together.
- 2026-10-06: **SPI read against the datasheet and fixed** (reserved bits, nothing sent while SSE = 0, the DMA requests gated by SSE and SSPDMACR, loop back, SSPRIS reset). Next: I2C.
- 2026-10-06: **I2C read against the datasheet's register tables and fixed** (FIRST_DATA_BYTE bit, configuration writable only while disabled, widths and minima, stored registers, RX_FULL as a level, abort flushes RX, ACTIVITY). Next: SIO, DMA, SSI, CLOCKS/PLL, PIO - then the small blocks as they are ported.
- 2026-10-06: **SIO and DMA read against the datasheet and fixed** (SIO: QSPI GPIO width, divider reset, ACCUM_ADD width, FORCE_MSB per lane; DMA: pacing timer period, TIMER3, reads of CHAN_ABORT/MULTI_CHAN_TRIGGER/FIFO_LEVELS). Next: SSI, CLOCKS/PLL, PIO.
- 2026-10-06: **SSI read against the datasheet's register tables and fixed** (reserved bits, SPI_CTRLR0 reset, TXFLR read only, the unimplemented registers stored). Next: CLOCKS/PLL, PIO.
- 2026-10-06: **CLOCKS and PLL read against the datasheet's register tables and fixed** (DIV masks, RESUS_CTRL, WAKE/SLEEP/INT registers stored, PLL widths). Next: PIO.
- 2026-10-06: **PIO read against the datasheet (instruction set, shift counters, the SMx register tables) and fixed** (IFFULL/IFEMPTY without auto-transfer, PULL behind an autopull, OUT 32, the reset OSR count, SM_RESTART and EXEC_STALLED, reserved bits). Open: FJOIN, OUT_STICKY. **Every block ported on this branch has now been read.**
