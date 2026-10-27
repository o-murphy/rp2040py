<!-- Reference (living table). Unlike docs/records/, which are append-only, THIS FILE IS UPDATED IN PLACE: whoever changes what the chip implements updates the row in the same commit. -->

# What the emulated RP2040 implements, and what it does not

One place to see, block by block and datasheet section by section, what is modelled, what is only a stub, what is missing, and **when** each missing thing is to be done. The *why* and the evidence live in the records (every row names one); this file is the table that tells you where to work next. Every register of the datasheet, with the same kind of status, is in [register-coverage.md](register-coverage.md) (generated).

**Rules of upkeep.** (1) A change that adds, fixes or removes behaviour of a block updates its rows here in the same commit. (2) A missing feature is listed here **and** in 0098's backlog table; it is implemented only on an explicit go-ahead (CLAUDE.md, "Document vs. implement"). (3) A row says *what* is missing in the datasheet's words, never "TODO". (4) A status is never rounded up: if only part of a section works, it is Partial, and the note says which part.

**Status words.** *Done*: modelled and tested (for the audited blocks, against the datasheet). *Partial*: some of it works; the note says what does not. *Stub*: the registers exist and read back, nothing acts on them. *Missing*: not modelled. *Unchecked*: not checked when this was written - check it and fix the row. *n/a*: nothing to emulate (electrical, mechanical, an off-chip tool). **When**: *Next* = the agreed order (records 0096 / 0098); *With X* = done together with another item; *Go-ahead* = a backlog feature that waits for the maintainer's explicit word; *Needs X* = blocked by a prerequisite.

## 1. The blocks at a glance

| Block | Model | Read against the datasheet | C++ core | Main gaps |
|---|---|---|---|---|
| Cortex-M0+ CPU, NVIC, SysTick, PPB | Partial | n/a (ARMv6-M tests; MicroPython interpreter tests) | Ported | System control and MPU registers present (0098); `SCR` and the MPU are stored, not acted on; core 1 has its own NVIC, SysTick, VTOR and NMI mask in both builds (the Python view of core 1's PPB on the native chip and the GDB stub's core selection are not done: record 0104) |
| SIO | Partial | Yes | Ported (the inter-core FIFOs, core 1's own divider and interpolators, `CPUID` of the initiating core and the boot ROM's launch handshake, in the reference and the C++ core alike - record 0104) | the real boot ROM on core 1 as an option |
| TIMER | Partial | Yes | Ported | `DBGPAUSE` is stored (no debug halt) |
| WATCHDOG | Partial | Yes | Ported | `PAUSE_DBG*`/`PAUSE_JTAG` are stored (no debug halt) |
| RTC | Partial | Yes | Ported | clk_rtc synchroniser latency, dormant wake, GPIN reference |
| UART0/1 | Partial | Yes + second pass | Ported | receive timing, `UARTRTINTR`, per-entry error flags, flow control, modem lines |
| SPI0/1 | Partial | Yes + second pass | Ported | slave mode, TI/Microwire, receive timeout; the inter-frame gap of SPH = 0 |
| I2C0/1 | Partial | Yes + second pass | Ported | slave mode, DREQs, `IC_SAR` and friends are stubs |
| ADC | Partial | Yes + second pass | Ported | `TS_EN` gating, SAR error flag |
| PWM | Done | Yes | Ported | - |
| DMA | Partial | Yes + second pass + the datasheet examples | Ported | sniffer, priority/arbitration, error flags, I2C DREQs; bus contention |
| PIO0/1 | Partial | Yes + second pass | Ported | `OUT_STICKY`/`INLINE_OUT_EN`/`OUT_EN_SEL`, autopull timing |
| SSI (XIP flash controller) | Partial | Yes + XIP_CTRL pass | Ported | a flash-command model with a 16-entry RX FIFO, the six interrupts (`XIP_IRQ`), DMA requests 38/39 and `TMOD` = transmit only; the TX FIFO is always empty (a frame shifts at once); 7 of 28 registers stored only |
| CLOCKS | Partial | Yes + second pass | C++ (`core/clocks.hpp`, `clock_sinks.hpp`) | resus, clock enables / sleep, GPIN, the GPOUT clock outputs; 18 of 48 registers are stubs |
| PLL | Partial | Yes | C++ (`core/pll.hpp`) | power bits and LOCK follow the datasheet; no lock delay |
| XOSC | Partial | Yes | Ported | DORMANT, the start-up delay and `COUNT` are stored; `STABLE` is set at once |
| ROSC | Partial | Yes (read with the port) | Ported | the oscillator never stops (DORMANT and DISABLE are stored), STABLE is immediate, the drive strengths and the phase output do nothing |
| PSM, RESETS, SYSINFO, TBMAN | Done | Yes | Ported | - |
| SYSCFG | Stub | Yes | Ported | only `PROC0_NMI_MASK` and `PROC1_NMI_MASK` act (each core's own NMI mask) |
| VREG_AND_CHIP_RESET | Partial | Yes | Ported | `VREG`, `BOD` are stored; `CHIP_RESET` acts |
| BUSCTRL | Stub | Yes | Ported | no arbitration, no performance counters |
| IO_BANK0 | Partial | Yes | C++ (`core/io.hpp`) over the shared pin bank; also IO_QSPI, PADS_BANK0, PADS_QSPI (`core/pads.hpp`) | `PROC1_*` / `DORMANT_WAKE_*` are stored and raise nothing (the per-core GPIO lines are not split: core 1 sees the lines as core 0 does; no dormant); the pad electrical model (drive, slew, Schmitt, `VOLTAGE_SELECT`) is absent |
| PADS_BANK0 / PADS_QSPI / IO_QSPI | Partial | Yes | C++ (with IO_BANK0, above) | `VOLTAGE_SELECT` and `SWCLK` / `SWD` are stored; pad drive, slew and Schmitt have no electrical model |
| GPIO: input synchronisers | Missing | Yes | - | two clk_sys cycles of latency (2.21), bypassed by `SYSCFG.PROC_IN_SYNC_BYPASS(_HI)` and the PIO's `INPUT_SYNC_BYPASS`, both stored; needs a go-ahead |
| XIP_CTRL | Done | Yes | Ported | tag-only 2-way cache, aliases, counters, flush, cache-as-SRAM, streaming FIFO + `DREQ_XIP_STREAM`, `XIP_AUX`; the XIP window is read-only to the bus (0098); no bus fault is raised |
| USB controller + CDC host | Partial | Yes (reference fixed, oracle + C++ checks, 66 logic mutants) | C++ block + Cython shell (`core/usb.hpp`) | device mode (CDC shortcut) and host mode (Python `USBDevice`) work; no token-level wire: STALL/NAK handshakes, suspend/resume, device SOF absent (see the rows below) |

## 2. Every section of the datasheet

Follows the datasheet's own table of contents (chapter.section), so a row is something the datasheet describes. Evidence for a row is the record named in the note.

### Chapter 2 - System description

| Section | Feature | Status | Notes | When |
|---|---|---|---|---|
| 2.1.1 | AHB-Lite crossbar | Partial | no contention and no priority; `BUSCTRL.BUS_PRIORITY` is stored | Go-ahead |
| 2.1.2 | Atomic register access (XOR / SET / CLR aliases) | Done | 0098 | - |
| 2.1.3 | APB bridge | Done | | - |
| 2.1.4 | Narrow IO register writes | Unchecked | | - |
| 2.2 | Address map | Done | memory_map, the window table | - |
| 2.3.1 | SIO: divider, interpolators, spinlocks, GPIO ops | Done | 0098 | - |
| 2.3.1 | SIO: inter-core FIFO | Done | two 8-deep FIFOs, `FIFO_ST` VLD / RDY / ROE / WOF, the two IRQ lines (15, 16); the Python reference and the C++ core, held by `tests/test_core1_parity.py` (record 0104) | - |
| 2.3.2 | Interrupts: every IRQ line | Partial | both cores' NVICs get every line (each core enables its own); only the two GPIO lines are meant to be per core and are not split yet (`IO_BANK0` `PROC1_*`) | The per-core GPIO lines (record 0104, next steps) |
| 2.3.3 | Event signals (SEV / WFE) | Done | | - |
| 2.3.4 | Debug | Partial | a GDB stub, not an SWD/DAP model | - |
| 2.4.1-2.4.3 | Cortex-M0+ core and programmer's model | Done | MicroPython interpreter tests: 664 files, no failure | - |
| 2.4.4 | System control | Partial | `CPUID`, `ICSR`, `VTOR`, `AIRCR` (`SYSRESETREQ` resets the core only, through the `on_system_reset_request` hook), `CCR`, `SHCSR.SVCALLPENDED`, reserved bits of `VTOR`/`SHPR2`/`SHPR3`; **`SCR` is stored only** (`SLEEPONEXIT`, `SEVONPEND` do nothing) | Go-ahead |
| 2.4.5 | NVIC, SysTick | Done | | - |
| 2.4.6 | MPU | Stub | `MPU_TYPE` (8 regions), `MPU_CTRL`, `MPU_RNR`, `MPU_RBAR` (`VALID`/`REGION`), `MPU_RASR` registers store and read back; **no access is checked against a region** (no MemManage on ARMv6-M: a violation is a HardFault, which the core does not raise) | Go-ahead |
| 2.5.1 | DMA: configuring channels | Done | 0098 | - |
| 2.5.2 | DMA: starting (aliases, triggers, chaining, null triggers) | Done | the datasheet's two example use cases run as tests (0098) | - |
| 2.5.3 | DMA: DREQ, system table | Partial | I2C has no DREQ output | Go-ahead |
| 2.5.3.2 | DMA: credit-based DREQ scheme | Unchecked | | - |
| 2.5.4 | DMA: interrupts | Done | | - |
| 2.5.5.1 | DMA: pacing timers | Done | | - |
| 2.5.5.2 | DMA: CRC sniffer | Missing | `SNIFF_CTRL`, `SNIFF_DATA`; the algorithm's bit order needs a hardware-sourced vector | Go-ahead |
| 2.5.5.3 | DMA: channel abort | Done | | - |
| 2.5.5.4 | DMA: debug (`DBG_CTDREQ`) | Missing | | Go-ahead |
| 2.15 | CPU time follows `clk_sys` | Done | an instruction costs `1 / clk_sys` of simulated time (`machine.freq(250_000_000)` halves a loop's simulated time; live boot 2026-10-14), a batch ends on a change of clk_sys; a bare chip stays at 125 MHz | - |
| 2.5 | DMA: transfer rate | Done | an unpaced transfer takes one `clk_sys` cycle per word ("one read access and one write access ... every clock cycle"); `rp2_dma` timing case passes (0098) | - |
| 2.5 | DMA: `HIGH_PRIORITY` and arbitration, read/write/AHB error flags, bus contention | Missing | a transfer never waits for the bus; the flags are stored | Go-ahead |
| 2.6.1 | ROM | Done | the real boot ROM image | - |
| 2.6.2 | SRAM | Unchecked | striped / non-striped, per-bank | - |
| 2.6.3 | Flash and the XIP cache | Done | 0098; tags only (the data comes from the image); LRU, flush time and stream pace are model choices | - |
| 2.7 | Boot sequence | Done | flash boot with boot2: MicroPython, CircuitPython, Kaluma, SDK examples in CI | - |
| 2.8.1, 2.8.3 | Bootrom: processor-controlled boot, ROM contents | Done | flash routines go through the SSI | - |
| 2.8.2 | Bootrom: launching code on core 1 | Partial | the SIO answers the handshake instead of the ROM (both builds) (`0, 0, 1, vector table, sp, entry`, echoes, restart on a wrong word) and `PSM.FRCE_OFF` `PROC1` returns core 1 to it; an entry function that returns lands in the ROM's wait (LR is left at `kCore1RomReturn`, the core parks and is put back as after a reset; a relaunch with or without `multicore_reset_core1()` works); MicroPython's `_thread` runs (tests/test_gate_core1.py), CoreMark on both cores gives a valid result (`demo/coremark_dual_core.py`) | the ROM image on core 1 (exact) as an option |
| 2.8.4, 2.8.5 | Bootrom: USB mass storage, PICOBOOT | Unchecked | | - |
| 2.9, 2.10 | Power supplies, core regulator (analogue) | n/a | the `VREG` / `BOD` registers are stored (Stub) | - |
| 2.11.1 | Top-level clock gates | Missing | | Go-ahead |
| 2.11.2 | SLEEP state | Unchecked | | - |
| 2.11.3 | DORMANT state | Missing | `XOSC.DORMANT` and `ROSC.DORMANT` are stored | Go-ahead |
| 2.11.4 | Memory power down | Stub | `SYSCFG.MEMPOWERDOWN` is stored | - |
| 2.12 | Chip-level reset: power-on, external, watchdog, source of last reset | Done | 0098 | - |
| 2.12.3 | Brown-out detection | Stub | `BOD` is stored | - |
| 2.12.6 | Rescue debug port reset | Missing | | - |
| 2.13 | Power-on state machine (incl. the watchdog interaction) | Done | 0098 | - |
| 2.14 | Subsystem resets | Done | 0098 | - |
| 2.15.1-2.15.3 | Clocks: overview, multiplexers, dividers, generators | Done | a disabled crystal gives 0 Hz as the source of clk_ref, clk_sys, clk_peri, clk_rtc and the frequency counter; the switch times of the muxes are not modelled | - |
| 2.15.2 | Clock sources: XOSC, PLLs | Done | PLL LOCK and power bits modelled; no lock delay | - |
| 2.15.2.1 | Clock source: ROSC | Done | `rosc_freq` follows `DIV` and `FREQ_RANGE`, the clock tree is retuned (0098) | - |
| 2.15.2.3 | Clock source: external clocks (GPIN) | Missing | | Go-ahead |
| 2.15.3.4 | Duty cycle correction | Unchecked | | - |
| 2.15.3.5 | Clock enables (`WAKE_EN` / `SLEEP_EN` / `ENABLED0/1`), system sleep | Stub | stored; `ENABLED0/1` exist and read 0 (their reset value); needs the cores' sleep state | Go-ahead |
| 2.15.4 | Frequency counter `FC0_*` | Done | 0098 | - |
| 2.15.5 | Resus | Missing | the `FRCE` slice needs no stopped-clock model; the detection does | Go-ahead |
| 2.15.6 | Programmer's model: configuring a generator | Done | | - |
| 2.15.6.3 | A GPIO output clock (`CLK_GPOUT0-3`) | Stub | CTRL / DIV are stored; nothing is driven onto a pin | Go-ahead |
| 2.15.6.4, 2.15.6.5, 2.15.6.6 | A GPIO input clock, enabling resus, configuring sleep mode | Missing | resus needs a "stopped clk_sys" model (requirements in 0098) | After the gate |
| 2.16.1, 2.16.2 | XOSC: overview, usage (enable, `STATUS.STABLE`) | Done | 0098 | - |
| 2.16.3 | XOSC: start-up delay | Stub | `STARTUP` is stored; `STABLE` is set at once, not after the delay | Go-ahead |
| 2.16.4 | XOSC counter | Stub | `COUNT` is loaded by a write and never counts down | Go-ahead |
| 2.16.5 | XOSC DORMANT | Stub | stored, no effect | Go-ahead |
| 2.17.1-2.17.3 | ROSC: overview, CTRL (`ENABLE`, `FREQ_RANGE`), frequency | Partial | `FREQ_RANGE` ratios and `DIV` set the frequency (6.5 MHz nominal); FREQA / FREQB validated and stored, **no effect on the frequency** (the datasheet gives no figures); the oscillator does not stop when disabled | - |
| 2.17.4 | ROSC divider, phase-shifted output | Partial | `DIV` works; `PHASE` is validated and stored, there is no second output | - |
| 2.17.5 | ROSC: random number generator | Done | `RANDOMBIT` from a hook (seeded generator by default) | - |
| 2.17.6 | ROSC counter | Done | counts down at the ROSC frequency, stops at zero (the "ROSC frequency" is taken as the output frequency: not independently sourced) | - |
| 2.17.7 | ROSC DORMANT | Stub | the register is stored; nothing goes dormant (see 2.11.3) | Go-ahead |
| 2.18 | PLL | Partial | LOCK follows power and reference; no start-up time | - |
| 2.19.2 | GPIO function select | Done | | - |
| 2.19.3 | GPIO interrupts | Partial | `INTR` level bits live, edges latch on a transition, `INTE` / `INTF` / `INTS` for proc 0; proc 1 and dormant wake are registers only (no line) | - |
| 2.19.4 | Pads | Partial | reset `0x56`, reserved bits, SWCLK / SWD, PADS_QSPI order; drive / slew / Schmitt / `VOLTAGE_SELECT` are stored (no electrical model) | - |
| 2.20-2.22 | Sysinfo, Syscfg, TBMAN | Done | Syscfg is mostly stored (Stub) | - |

### Chapter 3 - PIO

| Section | Feature | Status | Notes | When |
|---|---|---|---|---|
| 3.2 | Programmer's model (programs, control flow, registers, stalling, pin mapping, IRQ flags, interactions) | Done | | - |
| 3.3 | PIO assembler (`pioasm`) | n/a | an off-chip tool; the emulator runs assembled programs | - |
| 3.4 | Instruction set (JMP, WAIT, IN, OUT, PUSH, PULL, MOV, IRQ, SET) | Done | | - |
| 3.5.1, 3.5.2 | Side-set, program wrapping | Done | | - |
| 3.5.3 | FIFO joining (`FJOIN_TX/RX`) | Done | 8-deep joined FIFO, the other reads full and empty (PUSH / PULL stall, TXOVER / RXUNDER, FSTAT, FLEVEL, DREQ, interrupts), both bits make both unavailable, a change flushes (0098) | - |
| 3.5.4 | Autopush and autopull | Partial | the autopull happens when the next `OUT` starts, not at the `OUT` that reaches the threshold | Go-ahead |
| 3.5.5 | Clock dividers | Done | | - |
| 3.5.6 | GPIO mapping | Partial | per-cycle write arbitration (highest machine wins), `OUT_STICKY`, `INLINE_OUT_EN`, `OUT_EN_SEL` done (0098, "PIO pin-write arbitration"; the model's own choices are listed there); the 2-cycle register stage, the input synchronisers and `INPUT_SYNC_BYPASS` are not modelled | Go-ahead |
| 3.5.7 | Forced and EXEC'd instructions | Done | | - |
| 3.6 | Examples | Partial | WS2812 is a test; duplex SPI, UART TX/RX, Manchester, BMC, I2C, PWM, addition are Unchecked | - |

### Chapter 4 - Peripherals

| Section | Feature | Status | Notes | When |
|---|---|---|---|---|
| 4.1 | USB controller as a host with the Python USB device and the CDC console | Done | audited and ported 2026-10-13 (record 0098, USB) | - |
| 4.1 | USB device mode: buffers, status write-back, `BUFF_STATUS` / `BUFF_CPU_SHOULD_HANDLE`, per-buffer interrupts, double buffering, isochronous offset | Done | audited and ported 2026-10-13 | - |
| 4.1.2.8 | STALL / NAK handshakes, `EP_STATUS_STALL_NAK`, `EP_STALL_ARM` gating of EP0, `EP_ABORT` taking time | Missing | needs a token-level host (record 0098, USB: "Still not modelled") | With a token-level USB wire, own go-ahead |
| 4.1.2 | Device SOF, suspend, resume, `DEV_CONN_DIS` from VBUS, `LINE_STATE` beyond J / SE0 | Missing | as above | With a token-level USB wire, own go-ahead |
| 4.1.3 | Host: erratum RP2040-E4, NAK retry timing (`NAK_POLL`), low speed / preamble, interrupt-endpoint interval model | Missing | as above | With a token-level USB wire, own go-ahead |
| 4.1.3 | `USB_MUXING`, `USBPHY_DIRECT*`, `USBPHY_TRIM` | Done | stored with the datasheet masks and resets (the analogue pins of `USBPHY_DIRECT` read 0: no electrical model) | - |
| 4.1.3 | `EP_ABORT`, `EP_ABORT_DONE` (done at once), `EP_STALL_ARM` (cleared by a setup), `USB_PWR` (drives `VBUS_DETECTED` / `VBUS_OVER_CURR`) | Done | | - |
| 4.1.3 | `NAK_POLL`, `EP_STATUS_STALL_NAK` | Stub | stored; nothing acts on them (no NAK / STALL on the shortcut wire) | With a token-level USB wire |
| 4.2.2, 4.2.3 | UART: functional description, operation, transmitter time | Done | 0098 | - |
| 4.2.3 | UART: receive timing | Missing | bytes fed from outside arrive at once | Go-ahead |
| 4.2.4 | UART: hardware flow control (CTS / RTS) | Missing | | Go-ahead |
| 4.2.5 | UART: DMA interface | Partial | `UARTTXDMASREQ` / `UARTRXDMASREQ`; the burst requests are not modelled | - |
| 4.2.6 | UART: interrupts | Partial | RX, TX, overrun work; `UARTRTINTR` (MicroPython `IRQ_RXIDLE`), modem, BE/PE/FE are never raised | Go-ahead |
| 4.2.7 | UART: per-entry error flags `DR[11:8]` | Missing | | Go-ahead |
| 4.3.5-4.3.7 | I2C: behaviour, protocols, TX FIFO management with START / STOP / RESTART | Done | master mode, 0098 | - |
| 4.3.8 | I2C: multiple master arbitration | Done | `ARB_LOST` | - |
| 4.3.9 | I2C: clock synchronisation | Unchecked | | - |
| 4.3.10.1 | I2C: slave mode | Missing | needs an external master on the bus (requirements in 0098) | Needs a bus model, after the gate |
| 4.3.10.2-4.3.10.4 | I2C: master mode, disabling, aborting | Done | | - |
| 4.3.11 | I2C: spike suppression | Stub | registers only | - |
| 4.3.12, 4.3.14 | I2C: fast mode plus, IC_CLK configuration | Done | registers | - |
| 4.3.13 | I2C: bus clear | n/a | the chip has no registers for it | - |
| 4.3.15 | I2C: DMA interface | Stub | registers stored; **no DREQ** | Go-ahead |
| 4.3.16 | I2C: interrupt registers | Done | | - |
| 4.4 | SPI (PL022): Motorola SPI frame format, master, FIFOs, DMA requests | Done | 0098 | - |
| 4.4 | SPI: transmitter time | Done | a word takes `DSS+1` periods of `SSPCLKOUT` = `clk_peri / (CPSDVSR x (1 + SCR))`, `BSY` and the RX data follow it, the device's reply is held to the frame's end; the gap between words of SPH = 0 (`SSPFSSOUT` pulsed) is **not** modelled: the datasheet gives it only in a figure (0098) | - |
| 4.4 | SPI: slave mode, receive timeout, TI and Microwire frames | Missing | | Go-ahead |
| 4.5 | PWM: counter, TOP, compare A/B, phase-correct, dividers, B-pin counting modes, `PH_ADV` / `PH_RET`, wrap interrupt | Done | 0098 | - |
| 4.6 | Timer: 64-bit counter, `TIMEHW` / `TIMELW`, four alarms, `PAUSE` | Done | 0098 | - |
| 4.6 | Timer: `DBGPAUSE` | Stub | stored: there is no debug halt for the timer to pause on | - |
| 4.7 | Watchdog: tick generation, counter, scratch registers, `REASON`, reboot | Done | the tick is also the TIMER's and SysTick's reference | - |
| 4.7 | Watchdog: `PAUSE_DBG0`, `PAUSE_DBG1`, `PAUSE_JTAG` | Stub | stored: there is no debug halt to pause on | - |
| 4.8 | RTC: storage format, leap years, interrupts, reference clock | Done | 0098 | - |
| 4.8.4 | RTC: clk_rtc synchroniser latency | Missing | `RTC_ACTIVE`, `MATCH_ACTIVE`, LOAD arrive after 2 clk_rtc periods | Go-ahead |
| 4.9.1, 4.9.2 | ADC: controller, SAR ADC (one-shot, free-running, round-robin, FIFO, DMA, interrupts) | Done | 0098 | - |
| 4.9.5 | ADC: temperature sensor as channel 4 | Partial | `CS.TS_EN` does not gate it | Go-ahead |
| 4.9.3, 4.9.4 | ADC: ENOB, INL / DNL | n/a | silicon characteristics | - |
| 4.10 | SSI: the flash-command model used by the boot ROM and XIP, `DR` aliases, clear-on-disable | Done | 0098 | - |
| 4.10.5, 4.10.7, 4.10.8 | SSI: 16-entry RX FIFO, `RXO`/`RXU`/`RXF`/`TXE`, `TMOD` 01 | Done | 0098; TX side not timed (always empty), `TMOD` 10/11 behave as 00, `TXO`/`MST` never rise | Go-ahead (a timed shifter) |
| 4.10.6 | SSI: 32-bit frame size | Missing | | Go-ahead |
| 4.10.11 | SSI: DMA interface | Done | `DREQ_XIP_SSITX/RX` (38, 39) from `DMACR`, `DMATDLR`, `DMARDLR` | - |
| 4.10 | SSI: XIP streaming | Done | stream FIFO, `XIP_AUX`, DREQ 37 in XIP_CTRL (0098) | - |

### Chapters 1 and 5

Introduction (1), electrical and mechanical (5): n/a.

## 3. Upstream conformance suites

| Suite | What it checks | Where | State |
|---|---|---|---|
| MicroPython `tests/` against the emulated board (CI: *MicroPython official tests*, ten parallel groups; `tests/micropython_official/run_suite.sh`) | peripherals (`ports/rp2`, `extmod/machine_*`; gating) and the interpreter (`micropython/` gating; `float`, `basics`, `inlineasm/thumb` report-only) | 0098, "MicroPython's own test suite" and "The interpreter tests" | interpreter: 0 failures; peripherals: the DMA copy time and the UART timing tests (UART now passes), `machine_spi_rate` (not diagnosed; probably the missing transmitter time), and core 1 (the `thread/` group below) |
| MicroPython `tests/` (CI, gating) | `extmod` beyond `machine_*` (140 pass / 0 fail / 50 skip) | 0098 | Done; the 50 skips are modules the Pico build does not have |
| MicroPython `tests/` (CI, gating) | `unicode`, `stress`, `misc`, `io` (data files pushed onto the board; 6 board-incompatible tests excluded), `import` (modules pushed; 1 excluded) | 0098 | Done; the exclusions are board-vs-unix differences, each explained in 0098 |
| MicroPython `tests/` (CI, gating) | `extmod_hardware` with GPIO0 wired to GPIO1 (`external/jumper.py`; 4 of 10 files excluded: UART IRQ x3, I2C target) | 0098 | `machine_pwm.py` passes; CAN, counter, encoder and SD card skip themselves; the UART IRQ tests need the UART function routed to the pads (not modelled: record 0106) |
| MicroPython `tests/` (CI, report only) | `thread` on core 1 (`CORE1=1`: 24 pass / 0 fail / 9 skip themselves / 0 hang) | 0104 | Done; the 9 skips are the files' own (`mutate_*`, `stress_heap`, `thread_lock2/3`, `thread_shared2`) |
| MicroPython `tests/` not run | `cmdline` (tests the unix binary's flags), `cpydiff` (documentation scripts), `multi_*` (several boards at once), network and Bluetooth (no radio), `internal_bench`/`perf_bench` | - | Not meaningful on the emulated board, or planned |
| pico-sdk / pico-examples | firmware boots and prints (`hello_usb`), eight SDK versions | CI: *Test Pi Pico SDK* | passing |
| CircuitPython, Kaluma | boot and run a script, several releases | CI | passing |

## 4. Cross-cutting

| Item | Status | Notes | When |
|---|---|---|---|
| WASM host and the drop-in for rp2040js | Missing | record 0099, step zero not started | Go-ahead |
| Consolidating the Cython shells | Missing | less wrapper code once the ports are done | After the 0096 gate |
| Step 0 closures before the plan: ~~the DMA reading `clk_sys` from the tree~~ (done 2026-10-14); ~~`update_io_interrupt()` / `gpio_values()` over the pin banks~~ (done 2026-10-14: a pure-Python bank with the native one's interface, both chips ask the bank); ~~the crystal's frequency and the ROSC reaching the tree directly~~ (done 2026-10-14); `multicore_launch_core1` failing loudly (tried and withdrawn 2026-10-14: stock MicroPython touches the SIO FIFO at boot; the stub stays) | Missing | 0096, entry of 2026-10-14 (the order and the gate G1-G8); each small, with an oracle | Each its own go-ahead; first |
| The core's step interface and top-level host interface (one flat struct of callbacks, `Mcu::run`; pacing stays the wrapper's) | Missing | 0096, entry of 2026-10-14 | After the plan; own go-ahead |
| The USB CDC host (the computer's side of the cable: enumeration, the CDC class requests, the line coding) in C++ | Missing | `usb/cdc.py`, `usb_device.py`, `setup.py`, `interfaces.py`, ~630 lines of Python; not needed for the gate (UART `main.py`), needed by a wrapper that cannot run Python and wants a USB console (a browser REPL on wasm) (0096, 2026-10-14) | After the gate, with Phase 6; not designed |
| ~~The reset cause recorded by the chip itself (the RUN-pin row of 0089 section 1.3 on a bare chip)~~ | Done 2026-10-14 | `RP2040.enter_reset(cause=)` in both chip classes (`reset_cause.py`); `tests/test_bare_chip_reset.py`, `tests/test_reset_cause_parity.py` (0096 step 1, F1) | - |
| The reset cascade, the wiring and the chip-level interrupt lines (`set_interrupt`, `update_io_interrupt`) in C++; the DMA reading `clk_sys` from the tree | Missing | still in `native/_rp2040.pyx` and `_rp2040.py`; the pure-Python chip stays as the reference and fallback (0096, 2026-10-14) | After the plan; own go-ahead |
| A chip-level oracle over every reset trigger (power-on, RUN pin, watchdog with each `WDSEL`) | Missing | holds the native cascade equal to the pure one | With the cascade |
| How the empty-host gate gets its bytes (a UART REPL, or the USB CDC host shortcut in C++) | Missing | `usb/cdc.py` is Python behind host functions; a decision to take | Before the gate |
| The gate review: MicroPython to the REPL on an empty host interface | Missing | 0096, entry of 2026-10-06 | After the above |
| An intermediate single-core C++ release | Missing | after the gate review; keeps the three dual-core seams from 0096 (initiator id on bus accesses, IRQ line to core mask, per-core CPU state) | After the 0096 gate |
