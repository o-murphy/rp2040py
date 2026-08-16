# 0052. XIP_CTRL: implement the registers, not the cache

- Status: Implemented (2026-08-16)
- Conceived: 2026-08-16
- Related: 0050 (found this block unimplemented while root-causing the CircuitPython 10.x stall,
  and established that an all-ones read of a status register is an active hazard, not a gap)

## Decision

Implement `XIP_CTRL` (`0x14000000`) as a register block with correct values, and **deliberately do
not model the cache**.

The cache is not worth emulating: this emulator reads flash directly, so there is no hit/miss
behaviour to reproduce, and no timing model that a cache would change. Emulating it would add state
and invalidation rules that nothing could observe.

The *registers* are worth having, for one reason: without them, `BasePeripheral` answers every read
with `0xFFFFFFFF`. Firmware polls this block during boot (enable the cache, flush it, wait for the
flush to complete), and all-ones means **every status bit reads as set**. That is precisely the
failure mode 0050 documented for `CHIP_RESET`, where a permanently-set `PSM_RESTART_FLAG` sent the
bootrom into a loop. A block that reads all-ones is worse than one that reads zero, and both are
worse than one that reads the datasheet's own reset values.

## Behaviour

- `CTRL` resets to `0x3` (EN | ERR_BADWRITE) and is writable.
- `FLUSH` reads `1` and writes are no-ops: a flush of nothing is already complete.
- `STAT` reads `FLUSH_READY | FIFO_EMPTY`, permanently - a flush that has nothing to flush is
  always ready, a streaming FIFO that never streams is always empty.
- `CTR_HIT`/`CTR_ACC` read `0` rather than inventing plausible counts. There is no cache, so there
  are no hits and no accesses; a fabricated number would be worse than an honest zero.
- `STREAM_ADDR`/`STREAM_CTR` store what is written (so read-back works); `STREAM_FIFO` reads `0`.
- Unknown offsets inside the block read `0`, not `0xFFFFFFFF` - see above.
- Writes past the register block are ignored rather than warned about. A real boot does write
  there (`+0x2000` was observed during CircuitPython's), and with no cache to maintain there is
  nothing for us to do about it. Note this record does **not** claim to know exactly what that
  window is - only that ignoring writes to it is right for an emulator with no cache.

## Not a fix for anything currently broken

Worth stating plainly: this did not unblock CircuitPython 10.x - 0050's QSPI pad defaults did, and
that was proven by ablation. The boot touched XIP_CTRL and survived it. This record closes a latent
trap rather than a live bug, on the same reasoning that kept 0050's `VREG_AND_CHIP_RESET` change.

## Verified

`tests/test_xip_ctrl.py` covers the reset values, immediate flush completion, `CTRL` write-back,
zeroed counters, the all-ones-avoidance for unknown offsets, and that writes past the block do not
fall through to the "undefined address" path. `pre-commit run --all-files` clean; CircuitPython
10.2.1 and MicroPython both still boot.
