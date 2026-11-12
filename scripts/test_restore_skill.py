#!/usr/bin/env python3
"""Tests for scripts/restore_skill.py.

Each test builds a throwaway git repo under tmp_path: one plugin with two
skills, both manifests, plugin_layout.json, shipped_names.json,
skill-impact.md and a README with the generated-table markers. The script and
the README generator it runs are copied in, so the script runs from its own
tree the way it does in the registry. Deterministic: `--date` is always passed;
no network.

Run: python3 -m pytest scripts/test_restore_skill.py -q
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import restore_skill as rs  # noqa: E402

PLUGIN = "demo-plugin"
GONE = "beta-skill"
KEPT = ["alpha-skill", "gamma-skill"]
DATE = "2026-11-12"

HEADER = "# skill-impact.md\n\nIntro.\n\n---\n"
OLD_ENTRY = "## 2026-01-01 — demo-plugin/alpha-skill — create\n\n- Eval: 3/3\n"


def run_git(repo, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, msg):
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", msg)
    return run_git(repo, "rev-parse", "HEAD")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def skill_md(name):
    return f"---\nname: {name}\ndescription: Does {name} things.\n---\n\nBody of {name}.\n"


def layout(skills):
    return json.dumps({"plugins": {PLUGIN: {"defaultEnabled": True, "skills": skills}}},
                      indent=2) + "\n"


def manifest(version):
    return json.dumps({"name": PLUGIN, "version": version, "description": "d"}, indent=2) + "\n"


def make_repo(tmp_path, ledger_skills=None):
    """main with the plugin at 1.2.3 and three skills (one is GONE)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    for name in (SCRIPTS / "restore_skill.py", SCRIPTS / "generate_readme_table.py",
                 SCRIPTS / "check_consistency.py"):
        (repo / "scripts").mkdir(exist_ok=True)
        shutil.copy(name, repo / "scripts" / name.name)
    for name in sorted([*KEPT, GONE]):
        sdir = repo / "plugins" / PLUGIN / "skills" / name
        write(sdir / "SKILL.md", skill_md(name))
        write(sdir / "PURPOSE.md", f"# Purpose of {name}\n")
    write(repo / "plugins" / PLUGIN / "skills" / GONE / "extra" / "notes.txt", "crlf\r\nbytes\x00\n")
    for rel in rs.MANIFESTS:
        write(repo / "plugins" / PLUGIN / rel, manifest("1.2.3"))
    write(repo / ".claude-plugin" / "marketplace.json", json.dumps(
        {"name": "m", "plugins": [{"name": PLUGIN, "source": f"./plugins/{PLUGIN}",
                                   "description": "d"}]}, indent=2) + "\n")
    write(repo / "scripts" / "plugin_layout.json", layout(["alpha-skill", GONE, "gamma-skill"]))
    ledger = ledger_skills if ledger_skills is not None else [f"{PLUGIN}/{s}" for s in sorted([*KEPT, GONE])]
    write(repo / "scripts" / "shipped_names.json", json.dumps(
        {"marketplace": ["m"], "plugins": [PLUGIN], "skills": ledger}, indent=2) + "\n")
    write(repo / "docs" / "skill-impact.md", HEADER + "\n" + OLD_ENTRY)
    write(repo / "README.md", "# R\n\n<!-- BEGIN GENERATED PLUGIN TABLE -->\n<!-- END GENERATED PLUGIN TABLE -->\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "base")
    run_readme(repo)
    commit(repo, "readme table")
    return repo


def run_readme(repo):
    subprocess.run([sys.executable, str(repo / "scripts" / "generate_readme_table.py")],
                   cwd=repo, check=True, capture_output=True)


def retire(repo):
    """The removal commit, the way retire_skill.py makes it: the directory
    gone, MAJOR bumped, layout and README updated, a `remove` entry."""
    run_git(repo, "rm", "-r", "-q", "--", f"plugins/{PLUGIN}/skills/{GONE}")
    for rel in rs.MANIFESTS:
        write(repo / "plugins" / PLUGIN / rel, manifest("2.0.0"))
    write(repo / "scripts" / "plugin_layout.json", layout(KEPT))
    text = (repo / "docs" / "skill-impact.md").read_text(encoding="utf-8")
    write(repo / "docs" / "skill-impact.md", rs.insert_entry(
        text, f"## 2026-11-01 — {PLUGIN}/{GONE} — remove\n\n- Eval: n=10\n"))
    run_readme(repo)
    return commit(repo, "Remove beta-skill")


def tree_bytes(directory):
    return {str(p.relative_to(directory)): p.read_bytes()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def restore(repo, *extra):
    proc = subprocess.run([sys.executable, str(repo / "scripts" / "restore_skill.py"),
                           GONE, "--date", DATE, *extra],
                          cwd=repo, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def removed(tmp_path):
    repo = make_repo(tmp_path)
    original = tree_bytes(repo / "plugins" / PLUGIN / "skills" / GONE)
    sha = retire(repo)
    return repo, sha, original


def test_restore_brings_everything_back(removed):
    repo, sha, original = removed
    code, out = restore(repo)
    assert code == 0, out
    assert tree_bytes(repo / "plugins" / PLUGIN / "skills" / GONE) == original
    layout_doc = json.loads((repo / "scripts" / "plugin_layout.json").read_text(encoding="utf-8"))
    assert layout_doc["plugins"][PLUGIN]["skills"] == ["alpha-skill", GONE, "gamma-skill"]
    assert (repo / "scripts" / "plugin_layout.json").read_text(encoding="utf-8") == \
        layout(["alpha-skill", GONE, "gamma-skill"])
    for rel in rs.MANIFESTS:
        assert json.loads((repo / "plugins" / PLUGIN / rel).read_text(encoding="utf-8"))["version"] == "2.1.0"
    impact = (repo / "docs" / "skill-impact.md").read_text(encoding="utf-8")
    first = impact.split("---\n", 1)[1].lstrip().splitlines()[0]
    assert first == f"## {DATE} — {PLUGIN}/{GONE} — restore"
    assert sha in impact and "2.0.0 -> 2.1.0" in impact
    assert OLD_ENTRY in impact
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert f"`/{PLUGIN}:{GONE}`" in readme
    assert subprocess.run([sys.executable, "scripts/generate_readme_table.py", "--check"],
                          cwd=repo).returncode == 0
    # staged, not committed; the ledger is untouched
    assert run_git(repo, "diff", "--name-only") == ""
    assert "shipped_names.json" not in run_git(repo, "diff", "--cached", "--name-only")


def test_restore_with_explicit_commit(removed):
    repo, sha, original = removed
    code, out = restore(repo, "--commit", sha[:12])
    assert code == 0, out
    assert tree_bytes(repo / "plugins" / PLUGIN / "skills" / GONE) == original


def test_unlocked_plugin_says_no_repin(removed):
    code, out = restore(removed[0])
    assert code == 0 and "no re-pin needed" in out


def test_locked_plugin_prints_the_follow_up(removed):
    repo, _, _ = removed
    write(repo / "skills.lock", json.dumps({"bundles": [PLUGIN], "skills": {}}))
    commit(repo, "lock")
    code, out = restore(repo)
    assert code == 0, out
    assert "generate_skills_lock.py --repin" in out and "--check-current" in out


def test_layout_first_position(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "scripts" / "plugin_layout.json", layout([GONE, "alpha-skill", "gamma-skill"]))
    commit(repo, "layout order")
    original = tree_bytes(repo / "plugins" / PLUGIN / "skills" / GONE)
    retire(repo)
    code, out = restore(repo)
    assert code == 0, out
    doc = json.loads((repo / "scripts" / "plugin_layout.json").read_text(encoding="utf-8"))
    assert doc["plugins"][PLUGIN]["skills"] == [GONE, "alpha-skill", "gamma-skill"]
    assert tree_bytes(repo / "plugins" / PLUGIN / "skills" / GONE) == original


def test_refuses_without_a_removal_commit(tmp_path):
    repo = make_repo(tmp_path)  # every shipped skill is still present
    proc = subprocess.run([sys.executable, str(repo / "scripts" / "restore_skill.py"),
                           KEPT[0], "--date", DATE], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 2 and "no commit" in proc.stderr
    assert run_git(repo, "status", "--porcelain") == ""


def test_refuses_when_the_directory_exists(removed):
    repo, sha, _ = removed
    write(repo / "plugins" / PLUGIN / "skills" / GONE / "SKILL.md", skill_md(GONE))
    commit(repo, "someone re-added it")
    code, out = restore(repo)
    assert code == 2 and "already exists" in out


def test_refuses_a_dirty_tree(removed):
    repo, _, _ = removed
    write(repo / "stray.txt", "x")
    code, out = restore(repo)
    assert code == 2 and "not clean" in out


def test_refuses_an_unshipped_name(tmp_path):
    repo = make_repo(tmp_path, ledger_skills=[f"{PLUGIN}/{s}" for s in KEPT])
    retire(repo)
    code, out = restore(repo)
    assert code == 2 and "shipped_names.json" in out
    assert run_git(repo, "status", "--porcelain") == ""


def test_refused_restore_writes_nothing(removed):
    repo, _, _ = removed
    write(repo / "plugins" / PLUGIN / "skills" / GONE / "SKILL.md", "x")
    code, _ = restore(repo)
    assert code == 2
    assert (repo / "docs" / "skill-impact.md").read_text(encoding="utf-8").count("restore") == 0


def test_bump_minor():
    assert rs.bump_minor("1.2.3") == "1.3.0"
    assert rs.bump_minor("2.0.0") == "2.1.0"
    with pytest.raises(rs.Refusal):
        rs.bump_minor("1.2")


def test_layout_position():
    assert rs.layout_position(["a", "b", "c"], ["a", "c"], "b") == 1
    assert rs.layout_position(["b", "c"], ["a", "c"], "b") == 0
    assert rs.layout_position(["a", "x", "b"], ["a"], "b") == 1
    assert rs.layout_position([], ["a", "c"], "b") == 1


def test_restored_tree_passes_the_skill_gates(removed):
    repo, _, _ = removed
    run_git(repo, "branch", "base")
    run_git(repo, "checkout", "-q", "-b", "pr")
    code, out = restore(repo)
    assert code == 0, out
    commit(repo, "Restore beta-skill")
    proc = subprocess.run([sys.executable, str(SCRIPTS / "check_skill_gates.py"), "--base", "base",
                           "--head", "pr", "--census", "none"],
                          cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 created" in proc.stdout
