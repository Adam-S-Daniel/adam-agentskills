#!/usr/bin/env python3
"""check_one_way_doors.py — guard this registry's one-way doors on a pull request.

Why this exists
----------------
AGENTS.md's "One-way doors get an adversarial round" was prose only. A 2026-11-09
review renamed the shipped skill fan-out-triage (folder, `name:`,
scripts/plugin_layout.json, impact entries) and every check passed: the layout
test compares the disk with plugin_layout.json, which the same pull request
edits, so nothing compared the change with what had already shipped. Nothing
checked that the evals loop's pull requests stay inside ADR 0021's limits
either. This script is the check for both (issue #133).

Check 1: a shipped name may not disappear unrecorded
------------------------------------------------------
A name is the marketplace's `name`, a plugin's `name` in
.claude-plugin/marketplace.json, or `<plugin>/<skill>` for a skill that has
`<plugin source>/skills/<skill>/SKILL.md` and, when that SKILL.md changed, a
frontmatter `name` equal to `<skill>`. A name present at the merge-base and
absent at head was removed, renamed or moved between plugins. Each one fails
the check unless the pull request body carries the line

    Adversarial-review: pass <https url>

and no `Adversarial-review: block` line. The URL is the recorded review: a
skills-evals removal reviewer's run (skills-evals#917) or any separately
prompted agent's report. A person or an agent may write it; removals stay
autonomous (owner decision 2026-11-09). docs/one-way-doors.md defines what a
review must cover and when it is done.

scripts/shipped_names.json is the ledger of every name that has shipped. It is
append-only: every name in it at the merge-base must still be in it at head.
A name that disappears must be in it at head, so a retired name is never
forgotten (a later skill reusing it would collide with stale locks and records
downstream). Detection does not depend on the ledger: names present at the
merge-base count as shipped whether or not the ledger lists them yet.

Check 2: an evals-loop pull request stays inside ADR 0021
----------------------------------------------------------
A pull request whose head branch starts with a prefix in scripts/loop_prs.json
(`draft-skill/`, `eval-improve/`, `retire-skill/`) merges with no human review
(ADR 0021), and _agent-guidance's eval-protected.js exempts it on the prefix
plus a Bot author. So here it must be:

- opened by the App login scripts/loop_prs.json names (user type `Bot`), from
  a branch in this repository, never a fork;
- about the one skill its branch names, in exactly one plugin, with the
  lane's kind: a create, an edit, or a removal of that SKILL.md;
- inside ADR 0021 Decision 3's paths: the skill's directory,
  docs/skill-impact.md, README.md, skills.lock, the plugin's two manifests
  with only `version` changed, and scripts/plugin_layout.json and
  scripts/shipped_names.json for a create or removal;
- in a plugin that runs no code at the merge-base or head (Decision 4): no
  hooks/ or bin/ directory, no settings.json, package.json or package lock,
  no `hooks` or `settings` key in a manifest or its marketplace entry;
- the only open loop pull request (Decision 5).

Config is read at the merge-base, so a pull request cannot loosen the rules
it is judged by; a file absent there (the pull request that adds it) is read
at head.

Inputs and trust
-----------------
Every file is read from the git object store (`git show <rev>:<path>`), never
the working tree, and diffed from `git merge-base <base> <head>` (see
check_skill_gates.py for why). The pull request's JSON and the open pull
request list come from the GitHub API through files the workflow writes; all
of it is untrusted data, parsed with json only. Output names repo-relative
paths and names, JSON-quoted, never file contents or the body.

Exit status: 0 pass, 1 a rule failed, 2 the check could not run.

Usage:
  python3 scripts/check_one_way_doors.py --base SHA --head SHA
      --pr-json PATH [--open-prs PATH]
"""

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

MARKETPLACE = ".claude-plugin/marketplace.json"
LEDGER = "scripts/shipped_names.json"
LOOP_CONFIG = "scripts/loop_prs.json"
LEDGER_KEYS = ("marketplace", "plugins", "skills")
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")
RUNTIME_KEYS = ("hooks", "settings")
RUNTIME_DIRS = ("hooks/", "bin/")
RUNTIME_FILES = frozenset({"settings.json", "package.json", "bun.lock", "bun.lockb",
                           "npm-shrinkwrap.json", "package-lock.json"})
KINDS = ("create", "edit", "remove")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REVIEW_PASS_RE = re.compile(r"^[ \t]*Adversarial-review:[ \t]*pass[ \t]+(https://\S+)[ \t]*$",
                            re.MULTILINE)
REVIEW_BLOCK_RE = re.compile(r"^[ \t]*Adversarial-review:[ \t]*block\b", re.MULTILINE)
_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "core.quotepath=off")


class CheckError(Exception):
    """The check could not run at all (exit 2)."""


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *_GIT_VERBATIM, *args],
                          capture_output=True, text=True, encoding="utf-8")


def merge_base(base: str, head: str) -> str:
    proc = _git("merge-base", base, head)
    if proc.returncode != 0 or not proc.stdout.strip():
        why = proc.stderr.strip() or f"exit {proc.returncode}"
        raise CheckError(f"git merge-base {base} {head} failed: {why}. The checkout may "
                         "be shallow (CI needs fetch-depth: 0) or a revision is unknown.")
    return proc.stdout.strip()


def show(rev: str, path: str) -> Optional[str]:
    """File content at rev, or None when the path is not a file there."""
    if _git("cat-file", "-e", f"{rev}:{path}").returncode != 0:
        return None
    proc = _git("show", f"{rev}:{path}")
    if proc.returncode != 0:
        raise CheckError(f"git show {rev}:{path} failed: {proc.stderr.strip()}")
    return proc.stdout


def blob_id(rev: str, path: str) -> Optional[str]:
    proc = _git("rev-parse", "--verify", "-q", f"{rev}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def files_under(rev: str, root: str) -> List[str]:
    """Every file path under root/ at rev (repo-relative)."""
    proc = _git("ls-tree", "-r", "--name-only", rev, "--", root + "/")
    if proc.returncode != 0:
        raise CheckError(f"git ls-tree {rev} {root} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.splitlines() if p]


def changed_paths(mb: str, head: str) -> List[str]:
    proc = _git("diff", "--no-renames", "--name-only", mb, head)
    if proc.returncode != 0:
        raise CheckError(f"git diff {mb} {head} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.splitlines() if p]


def _json_at(rev: str, path: str):
    text = show(rev, path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CheckError(f"{path} at {rev[:12]} is not valid JSON: {exc}") from None


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

def _local_root(source) -> Optional[str]:
    """`./plugins/x` -> `plugins/x`; None for a remote source or anything odd."""
    if not isinstance(source, str):
        return None
    norm = posixpath.normpath(source)
    if norm.startswith("/") or norm == ".." or norm.startswith("../") or norm == ".":
        return None
    return norm


def marketplace_entries(rev: str) -> Tuple[Optional[str], Dict[str, dict]]:
    data = _json_at(rev, MARKETPLACE)
    if not isinstance(data, dict):
        return None, {}
    entries = {}
    for entry in data.get("plugins") or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            entries[entry["name"]] = entry
    name = data.get("name") if isinstance(data.get("name"), str) else None
    return name, entries


def frontmatter_name(text: str) -> Optional[str]:
    """The SKILL.md frontmatter `name`, parsed as YAML; None when absent."""
    import yaml  # third-party; imported only when a SKILL.md changed

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            value = data.get("name") if isinstance(data, dict) else None
            return value.strip() if isinstance(value, str) else None
    return None


def present_names(rev: str, verify_against: Optional[str] = None) -> Dict[str, Set[str]]:
    """{marketplace, plugins, skills} present at rev. With verify_against (the
    merge-base), a SKILL.md whose blob differs from that rev's must also carry a
    frontmatter `name` equal to its directory, or the skill is not present."""
    name, entries = marketplace_entries(rev)
    names = {"marketplace": {name} if name else set(), "plugins": set(entries), "skills": set()}
    for plugin, entry in entries.items():
        root = _local_root(entry.get("source"))
        if root is None:
            continue
        prefix = root + "/skills/"
        for path in files_under(rev, root + "/skills"):
            rest = path[len(prefix):] if path.startswith(prefix) else ""
            skill, _, leaf = rest.partition("/")
            if leaf != "SKILL.md" or not skill:
                continue
            if verify_against is not None:
                if blob_id(rev, path) != blob_id(verify_against, path):
                    if frontmatter_name(show(rev, path) or "") != skill:
                        continue
            names["skills"].add(f"{plugin}/{skill}")
    return names


def ledger(rev: str) -> Optional[Dict[str, List[str]]]:
    data = _json_at(rev, LEDGER)
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != set(LEDGER_KEYS) or not all(
            isinstance(data[k], list) and all(isinstance(v, str) for v in data[k])
            for k in LEDGER_KEYS):
        raise CheckError(f"{LEDGER} at {rev[:12]} must be an object with exactly the "
                         f"list-of-strings keys {list(LEDGER_KEYS)}")
    return data


def review_line(body: Optional[str]) -> Tuple[Optional[str], bool]:
    """(the pass URL or None, whether a block line is present)."""
    body = body or ""
    match = REVIEW_PASS_RE.search(body)
    return (match.group(1) if match else None), bool(REVIEW_BLOCK_RE.search(body))


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _q(value: str) -> str:
    return json.dumps(value)


def check_names(mb: str, head: str, body: Optional[str]) -> List[str]:
    errors = []
    base_ledger = ledger(mb)
    head_ledger = ledger(head)
    if base_ledger is not None and head_ledger is None:
        return [f"{LEDGER} was deleted; it is append-only. Restore it."]
    if base_ledger is not None:
        for key in LEDGER_KEYS:
            for name in base_ledger[key]:
                if name not in head_ledger[key]:
                    errors.append(f"{LEDGER} lost {key} entry {_q(name)}; it is "
                                  "append-only, so a retired name stays listed.")
    before = present_names(mb)
    after = present_names(head, verify_against=mb)
    if not after["marketplace"]:
        errors.append(f"{MARKETPLACE} at head has no readable `name`.")
    lost = [(key, name) for key in LEDGER_KEYS for name in sorted(before[key] - after[key])]
    if lost:
        url, blocked = review_line(body)
        names = ", ".join(f"{key[:-1] if key != 'marketplace' else key} {_q(n)}"
                          for key, n in lost)
        if blocked:
            errors.append(f"Shipped names disappear ({names}) and the pull request body "
                          "records `Adversarial-review: block`. Address the findings, "
                          "then record a passing review.")
        elif url is None:
            errors.append(
                f"Shipped names disappear ({names}): a removal, rename or move between "
                "plugins is a one-way door. Run the adversarial review in "
                "docs/one-way-doors.md and put `Adversarial-review: pass <https url>` "
                "in the pull request body, then re-run this job.")
        else:
            print(f"one-way-doors: shipped names disappear ({names}); the body records "
                  f"a passing review at {_q(url)}.")
        recorded = head_ledger or {k: [] for k in LEDGER_KEYS}
        for key, name in lost:
            if name not in recorded[key]:
                errors.append(f"{_q(name)} disappears but is not in {LEDGER}'s "
                              f"`{key}` at head. Add it, so the retired name stays on "
                              "record.")
    return errors


def _plugin_runs_code(rev: str, plugin: str, root: str) -> List[str]:
    reasons = []
    for path in files_under(rev, root):
        rel = path[len(root) + 1:]
        if rel.startswith(RUNTIME_DIRS):
            reasons.append(f"{_q(path)} (a {rel.split('/', 1)[0]}/ directory)")
        elif rel in RUNTIME_FILES:
            reasons.append(f"{_q(path)} (a settings or package file)")
    for manifest in MANIFESTS:
        data = _json_at(rev, f"{root}/{manifest}")
        if isinstance(data, dict) and any(k in data for k in RUNTIME_KEYS):
            reasons.append(f"{_q(root + '/' + manifest)} has a hooks or settings key")
    _, entries = marketplace_entries(rev)
    entry = entries.get(plugin) or {}
    if any(k in entry for k in RUNTIME_KEYS):
        reasons.append(f"the marketplace entry for {_q(plugin)} has a hooks or settings key")
    return reasons


def check_loop_pr(mb: str, head: str, pr: dict, open_prs: Optional[list]) -> List[str]:
    config = _json_at(mb, LOOP_CONFIG)
    if config is None:
        config = _json_at(head, LOOP_CONFIG)
    if not isinstance(config, dict) or not isinstance(config.get("author"), str) \
            or not config["author"] or not isinstance(config.get("lanes"), dict) \
            or not all(v in KINDS for v in config["lanes"].values()):
        raise CheckError(f"{LOOP_CONFIG} is missing or malformed: it must name the loop's "
                         f"App login as `author` and map each branch prefix to one of "
                         f"{list(KINDS)} under `lanes`.")
    ref = ((pr.get("head") or {}).get("ref")) or ""
    lane = next((p for p in config["lanes"] if ref.startswith(p)), None)
    if lane is None:
        return []
    kind = config["lanes"][lane]
    skill = ref[len(lane):]
    errors = []
    print(f"one-way-doors: {_q(ref)} is an evals-loop pull request ({kind}); "
          "checking ADR 0021's limits.")

    user = pr.get("user") or {}
    if user.get("login") != config["author"] or user.get("type") != "Bot":
        errors.append(f"A {_q(lane)} branch merges without review only when the loop's App "
                      f"{_q(config['author'])} opens it; this one was opened by "
                      f"{_q(str(user.get('login')))}. Use another branch name.")
    head_repo = (((pr.get("head") or {}).get("repo")) or {}).get("full_name")
    base_repo = (((pr.get("base") or {}).get("repo")) or {}).get("full_name")
    if not head_repo or head_repo != base_repo:
        errors.append("An evals-loop pull request must come from a branch in this "
                      "repository, not a fork.")
    if not SKILL_NAME_RE.match(skill):
        return errors + [f"Branch {_q(ref)} does not name one skill after {_q(lane)}."]

    changed = changed_paths(mb, head)
    plugins = sorted({p.split("/")[1] for p in changed
                      if re.match(r"^plugins/[^/]+/skills/" + re.escape(skill) + r"/", p)})
    if len(plugins) != 1:
        return errors + [f"An evals-loop pull request changes one skill's directory in one "
                         f"plugin; this one touches {_q(skill)} in {len(plugins)} plugins."]
    plugin = plugins[0]
    root = f"plugins/{plugin}"
    skill_md = f"{root}/skills/{skill}/SKILL.md"
    at_base, at_head = blob_id(mb, skill_md), blob_id(head, skill_md)
    actual = ("create" if at_base is None and at_head is not None else
              "remove" if at_base is not None and at_head is None else
              "edit" if at_base is not None and at_base != at_head else None)
    if actual != kind:
        errors.append(f"A {_q(lane)} pull request must {kind} {_q(skill_md)}; this one "
                      f"{'leaves it unchanged' if actual is None else 'does a ' + actual}.")

    allowed = {"docs/skill-impact.md", "README.md", "skills.lock"}
    allowed |= {f"{root}/{m}" for m in MANIFESTS}
    if kind in ("create", "remove"):
        allowed |= {"scripts/plugin_layout.json", LEDGER}
    outside = [p for p in changed
               if p not in allowed and not p.startswith(f"{root}/skills/{skill}/")]
    for path in outside:
        errors.append(f"{_q(path)} is outside ADR 0021 Decision 3's scope for a "
                      f"{_q(lane)} pull request.")
    for manifest in MANIFESTS:
        path = f"{root}/{manifest}"
        if path not in changed:
            continue
        old, new = _json_at(mb, path), _json_at(head, path)
        if not isinstance(old, dict) or not isinstance(new, dict) or \
                {k: v for k, v in old.items() if k != "version"} != \
                {k: v for k, v in new.items() if k != "version"}:
            errors.append(f"{_q(path)} may change only its `version` in an evals-loop "
                          "pull request.")

    for rev in (mb, head):
        for reason in _plugin_runs_code(rev, plugin, root):
            errors.append(f"Plugin {_q(plugin)} runs code ({reason}), so ADR 0021 "
                          "Decision 4 never merges its skills automatically.")

    if open_prs is not None:
        number = pr.get("number")
        others = sorted(o.get("number") for o in open_prs
                        if isinstance(o, dict) and o.get("number") != number
                        and any(((o.get("head") or {}).get("ref") or "").startswith(p)
                                for p in config["lanes"]))
        if others:
            errors.append(f"Another evals-loop pull request is open ({others}); ADR 0021 "
                          "Decision 5 allows one at a time. Close one and re-run.")
    return errors


def run(base: str, head: str, pr: dict, open_prs: Optional[list]) -> List[str]:
    mb = merge_base(base, head)
    return check_names(mb, head, pr.get("body")) + check_loop_pr(mb, head, pr, open_prs)


def _escape(message: str) -> str:
    """Workflow-command data escaping, so no value can end the annotation."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise CheckError(f"cannot read {path}: {exc}") from None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--pr-json", required=True, help="the pull request, from the API")
    ap.add_argument("--open-prs", help="the open pull requests, from the API")
    args = ap.parse_args(argv)
    try:
        pr = _load(args.pr_json)
        if not isinstance(pr, dict):
            raise CheckError(f"{args.pr_json} is not a pull request object")
        open_prs = _load(args.open_prs) if args.open_prs else None
        if open_prs is not None and not isinstance(open_prs, list):
            raise CheckError(f"{args.open_prs} is not a list")
        errors = run(args.base, args.head, pr, open_prs)
    except CheckError as exc:
        print(f"one-way-doors: error: {exc}", file=sys.stderr)
        return 2
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for error in errors:
        print(f"::error::{_escape(error)}" if annotate else f"one-way-doors: FAIL: {error}")
    if not errors:
        print("one-way-doors: pass.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
