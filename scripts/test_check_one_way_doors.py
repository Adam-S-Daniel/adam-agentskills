"""Tests for scripts/check_one_way_doors.py (issue #133).

Every scenario builds a throwaway git repository in tmp_path with a
marketplace, plugins, skills and the two data files, commits a `main` and a
`pr` branch, and runs the script as a subprocess. No network.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_one_way_doors.py"
sys.path.insert(0, str(ROOT / "scripts"))
import check_one_way_doors as owd  # noqa: E402

_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")
PLUGIN = "adam-coding-anywhere"
CODE_PLUGIN = "adam-coding-local"
SKILL = "widget-polisher"
OTHER = "gadget-sorter"
APP = "skills-evals-improve[bot]"
PASS = "Adversarial-review: pass https://github.com/Example/registry/actions/runs/1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *_GIT_VERBATIM, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _json(path: Path, data) -> None:
    _write(path, json.dumps(data, indent=2) + "\n")


def skill_md(skill: str, name: str = None) -> str:
    return f"---\nname: {name or skill}\ndescription: Invented.\n---\n\n# {skill}\n"


def manifest(plugin: str, version: str = "1.0.0", **extra) -> dict:
    return {"name": plugin, "version": version, **extra}


def marketplace(repo: Path, plugins, name: str = "adam-agentskills", **entry_extra) -> None:
    _json(repo / ".claude-plugin" / "marketplace.json", {
        "name": name,
        "plugins": [{"name": p, "source": f"./plugins/{p}", **entry_extra.get(p, {})}
                    for p in plugins]})


def commit(repo: Path, msg: str = "c") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path, ledger: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    marketplace(repo, [PLUGIN, CODE_PLUGIN])
    for plugin in (PLUGIN, CODE_PLUGIN):
        for m in owd.MANIFESTS:
            _json(repo / "plugins" / plugin / m, manifest(plugin))
    for skill in (SKILL, OTHER):
        _write(repo / "plugins" / PLUGIN / "skills" / skill / "SKILL.md", skill_md(skill))
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / "hooky" / "SKILL.md", skill_md("hooky"))
    _write(repo / "plugins" / CODE_PLUGIN / "hooks" / "run.sh", "#!/bin/sh\n")
    _write(repo / "docs" / "skill-impact.md", "# impact\n")
    _write(repo / "README.md", "# readme\n")
    _json(repo / "scripts" / "plugin_layout.json", {"plugins": {}})
    _json(repo / "scripts" / "loop_prs.json", {
        "author": APP,
        "lanes": {"draft-skill/": "create", "eval-improve/": "edit", "retire-skill/": "remove"}})
    if ledger:
        _json(repo / "scripts" / "shipped_names.json", {
            "marketplace": ["adam-agentskills"],
            "plugins": [CODE_PLUGIN, PLUGIN],
            "skills": sorted([f"{PLUGIN}/{SKILL}", f"{PLUGIN}/{OTHER}", f"{CODE_PLUGIN}/hooky"])})
    commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    return repo


def pr_json(ref: str = "claude/some-change", body: str = "", login: str = "Adam-S-Daniel",
            user_type: str = "User", head_repo: str = "Example/registry", number: int = 7) -> dict:
    return {"number": number, "body": body, "user": {"login": login, "type": user_type},
            "head": {"ref": ref, "repo": {"full_name": head_repo}},
            "base": {"repo": {"full_name": "Example/registry"}}}


def loop_pr(ref: str, body: str = "", auto_merge: bool = False, **kw) -> dict:
    pr = pr_json(ref=ref, body=body, login=APP, user_type="Bot", **kw)
    pr["auto_merge"] = {"merge_method": "merge"} if auto_merge else None
    return pr


def check(repo: Path, tmp_path: Path, pr: dict, open_prs=None):
    pr_path = tmp_path / "pr.json"
    pr_path.write_text(json.dumps(pr), encoding="utf-8")
    args = [sys.executable, str(SCRIPT), "--base", "main", "--head", "pr",
            "--pr-json", str(pr_path)]
    if open_prs is not None:
        open_path = tmp_path / "open.json"
        open_path.write_text(json.dumps(open_prs), encoding="utf-8")
        args += ["--open-prs", str(open_path)]
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    proc = subprocess.run(args, cwd=repo, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def remove_skill(repo: Path, skill: str = SKILL, plugin: str = PLUGIN) -> None:
    _git(repo, "rm", "-rq", f"plugins/{plugin}/skills/{skill}")


def rename_skill(repo: Path) -> None:
    src = repo / "plugins" / PLUGIN / "skills" / SKILL
    dst = repo / "plugins" / PLUGIN / "skills" / "widget-buffer"
    _write(dst / "SKILL.md", skill_md("widget-buffer"))
    _git(repo, "rm", "-rq", str(src.relative_to(repo)))


# ---------------------------------------------------------------------------
# Check 1: shipped names
# ---------------------------------------------------------------------------

def test_unrelated_change_passes(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "README.md", "# readme\n\nmore\n")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 0, out


def test_renaming_a_shipped_skill_fails_without_a_review(tmp_path):
    repo = make_repo(tmp_path)
    rename_skill(repo)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1, out
    assert f'"{PLUGIN}/{SKILL}"' in out and "Adversarial-review: pass" in out


def test_renaming_a_shipped_skill_passes_with_a_review_line(tmp_path):
    repo = make_repo(tmp_path)
    rename_skill(repo)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=f"## Adversarial review\n\n{PASS}\n"))
    assert code == 0, out


def test_a_block_line_fails_even_beside_a_pass_line(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    body = f"{PASS}\n\nAdversarial-review: block https://github.com/Example/registry/pull/2\n"
    code, out = check(repo, tmp_path, pr_json(body=body))
    assert code == 1 and "block" in out, out


@pytest.mark.parametrize("body", [
    "Adversarial-review: pass <url>",
    "Adversarial-review: pass http://github.com/Example/registry/actions/runs/1",
    "Adversarial-review: pass https://example.com/review/1",
    "Adversarial-review: pass",
    "See Adversarial-review: pass https://github.com/Example/registry/actions/runs/1 later",
    "<!--\nAdversarial-review: pass https://github.com/Example/registry/actions/runs/1\n-->",
    "```\nAdversarial-review: pass https://github.com/Example/registry/actions/runs/1\n```",
    "    Adversarial-review: pass https://github.com/Example/registry/actions/runs/1",
    "Removes a skill. <!--\nAdversarial-review: pass https://github.com/Example/registry/actions/runs/1\n-->",
    "",
])
def test_a_malformed_review_line_does_not_count(tmp_path, body):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=body))
    assert code == 1, out


def test_changing_the_frontmatter_name_counts_as_a_rename(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md",
           skill_md(SKILL, name="widget-buffer"))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'"{PLUGIN}/{SKILL}"' in out, out


def test_editing_a_skill_body_keeps_its_name(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nmore\n")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 0, out


def test_moving_a_skill_between_plugins_fails(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'"{PLUGIN}/{SKILL}"' in out, out


def test_renaming_a_plugin_fails(tmp_path):
    repo = make_repo(tmp_path)
    marketplace(repo, ["adam-coding-everywhere", CODE_PLUGIN])
    (repo / "plugins" / PLUGIN).rename(repo / "plugins" / "adam-coding-everywhere")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1, out
    assert f'plugin "{PLUGIN}"' in out and f'skill "{PLUGIN}/{SKILL}"' in out


def test_renaming_the_marketplace_fails(tmp_path):
    repo = make_repo(tmp_path)
    marketplace(repo, [PLUGIN, CODE_PLUGIN], name="adam-skills")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and 'marketplace "adam-agentskills"' in out, out


def test_moving_a_plugin_directory_counts_as_a_rename(tmp_path):
    """Round 2: the lock's `<bundle>` is the directory under plugins/, so a
    moved directory breaks every consumer key even when the name stays."""
    repo = make_repo(tmp_path)
    _json(repo / ".claude-plugin" / "marketplace.json", {
        "name": "adam-agentskills",
        "plugins": [{"name": PLUGIN, "source": "./plugins/aca"},
                    {"name": CODE_PLUGIN, "source": f"./plugins/{CODE_PLUGIN}"}]})
    (repo / "plugins" / PLUGIN).rename(repo / "plugins" / "aca")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'plugin "{PLUGIN}"' in out, out


def test_renaming_a_plugin_in_its_manifests_counts_as_a_rename(tmp_path):
    repo = make_repo(tmp_path)
    for m in owd.MANIFESTS:
        _json(repo / "plugins" / PLUGIN / m, manifest("adam-coding-everywhere"))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'plugin "{PLUGIN}"' in out, out


def test_a_decoy_duplicate_marketplace_entry_counts_as_a_rename(tmp_path):
    repo = make_repo(tmp_path)
    _json(repo / ".claude-plugin" / "marketplace.json", {
        "name": "adam-agentskills",
        "plugins": [{"name": PLUGIN, "source": "./plugins/nowhere"}]
        + [{"name": p, "source": f"./plugins/{p}"} for p in (PLUGIN, CODE_PLUGIN)]})
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'plugin "{PLUGIN}"' in out, out


def test_a_plugin_root_that_moves_every_source_counts_as_a_rename(tmp_path):
    repo = make_repo(tmp_path)
    data = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    data["metadata"] = {"pluginRoot": "./nowhere"}
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'plugin "{PLUGIN}"' in out, out


def test_the_ledger_is_append_only(tmp_path):
    repo = make_repo(tmp_path)
    path = repo / "scripts" / "shipped_names.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["skills"].remove(f"{PLUGIN}/{OTHER}")
    _json(path, data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 1 and "append-only" in out, out


def test_deleting_the_ledger_fails(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "rm", "-q", "scripts/shipped_names.json")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 1 and "append-only" in out, out


def test_a_lost_name_missing_from_the_ledger_fails(tmp_path):
    """A skill shipped after the ledger was last appended to is still caught,
    and its removal must record it."""
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    _write(repo / "plugins" / PLUGIN / "skills" / "late-skill" / "SKILL.md", skill_md("late-skill"))
    commit(repo)
    _git(repo, "checkout", "-q", "-B", "pr")
    remove_skill(repo, "late-skill")
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 1 and "not in scripts/shipped_names.json" in out, out
    path = repo / "scripts" / "shipped_names.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["skills"] = sorted(data["skills"] + [f"{PLUGIN}/late-skill"])
    _json(path, data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 0, out


def test_an_unknown_revision_exits_2(tmp_path):
    repo = make_repo(tmp_path)
    pr_path = tmp_path / "pr.json"
    pr_path.write_text(json.dumps(pr_json()), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--base", "nope", "--head", "pr",
                           "--pr-json", str(pr_path)], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_annotations_cannot_be_split_by_a_newline(tmp_path):
    assert "\n" not in owd._escape("a\nb\r%c")
    assert owd._escape("a\nb\r%c") == "a%0Ab%0D%25c"


# ---------------------------------------------------------------------------
# Check 2: evals-loop pull requests stay inside ADR 0021
# ---------------------------------------------------------------------------

def bump_version(repo: Path, plugin: str = PLUGIN, version: str = "2.0.0", **extra) -> None:
    for m in owd.MANIFESTS:
        _json(repo / "plugins" / plugin / m, manifest(plugin, version, **extra))


def loop_removal(repo: Path) -> None:
    remove_skill(repo)
    bump_version(repo)
    _write(repo / "docs" / "skill-impact.md", "# impact\n\n## entry\n")
    _json(repo / "scripts" / "plugin_layout.json", {"plugins": {"x": []}})
    commit(repo)


def test_an_in_scope_loop_removal_with_a_review_passes(tmp_path):
    repo = make_repo(tmp_path)
    loop_removal(repo)
    code, out = check(repo, tmp_path, loop_pr(f"retire-skill/{SKILL}", body=PASS), open_prs=[])
    assert code == 0, out


def test_a_loop_removal_without_a_review_fails(tmp_path):
    repo = make_repo(tmp_path)
    loop_removal(repo)
    code, out = check(repo, tmp_path, loop_pr(f"retire-skill/{SKILL}"), open_prs=[])
    assert code == 1 and "Adversarial-review" in out, out


def test_a_loop_pr_touching_another_path_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    _write(repo / "scripts" / "check_something.py", "print('x')\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 1 and '"scripts/check_something.py"' in out, out


def test_an_in_scope_improvement_passes(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "PURPOSE.md", "# Purpose\n")
    bump_version(repo, version="1.1.0")
    _write(repo / "README.md", "# readme\n\ntable\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 0, out


def test_an_improvement_may_not_touch_the_layout(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    _json(repo / "scripts" / "plugin_layout.json", {"plugins": {"x": []}})
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 1 and '"scripts/plugin_layout.json"' in out, out


def test_an_in_scope_draft_passes(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / "new-skill" / "SKILL.md", skill_md("new-skill"))
    _json(repo / "scripts" / "plugin_layout.json", {"plugins": {"x": ["new-skill"]}})
    bump_version(repo, version="1.1.0")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr("draft-skill/new-skill"), open_prs=[])
    assert code == 0, out


def test_a_loop_pr_that_does_the_wrong_kind_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"draft-skill/{SKILL}"), open_prs=[])
    assert code == 1 and "must create" in out, out


def test_a_loop_pr_editing_two_skills_fails(tmp_path):
    repo = make_repo(tmp_path)
    for skill in (SKILL, OTHER):
        _write(repo / "plugins" / PLUGIN / "skills" / skill / "SKILL.md", skill_md(skill) + "\nv2\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 1 and f"skills/{OTHER}/SKILL.md" in out, out


def test_a_manifest_change_beyond_the_version_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    bump_version(repo, version="1.1.0", description="changed")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 1 and "only its `version`" in out, out


@pytest.mark.parametrize("login,user_type", [
    ("Adam-S-Daniel", "User"), ("another-app[bot]", "Bot"), (APP, "User")])
def test_a_loop_branch_from_anyone_but_the_app_fails(tmp_path, login, user_type):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    commit(repo)
    pr = pr_json(ref=f"eval-improve/{SKILL}", login=login, user_type=user_type)
    code, out = check(repo, tmp_path, pr, open_prs=[])
    assert code == 1 and "opened by" in out, out


def test_a_loop_branch_from_a_fork_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}", head_repo="Someone/fork"),
                      open_prs=[])
    assert code == 1 and "fork" in out, out


def test_a_text_only_loop_pr_in_a_plugin_with_hooks_passes(tmp_path):
    """Round 2: the owner decided on 2026-11-03 that text-only loop PRs in
    plugins with hooks auto-merge (skills-evals ADR 0005 addendum); runtime
    changes are held by plugin-runtime-review, not here."""
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / "hooky" / "SKILL.md", skill_md("hooky") + "\nv2\n")
    bump_version(repo, plugin=CODE_PLUGIN, version="1.1.0")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr("eval-improve/hooky", auto_merge=True), open_prs=[])
    assert code == 0, out


def test_a_second_open_loop_pr_fails(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    commit(repo)
    open_prs = [open_pr(7, f"eval-improve/{SKILL}"), open_pr(5, f"draft-skill/{OTHER}"),
                open_pr(9, f"retire-skill/{OTHER}"), open_pr(3, "claude/unrelated")]
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=open_prs)
    # Only the older loop PR (5) blocks; the newer one (9) waits behind this.
    assert code == 1 and "[5]" in out, out


def open_pr(number, ref, login=APP, user_type="Bot", repo="Example/registry", draft=False):
    return {"number": number, "draft": draft, "user": {"login": login, "type": user_type},
            "head": {"ref": ref, "repo": {"full_name": repo}}}


@pytest.mark.parametrize("other", [
    open_pr(9, "retire-skill/x", login="someone", user_type="User", repo="Someone/fork"),
    open_pr(9, "retire-skill/x", login="someone", user_type="User"),
    open_pr(9, "retire-skill/x", repo="Someone/fork"),
    open_pr(9, f"draft-skill/{OTHER}", draft=True),
])
def test_only_the_apps_ready_loop_prs_hold_the_slot(tmp_path, other):
    """Round 1 finding: anyone could open a fork PR named retire-skill/x and
    turn every loop PR red; the loop's own drafts must not count either."""
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[other])
    assert code == 0, out


@pytest.mark.parametrize("extra", [
    "allowed-tools: Bash(*)\n",
    "hooks:\n  PreToolUse:\n    - command: ./run.sh\n",
])
def test_a_loop_pr_may_not_add_skill_runtime_frontmatter(tmp_path, extra):
    repo = make_repo(tmp_path)
    text = f"---\nname: {SKILL}\ndescription: Invented.\n{extra}---\n\n# {SKILL}\n"
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", text)
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}", auto_merge=True),
                      open_prs=[])
    assert code == 1 and "Decision 4" in out, out
    # Unarmed, it waits for a person, who may merge it by hand.
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}"), open_prs=[])
    assert code == 0, out


def test_a_criss_cross_merge_base_cannot_hide_a_deletion(tmp_path):
    """Round 1 finding: with two merge bases, `git merge-base` may return the
    one before the skill existed. The check judges the merge result instead."""
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "add-skill")
    _write(repo / "plugins" / PLUGIN / "skills" / "new-skill" / "SKILL.md", skill_md("new-skill"))
    commit(repo, "add")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "sibling")
    _write(repo / "README.md", "# readme\n\nsibling\n")
    sibling = commit(repo, "sibling")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge add", "add-skill")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge sibling", "sibling")
    _git(repo, "checkout", "-q", "-B", "pr", sibling)
    _git(repo, "merge", "-q", "--no-ff", "-m", "bring in add", "add-skill")
    _git(repo, "rm", "-rq", f"plugins/{PLUGIN}/skills/new-skill")
    commit(repo, "delete")
    assert len(_git(repo, "merge-base", "--all", "main", "pr").split()) == 2
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and f'"{PLUGIN}/new-skill"' in out, out


def test_reusing_a_retired_skill_name_in_any_plugin_needs_a_review(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    remove_skill(repo)
    commit(repo, "retire")
    _git(repo, "checkout", "-q", "-B", "pr")
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and "reuse a retired name" in out, out
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 0, out


def test_a_new_name_never_shipped_needs_no_review(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / "brand-new" / "SKILL.md", skill_md("brand-new"))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 0, out


def test_a_conflicting_pr_exits_2(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "README.md", "# readme\n\npr side\n")
    commit(repo)
    _git(repo, "checkout", "-q", "main")
    _write(repo / "README.md", "# readme\n\nmain side\n")
    commit(repo)
    _git(repo, "checkout", "-q", "pr")
    code, out = check(repo, tmp_path, pr_json())
    assert code == 2 and "conflicts" in out, out


def test_the_loop_config_is_read_at_the_merge_base(tmp_path):
    """A pull request cannot name its own author as the loop's App."""
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    _json(repo / "scripts" / "loop_prs.json", {
        "author": "Adam-S-Daniel", "lanes": {"eval-improve/": "edit"}})
    commit(repo)
    pr = pr_json(ref=f"eval-improve/{SKILL}", login="Adam-S-Daniel", user_type="Bot")
    code, out = check(repo, tmp_path, pr, open_prs=[])
    assert code == 1 and "opened by" in out, out


def test_a_missing_loop_config_exits_2(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "rm", "-q", "scripts/loop_prs.json")
    commit(repo)
    _git(repo, "checkout", "-q", "-B", "pr")
    code, out = check(repo, tmp_path, pr_json())
    assert code == 2 and "loop_prs.json" in out, out


# ---------------------------------------------------------------------------
# The repository's own data files
# ---------------------------------------------------------------------------

def _canonical(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2) + "\n", f"{path} is not canonical"


def test_the_ledger_is_canonical_sorted_and_lists_the_marketplace():
    # Skills on disk are deliberately not required here: one the draft lane
    # added since the ledger was last appended to is caught anyway (names
    # present at the merge-base count as shipped), and requiring it would
    # turn every later pull request red until someone appended it.
    path = ROOT / "scripts" / "shipped_names.json"
    _canonical(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == set(owd.LEDGER_KEYS)
    for key in owd.LEDGER_KEYS:
        assert data[key] == sorted(set(data[key])), f"{key} must be sorted and unique"
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] in data["marketplace"]
    for entry in market["plugins"]:
        assert entry["name"] in data["plugins"]


def test_the_loop_config_is_canonical_and_names_the_three_lanes():
    path = ROOT / "scripts" / "loop_prs.json"
    _canonical(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["lanes"] == {"draft-skill/": "create", "eval-improve/": "edit",
                             "retire-skill/": "remove"}
    assert data["author"].endswith("[bot]")


# ---------------------------------------------------------------------------
# Round 3 findings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "---\ndescription: Invented.\n---\n",                          # no name
    "---\nname: other-name\ndescription: Invented.\n---\n",        # name != directory
    "---\nname: brand-new\ndescription: Use when: x\n---\n",       # unparsable YAML
])
def test_a_new_skill_whose_frontmatter_name_is_not_its_directory_fails(tmp_path, text):
    """A skill that would load under another name (or none) can neither keep
    a name nor slip in past the retired-name check."""
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / "brand-new" / "SKILL.md", text)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 1 and "frontmatter `name`" in out, out


def test_a_retired_name_cannot_return_under_another_directory(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    remove_skill(repo)
    commit(repo, "retire")
    _git(repo, "checkout", "-q", "-B", "pr")
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / "fot2" / "SKILL.md",
           skill_md("fot2", name=SKILL))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1, out


def test_a_loop_draft_may_open_while_another_loop_pr_is_open(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / "new-skill" / "SKILL.md", skill_md("new-skill"))
    commit(repo)
    pr = loop_pr("draft-skill/new-skill")
    pr["draft"] = True
    code, out = check(repo, tmp_path, pr, open_prs=[open_pr(4, f"eval-improve/{OTHER}")])
    assert code == 0, out
    pr["draft"] = False
    code, out = check(repo, tmp_path, pr, open_prs=[open_pr(4, f"eval-improve/{OTHER}")])
    assert code == 1 and "[4]" in out, out


# ---------------------------------------------------------------------------
# Round 4 findings
# ---------------------------------------------------------------------------

def _waivers(repo: Path, entries) -> None:
    import yaml
    _write(repo / "scripts" / "skills_waivers.yml", yaml.safe_dump({"waivers": entries}))


def _waiver(skill: str, klass: str = "length-limit") -> dict:
    return {"klass": klass, "registry": "adam-agentskills",
            "path": f"plugins/{PLUGIN}/skills/{skill}/SKILL.md", "reason": "Invented."}


def _trim_repo(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    _waivers(repo, [_waiver(SKILL), _waiver(OTHER)])
    commit(repo, "waivers")
    _git(repo, "checkout", "-q", "-B", "pr")
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nshorter\n")
    return repo


def test_a_trim_may_retire_its_own_length_waiver(tmp_path):
    """Round 4: skills-evals' improve lane removes the trimmed skill's
    length-limit waiver; failing that would hold the loop slot."""
    repo = _trim_repo(tmp_path)
    _waivers(repo, [_waiver(OTHER)])
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}", auto_merge=True), open_prs=[])
    assert code == 0, out


@pytest.mark.parametrize("entries", [
    [_waiver(SKILL)],                          # drops another skill's waiver
    [],                                        # drops both
    [_waiver(SKILL), _waiver(OTHER), _waiver("x")],  # adds one
])
def test_a_trim_may_change_nothing_else_in_the_waivers(tmp_path, entries):
    repo = _trim_repo(tmp_path)
    _waivers(repo, entries)
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}", auto_merge=True), open_prs=[])
    assert code == 1 and "skills_waivers.yml" in out, out


@pytest.mark.parametrize("wrapper", [
    "<details>\nAdversarial-review: block https://github.com/Example/registry/pull/2\n</details>",
    "ADVERSARIAL-REVIEW: BLOCK https://github.com/Example/registry/pull/2",
])
def test_a_visible_block_line_anywhere_fails(tmp_path, wrapper):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=f"{wrapper}\n\n{PASS}\n"))
    assert code == 1 and "block" in out, out


def test_a_block_line_inside_a_comment_does_not_count(tmp_path):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    body = f"<!--\nAdversarial-review: block https://github.com/Example/registry/pull/2\n-->\n\n{PASS}\n"
    code, out = check(repo, tmp_path, pr_json(body=body))
    assert code == 0, out


# ---------------------------------------------------------------------------
# Round 5 findings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "- Adversarial-review: block https://github.com/Example/registry/pull/2",
    "> Adversarial-review: block https://github.com/Example/registry/pull/2",
    "**Adversarial-review: block** https://github.com/Example/registry/pull/2",
])
def test_a_block_line_in_a_list_quote_or_bold_fails(tmp_path, line):
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json(body=f"{line}\n\n{PASS}\n"))
    assert code == 1 and "block" in out, out


def test_moving_a_remote_plugin_to_another_repository_is_a_rename(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    data = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    data["plugins"].append({"name": "remote-one", "source": {"source": "github", "repo": "Example/one"}})
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo, "remote")
    _git(repo, "checkout", "-q", "-B", "pr")
    data["plugins"][-1]["source"] = {"source": "github", "repo": "Someone/other"}
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and 'plugin "remote-one"' in out, out


def test_the_ledger_lists_every_skill_the_impact_log_records_as_removed():
    """Round 5: the ledger was first seeded from a shallow clone and missed
    names retired before it. Every whole-skill `remove` entry for a plugin of
    this marketplace must be listed. Entries are parsed the way the skill
    gates parse them (markdown-it), not by a line scan."""
    import check_skill_gates as csg

    ledger = json.loads((ROOT / "scripts" / "shipped_names.json").read_text(encoding="utf-8"))
    entries = csg.parse_entries((ROOT / "docs" / "skill-impact.md").read_text(encoding="utf-8"))
    removed = {f"{e['plugin']}/{e['skill']}" for e in entries
               if e["kind"] == "remove" and e["plugin"] in ledger["plugins"]}
    on_disk = {f"{p.parent.parent.parent.name}/{p.parent.name}"
               for p in (ROOT / "plugins").glob("*/skills/*/SKILL.md")}
    # A partial removal (one flag of a skill that still ships) is not a retirement.
    missing = sorted(removed - on_disk - set(ledger["skills"]))
    assert removed, "the impact log parsed to no removals; the parser drifted"
    assert not missing, f"retired names missing from the ledger: {missing}"


# ---------------------------------------------------------------------------
# Round 6 findings
# ---------------------------------------------------------------------------

def _remote_repo(tmp_path: Path, source: dict) -> Path:
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    data = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    data["plugins"].append({"name": "remote-one", "source": source})
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo, "remote")
    _git(repo, "checkout", "-q", "-B", "pr")
    return repo


def test_moving_a_remote_plugins_pin_is_not_a_rename(tmp_path):
    repo = _remote_repo(tmp_path, {"source": "github", "repo": "Example/one", "ref": "v1.0.0"})
    data = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    data["plugins"][-1]["source"]["ref"] = "v1.1.0"
    data["plugins"][-1]["source"]["sha"] = "0" * 40
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 0, out


def test_adding_a_renames_map_needs_a_review(tmp_path):
    repo = make_repo(tmp_path)
    data = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    data["renames"] = {"old-plugin": PLUGIN}
    _json(repo / ".claude-plugin" / "marketplace.json", data)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and "renames" in out, out
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 0, out


# ---------------------------------------------------------------------------
# Round 7 findings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,rule", [
    (".gitattributes", f"plugins/{PLUGIN}/skills/{SKILL} export-ignore\n"),
    (f"plugins/{PLUGIN}/.gitattributes", "skills/** export-subst\n"),
])
def test_an_export_rule_that_git_archive_follows_needs_a_review(tmp_path, path, rule):
    """Round 7: locks are built from `git archive`, which honours these."""
    repo = make_repo(tmp_path)
    _write(repo / path, rule)
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and "git archive" in out, out
    code, out = check(repo, tmp_path, pr_json(body=PASS))
    assert code == 0, out


def test_a_loop_pr_may_not_add_gitattributes_in_its_skill(tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / "SKILL.md", skill_md(SKILL) + "\nv2\n")
    _write(repo / "plugins" / PLUGIN / "skills" / SKILL / ".gitattributes", "*.md text\n")
    commit(repo)
    code, out = check(repo, tmp_path, loop_pr(f"eval-improve/{SKILL}", auto_merge=True), open_prs=[])
    assert code == 1 and ".gitattributes" in out, out


@pytest.mark.parametrize("variant", ["Widget-Polisher", "widget-polisher."])
def test_a_retired_name_cannot_return_with_another_case_or_a_trailing_dot(tmp_path, variant):
    repo = make_repo(tmp_path)
    _git(repo, "checkout", "-q", "main")
    remove_skill(repo)
    commit(repo, "retire")
    _git(repo, "checkout", "-q", "-B", "pr")
    _write(repo / "plugins" / CODE_PLUGIN / "skills" / variant / "SKILL.md", skill_md(variant))
    commit(repo)
    code, out = check(repo, tmp_path, pr_json())
    assert code == 1 and "reuse a retired name" in out, out


def test_a_finding_that_quotes_the_block_phrase_mid_line_does_not_block(tmp_path):
    """Round 7: skills-evals' reviewer arms on a line-start pass; a finding
    that quotes the phrase must not turn that passing review red here."""
    repo = make_repo(tmp_path)
    remove_skill(repo)
    commit(repo)
    body = ("## Adversarial review\n\n- **low** (references): the docs mention "
            "Adversarial-review: block lines.\n\n" + PASS + "\n")
    code, out = check(repo, tmp_path, pr_json(body=body))
    assert code == 0, out
