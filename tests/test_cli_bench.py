"""`rp2040py bench --image`: the default mode runs the real batch engine and reports the real-time factor; `--stepwise` is the per-instruction-call mode, which counts real instructions apart from
trips round its loop with the core asleep (docs/records/0096-cpp-mcu-core.md, the bench entry). Needs a cached firmware image: skipped where there is none."""

from pathlib import Path

import pytest

from rp2040py.cli import _bench_firmware
from rp2040py.utils.logging import LogLevel

_IMAGES = [
    Path.home() / ".cache" / "rp2040py" / name
    for name in ("RPI_PICO-20240602-v1.23.0.uf2", "RPI_PICO-20231005-v1.21.0.uf2")
]
_IMAGE = next((p for p in _IMAGES if p.exists()), None)

pytestmark = pytest.mark.skipif(_IMAGE is None, reason="no cached MicroPython image (rp2040py bench --fetch-fw-only)")


def _bench(capsys, **kwargs):
    _bench_firmware(_IMAGE, None, None, False, kwargs.pop("timeout", 3.0), None, "pico", LogLevel.ERROR, **kwargs)
    return capsys.readouterr().out


def test_the_engine_mode_reports_the_real_time_factor(capsys):
    out = _bench(capsys)
    assert "of wall time" in out and "real time" in out and "batch engine" in out
    assert "instructions/sec" not in out


def test_an_idle_firmware_ends_the_run_instead_of_spinning_to_the_timeout(capsys):
    import time

    if _IMAGE is not None and "v1.23" not in _IMAGE.name:
        pytest.skip("only MicroPython 1.23 sits in WFE with no timer armed at the REPL")
    t = time.perf_counter()
    out = _bench(capsys, timeout=30.0)
    assert "firmware idle" in out
    assert time.perf_counter() - t < 15


def test_stepwise_counts_real_instructions_apart_from_idle_iterations(capsys):
    out = _bench(capsys, stepwise=True, timeout=1.0)
    assert "instructions/sec" in out and "not the batch engine" in out
