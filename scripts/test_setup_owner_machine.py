#!/usr/bin/env python3
"""The whole of setup.sh, run with and without `--owner-machine`.

README tells anyone to run `bash setup.sh`. On a stranger's machine that must
only remove the skill links earlier versions made (ADR 0017: it links none any
more): it must not register a GLOBAL git pre-push hook, must not touch
~/.claude/settings.json (which would register the owner's private
marketplace, enable the owner's plugins and turn off the stranger's claude.ai
account skills; ADR 0013), and must not change git config or register a
scheduled task.

Hermetic: HOME is a tmp directory, git's global config is redirected to a
tmp file (GIT_CONFIG_GLOBAL), PATH drops every /mnt/ entry (so a WSL
machine's Windows pwsh.exe is unreachable) and the machine kind is pinned
with AGENTSKILLS_HOST_KIND, so the real machine is never touched and no task
is ever registered: the one test of the Git Bash step puts a stub pwsh.exe
first on PATH. POSIX only — on Windows setup.sh removes junctions through
powershell.exe, which is not something to drive from a test.

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


def run_setup(tmp_path: Path, *args: str, env_extra=None, path_first=None) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("AGENTSKILLS_OWNER_MACHINE", "AGENTSKILLS_REPOS", "WSL_DISTRO_NAME",
                        "AGENTSKILLS_HOST_KIND")}
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and "/mnt/" not in p]
    if path_first is not None:
        path.insert(0, str(path_first))
    env.update(HOME=str(home), USERPROFILE=str(home), PATH=os.pathsep.join(path),
               GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
               AGENTSKILLS_HOST_KIND="other")
    env.update(env_extra or {})
    return subprocess.run([shutil.which("bash") or "bash", str(SETUP), *args],
                          env=env, capture_output=True, text=True, cwd=str(tmp_path))


def global_hook(tmp_path: Path, section: str = "hook.sync-skills-reminder") -> str:
    config = tmp_path / "gitconfig"
    if not config.exists():
        return ""
    proc = subprocess.run(["git", "config", "--file", str(config), "--get",
                           f"{section}.event"],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def seed_global_hook_sections(tmp_path: Path) -> None:
    """Seed both retired hook sections in the throwaway global git config, the
    way an earlier sync-skills setup.sh run would have left them — so the
    owner-machine cleanup step has something real to remove."""
    config = tmp_path / "gitconfig"
    for section in ("hook.sync-skills-reminder", "hook.sync-skills-private-reminder"):
        subprocess.run(
            ["git", "config", "--file", str(config), f"{section}.event", "pre-push"],
            check=True)
        subprocess.run(
            ["git", "config", "--file", str(config), f"{section}.command",
             "bash \"/old/path/to/sync-skills/hooks/pre-push\""],
            check=True)


def test_a_plain_run_links_nothing_and_writes_nothing(tmp_path):
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    home = tmp_path / "home"
    for rel in (".agents", ".agent", ".cursor", ".gemini", ".claude"):
        assert not (home / rel).exists(), rel
    assert not (tmp_path / "gitconfig").exists()
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
    # A fresh machine never had the retired hook registered, so there is
    # nothing to clean up and nothing gets written.
    assert global_hook(tmp_path) == ""
    assert global_hook(tmp_path, section="hook.sync-skills-private-reminder") == ""


def test_the_owner_machine_opt_in_cleans_up_both_stale_hook_sections(tmp_path):
    seed_global_hook_sections(tmp_path)
    proc = run_setup(tmp_path, "--owner-machine")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert global_hook(tmp_path) == ""
    assert global_hook(tmp_path, section="hook.sync-skills-private-reminder") == ""
    assert "REMOVED  global hook section: hook.sync-skills-reminder" in proc.stdout
    assert "REMOVED  global hook section: hook.sync-skills-private-reminder" in proc.stdout


def test_a_second_owner_machine_run_does_not_repeat_the_hook_cleanup(tmp_path):
    seed_global_hook_sections(tmp_path)
    first = run_setup(tmp_path, "--owner-machine")
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_setup(tmp_path, "--owner-machine")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "REMOVED" not in second.stdout
    assert global_hook(tmp_path) == ""
    assert global_hook(tmp_path, section="hook.sync-skills-private-reminder") == ""


def test_a_plain_run_does_not_touch_a_stale_hook_section(tmp_path):
    # Without --owner-machine, setup.sh must not clean up (or touch at all)
    # the global git config — that is an owner-machine-only step.
    seed_global_hook_sections(tmp_path)
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert global_hook(tmp_path) == "pre-push"
    assert global_hook(tmp_path, section="hook.sync-skills-private-reminder") == "pre-push"


def test_an_unknown_argument_is_refused_before_anything_runs(tmp_path):
    proc = run_setup(tmp_path, "--ownermachine")
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr
    assert not (tmp_path / "home" / ".agents").exists()


RETIRED_HOMES = (".agents/skills", ".agent/skills", ".cursor/skills",
                 ".gemini/skills", ".gemini/antigravity/skills")


def test_every_link_setup_made_is_swept_from_every_retired_home(tmp_path):
    # ADR 0017: setup.sh links no skills any more, so every link it (or the
    # retired registry's setup.sh, from a sibling `agentskills` checkout) made
    # goes - live or dangling - and nothing else does.
    home = tmp_path / "home"
    plugins = REPO / "plugins"
    retired_registry = REPO.parent / "agentskills"
    user_target = tmp_path / "users-own-skill"
    user_target.mkdir()
    for rel in RETIRED_HOMES:
        d = home / rel
        d.mkdir(parents=True)
        # Ours: a live link, a dangling one (a renamed skill), and one into
        # the retired registry's checkout. Symlinks only; nothing is created
        # outside tmp_path.
        (d / "finding-unknowns").symlink_to(
            plugins / "adam-anything-anywhere" / "skills" / "finding-unknowns")
        (d / "launch-wsl-claude-session").symlink_to(
            plugins / "adam-coding-local" / "skills" / "launch-wsl-claude-session")
        (d / "old-registry-skill").symlink_to(retired_registry / "skills" / "old")
        # The user's own: a live link elsewhere, a dangling link elsewhere, a
        # link into a directory that merely shares the name's prefix, and a
        # real directory. None may be touched.
        (d / "mine-live").symlink_to(user_target)
        (d / "mine-dangling").symlink_to(tmp_path / "gone")
        (d / "mine-prefix").symlink_to(REPO.parent / "agentskills-other" / "x")
        (d / "mine-real").mkdir()
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in RETIRED_HOMES:
        d = home / rel
        for ours in ("finding-unknowns", "launch-wsl-claude-session", "old-registry-skill"):
            assert not os.path.lexists(d / ours), (rel, ours)
        assert (d / "mine-live").is_symlink(), rel
        assert (d / "mine-dangling").is_symlink(), rel
        assert (d / "mine-prefix").is_symlink(), rel
        assert (d / "mine-real").is_dir(), rel
    assert proc.stdout.count("UNLINK   finding-unknowns") == len(RETIRED_HOMES)


def test_a_retired_home_left_empty_is_removed(tmp_path):
    d = tmp_path / "home" / ".agents" / "skills"
    d.mkdir(parents=True)
    (d / "finding-unknowns").symlink_to(
        REPO / "plugins" / "adam-anything-anywhere" / "skills" / "finding-unknowns")
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not d.exists()
    assert (tmp_path / "home" / ".agents").is_dir()  # only the skills dir is ours to remove
    assert "RMDIR" in proc.stdout


def test_a_whole_directory_home_link_is_removed_only_when_it_is_ours(tmp_path):
    home = tmp_path / "home"
    (home / ".agents").mkdir(parents=True)
    (home / ".agents" / "skills").symlink_to(REPO / "plugins" / "adam-coding-local" / "skills")
    # A home that links to the USER's directory: neither the link nor any link
    # inside its target may be touched, even one that points into plugins/.
    users_dir = tmp_path / "users-skill-dir"
    users_dir.mkdir()
    (users_dir / "finding-unknowns").symlink_to(
        REPO / "plugins" / "adam-anything-anywhere" / "skills" / "finding-unknowns")
    (home / ".cursor").mkdir()
    (home / ".cursor" / "skills").symlink_to(users_dir)
    proc = run_setup(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not os.path.lexists(home / ".agents" / "skills")
    assert (REPO / "plugins" / "adam-coding-local" / "skills").is_dir()
    assert (home / ".cursor" / "skills").is_symlink()
    assert (users_dir / "finding-unknowns").is_symlink()


# =================================================================================
# --owner-machine in WSL: WSL git reads the Windows clones with autocrlf
# =================================================================================


def git_global(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "config", "--file", str(tmp_path / "gitconfig"), *args],
                          capture_output=True, text=True)


def test_wsl_owner_machine_sets_up_the_windows_clones_config(tmp_path):
    proc = run_setup(tmp_path, "--owner-machine", env_extra={"AGENTSKILLS_HOST_KIND": "wsl"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    windows = tmp_path / "home" / ".gitconfig-windows-clones"
    assert windows.read_text(encoding="utf-8") == "[core]\n\tautocrlf = true\n"
    values = git_global(tmp_path, "--get-all", "includeIf.gitdir:/mnt/.path").stdout.splitlines()
    assert values == ["~/.gitconfig-windows-clones"]
    again = run_setup(tmp_path, "--owner-machine", env_extra={"AGENTSKILLS_HOST_KIND": "wsl"})
    assert again.returncode == 0, again.stdout + again.stderr
    assert "SET      core.autocrlf" not in again.stdout and "ADDED" not in again.stdout
    assert windows.read_text(encoding="utf-8") == "[core]\n\tautocrlf = true\n"
    assert git_global(tmp_path, "--get-all", "includeIf.gitdir:/mnt/.path").stdout.splitlines() \
        == ["~/.gitconfig-windows-clones"]


def test_wsl_is_recognized_from_wsl_distro_name(tmp_path):
    proc = run_setup(tmp_path, "--owner-machine",
                     env_extra={"AGENTSKILLS_HOST_KIND": "", "WSL_DISTRO_NAME": "Example"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Host:      wsl" in proc.stdout
    assert (tmp_path / "home" / ".gitconfig-windows-clones").is_file()


def test_wsl_owner_machine_keeps_an_existing_windows_clones_config(tmp_path):
    windows = tmp_path / "home" / ".gitconfig-windows-clones"
    windows.parent.mkdir(parents=True)
    windows.write_text("[user]\n\tname = Example Person\n[core]\n\tautocrlf = true\n",
                       encoding="utf-8")
    git_global(tmp_path, "includeIf.gitdir:/mnt/.path", str(windows))
    before = (tmp_path / "gitconfig").read_text(encoding="utf-8")
    proc = run_setup(tmp_path, "--owner-machine", env_extra={"AGENTSKILLS_HOST_KIND": "wsl"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert windows.read_text(encoding="utf-8").startswith("[user]\n\tname = Example Person\n")
    assert (tmp_path / "gitconfig").read_text(encoding="utf-8") == before


def test_wsl_owner_machine_never_overwrites_a_different_include(tmp_path):
    git_global(tmp_path, "includeIf.gitdir:/mnt/.path", "~/.somewhere-else")
    proc = run_setup(tmp_path, "--owner-machine", env_extra={"AGENTSKILLS_HOST_KIND": "wsl"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert git_global(tmp_path, "--get-all", "includeIf.gitdir:/mnt/.path").stdout.splitlines() \
        == ["~/.somewhere-else"]
    assert "already set to something else" in proc.stderr


def test_wsl_without_owner_machine_changes_no_git_config(tmp_path):
    proc = run_setup(tmp_path, env_extra={"AGENTSKILLS_HOST_KIND": "wsl"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (tmp_path / "home" / ".gitconfig-windows-clones").exists()
    assert not (tmp_path / "gitconfig").exists()


def test_an_unknown_host_kind_is_refused(tmp_path):
    proc = run_setup(tmp_path, env_extra={"AGENTSKILLS_HOST_KIND": "beos"})
    assert proc.returncode == 2
    assert "AGENTSKILLS_HOST_KIND" in proc.stderr


# =================================================================================
# --owner-machine in Git Bash: the adam-clone-sync task, through a STUB pwsh
# =================================================================================


def stub_pwsh(tmp_path: Path, code: int = 0) -> Path:
    """A pwsh.exe that records its arguments and registers nothing."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "pwsh.exe"
    stub.write_text("#!/bin/sh\nprintf '%%s\\n' \"$@\" > \"$0.args\"\nexit %d\n" % code,
                    encoding="utf-8")
    stub.chmod(0o755)
    return bin_dir


def test_gitbash_owner_machine_runs_the_register_script_through_pwsh(tmp_path):
    bin_dir = stub_pwsh(tmp_path)
    proc = run_setup(tmp_path, "--owner-machine", path_first=bin_dir,
                     env_extra={"AGENTSKILLS_HOST_KIND": "gitbash"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    args = (bin_dir / "pwsh.exe.args").read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["-NoProfile", "-NonInteractive", "-File"], args
    assert Path(args[3]) == (REPO / "plugins" / "adam-coding-local" / "hooks" / "clone-sync"
                             / "Register-CloneSyncTask.ps1"), args
    assert (REPO / "plugins" / "adam-coding-local" / "hooks" / "clone-sync"
            / "Register-CloneSyncTask.ps1").is_file()


def test_gitbash_owner_machine_fails_when_registration_fails(tmp_path):
    bin_dir = stub_pwsh(tmp_path, code=3)
    proc = run_setup(tmp_path, "--owner-machine", path_first=bin_dir,
                     env_extra={"AGENTSKILLS_HOST_KIND": "gitbash"})
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "registering the adam-clone-sync task failed" in proc.stderr


def test_gitbash_owner_machine_warns_without_pwsh(tmp_path):
    proc = run_setup(tmp_path, "--owner-machine", env_extra={"AGENTSKILLS_HOST_KIND": "gitbash"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pwsh.exe) not found" in proc.stderr
    assert "Setup complete." in proc.stdout


@pytest.mark.parametrize("kind, owner", [("gitbash", False), ("wsl", True), ("other", True)])
def test_the_task_is_registered_only_by_an_owner_run_in_git_bash(tmp_path, kind, owner):
    bin_dir = stub_pwsh(tmp_path)
    proc = run_setup(tmp_path, *(["--owner-machine"] if owner else []), path_first=bin_dir,
                     env_extra={"AGENTSKILLS_HOST_KIND": kind})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (bin_dir / "pwsh.exe.args").exists()
