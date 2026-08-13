# 0043. `RPPIO` CTRL-enable first-batch/DMA-refill race (breaks MicroPython v1.23.0's CYW43 boot)

- Status: Implemented — verified (2026-08-13)
- Conceived: 2026-08-13 · Implemented: 2026-08-13
- Related: 0027 (CYW43439/Pico W epic - where this was found, comparing a live `v1.23.0` boot
  against the already-working `v1.28.0` one), 0037 (`RPPIO`/CPU scheduling - this record's own
  direct predecessor and same "shared simulator infrastructure, not CYW43-specific" theme; this is
  a residual gap in that fix's own "first batch" shortcut, not a new class of bug), 0042
  (`SPI_INTERRUPT_REGISTER` W1C fix - unrelated, landed concurrently in the same session, both
  independently found while auditing real CYW43 live-boot chatter)

## Context

```
uv run rp2040py --log-level error micropython --board pico_w --image v1.23.0 tests/micropython/main-cyw43.py
```

used to fail partway through:

```
Initializing...
active: False
Scan for networks
Traceback (most recent call last):
  File "<stdin>", line 14, in <module>
OSError: [Errno 1] EPERM
```

`nic.active(True)` (line 11) prints `False` right after being called - no exception, just silently
never came up - and `nic.scan()` (line 14) then raises `EPERM`. The exact same script against
`--image v1.28.0` worked completely differently: `active()` printed `True`, `scan()` returned a real
result, `connect()` ran the full scripted link-layer join sequence to `CYW43_LINK_NOIP`. So
`v1.28.0` worked and `v1.23.0` didn't - a real, version-specific regression, not a WiFi-emulation
gap common to both.

## First finding: `active: False` for v1.23.0 is *not* a bug on its own

MicroPython's `extmod/network_cyw43.c` changed its own `active()` getter between these two
releases (`git diff v1.23.0 v1.28.0 -- extmod/network_cyw43.c`, in a separate, real MicroPython
checkout on this machine with matching submodules - `/home/murphy/pyproj/micropython`, read-only,
not part of this repo):

- `v1.23.0`: `return mp_obj_new_bool(cyw43_tcpip_link_status(self->cyw, self->itf));` - reflects the
  *actual WiFi link status* (down/join/noip/up), not "is the interface up."
- `v1.28.0`: `return mp_obj_new_bool(if_active[self->itf]);` - a separate, explicitly-tracked flag
  set unconditionally the instant `active(True)`/`active(False)` is called, regardless of whether
  bring-up actually succeeded.

Right after `active(True)` and before any `connect()`, the link is legitimately still down
(`CYW43_LINK_DOWN`) on real hardware too - `v1.23.0`'s `active()` printing `False` here is expected,
if surprising, upstream API behavior for that release, not something this emulator got wrong. The
real bug is downstream: `scan()` raising `EPERM`.

## Root cause: `cyw43_wifi_scan()`'s own `itf_state == 0` gate, and why `itf_state` was never set

`cyw43_wifi_scan()` (`cyw43_ctrl.c`, unchanged between the two driver versions - confirmed via
`git diff` of the pinned `lib/cyw43-driver` commits, see below) starts with:

```c
if (self->itf_state == 0) {
    return -CYW43_EPERM;
}
```

`itf_state` only gets set inside `cyw43_wifi_set_up()`, and only *after* `cyw43_wifi_on()` (which
calls `cyw43_ll_bus_init()`, the whole ALP/HT/firmware-download/backplane bring-up sequence)
returns success. `cyw43_wifi_set_up()` itself is `void` - if `cyw43_wifi_on()` fails, it just
`return`s, with no error surfaced to Python at all, matching the observed silent failure exactly.

So the real question became: why does `cyw43_ll_bus_init()` fail for `v1.23.0` but not `v1.28.0`,
given both link an almost-identical `cyw43-driver` (`git diff` between the two pinned commits -
`9f6405f0` for `v1.23.0`, `055d6427`/v1.1.1 for `v1.28.0` - touches `cyw43_ll_wifi_join()`'s WPA3
support, a `cyw43_spi.c`/`cyw43_ll.c` file split, and one polarity-neutral-for-Pico-W
`host_interrupt_pin_active` refactor, but *nothing* in `cyw43_ll_bus_init()`'s own backplane/ALP/HT/
firmware-download logic)?

**Live wire-level tracing (temporary `print(..., file=sys.stderr)` instrumentation in
`GSPIBus.read_register()`/`write_register()`/`_on_cs_change()`/`_on_clock_rising()`'s header decode
- this file's own established throwaway-debug pattern per 0038/0041/0042, reverted before landing)
found the answer at the *wire* level, not the driver-source level**: `v1.23.0`'s boot produces
**zero** `CONTROL_HEADER` (ioctl) writes over F2 ever - the driver never even gets that far. The
trace showed real ALP/HT/backplane/firmware-download traffic starting normally, then, immediately
after the very first large (68-byte/17-word) firmware-download block write begins
(`cyw43_download_resource()`'s `cyw43_write_bytes(BACKPLANE_FUNCTION, ...)`), CS deselects
mid-transaction - only 3 of the required 17 words clocked in - and every subsequent header decode
is garbage (nonsensical function/address/size combinations), a permanent desync. `cyw43_ll_bus_init()`
eventually times out waiting for `SBSDIO_HT_AVAIL` (1000 iterations of a 1ms-delay poll, resolved
near-instantly by the simulated clock - explaining why the failure produces no visible hang) and
returns `-CYW43_EIO`, which is exactly what leaves `itf_state == 0`.

**This is not a `GSPIBus` decode bug** - `_on_cs_change()`/`_on_clock_rising()` correctly, faithfully
report whatever CS/clock signal they're actually given; the signal itself was wrong. The real cause
is a genuine timing gap in this simulator's own PIO/DMA co-simulation, one directory up from
`external/cyw43/`:

`RPPIO.write_uint32()`'s `CTRL` branch (`peripherals/pio.py`) - the code that runs when
`pio_sm_set_enabled(true)` is written - used to call `self._step_batch()` (up to 1000 PIO `step()`
calls) **synchronously, inline, inside the MMIO write itself**, whenever a stopped PIO instance
transitioned to running (0037's own "first batch runs synchronously inline" shortcut, kept as a
convenience for callers reading FIFO/register state immediately after the write). Nothing calls
`SimulationClock.tick()` during that inline burst - `tick()` only ever runs from
`_execute_batch.py`/`native/_simulator.pyx`'s own outer per-CPU-instruction loop. A DMA-fed PIO TX
FIFO (4 words deep) drains in about 8 `step()` calls for `cyw43_bus_pio_spi.pio`'s bit-at-a-time
`spi_gap01_sample0` program - far short of the 1000-step ceiling - so that inline burst can outrun
`RPDMAChannel`'s own alarm-paced refill (`peripherals/dma.py`, correctly gated on
`SimulationClock` alarms that need `tick()` to fire) and leave the state machine genuinely, but
*prematurely*, `FDEBUG_TXSTALL`'d, with most of a larger transfer still undelivered.

**Why only `v1.23.0` is affected, confirmed from the real pico-sdk source** (also pinned per
MicroPython release, `git diff` between the two commits recorded in `lib/pico-sdk`'s own history -
`6a7db34f` for `v1.23.0`, `a1438dff` for `v1.28.0` - on
`src/rp2_common/pico_cyw43_driver/cyw43_bus_pio_spi.c`): `cyw43_spi_transfer()`'s TX-only branch
changed the order of two calls.

`v1.23.0`'s pico-sdk:
```c
dma_channel_configure(bus_data->dma_out, &out_config, &bus_data->pio->txf[bus_data->pio_sm], tx, tx_length / 4, true);
uint32_t fdebug_tx_stall = 1u << (PIO_FDEBUG_TXSTALL_LSB + bus_data->pio_sm);
bus_data->pio->fdebug = fdebug_tx_stall;
pio_sm_set_enabled(bus_data->pio, bus_data->pio_sm, true);
while (!(bus_data->pio->fdebug & fdebug_tx_stall)) {
    tight_loop_contents();
}
```

`v1.28.0`'s pico-sdk:
```c
dma_channel_configure(bus_data->dma_out, &out_config, &bus_data->pio->txf[bus_data->pio_sm], tx, tx_length / 4, true);
pio_sm_set_enabled(bus_data->pio, bus_data->pio_sm, true);
dma_channel_wait_for_finish_blocking(bus_data->dma_out);
uint32_t fdebug_tx_stall = 1u << (PIO_FDEBUG_TXSTALL_LSB + bus_data->pio_sm);
bus_data->pio->fdebug = fdebug_tx_stall;
while (!(bus_data->pio->fdebug & fdebug_tx_stall)) {
    tight_loop_contents();
}
```

Both versions enable the SM (triggering the same inline 1000-step burst) and both are equally
exposed to the same premature-stall race *during* that burst. The difference is what happens next:
`v1.23.0` goes straight into the `PIO.FDEBUG` poll loop, so it directly observes whatever
(possibly premature) stall state the burst already left behind. `v1.28.0` inserts
`dma_channel_wait_for_finish_blocking()` first - a busy-wait on the *DMA channel's own* transfer-
count/busy status, executed as ordinary CPU instructions that `_execute_batch()`'s outer loop
*does* correctly interleave with `clock.tick()` - so by the time `v1.28.0` ever reaches the same
`FDEBUG` poll, the transfer has already genuinely finished for real (any stalled state machine
resumed once the outer loop's own ticking let DMA's pending alarm land, un-stalling it via
`StateMachine.write_fifo()`'s existing `waiting`-clearing side effect). `v1.28.0` masks the exact
same race rather than avoiding it - not a version-specific bug in the driver logic itself, a
version-specific difference in how exposed each one is to a pre-existing simulator gap.

## Fix

`RPPIO.write_uint32()`'s `CTRL` branch (`src/rp2040py/peripherals/pio.py`) now only runs the first
batch synchronously (`self._step_batch()`) when `self.rp2040.simulator is None` - the same
distinction the very next few lines already use to decide whether a continuation task is needed
(`tests/test_pio.py`'s no-owning-`Simulator` fixture, the only other caller of `RPPIO` driven
without an outer per-instruction loop, still needs the inline burst - there's nothing else to defer
stepping to). When a real `Simulator` owns the `RP2040` (every live boot), the write just flips
`self.stopped = False` and returns; `_execute_batch()`'s own existing "step every non-stopped
`RPPIO` once per CPU instruction" loop (0037) picks the newly-enabled state machine up on its very
next iteration - exactly how 0037's own ">1000-step continuation" already had to work - guaranteeing
`clock.tick()` runs between every single PIO step whenever it matters, so a DMA-fed FIFO can never
be observably outrun. No native-Cython counterpart exists for `RPPIO`/`peripherals/pio.py` (unlike
`peripherals/state_machine.py`/`_state_machine.py` or `_rp2040.py`) - one file to change.

A regression test, `test_enabling_a_dma_fed_sm_does_not_run_steps_synchronously_when_a_simulator_owns_the_rp2040`
(`tests/test_pio.py`), reproduces the exact shape: a real `Simulator()` (not the `cpu`
no-owning-`Simulator` fixture the rest of that file uses), a 2-instruction `pull`/`jmp` PIO0 SM0
program (no GPIO/pin config needed), and a DMA channel feeding it 9 words (more than the 4-word
FIFO) the same way `cyw43_spi_transfer()`'s TX-only branch does. Confirmed to fail without the fix
(`machine.cycles == 1000` immediately after the `CTRL`-enable write - the full synchronous burst
ran) and pass with it (`machine.cycles == 0` immediately after that same write; the transfer then
completes correctly, `DMA CTRL_TRIG`'s `BUSY` bit clears and `TRANS_COUNT` reaches 0, once genuinely
driven via `_execute_batch()`).

## Verification

Re-ran the exact live-boot command from the bug report, both versions:

```
$ uv run rp2040py --log-level error micropython --board pico_w --image v1.23.0 tests/micropython/main-cyw43.py
Initializing...
active: False
Scan for networks
[(b'RP2040PY-GUEST', b'B\x137U\xaa\x01', 6, -87, 0, 1)]
Connected False
None
b'\x00\x00\x00\x00\x00\x00'
('0.0.0.0', '255.255.255.0')
```

`scan()` now returns the real fake AP (previously raised `EPERM` before ever reaching this line);
`active: False` remains, correctly, per this record's own first finding (v1.23.0's own `active()`
semantics, not a bug). A fuller `MicroPythonDevice`-driven harness (mirroring
`docs/tasks/cyw43-3g-live-boot-verification.md`'s own script) confirms `connect()` also now reaches
`status() == 2` (`CYW43_LINK_NOIP`) and stays there with `isconnected()` staying `False` - the exact
same outcome already confirmed for `v1.28.0` in that task's own findings (full `isconnected() ==
True` needs a real DHCP lease - step 4's NAT bridge, not built yet, and out of scope here).

```
$ uv run rp2040py --log-level error micropython --board pico_w --image v1.28.0 tests/micropython/main-cyw43.py
Initializing...
active: True
Scan for networks
[(b'RP2040PY-GUEST', b'B\x137U\xaa\x01', 6, -87, 0, 1)]
Connected False
None
b'\x00\x00\x00\x00\x00\x00'
('0.0.0.0', '255.255.255.0')
```

`v1.28.0` output is byte-for-byte unchanged from before this fix - no regression.

`tests/test_pio.py` (36 tests, including the new one) and `tests/test_cyw43_bus.py` (44 tests, per
0042's own count - unaffected by this fix, since `GSPIBus` never sees the difference between a
correctly- and prematurely-terminated CS transaction, only the resulting signal) both pass; full
`uv run pre-commit run --all-files` (mypy, ruff, pytest, both pure-Python and native-Cython builds)
passes clean.
