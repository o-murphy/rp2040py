# 0091. Porting the emulator to ESP32-C3: what it would actually cost

- Status: **Idea only - an off-hand feasibility estimate, nothing proposed for building
  (2026-08-20).** Written per CLAUDE.md's "document vs. implement" rule from a theoretical
  question ("чи важко буде зробити порт емулятора але на esp32-c3?"), so that the reasoning
  survives the conversation. It is **not** a plan, and it is not source-verified - see "Confidence"
  below before treating any ESP32-C3 claim here as fact.
- Conceived: 2026-08-20
- Related: [0027](0027-cyw43-wifi.md) (the CYW43 epic - the direct comparison for "how hard is
  WiFi", and the source of the **3g rule** this record deliberately does *not* satisfy yet),
  [0048](0048-cyw43-nat-reflector.md) (the NAT bridge that made WiFi useful), [0026](0026-main-thread-asyncio.md)
  (the engine-room concurrency model - the most reusable thing here), [0072](0072-w5500-ethernet-and-board.md)
  (the other "proposed, phased plan only" record, whose shape this one deliberately does *not*
  claim - 0072 is sourced, this is not), [0052](0052-xip-ctrl-registers.md) (the
  "no cache modelled on purpose" decision that a C3 port could not keep),
  [0032](0032-docs-restructure.md) (why this is a record and not a chat message)

## Confidence, stated first

Two different kinds of claim appear below and they are not equally trustworthy:

- **Everything about *this* repo** - file paths, line counts, what is coupled to what - was read
  out of the tree on 2026-08-20 and is checkable.
- **Everything about the ESP32-C3 itself** - its ISA, peripheral set, boot flow, ROM behaviour,
  WiFi implementation - is from general knowledge, **not** read out of the ESP32-C3 TRM, ESP-IDF,
  or any upstream source in the session that produced this record.

That second half is exactly what [0027](0027-cyw43-wifi.md)'s 3g rule forbids relying on. Nothing
here is wrong on purpose, but if this idea is ever picked up, **step one is re-deriving every C3
claim from the TRM / ESP-IDF source**, and this record's estimates should be assumed to move when
that happens. It is written down as a starting point for that work, not as its conclusion.

## The short answer

The CPU core is the *easiest* part, not the hardest. Two things carry essentially all the risk:
the boot ROM, and WiFi. Getting to "MicroPython REPL over the console" looks like months of the
same kind of work this project already knows how to do. Getting to "WiFi works, like it does on
Pico W" does not look honestly achievable at all - and that is a **structural** difference from
[0027](0027-cyw43-wifi.md), not a matter of effort.

## What ports over nearly free

Of ~27k lines under `src/`, roughly 40% is harness that has little to do with which chip is being
emulated - and it is the half that cost the most to get right, by record count:

- `simulator.py` + the engine-room asyncio model ([0026](0026-main-thread-asyncio.md), 5 phases)
- `clock/` - the time model, plus its Cython port ([0039](0039-simulation-clock-native-port.md))
- `device/` minus `bootrom.py` - the raw-REPL runner, `aexec()`, mpremote compatibility, and
  [0089](0089-one-reset-for-every-trigger.md)'s reset ownership. This layer talks to *firmware*,
  not to silicon, and both MicroPython and CircuitPython have ESP32-C3 ports.
- `gdb/` - only the register table differs (RISC-V vs M0+)
- `external/` + `boards.py` - the `ExternalDevice` framework, littlefs/FAT12 images, the live-boot
  CI pattern, and the skill/checklist around them ([0049](0049-external-device-authoring-docs.md),
  [0067](0067-external-devices-and-boards-skill.md))
- The `_x.py` + `native/_x.pyx` + `.pyi` twin-file pattern, as a template

**But there is no chip-abstraction seam today, at all.** `device/base_device.py` constructs
`RP2040` directly (`self.simulator = Simulator(rp2040=build_rp2040_from_spec(board))`, then
`self.mcu: RP2040 = ...`), `memory_map.py` is a flat set of RP2040 constants, and
`ExternalDevice.attach(rp2040)` has the chip in its signature. So "reusable in principle" is not
"reusable today": either a second package shares the harness, or a seam gets introduced first -
and introducing that seam is its own epic, touching the device API and every `ExternalDevice`.

## What gets rewritten

| Here | On C3 |
|---|---|
| `_cortex_m0_core.py`, 1573 lines of Thumb-2 subset (+ Cython twin) | RV32IMC + CSRs + M extension. More orthogonal encoding than Thumb - **easier to write**, guess ~2k lines |
| 30 files under `peripherals/` | a different set: GDMA, SYSTIMER, TIMG, LEDC, RMT, eFuse, RNG, crypto accelerators, APM |
| PIO - `_pio.py` + `_state_machine.py`, ~1.2k lines plus a Cython port and three records ([0037](0037-pio-clock-coupled-stepping.md)/[0043](0043-pio-dma-first-batch-race.md)/[0063](0063-pio-clkdiv-and-delay-cycles.md)) | **does not exist on C3.** All of it drops. RMT replaces it for the one thing boards here actually use PIO for (WS2812), and RMT is simpler |
| `peripherals/usb.py`, 906 lines of DPRAM device controller | USB Serial/JTAG - a fixed-function CDC-ACM + JTAG peripheral with FIFO registers. **Simpler**, and the `usb/cdc.py` consumer above it should mostly survive |
| `xip_ctrl.py` - [0052](0052-xip-ctrl-registers.md) models the registers and deliberately **not** the cache | cache **plus an MMU page table** mapping flash into IROM/DROM. Without real translation, code does not execute at all - so 0052's convenience is one of the things a C3 port cannot keep |

## The two real blockers

### 1. The boot ROM is not a one-shot

`device/bootrom.py` here is 4105 lines of data with a provenance header naming the open-source
`pico-bootrom` repo and the exact revision - i.e. it is *derivable*, and licensed.

The C3's ROM is ~384 KB, closed, and - the part that matters - **not one-shot**: ESP-IDF links
against ROM functions through its `.rom.ld` scripts and calls into them continuously at runtime.
So "stub the boot ROM and move on" is not available; a real dump has to live in the tree. There is
precedent for distributing one (Espressif ships ROM images with its own QEMU fork), but that is a
different situation from building a BSD-licensed source, and the licensing needs its own answer
before any code gets written.

Adjacent, and equally capable of aborting a boot if faked badly: eFuse (IDF reads chip revision,
MAC, calibration data), the RNG register, the clock tree, and the several watchdogs.

### 2. WiFi is a different *kind* of problem than CYW43 was

On RP2040 the radio is an **external** chip on a bus whose protocol can be derived from driver
source - which is why [0027](0027-cyw43-wifi.md) was hard but possible, and why the 3g rule was
enough to keep it honest.

On C3 the WiFi MAC is **on-die**, driven by closed binary blobs (`libpp`, `libnet80211`) writing
undocumented registers. There is no public register documentation to derive from. Both ways out
are bad: reverse-engineer the blob's register usage (very large, very brittle), or intercept at the
`esp_wifi` API level - which breaks the property this whole project is built on, that *unmodified*
firmware boots as-is. BLE is the same story.

Corroborating, as far as is known without checking: Espressif's own QEMU fork does not emulate the
radio. Wokwi does offer WiFi for ESP32 targets, but how is not publicly described.

## Rough phasing, if it were ever picked up

1. RV32IMC interpreter + memory + a bare UART, tested against hand-written assembly - **weeks**,
   and the pleasant part.
2. ROM + second-stage bootloader + cache/MMU, up to "the bootloader prints something". **This is
   where it becomes clear whether the idea is alive**, and it should be attempted before anything
   else is planned.
3. Enough peripherals for MicroPython to boot, console over USB Serial/JTAG, `aexec()` working -
   **months**, but a familiar kind of month: the same "live boot found three real bugs" loop as
   [0027](0027-cyw43-wifi.md)'s steps 3e/3f.
4. WiFi - a separate category, not the next phase.

## Conclusion (opinion, not a decision)

Doable and interesting **up to phase 3**, and phase 3 alone would already be a useful emulator.
But it is not a "port" - it is a second chip sharing a harness that currently has no seam for one,
so the abstraction epic comes first. And if the motivation were ever "WiFi on a cheaper chip",
C3 is the *worse* target despite the hardware saying the opposite - because here, an external
radio with a derivable bus protocol is a feature.


## Addendum (2026-08-20): would a C3 emulator run *faster*, and two claims now sourced

Asked as a follow-up to this record: RV32IMC is a simpler, more regular ISA than Thumb-2 - would
emulating it beat the Cortex-M0+? Answered here rather than in chat because the reasoning leans
entirely on numbers this project already measured.

**Verdict: no, and end-to-end it would very likely be slower. Speed is not a reason to build this.**

*Per instruction: a wash.* RISC-V decodes more regularly (fixed field positions), but the C
extension means variable length - checking the low two bits before every fetch, and 32-bit
instructions that can sit at 2-byte alignment. Roughly cancels out.

*Per unit of useful work: RV32IMC modestly ahead.* Two reasons, and the first matters more than it
looks. **32 registers against ARMv6-M's effective eight** means fewer spills, and a spill here is
not a register move - it is a call into `read_uint32`/`write_uint32` with its range-compare chain,
which is one of the most expensive things this emulator does. Second, the **M extension has
hardware divide**; the M0+ has none, so a dividing guest either runs a software routine or goes
through the RP2040's SIO divider (which `sio.py` models with real per-access work).

*End-to-end - time to a usable REPL - C3 loses*, and this is the half that decides it:

- **The ROM cannot be skipped.** This emulator starts at `FLASH_START_ADDRESS` and never runs the
  RP2040 bootrom's boot path at all. On C3 the flash window does not exist until the second-stage
  bootloader programs the MMU, so there is nowhere to jump to - the ROM runs, for real. That is the
  same blocker as "Gap 1" above, seen from the performance side.
- **Heavier firmware** on the way up, and **160 MHz against 125** - 28% more instructions for the
  same wall-clock fidelity.

*And the ISA is not where the time goes anyway.* [0013](0013-cython-core.md) measured the batch
loop itself at **43-47% of profiled time even with a fully native CPU core and bus** - which is why
[0034](0034-execute-batch-native-port.md) ported it. That cost is ISA-independent. The harness
dominates; changing the guest architecture moves a smaller lever than changing the host loop did.

### XIP versus MMU, priced

The one place a C3 port pays on the hottest path in the emulator - every instruction fetch goes
through `read_uint16()`. Today that is a range compare, an AND, and a buffer read: all four XIP
mirrors fold onto one array by masking, and [0052](0052-xip-ctrl-registers.md) models `XIP_CTRL`'s
registers and no cache at all, so there is zero per-access bookkeeping.

A C3 needs `page = (addr - window) >> 16; phys = table[page] * 64K + (addr & 0xFFFF)` instead. But
this is **engineering, not a wall**: instruction fetch has enormous locality, so caching the last
translation makes the common case a single "same page?" compare, and a flat 256-entry array (16 MB
/ 64 KB) indexed by page number gets it to about the cost of the mask. The cache itself needs no
model, by 0052's own argument - reads return flash contents either way; only invalidation on flash
*writes* would need care, and reads and writes already share one array here.

So the MMU is a real tax that can be engineered down to near parity. What cannot be engineered away
is the boot-sequencing dependency above.

### Two claims from this record, now actually sourced

The Confidence section says every C3 claim here is unsourced general knowledge. Two are no longer:

- **GDMA exists** - `SOC_GDMA_SUPPORTED 1` / `SOC_AHB_GDMA_SUPPORTED 1`, plus `SOC_AES_GDMA`,
  `SOC_SHA_GDMA`, `SOC_ADC_DMA_SUPPORTED`, in ESP-IDF's own
  `components/soc/esp32c3/include/soc/soc_caps.h`. Worth recording because the API reference has
  **no public GDMA page** for C3 (IDF exposes it through the private `esp_private/gdma.h` and drives
  it from the SPI/I2S/crypto drivers), so "no DMA driver documented" reads like "no DMA" and is not.
- **RMT exists and PIO does not** - "Remote Control Transceiver (RMT)" is in the C3 peripheral API
  index; nothing PIO-shaped is.

Method worth reusing for the rest of this record: `soc_caps.h` per target is the cheapest
authoritative answer to "does this chip have X", and it is one `gh api` call.

*One correction to the PIO row above, while here:* dropping PIO removes a real cost, but a smaller
one than the raw profile suggests. The ~55% of profiled time PIO once took was a window where it was
actively driving CYW43's gSPI; since [0063](0063-pio-clkdiv-and-delay-cycles.md) an idle PIO costs a
single integer compare per instruction. What disappears on C3 is the peak under load, not a
standing tax - and it disappears twice over, since an on-die radio means there is no bit-banged bus
to decode at all.

