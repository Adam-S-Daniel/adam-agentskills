#!/usr/bin/env python3
"""The fleet's agent-checks runner and its config-based hook installer (ADR 0020).

`.githooks/agent-checks` is run three ways: by a pre-commit hook, by a pre-push
hook, and by CI over a pull request's commits. These tests drive the runner
directly for each, then install the hooks into a throwaway HOME and commit
through real git to prove the wiring, on Git 2.54+ only (older git ignores
config-based hooks, which is itself checked).

Hermetic: every repo is a tmp directory, HOME is a tmp directory, and git's
global config is redirected with GIT_CONFIG_GLOBAL, so the real machine's
config is never read or written.

Run: python3 -m pytest scripts/test_agent_checks.py -q
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / ".githooks" / "agent-checks"
INSTALLER = REPO / "scripts" / "install-agent-hooks.sh"

# On Windows, `bash` on PATH can be WSL's launcher in System32, which cannot
# see these tmp paths. Git for Windows' own bash is the one its hooks use.
if os.name == "nt":
    _git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    BASH = str(_git_bash) if _git_bash.exists() else None
else:
    BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash (Git Bash on Windows)")


def git_version() -> tuple:
    out = subprocess.run(["git", "version"], capture_output=True, text=True).stdout
    match = re.search(r"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def env_for(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(HOME=str(home), USERPROFILE=str(home),
               GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
    return env


def git(repo: Path, env: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=str(repo), env=env,
                          capture_output=True, text=True, check=check)


def make_repo(tmp_path: Path, name: str = "repo",
              remote: str = "https://github.com/Adam-S-Daniel/example") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    env = env_for(tmp_path)
    git(repo, env, "init", "-q", "-b", "feature")
    git(repo, env, "remote", "add", "origin", remote)
    (repo / ".githooks").mkdir()
    shutil.copyfile(RUNNER, repo / ".githooks" / "agent-checks")
    (repo / "README.md").write_text("example\n")
    git(repo, env, "add", ".")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "base")
    return repo


def run_checks(repo: Path, tmp_path: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([BASH, ".githooks/agent-checks", *args], cwd=str(repo), env=env_for(tmp_path),
                          input=stdin, capture_output=True, text=True)


def stage(repo: Path, tmp_path: Path, relpath: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")
    git(repo, env_for(tmp_path), "add", "--", relpath)


def head(repo: Path, tmp_path: Path) -> str:
    return git(repo, env_for(tmp_path), "rev-parse", "HEAD").stdout.strip()


ZERO = "0" * 40


# --- pre-commit -------------------------------------------------------------

@pytest.mark.parametrize("relpath", [".env", "config/.env.production", "certs/server.pem",
                                     "deploy.key", "id_ed25519", ".claude/rules/pinning.md"])
def test_pre_commit_refuses_a_staged_secret_or_path_scoped_rule(tmp_path, relpath):
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, relpath)
    proc = run_checks(repo, tmp_path, "pre-commit")
    assert proc.returncode == 1
    assert relpath in proc.stderr
    assert "--no-verify" in proc.stderr  # the closing line says not to bypass


@pytest.mark.parametrize("relpath", [".env.example", "src/app.py", "docs/keys.md", "id_ed25519.pub"])
def test_pre_commit_allows_ordinary_files(tmp_path, relpath):
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, relpath)
    proc = run_checks(repo, tmp_path, "pre-commit")
    assert proc.returncode == 0, proc.stderr


def test_pre_commit_ignores_a_deleted_secret_file(tmp_path):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    stage(repo, tmp_path, ".env")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "oops")
    git(repo, env, "rm", "-q", "--cached", ".env")
    assert run_checks(repo, tmp_path, "pre-commit").returncode == 0


def test_an_unknown_event_never_blocks(tmp_path):
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, ".env")
    assert run_checks(repo, tmp_path, "post-checkout").returncode == 0


# --- pre-push ---------------------------------------------------------------

def push_line(local_oid: str, remote_ref: str, remote_oid: str = ZERO) -> str:
    return f"refs/heads/x {local_oid} {remote_ref} {remote_oid}\n"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_pre_push_refuses_the_default_branch(tmp_path, branch):
    repo = make_repo(tmp_path)
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), f"refs/heads/{branch}"))
    assert proc.returncode == 1
    assert "pull request" in proc.stderr


def test_pre_push_uses_the_remotes_recorded_default_branch(tmp_path):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    sha = head(repo, tmp_path)
    git(repo, env, "update-ref", "refs/remotes/origin/trunk", sha)
    git(repo, env, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(sha, "refs/heads/trunk")).returncode == 1
    assert run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(sha, "refs/heads/main")).returncode == 0


def test_pre_push_allows_a_feature_branch(tmp_path):
    repo = make_repo(tmp_path)
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), "refs/heads/feature"))
    assert proc.returncode == 0, proc.stderr


def test_pre_push_refuses_while_changes_are_staged_but_uncommitted(tmp_path):
    # The trap: a hook refused `git commit`, and the push that follows would
    # publish the commit before it and print an ordinary success.
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, "src/work.py")
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), "refs/heads/feature"))
    assert proc.returncode == 1
    assert "staged changes" in proc.stderr


def test_pre_push_finds_a_secret_added_and_deleted_inside_the_pushed_range(tmp_path):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    base = head(repo, tmp_path)
    stage(repo, tmp_path, ".env")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "add")
    git(repo, env, "rm", "-q", ".env")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "remove")
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), "refs/heads/feature", base))
    assert proc.returncode == 1
    assert ".env" in proc.stderr


def test_pre_push_of_a_branch_delete_checks_no_paths(tmp_path):
    repo = make_repo(tmp_path)
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(ZERO, "refs/heads/feature", head(repo, tmp_path)))
    assert proc.returncode == 0, proc.stderr


# --- ci ---------------------------------------------------------------------

def test_ci_checks_every_commit_in_the_range(tmp_path):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    base = head(repo, tmp_path)
    stage(repo, tmp_path, ".claude/rules/x.md")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "rule")
    stage(repo, tmp_path, "src/ok.py")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "ok")
    assert run_checks(repo, tmp_path, "ci", base, "HEAD").returncode == 1
    assert run_checks(repo, tmp_path, "ci", head(repo, tmp_path), "HEAD").returncode == 0


@pytest.mark.parametrize("base", ["", ZERO, "f" * 40])
def test_ci_without_a_usable_base_checks_the_head_commit(tmp_path, base):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    stage(repo, tmp_path, ".env")
    git(repo, env, "commit", "-q", "--no-verify", "-m", "secret")
    assert run_checks(repo, tmp_path, "ci", base, "HEAD").returncode == 1


# --- installer --------------------------------------------------------------

def install(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(INSTALLER), *args], env=env_for(tmp_path),
                          capture_output=True, text=True)


def include_paths(tmp_path: Path) -> list:
    proc = subprocess.run(["git", "config", "--file", str(tmp_path / "gitconfig"),
                           "--get-regexp", r"^includeif\..*\.path$"],
                          capture_output=True, text=True)
    return proc.stdout.splitlines()


def test_installer_is_idempotent_and_scoped_to_the_fleet_owners(tmp_path):
    assert install(tmp_path).returncode == 0
    first = include_paths(tmp_path)
    assert install(tmp_path).returncode == 0
    assert include_paths(tmp_path) == first
    joined = "\n".join(first)
    for owner in ("Adam-S-Daniel", "jodidaniel"):
        assert f"https://github.com/{owner}/**" in joined
        assert f"git@github.com:{owner}/**" in joined
    inc = tmp_path / "home" / ".config" / "git" / "agent-hooks.inc"
    events = subprocess.run(["git", "config", "--file", str(inc), "--get-regexp", r"^hook\..*\.event$"],
                            capture_output=True, text=True).stdout.split()
    assert "pre-commit" in events and "pre-push" in events
    # No absolute path anywhere: that is what broke every push on the machine
    # when the retired sync-skills hook's script moved (ADR 0001, ADR 0014).
    assert str(tmp_path) not in inc.read_text()


def test_installer_uninstall_removes_what_it_added(tmp_path):
    install(tmp_path)
    assert install(tmp_path, "--uninstall").returncode == 0
    assert include_paths(tmp_path) == []
    assert not (tmp_path / "home" / ".config" / "git" / "agent-hooks.inc").exists()


# --- end to end through real git ----------------------------------------------

needs_config_hooks = pytest.mark.skipif(git_version() < (2, 54),
                                        reason="config-based hooks need Git 2.54+")


def commit(repo: Path, tmp_path: Path, message: str) -> subprocess.CompletedProcess:
    return git(repo, env_for(tmp_path), "commit", "-q", "-m", message, check=False)


@needs_config_hooks
def test_installed_hook_refuses_a_secret_in_a_fleet_repo(tmp_path):
    install(tmp_path)
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, ".env")
    proc = commit(repo, tmp_path, "secret")
    assert proc.returncode != 0
    assert ".env" in proc.stderr


@needs_config_hooks
def test_installed_hook_runs_in_a_linked_worktree_and_from_a_subdirectory(tmp_path):
    install(tmp_path)
    repo = make_repo(tmp_path)
    git(repo, env_for(tmp_path), "worktree", "add", "-q", "-b", "other", str(tmp_path / "wt"))
    worktree = tmp_path / "wt"
    stage(worktree, tmp_path, "sub/.env")
    proc = git(worktree / "sub", env_for(tmp_path), "commit", "-q", "-m", "secret", check=False)
    assert proc.returncode != 0
    assert "sub/.env" in proc.stderr


@needs_config_hooks
def test_installed_hook_never_runs_a_third_party_repos_runner(tmp_path):
    install(tmp_path)
    repo = make_repo(tmp_path, remote="https://github.com/someone-else/project")
    stage(repo, tmp_path, ".env")
    assert commit(repo, tmp_path, "theirs").returncode == 0


@needs_config_hooks
def test_a_missing_include_file_or_runner_never_breaks_a_commit(tmp_path):
    install(tmp_path)
    repo = make_repo(tmp_path)
    (tmp_path / "home" / ".config" / "git" / "agent-hooks.inc").unlink()
    stage(repo, tmp_path, ".env")
    assert commit(repo, tmp_path, "no include").returncode == 0

    install(tmp_path)
    other = make_repo(tmp_path, name="no-runner")
    (other / ".githooks" / "agent-checks").unlink()
    stage(other, tmp_path, ".env")
    assert commit(other, tmp_path, "no runner").returncode == 0
