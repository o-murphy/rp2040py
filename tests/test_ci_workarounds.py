"""Tripwires for CI workarounds: each fails once the thing it works around is fixed upstream, so the workaround gets removed instead of lingering.

The i686 manylinux build runs with `seccomp=unconfined` (pyproject.toml, `[[tool.cibuildwheel.overrides]]`) because Docker 29.4.2, the one on
the ubuntu-26.04 runner image, denies `socketcall` in its default seccomp profile (actions/runner-images#14790, astral-sh/uv#23147). 32-bit
glibc makes every socket call through `socketcall`, so a 32-bit process dies with ENOSYS on its first `socketpair`. Docker 29.4.3 reverts the deny.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
WORKAROUND = "seccomp=unconfined"

# 32-bit userspace image whose glibc goes through socketcall; perl-base ships Socket, so no package needs installing
IMAGE = "i386/debian:bookworm-slim"
PROBE = ["perl", "-MSocket", "-e", "socketpair(A, B, AF_UNIX, SOCK_STREAM, 0) or die $!"]


def test_i686_seccomp_workaround_is_still_needed() -> None:
    # Only the runner image that had the bug says anything: on ubuntu-24.04 (Docker 28) the probe passes whether or not 26.04 was fixed.
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("ImageOS") != "ubuntu26":
        pytest.skip("only meaningful on a GitHub-hosted ubuntu-26.04 runner")
    if shutil.which("docker") is None:
        pytest.skip("no docker")
    if WORKAROUND not in PYPROJECT.read_text(encoding="utf-8"):
        pytest.skip("workaround already removed")

    done = subprocess.run(
        ["docker", "run", "--rm", IMAGE, *PROBE], check=False, capture_output=True, text=True, timeout=300
    )
    if done.returncode != 0 and "Function not implemented" not in done.stderr:
        pytest.skip(f"probe could not run (pull failed?): {done.stderr.strip()[-200:]}")

    assert done.returncode != 0, (
        "the default seccomp profile no longer blocks socketcall on the ubuntu-26.04 runner (Docker was updated): "
        f"remove the `{WORKAROUND}` override for *-manylinux_i686 from pyproject.toml and this test"
    )
