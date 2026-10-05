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
from rp2040py.rp2040 import RP2040

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

MACHINE_FIELDS = (
    "x", "y", "pc", "input_shift_reg", "input_shift_count", "output_shift_reg", "output_shift_count", "cycles", "enabled", "waiting",
    "wait_type", "wait_index", "wait_polarity", "wait_delay", "next_due_fp", "due_rearmed", "exec_valid", "update_pc", "exec_opcode",
    "out_pin_values", "out_pin_direction", "div_fp", "exec_ctrl", "shift_ctrl", "pin_ctrl",
)  # fmt: skip
BLOCK_FIELDS = (
    "irq", "fdebug", "tx_stall", "rx_stall", "input_sync_bypass", "pin_values", "pin_directions", "old_pin_values", "old_pin_directions",
    "cycle_fp", "next_due_fp", "backlog_drops", "stopped", "irq0_int_enable", "irq0_int_force", "irq1_int_enable", "irq1_int_force",
)  # fmt: skip


class Rig:
    """One chip plus the logs of everything that left its PIOs."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.chip = RP2040()
        # A (fake) owner for the chip, so a CTRL write does not run the "no Simulator" fallback that steps the block inline: the stimulus calls advance() itself,
        # as the batch loop does.
        self.chip.simulator = object()
        if kind == "pure":
            self._replace_pios_with_the_pure_python_ones()
        self.log: list[tuple] = []
        self._tap()

    def _replace_pios_with_the_pure_python_ones(self) -> None:
        from rp2040py.peripherals import _pio, _state_machine

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
        chip.dma.set_dreq = lambda channel: log.append(("dreq", int(channel), 1))  # type: ignore[method-assign]
        chip.dma.clear_dreq = lambda channel: log.append(("dreq", int(channel), 0))  # type: ignore[method-assign]
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
            chip.pio[op[1]].reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip = self.chip
        out: dict[str, Any] = {}
        for index, block in enumerate(chip.pio):
            out[f"pio{index}.regs"] = tuple(self.read(index, offset) for offset in SNAPSHOT_REGISTERS)
            out[f"pio{index}.block"] = tuple(int(getattr(block, name)) for name in BLOCK_FIELDS)
            out[f"pio{index}.instructions"] = tuple(int(word) for word in block.instructions)
            for number, machine in enumerate(block.machines):
                out[f"pio{index}.sm{number}"] = tuple(int(getattr(machine, name)) for name in MACHINE_FIELDS)
                out[f"pio{index}.sm{number}.tx"] = tuple(int(v) for v in machine.tx_fifo.items)
                out[f"pio{index}.sm{number}.rx"] = tuple(int(v) for v in machine.rx_fifo.items)
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
        ops.append(("write", pio, R.SM0_EXECCTRL + base, execctrl, 0))
    elif which == 2:
        shiftctrl = (r.getrandbits(4) << 16) | (r.randrange(32) << 20) | (r.randrange(32) << 25)
        ops.append(("write", pio, R.SM0_SHIFTCTRL + base, shiftctrl, 0))
    else:
        pinctrl = (
            (r.randrange(6) << 29) | (r.randrange(7) << 26) | (r.randrange(33) << 20) | (r.randrange(32) << 15)
            | (r.randrange(32) << 10) | (r.randrange(32) << 5) | r.randrange(30)
        )  # fmt: skip
        ops.append(("write", pio, R.SM0_PINCTRL + base, pinctrl, 0))
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
        roll = r.random()
        if roll < 0.40:
            n = r.choice((1, 1, 1, 1, 2, 3, 4, 8, 16, 100, 2000, 600000))
            ops.append(("advance", pio, n))
        elif roll < 0.48:
            ops.extend(_machine_config(r, pio, machine))
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
            ops.append(("write", pio, R.IRQ_FORCE, r.getrandbits(8), 0))
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
                ("write", pio, r.choice((0x1F0, 0x2F0, R.DBG_PADOUT)), r.getrandbits(32), 0)
            )  # unimplemented registers: the warnings
    return ops[:steps]


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
    finally:
        _state_machine.StateMachine.execute_instruction = original  # type: ignore[method-assign]
    return counts
