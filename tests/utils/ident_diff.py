"""A lockstep differential oracle for the two chip-identity blocks, SYSINFO and TBMAN (docs/records/0096-cpp-mcu-core.md, "The small blocks"): the same generated stimulus drives two chips and
everything observable is compared after every step. One file for both, as the record decided for the trivial blocks: they have no state and no callbacks, only registers and the warnings.

One chip's block is the *pure-Python* reference (``peripherals/_sysinfo.py`` / ``_tbman.py``, built explicitly because the facade would hand out the native one), the other's is whatever the facade
gives (the C++ block in a native build). The operations are reads and writes of **every register offset through the bus and its XOR/SET/CLR aliases**, of offsets that are no register (so the
warnings are compared, the ones above 0x1000 included), a change of the **bootrom version byte** (the SYSINFO CHIP_ID revision follows it) and ``reset()``. After each step the register file read
through the bus, ``raw_write_value`` and the ordered log of the warnings are compared; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _sysinfo as S
from rp2040py.peripherals import _tbman as T
from rp2040py.rp2040 import RP2040

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
BLOCKS: dict[str, dict[str, Any]] = {
    "sysinfo": {
        "base": 0x40000000,
        "cls": S.RP2040SysInfo,
        "name": "SYSINFO_BASE",
        "registers": (S.CHIP_ID, S.PLATFORM, S.GITREF_RP2040),
        "unimplemented": (0x8, 0xC, 0x10, 0x3C, 0x44, 0x80, 0xFFC),
    },
    "tbman": {
        "base": 0x4006C000,
        "cls": T.RPTBMAN,
        "name": "TBMAN_BASE",
        "registers": (T.PLATFORM,),
        "unimplemented": (0x4, 0x8, 0x40, 0xFFC),
    },
}
VERSIONS = (0, 1, 2, 3, 4, 0xFF)  # the bootrom's version byte (ROM address 0x13): 1 B0, 2 B1, 3 B2


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

    def set_rom_version(self, version: int) -> None:
        """What `load_bootrom()` does to ROM address 0x13: the top byte of the word at 0x10."""
        self.chip.bootrom[4] = (self.chip.bootrom[4] & 0x00FFFFFF) | (version << 24)

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip, base = self.chip, self.spec["base"]
        if kind == "write":
            chip.write_uint32(base + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(base + op[2] + op[1]))
        elif kind == "rom":
            self.set_rom_version(op[1])
        elif kind == "reset":
            self.peripheral.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        base = self.spec["base"]
        return {
            "regs": tuple(int(self.chip.read_uint32(base + offset)) for offset in self.spec["registers"]),
            "raw": int(self.peripheral.raw_write_value),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

SYSINFO_MUTANTS = (
    "revision_always_2",
    "revision_2_from_b1",
    "revision_ignores_rom",
    "manufacturer_926",
    "part_wrong",
    "rom_version_wrong_byte",
    "platform_1",
    "gitref_wrong",
    "write_warns",
    "chip_id_write_warns",
    "unimplemented_read_zero",
)
TBMAN_MUTANTS = ("platform_0", "platform_3", "write_warns", "unimplemented_read_zero")


def mutant_rig(block: str, name: str) -> Rig:
    """A rig whose block is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""
    base = BLOCKS[block]["cls"]

    class Mutant(base):  # type: ignore[valid-type, misc]
        def read_uint32(self, offset: int) -> int:
            if block == "sysinfo":
                version = S.rom_version(self.rp2040)
                if offset == S.CHIP_ID and name == "revision_always_2":
                    return (2 << 28) | (S.PART << 12) | S.MANUFACTURER
                if offset == S.CHIP_ID and name == "revision_2_from_b1":
                    return ((2 if version >= 2 else 1) << 28) | (S.PART << 12) | S.MANUFACTURER
                if offset == S.CHIP_ID and name == "revision_ignores_rom":
                    return (1 << 28) | (S.PART << 12) | S.MANUFACTURER
                if offset == S.CHIP_ID and name == "manufacturer_926":
                    return (S.chip_revision(version) << 28) | (S.PART << 12) | 0x926
                if offset == S.CHIP_ID and name == "part_wrong":
                    return (S.chip_revision(version) << 28) | (3 << 12) | S.MANUFACTURER
                if offset == S.CHIP_ID and name == "rom_version_wrong_byte":
                    wrong = (self.rp2040.bootrom[S.ROM_VERSION_WORD] >> 16) & 0xFF
                    return (S.chip_revision(wrong) << 28) | (S.PART << 12) | S.MANUFACTURER
                if offset == S.PLATFORM and name == "platform_1":
                    return 1
                if offset == S.GITREF_RP2040 and name == "gitref_wrong":
                    return 0xE0C912E9
            elif offset == T.PLATFORM and name in ("platform_0", "platform_3"):
                return 0 if name == "platform_0" else 3
            if name == "unimplemented_read_zero" and offset not in BLOCKS[block]["registers"]:
                return 0
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if name == "write_warns" and offset in BLOCKS[block]["registers"]:
                BLOCKS[block]["cls"].__mro__[1].write_uint32(
                    self, offset, value
                )  # BasePeripheral: "unimplemented peripheral write"
                return
            if name == "chip_id_write_warns" and offset == S.CHIP_ID:
                BLOCKS[block]["cls"].__mro__[1].write_uint32(self, offset, value)
                return
            super().write_uint32(offset, value)

    return Rig(block, "mutant", Mutant)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random) -> int:
    return r.choice((0, 1, 2, 3, 0xFF, 0x927, 0x10002927, 0xFFFFFFFF, r.getrandbits(32)))


def generate(block: str, seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    spec = BLOCKS[block]
    registers, unimplemented = spec["registers"], spec["unimplemented"]
    ops: list[tuple] = [("rom", 2)]  # the default bootrom, B1
    while len(ops) < steps:
        roll = r.random()
        alias = 0 if r.random() < 0.5 else r.choice(ALIAS_BASES[1:])
        if roll < 0.30:
            ops.append(("read", r.choice(registers), alias))
        elif roll < 0.50:
            ops.append(("read", r.choice(unimplemented), alias if r.random() < 0.5 else 0))
        elif roll < 0.70:
            ops.append(("write", r.choice(registers), _value(r), alias))
        elif roll < 0.85:
            ops.append(("write", r.choice(unimplemented), _value(r), alias if r.random() < 0.5 else 0))
        elif roll < 0.95:
            ops.append(("rom", r.choice(VERSIONS)))
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
        elif op[0] == "rom":
            count(f"rom.{op[1]}")
        else:
            count(op[0])
        _step(rig, op)
        count("log.entries") if len(rig.log) > before else None
    return counts
