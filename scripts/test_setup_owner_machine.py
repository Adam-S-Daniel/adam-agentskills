#!/usr/bin/env python3
"""The whole of setup.sh, run with and without `--owner-machine`.

README tells anyone to run `bash setup.sh`. On a stranger's machine that must
only link skills into the per-agent homes: it must not register a GLOBAL git
pre-push hook, and must not touch ~/.claude/settings.json (which would
register the owner's private marketplace, enable the owner's plugins and turn
off the stranger's claude.ai account skills). ADR 0013.

Hermetic: HOME is a tmp directory and git's global config is redirected to a
tmp file (GIT_CONFIG_GLOBAL), so the real machine is never touched. POSIX
only — on Windows setup.sh makes junctions through powershell.exe, which is
not something to drive from a test.

Run: python3 -m pytest scripts/test_setup_owner_machine.py -q
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="setup.sh uses powershell.exe junctions on Windows")


def run_setup(tmp_path: Path, *args: str, env_extra=None) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("AGENTSKILLS_OWNER_MACHINE", "AGENTSKILLS_REPOS")}
    env.update(HOME=str(home), USERPROFILE=str(home),
               GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
    env.update(env_extra or {})
    return subprocess.run([shutil.which("bash") or "bash", str(SETUP), *args],
                          env=env, capture_output=True, text=True, cwd=str(tmp_path))


def global_hook(tmp_path: Path) -> str:
    config = tmp_path / "gitconfig"
    if not config.exists():
        return ""
    proc = subprocess.run(["git", "config", "--file", str(config), "--get",
                           "hook.sync-skills-reminder.event"],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def test_a_plain_run_only_links_skills(tmp_path):
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    home = tmp_path / "home"
    assert (home / ".agents" / "skills" / "finding-unknowns" / "SKILL.md").is_file()
    assert not (home / ".claude" / "settings.json").exists()
    assert global_hook(tmp_path) == ""
    assert "bash setup.sh --owner-machine" in proc.stdout


def test_a_plain_run_leaves_an_existing_settings_file_byte_identical(tmp_path):
    settings = tmp_path / "home" / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model": "claude-opus-5"}\n', encoding="utf-8")
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert settings.read_text(encoding="utf-8") == '{"model": "claude-opus-5"}\n'


@pytest.mark.parametrize("how", ["flag", "env"])
def test_the_owner_machine_opt_in_runs_both_steps(tmp_path, how):
    if how == "flag":
        proc = run_setup(tmp_path, "--owner-machine")
    else:
        proc = run_setup(tmp_path, env_extra={"AGENTSKILLS_OWNER_MACHINE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = json.loads((tmp_path / "home" / ".claude" / "settings.json")
                          .read_text(encoding="utf-8"))
    assert settings["syncClaudeAiSkills"] is False
    assert settings["enabledPlugins"]["adam-coding-local@adam-agentskills"] is True
    assert global_hook(tmp_path) == "pre-push"


def test_an_unknown_argument_is_refused_before_anything_runs(tmp_path):
    proc = run_setup(tmp_path, "--ownermachine")
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr
    assert not (tmp_path / "home" / ".agents").exists()
