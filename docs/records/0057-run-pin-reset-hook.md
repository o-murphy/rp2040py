# 0057. RESET button / the RUN pin: a reset hook on `RP2040`

- Status: **Proposed — documented, not implemented (2026-08-17).** Nothing in this record has been
  built; it exists so the decision is made deliberately rather than guessed at inside a board file.
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

[0087](0087-circuitpython-writable-circuitpy-over-the-raw-repl.md) proposes a public, host-side
`device.areset()` for an unrelated reason - restarting a board after writing its CIRCUITPY over the
raw REPL, so the written filesystem (flash is preserved) is what boots. Its body is this record's
own three-line sequence plus the waiting half of `_aconnect()`, which is exactly the `cdc.reset()`
+ re-enumeration step "The gap" above calls the blocker.

That does not resolve this record. The blocker it removes is the *sequencing* one; the other half
stands untouched - `ExternalDevice` gets `attach(rp2040)`, and a `BaseDevice` is not reachable from
there, so a RESET button still has nothing to call. Worth designing the two together: if 0087's
reset lands first without this record in view, a RUN-pin model will have to work around whatever
shape it took.

