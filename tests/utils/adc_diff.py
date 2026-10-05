"""A lockstep differential oracle for the ADC: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 4 (the ADC design note). The method is the PIO's, the DMA's, the SSI's, the UART's, the SPI's and the I2C's (tests/utils/*_diff.py): one chip
whose ADC is the *pure-Python* reference (``peripherals/_adc.py``, built explicitly because the facade would hand out the native one) and one whose ADC is whatever the facade gives
(today the same class, later the C++ one), fed one stream of operations:

* writes and reads of **every register offset**, through the bus and through its four aliases (normal, XOR, SET, CLR - an alias write decodes against a *read*, and a read of FIFO
  pulls a conversion result), unimplemented offsets included (so the warnings are compared);
* the **device on the ADC**, in the shapes a real one has: the reference's **default** (the sample comes from ``channel_values`` after ``sample_time`` of simulated time, from a
  clock alarm), an **immediate** device that calls ``complete_adc_read()`` from inside ``on_adc_read`` (re-entrancy, bounded in depth: START_MANY with a small divider would
  otherwise recurse for ever, in the reference as well), a **deferred** one completed by a later operation, and a **silent** one that never completes (the block stays busy);
* **simulated time** advanced by ``clock.tick()`` in steps from nothing to several samples (the sample alarm and the multi-shot alarm fire inside it, in order), the analog inputs
  changed in place and by assignment, ``sample_time`` and ``num_channels`` changed, ``complete_adc_read()`` and ``start_adc_read()`` called directly, ``check_interrupts()``, ``reset()``.

After **each** step: the register file read through the bus (FIFO excluded - it has a side effect - and read as an operation of its own), the private state (CS, FCS, the divider
register, both interrupt registers, the last result, busy, err, the channel being sampled), the FIFO's level and contents, the derived properties, whether an alarm is
scheduled and when (seen through the clock: the next alarm's due time), the NVIC's pending bits, and the ordered, timestamped log of everything that left the block (IRQ line changes, DREQ set/clear, reads asked of the device,
warnings); an exception on one side and not the other is a difference like any other (an ``IndexError`` out of the sample alarm for a channel above 4 included).

The rig *replaces* the chip's DMA with a recorder and wraps ``set_interrupt``.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _adc as P
from rp2040py.rp2040 import RP2040

ADC_BASE = 0x4004C000
NVIC_ISPR = 0xE000E200
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTERS = (P.CS, P.RESULT, P.FCS, P.DIV, P.INTR, P.INTE, P.INTF, P.INTS)  # no side effect on read
WRITABLE = (P.CS, P.FCS, P.DIV, P.INTE, P.INTF)
UNIMPLEMENTED = (
    0x20 + 4,
    0x28,
    0x40,
    0x100,
    0xFFC,
    P.RESULT,
    P.INTR,
    P.INTS,
    P.FIFO_REG,
)  # the last four are read-only or have no write handler
TICKS = (0, 1, 500, 1999, 2000, 2001, 4000, 10_000, 50_000, 200_000)
MAX_DEPTH = 3  # how deep an immediate device completes from inside its own callback


class Rig:
    """One chip plus the timestamped log of everything that left its ADC."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        self.pending: list[int] = []  # channels a deferred device has been asked for and has not completed
        self.depth = 0
        self.counter = 0
        if kind == "pure":
            self._replace_the_adc(P.RPADC)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_adc(factory)
        self._tap()
        self.mode = "default"

    def _replace_the_adc(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(chip, "ADC")
        chip.adc = new
        chip.peripherals[ADC_BASE >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log

        class _Dma:
            def set_dreq(self, channel: int) -> None:
                log.append(("dreq", chip.clock.nanos, int(channel), 1))

            def clear_dreq(self, channel: int) -> None:
                log.append(("dreq", chip.clock.nanos, int(channel), 0))

        chip.dma = _Dma()  # type: ignore[assignment]  # the ADC looks `rp2040.dma` up whenever its FIFO level changes
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", chip.clock.nanos, int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", chip.clock.nanos, _m, str(name), str(message))),
            )

    def attach_device(self, mode: str) -> None:
        adc, log, chip = self.chip.adc, self.log, self.chip
        self.mode = mode
        if mode == "default":
            adc.on_adc_read = adc._default_on_adc_read
            return

        def on_adc_read(channel: int) -> None:
            log.append(("read", chip.clock.nanos, int(channel)))
            if mode == "immediate" and self.depth < MAX_DEPTH:
                self.depth += 1
                try:
                    self.counter += 1
                    adc.complete_adc_read((self.counter * 977) & 0xFFFF, self.counter % 7 == 0)
                finally:
                    self.depth -= 1
            elif mode == "deferred":
                self.pending.append(int(channel))

        adc.on_adc_read = on_adc_read

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        adc = chip.adc
        if kind == "write":
            chip.write_uint32(ADC_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(ADC_BASE + op[2] + op[1]))
        elif kind == "device":
            self.attach_device(op[1])
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "values":
            if op[2]:
                adc.channel_values[:] = op[1]
            else:
                adc.channel_values = list(op[1])
        elif kind == "sample_time":
            adc.sample_time = op[1]
        elif kind == "num_channels":
            adc.num_channels = op[1]
        elif kind == "complete":  # a deferred device finishing (or a spurious completion when nothing was asked)
            if self.pending:
                self.pending.pop(0)
            adc.complete_adc_read(op[1], op[2])
        elif kind == "start":
            adc.start_adc_read()
        elif kind == "check":
            adc.check_interrupts()
        elif kind == "reset":
            adc.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, adc = self.chip, self.chip.adc
        fifo = adc.fifo
        return {
            "regs": tuple(int(chip.read_uint32(ADC_BASE + offset)) for offset in REGISTERS),
            "state": (
                int(adc.cs),
                int(adc.fcs),
                int(adc.clock_div),
                int(adc.int_enable),
                int(adc.int_force),
                int(adc.result),
                bool(adc.busy),
                bool(adc.err),
                int(adc.current_channel),
                int(adc.raw_write_value),
            ),
            "fifo": (bool(fifo.empty), bool(fifo.full), int(fifo.item_count), tuple(int(v) for v in fifo.items)),
            "props": (
                int(adc.temperature_enable),
                int(adc.enabled),
                float(adc.divider),
                int(adc.int_raw),
                int(adc.int_status),
                int(adc._active_channel),
            ),
            "alarms": (
                bool(chip.clock.has_scheduled_alarm),
                float(chip.clock.nanos_to_next_alarm),
            ),  # the chip has no other alarm in this rig
            "wiring": (
                int(adc.num_channels),
                adc.sample_time,
                int(adc.resolution),
                int(adc.dreq),
                tuple(adc.channel_values),
            ),
            "nvic": int(chip.read_uint32(NVIC_ISPR)),
            "time": float(chip.clock.nanos),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose ADC is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""

    class Mutant(P.RPADC):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if name == "fifo_depth_8":
                self.fifo = P.FIFO(8)

        # -- the sample
        def _default_on_adc_read(self, channel: int) -> None:
            if name == "sample_time_halved":
                self.current_channel = channel
                self.sample_alarm.schedule(self.sample_time * 500)
                return
            if name == "channel_not_recorded":
                self.sample_alarm.schedule(self.sample_time * 1000)
                return
            super()._default_on_adc_read(channel)

        def _on_sample_alarm(self) -> None:
            if name == "sample_reads_active_channel":
                self.complete_adc_read(self.channel_values[self._active_channel], False)
                return
            if name == "sample_flags_error":
                self.complete_adc_read(self.channel_values[self.current_channel], True)
                return
            super()._on_sample_alarm()

        def _on_multi_shot_alarm(self) -> None:
            if name == "multi_shot_ignored":
                return
            if name == "multi_shot_checks_start_one":
                if self.cs & P.CS_START_ONE:
                    self.start_adc_read()
                return
            super()._on_multi_shot_alarm()

        def start_adc_read(self) -> None:
            if name == "start_not_busy":
                self.on_adc_read(self._active_channel)
                return
            super().start_adc_read()

        # -- derived
        @property
        def divider(self) -> float:
            if name == "divider_no_one":
                return ((self.clock_div >> 8) & 0xFFFF) + (self.clock_div & 0xFF) / 256
            if name == "divider_frac_256_wrong":
                return 1 + ((self.clock_div >> 8) & 0xFFFF) + (self.clock_div & 0xFF) / 255
            if name == "divider_int_mask_8":
                return 1 + ((self.clock_div >> 8) & 0xFF) + (self.clock_div & 0xFF) / 256
            return super().divider

        @property
        def int_raw(self) -> int:
            if name == "int_raw_gt":
                thres = (self.fcs >> P.FCS_THRESH_SHIFT) & P.FCS_THRES_MASK
                return P.FIFO_INT if self.fifo.item_count > thres else 0
            return super().int_raw

        @property
        def int_status(self) -> int:
            if name == "int_status_no_force":
                return self.int_raw & self.int_enable
            if name == "int_status_force_masked":
                return (self.int_raw | self.int_force) & self.int_enable
            if name == "int_status_no_mask":
                return self.int_raw | self.int_force
            return super().int_status

        def _update_dma(self) -> None:
            if name == "dma_gt":
                if self.fcs & P.FCS_DREQ_EN:
                    thres = (self.fcs >> P.FCS_THRESH_SHIFT) & P.FCS_THRES_MASK
                    (self.rp2040.dma.set_dreq if self.fifo.item_count > thres else self.rp2040.dma.clear_dreq)(
                        self.dreq
                    )
                return
            if name == "dma_ignores_dreq_en":
                thres = (self.fcs >> P.FCS_THRESH_SHIFT) & P.FCS_THRES_MASK
                (self.rp2040.dma.set_dreq if self.fifo.item_count >= thres else self.rp2040.dma.clear_dreq)(self.dreq)
                return
            if (
                name == "dma_off_keeps"
            ):  # the old behaviour: with DREQ_EN off nothing is published, so a request that was up stays up
                if self.fcs & P.FCS_DREQ_EN:
                    thres = (self.fcs >> P.FCS_THRESH_SHIFT) & P.FCS_THRES_MASK
                    (self.rp2040.dma.set_dreq if self.fifo.item_count >= thres else self.rp2040.dma.clear_dreq)(
                        self.dreq
                    )
                return
            if name == "dma_never_clears":
                if self.fcs & P.FCS_DREQ_EN:
                    thres = (self.fcs >> P.FCS_THRESH_SHIFT) & P.FCS_THRES_MASK
                    if self.fifo.item_count >= thres:
                        self.rp2040.dma.set_dreq(self.dreq)
                return
            super()._update_dma()

        # -- the completion: one reimplementation with the knobs the mutants turn
        def complete_adc_read(self, value: int, error: bool) -> None:
            if not name.startswith("c_"):
                super().complete_adc_read(value, error)
                return
            if name != "c_busy_kept":
                self.busy = False
            if name != "c_result_not_stored":
                self.result = value
            if error:
                self.cs |= P.CS_ERR_STICKY | (0 if name == "c_err_missing" else P.CS_ERR)
            elif name != "c_err_never_cleared":
                self.cs &= ~P.CS_ERR
            if name == "c_fifo_always" or self.fcs & P.FCS_EN:
                if self.fifo.full:
                    if name != "c_overflow_silent":
                        self.fcs |= P.FCS_OVER
                else:
                    if name != "c_value_unmasked":
                        value &= 0xFFF
                    if self.fcs & P.FCS_SHIFT and name != "c_shift_ignored":
                        value >>= 4 if name != "c_shift_8" else 8
                    if error and self.fcs & P.FCS_ERR and name != "c_fifo_err_ignored":
                        value |= P.FIFO_ERR
                    self.fifo.push(value)
                    if name != "c_no_dma":
                        self._update_dma()
                    if name != "c_no_interrupts":
                        self.check_interrupts()
            round_mask = (self.cs >> P.CS_RROBIN_SHIFT) & P.CS_RROBIN_MASK
            if round_mask and name != "c_no_round_robin":
                channel = self._active_channel + (0 if name == "c_round_robin_same" else 1)
                if (
                    name != "c_round_robin_unwrapped"
                ):  # the old behaviour: the first candidate was not wrapped (5 % 5 = 0 was skipped)
                    channel %= self.num_channels
                while not (round_mask & (1 << channel)):
                    channel = (channel + 1) % self.num_channels
                self._active_channel = channel
            if self.cs & P.CS_START_MANY and name != "c_no_multi_shot":
                clock_mhz = 48
                sample_ticks = clock_mhz * self.sample_time
                gate = self.divider >= sample_ticks if name == "c_divider_ge" else self.divider > sample_ticks
                if gate:
                    micros = (self.divider - sample_ticks) / clock_mhz
                    self.multi_shot_alarm.schedule(micros * 1000 if name != "c_micros_doubled" else micros * 2000)
                else:
                    self.start_adc_read()

        # -- the active channel
        @property
        def _active_channel(self) -> int:
            return (self.cs >> P.CS_AINSEL_SHIFT) & P.CS_AINSEL_MASK

        @_active_channel.setter
        def _active_channel(self, channel: int) -> None:
            self.cs &= ~(P.CS_AINSEL_MASK << P.CS_AINSEL_SHIFT)
            if (
                name == "ainsel_setter_shift_mask"
            ):  # the old behaviour: masked with the shift (12), not the field mask (7)
                self.cs |= (channel & P.CS_AINSEL_SHIFT) << P.CS_AINSEL_SHIFT
            else:
                self.cs |= (channel & P.CS_AINSEL_MASK) << P.CS_AINSEL_SHIFT

        # -- the registers
        def read_uint32(self, offset: int) -> int:
            if offset == P.CS and name == "cs_ready_inverted":
                return self.cs | (P.CS_ERR if self.err else 0) | (P.CS_READY if self.busy else 0)
            if offset == P.CS and name == "cs_ready_missing":
                return self.cs | (P.CS_ERR if self.err else 0)
            if offset == P.FCS and name == "fcs_level_unmasked":
                return super().read_uint32(offset) | (self.fifo.item_count << 20)
            if offset == P.FCS and name == "fcs_full_missing":
                return super().read_uint32(offset) & ~P.FCS_FULL
            if offset == P.FCS and name == "fcs_empty_missing":
                return super().read_uint32(offset) & ~P.FCS_EMPTY
            if offset == P.FIFO_REG and name == "fifo_read_no_under" and self.fifo.empty:
                return 0
            if offset == P.FIFO_REG and name == "fifo_read_no_dma":
                if self.fifo.empty:
                    self.fcs |= P.FCS_UNDER
                    return 0
                return self.fifo.pull()
            if offset == P.INTR and name == "intr_is_status":
                return self.int_status
            if offset == P.INTE and name == "inte_reads_force":
                return self.int_force
            if offset == P.RESULT and name == "result_masked":
                return self.result & 0xFFF
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if offset == P.CS and name in (
                "cs_start_without_en",
                "cs_start_when_busy",
                "cs_mask_all",
                "cs_start_one_only",
                "cs_sticky_in_fcs",
                "cs_start_one_sticky",
            ):
                if (
                    name == "cs_sticky_in_fcs"
                ):  # the old behaviour: the write-clear hit FCS (whose bit 10 is UNDER), not CS
                    self.fcs &= ~(value & P.CS_ERR_STICKY)
                else:
                    self.cs &= ~(value & P.CS_ERR_STICKY)
                mask = 0xFFFFFFFF if name == "cs_mask_all" else P.CS_WRITE_MASK
                self.cs = (self.cs & ~mask) | (value & mask)
                if name != "cs_start_one_sticky":  # the old behaviour: START_ONE stayed set in CS
                    self.cs &= ~P.CS_START_ONE
                start = value & P.CS_START_ONE or value & P.CS_START_MANY
                if name == "cs_start_one_only":
                    start = value & P.CS_START_ONE
                en = True if name == "cs_start_without_en" else value & P.CS_EN
                busy_ok = True if name == "cs_start_when_busy" else not self.busy
                if en and busy_ok and start:
                    self.start_adc_read()
                return
            if offset == P.FCS and name == "fcs_over_not_cleared":
                self.fcs = (self.fcs & ~P.FCS_WRITE_MASK) | (value & P.FCS_WRITE_MASK)
                self._update_dma()
                self.check_interrupts()
                return
            if offset == P.FCS and name == "fcs_no_check":
                self.fcs &= ~(value & (P.FCS_OVER | P.FCS_UNDER))
                self.fcs = (self.fcs & ~P.FCS_WRITE_MASK) | (value & P.FCS_WRITE_MASK)
                self._update_dma()
                return
            if offset == P.FCS and name == "fcs_mask_all":
                self.fcs &= ~(value & (P.FCS_OVER | P.FCS_UNDER))
                self.fcs = value
                self._update_dma()
                self.check_interrupts()
                return
            if offset == P.FCS and name == "fcs_no_dma":  # the old behaviour: an FCS write did not touch the DREQ
                self.fcs &= ~(value & (P.FCS_OVER | P.FCS_UNDER))
                self.fcs = (self.fcs & ~P.FCS_WRITE_MASK) | (value & P.FCS_WRITE_MASK)
                self.check_interrupts()
                return
            if offset == P.DIV and name == "div_masked":
                self.clock_div = value & 0xFFFFFF
                return
            if offset == P.INTE and name == "inte_unmasked":
                self.int_enable = value
                self.check_interrupts()
                return
            if offset == P.INTE and name == "inte_no_check":
                self.int_enable = value & P.FIFO_INT
                return
            if offset == P.INTF and name == "intf_unmasked":
                self.int_force = value
                self.check_interrupts()
                return
            if offset == P.INTF and name == "intf_no_check":
                self.int_force = value & P.FIFO_INT
                return
            super().write_uint32(offset, value)

        def reset(self) -> None:
            on_adc_read, values = self.on_adc_read, self.channel_values
            if name == "reset_keeps_alarms":
                real = (self.sample_alarm, self.multi_shot_alarm)

                class _Keep:
                    def __init__(self, alarm: Any) -> None:
                        self.alarm = alarm

                    def schedule(self, delta: float) -> None:
                        self.alarm.schedule(delta)

                    def cancel(self) -> None:
                        pass

                self.sample_alarm, self.multi_shot_alarm = _Keep(real[0]), _Keep(real[1])  # type: ignore[assignment]
                try:
                    super().reset()
                finally:
                    self.sample_alarm, self.multi_shot_alarm = real
                self.on_adc_read = on_adc_read
                return
            if name == "reset_keeps_fifo":
                items = list(self.fifo.items)
                super().reset()
                for item in items:
                    self.fifo.push(item)
                self.on_adc_read = on_adc_read
                return
            if name == "reset_keeps_div":
                div = self.clock_div
                super().reset()
                self.clock_div = div
                self.on_adc_read = on_adc_read
                return
            if name == "reset_no_irq":
                self.rp2040.set_interrupt = lambda *_a: None  # type: ignore[method-assign]
                try:
                    super().reset()
                finally:
                    del self.rp2040.set_interrupt
                self.on_adc_read = on_adc_read
                return
            super().reset()
            if name == "reset_clears_callback":
                self.on_adc_read = self._default_on_adc_read
            else:
                self.on_adc_read = on_adc_read
            if name == "reset_clears_channel_values":
                self.channel_values = [0] * 5
            else:
                self.channel_values = values

    return Rig("mutant", Mutant)


MUTANTS = (
    "fifo_depth_8", "sample_time_halved", "channel_not_recorded", "sample_reads_active_channel", "sample_flags_error", "multi_shot_ignored", "multi_shot_checks_start_one",
    "start_not_busy", "divider_no_one", "divider_frac_256_wrong", "divider_int_mask_8", "int_raw_gt", "int_status_no_force", "int_status_force_masked",
    "int_status_no_mask", "dma_gt", "dma_ignores_dreq_en", "dma_never_clears", "c_busy_kept", "c_result_not_stored", "c_err_missing", "c_err_never_cleared", "c_fifo_always",
    "c_overflow_silent", "c_value_unmasked", "c_shift_ignored", "c_shift_8", "c_fifo_err_ignored", "c_no_dma", "c_no_interrupts", "c_no_round_robin", "c_round_robin_same",
    "c_no_multi_shot", "c_divider_ge", "c_micros_doubled", "cs_ready_inverted", "cs_ready_missing", "fcs_level_unmasked", "fcs_full_missing", "fcs_empty_missing",
    "fifo_read_no_under", "fifo_read_no_dma", "intr_is_status", "inte_reads_force", "result_masked", "cs_start_without_en", "cs_start_when_busy", "cs_mask_all",
    "cs_start_one_only", "fcs_over_not_cleared", "fcs_no_check", "fcs_mask_all", "div_masked", "inte_unmasked", "inte_no_check", "intf_unmasked", "intf_no_check",
    "reset_keeps_alarms", "reset_keeps_fifo", "reset_keeps_div", "reset_no_irq", "reset_clears_callback", "reset_clears_channel_values",
    "cs_sticky_in_fcs", "ainsel_setter_shift_mask", "c_round_robin_unwrapped", "fcs_no_dma", "dma_off_keeps", "cs_start_one_sticky",
)  # fmt: skip


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == P.CS:
        return r.choice(
            (
                P.CS_EN,
                P.CS_EN | P.CS_START_ONE,
                P.CS_EN | P.CS_START_ONE,
                P.CS_EN | P.CS_START_MANY,
                P.CS_EN | P.CS_TS_EN | P.CS_START_ONE,
                r.randrange(8) << P.CS_AINSEL_SHIFT | P.CS_EN | P.CS_START_ONE,
                r.randrange(32) << P.CS_RROBIN_SHIFT | P.CS_EN,
                r.randrange(32) << P.CS_RROBIN_SHIFT | P.CS_EN | P.CS_START_MANY,
                P.CS_ERR_STICKY,
                0,
                r.getrandbits(32),
            )
        )
    if offset == P.FCS:
        return r.choice(
            (
                P.FCS_EN,
                P.FCS_EN | P.FCS_SHIFT,
                P.FCS_EN | P.FCS_ERR,
                P.FCS_EN | P.FCS_DREQ_EN | (1 << P.FCS_THRESH_SHIFT),
                P.FCS_EN | P.FCS_DREQ_EN | (r.randrange(5) << P.FCS_THRESH_SHIFT),
                r.randrange(16) << P.FCS_THRESH_SHIFT | P.FCS_EN,
                P.FCS_OVER | P.FCS_UNDER,
                0,
                r.getrandbits(32),
            )
        )
    if offset == P.DIV:
        return r.choice(
            (0, 0x100, 0x5F00, 0x6000, 0x6080, 0x1_0000, 0xFFFF00, 0x80, r.getrandbits(24), r.getrandbits(32))
        )
    if offset in (P.INTE, P.INTF):
        return r.choice((0, 1, 1, 2, 0xFFFFFFFF)) if roll < 0.8 else r.getrandbits(32)
    return r.getrandbits(32)


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.08 and ops:  # attach a device only after a while: the default behaviour gets a prefix of its own
            ops.append(("device", r.choice(("immediate", "deferred", "silent", "default", "default"))))
        elif roll < 0.38:
            offset = r.choice(WRITABLE + (P.CS, P.CS, P.FCS))
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.42:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.52:  # a burst of conversions: START_ONE writes, to fill the FIFO when nothing drains it
            ops.extend(("write", P.CS, P.CS_EN | P.CS_START_ONE, ALIASES[0]) for _ in range(r.choice((1, 2, 5, 6)) * 2))
            ops.append(("tick", r.choice((2000, 4000, 10_000))))
        elif (
            roll < 0.60
        ):  # a free-running capture: START_MANY, a divider around the sample time, round-robin masks, then time
            ops.append(("write", P.FCS, P.FCS_EN | (r.choice((0, 1, 2)) << P.FCS_THRESH_SHIFT), ALIASES[0]))
            ops.append(("write", P.DIV, r.choice((0x2F00, 0x5F00, 0x6000, 0x6080, 0x8F00, 0x8000, 0)), ALIASES[0]))
            rr = r.choice((0, 0x11, 0x1F, 0x0F, 0x10, 0x03))
            ops.append(("write", P.CS, P.CS_EN | P.CS_START_MANY | (rr << P.CS_RROBIN_SHIFT), ALIASES[0]))
            ops.extend(("tick", r.choice((2000, 4000, 30_000, 100_000))) for _ in range(r.choice((2, 4, 8))))
            if r.random() < 0.7:
                ops.append(("write", P.CS, P.CS_EN, ALIASES[0]))
        elif roll < 0.68:
            ops.extend(("read", P.FIFO_REG, ALIASES[0]) for _ in range(r.choice((1, 1, 3, 5))))
        elif roll < 0.76:
            ops.append(
                ("read", r.choice(REGISTERS + UNIMPLEMENTED), r.choice(ALIASES) if r.random() < 0.12 else ALIASES[0])
            )
        elif roll < 0.86:
            ops.append(("tick", r.choice(TICKS)))
        elif roll < 0.89:
            ops.append(
                ("values", [r.choice((0, 0x7FF, 0xFFF, 0x1234, r.getrandbits(16))) for _ in range(5)], r.random() < 0.5)
            )
        elif roll < 0.91:
            ops.append(("sample_time", r.choice((1, 2, 2, 3, 50))))
        elif roll < 0.92:
            ops.append(("num_channels", r.choice((5, 5, 5, 6, 8))))
        elif roll < 0.95:
            if r.random() < 0.3:  # an error flagged into a FIFO that records it
                ops.append(
                    ("write", P.FCS, r.choice((P.FCS_EN | P.FCS_ERR, P.FCS_EN | P.FCS_ERR | P.FCS_SHIFT)), ALIASES[0])
                )
            ops.append(("complete", r.getrandbits(r.choice((12, 12, 16))), r.random() < 0.4))
        elif roll < 0.965:
            ops.append(("start",))
        elif roll < 0.98:
            ops.append(("reset",))
        else:
            ops.append(("check",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b[key]
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:60] for i in where[:6]]} vs {[str(value_b[i])[:60] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    adc = rig.chip.adc
    for op in ops:
        before = len(rig.log)
        fifo_full, was_busy = adc.fifo.full, adc.busy
        if op[0] == "read" and op[1] == P.FIFO_REG and op[2] == 0:
            count("fifo.read.empty" if adc.fifo.empty else "fifo.read.data")
        if op[0] == "complete":
            count("complete.spurious" if not was_busy else "complete.busy")
            if fifo_full and adc.fcs & P.FCS_EN:
                count("fifo.overflow")
            if op[2]:
                count("complete.error")
        if op[0] == "device":
            count(f"device.{op[1]}")
        if op[0] == "tick":
            count("tick")
        if op[0] == "reset":
            count("reset.alarm" if rig.chip.clock.has_scheduled_alarm else "reset")
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        if rig.chip.clock.has_scheduled_alarm:
            count("alarm.scheduled")
        if (adc.cs >> P.CS_RROBIN_SHIFT) & P.CS_RROBIN_MASK and op[0] == "tick":
            count("round_robin.tick")
        for event in rig.log[before:]:
            count(
                f"log.{event[0]}"
                + (f".{event[3]}" if event[0] == "dreq" else (f".{event[3]}" if event[0] == "irq" else ""))
            )
    return counts
