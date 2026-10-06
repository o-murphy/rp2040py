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
- **The DMA requests were wrong.** The reference raised the TX DREQ when UARTEN was set (whatever DMACR said) and never the RX one, so a DMA channel paced by `DREQ_UARTn_RX` could not run. Now TX asks while UARTEN, TXE and `DMACR.TXDMAE` are set (the FIFO never fills), and RX while UARTEN, RXE and `RXDMAE` are set and the FIFO holds a byte, unless `DMAONERR` is set and an error interrupt is up ("the DMA receive request outputs ... are disabled when the UART error interrupt is asserted"). Both are re-announced (TX then RX) on every change of CR, DMACR, the FIFO's emptiness, the error interrupts and at reset (a reset used to leave a stale request up). pico-sdk's `uart_init` writes `DMACR` with both enables, so real firmware is unaffected.
- **A disabled UART still sent and received.** The datasheet: "TXE: ... If this bit is set to 1, the transmit section of the UART is enabled", RXE likewise, UARTEN gates both. A `UARTDR` write now sends only with UARTEN and TXE, and a byte fed from the wire is taken only with UARTEN and RXE (UARTEN is 0 after a reset until the firmware sets it; the tests that feed or send now enable the UART as a firmware does).
- **Overrun:** a byte that arrives with the 32-entry FIFO full is dropped as before, and now also sets `UARTRSR.OE` and the raw OE interrupt (bit 10 of `UARTRIS`), cleared by `UARTICR` / `UARTECR` ("no more data is written when the FIFO is full").
- **Checked and consistent:** the baud divisors (`IBRD` 16 bits, `FBRD` 6 bits, `baud = clk_peri / (16 x (IBRD + FBRD / 64))`), the interrupt mask/status/clear layout, `UARTFR` (TXFE and RXFE at reset; TXFF and BUSY never set because the transmitter is instant), the ID registers.
- **Left, deliberately:** the RX FIFO is 32 deep whatever `FEN` says (the datasheet: with FIFOs disabled it is a 1-byte holding register; but bytes arrive here in instant bursts that a real line would space at the baud rate, so a 1-deep register would drop what hardware would not); no break/parity/framing errors, no modem lines, no loopback, no timing of transmission.
- Tests: `tests/test_uart.py` (seven datasheet cases), `tests/cpp/test_uart.cpp` rewritten around the new DREQ rule and the gating, the oracle (`tests/utils/uart_diff.py`: the new registers in the stream, an enabling scenario so that traffic passes, 47 mutants - the three obsolete CR-DREQ ones replaced by 14 DREQ/gating/overrun/register mutants).

### SPI (datasheet 4.4, the PL022)

Read: 4.4.2 (the block), 4.4.3.2-4.4.3.6 (configuring, enabling, clock ratios, SSPCR0/SSPCR1, bit rate), the DMA interface text (4.4.3.16) and every register table (SSPCR0 .. SSPDMACR, the ID registers; the values the reference returns for those, `0x22 0x10 0x34 0x00` and `0x0D 0xF0 0x05 0xB1`, are right - the tool's PCELLID3 "reset" finding is its own misparse).

- **Reserved bits stuck:** SSPCR0 kept bits 31:16, SSPCR1 and SSPIMSC bits 31:4, SSPDMACR bits 31:2. Now 0xFFFF, 0xF, 0xF, 0x3. (SSPCPSR's bit 0 "not sticking" is right: "the least significant bit always returns zero on reads"; the reference already masked 0xFE.)
- **A disabled SSP sent.** "You can prime the transmit FIFO ... when the PrimeCell SSP is disabled ... Once enabled, transmission or reception of data begins" (4.4.3.3): the reference pushed a word to the device as soon as it was written, whatever SSE said. Now nothing leaves while SSE is 0 and a primed FIFO starts when SSPCR1 enables it.
- **The DMA requests ignored SSE and SSPDMACR.** "All request signals are deasserted if the PrimeCell SSP is disabled, or the DMA enable signal is cleared" (4.4.3.16): the reference kept the TX request up whenever the FIFO had room and the RX one whenever it had a word. Now TX needs SSE and TXDMAE, RX needs SSE and RXDMAE, and both are re-published on every write of SSPCR1 and SSPDMACR as well as on every FIFO change. pico-sdk's `spi_init` writes SSPDMACR with both enables and enables the SSP, so real firmware is unaffected.
- **Loop back (SSPCR1.LBM) was not implemented.** "Output of transmit serial shifter is connected to input of receive serial shifter internally": the word written to SSPDR now comes straight back into the RX FIFO and does not reach the device.
- **`SSPRIS` reset:** the datasheet has TXRIS = 1 at reset (the TX FIFO is empty, "half empty or less"); the reference started at 0 and only set it after the first FIFO change. Fixed at power-on and at `reset()`.
- **Checked and consistent:** the FIFOs (16 bits wide, 8 deep), the interrupt thresholds (TX at 4 or fewer, RX at 4 or more), the overrun interrupt, SSPICR clearing only RT and ROR, `SSPSR` (BSY while a frame is in flight or the TX FIFO is not empty), the bit-rate formula `clk_peri / (CPSDVSR x (1 + SCR))`, data size = DSS + 1.
- **Left, deliberately:** the frame format (FRF, TI/Microwire), the receive-timeout interrupt (RT never rises), slave mode (MS and SOD are stored; the block still masters), the burst DMA requests (the chip has one request per direction), the rule that MS can be changed only with SSE = 0, and the DSS values 0-2 (reserved, "undefined operation": treated as 1-3 bits).
- Tests: `tests/test_spi.py` (four datasheet cases), `tests/cpp/test_spi.cpp` (the three new behaviours, the old checks now run with the SSP enabled as a firmware has it), the oracle (`tests/utils/spi_diff.py`: SSPDMACR values in the stream, 13 new mutants for the masks, the gating, the loop back, the start on enable and the reset value; `imsc_masked`, which the fix made equal to the reference, became `imsc_unmasked`).

## Progress log

- 2026-10-06: opened. Tool and the first-pass register-level findings above; nothing fixed yet.
- 2026-10-06: **TIMER read against the datasheet and fixed** (PAUSE, TIMEHW/TIMELW, DBGPAUSE, the 64-bit wrap and the C++ int64 undefined behaviour it exposed). Next: ADC.
- 2026-10-06: **ADC read against the datasheet and fixed** (8-entry FIFO, READY needs EN, DIV 24 bits). Next: PWM.
- 2026-10-06: **PWM read against the datasheet and fixed** - the phase-correct output and period (the 0096 "bug 4"), CSR/DIV reserved bits, PH_ADV at full speed. Next: UART.
- 2026-10-06: **UART read against the datasheet and fixed (reserved bits, IFLS/ILPR/DMACR/RSR/ECR, DREQ rule, TXE/RXE/UARTEN gating, overrun)**; reference, C++, oracle and tests together.
- 2026-10-06: **SPI read against the datasheet and fixed** (reserved bits, nothing sent while SSE = 0, the DMA requests gated by SSE and SSPDMACR, loop back, SSPRIS reset). Next: I2C.
