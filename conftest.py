"""Shared pytest setup for the whole suite (scripts/ and every skill's tests).

PowerShell probe. A test that shells out to `pwsh` needs one that can START,
not just one on PATH. A snap-packaged `pwsh` on a workstation cannot start
inside the PID namespace the fleet guidance asks spawning suites to run in
(`unshare --user --map-current-user --pid --fork --mount-proc`): it exits 46
with `cannot create transient scope`. Dozens of tests then fail for a reason
that is the machine's, not the code's.

The rule, in one place:
- no `pwsh` at all: skip, naming that;
- a `pwsh` that cannot start (non-zero exit, timeout or OSError from
  `pwsh -NoProfile -NonInteractive -Command exit 0`): skip locally with the
  cause, but FAIL when CI is set (`CI=true` or `GITHUB_ACTIONS=true`), because
  the CI runners ship a working `pwsh` and a broken one there is a real
  problem a skip would hide.

Use it as `@pytest.mark.needs_pwsh` (PATH `pwsh`) or
`@pytest.mark.needs_pwsh(PWSH)` (a named executable) on a test, or call the
`require_pwsh` fixture's function from another fixture.
"""
import functools
import os
import shutil
import subprocess

import pytest

PROBE_TIMEOUT_SECONDS = 30


def _in_ci(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("CI") == "true" or environ.get("GITHUB_ACTIONS") == "true"


@functools.lru_cache(maxsize=None)
def pwsh_unstartable_reason(path):
    """None when `path` starts and exits 0; else a short cause."""
    try:
        proc = subprocess.run(
            [path, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"it did not exit within {PROBE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return f"it could not be executed ({type(exc).__name__})"
    if proc.returncode == 0:
        return None
    detail = ((proc.stderr or "").strip() or (proc.stdout or "").strip()).splitlines()
    detail = detail[0][:200] if detail else "no output"
    hint = ""
    if "/snap/" in path or "transient scope" in detail:
        hint = " (a snap-packaged pwsh cannot start inside a PID namespace)"
    return f"it exited {proc.returncode}: {detail}{hint}"


def require_pwsh_or_skip(path=None):
    """Skip or fail the running test unless `path` (default: PATH pwsh) starts."""
    path = path or shutil.which("pwsh")
    if not path:
        pytest.skip("pwsh (PowerShell 7+) is not on PATH")
    reason = pwsh_unstartable_reason(path)
    if reason is None:
        return
    message = f"pwsh at {path} cannot start: {reason}"
    if _in_ci():
        pytest.fail(message + "; CI runners ship a working pwsh, so this is not skipped")
    pytest.skip(message)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_pwsh(path=None): needs a pwsh that starts; skipped locally when it "
        "is absent or cannot start, failed when it cannot start under CI",
    )


def pytest_runtest_setup(item):
    marker = item.get_closest_marker("needs_pwsh")
    if marker is not None:
        # An explicit None means "the caller found no pwsh": skip, never fall
        # back to a different one.
        if marker.args:
            if not marker.args[0]:
                pytest.skip("pwsh (PowerShell 7+) is not on PATH")
            require_pwsh_or_skip(marker.args[0])
        else:
            require_pwsh_or_skip()


@pytest.fixture(scope="session")
def require_pwsh():
    """The skip-or-fail function, for fixtures that run pwsh."""
    return require_pwsh_or_skip
