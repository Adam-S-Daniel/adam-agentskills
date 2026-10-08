#!/usr/bin/env python3
"""Tests for adam-coding-local's harness-aware clone-sync hooks (ADR 0017).

Covers plugins/adam-coding-local/hooks/: harness detection (lib/harness.sh),
clone-sync.sh against real temporary git repositories, the PostToolUse and
SessionStart hooks, the scheduled task's launcher, the shape of hooks.json,
and Register-CloneSyncTask.ps1's dry run.

Hermetic and deterministic:
  - every git remote is a local bare repository. Clones carry GitHub-shaped
    origin URLs, and a tmp global git config (GIT_CONFIG_GLOBAL) maps each
    one to the bare repository with url.<file-url>.insteadOf, so nothing
    touches the network;
  - HOME, XDG_CACHE_HOME and the clone roots (CLONE_SYNC_ROOTS) are tmp dirs,
    and PATH drops every /mnt/ entry, so a WSL machine's git.exe, wsl.exe and
    pwsh.exe are unreachable;
  - the hooks never start background work here: CLONE_SYNC_HOOK_DRY_RUN makes
    them record the launch instead. No sleeps; the one stale-lock case sets
    an mtime an hour back rather than waiting.

The shell-driven tests are POSIX-only; the hook-input parser, hooks.json
shape and PowerShell dry run also run on Windows.

Run: python3 -m pytest scripts/test_clone_sync_hooks.py -q
"""

import importlib.util
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_consistency  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "plugins" / "adam-coding-local" / "hooks"
CLONE_SYNC = HOOKS / "clone-sync"
BASH = shutil.which("bash")

posix_only = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="drives the hook scripts with a POSIX bash and real git repositories")

URL = "https://github.com/example-org/widget.git"
SLUG = "example-org/widget"


def _load_hook_input():
    spec = importlib.util.spec_from_file_location("hook_input", CLONE_SYNC / "hook_input.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook_input = _load_hook_input()


def clean_path():
    return os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and "/mnt/" not in p)


# =================================================================================
# Harness detection
# =================================================================================


def detect(payload, env=None, path=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    base = {"PATH": clean_path() if path is None else path, "HOME": "/nonexistent"}
    base.update(env or {})
    proc = subprocess.run([BASH, str(HOOKS / "lib" / "harness.sh")], input=text,
                          env=base, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


CLAUDE_INPUT = {"session_id": "s1", "cwd": "/work/widget", "hook_event_name": "SessionStart",
                "transcript_path": "/home/example/.claude/projects/-work-widget/s1.jsonl"}
CODEX_INPUT = {"session_id": "s2", "cwd": "/work/widget", "hook_event_name": "SessionStart",
               "model": "example-model",
               "transcript_path": "/home/example/.codex/sessions/2026/10/08/s2.jsonl"}
CURSOR_INPUT = {"session_id": "s3", "cwd": "/work/widget", "hook_event_name": "SessionStart",
                "transcript_path": "/home/example/.cursor/projects/widget/s3.jsonl"}


@posix_only
@pytest.mark.parametrize("payload, env, expected", [
    (CLAUDE_INPUT, {"CLAUDE_PROJECT_DIR": "/work/widget", "CLAUDE_PLUGIN_ROOT": "/p"}, "claude-code"),
    (dict(CLAUDE_INPUT, transcript_path="C:\\Users\\example\\.claude\\projects\\w\\s1.jsonl"),
     {"CLAUDE_PROJECT_DIR": "C:\\work\\widget"}, "claude-code"),
    # A Claude-shaped transcript without CLAUDE_PROJECT_DIR is not enough.
    (CLAUDE_INPUT, {}, "other"),
    (CODEX_INPUT, {"PLUGIN_ROOT": "/p", "CLAUDE_PLUGIN_ROOT": "/p", "CLAUDE_PLUGIN_DATA": "/d"}, "codex"),
    (CODEX_INPUT, {}, "codex"),
    (dict(CODEX_INPUT, transcript_path=None), {"PLUGIN_ROOT": "/p"}, "codex"),
    # Cursor sets CLAUDE_PROJECT_DIR but keeps its transcripts elsewhere.
    (CURSOR_INPUT, {"CLAUDE_PROJECT_DIR": "/work/widget"}, "other"),
    (CURSOR_INPUT, {"CLAUDE_PROJECT_DIR": "/work/widget", "PLUGIN_ROOT": "/p"}, "other"),
    ("", {"CLAUDE_PROJECT_DIR": "/work/widget"}, "other"),
    ("not json", {"PLUGIN_ROOT": "/p"}, "other"),
    ("[1, 2]", {}, "other"),
])
def test_harness_detection(payload, env, expected):
    assert detect(payload, env) == expected


@posix_only
def test_harness_detection_without_python_is_other(tmp_path):
    assert detect(CLAUDE_INPUT, {"CLAUDE_PROJECT_DIR": "/work"}, path=str(tmp_path)) == "other"


# =================================================================================
# clone-sync.sh against real repositories
# =================================================================================


class World:
    """A bare 'GitHub' remote per slug, clones under repos/, a tmp HOME."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.cache = tmp_path / "cache"
        self.repos = tmp_path / "repos"
        self.repos.mkdir()
        self.gitconfig = tmp_path / "gitconfig"
        self.gitconfig.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.env = {
            "PATH": clean_path(), "HOME": str(self.home), "XDG_CACHE_HOME": str(self.cache),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Example Person", "GIT_AUTHOR_EMAIL": "person@example.com",
            "GIT_COMMITTER_NAME": "Example Person", "GIT_COMMITTER_EMAIL": "person@example.com",
            "CLONE_SYNC_ROOTS": str(self.repos / "*"), "LANG": "C",
        }
        self.remotes = {}

    def git(self, cwd, *args, check=True):
        proc = subprocess.run(["git", *args], cwd=str(cwd), env=self.env,
                              capture_output=True, text=True)
        if check:
            assert proc.returncode == 0, (args, proc.stderr)
        return proc.stdout.strip()

    def remote(self, slug=SLUG, branch="main"):
        """Create the bare remote for slug with one commit on branch."""
        bare = self.tmp / "remotes" / (slug.replace("/", "__") + ".git")
        seed = self.tmp / "seeds" / slug.replace("/", "__")
        seed.mkdir(parents=True)
        self.git(seed, "init", "-q", "-b", branch)
        (seed / "README.md").write_text("one\n", encoding="utf-8")
        self.git(seed, "add", "README.md")
        self.git(seed, "commit", "-q", "-m", "one")
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.tmp, "clone", "-q", "--bare", str(seed), str(bare))
        self.git(seed, "remote", "add", "origin", str(bare))
        self.remotes[slug] = (bare, seed, branch)
        return bare

    def map_url(self, url, slug=SLUG):
        bare = self.remotes[slug][0]
        self.git(self.tmp, "config", "--file", str(self.gitconfig), "--add",
                 f"url.{bare.as_uri()}.insteadOf", url)

    def advance(self, slug=SLUG, name="CHANGES.md", text="two\n"):
        bare, seed, branch = self.remotes[slug]
        (seed / name).write_text(text, encoding="utf-8")
        self.git(seed, "add", name)
        self.git(seed, "commit", "-q", "-m", f"add {name}")
        self.git(seed, "push", "-q", "origin", branch)
        return self.git(seed, "rev-parse", "HEAD")

    def clone(self, name, slug=SLUG, url=URL, parent=None):
        bare = self.remotes[slug][0]
        dest = (parent or self.repos) / name
        self.git(self.tmp, "clone", "-q", str(bare), str(dest))
        self.git(dest, "remote", "set-url", "origin", url)
        return dest

    def head(self, clone):
        return self.git(clone, "rev-parse", "HEAD")

    def sync(self, *args, env=None):
        full = dict(self.env)
        full.update(env or {})
        return subprocess.run([BASH, str(CLONE_SYNC / "clone-sync.sh"), *args],
                              env=full, capture_output=True, text=True)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.remote()
    w.map_url(URL)
    return w


def lines(proc):
    return sorted(proc.stdout.splitlines())


@posix_only
def test_a_clean_clone_behind_its_remote_is_fast_forwarded(world):
    clone = world.clone("widget")
    new = world.advance()
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 0, proc.stderr
    assert lines(proc) == [f"updated {clone}"]
    assert world.head(clone) == new
    assert proc.stderr == ""


@posix_only
def test_a_clone_already_at_the_remote_is_current(world):
    world.advance()
    clone = world.clone("widget")
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 0 and lines(proc) == [f"current {clone}"]


@posix_only
@pytest.mark.parametrize("url", [
    "https://github.com/Example-Org/Widget",
    "https://github.com/example-org/widget/",
    "git@github.com:Example-Org/widget.git",
    "ssh://git@github.com/example-org/WIDGET.git",
    "ssh://git@ssh.github.com:443/example-org/widget.git",
])
def test_every_github_url_form_matches_case_insensitively(world, url):
    world.map_url(url)
    clone = world.clone("widget", url=url)
    new = world.advance()
    proc = world.sync("--repo", "EXAMPLE-ORG/widget")
    assert lines(proc) == [f"updated {clone}"], proc.stdout + proc.stderr
    assert world.head(clone) == new


@posix_only
def test_a_credential_in_the_origin_url_is_never_printed(world):
    url = "https://example-user:zz-marker-zz@github.com/example-org/widget.git"
    world.map_url(url)
    clone = world.clone("widget", url=url)
    world.advance()
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"updated {clone}"]
    assert "zz-marker-zz" not in proc.stdout + proc.stderr


@posix_only
def test_clones_of_other_repos_are_not_touched_or_reported(world):
    world.remote("example-org/other")
    world.map_url("https://github.com/example-org/other.git", "example-org/other")
    other = world.clone("other", "example-org/other", "https://github.com/example-org/other.git")
    before = world.head(other)
    world.advance("example-org/other")
    clone = world.clone("widget")
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"current {clone}"]
    assert world.head(other) == before


@posix_only
def test_all_syncs_every_github_clone_and_ignores_other_hosts(world):
    world.remote("example-org/other")
    world.map_url("https://github.com/example-org/other.git", "example-org/other")
    a = world.clone("widget")
    b = world.clone("other", "example-org/other", "https://github.com/example-org/other.git")
    c = world.clone("elsewhere", url="https://example.com/example-org/widget.git")
    world.advance()
    world.advance("example-org/other")
    proc = world.sync("--all")
    assert proc.returncode == 0, proc.stderr
    assert lines(proc) == sorted([f"updated {a}", f"updated {b}"])
    assert str(c) not in proc.stdout


@posix_only
def test_repo_of_resolves_a_directory_inside_a_clone(world):
    a = world.clone("widget")
    b = world.clone("widget-2")
    (a / "sub").mkdir()
    world.advance()
    proc = world.sync("--repo-of", str(a / "sub"))
    assert lines(proc) == sorted([f"updated {a}", f"updated {b}"])


@posix_only
def test_repo_of_a_directory_without_a_github_origin_does_nothing(world, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = world.sync("--repo-of", str(plain))
    assert proc.returncode == 0 and proc.stdout == ""


def _not_default_branch(w, clone):
    w.git(clone, "checkout", "-q", "-b", "feature")


def _detached(w, clone):
    w.git(clone, "checkout", "-q", "--detach")


def _unstaged(w, clone):
    (clone / "README.md").write_text("edited\n", encoding="utf-8")


def _staged(w, clone):
    (clone / "new.md").write_text("new\n", encoding="utf-8")
    w.git(clone, "add", "new.md")


def _no_default_branch(w, clone):
    w.git(clone, "branch", "-q", "-m", "main", "trunk")
    w.git(clone, "remote", "set-head", "origin", "--delete")
    w.git(clone, "update-ref", "refs/remotes/origin/trunk", "refs/remotes/origin/main")
    w.git(clone, "update-ref", "-d", "refs/remotes/origin/main")


def _in_progress(path, directory=False):
    def make(w, clone):
        target = clone / ".git" / path
        if directory:
            target.mkdir()
        else:
            target.write_text(w.head(clone) + "\n", encoding="utf-8")
    return make


SKIPS = {
    "not-default-branch": _not_default_branch,
    "detached-head": _detached,
    "dirty-unstaged": _unstaged,
    "dirty-staged": _staged,
    "no-default-branch": _no_default_branch,
    "merge": _in_progress("MERGE_HEAD"),
    "rebase-merge": _in_progress("rebase-merge", directory=True),
    "rebase-apply": _in_progress("rebase-apply", directory=True),
    "cherry-pick": _in_progress("CHERRY_PICK_HEAD"),
    "revert": _in_progress("REVERT_HEAD"),
    "bisect": _in_progress("BISECT_LOG"),
}
REASONS = {"dirty-unstaged": "dirty", "dirty-staged": "dirty",
           "merge": "operation-in-progress", "rebase-merge": "operation-in-progress",
           "rebase-apply": "operation-in-progress", "cherry-pick": "operation-in-progress",
           "revert": "operation-in-progress", "bisect": "operation-in-progress"}


@posix_only
@pytest.mark.parametrize("case", sorted(SKIPS))
def test_a_clone_that_is_not_safe_to_fast_forward_is_skipped_untouched(world, case):
    clone = world.clone("widget")
    SKIPS[case](world, clone)
    before = world.head(clone)
    status_before = world.git(clone, "status", "--porcelain")
    world.advance()
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 0, proc.stderr
    assert lines(proc) == [f"skipped:{REASONS.get(case, case)} {clone}"]
    assert world.head(clone) == before
    assert world.git(clone, "status", "--porcelain") == status_before


@posix_only
def test_an_untracked_file_does_not_block_a_fast_forward(world):
    clone = world.clone("widget")
    (clone / "notes.txt").write_text("mine\n", encoding="utf-8")
    new = world.advance()
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"updated {clone}"]
    assert world.head(clone) == new
    assert (clone / "notes.txt").read_text(encoding="utf-8") == "mine\n"


@posix_only
def test_an_untracked_file_the_fast_forward_would_overwrite_is_kept(world):
    clone = world.clone("widget")
    (clone / "CHANGES.md").write_text("mine\n", encoding="utf-8")
    before = world.head(clone)
    world.advance(name="CHANGES.md")
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 1
    assert lines(proc) == [f"error:ff-failed {clone}"]
    assert world.head(clone) == before
    assert (clone / "CHANGES.md").read_text(encoding="utf-8") == "mine\n"


@posix_only
def test_a_clone_with_its_own_commits_is_reported_diverged(world):
    clone = world.clone("widget")
    (clone / "local.md").write_text("local\n", encoding="utf-8")
    world.git(clone, "add", "local.md")
    world.git(clone, "commit", "-q", "-m", "local")
    before = world.head(clone)
    world.advance()
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"skipped:diverged {clone}"]
    assert world.head(clone) == before


@posix_only
def test_a_clone_ahead_of_its_remote_is_current(world):
    clone = world.clone("widget")
    (clone / "local.md").write_text("local\n", encoding="utf-8")
    world.git(clone, "add", "local.md")
    world.git(clone, "commit", "-q", "-m", "local")
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"current {clone}"]


@posix_only
def test_a_failed_fetch_is_an_error_and_exits_one(world):
    clone = world.clone("widget")
    world.git(world.tmp, "config", "--file", str(world.gitconfig), "--add",
              f"url.{(world.tmp / 'missing.git').as_uri()}.insteadOf",
              "https://github.com/example-org/gone.git")
    world.git(clone, "remote", "set-url", "origin", "https://github.com/example-org/gone.git")
    proc = world.sync("--repo", "example-org/gone")
    assert proc.returncode == 1
    assert lines(proc) == [f"error:fetch-failed {clone}"]


@posix_only
def test_a_linked_worktree_is_skipped_and_its_main_clone_synced(world):
    clone = world.clone("widget")
    worktree = world.repos / "widget-wt"
    world.git(clone, "worktree", "add", "-q", "-b", "wt", str(worktree))
    world.advance()
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == sorted([f"updated {clone}", f"skipped:linked-worktree {worktree}"])


@posix_only
def test_claude_worktrees_are_never_candidates(world):
    clone = world.clone("widget")
    nested = clone / ".claude" / "worktrees" / "task"
    world.git(clone, "worktree", "add", "-q", "-b", "task", str(nested))
    world.advance()
    roots = f"{world.repos}/*:{world.repos}/*/.claude/worktrees/*"
    proc = world.sync("--repo", SLUG, env={"CLONE_SYNC_ROOTS": roots})
    assert lines(proc) == [f"updated {clone}"]


@posix_only
def test_a_held_lock_makes_a_second_run_exit_quietly(world):
    clone = world.clone("widget")
    before = world.head(clone)
    world.advance()
    lock = world.cache / "clone-sync" / "lock"
    lock.mkdir(parents=True)
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""
    assert world.head(clone) == before
    assert lock.is_dir()  # someone else's lock is never removed


@posix_only
def test_a_stale_lock_is_taken_over_and_released(world):
    clone = world.clone("widget")
    world.advance()
    lock = world.cache / "clone-sync" / "lock"
    lock.mkdir(parents=True)
    an_hour_ago = time.time() - 3600
    os.utime(lock, (an_hour_ago, an_hour_ago))
    proc = world.sync("--repo", SLUG)
    assert lines(proc) == [f"updated {clone}"]
    assert not lock.exists()


@posix_only
def test_the_lock_is_released_after_a_run(world):
    world.clone("widget")
    proc = world.sync("--repo", SLUG)
    assert proc.returncode == 0
    assert not (world.cache / "clone-sync" / "lock").exists()


@posix_only
@pytest.mark.parametrize("args", [[], ["--repo"], ["--repo", "not a slug"], ["--repo", "a/b/c"],
                                  ["--bogus"]])
def test_a_usage_error_exits_two(world, args):
    proc = world.sync(*args)
    assert proc.returncode == 2 and proc.stdout == ""


# =================================================================================
# The hooks: on-merge.sh and on-session-start.sh
# =================================================================================


def run_hook(tmp_path, script, payload, env=None):
    record = tmp_path / "launches.txt"
    full = {"PATH": clean_path(), "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "CLONE_SYNC_HOOK_DRY_RUN": str(record)}
    full.update(env or {})
    text = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run([BASH, str(CLONE_SYNC / script)], input=text, env=full,
                          capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "" and proc.stderr == ""
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


def bash_call(command, response=None, cwd="/work/widget", **extra):
    payload = {"session_id": "s1", "cwd": cwd, "hook_event_name": "PostToolUse",
               "tool_name": "Bash", "tool_input": {"command": command},
               "tool_response": response if response is not None
               else {"stdout": "Merged pull request #12\n", "stderr": "", "interrupted": False}}
    payload.update(extra)
    return payload


@posix_only
@pytest.mark.parametrize("command, expected", [
    ("gh pr merge 12 --merge", "--repo-of /work/widget"),
    ("gh pr merge --repo example-org/widget 12", f"--repo {SLUG}"),
    ("gh pr merge -R Example-Org/Widget 12 --merge --delete-branch", f"--repo {SLUG}"),
    ("gh pr merge --repo=github.com/example-org/widget 12", f"--repo {SLUG}"),
    ("gh pr merge https://github.com/Example-Org/Widget/pull/12 --squash", f"--repo {SLUG}"),
    ("cd /work && gh pr merge 12 --merge && git pull", "--repo-of /work/widget"),
    ("/usr/bin/gh pr merge 12 --merge 2>&1 | tail -5", "--repo-of /work/widget"),
])
def test_a_successful_gh_pr_merge_launches_a_sync(tmp_path, command, expected):
    assert run_hook(tmp_path, "on-merge.sh", bash_call(command)) == [f"harness=other {expected}"]


@posix_only
def test_an_mcp_merge_tool_launches_a_sync(tmp_path):
    payload = {"session_id": "s1", "cwd": "/work", "hook_event_name": "PostToolUse",
               "tool_name": "mcp__github__merge_pull_request",
               "tool_input": {"owner": "Example-Org", "repo": "widget", "pullNumber": 12},
               "tool_response": {"merged": True, "message": "Pull Request successfully merged"}}
    assert run_hook(tmp_path, "on-merge.sh", payload) == [f"harness=other --repo {SLUG}"]


@posix_only
@pytest.mark.parametrize("payload", [
    bash_call("gh pr view 12"),
    bash_call("git merge --ff-only origin/main"),
    bash_call("gh pr create --fill"),
    bash_call("ls"),
    {"tool_name": "Read", "tool_input": {"file_path": "/work/x"}, "cwd": "/work"},
    {"tool_name": "mcp__github__merge_pull_request", "tool_input": {"owner": "example-org"}},
    "",
    "not json",
])
def test_anything_else_launches_nothing(tmp_path, payload):
    assert run_hook(tmp_path, "on-merge.sh", payload) == []


@posix_only
@pytest.mark.parametrize("response", [
    {"stdout": "", "stderr": "X Pull request is not mergeable", "exit_code": 1},
    {"stdout": "", "stderr": "", "interrupted": True},
    {"is_error": True, "content": "failed"},
    "Exit code: 1\nWall time: 0.4 seconds\nOutput:\nX Pull request is not mergeable\n",
    "Process exited with code 1\nOutput:\nnot mergeable\n",
    {"metadata": {"exit_code": 1}, "output": "not mergeable"},
])
def test_a_failed_merge_launches_nothing(tmp_path, response):
    assert run_hook(tmp_path, "on-merge.sh", bash_call("gh pr merge 12 --merge", response)) == []


@posix_only
def test_a_failed_mcp_merge_launches_nothing(tmp_path):
    payload = {"tool_name": "mcp__github__merge_pull_request",
               "tool_input": {"owner": "example-org", "repo": "widget", "pullNumber": 12},
               "tool_response": {"isError": True, "content": [{"type": "text", "text": "405"}]}}
    assert run_hook(tmp_path, "on-merge.sh", payload) == []


@posix_only
@pytest.mark.parametrize("response", [
    "Exit code: 0\nWall time: 1.0 seconds\nOutput:\nMerged\n",
    {"exit_code": 0, "stdout": "Merged"},
    None,
])
def test_an_unclear_or_zero_exit_counts_as_success(tmp_path, response):
    payload = bash_call("gh pr merge 12 --merge")
    payload["tool_response"] = response
    assert run_hook(tmp_path, "on-merge.sh", payload) == ["harness=other --repo-of /work/widget"]


@posix_only
def test_the_hook_records_which_harness_ran_it(tmp_path):
    claude = bash_call("gh pr merge 12", transcript_path=CLAUDE_INPUT["transcript_path"])
    codex = bash_call("gh pr merge 12", transcript_path=CODEX_INPUT["transcript_path"])
    assert run_hook(tmp_path, "on-merge.sh", claude, {"CLAUDE_PROJECT_DIR": "/work"}) \
        == ["harness=claude-code --repo-of /work/widget"]
    (tmp_path / "launches.txt").unlink()
    assert run_hook(tmp_path, "on-merge.sh", codex, {"PLUGIN_ROOT": "/p"}) \
        == ["harness=codex --repo-of /work/widget"]


@posix_only
def test_codex_shell_workdir_is_preferred_over_the_session_cwd(tmp_path):
    payload = bash_call("gh pr merge 12", cwd="/work")
    payload["tool_input"]["workdir"] = "/work/widget"
    assert run_hook(tmp_path, "on-merge.sh", payload) == ["harness=other --repo-of /work/widget"]


@posix_only
def test_session_start_syncs_the_session_directory(tmp_path):
    assert run_hook(tmp_path, "on-session-start.sh", CODEX_INPUT, {"PLUGIN_ROOT": "/p"}) \
        == ["harness=codex --repo-of /work/widget"]


@posix_only
def test_session_start_without_a_cwd_falls_back_per_harness(tmp_path):
    claude = dict(CLAUDE_INPUT)
    del claude["cwd"]
    assert run_hook(tmp_path, "on-session-start.sh", claude, {"CLAUDE_PROJECT_DIR": "/work/p"}) \
        == ["harness=claude-code --repo-of /work/p"]
    (tmp_path / "launches.txt").unlink()
    assert run_hook(tmp_path, "on-session-start.sh", "not json") \
        == [f"harness=other --repo-of {tmp_path}"]


@posix_only
def test_a_real_launch_is_detached_silent_and_runs_to_completion(tmp_path):
    """Without the dry-run seam the hook starts clone-sync.sh detached. The
    test hands the hook one extra pipe end, which the detached child inherits;
    EOF on the other end means every holder, the child included, has exited.
    That is the completion marker: no sleep, no polling, no stray process.
    There is no clone to sync here, so the child only takes and releases the
    lock."""
    payload = bash_call("gh pr merge 12", cwd=str(tmp_path))
    env = {"PATH": clean_path(), "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "CLONE_SYNC_ROOTS": str(tmp_path / "no-repos" / "*")}
    read_end, write_end = os.pipe()
    try:
        proc = subprocess.run([BASH, str(CLONE_SYNC / "on-merge.sh")], input=json.dumps(payload),
                              env=env, capture_output=True, text=True, cwd=str(tmp_path),
                              pass_fds=(write_end,))
        os.close(write_end)
        write_end = None
        ready, _, _ = select.select([read_end], [], [], 60)
        assert ready, "the detached clone-sync.sh did not finish within 60 seconds"
        assert os.read(read_end, 1) == b""
    finally:
        os.close(read_end)
        if write_end is not None:
            os.close(write_end)
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""
    log = (tmp_path / "cache" / "clone-sync" / "hook.log").read_text(encoding="utf-8")
    assert f"harness=other --repo-of {tmp_path}" in log
    assert (tmp_path / "cache" / "clone-sync").is_dir()
    assert not (tmp_path / "cache" / "clone-sync" / "lock").exists()


# =================================================================================
# hook_input.py, directly
# =================================================================================


@pytest.mark.parametrize("value, expected", [
    ("example-org/widget", SLUG),
    ("Example-Org/Widget", SLUG),
    ("github.com/example-org/widget", SLUG),
    ("https://github.com/example-org/widget.git", SLUG),
    ("https://github.com/example-org/widget/pull/7", SLUG),
    ("example.com/example-org/widget", None),
    ("widget", None),
    ("a/b/c/d", None),
    (None, None),
])
def test_slug_normalization(value, expected):
    assert hook_input._slug(value) == expected


@pytest.mark.parametrize("command, expected", [
    ("gh pr merge 1", (True, None)),
    ("gh  pr  merge", (True, None)),
    (["bash", "-lc", "gh pr merge 1 --repo example-org/widget"], (True, SLUG)),
    ("gh pr merge 1 --body 'merging --repo x/y now'", (True, None)),
    ("echo done; gh pr merge 1 -Rexample-org/widget", (True, SLUG)),
    ("gh pr merge 'unbalanced", (True, None)),
    ("gh pr view 1", (False, None)),
    ("ghx pr merge 1", (False, None)),
    ("gh repo merge", (False, None)),
    ("", (False, None)),
    (None, (False, None)),
])
def test_gh_pr_merge_parsing(command, expected):
    assert hook_input.gh_pr_merge(command) == expected


# =================================================================================
# hooks.json
# =================================================================================


HOOKS_JSON = HOOKS / "hooks.json"
CLAUDE_ONLY_FIELDS = ("if", "args", "shell")
WINDOWS_BASH = '"C:\\Program Files\\Git\\bin\\bash.exe" '


def handlers():
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in config["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, group, handler


def test_hooks_json_holds_only_the_harness_agnostic_events():
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    assert set(config) == {"hooks"}
    assert set(config["hooks"]) == {"PostToolUse", "SessionStart"}


def test_every_handler_runs_in_claude_code_and_in_codex_on_both_systems():
    found = list(handlers())
    assert found
    for event, _, handler in found:
        assert handler["type"] == "command", event
        command, windows = handler["command"], handler["commandWindows"]
        prefix = '"${CLAUDE_PLUGIN_ROOT}"/'
        assert command.startswith(prefix), command
        script = command[len(prefix):]
        assert re.fullmatch(r"hooks/[A-Za-z0-9_./-]+\.sh", script), command
        assert windows == f'{WINDOWS_BASH}"%PLUGIN_ROOT%/{script}"', windows
        target = HOOKS.parent / script
        assert target.is_file(), script
        if os.name != "nt":
            assert os.access(target, os.X_OK), script
        assert isinstance(handler["timeout"], int) and 0 < handler["timeout"] <= 30
        # Codex drops these; a handler that relied on one would behave differently there.
        assert not set(CLAUDE_ONLY_FIELDS) & set(handler), handler


def test_no_handler_runs_through_a_bare_bash():
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    assert check_consistency._bare_bash_hooks(config) == 0


def test_the_matchers_mean_the_same_in_both_harnesses():
    # Claude Code reads a matcher with any character outside [A-Za-z0-9_|] as
    # an unanchored regex, and Codex always does; both test it with search
    # semantics, which Python's re.search mirrors for these patterns.
    matchers = {event: group["matcher"] for event, group, _ in handlers()}
    post = re.compile(matchers["PostToolUse"])
    assert not re.fullmatch(r"[A-Za-z0-9_|]+", matchers["PostToolUse"])
    for tool in ("Bash", "mcp__github__merge_pull_request",
                 "mcp__example-connector__merge_pull_request"):
        assert post.search(tool), tool
    for tool in ("Read", "Edit", "mcp__github__create_pull_request"):
        assert not post.search(tool), tool
    start = re.compile(matchers["SessionStart"])
    assert start.search("startup") and start.search("resume")
    assert not start.search("clear") and not start.search("compact")


# =================================================================================
# The scheduled task's launcher
# =================================================================================


def fake_install(tmp_path):
    install = tmp_path / "install"
    script = install / "hooks" / "clone-sync" / "clone-sync.sh"
    script.parent.mkdir(parents=True)
    script.write_text('#!/bin/sh\nprintf "%s|%s\\n" "$*" "${CLONE_SYNC_ROOTS:-}" >> "$RECORD"\n',
                      encoding="utf-8")
    return install


def write_registry(home, entries):
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"version": 2, "plugins": entries}), encoding="utf-8")


def run_launcher(tmp_path, *args, path_first=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = clean_path() if path_first is None else f"{path_first}{os.pathsep}{clean_path()}"
    env = {"PATH": path, "HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "RECORD": str(tmp_path / "record.txt")}
    proc = subprocess.run([BASH, str(CLONE_SYNC / "launcher.sh"), *args], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == "", proc
    record = tmp_path / "record.txt"
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


@posix_only
def test_the_launcher_runs_the_user_scope_install(tmp_path):
    install = fake_install(tmp_path)
    write_registry(tmp_path / "home", {"adam-coding-local@adam-agentskills": [
        {"scope": "project", "installPath": str(tmp_path / "elsewhere")},
        {"scope": "user", "installPath": str(install)},
    ]})
    assert run_launcher(tmp_path) == ["--all|"]


@posix_only
@pytest.mark.parametrize("registry", [
    None,
    {"adam-coding-anywhere@adam-agentskills": [{"scope": "user", "installPath": "/x"}]},
    {"adam-coding-local@adam-agentskills": "not a list"},
    "not json",
])
def test_the_launcher_exits_quietly_when_the_plugin_is_not_installed(tmp_path, registry):
    home = tmp_path / "home"
    home.mkdir()
    if isinstance(registry, dict):
        write_registry(home, registry)
    elif registry is not None:
        path = home / ".claude" / "plugins" / "installed_plugins.json"
        path.parent.mkdir(parents=True)
        path.write_text(registry, encoding="utf-8")
    assert run_launcher(tmp_path) == []
    assert "skipped" in (tmp_path / "cache" / "clone-sync" / "scheduled.log").read_text(encoding="utf-8")


def stub_wsl(tmp_path, running):
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    listing = "".join(name + "\r\n" for name in running).encode("utf-16-le")
    (tmp_path / "running.bin").write_bytes(listing)
    stub = bin_dir / "wsl.exe"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --list ]; then cat "%s"; exit 0; fi\n'
        'printf "%%s\\n" "$@" > "%s"\n' % (tmp_path / "running.bin", tmp_path / "wsl-args.txt"),
        encoding="utf-8")
    stub.chmod(0o755)
    return bin_dir


@posix_only
def test_the_launcher_runs_inside_an_already_running_distro(tmp_path):
    install = fake_install(tmp_path)
    write_registry(tmp_path / "home", {"adam-coding-local@adam-agentskills": [
        {"scope": "user", "installPath": str(install)}]})
    bin_dir = stub_wsl(tmp_path, ["Example-Linux"])
    assert run_launcher(tmp_path, path_first=bin_dir) == ["--all|"]
    args = (tmp_path / "wsl-args.txt").read_text(encoding="utf-8").splitlines()
    assert args[:5] == ["-d", "Example-Linux", "-e", "bash", "-lc"], args
    assert "--local-only" in args[5] and 'CLONE_SYNC_ROOTS="$HOME/repos/*"' in args[5]


@posix_only
@pytest.mark.parametrize("args, running", [([], []), (["--local-only"], ["Example-Linux"])])
def test_the_launcher_never_starts_wsl(tmp_path, args, running):
    bin_dir = stub_wsl(tmp_path, running)
    run_launcher(tmp_path, *args, path_first=bin_dir)
    assert not (tmp_path / "wsl-args.txt").exists()


# =================================================================================
# Register-CloneSyncTask.ps1, dry run only
# =================================================================================


PWSH = shutil.which("pwsh")
REGISTER = CLONE_SYNC / "Register-CloneSyncTask.ps1"


def run_register(tmp_path, git_bash):
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "localappdata"),
               CLAUDE_CODE_GIT_BASH_PATH=str(git_bash))
    # -DryRun AND -WhatIf: either alone prints and returns before anything is
    # registered or copied; both, so one regression cannot register a task.
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(REGISTER),
                           "-DryRun", "-WhatIf"], env=env, capture_output=True, text=True)


@pytest.mark.skipif(PWSH is None, reason="no pwsh on this machine")
def test_the_register_dry_run_prints_the_task_and_changes_nothing(tmp_path):
    git_bash = tmp_path / "bash.exe"
    git_bash.write_text("", encoding="utf-8")
    proc = run_register(tmp_path, git_bash)
    assert proc.returncode == 0, proc.stderr
    task = json.loads(proc.stdout)
    launcher = Path(task["Launcher"])
    assert task["TaskName"] == "adam-clone-sync"
    assert task["Execute"] == "conhost.exe"
    assert task["Argument"] == f'--headless "{git_bash}" "{launcher}"'
    assert launcher.name == "clone-sync-launcher.sh"
    assert launcher.parent == tmp_path / "localappdata" / "adam-agentskills"
    assert Path(task["LauncherSource"]) == CLONE_SYNC / "launcher.sh"
    assert task["RepetitionMinutes"] == 30
    assert set(task["Triggers"]) == {"AtLogOn", "Every30Minutes"}
    assert task["RunLevel"] == "Limited"
    assert not (tmp_path / "localappdata").exists()


@pytest.mark.skipif(PWSH is None, reason="no pwsh on this machine")
def test_the_register_script_refuses_a_missing_git_bash(tmp_path):
    proc = run_register(tmp_path, tmp_path / "no-such-bash.exe")
    assert proc.returncode != 0
    assert "Git Bash not found" in proc.stdout + proc.stderr
    assert not (tmp_path / "localappdata").exists()
