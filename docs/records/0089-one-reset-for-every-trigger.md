# 0089 - One reset, every trigger: soft vs hard, guest-initiated vs host-initiated

- Status: **Done and closed (2026-08-20)** - every phase landed except Phase 3, which was built,
  live-verified and then dropped on the maintainer's call (a soft reset needs no API of its own).
  Each phase's own "Known gaps" were then worked back through: the last unreset register blocks,
  the device staying enumerated while RUN is held, and the TIMER not restarting are all closed (see
  the Progress log's two follow-up sections, one of which found a real bug - Phase 5's WDSEL model
  was never actually reached from the device layer). What remains are **decisions, not defects**:
  `xosc`/`rosc` are deliberately not reset, the core is deliberately reset on every path, and a
  button-driven reset deliberately has no awaitable form. CircuitPython 8.0.2's failure to
  re-enumerate is tracked separately in [0093](0093-circuitpython-802-warm-boot-hang.md). Design settled and the
  phased plan below written first - see the "Progress log" at the very end for what has
  actually shipped, per phase.
  Asked for while working through
  [0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md)'s restart step: a reset that
  behaves the same no matter who asks for it. The first half of this record is the design as
  originally written; **"Resolution (2026-08-20)" below is the current state** - it answers the
  "Not decided here" list, lays out a six-phase implementation plan, and carries an appendix of
  live measurements against real MicroPython v1.23.0 and CircuitPython 10.2.1 firmware. Mass
  storage (USB MSC / the bootrom's UF2 mode) is **rejected, not deferred** - the CLI and device API
  already cover what it would be for; see section 5 there.
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


---

# Resolution (2026-08-20): the phased plan, and every open question answered

Everything above is the design as first written. This half turns it into an implementation plan,
answers the "Not decided here" list rather than leaving it open, and folds in the three records
whose own reset sections were pointing here - [0087] (a restart after writing CIRCUITPY),
[0088] (the USB host side), [0057] (the RESET button / RUN pin) - plus
[0066](0066-board-support-expansion.md), whose board-expansion work keeps producing boards whose
RESET button is written down as "not modelled" (`boards/vcc_gnd_yd_rp2040/__init__.py`,
`boards/waveshare_rp2040_lcd_0_96/__init__.py`, `boards/weactstudio/__init__.py`).

**Still nothing implemented.** This section is a plan and a set of decisions, per this repo's
"document vs. implement" rule; the code changes it describes need their own go-ahead. What *was*
done for it is verification: every claim below is either read out of real upstream firmware source
at the exact version this project runs, or measured against real firmware booted in this emulator -
see "Appendix: live verification" at the end, which is where the two questions that used to block
[0087] are answered.

## 1. The bar: "exactly like a real controller"

The framing that makes this tractable is that a reset is not one event with a switch on it. It is
**two events** (a firmware VM restart and a chip restart), reachable from **five triggers**, and
each combination has an observable signature on real hardware that firmware already exposes to
user code. "Works like a real controller" means those signatures match - not merely that the board
comes back up.

### 1.1 The two levels, restated with what firmware sees

| | **soft reset** (VM) | **hard reset** (chip) |
|---|---|---|
| implemented by | the firmware itself | the emulator |
| what restarts | the MicroPython/CircuitPython VM | the RP2040 |
| flash | untouched | preserved (`preserve_flash=True`) |
| RAM | VM heap re-initialised; SRAM array not cleared | same (see 4.5 - SRAM is not cleared on hardware either) |
| USB | stays enumerated | drops, must re-enumerate |
| `machine.reset_cause()` / `microcontroller.cpu.reset_reason` | **unchanged** - no reset happened at chip level | changes, per 1.3 |
| host's REPL connection | survives | invalid until re-enumeration |

### 1.2 The five triggers, and the level each one is

| # | trigger | initiated by | reaches the emulator as | level |
|---|---|---|---|---|
| 1 | `machine.reset()` / `microcontroller.reset()` (and mpremote `reset`/`bootloader`) | guest | a write to `WATCHDOG.CTRL.TRIGGER` | hard |
| 2 | bare Ctrl-D at a REPL prompt (mpremote `soft-reset`) | host, over CDC | nothing - firmware-internal | soft |
| 3 | a host API call (`Device.a*reset()`) | host | **does not exist yet** | either, caller picks |
| 4 | RESET button / RUN pin | an `ExternalDevice` | **does not exist yet** ([0057]) | hard |
| 5 | 1200-bps touch over CDC | host, over CDC | **out of scope** - no firmware here honours it ([0088]) | hard, if it ever exists |

Trigger 1 is the only one that is *already* correct end to end, and it is correct because it goes
through real firmware code: `machine.reset()` on rp2 is
`ports/rp2/modmachine.c`'s `mp_machine_reset()` -> `watchdog_reboot(0, SRAM_END, 0)`, and
CircuitPython's `microcontroller.reset()` is `ports/raspberrypi/supervisor/port.c`'s `reset_cpu()`
-> the *same* `watchdog_reboot(0, SRAM_END, 0)` (both read at the versions this project runs;
CircuitPython additionally calls `filesystem_flush()` first, in
`ports/raspberrypi/common-hal/microcontroller/__init__.c`'s `common_hal_mcu_reset()`). With
`delay_ms == 0`, pico-sdk's `_watchdog_enable()` takes the branch
`hw_set_bits(&watchdog_hw->ctrl, WATCHDOG_CTRL_TRIGGER_BITS)` - so the TRIGGER bit this emulator
already handles is literally the path both firmwares take, not an approximation of it.

### 1.3 Reset cause: the signature each trigger has to leave

This is the part "one reset for every trigger" was missing, and it is the sharpest definition of
"exactly like a real controller" available, because both firmwares expose it to user code.

Read from source (pico-sdk 2.1.1 `hardware_watchdog/watchdog.c`,
`hardware/regs/vreg_and_chip_reset.h`; CircuitPython 10.2.1
`ports/raspberrypi/common-hal/microcontroller/Processor.c`; MicroPython v1.23.0
`ports/rp2/modmachine.c`):

- `watchdog_caused_reboot()` is just `watchdog_hw->reason != 0`; `watchdog_enable_caused_reboot()`
  additionally requires `scratch[4] == 0x6ab73121`, the magic only `watchdog_enable()` writes (a
  `watchdog_reboot()` writes `0` there instead). That is how CircuitPython tells a *deliberate
  reboot* (`SOFTWARE`) from a *watchdog timeout* (`WATCHDOG`) - both set `REASON`.
- CircuitPython checks `CHIP_RESET` first and the watchdog last, with the comment
  "*Check watchdog after chip reset since watchdog doesn't clear chip_reset, while chip_reset
  clears the watchdog*". So: a RUN-pin reset **clears `WATCHDOG.REASON`**; a watchdog reset
  **leaves `CHIP_RESET` alone**.
- `CHIP_RESET.HAD_RUN` (bit 16, `RO`) is documented as "Last reset was from the RUN pin",
  `HAD_POR` (bit 8, `RO`) as "power-on reset or brown-out".

Which gives the table every trigger has to satisfy, and which the tests in each phase assert:

| after... | `WATCHDOG.REASON` | `WATCHDOG.SCRATCH[4]` | `CHIP_RESET` | `machine.reset_cause()` | `microcontroller.cpu.reset_reason` |
|---|---|---|---|---|---|
| power-on (fresh `BaseDevice`) | `0` | `0` | `HAD_POR` | `1` (`PWRON`) | `POWER_ON` |
| `machine.reset()`/`microcontroller.reset()` | `FORCE` | `0` | unchanged | `3` (`WDT`) | `SOFTWARE` |
| a watchdog *timeout* (`WDT.feed()` missed) | `TIMER` | `0x6ab73121` | unchanged | `3` (`WDT`) | `WATCHDOG` |
| RUN pin / RESET button | **`0`** | **`0`** | **`HAD_RUN`** | `1` (`PWRON`) | `RESET_PIN` |
| host API hard reset | as the RUN pin (see D4) | | | | |

Today the emulator gets the first three rows right *by accident of omission* - `RP2040.reset()`
does not touch `watchdog` or `vreg_and_chip_reset`, so `REASON` survives exactly as hardware
survives it, and `CHIP_RESET` stays `HAD_POR` forever. Rows 4 and 5 are what needs new code: a
RUN-pin reset that leaves `REASON` set would make firmware **misreport why it rebooted**, which is
[0057]'s own "what does `WATCHDOG.REASON` read after a RUN reset?" question, now with a full
answer instead of a warning.

## 2. One owner per level, and what each trigger calls

### 2.1 Hard reset - `BaseDevice`

```
BaseDevice.hard_reset(*, cause: ResetCause = ResetCause.RUN_PIN) -> None
```

Synchronous, fire-and-forget, safe to call from inside a register write. The body is today's
`_on_watchdog_trigger()` (`mcu.reset(preserve_flash=True)` / `core.pc = FLASH_START_ADDRESS` /
`cdc.reset()`) plus the `cause` bookkeeping from 1.3. Callers:

- `_on_watchdog_trigger()` -> `hard_reset(cause=ResetCause.WATCHDOG)` (leaves `REASON` as the
  guest's own write set it);
- the RUN-pin hook -> `hard_reset(cause=ResetCause.RUN_PIN)`;
- the awaitable host-side wrapper below.

```
BaseDevice.hard_reset_async(timeout) -> Future[None]      # and: async ahard_reset(timeout)
```

The host-initiated form: takes `MicroPythonDevice._repl_lock`, calls `hard_reset()`, awaits
re-enumeration (the waiting half of `_aconnect()`), then re-runs the family's post-boot handshake.
Only this form can await anything - a guest-triggered reset has nobody waiting and must stay
fire-and-forget, which is why the split is at the entry point and not inside the sequence.

### 2.2 Soft reset - `MicroPythonDevice` (**dropped 2026-08-20 - see Phase 3**)

```
MicroPythonDevice.soft_reset_async(*, rerun_startup_scripts=False, timeout) -> Future[None]
async MicroPythonDevice.asoft_reset(*, rerun_startup_scripts=False, timeout) -> None
```

On `MicroPythonDevice`, not `BaseDevice`: a soft reset is a *property of a Python REPL firmware*,
and `KalumaDevice` shares no such protocol. No chip reset, no re-enumeration, no handshake problem,
because USB never drops.

`rerun_startup_scripts` is not a convenience flag - it is the difference between the two things
real firmware does, and the Appendix measures both (see D2 for why the default is `False`).

### 2.3 The trigger x level matrix, once the two owners exist

| trigger | soft | hard |
|---|---|---|
| from the interpreter (guest code) | `raise SystemExit` / Ctrl-D semantics - firmware's own, nothing to build | `machine.reset()` -> TRIGGER bit -> `hard_reset(WATCHDOG)` (**works today**) |
| from mpremote / CDC bytes | `soft-reset` = bare Ctrl-D -> firmware's own (**works today**) | mpremote `reset` = `exec machine.reset()` -> same path as above (**works today**) |
| from the host API | `asoft_reset()` (**Phase 3**) | `ahard_reset()` (**Phase 2**) |
| from an `ExternalDevice` (RESET button) | n/a - RUN is not a VM concept | `hard_reset(RUN_PIN)` via the RUN pin (**Phase 4**) |

The point worth keeping: **"reset over CDC" needs no new emulator mechanism at all.** Both cells in
its row are bytes on a link that already works. What is missing is only a way to *ask* for them
from the device API, and one owner behind each.

## 3. Decisions (answering "Not decided here")

**D1 - `reset()`, `areset()`, or both: both, split by level, following the house pattern.**
`hard_reset()` (sync, no waiting) + `hard_reset_async()`/`ahard_reset()` for the host-side one, and
`soft_reset_async()`/`asoft_reset()` for the soft one - matching `start_async()`/`astart()` and
`exec_async()`/`aexec()`. No blocking `reset()` facade, for the same reason `base_device.py`'s
module docstring gives for having no blocking `start()`.

**D2 - the soft reset needs a mode, and the default is mpremote's.** (The *finding* stands and is
what Phase 6.1 documents; the API it was deciding for was dropped - see Phase 3.) Measured (Appendix, point 1) and
confirmed in source: a bare Ctrl-D at the **raw** prompt restarts the VM but does
**not** re-run `main.py`/`code.py`, in *both* families; only a Ctrl-D at the **friendly** prompt
does. So `asoft_reset()` defaults to `rerun_startup_scripts=False` - what every existing tool means
by "soft reset", and what `mpremote soft-reset` does - and [0087]'s flow passes `True`.

**D3 - what a RUN-pin model hangs off: a level on `RP2040`, with `BaseDevice` installing the
implementation downward.** `RP2040` grows a RUN level (`run_pin_low` + a hook), and
`BaseDevice.__init__` points that hook at its own `hard_reset(cause=RUN_PIN)` - the exact shape it
already uses for `self.mcu.watchdog.on_watchdog_trigger = self._on_watchdog_trigger`, so no new
plumbing *concept* appears, and an `ExternalDevice` that only has `attach(rp2040)` can still reach
the full sequence. This picks [0057]'s option A for *placement* and its option C for *semantics*,
which is what "exactly like a real controller" forces: 0057's own addendum has the schematic
(`3V3 -[R12 10k]- RUN`, switch to GND, no series resistor), so a press is a **level**, not a pulse.
A held RESET button holds the chip in reset and the release is what boots it (Phase 4).

**D4 - a host API hard reset behaves as the RUN pin.** `ResetCause.RUN_PIN` is the default for
`hard_reset()`/`ahard_reset()` because the physical thing a host-side "reset the board" corresponds
to is someone pressing RESET, and because the alternative (leaving `REASON` at whatever the last
watchdog write left) makes the guest misreport its own boot reason - measured, see Appendix, point 3:
today's sequence leaves the registers byte-identical to a power-on (`REASON=0`, `SCRATCH=[0]*8`,
`CHIP_RESET=HAD_POR`), so a fresh boot reports `PWRON`/`POWER_ON` and a session where the guest
already called `machine.reset()` reports `WDT` - i.e. it reports *stale history*, not this reset,
and never the `RESET_PIN` a real board's RESET button would.

**D5 - `RP2040.reset()` grows, but in a later phase and by domain, not all at once.** Today it
resets `core`/`pwm`/`dma`/`ppb` only. A real reset covers far more, and the SDK says exactly how
much: `_watchdog_enable()` writes `psm_hw->wdsel = PSM_WDSEL_BITS & ~(ROSC | XOSC)` before
triggering - "reset everything apart from ROSC and XOSC", including the `RESETS` block itself,
which in turn holds every peripheral in reset until firmware releases it. So the target is
"everything except the two oscillators", the emulator already *stores* both `PSM.WDSEL` and
`RESETS.WDSEL` without acting on them (`peripherals/psm.py`, `peripherals/reset.py`), and the
faithful implementation is to honour what the guest itself selected. Phase 5, last, because it is
the change most likely to break a live boot and the least likely to be missed until then.

**D6 - SRAM is not cleared, and that is correct.** A PSM reset resets the SRAM *controllers*, not
the array; contents are undefined-but-typically-preserved on hardware, and firmware re-initialises
everything it relies on. Keep `preserve_flash=True`'s current behaviour of leaving `sram` alone,
and stop listing it as a fidelity gap.

**D7 - attached `ExternalDevice`s get no reset callback, because a real board's don't.** An
external chip is not wired to RUN; it sees a reset only through whatever GPIO the firmware drives
(the CYW43439's `WL_ON`, a display's `RES` line). The right fidelity fix is therefore **not** a new
protocol method on `ExternalDevice` - it is Phase 5 resetting the pads/IO to their reset state, so
those lines drop the way they do on hardware and firmware's own re-init sequence is what brings the
device back. This closes the question 0057 raised and 0049 tracks, without widening
`ExternalDevice`'s attach-only surface.

One implication to cost into Phase 5 rather than discover in it: `external/cyw43/` models no power
pin at all (nothing in it mentions `WL_ON`), so resetting the pads is necessary but not sufficient
- the CYW43439 model also has to *react* to that line dropping by returning to its power-on state.
The Appendix (point 5) measures what happens without it: CircuitPython on a Pico W cannot bring
WiFi back up after a hard reset.

**D8 - mass storage stays out of scope, deliberately** (maintainer's call, 2026-08-20). See
section 5.

## 4. The phased plan

Each phase is independently shippable and independently verifiable. Phase order is
prerequisite-driven, not importance-driven.

### Phase 0 - prerequisites, no behaviour change - **done (2026-08-20)**

0.1 **Move the post-boot handshake onto the device/family** ([0087]'s item 4, which stops being
"optional, cosmetic" here). `cli/__init__.py`'s `_micropython_async()` currently does
`if not args.circuitpython: cdc.send \r\n else: cdc.send Ctrl-C` *after* `await device.astart()`.
A device-level reset cannot reach into the CLI for it, so any host-initiated hard reset would come
back to a console the CLI has already finished setting up. Move it to a
`MicroPythonDevice._post_boot_handshake()` keyed on `self.circuitpython`, called from `_aconnect()`
and later from the reset path; the CLI keeps working unchanged.

0.2 **Make the hard-reset sequence a method.** `BaseDevice.hard_reset()`, with
`_on_watchdog_trigger()` reduced to a one-line caller. Pure refactor -
`tests/test_watchdog_reset.py` keeps passing as-is, plus one new test calling the method directly.

*Closes:* [0087] item 4. *Unblocks:* Phases 1-4.

### Phase 1 - reset-cause fidelity - **done (2026-08-20)**

1.1 `RPWatchdog` grows a `reset()` (clears `_reason` and `scratch_data`) - what a chip-level reset
does to it, and nothing else does.
1.2 `RPVREGAndChipReset` grows a way to record the cause (`HAD_POR` / `HAD_RUN` / `HAD_PSM_RESTART`
exclusive of each other, `PSM_RESTART_FLAG` untouched).
1.3 `ResetCause` enum + `BaseDevice.hard_reset(cause=...)` applying 1.3's table.

*Tests:* unit tests over the register values per cause (no firmware needed, the
`tests/test_watchdog_reset.py` pattern); live-boot assertions that `machine.reset_cause()` reads
`1`/`3` and `microcontroller.cpu.reset_reason` reads `POWER_ON`/`SOFTWARE` in the right places -
both already measured working in the Appendix, so these are regression tests, not exploration.

*Closes:* [0057]'s "what does `WATCHDOG.REASON` read after a RUN reset?".

### Phase 2 - the host-initiated hard reset - **done (2026-08-20)**

2.1 `BaseDevice.hard_reset_async()`/`ahard_reset()` per 2.1: `_repl_lock`, `hard_reset()`,
await re-enumeration, re-run 0.1's handshake.
2.2 Factor `_aconnect()`'s waiting half so the reset path and the boot path share it rather than
growing a second copy (`MicroPythonDevice._aconnect()` is already an override of
`BaseDevice._aconnect()` differing only by the lock).
2.3 Note in passing: `_started` stays `True` across a reset - a reset is explicitly *not* a second
`start()`, and `start_async()` must keep raising if called again.

*Tests:* live-boot, both firmwares - reset, re-enumerate, `aexec()` still works, and a variable set
before the reset is gone. The Appendix already ran exactly this against today's private sequence
(point 5), so the expected results are known - including the one trap: assert on state, not on
boot output (point 4).

*Closes:* [0087]'s hard-reset fallback; [0088]'s "host-driven board reset" row.

### Phase 3 - the host-initiated soft reset - **dropped (maintainer, 2026-08-20)**

**Not wanted as an API** (maintainer's call): *"мені не треба окремий soft reset в API - мені аби
треба правильно працював `machine.reset()`"*. The plan below is kept as written, because its
*findings* (D2, and the transcripts in the Appendix) are what `docs/reference/mpremote.md` documents
in Phase 6.1 - but no `SoftResetRunner`, no `asoft_reset()`, and nothing on the device API.

It was built and live-verified before being dropped, which is worth recording so the decision is
read as a scope call rather than a failure: a `SoftResetRunner` beside `RawReplRunner` drove both
prompts on MicroPython v1.23.0 and CircuitPython 10.2.1, demonstrating D2 end to end (a raw-prompt
Ctrl-D restarts the VM without re-running `main.py`; a friendly-prompt one re-runs it), with the
chip's reset cause unchanged across it. All of it was removed again.

**The capability itself never went anywhere, which is why dropping it costs nothing** - the
maintainer's own argument: *"ми можемо це прямо через exec/mpremote зробити"*. A soft reset is bytes
the firmware already answers, from paths that all already exist: a user pressing Ctrl-D in the
console, `mpremote connect ... soft-reset` (documented as working, no emulator-side reset involved),
or simply asking the guest to do it to itself over the raw REPL we already have.

That last one was measured 2026-08-20, because "just exec it" had an obvious way to go wrong - the
VM restarts *mid-exec*, so the call could plausibly never see its own terminator - and it does not:

```python
await device.aexec("import machine\nmachine.soft_reset()")  # MicroPython v1.23.0
await device.aexec("import supervisor\nsupervisor.reload()")  # CircuitPython 10.2.1
```

Both **return cleanly** (`stdout=b''`, `stderr=b''`), and the next `aexec()` on the same console
reports `NameError` for a global set before the call - i.e. the VM really did restart, the raw REPL
came back, and the runner's own Ctrl-C/Ctrl-A on the following exec resynchronises it. No
emulator-side support, no wrapper, no waiting logic. Which is precisely why a `SoftResetRunner`
would have been a second way to do something the device API could already express in one line.

*Consequence for [0087]:* its restart step goes back to those paths, or to `ahard_reset()` (Phase 2,
built) - it no longer waits on anything here.

*Original plan, unbuilt:*

3.1 A `SoftResetRunner` beside `RawReplRunner` (or a flag on it - the runner is 130 lines and its
state machine is `await_prompt`/`await_ok`/`stdout`/`stderr`, none of which fits "send Ctrl-D,
expect a reboot banner", so a sibling is probably cleaner).
3.2 `rerun_startup_scripts=False`: Ctrl-C, Ctrl-C, Ctrl-A, wait for the raw banner, Ctrl-D, expect
`OK\r\n`, then `soft reboot`, then the raw banner again. Ends parked at the raw prompt.
3.3 `rerun_startup_scripts=True`: additionally Ctrl-B first (raw -> friendly), then Ctrl-D at the
friendly prompt; expect `soft reboot`, then the startup script's own output, then the friendly
banner.
3.4 Match on the substring `soft reboot`, not the whole line: MicroPython prints
`MPY: soft reboot`, CircuitPython prints `soft reboot`.

*Tests:* a fake-device unit test in the shape `tests/test_mpremote_integration.py` already uses,
plus live-boot on both firmwares (both transcripts are in the Appendix, byte for byte).

*Closes:* [0087]'s "the device API has no way to send that byte".

### Phase 4 - RESET button / RUN pin - **done (2026-08-20)**

4.1 **`RP2040` grows the RUN level** - in all three twins (`_rp2040.py`, `native/_rp2040.pyx`,
`native/_rp2040.pyi`), per [0057]'s "Cost of touching `RP2040` at all".
4.2 **The batch loop learns "held in reset"**: `execute_batch()` (both `_execute_batch.py` and the
native port) skips instruction execution while RUN is low. This is the new execution state 0057
option C priced - distinct from `core.waiting` (which still services alarms and is cleared by an
IRQ) and from `simulator.stopped` (which ends the engine room). It is the whole reason a held
RESET button is not the same as a tap.
4.3 **`BaseDevice.__init__` installs the hook downward** (D3), next to the watchdog line it already
has.
4.4 **`external/reset_button.py`**, `press()`/`release()`/`click()`, going through
`rp2040.schedule_threadsafe()` - 0030's rule, and unlike `BootselButton` this one is pressed
*while running* by definition.
4.5 **Boards**: attach it where the omission is currently written down -
`vcc_gnd_yd_rp2040`, `waveshare_rp2040_lcd_0_96`, `weactstudio` - and update those three "Not
modelled" notes. New boards from [0066]'s remaining checklists then get a RESET button for free.
Follow `.claude/skills/external-devices-and-boards/`'s checklist for the device and the board edits.

*Tests:* per 0057's own testing plan (hook fires once per release, reaches the MCU on the engine
room, parity between both `RP2040` twins), plus a live-boot integration test: set `x = 1`, press and
release RESET, confirm re-enumeration and that `x` is gone; and a held-RESET test that nothing
executes while it is held.

*Closes:* [0057] in full.

### Phase 5 - the fuller chip reset - **done (2026-08-20)**

5.1 Extend `RP2040.reset()` to the blocks a real reset covers, honouring `PSM.WDSEL`/`RESETS.WDSEL`
where the guest set them (D5): `io`/`pads`/`sio` (so GPIO returns to inputs - D7's fidelity fix),
`uart`, `spi`, `i2c`, `pio`, `timer`, `adc`, `clocks`, leaving `xosc`/`rosc` alone on the watchdog
path.
5.2 SRAM stays untouched (D6).

*Tests:* the live-boot bar, both firmwares, after **each** trigger. Two observables are already
measured and failing today (Appendix, point 5), so this phase has its regression tests written for
it in advance: an LED left on across `machine.reset()` must go dark (the pad currently keeps
`FUNCSEL=0x5, OE=1, OUT=1` where a power-on pad reads `FUNCSEL=0x1f, OE=0, IE=0`), and
**CircuitPython on a Pico W must be able to bring the CYW43439 back up after a hard reset** - it
currently cannot, filling the console with the firmware's own `[CYW43] Failed to start CYW43`,
because nothing resets the pads carrying `WL_ON` and the chip therefore never sees a power cycle.

*Risk note:* this phase is the reason the plan is phased at all. Phases 1-4 are additive; this one
changes what an existing, working reset does. It is also the phase that makes D7 true rather than
merely reasonable - until the pads reset, "the firmware re-drives its external devices on the way
up" is a claim the CYW43 measurement contradicts.

### Phase 6 - documentation - **done (2026-08-20)**

6.1 `docs/reference/mpremote.md`: the `reset`/`soft-reset` rows gain the raw-vs-friendly
distinction (D2) - today they say "handled entirely by firmware's own soft-reset code" without
saying that this means `main.py` does *not* re-run.
6.2 `docs/reference/external-devices-and-boards.md` + the skill: `ResetButton` as a worked example.
6.3 This record's status, and the tracker rows for [0057]/[0087]/[0088].

## 5. Rejected, not merely deferred: mass storage (maintainer's decision, 2026-08-20)

**No MSC, no USB-device emulation, no bootrom UF2 mode.** It needs a mass-storage class
implementation *and* an emulated USB device controller, and it is high-complexity - but the
deciding argument is not cost, it is that **the use cases MSC exists for are already covered here,
by paths that are better for an emulator anyway** (maintainer, 2026-08-20):

- **Replacing the firmware**: `--image`/`resolve_board_spec()` load a UF2 directly into emulated
  flash. On real hardware you drag a `.uf2` onto RPI-RP2 because that is the only channel a bare
  chip offers; here the channel is the constructor argument, and no bootloader has to be entered
  to use it.
- **Replacing the bootrom**: `--bootrom` / `bootrom_words=`, which MSC could not do at all.
- **Putting files on the device's filesystem**: `mklittlefs`/`mkfat12` build an image up front, and
  [0087] showed the guest can *write its own* CIRCUITPY over the raw REPL - a route that exists
  precisely because nothing here claims the MSC interface.
- **Driving the board like a host tool would**: `mpremote` works against this emulator over the
  raw REPL (`docs/reference/mpremote.md`), including `exec`/`run`/`fs` for MicroPython - no
  mass-storage or "UF2 mode" step involved.

So MSC would add a second, harder way to do things the CLI and the device API already do. Treat it
as decided rather than pending; a future need would have to argue from a case none of the above
covers. Three consequences, so nothing in this plan silently assumes otherwise:

- **`machine.bootloader()` keeps its documented approximation** - a plain hard reset rather than
  entering the bootrom's USB mass-storage mode ([0088], `docs/reference/mpremote.md`).
- **Trigger 5 (the 1200-bps touch) is not built.** [0088]'s finding stands: `ports/rp2` defines
  neither `MICROPY_HW_USB_CDC_1200BPS_TOUCH` nor `MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER`, and
  CircuitPython has no `tud_cdc_line_state_cb` on that path, so there is nothing here to trigger
  even if the host half existed. If it is ever built it is a *caller* of `hard_reset()`, not a
  sixth behaviour.
- **BOOTSEL held across a RESET stays unreachable in effect.** `BootselButton` can hold
  `GPIO_QSPI_SS` low and Phase 4 makes the reset real, so the *combination* becomes expressible -
  but with no bootrom USB mode to enter, the emulator will keep jumping to `FLASH_START_ADDRESS`.
  Phase 4's hook contract must not make the other behaviour impossible later (0057's own
  requirement); it does not need to implement it.

This also keeps [0087]'s writable-CIRCUITPY route intact by construction: that route works
*because* nothing claims the MSC interface, and nothing here changes that.


## Appendix: live verification (2026-08-20)

Run against the two firmware images the maintainer supplied, booted in this emulator on
`--board pico_w`: **MicroPython v1.23.0** (`RPI_PICO_W-20240602-v1.23.0.uf2`) and
**CircuitPython 10.2.1** (`adafruit-circuitpython-raspberry_pi_pico_w-en_US-10.2.1.uf2`). Probe
scripts were scratch-only - nothing in the tree changed - and drove the CDC console byte by byte
(`cdc.send_serial_byte()` + an `on_serial_data` capture), because a bare Ctrl-D at the prompt is
not expressible through `RawReplRunner` today, which is Phase 3's whole point.

### What was confirmed

**1. Both families behave identically at both prompts, and the raw prompt does *not* re-run the
startup script.** The transcripts, byte for byte:

| | MicroPython v1.23.0 | CircuitPython 10.2.1 |
|---|---|---|
| Ctrl-D at the **raw** prompt | `OK\r\nMPY: soft reboot\r\nraw REPL; CTRL-B to exit\r\n>` | `OK\r\nsoft reboot\r\nraw REPL; CTRL-B to exit\r\n>` |
| `main.py`/`code.py` re-run? | **no** | **no** |
| Ctrl-D at the **friendly** prompt | `\r\nMPY: soft reboot\r\nMAIN-RAN-v1\r\n` + banner | `soft reboot` + `code.py output:` + `CODE-RAN-v2` + `Code done running.` |
| `main.py`/`code.py` re-run? | **yes** | **yes** |

Matching the source exactly: both firmwares' `pyexec_raw_repl()` answer an empty line + Ctrl-D with
`OK\r\n` and `PYEXEC_FORCED_EXIT`, and both main loops then re-run the startup script **only** if
`pyexec_mode_kind == PYEXEC_MODE_FRIENDLY_REPL` (`ports/rp2/main.c` for MicroPython, `main.c`'s
`for(;;)` around `run_repl()`/`run_code_py()` for CircuitPython). Ctrl-D at the raw prompt leaves
that mode set to RAW, so the firmware restarts its VM and comes straight back to a raw prompt.

The MicroPython half was re-verified **without relying on console output at all**, since output can
be lost (point 4): `main.py` incremented a counter in its own littlefs, and the counter was read
back over the REPL afterwards. Cold boot -> `1`; after a raw-prompt soft reset -> still `1`; after
a friendly-prompt soft reset -> `2`.

*Consequence:* this is why D2 exists. [0087]'s flow (write `code.py` over the REPL, then restart so
it runs) needs the **friendly-prompt** variant - `asoft_reset()`'s default alone would not have
re-run anything, and the failure would have looked like "the write didn't take".

**2. A REPL-written `code.py` does not auto-reload** (CircuitPython). Rewriting `/code.py` through
`storage.remount('/', readonly=False)` + `open()` from the raw REPL, then idling 10 s with the
console captured, produced **zero** output - no reload, no re-run. Source agrees, twice over:
`autoreload_trigger()` is called from the USB **mass-storage** write-completion path
(`supervisor/shared/usb/usb_msc_flash.c`), which this emulator never drives, and `run_repl()`
calls `autoreload_suspend(AUTORELOAD_SUSPEND_REPL)` for the whole REPL session anyway.

*Consequence:* [0087]'s two "unverified, and blocking" questions are both answered - the restart
has to be asked for explicitly, and it is a soft reset at the friendly prompt.

**3. The reset-cause table in section 1.3 is real, and today's emulator already gets three rows
right.** Measured:

| measured | MicroPython `machine.reset_cause()` | CircuitPython `microcontroller.cpu.reset_reason` |
|---|---|---|
| cold boot | `1` (PWRON) | `microcontroller.ResetReason.POWER_ON` |
| after the guest's own `machine.reset()` / `microcontroller.reset()` | `3` (WDT) | `microcontroller.ResetReason.SOFTWARE` |
| after today's host-side sequence, from a fresh boot | `1` (PWRON) | `POWER_ON` (derived: the register dump below is what the firmware reads, and it is byte-identical to a cold boot) |

The register dump right after today's host-side sequence: `WATCHDOG.REASON=0`,
`WATCHDOG.SCRATCH=[0]*8`, `CHIP_RESET=0x100` (`HAD_POR`) - i.e. **indistinguishable from a
power-on**, and *not* what a RESET button leaves (`REASON=0`, `CHIP_RESET=HAD_RUN` ->
`RESET_PIN`). In a session where the guest had already called `machine.reset()`, the same host-side
sequence reports `3` (WDT) instead, because `REASON` is never cleared - it reports stale history.
That is D4's evidence: the host-side reset has to *say* what it is.

**4. Console output produced before the host re-enumerates is lost - on a hard reset, and on a cold
boot.** `main.py`/`code.py` output printed during the boot that follows a hard reset never reached
the capture, while everything after the console came up did. This is not an emulator defect: the
firmware only flushes CDC once DTR is asserted (`shared/tinyusb/mp_usbd_cdc.c`'s
`tud_cdc_line_state_cb()` arming `cdc_connected_flush_delay`; TinyUSB's `tud_cdc_n_connected()`
being literally the DTR bit), which is exactly what a real board does to a terminal that is
re-opening the port. CircuitPython's *later* boot chatter did arrive
(`Auto-reload is on...` / `Press any key to enter the REPL. Use CTRL-D to reload.`).

*Consequence for Phase 2:* `ahard_reset()` must promise "re-enumerated, console usable", **not**
"you will see the boot banner". Its tests must assert on VM state (a variable is gone, a
filesystem counter advanced) rather than on boot output - the four `FAIL`s in the raw probe logs
are all this artifact, not behaviour differences.

**5. Today's hard-reset sequence works, and its limits are exactly where section 3 predicted.**
It re-enumerates (0.0-4.5 s wall), the raw REPL works immediately afterwards (`print(1 + 1)` ->
`2`), flash survives, and the firmware really does re-boot (the littlefs boot counter advanced).
Two gaps showed up, both Phase 5's:

- **GPIO pads survive a reset.** A guest drove GPIO15 high (`FUNCSEL=0x5`, `OE=1`, `OUT=1`); after
  the reset sequence - and after the firmware had re-booted - the pad still read
  `FUNCSEL=0x5, OE=1, OUT=1`, where a freshly constructed `RP2040` reads
  `FUNCSEL=0x1f, OE=0, IE=0`. On hardware the pin stops driving; here an LED left on stays on.
- **CircuitPython cannot bring the CYW43439 back up after a hard reset.** The re-boot itself
  completed (status bar, `Auto-reload is on...`, the REPL prompt hint), then the console filled
  with `[CYW43] Failed to start CYW43`, repeatedly. The string is in the CircuitPython image
  itself (confirmed with `strings` on the `.uf2`), so this is the firmware's own re-init failing -
  the emulated chip never sees a power cycle, because nothing resets the pads that carry `WL_ON`
  and the gSPI/PIO state is left mid-flight. Same root cause as the pad finding, and the sharpest
  argument that Phase 5 is not cosmetic: on a Pico W, "reset the board" currently costs you WiFi
  until the process is restarted.

MicroPython does not hit this, because it only brings the CYW43 up when the guest asks
(`network.WLAN(...)`); a Pico W under MicroPython execs fine after the same reset.

### What was not measured

- **CircuitPython's `reset_reason` after today's host-side sequence** was derived, not read: the
  probe was stopped while waiting out one of point 4's unobservable-boot-output timeouts. The
  derivation is direct (the register dump is identical to a cold boot's, and cold boot was measured
  as `POWER_ON`), but it is worth asserting for real in Phase 1's tests.
- **Nothing about the RUN pin**, since none of it exists yet - Phase 4's numbers can only come from
  Phase 4.
- **`machine.bootloader()`**, whose approximation is unchanged and out of scope (section 5).

### Test-writing notes for whoever builds this

Cheap lessons from the probes, all of which cost a run to learn:

- **Drive `machine.reset()` from the friendly REPL, not the raw one.** Bytes sent at a raw prompt
  are buffered as *source* until a Ctrl-D, so `machine.reset()` typed there simply never runs (one
  probe silently measured nothing at all because of this).
- **`aexec()` leaves the device parked in the raw REPL** and sets `cdc.on_serial_data = None` on
  its way out (`BaseReplRunner.stop()`), so a capture has to be re-installed after every `aexec()`.
- **Assert on state, not on console output** - see point 4.
- **`on_device_connected` survives `cdc.reset()`**, which is what makes a re-enumeration wait
  possible; re-arm it with a fresh `asyncio.Event` before each reset.

## Progress log

### Phase 0 - done (2026-08-20)

**0.1 - the post-boot handshake moved onto the device/family.**
`MicroPythonDevice._post_boot_handshake()` (`device/mp_device.py`) sends the prompt nudge, and
`_aconnect()` calls it *after* the enumeration wait. (It sent `\r\n` for MicroPython and Ctrl-C
for CircuitPython when first written - see the correction below, which is why it no longer
branches at all.) `cli/__init__.py`'s `_micropython_async()` lost its own
`if not args.circuitpython:` block - the CLI no longer branches on a firmware flag it otherwise
doesn't care about, and Phase 2's reset path now has a handshake it can re-run without reaching
into the CLI. Also closes [0087]'s item 4.

**Correction (2026-08-20, later the same day): the nudge is a bare newline for *both* families,
not `\r\n` vs Ctrl-C** - measured, decided and written up as its own record,
[0090](0090-post-boot-nudge-is-a-newline.md), since it governs every device connect rather than
anything about reset. The CLI's split was carried over unexamined; Ctrl-C only ever helps on an
*idle* REPL, and both firmwares auto-run a script:

| | MicroPython v1.23.0 | CircuitPython 8.0.2 |
|---|---|---|
| nudge against a running `main.py`/`code.py`, Ctrl-C | `KeyboardInterrupt` in it, drops to the REPL | kills it (`Code done running.`), and does not reach a prompt either |
| same, newline | script keeps printing | script keeps printing |
| nudge with no script running | prompt | banner + `>>>`, byte-identical to Ctrl-C's |

So Ctrl-C bought nothing the newline does not, and cost the running script. Concretely it would
have broken the three `--littlefs ... --expect-text` jobs in `ci-micropython.yml`, *and* it was a
live regression from 0.1 itself for CircuitPython: `demo/lcd_run.py --code` boots a `code.py` and
never used to receive any nudge at all, so moving the CLI's Ctrl-C onto every device connect would
have interrupted it at enumeration. Attaching to a board must not disturb what it is doing; a user
who wants to interrupt can still type Ctrl-C into the console. Both CI commands re-verified live
afterwards (`--expect-text "Adafruit CircuitPython"` on 8.0.2 and 10.2.1, `--expect-text "Hello,
MicroPython!"` with a littlefs image).

One deliberate behaviour change beyond the CLI: the nudge now goes out on *every*
`MicroPythonDevice` connect, including the library/exec-only paths that previously never sent it.
Harmless in both directions - `RawReplRunner.start()` already opens with Ctrl-C, Ctrl-C, Ctrl-A,
so an exec is unaffected by an earlier newline or Ctrl-C - and it is what makes the handshake a
property of the device rather than of one caller.

**0.2 - `BaseDevice.hard_reset()` is now a real method.** Same three lines as before
(`mcu.reset(preserve_flash=True)` / `core.pc = FLASH_START_ADDRESS` / `cdc.reset()`);
`_on_watchdog_trigger()` is reduced to a one-line caller of it. The docstring carries the
"one owner per level" contract, plus the two properties Phase 2 depends on: it stays synchronous
and fire-and-forget (it runs from inside an emulated register write), and `cdc.reset()`
deliberately leaves `on_device_connected` wired so a re-enumeration wait is possible later.

*Tests:* `tests/test_watchdog_reset.py` unchanged and still passing, plus
`test_hard_reset_called_directly_runs_the_same_sequence` calling the method directly;
`tests/test_device.py` gains three handshake tests (per-family bytes, and that `_aconnect()` sends
them only after enumeration). Full `pre-commit run --all-files` green.

*Live-boot verified*, library API only (no CLI), both firmwares booted on `--board pico`:
MicroPython v1.23.0 emits `\r\n>>> ` and CircuitPython 8.0.2 emits its
"Press any key to enter the REPL" banner followed by `>>> ` - i.e. the prompt the CLI used to nudge
out now arrives from `astart()` alone.

*Not touched:* everything in Phases 1-6. No `ResetCause`, no host-side entry point, no RUN pin, and
`RP2040.reset()` still covers only `core`/`pwm`/`dma`/`ppb`.

### Phase 1 - done (2026-08-20)

**1.1 `RPWatchdog.reset()`** clears `_reason` and the eight scratch registers - and is deliberately
*not* called on the watchdog's own path, which is the whole point: on silicon the watchdog block
survives a watchdog reboot, which is why REASON is readable afterwards at all and why
`watchdog_enable()`'s `0x6ab73121` in SCRATCH[4] lives long enough for
`watchdog_enable_caused_reboot()` to tell a timeout from a deliberate reboot. Scoped to the
reset-*cause* state; the block's timer/alarm/tick enables stay Phase 5's business.

**1.2 `RPVREGAndChipReset.record_reset_cause(flag)`** sets exactly one of `HAD_POR`/`HAD_RUN`/
`HAD_PSM_RESTART` (they report the *last* reset, so they are mutually exclusive) and preserves
`PSM_RESTART_FLAG`, which belongs to the bootrom's write-1-to-clear handshake from [0050].

**1.3 `ResetCause`** (`POWER_ON`/`RUN_PIN`/`WATCHDOG`, exported from `rp2040py.device`) plus
`BaseDevice.hard_reset(*, cause=ResetCause.RUN_PIN)` applying §1.3's table:
`_on_watchdog_trigger()` passes `WATCHDOG` (record nothing - the absence *is* the signature),
everything else clears the watchdog and records a chip-level cause. The recording happens *after*
`mcu.reset()`, so Phase 5's wider `RP2040.reset()` cannot later wipe the cause it just set.

One structural change this needed: **`RP2040` now names `vreg_and_chip_reset`** (both twins -
`_rp2040.py` and `native/_rp2040.pyx`), instead of constructing it anonymously inside the
`peripherals` dict, so `hard_reset()` reaches it the same way it already reaches `mcu.watchdog`
rather than looking a peripheral up by base address. No `.pxd`/`.pyi` change needed (the native
class already carries a `__dict__`), but the extension does have to be rebuilt - `uv sync
--reinstall` - since pre-commit's own `uv sync` does not rebuild an editable native package on a
`.pyx` edit.

*Tests:* seven new unit tests in `tests/test_watchdog_reset.py` covering every row of §1.3's table
(fresh device = `HAD_POR`/`REASON=0`; watchdog keeps `FORCE`/`TIMER` + the SCRATCH[4] magic and
leaves `CHIP_RESET` alone; RUN pin clears both and sets `HAD_RUN`; the default is the RUN pin;
`POWER_ON` records `HAD_POR`; `PSM_RESTART_FLAG` survives all of it).

*Live-boot verified,* and now permanent CI steps rather than one-off probes:

- `tests/micropython/main-reset-cause.py` (a new littlefs image in `ci-micropython.yml`, run with
  `--expect-text "RESET CAUSE OK"` across the whole version x runtime matrix) boots, sees
  `PWRON_RESET`, calls `machine.reset()`, and must come back reporting `WDT_RESET`. Measured here
  on MicroPython v1.23.0: `reset_cause() == 3`, with `CHIP_RESET` still `0x100` (`HAD_POR`) and
  `REASON == 0x2` (`FORCE`) - the §1.3 row exactly. It prints in a loop, because `main.py` runs
  before USB enumerates and a single print at boot is gone before any host attaches.
- `tests/circuitpython/main-reset-cause.py` (new step in `ci-circuitpython.yml`, all three tags)
  asserts `microcontroller.cpu.reset_reason is ResetReason.POWER_ON` over the raw REPL. Measured
  here on CircuitPython 8.0.2. Power-on only: a `microcontroller.reset()` would drop USB mid-exec
  with nothing waiting for the re-enumeration until Phase 2.

*Closes:* [0057]'s "what does `WATCHDOG.REASON` read after a RUN reset?" - the registers now behave
as its answer says, ahead of the RUN pin that will pull them (Phase 4).

*Not touched:* Phases 2-6. There is still no host-side entry point, no soft-reset API, no RUN pin,
and `RP2040.reset()` still covers only `core`/`pwm`/`dma`/`ppb`.

### Phase 2 - done (2026-08-20)

**2.1 `BaseDevice.hard_reset_async()`/`ahard_reset()`** (`cause=ResetCause.RUN_PIN` by default, D4):
arm the enumeration Event, `hard_reset()`, wait for the device to come back, re-run the family's
post-boot handshake. `MicroPythonDevice` overrides only the lock - `async with self._repl_lock:
await super()._ahard_reset(...)` - so a host reset queues behind an `aexec()` in flight instead of
interleaving with it, while the guest-triggered path keeps taking no lock at all.

**2.2 `_aconnect()`'s halves are now shared, not copied.** `_arm_enumeration()` (wire a fresh Event
to `cdc.on_device_connected` *before* whatever triggers the boot) and `_await_enumeration()` (wait,
translate the timeout) serve both the cold boot and the reset. `MicroPythonDevice._aconnect()`
shrank to the same lock-wrapper shape (`await super()._aconnect(timeout)`) instead of re-stating
the body, and `_post_boot_handshake()` gained a no-op base so both paths can call it unconditionally
- Kaluma, which deliberately gets no nudge, inherits the no-op.

**2.3** `_started` stays `True` across a reset, and `start_async()` keeps raising - asserted.

*Tests:* six new ones in `tests/test_device.py` (reset before start raises; the wait ends with the
handshake re-sent; the default cause is the RUN pin; a device that never comes back raises
TimeoutError rather than hanging; a reset is not a second start; a reset queues behind whatever
holds the REPL lock), plus the live-boot driver **`tests/hard_reset_run.py`**, now a CI step on
both workflows: reset, re-enumerate, `aexec()` works, a variable set before the reset is gone
(`NameError`), and the firmware reports the right cause.

*Measured, both families, and one of them settles an open question:*

| | MicroPython v1.23.0 | CircuitPython 10.2.1 |
|---|---|---|
| re-enumerates, raw REPL usable | yes | yes (~20 s) |
| guest RAM gone (`marker` -> `NameError`) | yes | yes |
| cause after a host reset | `machine.reset_cause() == PWRON_RESET` | **`microcontroller.ResetReason.RESET_PIN`** |

That last cell is what the Appendix listed under "What was not measured" - CircuitPython's
`reset_reason` after a host-side reset was derived from a register dump, never read. It is now read,
and it is `RESET_PIN`, exactly as D4 predicted.

*One firmware does not come back, and it is not a regression:* **CircuitPython 8.0.2 never
re-enumerates after a hard reset** here - it re-boots and then spins in SRAM (measured
`pc` looping in `0x20003500`-`0x200039e0`, no console output, >120 s), where 9.2.9 and 10.2.1 both
come back in ~20 s. Reproduced identically with `cause=ResetCause.WATCHDOG`, i.e. with Phases 1-2's
bookkeeping skipped entirely, so it predates all of this work. Same family of gap as the Appendix's
point 5 (pads and peripherals survive a reset that should clear them) and therefore Phase 5's
problem; `ci-circuitpython.yml` gates the hard-reset step on `hard_reset: true` for 9.2.9/10.2.1
and says so in a comment rather than quietly skipping 8.0.2.

*Closes:* [0087]'s hard-reset fallback; [0088]'s "host-driven board reset" row.

*Not touched:* Phases 3-6 - no soft-reset entry point, no RUN pin, and `RP2040.reset()` still
covers only `core`/`pwm`/`dma`/`ppb`.

### Phase 4 - done (2026-08-20)

The RESET button, end to end - and with it [0057](0057-run-pin-reset-hook.md), which this closes in
full. Both of that record's options landed together, as section 2 said they had to: option A's
placement (a hook on `RP2040`, installed downward by `BaseDevice`) *and* option C's semantics (RUN
is a level, so a held button really holds the chip).

**4.1 The RUN level on `RP2040`** - `run_pin_low` (read-only) plus `set_run_pin(low=...)` and an
`on_run_pin_reset` hook defaulting to a warning, in both twins (`_rp2040.py`,
`native/_rp2040.pyx`). Only the *release* is an edge: pressing holds, re-driving a level already
present does nothing, and a release with no hook installed is logged rather than half-resetting a
chip whose USB half is unreachable from `RP2040`. `native/_rp2040.pyi` needed **no** change, which
is worth recording against 0057's "Cost of touching `RP2040` at all" - the stub re-exports
`RP2040` wholesale from `rp2040py._rp2040`, so the pure-Python class stays the single source of
truth for types, and the native class's `cdef dict __dict__` means no `.pxd` entry was needed
either. The cost was two twins, not three.

**4.2 "Held in reset", in both batch loops** (`_execute_batch.py` and `native/_simulator.pyx`): an
early return while `run_pin_low`. This is the third execution state 0057's option C priced -
nothing executes, no PIO steps, and **no simulated time passes**, which is what separates it from
`core.waiting` (a running chip in WFI, still servicing alarms) and from `simulator.stopped` (the
engine room ending). Checked once per batch, not per iteration: the level can only change from a
`schedule_threadsafe()` callback on the same loop, so it cannot move mid-batch.

`Simulator.execute()` needed one line beyond the plan, and it is not cosmetic: with the batch
returning instantly, its `await asyncio.sleep(0)` would have turned a held button into a fully
busy-spun core - the exact failure mode CLAUDE.md's threaded-test note warns about. It now parks
for `_HELD_IN_RESET_POLL_SECONDS` (0.005, one batch budget) per iteration while held.

**4.3 `BaseDevice.__init__` installs the hook downward** - `self.mcu.on_run_pin_reset =
self._on_run_pin_reset`, one line below the `watchdog.on_watchdog_trigger` line it copies, and
`_on_run_pin_reset()` is one more caller of `hard_reset(cause=RUN_PIN)` rather than a second
implementation. This is the trigger for which that cause is literal rather than a stand-in (D4).

**4.4 `external/reset_button.py`** - `press()`/`release()`/`click()`/`is_pressed`, every level
change handed to the engine room via `schedule_threadsafe()` per 0030, since unlike `BootselButton`
this one is pressed *while running* by definition. `is_pressed` is deliberately the host's view
(immediate); `mcu.run_pin_low` is the chip's (one engine-room turn later).

**4.5 Boards** - attached to the three whose files carried the "not modelled" note, and those notes
rewritten: `vcc_gnd_yd_rp2040` (whose own schematic is where the electrical facts came from),
`waveshare_rp2040_lcd_0_96`, `weactstudio`. Not attached to `pico`/`pico_w`: a real Pico has **no**
RESET button - BOOTSEL is its only one and RUN is a bare test point - so the live-boot check below
wires one on itself, which is what `attach_external_devices()` is for.

**Tests.** `tests/test_reset_button.py` (12) covers all three layers separately - the level/edge
semantics, the batch loop's new state (a recurring alarm fires 0 times while held and >0 once
released, with the clock frozen), and the device's handoff (asserted as "scheduled but not yet
applied", which a direct call would erase). Two more in `tests/test_watchdog_reset.py` prove the
`BaseDevice` wiring: releasing RUN runs the full device-level sequence (`pc`, flash preserved,
`cdc` reset, same `usb_ctrl` object) and produces §1.3's RUN-pin row through the *pin* rather than
through `hard_reset(cause=...)` directly. 0057's parity requirement needs nothing special - the
suite already runs under both `RP2040PY_SKIP_CYTHON=1` and `=0`.

**Live-boot verification** (`tests/reset_button_run.py`, now in both CI workflows): real
MicroPython v1.23.0 and CircuitPython 10.2.1, a button attached to a plain `pico`.

| observable | MicroPython v1.23.0 | CircuitPython 10.2.1 |
|---|---|---|
| RUN low while held | yes | yes |
| PC frozen across a 0.5 s hold | `0x20000ae0` | `0x20000c06` |
| re-enumerates on release, REPL usable | yes | yes |
| guest RAM gone (`marker` -> `NameError`) | yes | yes |
| reset cause | `machine.reset_cause() == PWRON_RESET` | **`microcontroller.ResetReason.RESET_PIN`** |

The frozen PC is the observable that only a genuinely held chip produces: an idle emulated device
is *not* still - a WFI'd core's PC moves constantly as timer/USB interrupts fire - so two reads
half a second apart matching is not something an executing loop can fake. CircuitPython 8.0.2 is
gated out of the CI step exactly as Phase 2's is, and for the same pre-existing reason.

**4.5b - the rest of `boards/`, swept board by board (2026-08-20).** 4.5 above attached the button
to the three boards whose files already documented it as "not modelled". The remaining 16 were then
checked one at a time, because the plan's "new boards get a RESET button for free" line quietly
assumes every board has one - and [0066](0066-board-support-expansion.md)'s own note said so
outright. It is not true, and that record now carries the correction.

*Method.* After Phase 4 there is **no electrical fact left to source** for a RESET button: 0057's
addendum settled that RUN's net has no pin number, no configurable pull and no polarity. The only
per-board question is whether the board has one, which firmware config can never answer (RUN is not
a GPIO, so `pins.csv`/`pins.c` never mention it; MicroPython's `board.json` `features` lists were
checked for seven of these boards and carry "USB-C"/"RGB LED", never buttons). So the source has to
be the vendor's own documentation, and that is what was fetched.

| verdict | boards | source |
|---|---|---|
| **attached, verified this session** | `seeed_xiao_rp2040`, `adafruit_feather_rp2040`, `adafruit_itsybitsy_rp2040`, `adafruit_qtpy_rp2040`, `sparkfun_promicro`, `pimoroni_tiny2040`, `machdyne_werkzeug` | vendor pages/wiki, quoted in each board file. Seeed's is the strongest: its wiki pin map lists the **R** pad as `RUN`, "Reset input" |
| **attached, on an in-tree citation that could not be re-read** | `waveshare_rp2040_zero`, `garatronic_pybstick26_rp2040` | the URL each file already cited; the fetch failed on 2026-08-20 and each file now says so |
| **not attached - the board has no RESET button** | `pimoroni_picolipo` | Pimoroni: an on/off **power** button, rebooting by "cut and reinstate the power" - a power cycle, `HAD_POR` not `HAD_RUN`. Attaching one would have been wrong, not approximate. What modelling it would take is [0092](0092-power-button-and-power-cycle.md) |
| **not attached - unsourceable** | `waveshare_rp2040_one`, `waveshare_rp2040_tiny`, `waveshare_rp2040_plus`, `nullbits_bit_c_pro`, `0xcb_gemini`, `0xcb_helios` | waveshare.com 403 / waveshare.net refused; the 0xCB guide URL 404s with no findable hardware repo; nullbits' page text names no buttons |

12 of 19 board files now carry one. The `pimoroni_picolipo` row is the reason this was worth doing
board by board rather than as a bulk edit: it is the one board where the "every board has one"
assumption would have produced a wrong emulation rather than a missing feature.

Live-boot: `seeed_xiao_rp2040`, `adafruit_feather_rp2040` and `pimoroni_tiny2040` were booted
through their own `--board-spec` files with the button attached (real MicroPython 1.28.0, each
reporting `machine.reset_cause() == 1` on a cold boot). The button's *behaviour* is proven on
`pico` instead (`tests/reset_button_run.py`) for a reason worth naming: `attach_external_devices()`
returns nothing, so a board file cannot hand its constructed `ResetButton` back to a caller - the
"a `BoardSpec` cannot hand you the devices it attached" caveat, not a new limitation.

*Known gaps, deliberately left:*

- **The emulated device stays enumerated while RUN is held.** On silicon the USB device drops off
  the bus the moment the chip is held; here `cdc.reset()` only runs as part of the release, since
  `hard_reset()` is one sequence and 0089 chose the release as its trigger. Visible only to a host
  watching enumeration state during a hold, and not worth splitting the one owner of the reset in
  two. **Closed the same day - see "Closing the two fidelity gaps" below, where splitting it turned
  out to be three lines and to uncover a real bug.**
- **A button-driven reset has no awaitable form.** `press()`/`release()` are fire-and-forget by
  nature; a caller that needs to *wait* for the board to come back uses `ahard_reset()` (Phase 2),
  which is the same reset with a host-side trigger. `tests/reset_button_run.py` therefore reaches
  for `_arm_enumeration()`/`_await_enumeration()` directly - acceptable in an in-tree test script,
  and a hint that a public form would need designing if a real caller ever wants one.
- **BOOTSEL held across a RESET** stays unreachable in effect, as section 5 requires - nothing here
  precludes it, and nothing here implements it either.

*Not touched:* Phases 5-6 - `RP2040.reset()` still covers only `core`/`pwm`/`dma`/`ppb`, so the
Appendix's point-5 gaps (a pad left driving, CircuitPython's CYW43 not coming back on a Pico W) are
unchanged and remain the sharpest argument for Phase 5.

### Phase 5 - done (2026-08-20)

The phase this plan was phased *for*: it changes what an existing, working reset does. Both
observables this record wrote down in advance as failing now pass live, and one prediction it made
turned out to be wrong.

**5.1 `RP2040.reset()` grows, and honours WDSEL** (both twins). The signature gains
`from_watchdog`, and the model is the two-level one the hardware actually has:

- **RUN pin / power-on**: everything.
- **Watchdog**: only what the guest selected - `PSM.WDSEL` for power-manager domains (SIO, CLOCKS,
  ...) and `RESETS.WDSEL` for individual peripheral blocks. Both registers were already *stored*
  and never acted on; both now are, as real `@property`s rather than cross-class private reads.

The route that does the work in practice is the indirect one, and it is worth stating because it
is not obvious from D5's quote alone: pico-sdk's `watchdog_reboot()` sets `PSM.WDSEL` and **never
touches `RESETS.WDSEL`**. The peripherals get reset because `PSM_WDSEL_RESETS` resets the RESETS
*controller*, whose own reset state holds every peripheral in reset. Measured live, not inferred:
during a real `machine.reset()` the firmware writes `PSM.WDSEL = 0x1fffc`, i.e. exactly
"everything apart from ROSC and XOSC". Every bit position used here comes from pico-sdk's own
`hardware/regs/psm.h` and `hardware/regs/resets.h` (0027's 3g rule), cited in `psm.py`/`reset.py`
next to the constants.

**What got a `reset()`, and the rule they all follow.** New in-place `reset()` methods on `GPIOPin`
(split `io=`/`pads=`, because `IO_BANK0` and `PADS_BANK0` are separate RESETS bits), `RPSIO`,
`Interpolator`, `RPPADS`, `RPUART`, `RPSPI`, `RPI2C`, `RPTimer`, `RPADC`, `RPClocks`, `RPPIO` and
`StateMachine` - the last two in both twins as well.

The rule, and it is the design content of this phase: **a reset resets registers, not wiring.**
Callbacks (`on_byte`, `on_transmit`, the five I2C `on_*`), `GPIOPin` listeners, DREQ identity,
alarm objects and `ADC.channel_values` all survive, because a chip reset does not unsolder the LED,
unplug the UART consumer or change the voltage on ADC0. That is also what makes resetting *in
place* safe for the `ExternalDevice`s and the `USBCDC` holding references into these objects - the
same doctrine `hard_reset()` already had at the device level. `tests/test_chip_reset.py` asserts it
directly, because a reset that cleared wiring would silently detach every device on the board while
every register assertion still passed.

`BasePeripheral` gained a documented no-op `reset()` (correct for read-only identity blocks like
`SYSINFO`/`TBMAN`) - but deliberately **not** the `Peripheral` Protocol, which is the *bus*
contract and is also implemented by `native/_pio.pyx`'s `cdef class`, which cannot inherit the base.

**5.2 SRAM stays untouched** (D6), asserted.

**Observable 1 - a pad left driving is released.** Live, MicroPython v1.23.0: a guest drives GPIO15
high (`FUNCSEL=0x5, OE=1`, pin reads HIGH), calls `machine.reset()`, and comes back with
`FUNCSEL=0x1f, OE=0, pad=0x36` - an LED left on goes dark, which is what silicon does.

**Observable 2 - CircuitPython brings the CYW43439 back up on a Pico W.** This is where D7's own
note ("resetting the pads is necessary but not sufficient") proved exactly right, and where a first
attempt at this phase looked like it had passed and had not:

| | before Phase 5 | pads reset only | pads + `WL_ON` |
|---|---|---|---|
| `[CYW43] Failed to start CYW43` | yes | **still yes** | no |
| `wifi.radio.mac_address` | `00:00:00:00:00:00` | **still all zeros** | `00:10:18:00:00:02` |

The middle column is the lesson: the firmware still *prints a MAC* when the chip is dead, so a
check that only asserted "the probe produced output" passed while the emulation was still broken.
`tests/chip_reset_run.py` asserts both halves.

What closed it: `Cyw43439` now models **`WL_ON`** - `CYW43_DEFAULT_PIN_WL_REG_ON 23u`, "gpio pin to
power up the cyw43 chip", from pico-sdk's own `boards/pico_w.h` - and drops `GSPIBus` back to its
power-on state on that pin's falling edge (`GSPIBus.power_off()`, wiring preserved: `_data_pin` and
`nat_bridge` survive, since cutting a regulator does not unsolder the chip). This is D7 working as
designed rather than an exception to it: the external chip sees the reset **through the GPIO
firmware drives**, exactly as the record argued, and `ExternalDevice` needed no new protocol method.

*One prediction that did not survive.* `ci-circuitpython.yml` said CircuitPython **8.0.2**'s failure
to re-enumerate after a chip reset was "almost certainly the peripheral/pad state a real reset
clears and 0089's Phase 5 will". Re-measured after this phase: it still does not come back
(`ahard_reset()` times out at 120 s). So Phase 5 is not the fix, the cause is still unknown, and the
CI gate stays. Comment corrected in the workflow rather than left to mislead the next reader.

*Known gaps, deliberately left:*

- **The core is reset on every path**, including a watchdog reset that selected no PSM domain at
  all. Broader than the hardware, and the one place this phase knowingly is: with PROC0 unselected
  the emulated CPU would run on into the reset it just requested, which is the hang
  `BaseDevice._on_watchdog_trigger()` exists to prevent. Written on `RP2040.reset()` itself.
- **Still unreset**: `xosc`/`rosc` (deliberately - `watchdog_reboot()` excludes them too), the RTC,
  and `busctrl`/`syscfg`/`sysinfo`/`tbman`/`ssi`/`xip_ctrl`, which have no `reset()` of their own
  yet. Named in `RP2040.reset()`'s docstring rather than left to be discovered. **Closed the same
  day - see the follow-up below.**
- **The TIMER's count** does not restart, because it reads from the simulation clock rather than
  from the block. A real `TIMER` restarts from zero. Unchanged by this phase, now written down.
  **Closed the same day - see below.**
- **CircuitPython 8.0.2**, above.

*Not touched:* Phase 6 - the documentation pass.

### Phase 6 - done (2026-08-20)

**6.1 `docs/reference/mpremote.md`.** Its `soft-reset` row said the firmware handles it and stopped
there, which is true and misleading: a new "Soft reset: raw prompt vs friendly prompt" section now
carries D2's measured table (both families, byte for byte) and states the consequence plainly -
`mpremote soft-reset` restarts the VM but **does not** re-run `main.py`/`code.py`, so it is not a
way to run a script you just uploaded. The one-line `aexec()` alternative is written out, together
with why there is deliberately no `asoft_reset()` (Phase 3). The `reset`/`bootloader` paragraph now
names all three triggers rather than only the watchdog one, and says what Phase 5 widened.

**6.2 `docs/reference/external-devices-and-boards.md` + the skill.** A "When the device acts on the
chip, not on a pin" section built around `ResetButton`, with its `attach()`/`press()`/`release()`
trimmed to the shape worth copying and the three things that shape is *for*: a press is a level not
a pulse, `schedule_threadsafe()` is mandatory for anything driven while the simulation runs, and the
device does not perform the reset (the hook does). It closes with the answer to "how does an
attached device see a chip reset" - it does not get a callback, it watches the GPIO firmware drives,
`Cyw43439`/`WL_ON` being the worked example (D7 + Phase 5). The skill gained the matching template
row and a testing row pointing at `tests/test_reset_button.py`'s "scheduled but not yet applied"
assertion.

**6.3 Statuses.** This record's own, above, and the tracker rows for [0057] (closed in full by
Phase 4), [0087] and [0088].

*What this phase deliberately did not do:* re-explain the reset design in the reference docs. The
records carry the reasoning; the reference docs get the parts a *user* needs - what a command does,
and what shape to copy.

### Phase 5 follow-up - the last unreset blocks (2026-08-20)

Phase 5's own "Still unreset" gap, closed rather than left: `RP2040RTC`, `RPBUSCTRL`, `RPXIPCtrl`
and `RPSSI` gained real `reset()` methods and are wired into `RP2040.reset()`. What is left is
`xosc`/`rosc` (deliberately) and the TIMER's count (it reads the simulation clock, not the block).

Two things this turned up that are worth more than the code:

- **`SYSCFG`/`SYSINFO`/`TBMAN` are not a gap.** They hold no instance state at all - read-only chip
  identity - so `BasePeripheral`'s no-op default *is* their correct implementation. Checked, and
  said so in `RP2040.reset()` rather than leaving them looking forgotten.
- **The XIP domain is not a `RESETS` bit.** There is no `RESETS_RESET_XIP` or `_SSI`; both halves
  sit in `PSM.WDSEL`'s XIP domain and reset together. Gating them on a RESETS bit would have looked
  right and never fired.

`RPSSI.reset()` had two landmines, both already documented in its constructor and both now covered
by tests: its QSPI_SS listener must **not** be re-registered (`_listeners` holds *bound methods* -
a fresh object each time, so re-adding leaves two firing per edge), and `_cs_asserted` must be
re-synced from the pin rather than set `False` (an `always_output_enabled` pad with nothing driving
it resolves LOW, i.e. already asserted; hardcoding False means the bootrom's first
`flash_cs_force(low)` fires no edge and its flash command hangs forever).

Since this touches the execution path out of flash, it was re-verified live rather than by tests
alone: `chip_reset_run.py --pads`, `hard_reset_run.py` (MicroPython v1.23.0) and
`chip_reset_run.py --cyw43` (CircuitPython 10.2.1) all still pass.

Also part of this pass: `Peripheral` (the Protocol that types `RP2040.peripherals`) gained
`reset()`. An earlier version of `BasePeripheral.reset()`'s docstring argued the opposite - that
the Protocol is the *bus* contract and that `native/_pio.pyx`'s `cdef class` could not satisfy it.
The first half is still true; the second stopped being true in Phase 4.1, when that class got its
own `reset()`. Checked before changing it: 26 classes inherit `BasePeripheral`, exactly one
implementer does not, and it has the method. Worth stating what this does **not** buy - it cannot
*find* a missing reset, because the base-class default satisfies it trivially.

### Closing the two fidelity gaps (2026-08-20)

Phases 4 and 5 each shipped one thing that was *wrong* rather than merely unbuilt, which is what
kept this record's tracker row open under the "a merged PR does not mean the idea is done" rule.
Both are now closed, and closing the first one found a bug.

**1. A held RESET now takes the chip off the USB bus.** `RP2040` gained a second hook,
`on_run_pin_held`, fired on the *falling* edge beside the existing `on_run_pin_reset` on the
rising one, and `BaseDevice.hard_reset()` split into the two halves the hardware actually has:

- `_enter_reset(cause)` - registers to their reset values, the cause recorded, `cdc.reset()`.
- `_leave_reset()` - point the core at flash's entry point and let it run.

A press runs the first, a release runs the second, and every other trigger (watchdog, host API)
runs both back to back - so there is still exactly one owner of the sequence, which was the
objection to splitting it. A board held in RESET is now *gone* from the host rather than idle, and
the core is in its reset state rather than frozen mid-instruction (visible in
`tests/reset_button_run.py`'s output: the frozen PC is now the reset PC).

**The bug this uncovered.** Writing `_enter_reset()` meant looking at the `mcu.reset()` call it
inherited - and it was `self.mcu.reset(preserve_flash=True)`, with **no `from_watchdog`**. Phase 5
added that parameter and the whole `PSM.WDSEL`/`RESETS.WDSEL` model behind it, and then never
passed it from the device layer: every device-level reset, watchdog included, silently took the
"reset everything" path. Phase 5's own live check passed *for the wrong reason*, and only its unit
tests (which call `RP2040.reset()` directly) ever exercised the gated path. Fixed, and
`tests/test_watchdog_reset.py::test_a_watchdog_reset_reaches_the_wdsel_gated_path` is the
regression test - a watchdog reset with no WDSEL bit set must now leave the peripherals alone.

Worth naming the general lesson, because this project has hit it before: a parameter with a default
is a silent seam. The unit tests exercised `RP2040.reset(from_watchdog=True)` and the live test
exercised the device path, and neither could see that the two were not connected.

**2. The TIMER restarts from zero.** `RPTimer` gained `_epoch_nanos` and a `_micros()` helper that
every read *and* every alarm arming goes through, so the two cannot disagree about "now";
`reset()` moves the epoch to the current instant rather than rewinding the simulation clock (which
is shared with USB SOF, every other peripheral's alarms and the engine room, and must not move).
Guest code now sees `time.ticks_ms()`/`time.monotonic()` start over after a reset, which is what
silicon does and what made this a fidelity gap rather than a detail.

Re-verified live after both changes, since this is still the reset path: `chip_reset_run.py --pads`
(MicroPython v1.23.0, the watchdog path - and now genuinely WDSEL-gated, with the firmware's own
`PSM.WDSEL = 0x1fffc` doing the selecting), `reset_button_run.py` (the RUN pin, both edges) and
`chip_reset_run.py --cyw43` (CircuitPython 10.2.1, WiFi back up after the reset). 803 unit tests
pass on both builds.

*What is left against this record*, stated precisely rather than as one bucket:

- **Decisions.** The core is deliberately reset on every path (see `RP2040.reset()`: with PROC0
  unselected the emulated CPU would run on into the reset it just requested). A button-driven reset
  deliberately has no awaitable form - `press()`/`release()` model a physical action, and a caller
  that needs to wait uses `ahard_reset()`, the same reset with a host-side trigger. `rosc` is not
  reset because it is an `UnimplementedPeripheral` with no state.
- **`xosc`, which was briefly written down as a residual gap and then simply fixed.** The first
  draft of this section claimed it was deliberately not reset "because `watchdog_reboot()` excludes
  it" - true of the watchdog path and *false* of the RUN-pin/power-on one, where silicon does reset
  it. What made the fix trivial is that Phase 5's own machinery already expressed the distinction:
  XOSC is a `PSM.WDSEL` domain bit, so gating `RPXOSC.reset()` on it is right on both paths for
  free - excluded when `watchdog_reboot()` clears the bit, reset when a power-on selects
  everything. `tests/test_chip_reset.py` asserts both halves, because getting only one right looks
  identical in any test that does not check the other.

  Worth keeping the wrong first draft visible: the temptation was to file it as a task note, and
  the honest check - *does the machinery already say this?* - turned a backlog item into ten lines.

So the row closes.

[0087]: 0087-circuitpython-writable-circuitpy-over-the-raw-repl.md
[0057]: 0057-run-pin-reset-hook.md
[0088]: 0088-usb-host-side-msc-control-lines-and-reset.md

## Note (2026-08-20): AIRCR/SYSRESETREQ - the trigger this record never enumerated

**Documentation only. Nothing here is built, and this authorizes nothing** (CLAUDE.md's
"document vs. implement"). Found while diagnosing the CI reds this record's own live checks
produced.

### What is missing

`AIRCR` (`0xE000ED0C`) is not implemented at all. `peripherals/ppb.py` has no constant for it and
no branch handling it, so the architectural reset request every ARM toolchain emits - CMSIS's
`NVIC_SystemReset()`, which writes `VECTKEY | SYSRESETREQ` - falls through to
`Peripheral.write_uint32()`'s default. Measured:

    >>> mcu.write_uint32(0xE000ED0C, (0x05FA << 16) | (1 << 2))
    [PPB] Unimplemented peripheral write to 0xd0c: 0x5fa0004
    triggers fired : none
    pc             : 0x10000100  (unchanged - nothing reset)

A read is worse than silent: `[PPB] Unimplemented peripheral read from 0xd0c` returns
`0xffffffff`, so any firmware doing a read-modify-write of AIRCR (the CMSIS `NVIC_SystemReset()`
for M3 and up does exactly that) computes its new value from garbage.

Section 1.2's table lists five triggers. This is a sixth, and neither firmware family here reaches
it: both MicroPython and CircuitPython reset through `watchdog_reboot()`, which is why nothing has
noticed. It is what a **non**-MicroPython/CircuitPython image would use - the tracker's own
"test TinyGo-compiled firmware" row is the near-term case, and any bare-metal SDK or RTOS image is
the general one.

### Why it is *not* simply a sixth caller of `hard_reset()`

This is the part worth writing down, because the obvious reading is wrong. On RP2040 SYSRESETREQ
is **not** a chip reset:

> [RP2040 datasheet §2.4.2.9] SYSRESETREQ on RP2040 resets only the core on which it is asserted,
> not the wider system.

The same datasheet's AIRCR description (§2.4.8) still carries ARM's generic wording - *"the
intention is to force a large system reset of all major components except for debug"* - which
contradicts it; Raspberry Pi's own tracker carries that contradiction as
[pico-feedback#329](https://github.com/raspberrypi/pico-feedback/issues/329), unresolved at the
time of writing. The narrower statement is the one to implement against: it is the specific one,
and it matches why the SDK reboots through the watchdog rather than through AIRCR.

So a faithful model needs something this record does not have: a **core-only** reset level, below
the two it settled on. Its D-decisions go the other way on purpose - "the core is reset on every
path" (section 4, Phase 5's note) is written as deliberately broader than the hardware, and
`_enter_reset()` always takes the whole chip down. A `SYSRESETREQ` that reset the chip would be
wrong in a way that is *hard to see*: firmware would come back with peripherals it expected to
keep, which is exactly the class of silent-wrongness this record exists to avoid.

### What that leaves as the decision, whenever someone picks this up

1. **Implement AIRCR as a register first** (VECTKEY validation, `ENDIANNESS`, the reserved
   read-back), so a read-modify-write stops seeing `0xffffffff`. That much is uncontroversial and
   independent of the reset question.
2. **Then decide the level.** Either model the core-only reset honestly (`core.reset()` plus
   whatever PSM `PROC0` implies, leaving peripherals alone), or refuse it loudly - a
   `logger.warning` naming SYSRESETREQ rather than the generic "unimplemented peripheral write" -
   until a firmware that actually uses it is in the tree to verify against. The second is cheap and
   honest; the first needs a real image to check, per this project's own live-verification rule.
3. **Do not route it into `hard_reset()`** without that decision, however tempting the one-line
   version looks.

