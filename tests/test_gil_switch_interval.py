"""The engine-room thread lowers the GIL switch interval when it is created (see simulator._tune_gil_switch_interval): a host thread doing Python work next to a thread that never blocks
otherwise pays the default 5 ms per GIL release (docs/records/0096-cpp-mcu-core.md, the e-paper demo)."""

import sys

import pytest
from utils.emscripten import needs_threads

from rp2040py import simulator as sim


@pytest.fixture
def restore_switch_interval():
    saved = sys.getswitchinterval()
    yield
    sys.setswitchinterval(saved)


def test_the_default_interval_is_lowered(restore_switch_interval, monkeypatch):
    monkeypatch.delenv("RP2040PY_SWITCH_INTERVAL", raising=False)
    sys.setswitchinterval(0.005)
    sim._tune_gil_switch_interval()
    assert sys.getswitchinterval() == pytest.approx(sim._ENGINE_SWITCH_INTERVAL_SECONDS)


def test_an_embedders_own_interval_is_left_alone(restore_switch_interval, monkeypatch):
    monkeypatch.delenv("RP2040PY_SWITCH_INTERVAL", raising=False)
    sys.setswitchinterval(0.02)
    sim._tune_gil_switch_interval()
    assert sys.getswitchinterval() == pytest.approx(0.02)


def test_the_environment_variable_wins(restore_switch_interval, monkeypatch):
    monkeypatch.setenv("RP2040PY_SWITCH_INTERVAL", "0.0002")
    sys.setswitchinterval(0.005)
    sim._tune_gil_switch_interval()
    assert sys.getswitchinterval() == pytest.approx(0.0002)
    monkeypatch.setenv("RP2040PY_SWITCH_INTERVAL", "0")  # 0: leave it alone
    sys.setswitchinterval(0.005)
    sim._tune_gil_switch_interval()
    assert sys.getswitchinterval() == pytest.approx(0.005)
    monkeypatch.setenv("RP2040PY_SWITCH_INTERVAL", "junk")
    sim._tune_gil_switch_interval()
    assert sys.getswitchinterval() == pytest.approx(0.005)


@needs_threads
def test_creating_the_engine_room_loop_applies_it(restore_switch_interval, monkeypatch):
    from rp2040py.simulator import Simulator

    monkeypatch.delenv("RP2040PY_SWITCH_INTERVAL", raising=False)
    sys.setswitchinterval(0.005)
    simulator = Simulator(threadless=False)  # the thread is what the interval is for (RP2040PY_THREADLESS=1 has none)
    simulator._ensure_loop()
    try:
        assert sys.getswitchinterval() == pytest.approx(sim._ENGINE_SWITCH_INTERVAL_SECONDS)
    finally:
        simulator.stop()
