# 0044. DMA-driven SPI TX/RX hang: stale DREQ cache after reset + same-tick alarm starvation

- Status: Implemented — verified (2026-08-14)
- Conceived: 2026-08-14 · Implemented: 2026-08-14
- Related: 0027 (CYW43439/Pico W epic - `docs/tasks/main-spi-hang.md` was found investigating this
  epic's own live-boot work, then confirmed unrelated to CYW43), 0043 (`RPPIO` CTRL-enable
  first-batch/DMA-refill race - same "shared simulator infrastructure, not
  CYW43-specific" theme, explicitly checked and ruled a separate root cause from this one, but its
  own regression test's DMA-vs-consumer shape is the direct model for this record's test), 0039
  (`SimulationClock` native-Cython port - both implementations of `link_alarm()` needed the same
  fix), 0032 (docs restructure - this scheme)

## Context

```
uv run rp2040py --log-level error micropython --image v1.28.0 tests/micropython/main-spi.py
```

(raw-REPL exec mode, plain `RPI_PICO`) hung forever at 100% CPU with zero output - see
`docs/tasks/main-spi-hang.md` for the full "what's confirmed so far" investigation trail that
narrowed this down to `peripherals/spi.py`'s DMA-channel-completion path specifically, as opposed
to the plain `on_transmit`/`complete_transmit()` byte callback (already ruled out).

`tests/micropython/main-spi.py`'s third `spi.write()` call is 48 bytes - past
`machine_spi.c`'s own `dma_min_size_threshold` (32 bytes), so it's the only one of the script's
three writes that takes MicroPython's DMA-driven path (`machine_spi_transfer()`'s `use_dma`
branch): two DMA channels, one paced by `DREQ_SPI0_TX` feeding `SSPDR`, one paced by
`DREQ_SPI0_RX` draining it, both triggered together via `dma_start_channel_mask()`, then the
driver busy-polls `dma_channel_wait_for_finish_blocking()` on each - real ARM instructions
executing the whole time, matching the observed "100% CPU, not idle" symptom exactly.

## Two independent bugs, both required to hang

Reproduced directly at the register level (bypassing CPU/firmware entirely -
`tests/test_dma.py::test_spi_dma_paired_tx_rx_transfer_completes_without_listener` does this),
configuring the same paired TX/RX DMA channels `machine_spi_transfer()` does. Each bug below was
confirmed independently by holding the other fixed.

### Bug 1: `RPDMA.reset()`'s `self.dreq.clear()` discards a peripheral's already-correct DREQ level

`self.dreq: dict[int, bool]` mirrors each DREQ-capable peripheral's own live FIFO-occupancy signal
(`RPSPI._update_dma_tx()`/`_update_dma_rx()` and equivalents call `RPDMA.set_dreq()`/
`clear_dreq()` on every FIFO push/pop) - it is peripheral-owned state that `RPDMA` merely caches,
not state the DMA controller itself produces. `RPSPI.__init__` establishes the correct initial
level (`DREQ_SPI0_TX=True`, tx FIFO starts empty; `DREQ_SPI0_RX=False`, rx FIFO starts empty) -
but `RP2040.__init__` calls `self.reset()` (which calls `RPDMA.reset()`) *after* constructing
`self.spi`, and `RPDMA.reset()` used to end with `self.dreq.clear()`, wiping that just-established
`DREQ_SPI0_TX=True` back to a missing (falsy) entry. Nothing re-establishes it afterward - DMA's
own `schedule_transfer()` only fires when the DREQ transitions to `True` (or the TREQ is a timer/
`PERMANENT`), so the TX channel's very first byte transfer never got scheduled at all: `active`
stayed `True` (`EN|BUSY` both set) but `_trans_count` never moved, forever.

This is not upstream `rp2040js` behavior - its `RP2040`'s own `reset()` only resets `core`/`pwm`/
`flash`; `RPDMA` there has no controller-level `reset()` at all, so `dreq` is never touched
out-of-band. `dma.reset()`/`ppb.reset()` were added locally in `c6c0ab0` (littlefs-dump PR #24) to
support a real live watchdog-triggered `machine.reset()`, without preserving this ordering
requirement.

`tests/test_pio.py`'s `test_enabling_a_dma_fed_sm_does_not_run_steps_synchronously_when_a_simulator_owns_the_rp2040`
(0043) had already hit and documented this exact same gap for PIO's own construction-time
`DREQ_PIO0_TX0=True`, working around it with a manual `TXF0` nudge rather than fixing the root
cause - that test's comment is updated by this record's fix to stop describing it as still-current
(the nudge itself is left in place: harmless, and no longer required but not worth the risk of
re-deriving that test's exact pull-count expectations to remove it).

**Fix**: `RPDMA.reset()` no longer clears `self.dreq` - a DMA-controller reset doesn't touch any
peripheral's FIFO on real silicon, so it shouldn't fabricate a "nothing ready" DREQ level either.

### Bug 2: same-timestamp `SimulationClock` alarms fire LIFO, letting a self-rescheduling zero-delay producer starve an already-pending zero-delay consumer

With bug 1 fixed in isolation, TX now starts and completes all 48 bytes - but RX still hangs
partway through (confirmed: `active=True`, `_trans_count` stuck above 0 forever). `RPSPI`'s
default `on_transmit` (`lambda value: self.complete_transmit(0)`, unless a caller wires a
realistically-paced listener - `tests/micropython_spi_run.py`'s own CI harness does, at 2us/byte,
which is exactly why CI never exercised this path; raw-REPL exec never wires one) completes each
TX byte *synchronously and instantly*, with no real bit-clock timing modeled. Real PL022 hardware
can never let TX outrun RX like this - both shift registers advance on the same physical clock
edge - so nothing was ever paced by DMA transfer scheduling order there. In this simulator,
though, TX's own `RPDMAChannel.transfer()` reschedules itself via `schedule_transfer()` →
`ClockAlarm.schedule(0)` (zero delay - `DREQ_SPI0_TX` stays asserted the whole time, tx FIFO
never has more than one byte in flight) immediately after each byte, and that reschedule races
against RX's own just-triggered zero-delay alarm (fired from the same TX byte's cascading
`_fifos_updated()` → `set_dreq(RX)` the instant rx FIFO went from empty to non-empty).

`SimulationClock.link_alarm()`'s insertion loop (`while alarm_list_item and alarm_list_item.nanos
< alarm.nanos`) stops as soon as it reaches an existing entry at the *same* timestamp as the one
being inserted, inserting the new one *before* it - LIFO for ties. Every time TX rescheds, it
lands ahead of RX's already-queued same-tick alarm; RX's `ClockAlarm` object never moves once
linked, so it just keeps getting pushed one further slot back, in practice never firing until TX's
own `_trans_count` reaches zero and it stops rescheduling. By then the 8-deep rx FIFO has long
since overflowed (each further TX byte set `SSPRORINTR` and silently dropped the byte rather than
queuing it, once rx FIFO hit 8/8) - RX only ever gets to pull the first 8 bytes, then permanently
starves waiting for a `DREQ_SPI0_RX` re-assertion that will never come again (rx FIFO empties
below the point where more real data will ever arrive).

This exact same-tick LIFO tie-break is byte-for-byte identical in upstream `rp2040js`
(`SimulationClock.linkAlarm()`) - not a porting regression, a latent bug shared with upstream that
this specific combination (DMA-paired producer/consumer + an instant-completion default listener +
a transfer longer than the consumer's FIFO depth) had apparently never exercised there either.

**Fix**: `link_alarm()`'s tie-break loop condition changed from `<` to `<=` in both
`clock/_simulation_clock.py` and `native/_simulation_clock.pyx` (kept in lockstep per 0039) - an
alarm already scheduled for a given instant now always fires before one just inserted for that
same instant (FIFO, not LIFO), so RX always gets a fair turn interleaved with TX rather than being
perpetually cut in line. Verified this produces a clean 1-for-1 TX/RX interleave with zero
overruns for the reproduction case (not just "eventually still completes despite drops").

## Verification

`tests/test_dma.py::test_spi_dma_paired_tx_rx_transfer_completes_without_listener` (new) reproduces
the exact register-level shape `machine_spi_transfer()`'s DMA path uses and asserts both channels'
`TRANS_COUNT` reach 0 (`BUSY` clears) with real per-byte RX completions, not just "stopped being
busy" - confirmed to fail with either fix reverted individually (bug 1 alone: TX never starts,
`TRANS_COUNT` stuck at 48; bug 2 alone, with bug 1 fixed: RX stuck partway with an overrun set) and
pass with both.

Re-ran the exact repro from `docs/tasks/main-spi-hang.md`, 3 consecutive times, each completing
promptly instead of hanging:

```
$ uv run rp2040py --log-level error micropython --image v1.28.0 tests/micropython/main-spi.py
$ echo $?
0
```

Note: `uv sync` alone does not reliably rebuild the native Cython extension after editing a
`.pyx` file in this environment (observed directly - the compiled `.so`'s mtime didn't change,
and a `pre-commit run --all-files` pass "succeeded" against a stale pre-fix native build without
any indication of doing so). `uv sync --reinstall-package rp2040py` forces it. Anyone editing
`native/*.pyx` files should verify the `.so` mtime actually advanced, not just trust a green
`pre-commit` run - a stale native extension silently validates the *old* behavior.

`uv run pre-commit run --all-files` (mypy, ruff, pytest, both pure-Python and native-Cython
builds, native extension confirmed freshly rebuilt) passes clean.
