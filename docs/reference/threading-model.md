# The threading model: the engine runs on the caller's thread

Record [0096](../records/0096-cpp-mcu-core.md) (the progress-log entries of 2026-10-05 on the GIL, `pump()` and the threadless mode) has the history and the
measurements; this page is what to know to *use* it. Since the entry "the engine runs without a thread by default" a `Simulator` has **no thread of its own**
unless you ask for one.

## The rule

The simulation advances only while something drives the engine's event loop **on your thread**. Between those moments it is paused - which costs nothing,
because its clock is virtual (see the time contract in 0096's "Core host contract"). There are three ways to drive it:

| you are... | do this |
|---|---|
| in `asyncio` code (a coroutine, `asyncio.run()`) | `await device.astart()` and `await device.aexec(...)`: the engine is a task on your loop (`bind_loop()` does it for a bare `Simulator`). This is how the CLI works. |
| in synchronous code (a script, a Tk window, a test) with the blocking API | `device.start_async().result()`, `device.exec_async(...).result()`, `simulator.call(coro)`: on a loop nobody runs they **pump it themselves** until they are done (or time out). Nothing else is needed. |
| in synchronous code that does its own work in a loop (a GUI timer, frame decoding, polling) | `simulator.pump(seconds)`: runs the engine for that much *wall* time and returns. Typical shape:<br>`loop = asyncio.new_event_loop()`<br>`loop.run_until_complete(device.astart())`<br>`task = loop.create_task(device.aexec_file(script))`<br>`while not task.done(): device.simulator.pump(0.02); ...draw, poll, drain queues...` |

`demo/eink_run.py`, `demo/lcd_run.py` and `demo/wifi_lcd_run.py` are the worked examples of the third row (with Tk or PNG output).

**What does not work any more** (it used to, by accident of the engine-room thread): start the engine (`simulator.start_execution()`, or
`device.start_async()` without `.result()`) and then just `time.sleep()`, or wait on a flag from another thread. Nothing runs the engine, so nothing happens
- a stall, not an error. Pump it, wait through one of the blocking calls, or opt back in to the thread (below).

## Opting in to the old engine-room thread

`Simulator(threadless=False)`, or `RP2040PY_THREADLESS=0` in the environment: the engine runs on a dedicated daemon thread with its own loop, and
`start_execution()`, `submit()`, `call()` and `schedule_threadsafe()` reach it from other threads, as before. The simulator then lowers the interpreter's GIL
switch interval from 5 ms to 1 ms once, when it creates the thread (only if the interval is still the default; `RP2040PY_SWITCH_INTERVAL=<seconds>` sets
it, `0` leaves it alone) - see the next section for why.

## Why: the numbers

The 2.9" e-paper demo (`demo/eink_run.py`: five frames, each decoded with Pillow and saved as a PNG while the emulator runs), native build, wall time of the
whole script, interpreter start-up included, 4 cores:

| how the engine is driven | time |
|---|---|
| **thread-free (the default)** | **0.9-1.15 s** |
| engine-room thread, GIL switch interval 1 ms | 1.05-1.4 s |
| engine-room thread, GIL switch interval 5 ms (CPython's default) | 29-57 s (`main` before this work: 35-50 s) |

The emulation itself is ~1 s of that. In the threaded form the host thread (Pillow decoding, file I/O) releases the GIL at every read and sleep and then waits
up to a switch interval to win it back from an engine that never blocks - thousands of waits of up to 5 ms. Found by sampling the threads' stacks with
`faulthandler` (it does not take the GIL): the host thread sat in a Pillow plugin import while the engine ran a batch. Shortening the engine's batch made it
*worse* (123 s), so it is the hand-offs, not the batch length. Thread-free there is nothing to hand the GIL to.

Not the same thing, and easy to confuse: `rp2040py bench` in firmware mode steps one instruction at a time **from Python** (`cli/__init__.py`,
`_bench_firmware`) and measures the cost of that call, not the batch engine (13.7 -> 18.4 M instr/s on one machine, 5.3 -> 14.6 on another, against ~97 M/s
through `Simulator.execute()` on a MicroPython loop).

## Why this shape (and not a faster thread)

**WASM has no pthreads.** A WASM host can only drive the engine in slices - call in, the core runs a batch and returns - never wait for it on a thread of
its own. `pump()` is that same shape on the Python side, and the C++ core is single-threaded by contract with re-entrancy from imports (0096's "Core host
contract"), so the thread-free Python host and the WASM host are one model. It is also why releasing the GIL around the C++ batch (`nogil`) was considered
and not pursued, and why the `await`-ing model is the one `rp2040js` has (one event loop, `setTimeout(0)` between batches; `Simulator.execute()` copies it).

## Where threads remain

- `Simulator(threadless=False)` / `RP2040PY_THREADLESS=0`: the optional engine-room thread above (tested: `tests/test_schedule_threadsafe.py`, the threaded
  tests of `tests/test_device.py` run in both modes).
- `cli/stdio_repl.py`: a fallback reader thread for stdin where the loop cannot watch it (a blocking read must stay off the engine's thread).
- A *blocking* real-world call made from inside an engine callback (a socket connect in a CYW43 NAT path) would stall a thread-free engine; the NAT's paths
  use `schedule_threadsafe()` and non-blocking sockets, and the suite passes thread-free, but a new device doing blocking I/O must not do it inline.

Verification of the claim "nothing in the library needs the thread": the full test suite, native build, with `RP2040PY_THREADLESS=1` (before the default
changed): 1632 passed, 20 skipped, and the only two failures were the tests of the threaded semantics themselves, which now select `threadless=False`.
