# A sketch of the protocol the host's glue narrows to (working note, 2026-10-27 - **nothing here is built**)

Written at the maintainer's request as an example of what "the same glue, whatever runs the chip" looks like once the border is narrow ([narrow-the-border-plan.md](narrow-the-border-plan.md), [narrow-the-input-plan.md](narrow-the-input-plan.md), the lists in [0102](../records/0102-c-abi-for-the-core.md)). It is a typed sketch (`mypy --strict` clean), not the current API: it assumes the further narrowing of the input plan (no plumbing, no per-block state getters, the `complete_*` families folded into one call per bus, pins as one `pin_input`).

**How it maps to what exists.** `World` is the 16 slots of `McuHost` (the core calls the host). `Chip` is the inward half (the host calls the core): the bus (`read32` / `write32`), the explicit inputs the bus cannot give (`uart_feed`, `spi_complete`, `i2c_complete`, `adc_complete`, `pin_input`, `dreq`, `set_crystal_hz`), `run` / `request_stop` / `set_real_io`, `snapshot(block, instance)` for everything a test or a tool wants to see, and `observe(port, ...)` for the five observer ports (`tap` and `after_tap` of the clock tree, `dreq_tap`, `pwm_input_tap`, the watchdog's tick). A `Backend` builds a `Chip` from a `World`; the three that exist or are planned - the pure-Python blocks, the C++ core through Cython, the wasm module under `wasmhost` - each implement `Backend`, and what makes them the same chip is the twin tests, not the type. The host's whole engine is `drive()` at the end: call `run`, sleep for `ticks_advanced` if it paces itself, give the control back, repeat.

**What is deliberately not in it:** the per-block accessors (a block is `Block` + `instance`), the plumbing (`check_interrupts`, `*_updated`, `tick_changed`), a block's own `reset` (the chip's `reset` is the cascade), the state getters (they are `snapshot()` entries), and the USB wire / USB host, which are nested protocols of the same shape and are left out of the sketch for length.

**Open in the sketch (decide when it is built):** whether failures raise (as here) or return a status for the wasm host; whether `pin_input` takes a `Drive` or the three-state code the `pin_changed` slot uses; whether the USB host is a nested protocol or part of `Chip`; and the exact set of `Block` values.

```python
"""A sketch (not built): the one interface the host's glue talks to, whatever runs the chip - the pure-Python blocks, the C++ core through Cython, or the wasm module under wasmhost.

Rules the sketch keeps: only ints, floats, bools, bytes, tuples and plain dicts cross; blocks are addressed by `Block` + an instance number, never by object; a failure raises (the
C++ side parks it and the shell re-raises); nothing is called back from inside a register write except what `World` lists; exactly two calls may come from another thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Mapping, Protocol, Sequence

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Values


class Block(IntEnum):
    """Which block a call is about (with an `instance` where there are several): the C ABI's block id."""

    UART = 0
    SPI = 1
    I2C = 2
    ADC = 3
    PIO = 4
    PWM = 5
    DMA = 6
    TIMER = 7
    WATCHDOG = 8
    RTC = 9
    CLOCKS = 10
    PLL = 11
    XOSC = 12
    ROSC = 13
    PPB = 14
    SIO = 15
    USB = 16
    # ... (the rest of the chip's blocks: every block has a snapshot, only some have inputs)


class Stop(IntEnum):
    BATCH_DONE = 0  # the instruction ceiling or the time budget
    STOP_REQUESTED = 1
    BREAK = 2  # BKPT / on_break
    REAL_IO = 3  # a device waits on the real world: the idle jump was capped
    FAULT = 4  # a host call raised


@dataclass(frozen=True)
class RunLimits:
    instruction_ceiling: int = 1_000_000
    yield_budget_seconds: float = 0.005  # wall time; 0 = none (a worker that never yields)
    paced_idle_nanos: float = 0.0  # 0 = jump over idle time at once


@dataclass(frozen=True)
class RunResult:
    reason: Stop
    ticks_advanced: int  # simulated time that passed: what a host that paces itself sleeps for
    instructions: int


Snapshot = Mapping[str, "int | float | bool | bytes | None | tuple[object, ...]"]
Unsubscribe = Callable[[], None]


class I2cStep(IntEnum):
    START = 0
    CONNECT = 1
    WRITE = 2
    READ = 3
    STOP = 4
    ARBITRATION_LOST = 5


class Drive(IntEnum):
    """What the world does to a pin from outside."""

    LOW = 0
    HIGH = 1
    PULL_DOWN = 2
    PULL_UP = 3
    RELEASED = 4


class Port(IntEnum):
    """Observers a host may install; each sees an event on its way and takes nothing away from the chip."""

    CLOCK_TREE = 0  # (domain, hz, old) before the consumers follow
    CLOCK_TREE_DONE = 1  # the same, after every consumer has followed
    DREQ = 2  # (number, asserted)
    PWM_INPUT = 3  # (pin)
    WATCHDOG_TICK = 4  # (hz)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# What the host provides: the chip calls these (today's 16 `McuHost` slots; a missing one means "nothing on the other end")


class World(Protocol):
    def warn(self, block: Block, kind: int, offset: int, value: int) -> None: ...
    def uart_byte(self, instance: int, byte: int) -> None: ...
    def uart_baud(self, instance: int) -> None: ...  # read the new rate off the chip's snapshot
    def spi_transmit(self, instance: int, word: int) -> None: ...  # the device answers with Chip.spi_complete
    def i2c_start(
        self, instance: int, repeated: bool
    ) -> None: ...  # each of the five is answered with Chip.i2c_complete
    def i2c_connect(self, instance: int, address: int, write: bool) -> None: ...
    def i2c_write(self, instance: int, byte: int) -> None: ...
    def i2c_read(self, instance: int, ack: bool) -> None: ...
    def i2c_stop(self, instance: int) -> None: ...
    def adc_read(self, channel: int) -> None: ...  # a conversion was requested: answer with Chip.adc_complete
    def adc_channel_value(self, channel: int) -> int: ...  # the analog value of a channel, raw
    def pin_changed(self, bank: int, pin: int, new_state: int, old_state: int) -> None: ...
    def reset_requested(self, cause: int) -> None: ...  # 0 = SYSRESETREQ, 1 = watchdog

    # the USB wire (the device at the other end of the cable) is a nested protocol of its own: `UsbWire`


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# What the host calls: the chip


class Chip(Protocol):
    # -- build / reset ----------------------------------------------------------------------------------------------------------------------------------------------------
    def reset(self, *, preserve_flash: bool = False) -> None: ...

    # -- memory the caller owns (a buffer, not a copy: the flash can be 16 MB) ---------------------------------------------------------------------------------------------
    @property
    def rom(self) -> memoryview: ...
    @property
    def flash(self) -> memoryview: ...
    @property
    def sram(self) -> memoryview: ...

    # -- time: the host calls `run` in a loop, yields between calls, and sleeps for `ticks_advanced` if it paces itself --------------------------------------------------------
    def run(self, limits: RunLimits = RunLimits()) -> RunResult: ...
    def request_stop(self) -> None: ...  # thread-safe: the only way to end a `run` from outside
    def set_real_io(self, waiting: bool) -> None: ...  # thread-safe: "a device waits on the real world"

    # -- the bus: a firmware's view, and a debugger's -----------------------------------------------------------------------------------------------------------------------
    def read32(self, address: int) -> int: ...
    def write32(self, address: int, value: int) -> None: ...  # (read8/16, write8/16 the same)

    # -- inputs the bus cannot give: the world tells the chip something -----------------------------------------------------------------------------------------------------
    def set_crystal_hz(self, hz: float) -> None: ...
    def uart_feed(self, instance: int, byte: int) -> None: ...
    def spi_complete(self, instance: int, word: int) -> None: ...
    def i2c_complete(self, instance: int, step: I2cStep, value: int = 0) -> None: ...
    def adc_complete(self, channel: int, value: int) -> None: ...
    def pin_input(self, bank: int, pin: int, drive: Drive) -> None: ...
    def dreq(self, number: int, asserted: bool) -> None: ...

    # the USB host's side (attach / detach / bus reset / setup packet / endpoint transfers) is `UsbHost`, nested the same way

    # -- observation: one call per block, a dict of plain values, identical on every backend ------------------------------------------------------------------------------------
    def snapshot(self, block: Block, instance: int = 0) -> Snapshot: ...

    # -- observers (optional) --------------------------------------------------------------------------------------------------------------------------------------------------
    def observe(self, port: Port, callback: Callable[..., None]) -> Unsubscribe: ...


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# A backend is anything that builds a Chip from a World


class Backend(Protocol):
    def build(self, world: World, *, crystal_hz: float = 12e6) -> Chip: ...


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The host's glue, written once against the protocol (this is the whole of `Simulator`'s engine, minus asyncio)


def drive(
    chip: Chip, stop: Callable[[], bool], yield_to_loop: Callable[[], None], pace: Callable[[int], None] | None = None
) -> RunResult:
    result = RunResult(Stop.BATCH_DONE, 0, 0)
    while not stop():
        result = chip.run()
        if result.reason in (Stop.STOP_REQUESTED, Stop.BREAK, Stop.FAULT):
            break
        if pace is not None:
            pace(result.ticks_advanced)  # real time: sleep for what the chip says passed
        yield_to_loop()  # let the rest of the host (devices, terminal, UI) run
    return result


class UartEcho:
    """An external device written in the host's language: it implements the `World` slot it cares about and answers through the chip's input."""

    def __init__(self, chip: Chip) -> None:
        self.chip = chip

    def uart_byte(self, instance: int, byte: int) -> None:
        self.chip.uart_feed(instance, byte)
```
