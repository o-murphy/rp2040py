# Task: `main-spi.py` hangs at 100% CPU, never reaches the REPL

**Resolved 2026-08-14 — root cause + fix in
[docs/records/0044-spi-dma-tx-rx-starvation-fix.md](../records/0044-spi-dma-tx-rx-starvation-fix.md).**
Kept below as-is (investigation history), not rewritten - see that record for what actually
happened: two independent bugs stacked (RPDMA.reset() discarding RPSPI's already-correct
construction-time DREQ level, and SimulationClock's same-timestamp alarm tie-break letting a
self-rescheduling DMA TX channel perpetually starve its own paired RX channel).

Not a `docs/records/` entry - a working note for whoever picks this up next. Found while
investigating [0027](../records/0027-cyw43-wifi.md)'s CYW43/pico_w wild-execution bug, but
**genuinely unrelated to CYW43** - reproduces on plain `RPI_PICO`, no `pico_w`/`Cyw43439`
involvement at all. Not yet root-caused.

## Repro

```
uv run rp2040py --log-level error micropython --image v1.28.0 tests/micropython/main-spi.py
```

(raw-REPL exec mode, plain `RPI_PICO`). Hangs at 100% CPU, no output at all - still running past
90 real seconds when last checked (2026-08-14, re-confirmed post-[0043](../records/0043-pio-dma-first-batch-race.md)
below), not confirmed whether it ever terminates.

## What's confirmed so far

- **Not the wild-execution bug** ([0035](../records/0035-board-aware-fs-flash-offset.md)) - a
  bounded trace of the same image+script shows only the same 2-3 benign out-of-range hits a normal
  `pico` boot shows.
- **Not a general raw-REPL exec-mode issue** - a trivial `print("hi")` script (no SPI, no
  littlefs) via the identical invocation completes in 0.4s, clean. The hang needs `main-spi.py`'s
  own content specifically.
- **Not (at least not simply) a missing SPI completion listener** - `peripherals/spi.py`'s
  `RPSPI.on_transmit` defaults to `lambda value: self.complete_transmit(0)`, i.e. every SPI byte
  self-completes synchronously unless something overrides it, so "nothing calls
  `complete_transmit()` without an explicit listener" doesn't hold as stated (confirmed by reading
  the source, not just running it). The real mechanism is still unidentified - possibly
  DMA-channel-completion (a separate path from the plain `on_transmit`/`complete_transmit()` byte
  callback) behaves differently; not yet checked.
- **100% CPU, not idle** - real instructions execute throughout (unlike a genuine
  wait-forever-for-an-event condition, which would show near-zero CPU) - consistent with either a
  true infinite busy-loop polling a status flag that never sets, or a very slow but eventually-
  converging one (not distinguished - no run has been let go longer than ~90s).
- **Not a main-thread-asyncio-migration regression** - reproduces on `origin/main` and
  `refactor/main-thread-asyncio` both, ruling out an initial suspicion that this might be the same
  class of cross-thread `tx_fifo` race [0018](../records/0018-raw-repl-txfifo.md) already fixed
  once.
- **Never exercised by CI as this exact code path** - `ci-micropython.yml` embeds `main-spi.py` as
  `main.py` via `mklittlefs` and drives it through `tests/micropython_spi_run.py` (which *does*
  wire a real `on_transmit`/`complete_transmit()` listener, on a delay matching real SPI clock
  timing) rather than raw-REPL exec - so this may be a previously never-exercised combination, not
  a new regression in the ordinary sense.
- **Not fixed by [0043](../records/0043-pio-dma-first-batch-race.md)** (2026-08-14) - that record
  fixed a real PIO/DMA-fed-FIFO synchronous-first-batch race in `peripherals/pio.py` that turned
  out to explain a *different*, CYW43-specific v1.23.0 regression. Worth checking given both bugs
  are shaped like "PIO/DMA timing," but re-running the repro above after 0043 landed still hangs
  the same way (90s timeout, zero output) - genuinely a separate issue, not the same root cause.

## Where to look next

- `peripherals/spi.py`'s DMA-channel-completion path specifically (as opposed to the plain
  `on_transmit`/`complete_transmit()` byte callback already ruled out above) - not yet checked at
  all.
- A stack trace / PC sample while it's spinning would likely settle "genuine infinite busy-loop
  polling a flag that never sets" vs. "slow but eventually converging" quickly - not yet attempted
  for this specific bug (0027's freeze investigation hit `ptrace_scope` restrictions trying this
  for a different, since-fixed bug - see [0041](../records/0041-cyw43-post-data-header-freeze-fix.md);
  same restriction likely applies here unless the environment's `ptrace_scope` policy has since
  changed).
- A real, populated MicroPython checkout with matching submodules now exists at
  `/home/murphy/pyproj/micropython` (read-only - it's a separate project, not part of this repo)
  which resolved a similar "no source to compare against" gap for 0042/0043's own investigations -
  worth checking `main-spi.py`'s real driver-side SPI/DMA polling loop against that source rather
  than guessing from `rp2040py`'s own behavior alone.

## Don't re-derive

- The four bullet points under "What's confirmed so far" above - re-confirmed multiple times
  already, no need to re-check unless something about the codebase changes in a way that would
  invalidate them.
