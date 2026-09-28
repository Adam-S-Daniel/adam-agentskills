#!/usr/bin/env python3
"""The whole of setup.sh, run with and without `--owner-machine`.

README tells anyone to run `bash setup.sh`. On a stranger's machine that must
only link skills into the per-agent homes: it must not register a GLOBAL git
pre-push hook change, and must not touch ~/.claude/settings.json (which would
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


RETIRED_HOOKS = ("sync-skills-reminder", "sync-skills-private-reminder")


def global_hook(tmp_path: Path, name: str = "sync-skills-reminder") -> str:
    config = tmp_path / "gitconfig"
    if not config.exists():
        return ""
    proc = subprocess.run(["git", "config", "--file", str(config), "--get",
                           f"hook.{name}.event"],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def plant_retired_hooks(tmp_path: Path) -> None:
    """The state an older `--owner-machine` run left in the global git config."""
    config = tmp_path / "gitconfig"
    for name in RETIRED_HOOKS:
        for key, value in (("event", "pre-push"),
                           ("command", "/nonexistent/<user>/pre-push")):
            subprocess.run(["git", "config", "--file", str(config),
                            f"hook.{name}.{key}", value], check=True)
    subprocess.run(["git", "config", "--file", str(config),
                    "hook.keep-me.event", "pre-commit"], check=True)


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
    # ADR 0014: the sync-skills pre-push hook is no longer registered.
    assert global_hook(tmp_path) == ""


def test_the_owner_machine_run_unregisters_the_retired_pre_push_hooks(tmp_path):
    plant_retired_hooks(tmp_path)
    assert global_hook(tmp_path) == "pre-push"
    proc = run_setup(tmp_path, "--owner-machine")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in RETIRED_HOOKS:
        assert global_hook(tmp_path, name) == "", name
    # Only the two retired sections go; the owner's other hooks stay.
    assert global_hook(tmp_path, "keep-me") == "pre-commit"


def test_the_owner_machine_run_tolerates_the_hooks_already_being_absent(tmp_path):
    for _ in range(2):
        proc = run_setup(tmp_path, "--owner-machine")
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_plain_run_leaves_the_retired_hooks_alone(tmp_path):
    # Not the owner's machine: setup.sh does not touch global git config.
    plant_retired_hooks(tmp_path)
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert global_hook(tmp_path) == "pre-push"


def test_an_unknown_argument_is_refused_before_anything_runs(tmp_path):
    proc = run_setup(tmp_path, "--ownermachine")
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr
    assert not (tmp_path / "home" / ".agents").exists()


def test_links_to_skills_that_no_longer_exist_are_swept_from_every_home(tmp_path):
    # A skill renamed or removed leaves its old link behind in each agent home;
    # link_one only repairs links at CURRENT skill names. setup.sh must reap
    # the orphans it made, and only those.
    home = tmp_path / "home"
    plugins = REPO / "plugins"
    user_target = tmp_path / "users-own-skill"
    user_target.mkdir()
    for rel in (".agents/skills", ".agent/skills", ".cursor/skills"):
        d = home / rel
        d.mkdir(parents=True)
        # Ours, dangling: the old name of a renamed skill.
        (d / "launch-wsl-claude-session").symlink_to(
            plugins / "adam-coding-local" / "skills" / "launch-wsl-claude-session")
        # The user's own: a live link elsewhere, a dangling link elsewhere,
        # and a real directory. None may be touched.
        (d / "mine-live").symlink_to(user_target)
        (d / "mine-dangling").symlink_to(tmp_path / "gone")
        (d / "mine-real").mkdir()
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in (".agents/skills", ".agent/skills", ".cursor/skills"):
        d = home / rel
        assert not os.path.lexists(d / "launch-wsl-claude-session"), rel
        assert (d / "mine-live").is_symlink(), rel
        assert (d / "mine-dangling").is_symlink(), rel
        assert (d / "mine-real").is_dir(), rel
        # The current name is linked, live.
        assert (d / "launch-top-level-claude-session" / "SKILL.md").is_file(), rel
    assert proc.stdout.count("UNLINK   launch-wsl-claude-session") == 3
