#!/usr/bin/env python3
"""restore_skill.py — put a removed skill back, exactly, in one reviewable change.

Why this exists
----------------
skills-evals' scripts/retire_skill.py removes a skill in one auto-merging pull
request (ADR 0021): it deletes plugins/<plugin>/skills/<skill>/, raises the
plugin's MAJOR version, adds a `remove` entry to docs/skill-impact.md, drops the
skill from scripts/plugin_layout.json and the README table, and re-pins
skills.lock when the lock covers the plugin. ADR 0025 lets the same lane remove
a skill nobody uses, so an unused-skill removal that turns out wrong needs a
cheap, exact way back. This is it.

What it does
-------------
Run at the repository root, on a fresh branch from main, with a clean tree:

  python3 scripts/restore_skill.py <skill> [--commit <removal-sha>] [--date YYYY-MM-DD]

1. Finds the removal commit: the latest commit in HEAD's history that deleted
   plugins/<plugin>/skills/<skill>/SKILL.md, or `--commit`.
2. Restores the skill directory from `<removal>^`, byte for byte
   (`git restore --source`), and its entry in scripts/plugin_layout.json under
   the same plugin at its original position.
3. Regenerates the README table with scripts/generate_readme_table.py.
4. Raises the plugin's MINOR version (x.y.z -> x.(y+1).0) in both manifests.
5. Prepends a `restore` entry to docs/skill-impact.md naming the removal commit.

It leaves the changes in the working tree and the index, uncommitted, so the
caller reviews and commits them (the commit that follows is the content commit
of a pull request). It does not touch scripts/shipped_names.json: that ledger
is append-only and the restored name is already in it, which is also why a name
absent from it is refused (a restore reuses a shipped basename, never a new
one).

skills.lock
------------
When skills.lock locks the plugin, this script does NOT re-pin it. The lock must
pin a commit that already has the restored skill, so the re-pin needs the
content commit to exist first, and writing a commit on the caller's behalf is
not this script's job. It prints the exact follow-up instead: commit, run
`python3 scripts/generate_skills_lock.py --repin`, run its `--check-current`,
commit the lock.

The one-way-door check (scripts/check_one_way_doors.py) treats a shipped name
that arrives again as a reused name, so the pull request body needs an
`Adversarial-review: pass <link>` line.

Exit status: 0 restored, 2 refused (the message says why).
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date as _date_cls
from pathlib import Path
from typing import List, Optional, Tuple

IMPACT_FILE = "docs/skill-impact.md"
LAYOUT_FILE = "scripts/plugin_layout.json"
LEDGER_FILE = "scripts/shipped_names.json"
LOCK_FILE = "skills.lock"
README_GENERATOR = "scripts/generate_readme_table.py"
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")

_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_REMOVED_PATH = re.compile(r"^plugins/([^/]+)/skills/([^/]+)/SKILL\.md$")


class Refusal(Exception):
    """The restore cannot proceed; the message says why (exit 2)."""


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=off",
                           "-C", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise Refusal(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def find_removal(root: Path, skill: str, commit: Optional[str]) -> Tuple[str, str]:
    """(removal commit sha, plugin) for the latest commit in HEAD's history that
    deleted plugins/*/skills/<skill>/SKILL.md, or for `commit`."""
    if commit is not None:
        if not _SHA.fullmatch(commit):
            raise Refusal(f"--commit {commit!r} is not a commit sha")
        sha = git(root, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}",
                  check=False).strip()
        if not sha:
            raise Refusal(f"--commit {commit} is not a commit in this repository")
        out = git(root, "diff-tree", "-r", "--no-commit-id", "--name-only",
                  "--diff-filter=D", "-m", "--first-parent", sha)
        plugins = sorted({m.group(1) for m in map(_REMOVED_PATH.match, out.splitlines())
                          if m and m.group(2) == skill})
        if len(plugins) != 1:
            raise Refusal(f"commit {sha} does not delete plugins/<plugin>/skills/{skill}/SKILL.md")
        return sha, plugins[0]
    out = git(root, "log", "--diff-filter=D", "--name-only", "--format=%x00%H", "HEAD",
              "--", f":(glob)plugins/*/skills/{skill}/SKILL.md")
    for block in out.split("\0"):
        lines = [ln for ln in block.splitlines() if ln]
        if not lines:
            continue
        for path in lines[1:]:
            m = _REMOVED_PATH.match(path)
            if m and m.group(2) == skill:
                return lines[0], m.group(1)
    raise Refusal(f"no commit in HEAD's history deleted plugins/*/skills/{skill}/SKILL.md; "
                  "pass --commit <removal-sha> if it is on another branch")


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------

def bump_minor(version: str) -> str:
    """x.y.z -> x.(y+1).0."""
    m = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if not m:
        raise Refusal("a plugin version is not MAJOR.MINOR.PATCH")
    return f"{m.group(1)}.{int(m.group(2)) + 1}.0"


def shipped_basenames(ledger_text: str) -> set:
    doc = json.loads(ledger_text)
    return {name.rsplit("/", 1)[-1] for name in doc.get("skills", []) if isinstance(name, str)}


def layout_position(old: List[str], current: List[str], skill: str) -> int:
    """Index in `current` that puts `skill` where it was in `old` (the list
    before its removal): right after the nearest earlier neighbor of `old`
    that `current` still lists, else first. When `old` does not list the skill
    (the layout file is newer than the skill), the sorted position."""
    if skill in old:
        for earlier in reversed(old[:old.index(skill)]):
            if earlier in current:
                return current.index(earlier) + 1
        return 0
    return sum(1 for s in current if s < skill)


def layout_with(current_text: str, old_text: Optional[str], plugin: str, skill: str) -> str:
    """plugin_layout.json's text with `skill` back under `plugin`, in the file's
    canonical form (json.dumps, indent 2, one trailing newline)."""
    doc = json.loads(current_text)
    entry = (doc.get("plugins") or {}).get(plugin)
    skills = entry.get("skills") if isinstance(entry, dict) else None
    if not isinstance(skills, list):
        raise Refusal(f"{LAYOUT_FILE} has no plugin {plugin} with a skills list")
    if skill in skills:
        raise Refusal(f"{LAYOUT_FILE} already lists {skill} under {plugin}")
    old: List[str] = []
    if old_text:
        old_entry = (json.loads(old_text).get("plugins") or {}).get(plugin)
        if isinstance(old_entry, dict) and isinstance(old_entry.get("skills"), list):
            old = old_entry["skills"]
    skills.insert(layout_position(old, skills, skill), skill)
    return json.dumps(doc, indent=2) + "\n"


def bump_manifest(text: str, rel: str) -> Tuple[str, str, str]:
    """(new text, old version, new version) with the version written once as
    `"version": "<v>"` replaced."""
    try:
        old = json.loads(text)["version"]
    except (ValueError, KeyError, TypeError):
        raise Refusal(f"cannot read the version in {rel}") from None
    new = bump_minor(old)
    needle = f'"version": "{old}"'
    if text.count(needle) != 1:
        raise Refusal(f"{rel}: its version is not written once as {needle}")
    return text.replace(needle, f'"version": "{new}"'), old, new


def impact_entry(date: str, plugin: str, skill: str, removal: str, versions: Tuple[str, str]) -> str:
    return (f"## {date} — {plugin}/{skill} — restore\n\n"
            f"- Motivation: the skill was removed in commit `{removal}` and is wanted back.\n"
            f"- Change: `plugins/{plugin}/skills/{skill}/` restored unchanged from `{removal}^`, "
            f"the skill back in `{LAYOUT_FILE}` and the README table, bundle `{plugin}` "
            f"{versions[0]} -> {versions[1]} (a restore adds a skill, a minor change), by "
            "`scripts/restore_skill.py`.\n"
            f"- Eval: none run; restored byte for byte from `{removal}^`, the content that last "
            "graduated, so its eval history is in the earlier entries.\n"
            "- Outcome: pending merge.\n")


def insert_entry(text: str, entry: str) -> str:
    """`entry` newest first: right after the header's `---` rule."""
    lines = text.split("\n")
    try:
        rule = lines.index("---")
    except ValueError:
        raise Refusal(f"{IMPACT_FILE} has no `---` rule to insert after") from None
    head = "\n".join(lines[:rule + 1]).rstrip("\n")
    rest = "\n".join(lines[rule + 1:]).lstrip("\n")
    return f"{head}\n\n{entry}\n{rest}"


def lock_covers(lock_text: Optional[str], plugin: str) -> bool:
    if not lock_text:
        return False
    doc = json.loads(lock_text)
    skills = doc.get("skills") or {}
    return plugin in (doc.get("bundles") or []) or any(
        isinstance(k, str) and k.startswith(f"{plugin}/") for k in skills)


def follow_up(plugin: str, skill: str) -> str:
    return (f"skills.lock locks {plugin}, so it must pin a commit that has the restored skill. "
            "Next:\n"
            f"  git add -A && git commit -m 'Restore {plugin}/{skill}'\n"
            "  python3 scripts/generate_skills_lock.py --repin\n"
            "  python3 scripts/generate_skills_lock.py --check-current\n"
            f"  git add {LOCK_FILE} && git commit -m 'Re-pin skills.lock after restoring "
            f"{plugin}/{skill}'")


# ---------------------------------------------------------------------------
# the restore
# ---------------------------------------------------------------------------

def restore(root: Path, skill: str, commit: Optional[str], date: str) -> List[str]:
    """Do the restore in `root`; returns the lines to print. Refusal on any
    precondition, before anything is written."""
    if not _SKILL_NAME.fullmatch(skill):
        raise Refusal(f"{skill!r} is not a skill directory name")
    if not _DATE.fullmatch(date):
        raise Refusal(f"--date {date!r} is not YYYY-MM-DD")
    if git(root, "status", "--porcelain").strip():
        raise Refusal("the working tree is not clean; commit or stash first")
    try:
        ledger = (root / LEDGER_FILE).read_text(encoding="utf-8")
        if skill not in shipped_basenames(ledger):
            raise Refusal(f"{skill} is not in {LEDGER_FILE}; a restore reuses a shipped name")
    except (OSError, ValueError):
        raise Refusal(f"cannot read {LEDGER_FILE}") from None
    sha, plugin = find_removal(root, skill, commit)
    rel_dir = f"plugins/{plugin}/skills/{skill}"
    if (root / rel_dir).exists():
        raise Refusal(f"{rel_dir} already exists")
    before = f"{sha}^"
    if git(root, "rev-parse", "--verify", "-q", before, check=False).strip() == "":
        raise Refusal(f"commit {sha} has no parent to restore from")

    try:
        layout = layout_with((root / LAYOUT_FILE).read_text(encoding="utf-8"),
                             _show(root, before, LAYOUT_FILE), plugin, skill)
        manifests = {}
        for name in MANIFESTS:
            rel = f"plugins/{plugin}/{name}"
            manifests[rel] = bump_manifest((root / rel).read_text(encoding="utf-8"), rel)
        if len({old for _, old, _ in manifests.values()}) != 1:
            raise Refusal(f"{plugin}'s two manifests disagree on its version")
        impact_text = (root / IMPACT_FILE).read_text(encoding="utf-8")
        versions = next((old, new) for _, old, new in manifests.values())
        entry = impact_entry(date, plugin, skill, sha, versions)
        new_impact = insert_entry(impact_text, entry)
        lock_text = (root / LOCK_FILE).read_text(encoding="utf-8") \
            if (root / LOCK_FILE).exists() else None
        locked = lock_covers(lock_text, plugin)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Refusal(f"cannot read a file the restore edits: {type(exc).__name__}") from None

    git(root, "restore", f"--source={before}", "--staged", "--worktree", "--", rel_dir)
    for rel, (text, _, _) in manifests.items():
        (root / rel).write_text(text, encoding="utf-8", newline="")
    (root / LAYOUT_FILE).write_text(layout, encoding="utf-8", newline="")
    (root / IMPACT_FILE).write_text(new_impact, encoding="utf-8", newline="")
    gen = subprocess.run([sys.executable, str(root / README_GENERATOR)], cwd=str(root),
                         capture_output=True, text=True)
    if gen.returncode != 0:
        raise Refusal(f"{README_GENERATOR} exited {gen.returncode}: {gen.stderr.strip()}")
    git(root, "add", "--", rel_dir, IMPACT_FILE, LAYOUT_FILE, "README.md", *manifests)

    out = [f"restored {plugin}/{skill} from {sha}^; plugin {plugin} {versions[0]} -> {versions[1]}",
           "changes are staged, not committed"]
    out.append(follow_up(plugin, skill) if locked
               else "skills.lock does not lock this plugin; no re-pin needed")
    return out


def _show(root: Path, rev: str, path: str) -> Optional[str]:
    proc = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.stdout if proc.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("skill")
    ap.add_argument("--commit", help="the removal commit (default: the latest one in HEAD's history)")
    ap.add_argument("--date", help="YYYY-MM-DD for the impact entry (default: today)")
    args = ap.parse_args(argv)
    date = args.date or _date_cls.today().isoformat()
    try:
        lines = restore(Path.cwd(), args.skill, args.commit, date)
    except Refusal as exc:
        print(f"restore_skill: refused: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
