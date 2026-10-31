#!/usr/bin/env python3
"""Tests for the manifest-path allowlist in .github/workflows/dependabot-auto-merge.yml.

The allowlist is inlined twice, once in job `auto-merge` and once in job
`sweep`, and the file asks that the two stay in lockstep. Each copy is a bash
`case` statement; these tests pull both out and run them under bash against
sample paths, so a pattern that reads right but never matches still fails.

Why it matters: since this repo pinned its actions through actions.lock,
Dependabot rewrites `.github/workflows/actions.lock` with every action bump.
Without it on the allowlist, job `auto-merge` failed on #89 (setup-python
7.0.0 to 7.1.0) on 2026-10-29 and the sweep would have skipped it for good.

Run: python3 -m pytest scripts/test_dependabot_auto_merge.py -q
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"

# On Windows, `bash` on PATH can be WSL's launcher, which has no distro on the
# runner; Git Bash is what this repo targets there (as test_agent_checks.py).
if os.name == "nt":
    _git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    BASH = str(_git_bash) if _git_bash.exists() else None
else:
    BASH = shutil.which("bash")

needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash (Git Bash on Windows)")

# Each copy opens with this line and ends at the matching `esac`.
_CASE_RE = re.compile(r'^\s*case "\$f" in\n(.*?)^\s*esac\b', re.M | re.S)

ALLOWED = [
    ".github/workflows/actions.lock",
    ".github/workflows/ci.yml",
    "package.json",
    "plugins/x/package-lock.json",
    "requirements-dev.txt",
]
REJECTED = [
    "README.md",
    "scripts/plugin_runtime_gate.py",
    ".github/workflows/actions.lock.bak",
    ".github/dependabot.yml",
]


def _patterns():
    """Return each copy's allowed patterns: every arm except the `*)` catch-all."""
    copies = []
    for body in _CASE_RE.findall(WORKFLOW.read_text(encoding="utf-8")):
        arms = re.findall(r"^\s*([^\s#][^\n]*?)\)\s*;;", body, re.M)
        copies.append([arm for arm in arms if arm != "*"])
    return copies


def _allowed(patterns, path):
    """Run one copy's patterns under bash's own `case` and report the verdict."""
    script = 'case "$1" in\n%s\n  *) echo reject ;;\nesac' % "\n".join(
        "  %s) echo allow ;;" % arm for arm in patterns
    )
    out = subprocess.run(
        [BASH, "-c", script, "bash", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return out == "allow"


def test_both_copies_are_found():
    assert len(_patterns()) == 2


def test_copies_stay_in_lockstep():
    auto_merge, sweep = _patterns()
    assert auto_merge == sweep


@needs_bash
@pytest.mark.parametrize("path", ALLOWED)
def test_allowed_paths_pass_both_copies(path):
    for patterns in _patterns():
        assert _allowed(patterns, path), path


@needs_bash
@pytest.mark.parametrize("path", REJECTED)
def test_other_paths_are_rejected_by_both_copies(path):
    for patterns in _patterns():
        assert not _allowed(patterns, path), path
