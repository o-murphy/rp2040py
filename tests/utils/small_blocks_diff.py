"""A lockstep differential oracle for three small register blocks, BUSCTRL, SYSCFG and VREG_AND_CHIP_RESET (docs/records/0096-cpp-mcu-core.md, "The small blocks"): the same generated stimulus drives
two chips and everything observable is compared after every step. One file for the three, as the record decided for the blocks that are only registers and warnings (the sibling of ``ident_diff``).

One chip's block is the *pure-Python* reference (``peripherals/_busctrl.py`` / ``_syscfg.py`` / ``_vreg_and_chip_reset.py``, built explicitly because the facade would hand out the native one), the
other's is whatever the facade gives (the C++ block in a native build). The operations are reads and writes of **every register offset through the bus and its XOR/SET/CLR aliases**, of offsets that
are no register (so the warnings are compared, the ones above 0x1000 included), ``reset()`` (BUSCTRL, SYSCFG), a record of the **reset cause** (VREG_AND_CHIP_RESET) and a write of the core's NMI mask
**from the CPU's side** (SYSCFG's PROC0_NMI_MASK is the core's). After each step the register file read through the bus, the block's attributes, ``raw_write_value`` and the ordered log of the
warnings are compared; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _busctrl as B
from rp2040py.peripherals import _syscfg as S
from rp2040py.peripherals import _vreg_and_chip_reset as V
from rp2040py.rp2040 import RP2040

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
BLOCKS: dict[str, dict[str, Any]] = {
    "busctrl": {
        "base": 0x40030000,
        "cls": B.RPBUSCTRL,
        "name": "BUSCTRL_BASE",
        "registers": (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24),
        "unimplemented": (0x28, 0x2C, 0x40, 0x80, 0xFFC),
        "attrs": ("bus_priority", "perf_ctr", "perf_sel"),
        "ops": ("reset", "count"),
    },
    "syscfg": {
        "base": 0x40004000,
        "cls": S.RP2040SysCfg,
        "name": "SYSCFG",
        "registers": (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18),
        "unimplemented": (0x1C, 0x20, 0x40, 0xFFC),
        "attrs": (
            "proc1_nmi_mask",
            "proc_config",
            "proc_in_sync_bypass",
            "proc_in_sync_bypass_hi",
            "dbgforce",
            "mempowerdown",
        ),
        "ops": ("reset", "nmi"),
    },
    "vreg": {
        "base": 0x40064000,
        "cls": V.RPVREGAndChipReset,
        "name": "VREG_AND_CHIP_RESET_BASE",
        "registers": (0x0, 0x4, 0x8),
        "unimplemented": (0xC, 0x10, 0x40, 0xFFC),
        "attrs": ("vreg", "bod", "chip_reset"),
        "ops": ("cause", "psm_flag"),
    },
}
CAUSES = (V.HAD_POR, V.HAD_RUN, V.HAD_PSM_RESTART)


def _freeze(value: Any) -> Any:
    return tuple(value) if hasattr(value, "__iter__") else value


class Rig:
    """One chip plus the log of every warning its block produced."""

    def __init__(self, block: str, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.block = block
        self.spec = BLOCKS[block]
        self.chip = RP2040()
        self.log: list[tuple] = []
        if kind == "pure":
            self._replace_the_block(self.spec["cls"])
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_block(factory)
        self._tap()

    def _replace_the_block(self, factory: Callable[..., Any]) -> None:
        self.chip.peripherals[self.spec["base"] >> 12] = factory(self.chip, self.spec["name"])

    def _tap(self) -> None:
        log = self.log
        for method in ("warning", "error", "info", "debug"):
            setattr(
                self.chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", _m, str(name), str(message))),
            )

    @property
    def peripheral(self) -> Any:
        return self.chip.peripherals[self.spec["base"] >> 12]

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip, base = self.chip, self.spec["base"]
        if kind == "write":
            chip.write_uint32(base + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(base + op[2] + op[1]))
        elif kind == "reset":
            self.peripheral.reset()
        elif kind == "count":
            self.peripheral.perf_ctr[op[1]] = op[
                2
            ]  # what a bus fabric that counted would do: nothing on the bus can make a counter non-zero
        elif kind == "nmi":
            chip.core.interrupt_nmi_mask = op[1]  # the CPU's side of PROC0_NMI_MASK
        elif kind == "psm_flag":
            self.peripheral.chip_reset |= (
                V.PSM_RESTART_FLAG
            )  # what a debugger's psm_restart does (tests/test_watchdog_reset.py): nothing on the bus can set it
        elif kind == "cause":
            self.peripheral.record_reset_cause(op[1])
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        base, peripheral = self.spec["base"], self.peripheral
        return {
            "regs": tuple(int(self.chip.read_uint32(base + offset)) for offset in self.spec["registers"]),
            "attrs": tuple(_freeze(getattr(peripheral, name)) for name in self.spec["attrs"]),
            "raw": int(peripheral.raw_write_value),
            "nmi": int(self.chip.core.interrupt_nmi_mask),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------
# name -> (offset, read-override or None, write-override or None); an override gets the block, the value and returns the new behaviour's result.

BUSCTRL_MUTANTS = (
    "priority_mask_wrong",
    "priority_all_bits",
    "ack_reads_0",
    "ack_writable",
    "counter_write_keeps",
    "counter_write_sets",
    "select_mask_wrong",
    "select_reset_0",
    "reset_keeps_priority",
    "reset_keeps_counter",
    "unimplemented_read_zero",
    "write_to_ack_warns",
)
SYSCFG_MUTANTS = (
    "proc_config_all_writable",
    "proc_config_reset_wrong",
    "proc1_mask_16_bits",
    "bypass_mask_wrong",
    "bypass_hi_mask_wrong",
    "dbgforce_reset_wrong",
    "dbgforce_all_writable",
    "mempowerdown_mask_wrong",
    "nmi_not_the_cores",
    "reset_keeps_dbgforce",
    "reset_keeps_nmi",
    "unimplemented_read_zero",
)
VREG_MUTANTS = (
    "vreg_writes_all",
    "vreg_hiz_not_writable",
    "bod_writes_all",
    "chip_reset_status_writable",
    "psm_flag_not_cleared",
    "psm_flag_set_by_write",
    "cause_keeps_other_flags",
    "cause_drops_psm_flag",
    "vreg_reset_wrong",
    "unimplemented_read_zero",
)


def mutant_rig(block: str, name: str) -> Rig:
    """A rig whose block is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""
    base = BLOCKS[block]["cls"]
    registers = BLOCKS[block]["registers"]

    class Mutant(base):  # type: ignore[valid-type, misc]
        def __init__(self, rp2040: Any, nm: str) -> None:
            super().__init__(rp2040, nm)
            self._shadow_nmi = 0

        # -- reads
        def read_uint32(self, offset: int) -> int:
            if name == "unimplemented_read_zero" and offset not in registers:
                return 0
            if block == "busctrl" and name == "ack_reads_0" and offset == B.BUS_PRIORITY_ACK:
                return 0
            if block == "syscfg" and name == "nmi_not_the_cores" and offset == S.PROC0_NMI_MASK:
                return self._shadow_nmi
            return super().read_uint32(offset)

        # -- writes
        def write_uint32(self, offset: int, value: int) -> None:
            value &= 0xFFFFFFFF
            if block == "busctrl":
                if name == "priority_mask_wrong" and offset == B.BUS_PRIORITY:
                    self.bus_priority = value & 0x1110
                    return
                if name == "priority_all_bits" and offset == B.BUS_PRIORITY:
                    self.bus_priority = value
                    return
                if name == "ack_writable" and offset == B.BUS_PRIORITY_ACK:
                    self.bus_priority = value & 1
                    return
                if name == "write_to_ack_warns" and offset == B.BUS_PRIORITY_ACK:
                    base.__mro__[1].write_uint32(self, offset, value)
                    return
                if name == "counter_write_keeps" and offset in (B.PERFCTR0, B.PERFCTR1, B.PERFCTR2, B.PERFCTR3):
                    return
                if name == "counter_write_sets" and offset == B.PERFCTR2:
                    self.perf_ctr[2] = value & 0xFFFFFF
                    return
                if name == "select_mask_wrong" and offset == B.PERFSEL1:
                    self.perf_sel[1] = value & 0x3F
                    return
            elif block == "syscfg":
                if name == "proc_config_all_writable" and offset == S.PROC_CONFIG:
                    self.proc_config = value
                    return
                if name == "proc1_mask_16_bits" and offset == S.PROC1_NMI_MASK:
                    self.proc1_nmi_mask = value & 0xFFFF
                    return
                if name == "bypass_mask_wrong" and offset == S.PROC_IN_SYNC_BYPASS:
                    self.proc_in_sync_bypass = value & 0xFFFFFFFF
                    return
                if name == "bypass_hi_mask_wrong" and offset == S.PROC_IN_SYNC_BYPASS_HI:
                    self.proc_in_sync_bypass_hi = value & 0xFF
                    return
                if name == "dbgforce_all_writable" and offset == S.DBGFORCE:
                    self.dbgforce = value & 0xFF
                    return
                if name == "mempowerdown_mask_wrong" and offset == S.MEMPOWERDOWN:
                    self.mempowerdown = value & 0x7F
                    return
                if name == "nmi_not_the_cores" and offset == S.PROC0_NMI_MASK:
                    self._shadow_nmi = value
                    return
            else:
                if name == "vreg_writes_all" and offset == V.VREG:
                    self.vreg = value
                    return
                if name == "vreg_hiz_not_writable" and offset == V.VREG:
                    self.vreg = (self.vreg & ~0xF3) | (value & 0xF1)
                    return
                if name == "bod_writes_all" and offset == V.BOD:
                    self.bod = value
                    return
                if name == "chip_reset_status_writable" and offset == V.CHIP_RESET:
                    self.chip_reset = value & (V.HAD_POR | V.HAD_RUN | V.HAD_PSM_RESTART | V.PSM_RESTART_FLAG)
                    return
                if name == "psm_flag_not_cleared" and offset == V.CHIP_RESET:
                    return
                if name == "psm_flag_set_by_write" and offset == V.CHIP_RESET and value & V.PSM_RESTART_FLAG:
                    self.chip_reset ^= V.PSM_RESTART_FLAG
                    return
            super().write_uint32(offset, value)

        # -- reset / cause
        def reset(self) -> None:
            kept_priority = getattr(self, "bus_priority", 0)
            kept_counter = self.perf_ctr[3] if block == "busctrl" else 0
            kept_dbgforce = getattr(self, "dbgforce", 0)
            kept_nmi = self.rp2040.core.interrupt_nmi_mask
            super().reset()
            if name == "select_reset_0" and block == "busctrl":
                self.perf_sel[0] = 0
            elif name == "reset_keeps_priority":
                self.bus_priority = kept_priority
            elif name == "reset_keeps_counter":
                self.perf_ctr[3] = kept_counter
            elif name == "reset_keeps_dbgforce":
                self.dbgforce = kept_dbgforce
            elif name == "reset_keeps_nmi":
                self.rp2040.core.interrupt_nmi_mask = kept_nmi
            elif name == "proc_config_reset_wrong":
                self.proc_config = 0
            elif name == "dbgforce_reset_wrong":
                self.dbgforce = 0

        def record_reset_cause(self, flag: int) -> None:
            if name == "cause_keeps_other_flags":
                self.chip_reset |= flag
                return
            if name == "cause_drops_psm_flag":
                self.chip_reset = flag
                return
            super().record_reset_cause(flag)

    rig = Rig(block, "mutant", Mutant)
    if block == "vreg" and name == "vreg_reset_wrong":
        rig.peripheral.vreg = 0xB0
    return rig


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random) -> int:
    return r.choice(
        (0, 1, 2, 3, 0x1F, 0x1111, 0xF1, 0xF3, 0xFF, 0x100, 0x1000000, 0xFFFFFFFF, r.getrandbits(32), r.getrandbits(32))
    )


def generate(block: str, seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    spec = BLOCKS[block]
    registers, unimplemented, extra = spec["registers"], spec["unimplemented"], spec["ops"]
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        alias = 0 if r.random() < 0.5 else r.choice(ALIAS_BASES[1:])
        if roll < 0.25:
            ops.append(("read", r.choice(registers), alias))
        elif roll < 0.35:
            ops.append(("read", r.choice(unimplemented), alias if r.random() < 0.5 else 0))
        elif roll < 0.75:
            ops.append(("write", r.choice(registers), _value(r), alias))
        elif roll < 0.88:
            ops.append(("write", r.choice(unimplemented), _value(r), alias if r.random() < 0.5 else 0))
        elif "cause" in extra:
            ops.append(("cause", r.choice(CAUSES)) if r.random() < 0.6 else ("psm_flag",))
        elif "count" in extra and r.random() < 0.6:
            ops.append(("count", r.randrange(4), r.getrandbits(24)))
        elif "nmi" in extra and r.random() < 0.5:
            ops.append(("nmi", r.getrandbits(32)))
        else:
            ops.append(("reset",))
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


def run_pair(
    block: str,
    ops: list[tuple],
    *,
    reference: "Callable[[], Rig] | None" = None,
    candidate: "Callable[[], Rig] | None" = None,
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a = reference() if reference else Rig(block, "pure")
    b = candidate() if candidate else Rig(block, "default")
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
            return Divergence(step, op, f"{snapshot_a} vs {snapshot_b}")
    return None


def coverage(block: str, ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig(block, "pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    registers = BLOCKS[block]["registers"]
    for op in ops:
        before = len(rig.log)
        if op[0] == "read":
            count("read.reg" if op[1] in registers else "read.unimplemented")
            if op[2]:
                count("read.alias")
        elif op[0] == "write":
            count("write.reg" if op[1] in registers else "write.unimplemented")
            if op[3]:
                count("write.alias")
        else:
            count(op[0])
        _step(rig, op)
        if len(rig.log) > before:
            count("log.entries")
    return counts
