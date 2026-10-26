# Testing two twin backends against each other: whole chips, no block swapping

Working note, 2026-10-26. **Documentation only - nothing here is built, and each step needs its own go-ahead** (CLAUDE.md, "Document" vs. "implement"). It comes *before* any narrowing of the
VM / world border ([vm-world-border.md](vm-world-border.md)): the narrowing is only safe once the two backends are compared in the way the product uses them.

## The model

There are two **twin backends** of one chip: the pure-Python one (`_rp2040.RP2040` and the blocks of `peripherals/_*.py`) and the C++ one (`native/_rp2040.RP2040` over `core/*.hpp`, and,
later, the same core behind wasm / FFI hosts). They must behave the same through their **public interface** (tier 1 and tier 2 of [0096](../records/0096-cpp-mcu-core.md), "The public
surface: three tiers"). Their insides differ and are not compared as structure. **A block of one backend never runs inside the other, in production or in a test**: the only things that cross from a
host language into a backend are external devices (pins, buses, the USB wire, ADC inputs, firmware, time).

So the test is: *the Python block inside the Python chip behaves exactly like the C++ block inside the C++ chip*, observed from outside. Not "the two blocks are implemented alike", and not
"swapping one in for the other breaks nothing".

## What exists today, and what is wrong with it

`tests/utils/*_diff.py` (16 files: adc, clocks, dma, i2c, ident, pin, pio, ppb, psm_xosc_resets, pwm, rosc, rtc, small_blocks, spi, ssi, uart, watchdog) are lockstep oracles: one generated
operation stream, everything observable compared after every step, first divergence reported with its step; they are proven by agreement over seeds, by perturbations of the candidate (caught at
the step they are made), by logic mutants of the reference (caught) and by coverage counts. **All of that stays.** What changes is the rig:

- today a rig builds a chip and *replaces its blocks* with the reference ones (`_replace_*` in each file; for the PIO it replaces `chip.pio[i]` and the window of the chip's bus), or replaces
  the chip's `dma` / `set_interrupt` with a recorder to get the ordered log of what left the block, or stands a probe in for a neighbour (`pin_diff`'s `_PioProbe`, `_PwmProbe`);
- that tests a mixture that does not exist in the product, it forces the native shell to carry swap seams (`set_*_world`, `_native_*_address`, `_from_mcu`: 41 places in `native/_rp2040.pyx`, and
  the `McuHost` slots `pin_source`, `pin_inputs`, `pin_update`, `pin_input_changed`, `xosc_hz`, `rosc_hz`, `clk_sys`, `ssi_baudr`, `reset_block`, `record_cause`), and it tests the block with a
  fake neighbour instead of the real one.

## The rig of the new model

**Two whole chips, one stream of operations.** Checked on 2026-10-26: `_rp2040.RP2040()` and `native._rp2040.RP2040()` coexist in one process, and the same bus writes give the same answers
(UART0 `UARTLCR_H` read back 112 on both; PIO0 `INSTR_MEM0` and `snapshot()["instructions"][0]` 57345 on both; the blocks are `peripherals._pio` and `native._pio` respectively, nothing swapped).

An operation is only one of the tier-1/2 interface:

- a bus read or write (with the set / xor / clear aliases), as the existing generators already emit them (`("write", pio, offset, value, alias)`, `("read", ...)`);
- a pin driven or released (`gpio[i].set_input_value` / `release_input`), a pad / function configuration done through the **bus** (not `pad_value |=`);
- an external device attached to the chip - **the same device object class on both chips** - answering a UART byte, an SPI word, an I2C transaction, an ADC conversion, a USB request; its
  observed calls are part of what is compared;
- firmware in (the flash buffer, the bootrom) and time forward (see below);
- observation: `snapshot()` of every block, the results of reads (including a read's side effects) and exceptions, and the ordered **event log**.

### The event log needs a public port

Today the order of interrupt lines, DREQs, pin changes and warnings comes from replacing `chip.set_interrupt`, `chip.dma` and the logger. In the new model the chip offers a **trace port**
(tier 2, same on both backends): an event stream of `irq(line, level)`, `dreq(number, level)`, plus what already leaves through the border (`pin_changed`, bytes and transactions of the devices,
`warn(block, kind, offset, value)`). The C++ core already has the hooks (`Mcu::set_irq_hook`, `set_dreq_hook`, `warn`); the Python chip needs the equivalent call points. Events are compared as an
ordered sequence per step, as the log of today is.

### Time

The PIO oracle calls `block.advance(n)` directly (it is the plumbing the batch loop uses). A whole-chip rig moves time with the public call that exists on both backends (the chip's clock
advance / a bounded run - to be named; `Simulator._execute_batch()` is the threadless driver), with small and huge steps as today. Both chips are stepped by the same amounts; any difference
in what a step does with time is a finding.

### Neighbours are real

With the real neighbour on both sides (the real DMA acts on the PIO's FIFOs and DREQs; a real PIO drives the pins that the pin oracle watches), blocks are compared together, not each
against a recorder. Stimulus that is only interesting for one block still compares everything: a divergence in another block shows up at once and is reported by block (the snapshot keys are
per block). Cost: the oracles that used a recorder or a probe get *more* surface compared, and may find real differences they never could.

### Perturbations and mutants

- **Perturbations** of the candidate (to prove the oracle sees damage) become bus writes, explicit inputs (`raise_*`, `set_counter`, `fire_timeout`) or an extra clock step on the candidate chip
  alone - as the snapshot series already did.
- **Mutants** keep their form: the reference's source with one expression changed, run as the *pure* chip - a mutated pure chip is still a pure chip, and swapping inside one backend is
  a test of the test, not of the product. The rig has to build a pure chip with a mutated block module (today `mutant_rig`); how a mutated block gets into `_rp2040.RP2040` without a swap
  of the chip's wiring is the part of the rig to design.

## The plan, in order (each step its own go-ahead)

1. **The twin rig** (`tests/utils/twin.py`, working name): two chips, the operation vocabulary above, the event log, the comparison and the divergence report; nothing about a specific block.
2. **The trace port** on both backends (a few lines in each; tested on its own: the same writes give the same event sequence).
3. **One pilot oracle** moved over - a small one with a real neighbour (UART or RTC; PIO is the hardest because of DMA and pins). Its perturbations and mutants are re-proven, and its
   strength is compared with the old version on the same seeds: no mutant that the old rig caught may escape the new one.
4. **The rest, one file at a time**, each keeping its old version until its replacement catches everything the old one did; the old rig then goes.
5. **The conformance test of tier 1 and tier 2** ([0096](../records/0096-cpp-mcu-core.md), step 5 of the tier plan) is this rig plus a list of the public names that must exist on both chips; it is
   not a separate piece of work.
6. Only then [vm-world-border.md](vm-world-border.md): narrowing `McuHost` and the shells, with the twin oracles as the check that nothing observable changed.

## Pilot result (2026-10-26, RTC)

`tests/utils/rtc_diff.py` moved to the model with a change of its `Rig` only (about twenty lines): the reference is `_rp2040.RP2040()`, the candidate `native._rp2040.RP2040()`, no block is moved
from one chip into the other; a mutant still replaces the block, inside the pure chip. The generators, the comparison, the coverage counters and the tests are untouched. **`tests/test_rtc_diff.py`:
101 passed** (ten seeds of 3,000 steps, all 90 logic mutants caught, the four perturbations caught at their step, the coverage counts met) - the oracle is as strong as before and now compares
two whole chips, with the real clock, bus and clock tree on each side.

What the pilot corrected in this note:

- **The trace port is not new work for the interrupt line.** `chip.set_interrupt` can be assigned on both chips (on the native chip it is a property: the lines go to the function, see the
  CHANGELOG of 1.0.0b1), and the RTC rig's tap already used it on the native chip. What is still open is the DREQ log: today a rig replaces `chip.dma` with a recorder, which with real DMAs on
  both chips is not needed (the DREQs act), but the *order* of requests is then only visible through the DMA's own snapshot - a DREQ tap is needed for the DMA, PIO, UART, SPI and ADC oracles.
- **A few operations still reach into the block**, and are the next to be moved to the bus: the `reset` op (`peripheral.reset()`; the public way is the RESETS block) and three of the
  perturbations (`peripheral.warn`, a block `write_uint32`). They work on both chips, which is why the pilot is green, but they are block-level calls and not tier 1/2.
- **The 32-bit CI jobs** hold two chips instead of one per oracle rig (the pure chip plus the native one); the `conftest` collection after each test already exists for that.

## Migration status (2026-10-26)

`tests/utils/twin.py` holds the one shared piece, `new_chip(kind)`: `"default"` is the C++ chip, `"pure"` and `"mutant"` the pure-Python chip. **Moved to the model, all green with every
mutant and perturbation caught as before:** rtc, rosc, adc, ident, small_blocks, psm_xosc_resets, ppb, watchdog, pwm, ssi, clocks, i2c, spi, uart, dma, pio, pin (17 oracles; the change
in each is the constructor of its `Rig`, plus removing the `pure` replacement). `usb_diff` and `xip_diff` already built a pure chip for their reference.

**What is still not the model, in every migrated oracle** (works on both chips, so it is green, but is a next step):
- *neighbours stood in for*: the ADC, PWM, SSI, PIO oracles still assign `chip.dma` a DREQ recorder, the pin oracle assigns `chip.pio` / `chip.pwm` probes, the DMA oracle attaches a probe
  block on the bus - symmetric on both chips, but not the real neighbour; a DREQ tap replaces the recorders (see "The event log needs a public port");
- *block-level calls*: the `reset` ops (`peripheral.reset()`), a few perturbations (`peripheral.warn`, a block `write_uint32`), the PIO's `advance()`, the plumbing the oracles read;
- *private attributes of the chip* the rigs touch (`chip.peripherals[...]`, `chip.rtc = block` in the mutant path - inside the pure chip, so allowed).

## Open questions

- Which public call advances time identically on both chips (the pure chip's `Simulator` path vs the native batch loop)? The two batch loops are themselves under test
  (`tests/test_batch_parity.py` compares them), so the rig's driver must not hide a difference in them.
- Can a mutant block be put into a pure chip without touching the chip's wiring (a chip class that takes its block classes as arguments, or a module-level override that `_rp2040.py` reads)?
- Both chips share process state: the parked-error slot (`native/_pending.pyx`) and the logger. The rig has to keep them apart (separate loggers per chip; the pending slot only matters on the native side).
- Pure-Python speed: whole chips cost two chips' worth of time per step and 16 MB of flash each (the 32-bit CI jobs already collect between tests for that reason). The step counts of the
  current oracles may have to drop or the chips share firmware memory.
- Stimulus that needs an interrupt *handler* (firmware) rather than bus operations: the CPU runs on both; a small hand-assembled program could be an operation too. To be decided with the first
  oracle that needs it.
