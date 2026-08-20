# 0089 - One reset, every trigger: soft vs hard, guest-initiated vs host-initiated

- Status: **Proposed - design only, nothing implemented (2026-08-20).** Asked for while working
  through [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)'s restart step: a
  reset that behaves the same no matter who asks for it.
- Related: [0087] (needs a restart after writing CIRCUITPY over the REPL - its immediate consumer),
  [0057](0057-run-pin-reset-hook.md) (the RESET button / RUN pin, blocked on exactly this),
  [0088](0088-usb-host-side-msc-control-lines-and-reset.md) (the USB host side, where a 1200-bps
  touch reset would land if it ever worked), `docs/reference/mpremote.md` (which already documents
  three of the four triggers below as working).

## The problem

There is no "reset" in this tree. There are several unrelated things called reset, at two
different levels, reachable from different places, and each trigger currently gets whichever one
its own code path happened to wire up.

Two levels, and conflating them is the actual hazard:

| | **soft reset** | **hard reset** |
|---|---|---|
| what restarts | the firmware's VM/interpreter | the chip |
| flash | untouched | preserved (`preserve_flash=True`) |
| USB | stays enumerated | drops, must re-enumerate |
| host's REPL connection | survives; banner comes back | invalid until re-enumeration |
| who implements it | the firmware, entirely | the emulator |

Four triggers, and today they do not share an implementation:

| trigger | initiated by | today |
|---|---|---|
| watchdog TRIGGER bit (`machine.reset()`, `microcontroller.reset()`, mpremote `reset`/`bootloader`) | guest | **hard** - `BaseDevice._on_watchdog_trigger()`, wired to `RPWatchdog.on_watchdog_trigger` |
| Ctrl-D at the raw-REPL prompt (mpremote `soft-reset`) | host, over CDC | **soft** - firmware-side; the emulator does nothing, and `docs/reference/mpremote.md` says so |
| a host API call | - | **does not exist**, at either level |
| RUN pin / RESET button | an `ExternalDevice` | **does not exist** - [0057] |

The two that exist are fine in isolation. What is missing is that they are the same two events,
and every other trigger should route into them rather than grow a third behaviour.

## What "one reset" means concretely

**One owner per level, every trigger a caller.** Not one function for both levels - they are
genuinely different events and a caller has to pick.

### Hard reset

The sequence already exists, as the *body of a callback* rather than as a method:
`BaseDevice._on_watchdog_trigger()` is `mcu.reset(preserve_flash=True)` / `core.pc =
FLASH_START_ADDRESS` / `cdc.reset()`. It should become a real method, with the watchdog hook
reduced to a caller of it. Then guest-initiated (watchdog), host-initiated (a new API), and
[0057]'s RUN pin are the same code by construction.

Four things the method has to get right, all visible in today's code:

- **It is not a second `start()`.** `start_async()` raises on `self._started`
  (`device/base_device.py`), so a reset cannot be expressed as re-starting.
- **Re-enumeration is part of it, but only the host-initiated caller can await it.** `cdc.reset()`
  clears `_initialized`/descriptors/endpoints and resets `usb_ctrl` in place; `on_device_connected`
  is *not* cleared by it (`usb/cdc.py`), so it fires again on the next enumeration - which is what
  makes an awaitable host-side reset possible at all. A guest-triggered reset has nobody waiting
  and must stay fire-and-forget; the difference belongs at the entry point, not in the sequence.
- **It must take `MicroPythonDevice._repl_lock`** when driven from the host, the same lock
  `_aconnect()` and `_aexec()` take, so a reset cannot interleave with an exec in flight. A
  guest-triggered one cannot take it - it runs from inside a register write - which is a real
  asymmetry to design for rather than paper over.
- **The post-boot handshake has to be re-done, and today it cannot be.** The
  CircuitPython-vs-MicroPython difference (`Ctrl-C` vs `\r\n`) lives in `cli/__init__.py`'s
  `_micropython_async()` as a bare `if not args.circuitpython:` *after* `await device.astart()`.
  A device-level reset cannot reach into the CLI for it. **This makes [0087]'s item 4 (move that
  handshake onto the device/family) a prerequisite for the host-initiated hard reset** - it is
  optional only for paths where USB never drops.

### Soft reset

Firmware-side and already working over the wire - `docs/reference/mpremote.md` lists
`mpremote connect ... soft-reset` / Ctrl-D at the raw-REPL prompt as handled entirely by the
firmware's own code, "no emulator-side reset needed", and
`tests/test_mpremote_integration.py`'s fake device models that exact byte
(`enter_raw_repl(soft_reset=True)`: a bare Ctrl-D before any raw-paste probe, answered with
`soft reboot` and a fresh banner).

What is missing is only a way to *ask for it* from the device API. `RawReplRunner` sends
Ctrl-C, Ctrl-C, Ctrl-A on start, and its `CTRL_D` is only ever the terminator appended to source
or the end-of-output marker in the reply (`device/raw_repl.py`) - a bare Ctrl-D at the prompt is
not expressible. So: a small runner, or a flag on the existing one, that enters raw REPL, sends
Ctrl-D, and waits for `soft reboot` + banner. No chip reset, no re-enumeration, no handshake
problem, because USB never drops.

### The RUN pin / RESET button ([0057])

A first-class trigger, not a follow-on. Electrically the RESET button pulls **RUN** low, which is a
chip reset - so it maps onto the hard-reset owner above, and must not grow its own sequence.

Two things have to be solved for it, and only one of them is the reset itself.

**Placement - and there is already a precedent for it in this file's own subject.** 0057's blocker
is that an `ExternalDevice` gets `attach(rp2040)` and cannot reach a `BaseDevice`, while the
sequence cannot move onto `RP2040` because `USBCDC` is constructed by `BaseDevice` and the
reference only points one way (`USBCDC.usb` -> `usb_ctrl`). But that is exactly the situation the
watchdog is already in, and it is solved by a hook installed *downward*: `BaseDevice.__init__`
does `self.mcu.watchdog.on_watchdog_trigger = self._on_watchdog_trigger`, so an MCU-owned object
calls a device-owned implementation without holding a device reference. A RUN-pin model wants the
same shape - something reachable from `rp2040` that `BaseDevice` installs its hard reset into - and
then a RESET button needs no new plumbing concept at all. Worth confirming against [0057]'s own
options before committing; the point here is that the pattern exists and the reset design should
not close it off.

**Level, not edge.** RUN held low holds the chip in reset; it restarts when released. A button is
`press()`/`release()`, so whatever the hook looks like has to express "held" rather than only
"pulse" - otherwise a held RESET button silently behaves like a tap. This is a property of the
trigger, not of the reset, and it is the reason a RESET button cannot just call a fire-and-forget
`reset()` and be done.

**What it inherits, and why that is worth stating.** `RP2040.reset()` resets `core`, `pwm`, `dma`
and `ppb`, and (with `preserve_flash=True`) leaves flash alone. It does **not** clear SRAM, and it
does not reset the other peripherals - `spi`, `i2c`, `pio`, `clocks`, `timer`, `adc`, `uart` - the
way a real RUN-pin reset does. That approximation is invisible for `machine.reset()`, because
firmware re-initialises what it uses on the way up. A RESET button is the trigger most likely to
expose it, since a user presses it precisely to get a clean chip. Not a reason to block this - a
reason for 0057 to decide deliberately whether it needs a fuller `RP2040.reset()` rather than
discovering the gap later.

### Both, over CDC

The CDC path should be able to ask for either, and both are just bytes on the same link:

- soft - the bare Ctrl-D above;
- hard - the guest doing it to itself, by `exec`ing `machine.reset()` (MicroPython) or its
  CircuitPython equivalent, which lands back on the watchdog TRIGGER bit and therefore on the same
  hard-reset owner. This is already what mpremote's `reset` does and is documented as working.

The point of routing it this way is that "reset over CDC" needs **no** new emulator mechanism -
it needs the two owners above to exist, and the CDC side to pick one.

## Consequences for the records this touches

- **[0087]**: its restart step becomes "call the soft-reset entry point", with the hard reset as
  the fallback if a soft reboot turns out not to re-run `code.py`.
- **[0057]**: covered as a trigger above rather than as a consequence. The *sequencing* half of its
  blocker is answered by a real hard-reset method including the `cdc.reset()` step; the *placement*
  half has a proposed shape (the watchdog's own downward-hook precedent) that this design must not
  close off, and its "does a RESET button need a fuller `RP2040.reset()`" question is now written
  down rather than left to be discovered.
- **[0088]**: a 1200-bps-touch reset, if it were ever implemented, is a fifth trigger and should
  land on the same hard-reset owner. That record's own finding is that rp2 firmware defines no such
  handler, so this is a note about shape, not a request to build it.

## Not decided here

- Whether the hard reset is `BaseDevice.reset()`, an async `areset()`, or both (the codebase's
  established pattern is a `*_async()`/`a*()` pair plus a sync facade - `start_async()`/`astart()`,
  `exec_async()`/`aexec()`).
- Exactly what a RUN-pin model hangs off, and whether a held RESET button needs the chip *kept* in
  reset rather than pulsed - [0057]'s questions, to be answered with this design rather than after
  it.
- Whether `RP2040.reset()` should grow to reset the peripherals and SRAM it currently leaves alone,
  or whether a second, fuller entry point is right - see the RUN pin section.
- **Unverified, needed before building on the soft path**: whether CircuitPython's soft reboot
  re-runs `code.py` the way MicroPython's re-runs `main.py`. There is no local CircuitPython
  checkout here, so per the 3g rule it has to be read from real source, not assumed.

[0087]: 0087-circuitpython-writable-circuitpy-over-the-raw-repl.md
[0057]: 0057-run-pin-reset-hook.md
[0088]: 0088-usb-host-side-msc-control-lines-and-reset.md
