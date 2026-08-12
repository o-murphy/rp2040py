# 0037. Couple `RPPIO` stepping to the CPU's own instruction loop

- Status: Implemented — measured (2026-08-13)
- Conceived: 2026-08-13
- Related: found while investigating 0027's `nic.active(True)` livelock; follow-up of 0031 (PIO
  Cython port + tick batching) and 0034 (`_execute_batch()` native port) - same "shared simulator
  infrastructure, not CYW43-specific" theme as those two

## Context

0027's own "`nic.active(True)` hang" investigation (2026-08-12/13) traced the CPU's hot PC on a
real, unmodified `v1.28.0` `RPI_PICO_W` boot and found it disassembles (`arm-none-eabi-objdump`
against a UF2→flat-binary conversion, cross-checked live against `mcu.core.pc`/register snapshots)
to exactly pico-sdk's `dma_channel_abort()` body - real firmware's `cyw43_bus_pio_spi.c` transfer
helper chains two DMA channels to a PIO1 SM0 gSPI bit-bang program, then busy-waits and retries.
`RPDMAChannel.transfer()`/`schedule_transfer()` (`peripherals/dma.py`) is correctly paced via
`SimulationClock` alarms - the same clock the CPU advances every instruction via `clock.tick()` in
`Simulator._execute_batch()`/`native/_simulator.pyx`. But `RPPIO.run()`
(`peripherals/pio.py`) - the loop that steps a PIO program forward past its first synchronous
1000-instruction batch - was a **separate, competing `asyncio.Task`** on the same single-threaded
engine-room loop, entirely decoupled from that clock/instruction cadence.
`_execute_batch()` runs up to 1,000,000 CPU instructions (or a real-time budget) before its *one*
`await asyncio.sleep(0)` yield, so in native (Cython) mode - CPU dispatch orders of magnitude
faster than pure Python - the CPU could race through many `dma_channel_abort()`+retry cycles for
every scheduling turn the PIO task got. Confirmed live, not guessed: sampling `mcu.pio[1].
machines[0].pc`/`.cycles` every few seconds against the real boot showed `pc` frozen at `27` and
`cycles` cycling through the same three values (260/580/900) for a full 120s run - a genuine
livelock, not merely slow. (An earlier same-session trace using `RP2040PY_SKIP_CYTHON=1` - forced
pure Python, so Python-level monkeypatches would work - showed real, if very slow, forward
progress instead; that trace undersold the severity because forcing pure Python also happened to
narrow the CPU/PIO speed gap enough to mask the livelock. Native mode, the real default path, does
not have that luck.)

## Decision

Make `RPPIO` stepping driven directly by the same loop that already advances the shared simulated
clock once per CPU instruction/idle-jump - the same "no competing task, just called inline" shape
`clock.tick()` itself already has - instead of a free-running `asyncio.Task`. Two changes, kept
minimal and low-risk given this touches the single hottest loop in the whole simulator:

1. **`peripherals/pio.py`'s `write_uint32()` CTRL branch** only creates the continuation task
   (`self._run_task = asyncio.get_running_loop().create_task(self.run())`) when
   `self.rp2040.simulator is None` - i.e. only for the no-owning-`Simulator` case
   (`tests/test_pio.py`'s `_LoopBoundDriver` fixture, the only other caller of `_run_task`/`run()`/
   `.stopped`, confirmed via `grep`). `run()`/`_step_batch()`/`stop()` themselves are unchanged -
   still real code, still exercised by that fixture. Mirrors the existing
   `RP2040.schedule_threadsafe()` pattern of branching on `rp2040.simulator` to distinguish "owned
   by a real Simulator" from "driven directly."
2. **`_execute_batch.py` and `native/_simulator.pyx`** (kept in sync by hand, the established
   pattern for this pair - see 0034) each grab `pios = rp2040.pio` once before the loop, then once
   per loop iteration (after the existing idle-jump/busy-instruction branch, so both paths cover
   it - real PIO keeps running independent of CPU sleep state):
   ```python
   for pio in pios:
       if not pio.stopped and _has_runnable_machine(pio):
           pio.step()
   ```
   `_has_runnable_machine(pio)` (a small per-file helper - see below, added in a same-day
   follow-up, not the version that first landed) returns `True` only if at least one machine is
   `enabled and not waiting`. `RPPIO.step()`'s existing behavior (steps all 4 machines, calls
   `check_changed_pins()`) is reused as-is, no new state added to `RP2040`.

   **Follow-up, same day: the first version of this check (`if not pio.stopped: pio.step()`, no
   `_has_runnable_machine()`) was itself a real, measured performance regression**, found while
   investigating why 0027's `nic.active(True)` still took too long after this record's own fix
   landed. A PIO instance whose only machine is permanently `waiting=True` (e.g. the stalled
   write-only gSPI transaction 0027's own follow-up entry describes) never gets `pio.stopped` set
   - a real hardware SM doesn't need to be explicitly disabled just because it's stalled, `stopped`
   only reflects the `CTRL` register's `should_run` bits - so the original check paid full
   `RPPIO.step()`/`check_changed_pins()` cost on every single subsequent CPU instruction for a
   machine that could not possibly make progress. Measured directly: `cProfile` scoped to the
   post-stall phase of a real boot showed `pio.py`'s `step()`/`check_changed_pins()` at ~55% of a
   20s profiled window (14.37M calls), `peripherals/usb.py` (the first, wrong hypothesis for the
   remaining slowness) at ~0.02s of the same window. Confirmed every `StateMachine` wait type
   already has its own targeted, event-driven re-check elsewhere (`RPPIO.irq_updated()` for `IRQ`,
   `GPIOPin._apply_input_value()` for `PIN`, `StateMachine.read_fifo()`/`write_fifo()` for
   `RX_FIFO`/`TX_FIFO`/`OUT`) that flips `waiting` back to `False` the instant whatever it's
   blocked on changes - so skipping `step()` for an all-waiting PIO instance loses nothing; the
   loop picks it back up on its very next iteration once an external event actually resolves the
   wait. `_has_runnable_machine()` is defined identically (structurally) in both
   `_execute_batch.py` and `native/_simulator.pyx`, iterating `pio.machines` and checking
   `machine.enabled and not machine.waiting`.

## Verification

- Native-mode live re-trace of the same `nic.active(True)` scenario: `sm0.pc`/`.cycles`/`tx_fifo`
  now genuinely advance (multiple real gSPI transactions complete - `cycles` reaches values well
  past the pre-fix ceiling of 900, `pc` moves through 0/26/27, `tx_fifo` fills and drains) for
  roughly the first 40s of a boot, instead of being frozen from the first few seconds as before.
  The livelock this record set out to fix is gone. (A separate, narrower stall was found further
  into the same boot after this fix landed - not this record's scope, see 0027's own follow-up
  entry.)
- `uv run rp2040py bench --instructions 5000000`, several runs before/after (both the initial fix
  and the `_has_runnable_machine()` follow-up): ~21-22M instructions/sec throughout - no
  measurable regression to the non-PIO hot path.
- `uv run pre-commit run --all-files` - clean (mypy, ruff, pytest on both the pure-Python and
  native builds), re-run after the follow-up fix too. `tests/test_pio.py`'s no-`Simulator` path
  and `tests/test_simulator.py`/`tests/test_cyw43_bus.py`'s `Simulator`-owned path between them
  exercise both branches of the `pio.py` change.
- Post-follow-up: re-profiled the same post-stall boot phase - `pio.py`'s `step()` cost dropped
  from ~55% of the profiled window to negligible, confirming the regression is actually gone, not
  just less visible.
- **What this record's fix does and does not claim, precisely, after the follow-up**: the original
  livelock (PIO frozen from the first few seconds, `cycles` capped at 900 forever) is fixed and
  stays fixed - confirmed via a fresh live trace against a locally-built `v1.28.0` firmware with
  real debug symbols (0027's own later entries explain why a locally-matched build was needed).
  `nic.active(True)` on that same firmware still does not complete within 10 real minutes even
  after both fixes here - but the CPU's PC keeps varying throughout that time (genuine execution,
  not a second livelock), and the cause is a separate, already-identified raw-throughput ceiling
  (`cyw43_delay_ms()`'s busy-wait needing more real wall-clock time to advance simulated
  milliseconds than the delay itself represents) - not a scheduling or correctness bug, and not
  this record's scope. See 0027's own final entry for the full detail and why it's a distinct,
  larger, not-yet-scoped follow-up.
