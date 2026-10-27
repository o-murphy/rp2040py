"""A lockstep differential oracle for the PIO: the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, Phase 3 (the "Oracle" paragraph of the PIO design note). A PIO cannot be judged by a recorded register trace the way TIMER
and SIO were: its pacing model (record 0063) steps at most one instruction per machine per ``advance(cycles)`` call, and the batch loop makes one such call per
CPU instruction (~93M on the Pico W scan), and its inputs include the level of every pin. So instead two chips are built - one whose PIOs are the *pure-Python*
reference (``peripherals/_pio.py`` with ``peripherals/_state_machine.py``, which is what the facade falls back to without a compiler), one whose PIOs are
whatever the facade gives (today the Cython ones, later the C++ ones) - and fed one and the same stream of operations:

* writes through the **bus** to the PIO registers (so the window dispatch and the set/xor/clear alias decode are part of what is compared): instruction memory,
  every state machine's CLKDIV/EXECCTRL/SHIFTCTRL/PINCTRL, CTRL (enable, restart, clock-divider restart), TXF, IRQ, IRQ_FORCE, INPUT_SYNC_BYPASS, INTE/INTF, and
  ``SM_INSTR`` (execute immediately); reads, including the side-effecting RXF; the programs are random sequences of valid instructions of every kind mixed with
  fully random 16-bit opcodes (the reserved encodings too);
* ``advance(n)`` on a running PIO with small and huge ``n`` (the arrears path), and a ``reset()``;
* external pin levels driven and released, and the pins' FUNCSEL (PIO0, PIO1, SIO) so the machines' outputs and the pins' inputs really interact.

After **each** step the whole readable register file, every machine's state and FIFO contents, the block's pacing state, and the ordered logs of what left
the block (interrupt-line changes, DREQ calls, pin-listener events, logger warnings/errors) are compared. The first difference is reported with the step that
produced it. The DMA is a recorder (the real one would act on the DREQs and on the FIFOs, which is a different block's behaviour, covered elsewhere).

Precondition for trusting it, as for every oracle here: the pure PIO against the *current* implementation must show 0 differences on long runs
(tests/test_pio_diff.py), and a deliberately perturbed run must be caught.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.peripherals import pio_registers as R
from utils.twin import new_chip

PIO_BASES = (0x50200000, 0x50300000)
PIO_IRQ0 = (int(IRQ.PIO0_IRQ0), int(IRQ.PIO1_IRQ0))
SM_STRIDE = R.SM1_CLKDIV - R.SM0_CLKDIV  # 0x18
SM_OFFSETS = (R.SM0_CLKDIV, R.SM0_EXECCTRL, R.SM0_SHIFTCTRL, R.SM0_ADDR, R.SM0_INSTR, R.SM0_PINCTRL)
ALIASES = (0x0000, 0x0000, 0x0000, 0x1000, 0x2000, 0x3000)  # mostly a plain write; xor / set / clear sometimes

# Registers a snapshot reads (no RXF: reading it pulls a word - the stimulus does that explicitly).
SNAPSHOT_REGISTERS = (
    R.CTRL, R.FSTAT, R.FDEBUG, R.FLEVEL, R.IRQ, R.INPUT_SYNC_BYPASS, R.DBG_PADOUT, R.DBG_PADOE, R.DBG_CFGINFO, R.INTR,
    R.IRQ0_INTE, R.IRQ0_INTF, R.IRQ0_INTS, R.IRQ1_INTE, R.IRQ1_INTF, R.IRQ1_INTS,
    *(R.SM0_CLKDIV + SM_STRIDE * m + (o - R.SM0_CLKDIV) for m in range(4) for o in SM_OFFSETS),
)  # fmt: skip


class Rig:
    """One chip plus the logs of everything that left its PIOs."""

    def __init__(self, kind: str, modules: "tuple[Any, Any] | None" = None) -> None:
        self.kind = kind
        self.chip = new_chip(kind)
        # A (fake) owner for the chip, so a CTRL write does not run the "no Simulator" fallback that steps the block inline: the stimulus calls advance() itself,
        # as the batch loop does.
        self.chip.simulator = object()
        if kind == "mutant":
            assert modules is not None
            self._replace_pios_with_the_pure_python_ones(*modules)
        self.log: list[tuple] = []
        self._tap()

    def _replace_pios_with_the_pure_python_ones(self, pio_module: Any = None, machine_module: Any = None) -> None:
        """The pure-Python PIOs (or - the modules of a mutant of the reference given - theirs) in place of the facade's."""
        from rp2040py.peripherals import _pio, _state_machine

        _pio = pio_module or _pio
        _state_machine = machine_module or _state_machine

        original = (
            _pio.StateMachine
        )  # the module global the pure RPPIO builds its machines from: the facade would hand it the native class
        _pio.StateMachine = _state_machine.StateMachine
        try:
            for index in range(2):
                pio = _pio.RPPIO(self.chip, f"PIO{index}", PIO_IRQ0[index], index)
                self.chip.pio[index] = pio
                self.chip.peripherals[PIO_BASES[index] >> 12] = pio
        finally:
            _pio.StateMachine = original

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        chip.dreq_tap = lambda number, asserted: log.append(
            ("dreq", int(number), int(asserted))
        )  # the observer of the requests that reach the chip's real DMA
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger, method, lambda name, message, _m=method: log.append(("log", _m, str(name), str(message)))
            )
        for index, pin in enumerate(chip.gpio):
            pin.add_listener(lambda new, old, _i=index: log.append(("pin", _i, int(new), int(old))))

    # --- the operations --------------------------------------------------------------------------------------------------

    def write(self, pio: int, offset: int, value: int, alias: int = 0) -> None:
        self.chip.write_uint32(PIO_BASES[pio] + alias + offset, value & 0xFFFFFFFF)

    def read(self, pio: int, offset: int) -> int:
        return int(self.chip.read_uint32(PIO_BASES[pio] + offset))

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            _, pio, offset, value, alias = op
            self.write(pio, offset, value, alias)
        elif kind == "read":
            return self.read(op[1], op[2])
        elif kind == "advance":
            _, pio, cycles = op
            block = chip.pio[pio]
            if not block.stopped:  # the batch loop only steps a block that is not stopped
                block.advance(cycles)
        elif kind == "drive":
            _, pin, level = op
            if level is None:
                chip.gpio[pin].release_input()
            else:
                chip.gpio[pin].set_input_value(level)
        elif kind == "pin_cfg":
            _, pin, funcsel = op
            chip.gpio[pin].ctrl = funcsel
            chip.gpio[pin].pad_value |= 0x40  # input enable
            chip.gpio[pin].check_for_updates()
        elif kind == "reset":
            chip.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip = self.chip
        out: dict[str, Any] = {}
        for index, block in enumerate(chip.pio):
            out[f"pio{index}.regs"] = tuple(self.read(index, offset) for offset in SNAPSHOT_REGISTERS)
            state = block.snapshot()
            machines = state.pop("machines")
            out[f"pio{index}.instructions"] = state.pop("instructions")
            out[f"pio{index}.block"] = tuple(state.values())
            for number, machine in enumerate(machines):
                out[f"pio{index}.sm{number}.held"] = machine.pop("held_write")
                out[f"pio{index}.sm{number}.tx"] = machine.pop("tx")
                out[f"pio{index}.sm{number}.rx"] = machine.pop("rx")
                out[f"pio{index}.sm{number}"] = tuple(machine.values())
        out["pins"] = tuple(
            (int(pin.ctrl), int(pin.input_value), int(pin.output_value), int(pin.output_enable), int(pin.status))
            for pin in chip.gpio
        )
        return out


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def random_opcode(r: random.Random) -> int:
    """A 16-bit instruction word: usually a well-formed one of a random kind, sometimes any bit pattern at all."""
    if r.random() < 0.25:
        return r.getrandbits(16)
    kind = r.randrange(8)
    delay_sideset = r.getrandbits(5)
    if (
        kind == 0
    ):  # JMP cond, addr - mostly a conditional one: a random 'always' jumps in tiny loops and starves the other instructions of steps
        arg = ((r.randrange(1, 8) if r.random() < 0.85 else 0) << 5) | r.randrange(32)
    elif kind == 1:  # WAIT pol, src, idx
        arg = (r.getrandbits(1) << 7) | (r.randrange(3) << 5) | r.randrange(32)
    elif kind == 2 or kind == 3:  # IN src, bits
        arg = (r.randrange(8) << 5) | r.randrange(32)
    elif kind == 4:  # PUSH/PULL
        arg = (r.getrandbits(1) << 7) | (r.getrandbits(1) << 6) | (r.getrandbits(1) << 5)
    elif kind == 5:  # MOV dst, op, src
        arg = (r.randrange(8) << 5) | (r.randrange(4) << 3) | r.randrange(8)
    elif kind == 6:  # IRQ clr, wait, idx
        arg = (r.getrandbits(1) << 6) | (r.getrandbits(1) << 5) | (r.getrandbits(1) << 4) | r.randrange(8)
    else:  # SET dst, data
        arg = (r.randrange(8) << 5) | r.randrange(32)
    return (kind << 13) | (delay_sideset << 8) | arg


def _machine_config(r: random.Random, pio: int, machine: int) -> list[tuple]:
    base = SM_STRIDE * machine
    ops: list[tuple] = []
    which = r.randrange(4)
    if which == 0:
        clkdiv = (r.choice((0, 1, 1, 1, 2, 3, 5, 40, 300)) << 16) | (r.choice((0, 0, 0, 64, 128, 255)) << 8)
        ops.append(("write", pio, R.SM0_CLKDIV + base, clkdiv, 0))
    elif which == 1:
        execctrl = (
            (r.randrange(32) << 24)
            | (r.randrange(32) << 12)
            | (r.randrange(32) << 7)
            | (r.getrandbits(1) << 4)
            | r.randrange(16)
        )
        execctrl |= (r.getrandbits(1) << 29) | (r.getrandbits(1) << 30)
        if r.random() < 0.5:  # OUT_STICKY, INLINE_OUT_EN, OUT_EN_SEL
            execctrl |= (r.getrandbits(1) << 17) | (r.getrandbits(1) << 18) | (r.randrange(8) << 19)
        ops.append(("write", pio, R.SM0_EXECCTRL + base, execctrl, 0))
    elif which == 2:
        shiftctrl = (r.getrandbits(4) << 16) | (r.randrange(32) << 20) | (r.randrange(32) << 25)
        shiftctrl |= r.choice(
            (0,) * 16 + (1 << 30, 1 << 31, 3 << 30)
        )  # FJOIN_TX / FJOIN_RX / both: the FIFOs change size and are flushed
        ops.append(("write", pio, R.SM0_SHIFTCTRL + base, shiftctrl, 0))
    else:
        pinctrl = (
            (r.randrange(6) << 29) | (r.randrange(7) << 26) | (r.randrange(33) << 20) | (r.randrange(32) << 15)
            | (r.randrange(32) << 10) | (r.randrange(32) << 5) | r.randrange(30)
        )  # fmt: skip
        ops.append(("write", pio, R.SM0_PINCTRL + base, pinctrl, 0))
    return ops


_STICKY_WORDS = (
    lambda r: 0xE000 | r.randrange(32),  # SET PINS, data
    lambda r: 0xE080 | r.randrange(32),  # SET PINDIRS, data
    lambda r: 0x6000 | r.randrange(1, 5),  # OUT PINS, n
    lambda r: 0x6080 | r.randrange(1, 5),  # OUT PINDIRS, n
    lambda r: 0x8080,  # PULL NOBLOCK
    lambda r: 0xA001 + r.randrange(2),  # MOV PINS, X / Y
    lambda r: 0xA042,  # NOP
    lambda r: r.getrandbits(5) << 8 | 0xE000 | r.randrange(32),  # SET PINS with a delay / side-set field
)  # fmt: skip


def _sticky_scenario(r: random.Random, pio: int) -> list[tuple]:
    """The four machines of a PIO on overlapping pins, each with random OUT_STICKY / INLINE_OUT_EN / OUT_EN_SEL, running short programs of pin writes one cycle at a time, with forced
    instructions, TX data for OUT, restarts and enable changes in between - the situations of datasheet 3.5.6.1 (the highest-numbered machine's write wins a cycle, sticky writes come back)."""
    ops: list[tuple] = []
    window = r.randrange(0, 22)
    for machine in range(4):
        base = SM_STRIDE * machine
        top, bottom = r.randrange(4, 12), r.randrange(0, 4)
        execctrl = (top << 12) | (bottom << 7) | (r.getrandbits(1) << 29)
        if r.random() < 0.55:
            execctrl |= 1 << 17
        if r.random() < 0.4:
            execctrl |= (1 << 18) | (r.randrange(4) << 19)
        pinctrl = (
            (r.randrange(3) << 29) | (r.randrange(1, 4) << 26) | (r.randrange(1, 4) << 20)
            | ((window + r.randrange(3)) << 10) | ((window + r.randrange(3)) << 5) | (window + r.randrange(3))
        )  # fmt: skip
        ops.append(("write", pio, R.SM0_EXECCTRL + base, execctrl, 0))
        ops.append(("write", pio, R.SM0_PINCTRL + base, pinctrl, 0))
        ops.append(("write", pio, R.SM0_INSTR + base, bottom, 0))  # JMP bottom
    for word in range(12):
        ops.append(("write", pio, R.INSTR_MEM0 + 4 * word, r.choice(_STICKY_WORDS)(r), 0))
    ops.append(("write", pio, R.CTRL, r.randrange(1, 16), 0))
    for _ in range(r.randrange(8, 20)):
        roll = r.random()
        machine = r.randrange(4)
        if roll < 0.55:
            ops.append(("advance", pio, 1))
        elif roll < 0.65:
            ops.append(("write", pio, R.TXF0 + 4 * machine, r.getrandbits(32), 0))
        elif roll < 0.80:
            ops.append(("write", pio, R.SM0_INSTR + SM_STRIDE * machine, r.choice(_STICKY_WORDS)(r), 0))
        elif roll < 0.88:
            ops.append(("write", pio, R.CTRL, r.randrange(16) | (r.getrandbits(4) << 4 if r.random() < 0.4 else 0), 0))
        elif roll < 0.94:
            ops.append(("advance", pio, r.choice((2, 3, 8))))
        else:
            ops.append(("write", pio, R.SM0_EXECCTRL + SM_STRIDE * machine, r.getrandbits(32) & 0x00FFFFFF, 0))
    return ops


def _fjoin_scenario(r: random.Random, pio: int, machine: int) -> list[tuple]:
    """Joins (or un-joins) a machine's FIFOs, fills them past four entries from both sides - PUSH NOBLOCK executed through SMx_INSTR for RX, TXF writes for TX - and drains them with PULL
    NOBLOCK and RXF reads, so that the 8-deep FIFOs and the disabled ones (TXOVER, RXUNDER, both full and empty) are all met."""
    base = SM_STRIDE * machine
    join = r.choice((1 << 30, 1 << 31, 1 << 30, 1 << 31, 3 << 30, 0))
    ops: list[tuple] = [("write", pio, R.SM0_SHIFTCTRL + base, join | (r.getrandbits(4) << 16), 0)]
    for _ in range(r.choice((5, 6, 9, 10))):
        ops.append(("write", pio, R.SM0_INSTR + base, 0x8000, 0))  # PUSH NOBLOCK
        ops.append(("write", pio, R.TXF0 + 4 * machine, r.getrandbits(32), 0))
    ops.append(("read", pio, R.FSTAT))
    ops.append(("read", pio, R.FLEVEL))
    for _ in range(r.choice((5, 9))):
        ops.append(("read", pio, R.RXF0 + 4 * machine))
        ops.append(("write", pio, R.SM0_INSTR + base, 0x8080, 0))  # PULL NOBLOCK
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = [("pin_cfg", pin, r.choice((6, 6, 7, 7, 5, 31))) for pin in range(30)]
    for pio in range(2):
        for word in range(32):
            ops.append(("write", pio, R.INSTR_MEM0 + 4 * word, random_opcode(r), 0))
        for machine in range(4):
            for _ in range(4):
                ops.extend(_machine_config(r, pio, machine))
    while len(ops) < steps:
        pio = r.randrange(2)
        machine = r.randrange(4)
        if r.random() < 0.05:
            ops.extend(_sticky_scenario(r, pio))
        roll = r.random()
        if roll < 0.40:
            n = r.choice((1, 1, 1, 1, 2, 3, 4, 8, 16, 100, 2000, 600000))
            ops.append(("advance", pio, n))
        elif roll < 0.44:
            ops.extend(_machine_config(r, pio, machine))
        elif roll < 0.48:
            ops.extend(_fjoin_scenario(r, pio, machine))
        elif roll < 0.55:
            enable = r.getrandbits(4) if r.random() < 0.7 else 0xF
            restart = (r.getrandbits(4) << 4) if r.random() < 0.25 else 0
            clkdiv_restart = (r.getrandbits(4) << 8) if r.random() < 0.2 else 0
            ops.append(("write", pio, R.CTRL, enable | restart | clkdiv_restart, r.choice(ALIASES)))
        elif roll < 0.65:
            ops.append(("write", pio, R.TXF0 + 4 * machine, r.getrandbits(32), 0))
        elif roll < 0.71:
            ops.append(("read", pio, R.RXF0 + 4 * machine))
        elif roll < 0.75:
            ops.append(("write", pio, R.IRQ, r.getrandbits(8), r.choice(ALIASES)))
        elif roll < 0.78:
            ops.append(
                ("write", pio, R.IRQ_FORCE, r.getrandbits(8 if r.random() < 0.6 else 32), 0)
            )  # the reserved bits 31:8 included
        elif roll < 0.81:
            ops.append(
                (
                    "write",
                    pio,
                    r.choice((R.IRQ0_INTE, R.IRQ0_INTF, R.IRQ1_INTE, R.IRQ1_INTF)),
                    r.getrandbits(12),
                    r.choice(ALIASES),
                )
            )
        elif roll < 0.89:
            ops.append(("write", pio, R.SM0_INSTR + SM_STRIDE * machine, random_opcode(r), 0))
        elif roll < 0.92:
            ops.append(("write", pio, R.INSTR_MEM0 + 4 * r.randrange(32), random_opcode(r), 0))
        elif roll < 0.96:
            ops.append(("drive", r.randrange(30), r.choice((True, False, None))))
        elif roll < 0.97:
            ops.append(("write", pio, R.INPUT_SYNC_BYPASS, r.getrandbits(32), 0))
        elif roll < 0.98:
            ops.append(("read", pio, r.choice(SNAPSHOT_REGISTERS)))
        elif roll < 0.9825:
            ops.append(("reset", pio))
        else:
            ops.append(
                (
                    "write",
                    pio,
                    r.choice(
                        (
                            0x1F0,
                            0x2F0,
                            R.DBG_PADOUT,
                            R.DBG_PADOE,
                            R.DBG_CFGINFO,
                            R.FSTAT,
                            R.FLEVEL,
                            R.INTR,
                            R.IRQ0_INTS,
                            R.IRQ1_INTS,
                            R.RXF0 + 4 * machine,
                        )
                    ),
                    r.getrandbits(32),
                    r.choice(ALIASES),
                )
            )  # unimplemented registers (the warnings) and the read-only ones (silence)
    return ops[:steps]


# --- logic mutants of the pin-write arbitration: what the oracle must see ---------------------------------------------------------

# name -> (module, the exact text in it, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, str, int]] = {
    "inline_enable_ignored": ("sm", "        return bool((value >> ((self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F)) & 1)", "        return True", 1),
    "inline_enable_inverted": ("sm", "        return bool((value >> ((self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F)) & 1)", "        return not ((value >> ((self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F)) & 1)", 1),
    "inline_enable_bit_zero_only": ("sm", "        return bool((value >> ((self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F)) & 1)", "        return bool(value & 1)", 1),
    "inline_enable_bit_off_by_one": ("sm", "(self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F)) & 1)", "(self.exec_ctrl >> EXECCTRL_OUT_EN_SEL_LSB) & 0x1F) + 1) & 1)", 1),
    "inline_option_ignored": ("sm", "        if not self.exec_ctrl & EXECCTRL_INLINE_OUT_EN:\n            return True\n", "", 1),
    "disabled_write_still_asserts": ("sm", "        if not enable:\n            mask = 0\n", "", 1),
    "disabled_write_keeps_the_held_one": ("sm", "        if not enable:\n            mask = 0\n        if not pio.pin_routing():", "        if not enable:\n            return\n        if not pio.pin_routing():", 1),
    "sticky_holds_nothing": ("sm", "            if self.exec_ctrl & EXECCTRL_OUT_STICKY:\n                self.held_write = write\n", "", 1),
    "side_set_is_held_too": ("sm", "        else:\n            self.fresh_side = write\n", "        else:\n            self.fresh_side = write\n            if self.exec_ctrl & EXECCTRL_OUT_STICKY:\n                self.held_write = write\n", 1),
    "restart_keeps_the_held_write": ("sm", "        self.held_write = None  # \"any pin write left asserted due to OUT_STICKY\" (SM_RESTART, datasheet CTRL)\n", "", 1),
    "sticky_off_keeps_the_held_write": ("sm", "            if not self.exec_ctrl & EXECCTRL_OUT_STICKY:\n                self.held_write = None  # nothing is re-asserted without OUT_STICKY\n", "", 1),
    "machine_reset_keeps_the_held_write": ("sm", "        self.fresh_channel = self.fresh_side = self.held_write = None\n        self.waiting = False\n        self.wait_index", "        self.fresh_channel = self.fresh_side = None\n        self.waiting = False\n        self.wait_index", 1),
    "lower_machine_wins": ("pio", "        for machine in self.machines:\n            write = machine.fresh_channel if machine.fresh_channel is not None else machine.held_write\n", "        for machine in reversed(self.machines):\n            write = machine.fresh_channel if machine.fresh_channel is not None else machine.held_write\n", 1),
    "side_set_loses_its_own_overlap": ("pio", "            if write is not None:\n                self.apply_pin_write(*write)\n            if machine.fresh_side is not None:\n                self.apply_pin_write(*machine.fresh_side)\n", "            if machine.fresh_side is not None:\n                self.apply_pin_write(*machine.fresh_side)\n            if write is not None:\n                self.apply_pin_write(*write)\n", 1),
    "held_write_not_reasserted_when_idle": ("pio", "write = machine.fresh_channel if machine.fresh_channel is not None else machine.held_write", "write = machine.fresh_channel", 1),
    "held_image_mixes_levels_and_directions": ("pio", "                if is_directions:\n                    directions = ((directions & ~mask) | (data & mask)) & 0x3FFFFFFF\n                else:\n                    values", "                if False:\n                    directions = ((directions & ~mask) | (data & mask)) & 0x3FFFFFFF\n                else:\n                    values", 1),
    "no_resolve_after_an_overwrite": ("pio", "            self.resolve_due_fp = self.cycle_fp + (1 << 8)\n        else:", "            self.resolve_due_fp = NEVER_DUE\n        else:", 1),
    "resolve_two_cycles_late": ("pio", "            self.resolve_due_fp = self.cycle_fp + (1 << 8)\n        else:", "            self.resolve_due_fp = self.cycle_fp + (2 << 8)\n        else:", 1),
    "resolve_not_a_due_time": ("pio", "        self.next_due_fp = min(next_due_fp, self.resolve_due_fp)\n", "        self.next_due_fp = next_due_fp\n", 1),
    "resolve_unheard_by_the_pins": ("pio", "            self.resolve_due_fp = NEVER_DUE\n            self._check_changed_pins()\n", "            self.resolve_due_fp = NEVER_DUE\n", 1),
    "sticky_never_routes": ("pio", "            machine.held_write is not None or machine.exec_ctrl & EXECCTRL_OUT_STICKY for machine in self.machines", "            machine.held_write is not None for machine in self.machines", 1),
    "stop_leaves_the_overwrite_pending": ("pio", "                self.stopped = True\n                self.settle_held_writes()\n", "                self.stopped = True\n", 1),
    "pio_reset_keeps_the_resolve_time": ("pio", "        self.in_cycle = False\n        self.resolve_due_fp = NEVER_DUE\n        self.irq0_int_enable = 0", "        self.in_cycle = False\n        self.irq0_int_enable = 0", 1),
}  # fmt: skip

MUTANTS = tuple(MUTATIONS)


def mutant_rig(name: str) -> "Rig":
    """A rig whose PIOs are the pure-Python ones with one expression of the arbitration changed, to prove the comparison sees a change of *logic*."""
    import inspect
    import types

    from rp2040py.peripherals import _pio, _state_machine

    module_name, old, new, count = MUTATIONS[name]
    original = _state_machine if module_name == "sm" else _pio
    source = inspect.getsource(original)
    assert source.count(old) == count, (
        f"{name}: {old!r} occurs {source.count(old)} times in the reference, expected {count}"
    )
    mutated = types.ModuleType(f"{original.__name__}_mutant_{name}")
    exec(compile(source.replace(old, new), f"<{original.__name__} mutant {name}>", "exec"), mutated.__dict__)  # noqa: S102 - the point of the exercise
    return Rig("mutant", (_pio, mutated) if module_name == "sm" else (mutated, _state_machine))


# --- the comparison --------------------------------------------------------------------------------------------------------


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
            parts.append(f"{key}[{where[:6]}] {[value_a[i] for i in where[:6]]} vs {[value_b[i] for i in where[:6]]}")
        else:
            parts.append(f"{key}: {value_a} vs {value_b}")
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
            return Divergence(step, op, f"what left the block: {new_a[:6]} vs {new_b[:6]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    from rp2040py.peripherals import _state_machine

    counts: dict[str, int] = {}

    def count(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    original = _state_machine.StateMachine.execute_instruction
    original_enable = _state_machine.StateMachine.inline_out_enable
    from rp2040py.peripherals import _pio

    original_schedule = _pio.RPPIO.schedule_resolve

    def counting_enable(self: Any, value: int) -> bool:
        enabled = original_enable(self, value)
        if self.exec_ctrl & (1 << 18):
            count("inline.enabled" if enabled else "inline.gated")
        return enabled

    def counting_schedule(self: Any) -> None:
        original_schedule(self)
        count("arbitration.cycles")
        count("arbitration.overwritten", int(self.resolve_due_fp < (1 << 62)))

    _state_machine.StateMachine.inline_out_enable = counting_enable  # type: ignore[method-assign]
    _pio.RPPIO.schedule_resolve = counting_schedule  # type: ignore[method-assign]

    def counting(self: Any, opcode: int) -> None:
        kind, arg = (opcode >> 13) & 7, opcode & 0xFF
        count(f"instr{kind}")
        if kind == 0:
            count(f"jmp.cond{arg >> 5}")
        elif kind == 1:
            count(f"wait.src{(arg >> 5) & 3}")
        elif kind == 4:
            count("pull" if arg & 0x80 else "push")
        original(self, opcode)

    _state_machine.StateMachine.execute_instruction = counting  # type: ignore[method-assign]
    try:
        rig = Rig("pure")
        for op in ops:
            _step(rig, op)
            for event in rig.log:
                count(event[0])
            rig.log.clear()
            for block in rig.chip.pio:
                for machine in block.machines:
                    count("enabled", int(machine.enabled))
                    count("waiting", int(machine.waiting))
                    count("wait_pin", int(machine.waiting and int(machine.wait_type) == 1))
                    count("wait_irq", int(machine.waiting and int(machine.wait_type) == 4))
                    count("wait_fifo", int(machine.waiting and int(machine.wait_type) in (2, 3, 5)))
                    join = (machine.shift_ctrl >> 30) & 3
                    count("fjoin.tx", int(join == 1))
                    count("fjoin.rx", int(join == 2))
                    count("fjoin.both", int(join == 3))
                    count("sticky.held", int(machine.held_write is not None))
                    count("sticky.on", int(bool(machine.exec_ctrl & (1 << 17))))
                    count("fjoin.tx_deep", int(machine.tx_fifo.item_count > 4))
                    count("fjoin.rx_deep", int(machine.rx_fifo.item_count > 4))
    finally:
        _state_machine.StateMachine.execute_instruction = original  # type: ignore[method-assign]
        _state_machine.StateMachine.inline_out_enable = original_enable  # type: ignore[method-assign]
        _pio.RPPIO.schedule_resolve = original_schedule  # type: ignore[method-assign]
    return counts
