#!/usr/bin/env python3
"""Tests for scripts/check_privacy_denylist.py.

Hermetic and deterministic: every test builds a throwaway git repository under
pytest's `tmp_path`, with invented names and example.com addresses only, and
passes the denylist in explicitly — never through this process's environment,
so a denylist the developer happens to have exported cannot change a result.

The property that matters most is the NEGATIVE one: on a match the output
names where and which pattern number, and never the matched text or the
pattern itself. Several tests assert exactly that on the captured output.

Run: python3 -m pytest scripts/test_check_privacy_denylist.py -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_privacy_denylist as cpd  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "check_privacy_denylist.py"

# Invented, and deliberately unlike anything real.
SECRET_NAME = "Zorblax Quimbleton"
SECRET_MAIL = "zquimbleton@example.com"
DENYLIST = "zorblax\\s+quimbleton\n# a comment line is ignored\n\nzquimbleton@example\\.com\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path: Path, files: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    commit(repo, files, "seed")
    return repo


def commit(repo: Path, files: dict, message: str) -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def run(repo: Path, env: dict, *args: str, capsys=None):
    code = cpd.main(["--repo", str(repo), *args], environ=env)
    out = capsys.readouterr() if capsys else None
    return code, out


def assert_nothing_leaked(text: str) -> None:
    lowered = text.lower()
    for leak in ("zorblax", "quimbleton", "example\\.com", "zorblax\\s+quimbleton"):
        assert leak not in lowered, f"output leaked {leak!r}: {text!r}"


def test_a_clean_tree_passes(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "Nothing personal here.\n"})
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 0, out
    assert "OK:" in out.out and "2 pattern(s)" in out.out


def test_a_match_fails_and_reports_only_location_and_index(tmp_path, capsys):
    repo = make_repo(tmp_path, {
        "README.md": "line one\nwritten by zorblax  QUIMBLETON\n",
        "docs/contact.md": "mail " + SECRET_MAIL + "\n",
    })
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 1
    assert "README.md:2: pattern #1" in out.out
    assert "docs/contact.md:1: pattern #2" in out.out
    assert_nothing_leaked(out.out + out.err)


def test_matching_is_case_insensitive(tmp_path, capsys):
    repo = make_repo(tmp_path, {"a.txt": SECRET_NAME.upper() + "\n"})
    code, _ = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 1


def test_a_file_name_is_scanned_too_and_withheld(tmp_path, capsys):
    """A matching PATH is itself the detail, so it is reported by position."""
    repo = make_repo(tmp_path, {
        "a.md": "clean\n",
        "notes/zquimbleton@example.com.txt": "hello\nby " + SECRET_NAME + "\n",
    })
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 1
    assert "<tracked file #2>:path: pattern #2" in out.out
    assert "<tracked file #2>:2: pattern #1" in out.out
    assert_nothing_leaked(out.out + out.err)


def test_a_matching_path_in_history_is_withheld(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    base = _git(repo, "rev-parse", "HEAD")
    commit(repo, {"zorblax-quimbleton.txt": "x\n"}, "add")
    commit(repo, {"zorblax-quimbleton.txt": None}, "remove")
    code, out = run(repo, {"PRIVACY_DENYLIST": "zorblax"}, "--base", base, capsys=capsys)
    assert code == 1
    assert ":<path withheld>:path: pattern #1" in out.out
    assert_nothing_leaked(out.out + out.err)


def test_an_untracked_file_is_not_scanned(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    (repo / "scratch.txt").write_text(SECRET_NAME + "\n", encoding="utf-8")
    code, _ = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 0


def test_the_file_variable_is_read_when_the_list_variable_is_unset(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": SECRET_NAME + "\n"})
    listing = tmp_path / "denylist.txt"
    listing.write_text(DENYLIST, encoding="utf-8")
    code, _ = run(repo, {"PRIVACY_DENYLIST_FILE": str(listing)}, capsys=capsys)
    assert code == 1


def test_an_unreadable_denylist_file_is_an_error(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    code, out = run(repo, {"PRIVACY_DENYLIST_FILE": str(tmp_path / "absent.txt")},
                    capsys=capsys)
    assert code == 2
    assert "could not be read" in out.err


@pytest.mark.parametrize("env", [{}, {"PRIVACY_DENYLIST": ""}, {"PRIVACY_DENYLIST": "\n# only\n"}])
def test_no_denylist_fails_closed_in_ci(tmp_path, capsys, env):
    repo = make_repo(tmp_path, {"README.md": SECRET_NAME + "\n"})
    code, out = run(repo, dict(env, CI="true"), capsys=capsys)
    assert code == 2
    assert "Failing closed" in out.err


def test_no_denylist_warns_and_passes_locally(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": SECRET_NAME + "\n"})
    code, out = run(repo, {}, capsys=capsys)
    assert code == 0
    assert "WARNING" in out.err and "nothing was scanned" in out.err


def test_an_invalid_pattern_is_reported_by_index_only(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    code, out = run(repo, {"PRIVACY_DENYLIST": "ok\nzorblax(unclosed\n"}, capsys=capsys)
    assert code == 2
    assert "pattern #2 is not a valid regular expression" in out.err
    assert_nothing_leaked(out.out + out.err)


def test_history_catches_a_detail_added_then_deleted(tmp_path, capsys):
    """The tree is clean at HEAD; the history is not. Without --base the
    tree scan passes, which is exactly why --base exists."""
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    base = _git(repo, "rev-parse", "HEAD")
    commit(repo, {"docs/draft.md": "first line\nby " + SECRET_NAME + "\n"}, "add a draft")
    commit(repo, {"docs/draft.md": None}, "remove the draft")
    code, _ = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, capsys=capsys)
    assert code == 0
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, "--base", base, capsys=capsys)
    assert code == 1
    assert ":docs/draft.md:2: pattern #1" in out.out
    assert_nothing_leaked(out.out + out.err)


def test_history_scans_commit_messages(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    base = _git(repo, "rev-parse", "HEAD")
    commit(repo, {"README.md": "still clean\n"}, "thanks " + SECRET_MAIL)
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, "--base", base, capsys=capsys)
    assert code == 1
    assert ":message:1: pattern #2" in out.out
    assert_nothing_leaked(out.out + out.err)


def test_an_unknown_base_is_an_error_not_a_pass(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": "clean\n"})
    code, out = run(repo, {"PRIVACY_DENYLIST": DENYLIST}, "--base", "0" * 40, capsys=capsys)
    assert code == 2
    assert "failed" in out.err


def test_the_script_runs_as_ci_runs_it(tmp_path):
    """End to end through a real process and a real environment variable, the
    way the `privacy` job calls it."""
    repo = make_repo(tmp_path, {"README.md": "by " + SECRET_NAME + "\n"})
    env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVACY_DENYLIST")}
    env.update(PRIVACY_DENYLIST=DENYLIST, CI="true")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo)],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "README.md:1: pattern #1" in proc.stdout
    assert_nothing_leaked(proc.stdout + proc.stderr)
