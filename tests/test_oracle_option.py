"""`--oracle` (tests/conftest.py): the lockstep oracles and parity tests in tiers, so that a wheel's test run need not repeat what was decided and checked before it was built."""

import re
import subprocess
import sys
from pathlib import Path

from utils.emscripten import needs_processes

pytestmark = needs_processes  # pytest is run in a subprocess

TESTS = Path(__file__).resolve().parent


def _collect(mode: str, *files: str) -> "list[str]":
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            f"--oracle={mode}",
            *[str(TESTS / f) for f in files],
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode in (0, 5), (
        done.stdout + done.stderr
    )  # 5: nothing collected (the tier `off` of a file that is all oracle)
    return [line for line in done.stdout.splitlines() if "::" in line]


def test_full_runs_every_oracle_test_smoke_one_parameter_set_of_each_and_off_none():
    full = _collect("full", "test_pwm_diff.py")
    smoke = _collect("smoke", "test_pwm_diff.py")
    off = _collect("off", "test_pwm_diff.py")
    functions = {line.split("[", 1)[0] for line in full}
    assert len(full) > len(functions) > 1  # parametrised: seeds, perturbations, mutants
    assert {line.split("[", 1)[0] for line in smoke} == functions
    assert len(smoke) == len(functions)  # exactly one parameter set of each
    assert off == []


def test_the_other_tests_are_not_touched_by_any_tier():
    assert len(_collect("off", "test_time.py")) == len(_collect("full", "test_time.py")) > 0
    assert all(re.search(r"test_time\.py", line) for line in _collect("smoke", "test_time.py"))
