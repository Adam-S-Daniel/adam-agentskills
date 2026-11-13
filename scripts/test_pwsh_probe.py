"""The suite-wide pwsh probe in the repo-root conftest.py.

Each case runs a throwaway pytest in a temp dir that carries a copy of the real
conftest.py and a fake `pwsh` first on PATH, so the rule is exercised end to
end (marker, hook, session cache) without a real PowerShell.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"

posix_only = pytest.mark.skipif(os.name == "nt", reason="the fake pwsh is a shell script")

INNER_TEST = """
import pytest

@pytest.mark.needs_pwsh
def test_marked():
    pass

def test_fixture(require_pwsh):
    require_pwsh(None)
"""


def run_inner(tmp_path, fake_pwsh_body, extra_env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if fake_pwsh_body is not None:
        fake = bin_dir / "pwsh"
        fake.write_text("#!/bin/sh\n" + fake_pwsh_body + "\n")
        fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    (work / "conftest.py").write_text(ROOT_CONFTEST.read_text())
    (work / "test_inner.py").write_text(INNER_TEST)
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
    # Only the temp dir: a pwsh elsewhere on the machine must be unreachable.
    env["PATH"] = str(bin_dir)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rsf", "-p", "no:cacheprovider", str(work)],
        capture_output=True, text=True, env=env, cwd=work, timeout=120,
    )


def counts(out):
    return {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|skipped|error)", out)}


@posix_only
def test_a_missing_pwsh_skips_with_that_cause(tmp_path):
    proc = run_inner(tmp_path, None)
    assert counts(proc.stdout) == {"skipped": 2}, proc.stdout + proc.stderr
    assert "pwsh (PowerShell 7+) is not on PATH" in proc.stdout


@posix_only
def test_a_pwsh_that_cannot_start_skips_locally_naming_the_cause(tmp_path):
    body = "echo 'cannot create transient scope: UnixProcessIdUnknown' >&2; exit 46"
    proc = run_inner(tmp_path, body)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert counts(proc.stdout) == {"skipped": 2}, proc.stdout
    assert "cannot start" in proc.stdout
    assert "exited 46" in proc.stdout
    assert "cannot create transient scope" in proc.stdout
    assert "PID namespace" in proc.stdout


@posix_only
@pytest.mark.parametrize("ci_env", [{"CI": "true"}, {"GITHUB_ACTIONS": "true"}])
def test_a_pwsh_that_cannot_start_fails_under_ci(tmp_path, ci_env):
    proc = run_inner(tmp_path, "exit 46", ci_env)
    assert proc.returncode != 0, proc.stdout
    # The marker fails in setup (an error), the fixture inside the test body.
    assert counts(proc.stdout) == {"failed": 1, "error": 1}, proc.stdout
    assert "cannot start" in proc.stdout and "exited 46" in proc.stdout


@posix_only
def test_a_pwsh_that_starts_runs_the_test_even_under_ci(tmp_path):
    proc = run_inner(tmp_path, "exit 0", {"CI": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert counts(proc.stdout) == {"passed": 2}, proc.stdout


@posix_only
def test_a_missing_pwsh_still_skips_under_ci(tmp_path):
    # The runners ship pwsh; an absent one keeps the old behavior (a skip), so
    # CI's test count does not change.
    proc = run_inner(tmp_path, None, {"CI": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert counts(proc.stdout) == {"skipped": 2}, proc.stdout
