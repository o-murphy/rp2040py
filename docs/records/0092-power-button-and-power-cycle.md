# 0092. A power button, and what a power cycle would have to destroy

- Status: **Proposed - documented, nothing implemented (2026-08-20).** Written per CLAUDE.md's
  "document vs. implement" rule, out of a question asked while sweeping `boards/` for RESET buttons
  ([0089](0089-one-reset-for-every-trigger.md)'s Phase 4.5b): *"HAD_POR - в нас нема?"*. The answer
  is that the reset **cause** has existed since 0089's Phase 1 and is not what is missing - so this
  record exists to write down what actually is, before anyone reads "power cycle" as a five-line
  change. It is not a plan and authorizes nothing.
- Conceived: 2026-08-20
- Related: [0089](0089-one-reset-for-every-trigger.md) (the reset umbrella - its §1.3 table, its D6
  on SRAM, its D7 on external devices, and its Phase 4 machinery this would reuse),
  [0057](0057-run-pin-reset-hook.md) (the RUN pin - the same "an ExternalDevice has nothing to call"
  blocker, one line further along), [0049](0049-external-device-authoring-docs.md) (the open
  question about `ExternalDevice`'s attach-only surface, which this is the first real case for),
  [0066](0066-board-support-expansion.md) (the board survey whose "every board has a RESET button"
  claim this found the counterexample to)

## Where it came from

`boards/pimoroni_picolipo.py` was expected to get a `ResetButton` like every other board in the
sweep, and could not: the Pico LiPo has **no RESET button at all**. Pimoroni's own product page
describes "an on/off button and a BOOTSEL button", and says how you reboot it - *"The power button
can also be used as a reset button, yay! Just double press it to cut and reinstate the power"*.

That is a **power cycle**, not a RUN short, and the difference is observable to firmware: a power-on
reset leaves `CHIP_RESET.HAD_POR`, a RUN reset leaves `HAD_RUN`, and both firmwares report them
differently (0089 §1.3). Attaching a `ResetButton` there would not have been an approximation - it
would have made the board report the wrong reason for its own reboot.

## What already exists (i.e. what this record is *not* about)

All of the cause bookkeeping, since 0089's Phase 1:

- `HAD_POR` (`peripherals/vreg_and_chip_reset.py`), and it is the value a freshly constructed
  `RPVREGAndChipReset` already starts at - a cold boot reports power-on without anyone asking.
- `ResetCause.POWER_ON` (`device/base_device.py`), and `_record_reset_cause()` mapping it to
  `HAD_POR` while clearing the watchdog's REASON/scratch.
- `BaseDevice.hard_reset(cause=ResetCause.POWER_ON)` - a real, tested path
  (`tests/test_watchdog_reset.py::test_power_on_reset_records_had_por`). A **host-side** caller can
  therefore already produce a power-on-flavoured reset today, and `ahard_reset(cause=POWER_ON)`
  makes it awaitable.

So "we have no HAD_POR" is not the gap. Two other things are.

## Gap 1: nothing a device can call

`RP2040` exposes `on_run_pin_reset` and `set_run_pin()`; there is no equivalent for power. An
`ExternalDevice` only ever gets `attach(rp2040)`, so a `PowerButton` would have nothing to reach -
the exact blocker [0057](0057-run-pin-reset-hook.md) spent a record on for the RUN pin, arriving one
line further along the same board.

The shape is presumably the one 0089's D3 already settled for RUN (a level on `RP2040` plus a hook
`BaseDevice` installs its own sequence into), and the *held* half is free: Phase 4's batch-loop
"held in reset" state is exactly "the chip executes nothing", which is also what "the board is
switched off" means here. Whether that is one state with two reasons or two states is a real design
question and is **not** decided here - a single flag read once per batch is cheap and honest, but
"why is this chip not running" then needs an answer for logs and for `run_pin_low`'s own callers.

A power button is also a **toggle**, not a momentary contact: `on()`/`off()`/`is_on`, not
`press()`/`release()`. The Pico LiPo's double-press-to-reboot is a *user* gesture on top of that,
not a hardware mode - modelling it as anything but two level changes would be inventing hardware.

## Gap 2: what a power cut destroys is undecided

This is the harder half, and the reason this is a record rather than an issue.

- **SRAM.** 0089's D6 keeps SRAM across a reset deliberately, and is careful about why: a PSM reset
  resets the SRAM *controllers*, not the array, so contents are undefined-but-typically-preserved.
  That reasoning does not transfer to the rail actually going away. A faithful power cycle loses
  RAM. Whether this emulator should zero it, fill it with a pattern, or keep D6's behaviour anyway
  (on the grounds that firmware re-initialises everything it relies on, so nothing observable
  changes) is undecided - and the cheapest of the three is not obviously the wrong one.
- **The USB device must be gone while the board is off**, not merely re-enumerating. `cdc.reset()`
  already drops it, but "dropped, and staying dropped until switched on" is a state nothing models;
   the same gap 0089's Phase 4 knowingly left for a *held* RESET button.
- **Attached `ExternalDevice`s lose power too, and this is where D7 stops applying.** 0089's D7
  says an external chip is not wired to RUN and only ever sees a reset through the GPIO firmware
  drives - which is true, and is why `ExternalDevice` kept its attach-only surface. Cutting the
  board's power is the case that breaks it: the display, the WS2812 and the CYW43439 lose their
  rails along with the RP2040. So a power button is the **first genuine caller** for the reset
  notification [0049](0049-external-device-authoring-docs.md) tracks as an open question and D7
  declined to add. That is an argument for designing the two together, not for bolting a callback
  on because this record wanted one.
- **Flash survives**, being non-volatile - the one thing here that needs no decision.

## What would have to be true before this is worth building

Stated plainly so nobody has to re-derive it:

1. A second board wants it. Today exactly one board file in this repo has a power button
   (`pimoroni_picolipo`), and one more (`waveshare_rp2040_plus`) *might* - its vendor documentation
   is unreachable and its own file calls the control "RESET/power", unresolved. One board is thin
   justification for a chip-level state plus an `ExternalDevice` protocol change.
2. 0089's **Phase 5** lands first. It is what makes a reset clear the pads/peripherals a real one
   clears; until then a power cycle would differ from a RUN reset only in a register value, which
   is precisely the kind of "faithful in the flag, wrong in the behaviour" modelling this project
   has been avoiding.
3. The `ExternalDevice` question (D7 / 0049) gets an answer that is not "add a callback because one
   device needs it".

Until all three, the honest state is what the board file now says: the Pico LiPo has a power button,
this emulator does not model it, and the reason is written down rather than approximated.
