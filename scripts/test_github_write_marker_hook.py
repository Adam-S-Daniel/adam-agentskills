"""Tests for the adam-anything-anywhere plugin's github-write-marker hook.

The hook (plugins/adam-anything-anywhere/hooks/github-write-marker/) is a
verbatim copy of _agent-guidance's .claude/hooks/github-write-marker.sh, whose
own suite covers it in depth. These tests pin what this plugin depends on: it
is wired as a harness-agnostic PreToolUse hook in hooks/hooks.json, so Claude
Code and Codex both run it (ADR 0017, #198), a GitHub write without the agent
marker is blocked, one with it passes, an APPROVE review is blocked, a write
holding a privacy-denylisted term is blocked without naming it, and the
calls a session makes all the time (non-GitHub tools, reads, `git push`,
GitHub text inside an argument) are never blocked. The tests that run the
script are POSIX-only; the shape tests also run on Windows.

Run with the repo's runner, never as a plain script:
    python3 -m pytest scripts/test_github_write_marker_hook.py -q
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "adam-anything-anywhere"
HOOKS = PLUGIN / "hooks"
SCRIPT = HOOKS / "github-write-marker" / "github-write-marker.sh"
CLAUDE_CODE_JSON = HOOKS / "claude-code.json"
HOOKS_JSON = HOOKS / "hooks.json"

MARKER = "<sub>🤖 Written by Claude Code on behalf of @Adam-S-Daniel</sub>"
BLOCK = 2

# The repo's pattern for hook tests (test_fleet_guidance_hook.py,
# test_credit_lane.py): the tests that run the script need a POSIX bash; the
# shape tests also run on Windows. On a Windows runner a bare `bash` is
# C:\Windows\System32\bash.exe, the WSL launcher (ADR 0016), which exits 1
# without running the script, so every run_hook() case read as exit 1 there.
posix_only = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or shutil.which("python3") is None,
    reason="runs the hook script with a POSIX bash and python3")


def run_hook(event, cwd=None, denylist=None):
    """Feed one PreToolUse event to the hook; return (exit code, stderr).

    The privacy denylist and this machine's identity names are pinned (none
    unless `denylist` names a file), so no case depends on the machine."""
    env = {k: v for k, v in os.environ.items() if k != "AGENT_NAME"}
    env["PRIVACY_DENYLIST_FILE"] = str(denylist or REPO / "nonexistent-denylist.txt")
    env["AGENT_CHECKS_LOCAL_USER"] = ""
    env["AGENT_CHECKS_LOCAL_HOST"] = ""
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        input=event if isinstance(event, str) else json.dumps(event),
        capture_output=True, text=True, env=env, timeout=30,
        cwd=cwd or str(REPO))
    return proc.returncode, proc.stderr


def bash(command, cwd="/nonexistent-cwd"):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def mcp(tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


# ── Blocked: a GitHub write without the marker, and any APPROVE ──────────────

@posix_only
@pytest.mark.parametrize("event", [
    mcp("mcp__github__create_pull_request", title="t", body="## TLDR\nA change."),
    mcp("mcp__github__add_issue_comment", body="A comment."),
    mcp("mcp__github__update_pull_request", body="A new description."),
    mcp("mcp__github-mcp__issue_write", method="create", title="t", body="Text."),
    bash('gh pr comment 7 --body "A comment."'),
    bash('gh issue create --title t --body "Text."'),
    bash("gh api -X POST repos/example/repo/issues/7/comments -f body='Text.'"),
    bash('cd /tmp && gh pr create --title t --body "Text."'),
], ids=lambda e: e["tool_name"] + ":" + str(e["tool_input"].get("command", "")))
def test_a_github_write_without_the_marker_is_blocked(event):
    rc, err = run_hook(event)
    assert rc == BLOCK, err
    assert MARKER in err  # it tells the agent exactly what to append


@posix_only
@pytest.mark.parametrize("event", [
    mcp("mcp__github__create_pull_request", title="t", body="## TLDR\nA change.\n\n" + MARKER),
    mcp("mcp__github__add_issue_comment", body="A comment.\n\n" + MARKER),
    mcp("mcp__github-mcp__issue_write", method="create", title="t", body="Text.\n" + MARKER),
    bash('gh pr comment 7 --body "A comment.\n\n%s"' % MARKER),
    bash("gh issue create --title t --body-file - <<'EOF'\nText.\n\n%s\nEOF" % MARKER),
], ids=lambda e: e["tool_name"])
def test_a_github_write_with_the_marker_passes(event):
    rc, err = run_hook(event)
    assert rc == 0, err


@posix_only
def test_a_body_file_holding_the_marker_passes(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Text.\n\n%s\n" % MARKER, encoding="utf-8")
    rc, err = run_hook(bash("gh pr create --title t --body-file %s" % shlex.quote(str(body))))
    assert rc == 0, err
    # Negative control: the same file without the marker is blocked.
    body.write_text("Text.\n", encoding="utf-8")
    rc, err = run_hook(bash("gh pr create --title t --body-file %s" % shlex.quote(str(body))))
    assert rc == BLOCK, err


@posix_only
@pytest.mark.parametrize("event", [
    mcp("mcp__github__pull_request_review_write", method="create", event="APPROVE",
        body="Looks right.\n" + MARKER),
    mcp("mcp__github-mcp__pull_request_review_write", method="submit_pending", event="approve"),
    bash("gh pr review 7 --approve"),
    bash('gh pr review 7 -a -b "ok %s"' % MARKER),
], ids=lambda e: e["tool_name"] + ":" + str(e["tool_input"].get("command", "")))
def test_an_approve_review_is_blocked_even_with_the_marker(event):
    rc, err = run_hook(event)
    assert rc == BLOCK, err
    assert "APPROVE" in err


# ── Blocked: a privacy-denylisted term in any GitHub write ──────────────────
# The term is invented. The block names the field and the pattern number, and
# never the term or the pattern, so the hook's own output leaks nothing.

@posix_only
@pytest.mark.parametrize("event", [
    bash('gh issue comment 7 --body "About Zebrafinch.\n\n%s"' % MARKER),
    bash('gh pr create --title "zebrafinch fix" --body "Text.\n\n%s"' % MARKER),
    bash("git push origin 0123abc:refs/heads/zebrafinch-work"),
    mcp("mcp__github__create_branch", owner="example", repo="repo", branch="zebrafinch"),
    mcp("mcp__github__add_issue_comment", body="zebrafinch\n" + MARKER),
], ids=lambda e: e["tool_name"] + ":" + str(e["tool_input"].get("command", "")))
def test_a_denylisted_term_is_blocked_without_naming_it(event, tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("# invented\nzebrafinch\n", encoding="utf-8")
    rc, err = run_hook(event, denylist=denylist)
    assert rc == BLOCK, err
    assert "pattern #1" in err
    assert "zebrafinch" not in err.lower()


@posix_only
def test_a_clean_write_passes_with_a_denylist(tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("zebrafinch\n", encoding="utf-8")
    rc, err = run_hook(bash('gh pr comment 7 --body "Clean.\n\n%s"' % MARKER), denylist=denylist)
    assert rc == 0, err


@posix_only
def test_a_codex_event_is_checked_and_gets_the_codex_footer() -> None:
    event = bash('gh pr comment 7 --body "A comment."')
    event["transcript_path"] = "/home/example/.codex/sessions/s.jsonl"
    rc, err = run_hook(event)
    assert rc == BLOCK, err
    assert "Written by Codex on behalf of @Adam-S-Daniel" in err


# ── Never blocked: the calls every session makes ─────────────────────────────

@posix_only
@pytest.mark.parametrize("event", [
    # Not GitHub at all.
    {"tool_name": "Read", "tool_input": {"file_path": "/tmp/gh-notes.md"}},
    {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.sh",
                                          "content": 'gh pr comment 7 --body "no marker"'}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x", "old_string": "a",
                                         "new_string": 'gh issue create --body "x"'}},
    mcp("mcp__hearthbot__post_message", text="Sent the github PR."),
    mcp("mcp__Claude_Docs__update", body="github body without a marker"),
    # git, not gh.
    bash("git push -u origin claude/example && git log --oneline -1"),
    bash('git commit -q -m "Use gh pr create --body in the docs"'),
    bash("git commit -q -F - <<'EOF'\nRun gh pr comment 7 --body x\nEOF"),
    # GitHub reads.
    bash("gh pr view 7 --json body"),
    bash("gh api repos/example/repo/pulls/7/comments"),
    bash("gh api repos/example/repo/issues/7 --jq .body"),
    mcp("mcp__github__pull_request_read", method="get", pullNumber=7),
    mcp("mcp__github__issue_read", method="get", issue_number=7),
    # GitHub writes with no body to mark.
    bash("gh pr edit 7 --add-label agent-authored"),
    bash("gh pr merge 7 --merge"),
    bash("gh api -X POST repos/example/repo/issues/7/labels -f 'labels[]=x'"),
    mcp("mcp__github__issue_write", method="update", issue_number=7, labels=["x"]),
    mcp("mcp__github__update_pull_request", pullNumber=7, reviewers=["example"]),
    mcp("mcp__github__merge_pull_request", pullNumber=7, merge_method="merge"),
    # GitHub text inside an argument is not a write.
    bash('echo "gh pr comment 7 --body x"'),
    bash('grep -rn "gh issue create --body" scripts/'),
    # Input the hook cannot read is allowed, never wedged.
    "not json",
    "[]",
], ids=lambda e: (e if isinstance(e, str) else e["tool_name"] + ":" + str(e["tool_input"].get("command", ""))))
def test_ordinary_calls_are_never_blocked(event):
    rc, err = run_hook(event)
    assert rc == 0, err


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_the_hook_is_wired_once_for_both_harnesses() -> None:
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    groups = config["hooks"]["PreToolUse"]
    assert len(groups) == 1
    group = groups[0]
    assert len(group["hooks"]) == 1
    handler = group["hooks"][0]
    assert handler["type"] == "command"
    assert handler["timeout"] == 10
    # Harness-agnostic (ADR 0017): no fields Codex drops, a Windows form.
    assert not {"if", "args", "shell"} & set(handler), handler
    assert handler["command"] == (
        '"${CLAUDE_PLUGIN_ROOT}"/hooks/github-write-marker/github-write-marker.sh')
    assert handler["commandWindows"] == (
        '"C:\\Program Files\\Git\\bin\\bash.exe" '
        '"%PLUGIN_ROOT%/hooks/github-write-marker/github-write-marker.sh"')
    # Claude Code merges both files, so a second registration would run it twice.
    claude_only = json.loads(CLAUDE_CODE_JSON.read_text(encoding="utf-8"))
    assert "PreToolUse" not in claude_only["hooks"]


@pytest.mark.parametrize("tool,matches", [
    ("Bash", True),
    ("exec_command", True),
    ("mcp__github__add_issue_comment", True),
    ("mcp__github-mcp__create_pull_request", True),
    ("Read", False),
    ("Write", False),
    ("BashOutput", False),
    ("mcp__hearthbot__post_message", False),
])
def test_the_matcher_reaches_only_shell_and_github_mcp_tools(tool: str, matches: bool) -> None:
    import re
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    assert bool(re.search(matcher, tool)) is matches


def test_the_bundled_script_is_executable_and_names_its_contract():
    assert SCRIPT.is_file()
    if os.name != "nt":
        assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n# github-write-marker.sh")
    assert 'TOKEN = "on behalf of @Adam-S-Daniel"' in text
