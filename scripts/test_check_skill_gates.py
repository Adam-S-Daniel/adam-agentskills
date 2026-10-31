#!/usr/bin/env python3
"""Tests for scripts/check_skill_gates.py.

Hermetic and deterministic: each scenario builds a throwaway git repo under
pytest's `tmp_path` with a repo-local identity (the approach
scripts/test_check_plugin_versions.py uses), and the census is a JSON file in
tmp_path, never the network. Every scenario runs the script as a subprocess,
so the exit code asserted is the one CI reads.

The loop-format tests at the bottom copy the impact entries skills-evals'
scripts/improve_gate.py, scripts/draft_new_skill.py and scripts/retire_skill.py
write, with invented values: the evals loop's own pull requests must pass this
gate as written.

Run: python3 -m pytest scripts/test_check_skill_gates.py -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPTS_DIR / "check_skill_gates.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import check_skill_gates as csg  # noqa: E402

_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")

HEADER = """# skill-impact.md — fixture

## Entry format

```
## YYYY-MM-DD — <plugin>/<skill> — <create|edit|rename|remove|rejected>
- Motivation: one line
- Eval: 21/21
```

Rules:

- Append-only.

---
"""

PLUGIN = "adam-coding-anywhere"
LOCAL = "adam-non-coding-local"
SKILL = "widget-polisher"
OTHER = "gadget-sorter"

OLD_ENTRY = f"""## 2026-01-01 — {PLUGIN}/{SKILL} — create

- Motivation: invented.
- Change: new skill.
- Eval: routine run `20260101T000000Z-aaaaaa`, with_skill objective 3/3, without_skill 1/3.
- Outcome: merged 2026-01-02.
"""

OTHER_OLD = f"""## 2026-01-01 — {PLUGIN}/{OTHER} — create

- Motivation: invented.
- Change: new skill.
- Eval: routine run `20260101T000000Z-bbbbbb`, with_skill objective 2/2, without_skill 0/2.
- Outcome: merged 2026-01-02.
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *_GIT_VERBATIM, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def skill_dir(repo: Path, plugin: str, skill: str) -> Path:
    return repo / "plugins" / plugin / "skills" / skill


def impact(repo: Path, *entries: str) -> None:
    _write(repo / "docs" / "skill-impact.md", HEADER + "\n" + "\n".join(entries))


def commit(repo: Path, msg: str = "c") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    """main with two shipped skills (SKILL.md + PURPOSE.md each) and their
    create entries; a `pr` branch checked out from it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    for skill in (SKILL, OTHER):
        _write(skill_dir(repo, PLUGIN, skill) / "SKILL.md", f"# {skill}\n\nv1\n")
        _write(skill_dir(repo, PLUGIN, skill) / "PURPOSE.md", "# Purpose\n")
    impact(repo, OLD_ENTRY, OTHER_OLD)
    commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    return repo


def census_file(tmp_path: Path, status: str, skill: str = SKILL) -> Path:
    path = tmp_path / "census.json"
    path.write_text(json.dumps({"schema": 1, "registries": [
        {"name": "adam-agentskills", "skills": [
            {"skill": skill, "bundle": PLUGIN, "status": status, "fixtures": 1}]}]}),
        encoding="utf-8")
    return path


def gate(repo: Path, census="none", base="main", head="pr", env=None):
    full_env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    full_env.update(env or {})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, "--head", head, "--census", str(census)],
        cwd=repo, capture_output=True, text=True, env=full_env)
    return proc.returncode, proc.stdout + proc.stderr


def edit_skill(repo: Path, plugin: str = PLUGIN, skill: str = SKILL) -> None:
    _write(skill_dir(repo, plugin, skill) / "SKILL.md", f"# {skill}\n\nv2\n")


def entry(kind: str, eval_text: str, plugin: str = PLUGIN, skill: str = SKILL,
          date: str = "2026-10-30") -> str:
    return (f"## {date} — {plugin}/{skill} — {kind}\n\n- Motivation: invented.\n"
            f"- Change: invented.\n- Eval: {eval_text}\n- Outcome: pending\n")


def touch(repo: Path, eval_text: str, kind: str = "edit", skill_for_entry: str = SKILL) -> None:
    edit_skill(repo)
    impact(repo, entry(kind, eval_text, skill=skill_for_entry), OLD_ENTRY, OTHER_OLD)
    commit(repo)


# ---------------------------------------------------------------------------
# basics
# ---------------------------------------------------------------------------

def test_no_skill_change_passes(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "README.md", "hi\n")
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out
    assert "no skill created, edited or removed" in out


def test_non_skill_file_in_a_skill_dir_is_not_a_touch(tmp_path):
    repo = make_repo(tmp_path)
    _write(skill_dir(repo, PLUGIN, SKILL) / "references" / "x.md", "x\n")
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out


def test_unresolvable_merge_base_exits_2(tmp_path):
    repo = make_repo(tmp_path)
    code, out = gate(repo, base="0" * 40)
    assert code == 2, out
    assert "merge-base" in out


# ---------------------------------------------------------------------------
# touch gate
# ---------------------------------------------------------------------------

def test_touch_without_entry_fails(tmp_path):
    repo = make_repo(tmp_path)
    edit_skill(repo)
    commit(repo)
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out
    assert f"skill-gates: {PLUGIN}/{SKILL}: SKILL.md changed with no new entry" in out


def test_touch_without_purpose_fails(tmp_path):
    repo = make_repo(tmp_path)
    (skill_dir(repo, PLUGIN, SKILL) / "PURPOSE.md").unlink()
    commit(repo)
    _git(repo, "branch", "-f", "main", "HEAD")
    touch(repo, "21/21 objective checks")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out
    assert "no PURPOSE.md" in out


def test_covered_touch_not_run_fails(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "not run; wording only")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out
    assert "census lists the skill as covered" in out


def test_covered_touch_with_counts_passes(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "exit 0, 21/21 objective checks")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 0, out


def test_covered_touch_with_run_id_passes(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "routine run `20261030T101010Z-0a1b2c`, no regression")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 0, out


def test_covered_touch_counts_but_starting_none_fails(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "`none` — the old 3/3 run predates this edit")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out


def test_skipped_touch_exempt_passes(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "exempt (DESIGN.md non-coverage table)")
    code, out = gate(repo, census_file(tmp_path, "skipped"))
    assert code == 0, out


def test_skipped_touch_without_evidence_fails(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "not run")
    code, out = gate(repo, census_file(tmp_path, "skipped"))
    assert code == 1, out
    assert "exempt" in out


def test_gap_touch_with_first_fixture_pr_passes(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "first fixture in [skills-evals#9](https://github.com/Adam-S-Daniel/skills-evals/pull/9)")
    code, out = gate(repo, census_file(tmp_path, "gap"))
    assert code == 0, out


def test_gap_touch_with_exempt_only_fails(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "exempt")
    code, out = gate(repo, census_file(tmp_path, "gap"))
    assert code == 1, out
    assert "first fixture" in out


def test_unreadable_census_warns_and_passes(tmp_path):
    for name, content in (("missing.json", None), ("bad.json", "{not json"),
                          ("schema.json", json.dumps({"schema": 2, "registries": []})),
                          ("noreg.json", json.dumps({"schema": 1, "registries": []}))):
        repo_root = tmp_path / name
        repo_root.mkdir()
        repo = make_repo(repo_root)
        touch(repo, "not run; wording only")
        path = repo_root / name
        if content is not None:
            path.write_text(content, encoding="utf-8")
        code, out = gate(repo, path)
        assert code == 0, (name, out)
        assert "skill-gates (warning): census: could not read the census" in out, out
        assert "missing data never blocks" in out, out


def test_skill_absent_from_census_warns(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "not run")
    code, out = gate(repo, census_file(tmp_path, "covered", skill="someone-else"))
    assert code == 0, out
    assert "does not list this skill" in out


def test_warning_is_an_annotation_under_actions(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "not run")
    code, out = gate(repo, env={"GITHUB_ACTIONS": "true"})
    assert code == 0, out
    assert "::warning title=skill-gates::" in out


def test_entry_for_a_different_skill_does_not_satisfy(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "21/21", skill_for_entry=OTHER)
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out
    assert f"{PLUGIN}/{SKILL}: SKILL.md changed with no new entry" in out


def test_rejected_entry_does_not_satisfy_a_touch(tmp_path):
    repo = make_repo(tmp_path)
    touch(repo, "21/21", kind="rejected")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out


def test_an_old_entry_does_not_satisfy_a_new_touch(tmp_path):
    repo = make_repo(tmp_path)
    edit_skill(repo)  # OLD_ENTRY (a create with counts) is already at the merge-base
    commit(repo)
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out


def test_fenced_entry_example_is_not_an_entry(tmp_path):
    repo = make_repo(tmp_path)
    edit_skill(repo)
    fenced = (f"```\n## 2026-10-30 — {PLUGIN}/{SKILL} — edit\n- Eval: 21/21\n```\n")
    impact(repo, fenced, OLD_ENTRY, OTHER_OLD)
    commit(repo)
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 1, out
    assert "no new entry" in out
    assert csg.parse_entries(HEADER + fenced) == []


def test_wrapped_eval_bullet_is_one_text():
    src = HEADER + (f"\n## 2026-10-30 — {PLUGIN}/{SKILL} — edit\n\n- Motivation: x\n"
                    "- Eval: the routine scored\n  21/21 on train\n- Outcome: y\n")
    [e] = csg.parse_entries(src)
    assert e["eval"] == "the routine scored\n21/21 on train"
    assert e["kind"] == "edit"


def test_sibling_edit_on_main_is_not_attributed_to_the_pr(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "notes.txt", "pr work\n")
    commit(repo, "pr")
    # main advances with its own (ungated) skill edit after the fork.
    _git(repo, "checkout", "-q", "main")
    edit_skill(repo, skill=OTHER)
    commit(repo, "main sibling")
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 0, out
    assert "no skill created, edited or removed" in out


# ---------------------------------------------------------------------------
# graduation gate
# ---------------------------------------------------------------------------

DRAFT_EVAL = ("draft-loop measurement, skills-evals routine run `20261031T120000Z-abc123`, "
              "3 trials per arm over 2 new fixtures: with_skill objective 14/18, without_skill "
              "9/18. Not badge input; the fixtures land in a companion skills-evals PR.")
ROUTINE_EVAL = ("routine run `20261026T211234Z-1b7681`, 3 trials per arm, subscription billing. "
                "`with_skill` objective 6/7 every trial, `without_skill` 5/7; judge skipped.")


def create(repo: Path, eval_text: str, plugin: str = PLUGIN, purpose: bool = True,
           skill: str = "new-gizmo") -> None:
    _write(skill_dir(repo, plugin, skill) / "SKILL.md", "# new\n")
    if purpose:
        _write(skill_dir(repo, plugin, skill) / "PURPOSE.md", "# Purpose\n")
    impact(repo, entry("create", eval_text, plugin=plugin, skill=skill), OLD_ENTRY, OTHER_OLD)
    commit(repo)


def test_create_without_purpose_fails(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, DRAFT_EVAL, purpose=False)
    code, out = gate(repo)
    assert code == 1, out
    assert "a new skill has no PURPOSE.md" in out


def test_create_green_passes_draft_format(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, DRAFT_EVAL)
    code, out = gate(repo)
    assert code == 0, out


def test_create_green_passes_routine_format(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, ROUTINE_EVAL)
    code, out = gate(repo)
    assert code == 0, out


def test_create_with_skill_below_without_fails(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, "routine run `20261031T120000Z-abc123`: with_skill objective 5/9, without_skill 6/9")
    code, out = gate(repo)
    assert code == 1, out
    assert "green with_skill arm" in out


def test_create_with_skill_zero_fails(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, "routine run `20261031T120000Z-abc123`: with_skill objective 0/9, without_skill 0/9")
    code, out = gate(repo)
    assert code == 1, out


def test_create_green_without_run_id_fails(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, "with_skill objective 9/9, without_skill 1/9")
    code, out = gate(repo)
    assert code == 1, out


def test_create_without_entry_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(skill_dir(repo, PLUGIN, "new-gizmo") / "SKILL.md", "# new\n")
    _write(skill_dir(repo, PLUGIN, "new-gizmo") / "PURPOSE.md", "# p\n")
    commit(repo)
    code, out = gate(repo)
    assert code == 1, out
    assert "no new `create` entry" in out


def test_create_exempt_in_machine_bound_plugin_warns(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, "exempt — drives a local desktop app", plugin=LOCAL)
    code, out = gate(repo)
    assert code == 0, out
    assert "evals/non-coverage.yml" in out


def test_create_exempt_in_cloud_plugin_fails(tmp_path):
    repo = make_repo(tmp_path)
    create(repo, "exempt — no fixture yet", plugin=PLUGIN)
    code, out = gate(repo)
    assert code == 1, out


# ---------------------------------------------------------------------------
# removal, moves, append-only
# ---------------------------------------------------------------------------

def remove_skill(repo: Path, skill: str = SKILL) -> None:
    for f in (skill_dir(repo, PLUGIN, skill)).iterdir():
        f.unlink()


def test_remove_with_entry_passes(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    impact(repo, entry("remove", "n=12 pooled arm runs"), OLD_ENTRY, OTHER_OLD)
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out


def test_remove_without_entry_fails(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    code, out = gate(repo)
    assert code == 1, out
    assert "no new `remove` entry" in out


def test_move_between_plugins_needs_an_entry(tmp_path):
    repo = make_repo(tmp_path)
    src = skill_dir(repo, PLUGIN, SKILL)
    for name in ("SKILL.md", "PURPOSE.md"):
        _write(skill_dir(repo, LOCAL, SKILL) / name, (src / name).read_text(encoding="utf-8"))
    remove_skill(repo)
    commit(repo)
    code, out = gate(repo)
    assert code == 1, out
    impact(repo, entry("rename", "none — content unchanged", plugin=LOCAL), OLD_ENTRY, OTHER_OLD)
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out


def test_rewording_an_old_entry_fails(tmp_path):
    repo = make_repo(tmp_path)
    impact(repo, OLD_ENTRY.replace("with_skill objective 3/3", "with_skill objective 2/3"),
           OTHER_OLD)
    commit(repo)
    code, out = gate(repo)
    assert code == 1, out
    assert f'"2026-01-01 — {PLUGIN}/{SKILL} — create"' in out
    assert "add a correcting entry instead of editing an old one" in out.lower()


def test_deleting_an_old_entry_fails(tmp_path):
    repo = make_repo(tmp_path)
    impact(repo, OLD_ENTRY)
    commit(repo)
    code, out = gate(repo)
    assert code == 1, out


def test_reordering_entries_passes(tmp_path):
    repo = make_repo(tmp_path)
    impact(repo, OTHER_OLD, OLD_ENTRY)
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out


# ---------------------------------------------------------------------------
# the evals loop's own entry formats (copied from skills-evals, invented values)
# ---------------------------------------------------------------------------

def improve_gate_entry(plugin, skill):
    """skills-evals scripts/improve_gate.py impact_entry(), mode auto."""
    return (f"## 2026-10-30 — {plugin}/{skill} — edit\n\n"
            "- Motivation: skills-evals improvement run `20261030T010203Z-a1b2c3`: train "
            "checks failed, and `scripts/propose_skill_edit.py`'s acceptance "
            "rule accepted its proposed edit "
            "([skills-evals#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71)).\n"
            "- Change: SKILL.md edited by the improvement loop and a `PURPOSE.md` stub added; bundle "
            f"`{plugin}` 1.0.0 -> 1.0.1, by skills-evals' "
            "`routine-improve-gate.yml`.\n"
            "- Eval: objective checks passed, baseline -> candidate, "
            "3 trial(s) per fixture in the eval routine (a "
            "local exhibit, not badge input): train alpha-case 19/21 -> 21/21; "
            "validation beta-case 21/21 -> 21/21.\n"
            "- Outcome: auto-merges once required checks pass, with no human review "
            "(ADR 0021, Adam's 2026-10-26 decision).\n")


def draft_entry(plugin, skill):
    """skills-evals scripts/draft_new_skill.py impact_entry()."""
    return "\n".join([
        f"## 2026-10-31 — {plugin}/{skill} — create", "",
        "- Motivation: a `new-skill` lesson mined from agent sessions "
        "(redacted, 4 pieces of evidence) that the "
        "draft loop's triage could not check mechanically.",
        f"- Change: new skill `plugins/{plugin}/skills/{skill}/`, "
        "drafted by the skills-evals draft loop with skill-creator at "
        f"`0123456789ab`; bundle `{plugin}` 1.0.0 -> 1.0.1 (PR pending).",
        "- Eval: draft-loop measurement, skills-evals routine run `20261031T120000Z-abc123`, "
        "3 trials per arm over 2 new "
        "fixtures: with_skill objective 14/18, without_skill "
        "9/18. Not badge input; the fixtures land in a "
        "companion skills-evals PR.",
        "- Outcome: pending review.", ""])


def retire_entry(plugin, skill):
    """skills-evals scripts/retire_skill.py impact_entry()."""
    return (f"## 2026-10-31 — {plugin}/{skill} — remove\n\n"
            "- Motivation: the retirement verdict is `retire` "
            "([skills-evals#70](https://github.com/Adam-S-Daniel/skills-evals/issues/70)): every cell "
            "(1 fixture(s) x 2 roster model(s)) "
            "shows no benefit at two fixed looks.\n"
            f"- Change: `plugins/{plugin}/skills/{skill}/` removed, the skill dropped from "
            f"`scripts/plugin_layout.json` and the README table, bundle `{plugin}` 1.0.0 -> "
            "2.0.0 (a removal is a breaking change), by skills-evals' "
            "`scripts/retire_skill.py` (workflow run https://example.com/run/1).\n"
            f"- Eval: n=24 pooled arm runs (with vs without the skill) as of "
            f"20261031T000000Z, fixtures evals/{skill}/alpha, "
            "policy evals/gate-policy.yml; per-cell bounds in the pull request.\n"
            "- Outcome: pending merge; auto-merges once required checks pass, with no "
            "human review ([ADR 0021](https://example.com/adr)).\n")


def insert_like_the_loop(repo: Path, new: str) -> None:
    """The loops insert newest first, right after the header's `---` rule."""
    path = repo / "docs" / "skill-impact.md"
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rule = lines.index("---")
    head = "\n".join(lines[:rule + 1]).rstrip("\n")
    rest = "\n".join(lines[rule + 1:]).lstrip("\n")
    _write(path, f"{head}\n\n{new}\n{rest}")


def test_improve_loop_entry_passes(tmp_path):
    repo = make_repo(tmp_path)
    edit_skill(repo)
    insert_like_the_loop(repo, improve_gate_entry(PLUGIN, SKILL))
    commit(repo)
    code, out = gate(repo, census_file(tmp_path, "covered"))
    assert code == 0, out


def test_draft_loop_entry_passes(tmp_path):
    repo = make_repo(tmp_path)
    _write(skill_dir(repo, PLUGIN, "new-gizmo") / "SKILL.md", "# new\n")
    _write(skill_dir(repo, PLUGIN, "new-gizmo") / "PURPOSE.md", "# p\n")
    insert_like_the_loop(repo, draft_entry(PLUGIN, "new-gizmo"))
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out


def test_retire_loop_entry_passes(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    insert_like_the_loop(repo, retire_entry(PLUGIN, SKILL))
    commit(repo)
    code, out = gate(repo)
    assert code == 0, out
