"""Generates tests/cpp/pwm_vectors.inc: scenarios for the C++ PWM checks (tests/cpp/test_pwm.cpp) with their expectations produced by the pure-Python reference.

``python tests/utils/pwm_cpp_vectors.py`` rewrites the file. Each scenario is a list of operations (writes, reads, time, a B input changing, a poke of the direction word, reset) that is
run on the reference ``peripherals/_pwm.RPPWM`` against a stub chip (a ``SimulationClock``, a DMA, an interrupt line, pins whose input levels the scenario sets, a logger); everything that
leaves the block and every value read is recorded as an event, in order, with the simulated time. The C++ test replays the same operations against ``core/pwm.hpp`` with a recording host and
compares the two event streams, so the translation is pinned to the reference rather than to a second reading of it. (The end-to-end proof is tests/test_pwm_diff.py.)
"""

import re
import sys
from pathlib import Path
from typing import Any

from rp2040py.clock._simulation_clock import SimulationClock
from rp2040py.peripherals import _pwm as P

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "cpp" / "pwm_vectors.inc"

# operation codes: W write(offset, value, atomic), R read(offset), T tick(ns), IN input(pin, level) + gpio_on_input, SET input level only, DIR poke gpio_direction, RESET
# event codes:     IRQ(level), DREQ(channel), PIN(pin, gpio_value), WARN(kind, offset, value), READ(offset, value)
W, R, T, IN, SET, DIR, RESET = range(7)
IRQ, DREQ, PIN, WARN, READ = range(5)
CSR, DIV, CTR, CC, TOP = 0x00, 0x04, 0x08, 0x0C, 0x10
EN, INTR, INTE, INTF, INTS = 0xA0, 0xA4, 0xA8, 0xAC, 0xB0
EN_BIT, PH_CORRECT, A_INV, B_INV = 1, 2, 4, 8


def ch(n: int, reg: int) -> int:
    return n * 0x14 + reg


def all_reads() -> list[tuple]:
    return [(R, c * 0x14 + r) for c in range(8) for r in (CSR, DIV, CTR, CC, TOP)] + [
        (R, o) for o in (EN, INTR, INTE, INTF, INTS)
    ]


class _Pin:
    def __init__(self, stub: "Stub", index: int) -> None:
        self.stub, self.index = stub, index

    @property
    def input_value(self) -> bool:
        return bool(self.stub.inputs >> self.index & 1)

    def check_for_updates(self) -> None:
        self.stub.events.append((PIN, self.stub.clock.nanos, self.index, self.stub.pwm.gpio_value))


class _Dma:
    def __init__(self, stub: "Stub") -> None:
        self.stub = stub

    def set_dreq(self, channel: int) -> None:
        self.stub.events.append((DREQ, self.stub.clock.nanos, int(channel) - 24, 0))


class _Logger:
    def __init__(self, stub: "Stub") -> None:
        self.stub = stub

    def warning(self, name: str, message: str) -> None:
        if m := re.fullmatch(r"Unimplemented peripheral read from 0x([0-9a-f]+)", message):
            event = (WARN, self.stub.clock.nanos, 0, int(m[1], 16), 0)
        elif message == "Unimplemented read from peripheral in the atomic operation region":
            event = (WARN, self.stub.clock.nanos, 1, 0, 0)
        elif m := re.fullmatch(r"Unimplemented peripheral write to 0x([0-9a-f]+): 0x([0-9a-f]+)", message):
            event = (WARN, self.stub.clock.nanos, 2, int(m[1], 16), int(m[2], 16))
        else:
            raise AssertionError(message)
        self.stub.events.append(event)

    debug = info = error = warning


class Stub:
    """The little of a chip the PWM touches."""

    def __init__(self) -> None:
        self.clock = SimulationClock()
        self.clk_sys = 125_000_000
        self.events: list[tuple] = []
        self.inputs = 0
        self.gpio = [_Pin(self, i) for i in range(30)]
        self.dma = _Dma(self)
        self.logger = _Logger(self)
        self.pwm: Any = None

    def set_interrupt(self, irq: int, value: bool) -> None:
        self.events.append((IRQ, self.clock.nanos, int(bool(value)), 0))


def run(ops: list[tuple]) -> list[tuple]:
    stub = Stub()
    pwm = P.RPPWM(stub, "PWM")
    stub.pwm = pwm
    pwm.reset()
    stub.events.clear()  # the construction and the first reset are not part of the scenario
    for op in ops:
        kind = op[0]
        if kind == W:
            if op[3] == 0:
                pwm.write_uint32(op[1], op[2])
            else:
                pwm.write_uint32_atomic(op[1], op[2], op[3])
        elif kind == R:
            stub.events.append((READ, stub.clock.nanos, op[1], pwm.read_uint32(op[1]) & 0xFFFFFFFF))
        elif kind == T:
            stub.clock.tick(op[1])
        elif kind == IN:
            stub.inputs = (stub.inputs & ~(1 << op[1])) | (int(op[2]) << op[1])
            pwm.gpio_on_input(op[1])
        elif kind == SET:
            stub.inputs = (stub.inputs & ~(1 << op[1])) | (int(op[2]) << op[1])
        elif kind == DIR:
            pwm.gpio_direction = op[1]
        elif kind == RESET:
            pwm.reset()
    return stub.events


def _w(offset: int, value: int, atomic: int = 0) -> tuple:
    return (W, offset, value, atomic)


def scenarios() -> dict[str, list[tuple]]:
    s: dict[str, list[tuple]] = {}
    s["power_on_registers"] = all_reads()
    s["free_running_channel0"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, CC), (1 << 16) | 2),
        _w(ch(0, CSR), EN_BIT),
        (T, 100.0),
        (T, 7.0),
        *all_reads(),
    ]
    s["free_running_second_pair_and_inversion"] = [
        _w(ch(2, TOP), 9),
        _w(ch(2, CC), (3 << 16) | 5),
        _w(ch(2, CSR), EN_BIT | A_INV),
        (T, 300.0),
        _w(ch(2, CSR), EN_BIT | B_INV),
        (T, 300.0),
        *all_reads(),
    ]
    s["channel7_has_no_second_pair"] = [
        _w(ch(7, TOP), 4),
        _w(ch(7, CC), (2 << 16) | 3),
        _w(ch(7, CSR), EN_BIT),
        (T, 200.0),
        *all_reads(),
    ]
    s["phase_correct"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, CC), (1 << 16) | 2),
        _w(ch(0, CSR), EN_BIT | PH_CORRECT),
        (T, 200.0),
        *all_reads(),
    ]
    s["double_buffering"] = [
        _w(ch(1, TOP), 5),
        _w(ch(1, CC), (1 << 16) | 2),
        _w(ch(1, CSR), EN_BIT),
        (T, 20.0),
        _w(ch(1, CC), (4 << 16) | 4),  # held until the next wrap
        _w(ch(1, TOP), 8),
        (T, 15.0),
        (R, ch(1, CC)),
        (R, ch(1, TOP)),
        (T, 200.0),
        *all_reads(),
    ]
    s["prescaler_and_ctr"] = [
        _w(ch(3, DIV), 0x28),
        _w(ch(3, TOP), 7),
        _w(ch(3, CC), (2 << 16) | 3),
        _w(ch(3, CTR), 5),
        _w(ch(3, CSR), EN_BIT),
        (R, ch(3, CTR)),
        (T, 80.0),
        (R, ch(3, CTR)),
        _w(ch(3, DIV), 0x18),
        (T, 160.0),
        (R, ch(3, CTR)),
        _w(ch(3, DIV), 0),
        (R, ch(3, DIV)),
        (T, 400.0),
        *all_reads(),
    ]
    s["interrupts_and_dreq"] = [
        _w(ch(0, TOP), 2),
        _w(INTE, 0x1),
        _w(ch(0, CSR), EN_BIT),
        (T, 60.0),
        (R, INTR),
        (R, INTS),
        _w(INTR, 0x1),
        (R, INTR),
        _w(INTF, 0x80),
        (R, INTS),
        _w(INTE, 0x100 | 0x2),
        _w(INTF, 0),
        _w(INTE, 0xFF, 2),  # SET
        _w(INTE, 0x0F, 3),  # CLR
        _w(INTE, 0xA5, 1),  # XOR
        (T, 60.0),
        *all_reads(),
    ]
    s["interrupt_bits_are_per_channel"] = [
        _w(ch(0, TOP), 2),
        _w(ch(2, TOP), 4),
        _w(INTE, 0x05),
        _w(EN, 0x05),
        (T, 80.0),
        (R, INTR),
        _w(INTR, 0x04),
        (R, INTR),
        (R, INTS),
        _w(INTR, 0x01, 0),
        (R, INTR),
        (T, 40.0),
        (R, INTR),
        _w(INTR, 0x00),
        (R, INTR),
        _w(INTR, 0x1FF),
        (R, INTR),
    ]
    s["en_register_with_a_gated_channel"] = [
        (DIR, 0),
        _w(ch(0, TOP), 3),
        _w(ch(0, CSR), 1 << 4),  # gated, not enabled
        _w(EN, 0x01),  # enabling through EN reads the B pin
        (T, 40.0),
        (SET, 1, 1),
        _w(EN, 0x01),
        (IN, 1, 1),
        (T, 40.0),
        _w(EN, 0x00),
        (R, ch(0, CSR)),
        *all_reads(),
    ]
    s["en_register"] = [
        _w(ch(0, TOP), 3),
        _w(ch(7, TOP), 3),
        _w(EN, 0x81),
        (R, EN),
        (R, ch(0, CSR)),
        (R, ch(7, CSR)),
        (T, 100.0),
        _w(EN, 0x80),
        (T, 100.0),
        *all_reads(),
    ]
    s["unimplemented_registers"] = [
        (R, 0xB4),
        (R, 0x1004),
        (R, 0x1000),
        (R, 0xFFC),
        _w(0xB4, 0x55),
        _w(0xFFC, 0xFFFFFFFF),
        _w(0xB0, 5),  # INTS has no write handler
        (R, 0x20B4),
    ]
    s["b_gated"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, CC), (1 << 16) | 2),
        (DIR, 0),
        _w(ch(0, CSR), EN_BIT | (1 << 4)),
        (T, 80.0),  # gated off: B is low
        (SET, 1, 1),
        (IN, 1, 1),
        (T, 80.0),
        (IN, 1, 0),
        (T, 80.0),
        (IN, 1, 1),
        (T, 40.0),
        *all_reads(),
    ]
    s["b_edge_counting"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, CC), (1 << 16) | 2),
        _w(ch(0, DIV), 0x28),  # prescaler 2.5
        (DIR, 0),
        _w(ch(0, CSR), EN_BIT | (2 << 4)),  # rising edges
        *[op for level in (1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1) for op in ((IN, 1, level), (R, ch(0, CTR)))],
        (IN, 1, 0),  # the first B pin low again: the second pair decides now
        (R, ch(0, CTR)),
        (IN, 17, 1),  # the second B pin of the channel
        (R, ch(0, CTR)),
        (IN, 17, 0),
        (R, ch(0, CTR)),
        _w(ch(0, CSR), EN_BIT | (3 << 4)),  # falling edges: the input is already high from the second pin
        *[op for level in (0, 0, 1, 0, 1, 0, 1, 0) for op in ((IN, 1, level), (R, ch(0, CTR)))],
        *all_reads(),
    ]
    s["b_edge_counting_other_channel_is_not_counted"] = [
        _w(ch(0, TOP), 3),
        _w(ch(1, TOP), 3),
        _w(ch(0, DIV), 0x10),
        _w(ch(1, DIV), 0x10),
        (DIR, 0),
        _w(ch(0, CSR), EN_BIT | (2 << 4)),
        _w(ch(1, CSR), EN_BIT | (2 << 4)),
        (IN, 1, 1),  # B of channel 0
        (IN, 1, 0),
        (IN, 3, 1),  # B of channel 1
        (IN, 3, 1),
        (IN, 19, 1),
        (IN, 3, 0),
        (IN, 19, 0),
        (IN, 1, 1),
        (IN, 1, 0),
        (SET, 3, 1),  # a silent level change on channel 1's B pin ...
        (IN, 1, 1),  # ... is not seen by an edge call for channel 0
        (R, ch(1, CTR)),
        (IN, 3, 0),
        (R, ch(1, CTR)),
        *all_reads(),
    ]
    s["b_input_already_high_when_csr_is_written"] = [
        _w(ch(0, TOP), 3),
        (DIR, 0),
        (SET, 1, 1),
        _w(ch(0, CSR), EN_BIT | (2 << 4)),
        (IN, 1, 1),  # no change: the level was latched by the CSR write
        (R, ch(0, CTR)),
        (IN, 1, 0),
        (IN, 1, 1),
        (R, ch(0, CTR)),
        (SET, 17, 1),  # a level change that is not seen by an edge call is picked up by the next one
        (IN, 1, 0),
        (R, ch(0, CTR)),
    ]
    s["input_ignored_while_direction_is_set"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, DIV), 0x10),
        _w(ch(0, CSR), EN_BIT | (2 << 4)),
        (IN, 1, 1),
        (IN, 1, 0),
        (IN, 1, 1),
        *all_reads(),
    ]
    s["reset_while_running"] = [
        _w(ch(0, TOP), 3),
        _w(ch(0, CC), (1 << 16) | 2),
        _w(ch(0, CSR), EN_BIT),
        _w(INTE, 1),
        (T, 50.0),
        (RESET,),
        (T, 50.0),
        *all_reads(),
    ]
    s["phase_strobes_are_dead"] = [
        _w(ch(0, TOP), 7),
        _w(ch(0, CSR), EN_BIT | 0x80),
        (R, ch(0, CSR)),
        _w(ch(0, CSR), EN_BIT | 0x40),
        (R, ch(0, CSR)),
        (T, 40.0),
        (R, ch(0, CTR)),
    ]
    s["top_zero_and_cc_edges"] = [
        _w(ch(4, TOP), 0),
        _w(ch(4, CC), 0xFFFFFFFF),
        _w(ch(4, CSR), EN_BIT),
        (T, 40.0),
        _w(ch(4, TOP), 1),
        _w(ch(4, CC), 0x00010000),
        (T, 40.0),
        *all_reads(),
    ]
    return s


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return repr(value)
    return str(value)


def emit() -> str:
    lines = [
        "// GENERATED by tests/utils/pwm_cpp_vectors.py from the pure-Python reference (peripherals/_pwm.py) - do not edit; see that file.",
        "// op  = {code, a, b, c}: W write(offset=a, value=b, atomic=c), R read(a), T tick(ns=x), IN input(a=pin, b=level) + gpio_on_input, SET input level only, DIR poke gpio_direction(a), RESET.",
        "// ev  = {code, time, a, b, c}: IRQ(a=level), DREQ(a=channel), PIN(a=pin, b=gpio_value), WARN(a=kind, b=offset, c=value), READ(a=offset, b=value).",
    ]
    names = []
    for name, ops in scenarios().items():
        events = run(ops)
        names.append(name)
        lines.append(f"static const Op {name}_ops[] = {{")
        for op in ops:
            if op[0] == W:
                lines.append(f"    {{{op[0]}, 0.0, {op[1]}u, {op[2]}ull, {op[3]}}},")
            elif op[0] == R:
                lines.append(f"    {{{op[0]}, 0.0, {op[1]}u, 0, 0}},")
            elif op[0] == T:
                lines.append(f"    {{{op[0]}, {_fmt(op[1])}, 0, 0, 0}},")
            elif op[0] in (IN, SET):
                lines.append(f"    {{{op[0]}, 0.0, {op[1]}u, {op[2]}, 0}},")
            elif op[0] == DIR:
                lines.append(f"    {{{op[0]}, 0.0, {op[1]}u, 0, 0}},")
            else:
                lines.append(f"    {{{op[0]}, 0.0, 0, 0, 0}},")
        lines.append("};")
        lines.append(f"static const Ev {name}_ev[] = {{")
        for ev in events:
            padded = tuple(ev) + (0,) * (5 - len(ev))
            lines.append(
                f"    {{{padded[0]}, {_fmt(float(padded[1]))}, {padded[2]}u, {padded[3]}ull, {padded[4]}ull}},"
            )
        if not events:
            lines.append("    {-1, 0.0, 0, 0, 0},")
        lines.append("};")
    lines.append("static const Scenario kScenarios[] = {")
    for name in names:
        lines.append(
            f'    {{"{name}", {name}_ops, sizeof({name}_ops) / sizeof(Op), {name}_ev, {len(run(scenarios()[name]))}}},'
        )
    lines.append("};")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    OUT.write_text(emit())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)", file=sys.stderr)
