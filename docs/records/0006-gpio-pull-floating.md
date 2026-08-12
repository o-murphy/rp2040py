# 0006. GPIO pull-up/pull-down resolution for undriven pins

- Status: Implemented
- Conceived: 2026-08-03 · #13
- Related: #13

<!-- migrated verbatim from docs/PORTING.md lines 214-258 -->

### GPIO pull-up/pull-down wasn't resolved into an actual bus reading for undriven pins

Found while debugging why `KalumaDevice(program=...)` never auto-executed even though the write to
flash was confirmably correct: `gpio_pin.py`'s `GPIOPin` (matching upstream rp2040js's `GPIOPin`
closely enough that this is very likely present there too - worth filing upstream) tracked
`pullup_enabled`/`pulldown_enabled` purely as pad-control-register metadata, never resolving them
into the actual bit `input_value`/`status` (and therefore firmware's `gpio_get()`) reads for a pin
nothing actively drives - `_raw_input_value` just stayed at its default `False` regardless of which
pull was configured, i.e. every undriven pin silently read low no matter what. Real hardware
resolves a floating, pulled-up pin to high.

This directly explained the Kaluma symptom: `km_running_script_check()` enables GP22's pull-up and
reads it to decide whether to skip auto-run (GP22 wired to GND is the documented "recovery mode"
signal) - an always-low reading is indistinguishable from "wired to GND," so auto-run silently
never ran, regardless of anything staged in the "user program" flash region.

Fixed by adding a `_driven` flag (set only by `set_input_value()`, the actual external-drive API -
button harnesses, other simulated peripherals) and a `_effective_raw_input_value` property that
falls back to resolving the enabled pull direction when the pin has never been driven, used in
`input_value`/`status` instead of the raw flag directly. `refresh_input()` (called whenever
`input_enable` toggles, e.g. from a normal `gpio_init()` config write - unrelated to anything
actually driving the pin) had to stop routing through `set_input_value()` for this to work - it
used to, and would otherwise have permanently marked every reconfigured pin as "driven" with its
stale default value the moment `input_enable` was toggled, defeating pull resolution immediately
for exactly the GP22 case this fixes. See `tests/test_gpio_pin.py`.

`start()`/`exec()`/`exec_file()` block the calling thread; each has an `_async` twin
(`start_async()`/`exec_async()`/`exec_file_async()`) returning a `concurrent.futures.Future`, plus
`astart()`/`aexec()`/`aexec_file()` for asyncio. All of these run as coroutines on the
`Simulator`'s own engine-room loop (`simulator.submit()`, `run_coroutine_threadsafe()` under the
hood - already returns a plain `concurrent.futures.Future`, no extra wrapping needed), serialized
by one `asyncio.Lock` per device: the device only has one REPL channel and can't run two `exec()`s
at once, so the lock gets FIFO queueing of overlapping calls the same way
`ThreadPoolExecutor(max_workers=1)` used to, for free. (Two earlier versions of this: first a
hand-rolled `deque` + a `Future`-per-call + a `threading.Timer` timeout watchdog; then a
`ThreadPoolExecutor(max_workers=1)` submitting plain blocking `threading.Event.wait()` calls -
replaced once `Simulator` got its own engine-room loop, see
`docs/ASYNCIO_MIGRATION_BACKLOG.md`'s phase 5: running on a worker thread neither of those first
two designs actually needed to exist raced `USBCDC.tx_fifo` against whatever thread was really
driving the simulator, the same class of bug PR 3 found and fixed for `cli/stdio_repl.py`.)
`concurrent.futures.TimeoutError` and `asyncio.TimeoutError` are each their own class, distinct
from the builtin `TimeoutError`, until Python 3.10 - `_result()`/`_await()` in `mp_device.py`
normalize all three to the builtin one so `except TimeoutError` behaves the same everywhere on the
3.10 floor this project supports.

