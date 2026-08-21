# 0057. RESET button / the RUN pin: a reset hook on `RP2040`

- Status: **Implemented (2026-08-20)**, as [0089](0089-one-reset-for-every-trigger.md)'s Phase 4 -
  see the closing section at the end of this record for what was actually built and where. It was
  "Proposed — documented, not implemented (2026-08-17)" for three days; the record exists because
  the decision needed making deliberately rather than being guessed at inside a board file, and
  what shipped is neither option A nor option C alone but both together, which is what the
  addendum's schematic forced.
- Conceived: 2026-08-17
- Related: 0056 (`WAVESHARE_RP2040_LCD_0_96` — the board whose RESET button raised this, and where
  the omission is currently written down), 0051 (`BootselButton` — the button that *could* be an
  `ExternalDevice`, and why), 0030 (`ExternalDevice` concurrency contract — what a host-thread
  `press()` is allowed to do), 0029 (board composition), 0021 (shutdown coordinator — the other
  place lifecycle events cross the device/MCU boundary)

## The gap

Every RP2040 board this project models has two buttons, and only one of them is emulatable today:

| button | electrically | emulated? |
|---|---|---|
| BOOTSEL | shorts `GPIO_QSPI_SS` (an ordinary pad) to ground | yes — `external/bootsel_button.py` drives that pad, 0050/0051 |
| RESET | pulls the **RUN** pin low | **no** — RUN is not a GPIO, not memory-mapped, and has no model at all |

`ExternalDevice`'s whole contract is `attach(rp2040)`, and everything a device can reach is on
that object: `rp2040.gpio[n]`, `rp2040.spi[n]`, `rp2040.clock`, and so on. A RESET button has
nothing to grab.

Worse, the reset it needs to perform is **not** `rp2040.reset()`. The only live-reset path in the
tree is `BaseDevice._on_watchdog_trigger()` (`device/base_device.py`), which is what a guest's own
`machine.reset()`/`machine.bootloader()` reaches by writing the watchdog's TRIGGER bit:

```python
self.mcu.reset(preserve_flash=True)  # registers/core, without erasing the running firmware
self.mcu.core.pc = FLASH_START_ADDRESS  # jump to flash's entry point, like _aconnect()'s cold boot
self.cdc.reset()  # USB-CDC: the host side must re-enumerate
```

The third line is the blocker. `USBCDC` is constructed by `BaseDevice` around `mcu.usb_ctrl`, and
the reference only points one way (`USBCDC.usb` → `usb_ctrl`); nothing hangs the `USBCDC` off the
`RP2040`. A device that performed only the first two steps would restart the chip while leaving
the host's REPL connection in a stale state — a confusing hang, strictly worse than the honest
"not modelled" the board file says today.

## Options

### A. A reset hook on `RP2040` (recommended)

Smallest possible API delta, and it mirrors a shape this codebase already uses — `RPWatchdog`
already exposes `on_watchdog_trigger`, which `BaseDevice` assigns to its own handler in
`__init__`. The same trick, one level up:

```python
# rp2040.py (both twins - see "Cost" below)
def set_reset_hook(self, hook: "Callable[[], None] | None") -> None:
    """Called when something outside the core requests a full chip reset - today, a RESET
    button pulling RUN low. `BaseDevice` points this at its own reset sequence, the one that
    also resets the USB-CDC host side; with no hook set, a request is logged and ignored."""
```

`BaseDevice.__init__` then does `self.mcu.set_reset_hook(self._on_watchdog_trigger)` right next to
the `watchdog.on_watchdog_trigger` assignment it already makes, and a new
`external/reset_button.py` gets:

```python
def press(self) -> None:
    self._rp2040.schedule_threadsafe(self._rp2040.request_reset)
```

`schedule_threadsafe()`, not a direct call: a button is pressed from whatever thread the host UI/
test runs on, and 0030 is explicit that an external device may not touch engine-room state from
another thread. (`BootselButton` gets away with a direct `set_input_value()` only because callers
press it before `start_execution()`; a RESET button is pressed *while running* by definition.)

**Why a method and not a plain attribute:** a setter can be given a docstring and keeps the
Cython twin's `cdef class` honest — a bare public attribute on a `cdef class` has to be declared
in the class body anyway, so there is no simplicity saved by skipping the method.

### B. Move the whole sequence into `RP2040`

Make `RP2040` own reset end to end (`RP2040.reset_and_boot()`), and let `USBCDC` observe it rather
than be called: `usb_ctrl` grows an `on_reset` notification that `USBCDC` subscribes to in its
constructor, so the CDC half stops being the device layer's job. This is the cleaner layering —
"a chip reset makes the device re-enumerate" is a property of the chip, not of whoever wrapped it
— and it would let `machine.reset()`, a RESET button, and any future `--reset` CLI action all
share one path with no hook indirection at all.

It is also the bigger change: it moves behavior out of `BaseDevice` (whose `_on_watchdog_trigger`
docstring explains exactly why the reset is done *in place* on objects external code holds
references to), adds a notification to `usb_ctrl`, and would want its own test pass over both
reset paths. Worth doing eventually; not worth coupling to "the board has a RESET button".

### C. Model RUN as a real pin

Give `RP2040` a `run` pseudo-pin, held high by default, and make the reset a consequence of its
edges: falling edge holds the core (nothing executes while RUN is low), rising edge performs the
reset-and-boot sequence. Most faithful to the hardware, and it is what makes "press and hold
RESET" behave like the real board rather than like a momentary event.

The cost is that "hold the core" is a new execution state the batch loop
(`_execute_batch.py`) does not have — distinct from `core.waiting` (WFI, which still advances
simulated time via alarms) and from `simulator.stopped`. Introducing it just for a button, without
a caller that needs press-and-hold semantics, is more machinery than the problem justifies today.

**Recommendation: A now, with C's `press()`/`release()` API shape on the device so the faithful
version can land later without a breaking change** (i.e. `release()` is what fires the reset, and
`press()` merely records that RUN is low — the difference between them being unobservable until C
exists). B stays a separate, deeper refactor.

## Cost of touching `RP2040` at all

Worth stating plainly, because it is the reason this is a record and not a five-line patch:
`RP2040` exists **twice** — `_rp2040.py` (pure Python) and `native/_rp2040.pyx` (a `cdef class`,
with `native/_rp2040.pyi` as its stub), selected at import time by `rp2040.py`'s try/except (0013,
0047). Any new public member has to land in all three, and the two implementations must not drift
— exactly the class of change 0047 already had to be careful about when adding `.pyi` stubs for
`native.*`. A hook is one attribute and one setter in each; option B's `reset_and_boot()` is more.

## Semantics still to decide before writing code

- **Does RUN-low erase flash?** No: `preserve_flash=True`, same as the watchdog path — a real
  RESET does not wipe the QSPI chip.
- **What does `WATCHDOG.REASON` read after a RUN reset?** Currently `_reason` is only ever set by
  the timer path. A RUN-driven reset must *not* leave `TIMER` set, or firmware will misreport why
  it rebooted (MicroPython exposes this as `machine.reset_cause()`).
- **BOOTSEL held during reset.** On real hardware, RESET released while BOOTSEL is held enters the
  bootrom's USB mass-storage mode. `BootselButton` can already hold `GPIO_QSPI_SS` low, so once a
  reset path exists this combination becomes reachable — and the bootrom this project ships would
  then have to be entered rather than jumping straight to `FLASH_START_ADDRESS`. Out of scope for
  a first pass, but the hook's contract should not make it impossible.
- **Does anything need to survive?** `simulator`, `logger`, `cdc`, and the board's attached
  `ExternalDevice`s all hold references into the MCU and must stay valid — the reason
  `_on_watchdog_trigger` resets in place instead of reconstructing. An attached device sees no
  callback at all today; whether devices should get a `reset()` notification is the same open
  question 0049 already tracks about `ExternalDevice`'s attach-only surface.

## Testing plan (for whenever this is built)

- Unit: hook fires exactly once per `release()`, is not called when unset, and reaches the MCU on
  the engine-room thread (assert via `schedule_threadsafe` rather than a direct call).
- Integration, against real MicroPython firmware: boot, `x = 1` at the REPL, press+release RESET,
  then confirm the device re-enumerates and `x` is gone (i.e. a real restart, not just a jump) —
  the same live-boot bar 0056's board work was held to.
- Parity: the pure-Python and native `RP2040` twins must behave identically, so the test belongs in
  the suite that already runs under both `RP2040PY_SKIP_CYTHON=1` and `=0`.

## Addendum, 2026-08-17: a real RESET net, from a vendor schematic

Written without a schematic in hand. One has since turned up, in
[0062](0062-yd-rp2040-board-and-ws2812.md)'s work on the YD-RP2040 (VCC-GND Studio's own
`YD-2040 2022 V1.1 SCH`), and it firms up two of the assumptions above rather than changing them:

    3V3 ──[ R12 10k ]──┬── RUN
                       └── RST (ST-1185S) ──/── GND

- **A press is a level, not a pulse.** The switch grounds RUN *directly* - no series resistor - for
  as long as it is held. That is a point in favour of option 3 (model RUN as a really-held-low pin,
  with a new execution state) being the faithful choice: a hook that fires a one-shot reset models
  a tap, and the difference is visible to anyone who holds the button.
- **There is nothing electrical left to get wrong.** RUN is not a GPIO, so no internal pull is
  configurable and the board supplies an external 10k pull-up; the released level is therefore
  unconditional. Unlike `BootselButton` (0051), whose correctness depends on the QSPI pad's own
  reset-value pull-up (0050), a `ResetButton` has no pull semantics to model at all. Every bit of
  the difficulty is on the "what does a reset actually do" side - i.e. exactly what this record is
  about, and none of it is cheaper than it looked.

Same board, for contrast: its USRKEY on GPIO24 has *no* external pull-up (a 10k sits in series with
the pin instead), so that button's released level comes entirely from whichever internal pull
firmware configures - the case 0006 models and 0049's addendum had to fix in `key_mock.py`.

## Note (2026-08-20): the third step now has a proposed owner

[0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md) may need a public, host-side
hard reset for an unrelated reason - restarting a board after writing its CIRCUITPY over the raw
REPL, so the written filesystem (flash is preserved) is what boots. Its body would be this
record's own three-line sequence plus the waiting half of `_aconnect()`, which is exactly the
`cdc.reset()` + re-enumeration step "The gap" above calls the blocker. Note it is 0087's
*fallback*, not its plan: a firmware soft reset (Ctrl-D at the raw-REPL prompt) already works and
needs none of this, so the hard reset only gets built there if a soft one turns out not to re-run
`code.py`.

That does not resolve this record. The blocker it removes is the *sequencing* one; the other half
stands untouched - `ExternalDevice` gets `attach(rp2040)`, and a `BaseDevice` is not reachable from
there, so a RESET button still has nothing to call. Worth designing the two together: if 0087's
reset lands first without this record in view, a RUN-pin model will have to work around whatever
shape it took.

Since written, that has become its own design record: [0089](0089-one-reset-for-every-trigger.md)
treats the RESET button as one of four triggers that must share a single hard-reset owner, proposes
the watchdog's own downward-hook precedent (`BaseDevice.__init__` installing an implementation onto
an MCU-owned object) as the answer to the placement half above, and writes down two questions this
record had not stated: that RUN is a *level* (a held button holds the chip in reset, so a
fire-and-forget `reset()` is the wrong shape), and that `RP2040.reset()` today resets only
`core`/`pwm`/`dma`/`ppb` - not SRAM, not `spi`/`i2c`/`pio`/`clocks`/`timer`/`adc`/`uart` - which a
RESET button is the trigger most likely to expose.


## Update (2026-08-20): this record's remaining half is now planned, in 0089

[0089](0089-one-reset-for-every-trigger.md)'s "Resolution" section turns its design into a
six-phase plan, and **Phase 4 is this record, in full**. What it settles, so nothing above stays an
open question:

- **Placement (option A) and semantics (option C), together, not one instead of the other.**
  `RP2040` grows a RUN *level* plus a hook; `BaseDevice.__init__` installs its own hard reset into
  that hook, exactly the way it already installs `_on_watchdog_trigger` onto
  `mcu.watchdog.on_watchdog_trigger`. So an `ExternalDevice` with only `attach(rp2040)` reaches the
  full sequence, and a held RESET button really holds the chip - the batch loop
  (`_execute_batch.py` and its native port) gains the "held in reset" state option C priced, since
  the schematic in the addendum above makes a press a level rather than a pulse.
- **`WATCHDOG.REASON` after a RUN reset: `0`, and `CHIP_RESET` reads `HAD_RUN`.** Read out of
  pico-sdk and CircuitPython source (`ports/raspberrypi/common-hal/microcontroller/Processor.c`:
  *"Check watchdog after chip reset since watchdog doesn't clear chip_reset, while chip_reset
  clears the watchdog"*), so `machine.reset_cause()` must read `1` (`PWRON`) and
  `microcontroller.cpu.reset_reason` must read `RESET_PIN` - the exact misreport this record warned
  about, now with a table to implement against (0089 section 1.3). **Implemented 2026-08-20** as
  0089's Phase 1: `RPWatchdog.reset()`, `RPVREGAndChipReset.record_reset_cause()` and
  `BaseDevice.hard_reset(cause=ResetCause.RUN_PIN)` already produce exactly these register values -
  what is still missing for this record is the RUN pin that pulls them (0089's Phase 4).
- **Does a RESET button need a fuller `RP2040.reset()`: yes, and it is 0089's Phase 5**, scoped by
  what the SDK itself asks for (`psm_hw->wdsel = PSM_WDSEL_BITS & ~(ROSC | XOSC)` - "reset
  everything apart from ROSC and XOSC"). SRAM stays uncleared, deliberately: a PSM reset resets the
  SRAM controllers, not the array.
- **Do attached `ExternalDevice`s need a reset callback: no.** A real board's external chip is not
  wired to RUN - it only sees a reset through the GPIO the firmware drives (`WL_ON`, a display's
  `RES`). Resetting the pads in Phase 5 is what reproduces that; `ExternalDevice`'s attach-only
  surface stays as it is.
- **BOOTSEL held across a RESET** stays unreachable in effect, because mass storage and the
  bootrom's USB mode are explicitly out of scope (0089 section 5, maintainer's decision). Phase 4's
  hook contract must still not preclude it.


## Closing (2026-08-20): built, live-verified, and where each piece landed

[0089](0089-one-reset-for-every-trigger.md)'s Phase 4 shipped this record in full. The mapping, so
nothing above has to be read as still-open:

| this record asked | what landed |
|---|---|
| A reset hook on `RP2040` (option A) | `RP2040.on_run_pin_reset`, defaulting to a warning; `BaseDevice.__init__` installs `_on_run_pin_reset` -> `hard_reset(cause=RUN_PIN)` next to the `watchdog.on_watchdog_trigger` line it copies |
| RUN as a real pin (option C) | `run_pin_low` + `set_run_pin(low=...)`; only the release is an edge |
| "hold the core" - the new execution state option C priced | an early return in both `execute_batch()` twins while `run_pin_low`: no instruction, no PIO, no simulated time. Plus one line in `Simulator.execute()` so a held button parks instead of busy-spinning |
| `press()`/`release()` on the device (the recommendation's API shape) | `external/reset_button.py`, every level change via `schedule_threadsafe()` per 0030 |
| "Does RUN-low erase flash?" - no | unchanged: `hard_reset()` calls `mcu.reset(preserve_flash=True)` |
| "What does `WATCHDOG.REASON` read?" - not `TIMER` | 0089's Phase 1 already answered this in code; Phase 4 is what pulls it. Live: MicroPython reads `PWRON_RESET`, CircuitPython reads `RESET_PIN` |
| Parity between the two `RP2040` twins | both changed; the suite runs under `RP2040PY_SKIP_CYTHON=1` and `=0` already. `native/_rp2040.pyi` needed nothing - it re-exports the pure-Python class, so the "any new public member has to land in all three" cost above was two, not three |
| The testing plan (unit, live-boot, parity) | `tests/test_reset_button.py`, `tests/test_watchdog_reset.py`'s two new cases, and `tests/reset_button_run.py` in both CI workflows |

**Option B is still not done, and is still worth doing.** Nothing here moved the reset sequence out
of `BaseDevice` or gave `usb_ctrl` an `on_reset` notification - the hook indirection this record
recommended as the cheaper path is exactly what shipped. What changed is that there are now three
callers behind it (watchdog, host API, RUN pin) rather than one, which makes the case for B
stronger, not weaker.

**Two of the "semantics still to decide" bullets stayed decided-but-unbuilt**, both by 0089's own
scoping: BOOTSEL held across a reset (mass storage is rejected outright, 0089 section 5) and
whether attached `ExternalDevice`s need a reset callback (no - 0089's D7 says a real board's
external chip only sees a reset through the GPIO firmware drives, which is Phase 5's pad reset).


## Closing, part 2 (2026-08-20, later the same day): option B shipped as well

The section above closed this record with "**Option B is still not done, and is still worth
doing**" and the reason it had got stronger: three callers (the watchdog, the host API, the RUN
pin) all reaching one sequence that only `BaseDevice` knew how to run. That is now built, so read
that paragraph as history rather than as an open item.

**What moved.** `RP2040` grew the sequence itself, in both twins:

```python
def enter_reset(self, *, from_watchdog: bool = False) -> None:
    self.reset(preserve_flash=True, from_watchdog=from_watchdog)
    if self.usb_ctrl.on_reset:
        self.usb_ctrl.on_reset()


def leave_reset(self) -> None:
    self.core.pc = FLASH_START_ADDRESS
```

`BaseDevice._enter_reset()`/`_leave_reset()` keep their names and their docstrings - they are the
device layer's word for the same thing - but are now one line each over the MCU's, plus the
`_record_reset_cause(cause)` the device layer still owns (a `ResetCause` is not something the chip
has an opinion about; the registers it writes are 0089 §1.3's table).

**The third step, which was this record's whole blocker, is now a notification.** The gap section
above named it exactly: `USBCDC` is constructed by `BaseDevice` around `mcu.usb_ctrl`, the
reference only points one way, and "a device that performed only the first two steps would restart
the chip while leaving the host's REPL connection in a stale state". The fix is the direction the
existing hooks already run - `USBCDC.__init__` installs `self.usb.on_reset =
self._on_controller_reset` next to the four `on_usb_enabled`/`on_reset_received`/
`on_endpoint_write`/`on_endpoint_read` assignments it already makes. So `enter_reset()` resets
whatever is attached to the bus without knowing a CDC exists, and `USBCDC.reset()` survives as
`_on_controller_reset()` + `usb.reset()` for callers that hold the CDC and want both halves.

**Two defaults changed from warning to resetting.** This is the part worth stating plainly, because
it is behaviour and not just placement:

| hook, with nothing installed over it | before | now |
|---|---|---|
| `RPWatchdog.on_watchdog_trigger` | logs "Watchdog triggered, but no reset handler provided", guest spins forever | `enter_reset(from_watchdog=True)` + `leave_reset()` |
| `RP2040.on_run_pin_held` / `on_run_pin_reset` | logs "RUN pin pulled low/released, but no reset handler provided" | `enter_reset()` / `leave_reset()` |

Those warnings were never a design position - they were "nobody wired the device layer up", which
was the only honest thing a bare `RP2040` could say while the sequence lived one layer above it.
`rp2040py run` builds exactly that (a bare `RP2040` + `USBCDC`), so a guest calling
`machine.reset()` under it used to hang; now it reboots. `tests/test_bare_chip_reset.py` is the
regression: a bare chip resets on a watchdog TRIGGER and on both RUN-pin edges, and an `on_reset`
consumer is notified.

**One decision inside the move.** `on_reset` fires from `enter_reset()` unconditionally, *not* from
`RPUSBController.reset()` - even though "the USB block was reset" looks like the more natural
place for it. The USB block is only register-reset when `RESETS.WDSEL` selects it (0089's Phase 5),
but a chip reset drops the device off the host's bus either way. Hanging the notification off the
block's own `reset()` would have made `hard_reset()`'s "the device re-enumerates" guarantee - the
one `ahard_reset()` waits on - quietly conditional on what a guest happened to write to WDSEL.
Option B moves ownership; it does not move behaviour.

**Verified**: `uv run pre-commit run --all-files` green on both builds (816 passed), plus live
`tests/hard_reset_run.py` and `tests/reset_button_run.py` on MicroPython 1.23.0 - both still
report `PWRON_RESET`, which is the point: the sequence changed owner, not effect.

With this, nothing in this record is outstanding. The two decided-but-unbuilt bullets above
(BOOTSEL held across a reset; a reset callback for `ExternalDevice`s) stay as 0089 scoped them -
rejected and answered "no" respectively, not deferred.
