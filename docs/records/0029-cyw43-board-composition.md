# 0029. CYW43439 board composition decision

- Status: Accepted
- Conceived: 2026-08-12
- Related: 0027 (epic)

<!-- migrated verbatim from docs/CYW43_WIFI_BACKLOG.md lines 511-651 -->

## Board composition decision (2026-08-11)

Resolves the earlier "opt-in mechanism" question, in light of a broader point: this project
will eventually want to emulate boards beyond Pico/Pico W too — other vendors' RP2040 boards
(Waveshare RP2040-Zero, YD-RP2040, ...) — and later other MCU silicon (RP2350/Pico 2). Two
independent axes (MCU variant, board-specific fixed extras) argue against solving this with
`RP2040` subclasses.

**Rejected: one `RP2040` subclass per board.** Most third-party RP2040 boards are the identical
die — they differ only in pin breakout and maybe one onboard extra (an RGB LED on a PIO-driven
pin, a different flash size), not in chip *behavior*. Subclassing per board crosses that with the
MCU-variant axis and the cyw43-or-not axis combinatorially (`Pico`, `PicoW`, `Pico2`, `Pico2W`,
`WaveshareZero`, `YD2040`, ...) for what's mostly metadata, not different emulation logic.

**Decision: composition, not inheritance — via an `ExternalDevice` component interface, not a
callable/string dispatch table.**

- `RP2040` itself stays **completely** unchanged — not even one new method. **Decided: a
  superstructure on top of the emulator, not a reconstruction of it.** `attach_external_devices()`
  is a plain standalone function, not an `RP2040` method, taking the MCU plus a variadic list of
  devices — named `attach_external_devices`, not the shorter `attach_devices`, specifically so
  it's unambiguous this is about `ExternalDevice`s:

  ```python
  def attach_external_devices(mcu: "RP2040", *devices: ExternalDevice) -> None:
      for device in devices:
          device.attach(mcu)
  ```

  Lives next to the `ExternalDevice` `Protocol` definition, not inside `_rp2040.py`. Callable
  anywhere, on any `RP2040` instance, board-registry-constructed or not — exactly the same
  "public extension point, no dependency on the board registry" property as calling
  `device.attach(mcu)` directly, just batched.

  **The pre-run-only constraint (see the dedicated subsection below) stays a documented contract,
  not a runtime-enforced check — deliberately.** `RP2040` has no "am I running" state today
  (`self.stopped`/`self.stopped = False` live entirely on `Simulator`, `simulator.py:63,169,184`;
  `RP2040` doesn't even hold a reference back to whichever `Simulator`, if any, owns it — a bare
  `RP2040()` isn't guaranteed to have one at all). Giving `attach_external_devices()` a real
  runtime guard would mean adding running-state to `RP2040` itself — exactly the reconstruction
  this decision avoids. So for now: call it before starting execution, documented, not asserted.
  Revisit if this ever causes real confusion in practice.
- A structural `Protocol` (mirrors the existing `Peripheral`/`BasePeripheral` pair in
  `peripherals/peripheral.py:36-45`, same idea applied outside `peripherals/`):

  ```python
  class ExternalDevice(Protocol):
      def attach(self, rp2040: "RP2040") -> None: ...
  ```

  `Cyw43439` (`external/cyw43/chip.py`) implements this structurally, same as peripherals implement
  `Peripheral` without explicit inheritance. Named `ExternalDevice`, deliberately not
  `Peripheral`/`PeripheralDevice` — this project's `Peripheral` already means "memory-mapped,
  `read_uint32`/`write_uint32`", and the whole point of the "Module layout decision" above is that
  CYW43439 is *not* that.
- A small board registry (e.g. `boards.py`) maps each `--board` **name** to a spec of which MCU
  class to instantiate (today only `RP2040`; later `RP2350`) and which already-constructed
  `ExternalDevice` **instances** to attach afterwards — `BoardSpec(mcu=RP2040,
  extras=[Cyw43439()])` for `pico_w`, `BoardSpec(mcu=RP2040, extras=[])` for plain `pico`. No
  separate string→class dispatch table anywhere in this: the only string involved is the one
  `--board` value itself, resolved once via a single lookup into a `BoardSpec` that already holds
  real class/instance references, not further string IDs to re-resolve per extra.
- Board setup = construct the MCU class, then `attach_external_devices(rp2040, *spec.extras)`.
- **This makes `attach()`/`attach_external_devices()` a public extension point, not just internal
  board-preset plumbing.** Any user of the library can wire up their *own* custom hardware onto a
  manually constructed `RP2040` the same way built-in boards do — either one device at a time
  (`MyCustomDevice().attach(rp2040)`) or a batch (`attach_external_devices(rp2040, ...)`) — with no
  dependency on the board registry at all.
  This also retroactively formalizes what `demo/components/virtual_eink.py` already does today, ad
  hoc and without a common interface (raw `GPIOPin.add_listener()`/`RPSPI.on_transmit` wiring): a
  future `ExternalDevice`-shaped rewrite of that demo would use the same interface as `Cyw43439`.
- **CLI:** `--board` (not `--mcu`/`--variant`), `choices=["pico", "pico_w"]` for now — it selects a
  *board*, and board is the right level even once other vendors' boards are added, since most of
  them don't change the MCU variant at all. Extends by adding more `choices` values
  (`waveshare_rp2040_zero`, `yd_rp2040`, later `pico2`/`pico2_w`) and matching registry entries —
  no new flag, no migration of `--board` itself.
- **Decided: any board customization beyond the built-in `--board` presets is API-only, not CLI,
  for now.** This covers both ends of the same spectrum: one extra device layered on top of a
  preset (`pico_w` plus something else), and an entirely custom board — a hand-picked set of
  `ExternalDevice`s that doesn't match any built-in preset at all. `--board` only ever selects from
  its fixed `choices`; there is no `--attach <arbitrary device>` CLI flag and no way to name a
  custom board from the command line, and neither is planned as part of this work. A user embedding
  `rp2040py` as a library already has the full mechanism available directly — construct `RP2040()`
  and call `attach_external_devices(rp2040, MyDevice(...), ...)` / `MyDevice(...).attach(rp2040)`
  with whatever combination of devices their custom board needs — since `attach()`/
  `attach_external_devices()` are public API regardless of how the `RP2040` was constructed or
  whether it came from the board registry at all. Exposing *that* from the CLI (naming a custom
  board or an arbitrary device as a CLI string, expressing per-device constructor kwargs, the
  arbitrary-code-execution surface of a CLI flag that dynamically imports and instantiates
  user-named code) would need real, separate design — a distinct future feature, left undesigned
  here, not something this work is blocked on or needs to solve.

**Summary of the above, stated as one design principle:** the CLI (`--board`'s fixed `choices`) is
a convenience layer, never the ceiling. `ExternalDevice.attach()`/`attach_external_devices()` (and,
later, `detach_external_devices()`) are the real mechanism underneath every built-in board too —
the board registry is just one caller of that public API, not a gate on it. So a user who finds
`--board`'s presets insufficient, or who doesn't want to go through the CLI at all, was never
actually blocked: they construct `RP2040()` directly and attach whatever combination of built-in
or custom `ExternalDevice`s they want, themselves, through the same standalone function the board
registry itself calls internally.

### `attach()`/`attach_external_devices()` timing: pre-run only, for now

**Constraint, confirmed against the actual implementation:** `attach()` (and therefore
`attach_external_devices()`) is only safe to call *before* the Simulator starts running — i.e. as
part of board setup, right after constructing `RP2040` and before `Simulator.start_execution()`.
Not a conceptual choice, a real race: `GPIOPin._listeners` is a plain unsynchronized `set()`
(`gpio_pin.py:103`), mutated by `add_listener()` (`:313-315`) and iterated on every pin value
change (`:299`) — and that iteration runs on the Simulator's dedicated engine-room thread
(`simulator.py:69-73`). Calling `add_listener()` from any other thread (CLI, test, GDB connection)
while a pin-change iteration is in flight races against it — `RuntimeError: Set changed size
during iteration` at best, undefined behavior at worst. This also matches real hardware semantics:
what's wired to the chip is fixed at power-on, not hot-pluggable mid-execution.

**Future hot-attach path, if ever needed:** not a dead end. The project already has an established,
tested bridge for exactly this class of problem — synchronous outside callers reaching safely into
engine-room state via `run_coroutine_threadsafe`/`call_soon_threadsafe`
(`simulator.py:88-116`, the same mechanism the CLI/tests/GDB connections already use).
A later hot-attach feature would marshal `attach()` onto the engine-room loop the same way, rather
than inventing new synchronization primitives. Out of scope for now.

**Confirmed (2026-08-11): not adding a `running` flag to `RP2040` now just to gate this check.**
Given `RP2040` has no such state today (see the "superstructure, not reconstruction" note above),
bolting one on purely to support a pre-run-only assertion would be the exact reconstruction this
whole design has been avoiding — for a check that's cheap to just document instead. Left as-is:
call `attach_external_devices()`/`attach()` before starting the simulator, documented, not
enforced.

**When hot-plug is actually wanted, don't just add the running-check gate — rethink attach/detach
to be safe regardless of run state.** The real fix isn't "block attach() while running," it's
making attach()/detach() genuinely safe to call at any time — most likely by routing them through
`schedule_threadsafe()` (see "Concurrency model" below) so they always execute on the engine-room
thread regardless of which thread calls them, the same way any other cross-thread engine-room
mutation already has to. That also removes the *need* for a running/not-running distinction at the
API level entirely: attach and detach both "just work," whether the simulator has started or not,
once they're marshaled onto the right thread. This is also the point where `detach_external_devices()`
design becomes genuinely useful beyond the virtual-serial-device case already noted in "Open
questions" — it lets tests inject a mid-run peripheral dropout/failure and observe how the emulated
controller (and firmware running on it) actually reacts, closer to real-world fault-injection
testing than anything possible with attach-only, pre-run-only wiring.

