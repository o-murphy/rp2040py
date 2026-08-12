# PORTING.md — moved

Port checklist → [docs/reference/porting-checklist.md](reference/porting-checklist.md).
Known differences split into numbered records (see [0000-TRACKER.md](0000-TRACKER.md),
[0032](records/0032-docs-restructure.md)).

| Old section | Now |
|---|---|
| Port checklist | [reference/porting-checklist.md](reference/porting-checklist.md) |
| Known differences from rp2040js | [reference/porting-checklist.md#known-differences-from-rp2040js](reference/porting-checklist.md#known-differences-from-rp2040js) |
| CLI packaging (no rp2040js equivalent) | [0001](records/0001-cli-device-api.md) |
| GPIO pull-up/pull-down for undriven pins | [0006](records/0006-gpio-pull-floating.md) |
| Threading model | [0014](records/0014-threading-model.md) |
| Raw-REPL cross-thread `tx_fifo` bug | [0018](records/0018-raw-repl-txfifo.md) |
| `pio_assembler.py` argument order | [reference/porting-checklist.md](reference/porting-checklist.md) |
| littlefs image format vs. old MicroPython | [0003](records/0003-littlefs-image-format.md) |
| `mklittlefs` corruption / PyPy crash | [0002](records/0002-mklittlefs-image.md) |
| Performance: pure-Python vs V8 | [0017](records/0017-perf-python-vs-v8.md) |
| `RPWatchdog` reset | [reference/porting-checklist.md](reference/porting-checklist.md) |
| Configurable bootrom revisions | [0007](records/0007-bootrom-revisions.md) |
| External serial-tool passthrough | [0020](records/0020-pty-serial-passthrough.md) |
