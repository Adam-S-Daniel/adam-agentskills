"""Tests for scripts/check_skill_freshness.py and its scheduled workflow.

Every repo here is a local git repo served through a `file://` remote or a
`--repo` override, so nothing touches the network.

Run with the repo's runner:
    python3 -m pytest scripts/test_check_skill_freshness.py -q
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_skill_freshness as fresh  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-freshness.yml"
LOCK = REPO_ROOT / ".github" / "workflows" / "actions.lock"

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git_repo(path: Path, files: dict) -> Path:
    """A one-commit git repo holding `files` ({relative path: text})."""
    path.mkdir(parents=True)
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "c"]):
        subprocess.run(["git", "-C", str(path), *cmd], check=True, env=GIT_ENV,
                       capture_output=True)
    return path


class World:
    """A registry of skills plus the repos they cite, all local."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.remote = tmp / "remote"
        self.registry = tmp / "registry"
        self.skills = {}
        self.waivers = []
        self.bare_paths = True
        self.repos = {
            "example-owner/host": {"infrastructure/deploy.sh": "x", "docs/OPS.md": "x",
                                   ".github/workflows/deploy-preview.yml": "x"},
            "example-owner/consumer": {"site/admin/config.base.yml": "x",
                                       "site/e2e/smoke.spec.js": "x"},
            "example-owner/other": {"docs/GUIDE.md": "x"},
        }

    def skill(self, name: str, body: str, extra: dict = None):
        self.skills[name] = (textwrap.dedent(body), extra or {})

    def run(self, *args, served=("example-owner/host", "example-owner/consumer",
                                 "example-owner/other")):
        for slug in served:
            src = git_repo(self.tmp / "src" / slug, self.repos[slug])
            dest = self.remote / f"{slug}.git"
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", "--bare", str(src), str(dest)],
                           check=True, env=GIT_ENV, capture_output=True)
        for name, (body, extra) in self.skills.items():
            d = self.registry / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
            for rel, text in extra.items():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_text(text, encoding="utf-8")
        reg_cfg = self.tmp / "registries.yml"
        reg_cfg.write_text(yaml.safe_dump({"registries": [
            {"name": "host", "path": str(self.registry), "layout": "skills/*/SKILL.md"}]}))
        cfg = self.tmp / "freshness.yml"
        cfg.write_text(yaml.safe_dump({
            "registries": {"host": {
                "repo": "example-owner/host", "bare_paths": self.bare_paths,
                "also_resolve_in": ["example-owner/consumer:site/"]}},
            "repos": {"other": "example-owner/other", "host": "example-owner/host"},
            "waivers": self.waivers,
        }))
        out = self.tmp / "report.md"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "check_skill_freshness.py"),
             "--config", str(cfg), "--registries-config", str(reg_cfg),
             "--remote", self.remote.as_uri(), "--output", str(out), *args],
            capture_output=True, text=True, env=GIT_ENV, timeout=120)
        report = out.read_text(encoding="utf-8") if out.exists() else ""
        return proc.returncode, report, proc.stderr


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ── Files and workflows ─────────────────────────────────────────────────────

def test_a_fresh_citation_passes_and_a_stale_one_fails_with_its_line(world):
    world.skill("deploy", """
        Run `infrastructure/deploy.sh` first.

        Then read `infrastructure/gone.sh`.
        """)
    rc, report, err = world.run()
    assert rc == 1, report + err
    assert "1 citation(s) fresh, 1 stale" in report
    assert "`host:skills/deploy/SKILL.md:8` cites path `infrastructure/gone.sh`" in report
    assert "infrastructure/deploy.sh" not in report.split("## Stale")[1]


def test_a_bare_name_and_a_workflow_name_resolve_anywhere_in_their_places(world):
    world.skill("names", """
        The `deploy-preview.yml` workflow, `smoke.spec.js` and `config.base.yml`
        (in the consumer's `site/`), and `e2e/smoke.spec.js` under its prefix.
        """)
    rc, report, err = world.run()
    assert rc == 0, report + err
    assert "4 citation(s) fresh, 0 stale" in report


def test_a_missing_workflow_name_is_stale(world):
    world.skill("wf", "Dispatch `teardown-preview.yml`.\n")
    rc, report, _ = world.run()
    assert rc == 1
    assert "cites workflow `teardown-preview.yml`" in report


def test_a_path_after_a_repo_name_is_checked_in_that_repo_alone(world):
    # docs/OPS.md is in host, not in other: naming `other` makes it stale.
    world.skill("named", "See other's `docs/OPS.md` and other's `docs/GUIDE.md`.\n")
    rc, report, _ = world.run()
    assert rc == 1
    assert "cites path `docs/OPS.md`, not found in example-owner/other" in report
    assert "docs/GUIDE.md" not in report


def test_github_links_to_a_branch_tip_are_checked_and_pinned_ones_skipped(world):
    sha = "0" * 40
    world.skill("links", f"""
        [guide](https://github.com/example-owner/other/blob/main/docs/GUIDE.md),
        [gone](https://github.com/example-owner/other/blob/main/docs/GONE.md),
        [pinned](https://github.com/example-owner/other/blob/{sha}/docs/OLD.md).

        ```bash
        curl https://raw.githubusercontent.com/example-owner/host/main/docs/OPS.md
        ```
        """)
    rc, report, _ = world.run()
    assert rc == 1
    assert "2 citation(s) fresh, 1 stale" in report
    assert "`docs/GONE.md`" in report and "OLD.md" not in report


def test_a_registry_of_generic_skills_checks_only_citations_that_name_a_repo(world):
    world.bare_paths = False
    world.skill("generic", """
        Write `docs/decisions/README.md` in your repo, and see
        other's `docs/GUIDE.md`.
        """)
    rc, report, _ = world.run()
    assert rc == 0, report
    assert "1 citation(s) fresh, 0 stale" in report


def test_a_path_inside_the_skills_own_directory_is_left_to_check_skills(world):
    world.skill("payload", "Run `scripts/helper.sh`.\n", {"scripts/helper.sh": "x"})
    rc, report, _ = world.run()
    assert rc == 0, report
    assert "0 citation(s) fresh, 0 stale" in report


def test_other_markdown_files_in_the_skill_directory_are_scanned(world):
    world.skill("refs", "See references.\n", {"references/more.md": "Run `infrastructure/gone.sh`.\n"})
    rc, report, _ = world.run()
    assert rc == 1
    assert "`host:skills/refs/references/more.md:1`" in report


@pytest.mark.parametrize("span", [
    "example.com", "pr-{N}/", "${ResourcePrefix}-previews", "cms/*", "/admin/",
    "admin/config*.yml", "https://example.com/a.html", "~/notes.md", "../x.md",
    "npm run lint", "www.example.com/a.html", "docs/", "site-params.env",
    "_site/admin/config.yml", "STACK_NAME", "a.b",
])
def test_spans_that_are_not_file_paths_are_never_citations(span):
    assert fresh.path_shape(span) is None


@pytest.mark.parametrize("span,expected", [
    ("infrastructure/bootstrap/deploy.sh", ("path", "infrastructure/bootstrap/deploy.sh")),
    ("./docs/OPS.md", ("path", "docs/OPS.md")),
    ("scripts/x.py:12", ("path", "scripts/x.py")),
    ("deploy-preview.yml", ("workflow", "deploy-preview.yml")),
    ("deploy.sh", ("file", "deploy.sh")),
    (".github/workflows/ci.yml", ("path", ".github/workflows/ci.yml")),
])
def test_path_shaped_spans_are_citations(span, expected):
    assert fresh.path_shape(span) == expected


# ── Waivers ─────────────────────────────────────────────────────────────────

def test_a_waiver_turns_a_stale_citation_green_and_an_unused_one_is_listed(world):
    world.skill("gen", "The rendered `admin/config.yml`.\n")
    world.waivers = [
        {"skill": "host/gen", "cite": "admin/config.yml", "reason": "rendered at build"},
        {"skill": "host/gen", "cite": "old.yml", "reason": "was here once"},
    ]
    rc, report, _ = world.run()
    assert rc == 0, report
    assert "1 waived" in report
    assert "## Waivers that matched nothing" in report and "`old.yml`" in report


def test_a_waiver_without_a_reason_is_a_config_error(world):
    world.skill("gen", "x\n")
    world.waivers = [{"skill": "host/gen", "cite": "a.yml", "reason": ""}]
    rc, _, err = world.run()
    assert rc == 2
    assert "reason" in err


# ── Unreadable repos ────────────────────────────────────────────────────────

def test_a_cited_repo_that_cannot_be_read_is_unverified_not_stale(world):
    world.skill("priv", "[x](https://github.com/example-owner/private/blob/main/a.md)\n")
    rc, report, _ = world.run()
    assert rc == 0, report
    assert "1 unverified" in report
    assert "a GitHub 404 does not say which" in report


def test_the_repo_bare_paths_resolve_in_must_be_readable(world):
    world.skill("deploy", "Run `infrastructure/deploy.sh`.\n")
    rc, report, _ = world.run(served=("example-owner/consumer", "example-owner/other"))
    assert rc == 2, report
    assert "cannot read example-owner/host" in report


def test_a_local_checkout_can_stand_in_for_a_repo(world, tmp_path):
    world.skill("deploy", "Run `infrastructure/local-only.sh`.\n")
    local = git_repo(tmp_path / "checkout", {"infrastructure/local-only.sh": "x"})
    rc, report, _ = world.run("--repo", f"example-owner/host={local}")
    assert rc == 0, report
    assert "1 citation(s) fresh" in report


# ── Secrets ─────────────────────────────────────────────────────────────────

def test_secret_names_are_reported_unchecked_without_check_secrets(world):
    world.skill("sec", """
        Set `secrets.AWS_ROLE_ARN`.

        ```yaml
        token: ${{ secrets.GITHUB_TOKEN }}
        role: ${{ secrets.AWS_ROLE_ARN }}
        ```
        """)
    rc, report, _ = world.run()
    assert rc == 0, report
    # GITHUB_TOKEN is built in; the other name is cited once.
    assert "Not checked: 1 cited secret name(s)" in report
    assert "Only files and workflows were checked" in report


def test_with_check_secrets_a_listed_name_is_fresh_and_a_missing_one_stale(world, monkeypatch):
    monkeypatch.setattr(fresh, "secret_names", lambda slug: {"AWS_ROLE_ARN"})
    citations = fresh.extract(
        "Set `secrets.AWS_ROLE_ARN` and `secrets.OLD_KEY`.\n", registry="host",
        skill="s", file="skills/s/SKILL.md", host=fresh.Place("example-owner/host"),
        bare=[], repos={})
    result = fresh.Result()
    config = {"registries": {"host": {"repo": "example-owner/host", "bare_paths": False,
                                      "also": []}}, "repos": {}, "waivers": []}
    fresh.check(citations, config, trees=None, check_secrets=True, result=result)
    assert [c.value for c in result.stale] == ["OLD_KEY"]
    assert result.checked == 1


def test_an_unreadable_secret_list_leaves_the_name_unchecked(monkeypatch):
    def deny(slug):
        raise fresh.Unreadable("this credential cannot list its secrets")
    monkeypatch.setattr(fresh, "secret_names", deny)
    citations = fresh.extract("`secrets.AWS_ROLE_ARN`\n", registry="host", skill="s",
                              file="f", host=fresh.Place("example-owner/host"),
                              bare=[], repos={})
    result = fresh.Result()
    config = {"registries": {}, "repos": {}, "waivers": []}
    fresh.check(citations, config, trees=None, check_secrets=True, result=result)
    assert not result.stale and len(result.secrets_unchecked) == 1
    assert "cannot list its secrets" in fresh.render(result, True)


# ── The shipped config and workflow ─────────────────────────────────────────

def test_the_shipped_config_covers_every_declared_registry():
    config = fresh.load_config(fresh.DEFAULT_CONFIG)
    declared = yaml.safe_load(fresh.DEFAULT_REGISTRIES.read_text(encoding="utf-8"))
    names = {r["name"] for r in declared["registries"]}
    assert names <= set(config["registries"]), names - set(config["registries"])
    for w in config["waivers"]:
        assert w["skill"].split("/", 1)[0] in names, w


def load_workflow():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    return doc, doc.get("on", doc.get(True))


def test_the_workflow_runs_on_a_schedule_and_by_hand_only():
    doc, on = load_workflow()
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert "concurrency" not in doc
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["freshness"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "concurrency" not in job


def test_the_workflow_runs_the_lint_without_secrets_and_files_an_issue_on_failure():
    doc, _ = load_workflow()
    steps = doc["jobs"]["freshness"]["steps"]
    lint = [s for s in steps if "check_skill_freshness.py" in s.get("run", "")]
    assert len(lint) == 1 and "--check-secrets" not in lint[0]["run"]
    issue = steps[-1]
    assert issue["if"] == "failure()"
    assert "${{" not in issue["run"]  # everything arrives through env:
    assert issue["env"]["ISSUE_TITLE"] == "Skill freshness lint is failing"
    assert "gh issue create" in issue["run"] and "gh issue comment" in issue["run"]


def test_every_action_the_workflow_uses_is_in_actions_lock():
    doc, _ = load_workflow()
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    listed = lock["workflows"][".github/workflows/skill-freshness.yml"]
    used = [s["uses"] for s in doc["jobs"]["freshness"]["steps"] if "uses" in s]
    assert used
    for uses in used:
        repo, ref = uses.rsplit("@", 1)
        key = f"{repo.lower()}@{ref}"
        assert key in lock["dependencies"], uses
        assert key in listed, uses
    assert sorted(listed) == sorted(f"{u.rsplit('@', 1)[0].lower()}@{u.rsplit('@', 1)[1]}"
                                    for u in used)
