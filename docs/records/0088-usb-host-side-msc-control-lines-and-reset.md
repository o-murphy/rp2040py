# 0088 - The host side of the emulated USB stack: mass storage, control lines, and reset

- Status: **Closed (2026-08-20).** Was "documented, measured, nothing implemented"; the two
  control-line gaps this record left open are now built and live-verified (see the closing section
  at the end), the reset question was closed by [0089](0089-one-reset-for-every-trigger.md)'s
  Phase 2, and mass storage / the 1200-bps touch are closed as **rejected**, not deferred. Three
  separate questions that all land in the same file (`usb/cdc.py`), written up together because
  they share one root: what our side of the USB link actually pretends to be.
- Sibling of [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md) (the raw-REPL half
  of the same investigation); the mass-storage section here is the constraint that record ends on.
- Touches [0057](0057-run-pin-reset-hook.md) (a reset hook on `RP2040`, proposed, not built).

## What our USB side actually is

Not a general-purpose USB host. `usb/cdc.py` enumerates the device far enough to read its
configuration descriptor, then `extract_endpoint_numbers()` walks that descriptor for **one**
interface - `num_endpoints == 2 and interface_class == CDC_DATA_CLASS` (10) - keeps its two
endpoints, and ignores everything else. It then sends `SET_CONFIGURATION(1)` and one
`SET_CONTROL_LINE_STATE`. That is the whole model: a CDC-ACM consumer.

Everything below follows from that single fact.

## 1. DTR/RTS - already works

`_cdc_set_control_line_state()` sends the class request `0x22` with `CDC_DTR | CDC_RTS` at the end
of enumeration, immediately before `on_device_connected` fires. It is not decoration: the firmware
sees it.

Measured on CircuitPython 10.2.1 (`boards/waveshare_rp2040_lcd_0_96`, via `-c`):

    supervisor.runtime.serial_connected  ->  True
    usb_cdc.console.connected            ->  True

and TinyUSB's own `tud_cdc_n_connected()` (`src/class/cdc/cdc_device.c`) is literally

    // DTR (bit 0) active  is considered as connected
    return tu_bit_test(_cdcd_itf[itf].line_state, 0);

so a `True` there means the DTR bit really is set in the CDC driver's line state - the request
reached it and was applied. On the MicroPython side the same assertion has a visible effect:
`shared/tinyusb/mp_usbd_cdc.c`'s `tud_cdc_line_state_cb()` uses `if (dtr)` to arm
`cdc_connected_flush_delay`, i.e. when the firmware flushes its TX buffer.

What is **missing** is only the ability to change the lines afterwards:

- `_cdc_set_control_line_state(value, interface_number)` already takes both parameters, but the
  sole caller passes neither, so the lines are asserted once and never move. There is no public
  `USBCDC` method, no `Device` API and no CLI flag to drop or toggle them.
- One spec detail to fix if that surface is ever added: we send the request with
  `recipient=DEVICE` and `wIndex=0`, where CDC PSTN specifies an **interface** recipient with
  `wIndex` naming the CDC control interface. TinyUSB accepts ours (the evidence above proves it),
  but that is leniency, not correctness, and a different stack need not be as forgiving.

## 2. Reset over the control lines - not implementable end-to-end today, and mostly not our half

The host-side gesture is standard: set the line coding to 1200 bps, then drop DTR/RTS. Our side
lacks `SET_LINE_CODING` (`0x20`) entirely - nothing in `usb/cdc.py` mentions it - and adding it
would be a handful of lines, structurally identical to the request we already send.

That is the easy half, and it would still not produce a reset, because **neither firmware this
project runs reacts to the gesture as built**:

- **MicroPython** implements it, guarded:
  `shared/tinyusb/mp_usbd_cdc.c`'s `tud_cdc_line_state_cb()` has two triggers -
  `MICROPY_HW_USB_CDC_1200BPS_TOUCH` (`dtr == false && rts == false` plus
  `tud_cdc_n_get_line_coding()` reading `bit_rate == 1200`) and
  `MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER` (an `rts && !dtr` -> `dtr && !rts` sequence). Both
  schedule `usbd_cdc_run_bootloader_task()` -> `machine_bootloader()`, which on this port is
  `ports/rp2/modmachine.c`'s `mp_machine_bootloader()` -> `reset_usb_boot(0, 0)`. But **`ports/rp2/
  mpconfigport.h` defines neither macro**, so on RP2040 that code is compiled out unless a board
  opts in - which fits the hardware, where BOOTSEL is a physical button.
- **CircuitPython** has the destination but no trigger on this path:
  `ports/raspberrypi/supervisor/port.c` defines `reset_to_bootloader()` (`reset_usb_boot(0, 0)`),
  and none of `supervisor/shared/usb/usb.c`, `supervisor/shared/serial.c` or that same `port.c`
  contains a `tud_cdc_line_state_cb` or any `1200` handling (checked at 10.2.1; a repo-wide search
  would be worth doing before relying on the absence). Its documented route is
  `microcontroller.on_next_reset(RunMode.BOOTLOADER)` + `microcontroller.reset()`, plus double-tap
  on hardware.

And even with both halves in place, the *result* is the interesting part: `reset_usb_boot()` jumps
into the RP2040 bootrom's own USB bootloader. To be more than "the guest vanishes into unemulated
code" that needs the bootrom image (which this project can already fetch - `BOOTROM` in
`firmware_specs.json`) **and** a device-mode USB model for what the bootloader exposes (UF2 mass
storage / PICOBOOT). That is a much bigger piece than the CDC request that starts it.

**So the cheap, useful thing is a different reset, not this one.** [0057](0057-run-pin-reset-hook.md)
already designs a reset hook on `RP2040` in the shape `RPWatchdog.on_watchdog_trigger` uses - the
one live-reset path that exists today is `BaseDevice._on_watchdog_trigger()`, what a guest's own
`machine.reset()` reaches. Exposing that as a host-side "reset the board" command gives the
practical capability without USB, the bootrom, or firmware opt-ins. A 1200-bps touch is worth
building only when there is a firmware in play that honours it (a board that defines those macros,
or a non-MicroPython/CircuitPython image).

## 3. Seeing CIRCUITPY as a USB drive

Three options, in the order I would weigh them:

- **B - read the flash region (available today).** `--dump-fs` already produces exactly the bytes
  a mass-storage host would be served: CircuitPython's `supervisor_flash_read_blocks()` reads
  `XIP_BASE + CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR + lba * 512`, which is the same region the
  dump copies. The image loop-mounts, or opens with `demo/mkfat12.py --read`. What is missing is
  only convenience (a "dump and mount" wrapper) and one caveat: the firmware keeps a 4 KiB write
  cache (`_cache`/`port_internal_flash_flush()`), so a dump taken mid-run can lag what the guest
  sees.
- **A - implement the MSC class beside CDC.** Bulk-Only Transport (31-byte CBW / 13-byte CSW) plus
  a handful of SCSI commands (`INQUIRY`, `READ CAPACITY`, `READ(10)`, `WRITE(10)`,
  `TEST UNIT READY`, `MODE SENSE`), structurally the same job `cdc.py` does for CDC and probably
  a similar size. The real prize is not "seeing" the drive - B already gives that - but
  **fidelity**: a real board's CIRCUITPY is read-only to the guest exactly because a host holds it,
  and today we never take that lock. **This is mutually exclusive with
  [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)'s writable-CIRCUITPY route by
  construction** (that route works *because* nothing enumerates MSC), so it has to be opt-in - a
  flag, off by default - and 0087's route documented as depending on its absence.
- **C - expose the emulated device to the host OS** (usbip/vhci-hcd, so a real kernel mounts
  CIRCUITPY). Maximum fidelity, needs a kernel module and root, is effectively Linux-only, and
  amounts to bridging two host stacks for what B already delivers. Not recommended.

## Summary of what is and isn't there

| capability | today | to build |
|---|---|---|
| DTR/RTS asserted at connect | **works**, verified through the firmware | - |
| DTR/RTS changed at runtime | no caller/API/flag | small: expose `_cdc_set_control_line_state`, fix the request recipient |
| `SET_LINE_CODING` (baud) | absent | small, mirrors the request we already send |
| 1200-bps-touch reset | absent, **and no firmware here honours it** | needs a firmware that opts in, plus bootrom USB-device emulation to be meaningful |
| host-driven board reset | only the guest's own `machine.reset()` path | [0057](0057-run-pin-reset-hook.md)'s reset hook - the cheap, useful one |
| CIRCUITPY as a drive | `--dump-fs` gives the same bytes | convenience wrapper; MSC only for fidelity, opt-in, mutually exclusive with [0087] |

## Note (2026-08-20): where a reset from this side would land

[0089](0089-one-reset-for-every-trigger.md) collects every reset trigger in the tree - the guest's
watchdog write, a bare Ctrl-D at the raw-REPL prompt, a host API call, and [0057]'s RUN pin - and
proposes one owner per level (soft = firmware-side, hard = the emulator's). A 1200-bps-touch reset,
if it were ever implemented, is a fifth trigger and belongs on the same hard-reset owner rather
than growing its own sequence. This record's own finding stands unchanged: rp2 defines neither
`MICROPY_HW_USB_CDC_1200BPS_TOUCH` nor `..._DTR_RTS_BOOTLOADER` and CircuitPython has no such
handler, so there is nothing here to trigger it with. The note is about shape, not schedule.


## Update (2026-08-20): mass storage is out of scope; the reset half is planned

Maintainer's decision, so the three questions this record holds together now split cleanly:

- **Mass storage (option A above) is not planned.** It needs an MSC class *and* a device-mode USB
  controller model; high complexity, no current priority. Option **B** (`--dump-fs`, the same bytes
  a host would be served) stays the answer for *seeing* CIRCUITPY, and [0087]'s writable-CIRCUITPY
  route stays valid by construction, since it works precisely because nothing here claims the MSC
  interface. Option C (usbip) stays not recommended.
- **The 1200-bps touch is therefore not worth building either**, and this record's own finding is
  still why: rp2 defines neither `MICROPY_HW_USB_CDC_1200BPS_TOUCH` nor
  `..._DTR_RTS_BOOTLOADER`, CircuitPython has no `tud_cdc_line_state_cb` on that path, and the
  destination (`reset_usb_boot()` -> the bootrom's UF2 mass-storage mode) is exactly the emulation
  being declined above. If it is ever built it is a *caller* of the hard-reset owner, not a
  behaviour of its own.
- **"Host-driven board reset" - the cheap, useful one this record recommended - is now
  [0089](0089-one-reset-for-every-trigger.md)'s Phase 2**, with the reset-cause fidelity it needs
  in Phase 1 and [0057]'s RUN pin in Phase 4. Nothing in it depends on this record's USB work.
  **Built 2026-08-20**: `BaseDevice.ahard_reset()`, live-verified on both firmwares - so this
  record's table row is answered without a single byte of MSC or control-line work, which was the
  point.
- **The DTR/RTS gaps stay open and unscheduled**: runtime control-line changes and
  `SET_LINE_CODING` are still absent, and the recipient/`wIndex` correction in section 1 is still
  the thing to fix if that surface is ever added. They are simply not on any critical path now that
  the 1200-bps touch is not being built.

## Closing (2026-08-20): the control lines are movable now, and what that measured

Built, in `usb/cdc.py` plus a thin pass-through on `BaseDevice`:

- **`USBCDC.set_control_lines(dtr=..., rts=...)`** and `BaseDevice.set_control_lines()` - the "no
  caller/API/flag" half of section 1's table. Plus read-back properties (`dtr`, `rts`,
  `control_line_state`) which report *what this host last sent*, since nothing here can ask the
  firmware what it thinks the lines are.
- **`USBCDC.set_line_coding(baud_rate, ...)`** / `BaseDevice.set_line_coding()` and a `LineCoding`
  NamedTuple - `SET_LINE_CODING` (0x20) with its EP0 OUT data stage answered from
  `_on_endpoint_read()`, which is the first control request here that has a data stage at all.
- **The recipient/`wIndex` correction section 1 asked for.** Every CDC class request now goes out
  as `recipient=INTERFACE` with `wIndex` naming the CDC *communications* interface, found by a new
  `extract_control_interface_number()` (class 0x02, the sibling of the class 0x0A data interface
  `extract_endpoint_numbers()` already looked for). It is 0 in every descriptor set in
  `tests/test_cdc.py` - MicroPython, CircuitPython, the pico-sdk and the Arduino core alike - so
  the wire value is unchanged and the *recipient* is the real fix.
- `USBCDC.reset()` clears all of it: a chip off the bus asserts nothing, and the re-enumeration
  re-asserts DTR/RTS through the same path a first boot uses.

**Threading:** both device-level methods are fire-and-forget onto the engine-room loop
(`schedule_threadsafe()`, [0030](0030-external-device-concurrency.md)'s rule, the shape
`ResetButton` uses). Deliberately no awaitable form: the effect is guest state, so what proves it
landed is asking the guest, not a Future on this side.

### What the live verification found

`tests/control_lines_run.py` (both families, wired into both CI workflows):

- **CircuitPython 10.2.1 sees DTR move.** The guest samples `supervisor.runtime.serial_connected`
  in a loop while the host drops the line underneath it: `SAMPLES 50 10` - 10 samples reading
  `False` inside the window, `True` on both sides of it. This is the first direct evidence in this
  tree that a *runtime* control-line change reaches the firmware, where section 1 could only show
  the once-at-enumeration assertion arriving.
- **Neither family exposes the line coding to Python**, which section 1 did not know: CircuitPython
  10.2.1 answers `AttributeError: 'Serial' object has no attribute 'baudrate'` for
  `usb_cdc.console.baudrate`, and MicroPython has no equivalent at all. TinyUSB keeps the value for
  `tud_cdc_n_get_line_coding()`, which nothing here calls. So `SET_LINE_CODING` is guest-invisible
  today and is kept for spec completeness and for whatever future firmware or 1200-bps-touch
  caller would read it - **not** because anything currently consumes it. The console this project
  actually hands to a user is a TCP socket or a PTY, where a baud rate has no meaning either.
- **MicroPython 1.17 and 1.23.0**: the console survives a DTR/RTS change and a line-coding change.
  That is all that is checkable there, and it is the regression that would matter - enumeration
  sends the same request on every boot, so the recipient change had to not break it.

### What stays unbuilt, and is now closed rather than open

- **Mass storage (option A) and the 1200-bps touch: rejected**, per this record's own 2026-08-20
  update. Option B (`--dump-fs`) stays the answer for seeing CIRCUITPY, option C (usbip) stays not
  recommended.
- **Host-driven board reset: built elsewhere** - 0089's Phase 2 (`BaseDevice.ahard_reset()`).
- **No CLI flag** for the control lines. The device API is the surface; a `--drop-dtr`-style flag
  has no caller asking for it, and the console the CLI exposes is a socket/PTY where the host end
  has no control lines to mirror anyway.
