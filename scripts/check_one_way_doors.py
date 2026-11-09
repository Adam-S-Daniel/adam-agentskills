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

What is compared
-----------------
The base branch's tip (`--base`: the workflow passes the remote-tracking ref
of the base branch the API names now, so a retargeted pull request is judged
against its new base) against
the tree `git merge-tree --write-tree <base> <head>` produces: what `main`
would hold if the pull request merged now. Not head against a merge-base: a
branch built across a criss-cross history can make `git merge-base` return
the commit before a skill existed, so a deletion of it would look like no
change (adversarial round 1 on #134). A pull request that conflicts has no
merge result and exits 2 until it is resolved. Every file is read from the
git object store, never the working tree.

Check 1: shipped names change only with a recorded review
-----------------------------------------------------------
A name is the marketplace's `name`, a plugin's `name` in
.claude-plugin/marketplace.json, or `<plugin>/<skill>` for a skill that has
`plugins/<plugin>/skills/<skill>/SKILL.md` and, when that SKILL.md changed, a
frontmatter `name` equal to `<skill>`. A local plugin counts only while it is
listed once, its `source` is `./plugins/<plugin>` and both manifests name it,
and no `metadata.pluginRoot` moves it: a renamed bundle directory or manifest
`name` is a rename too (round 2 on #134). A name present at the base and absent
from the merge result was removed, renamed or moved between plugins. A name
that arrives but is already in the ledger (a skill by basename, in any
plugin) reuses a retired name. Either fails the check unless the pull request
body's visible text (not an HTML comment or a code block) has the line

    Adversarial-review: pass <https://github.com/... or https://claude.ai/...>

and no `Adversarial-review: block` line. The link is the recorded review: a
skills-evals removal reviewer's run (skills-evals#917) or any separately
prompted agent's report. A person or an agent may write it; removals stay
autonomous (owner decision 2026-11-09). The line is a record, not proof:
docs/one-way-doors.md defines what a review must cover and when it is done.

scripts/shipped_names.json is the ledger of every name that has shipped. It
is append-only, and a name that disappears must be in it in the merge result,
so a retired name stays on record. Detection does not depend on the ledger:
names present at the base count as shipped whether or not it lists them yet.

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
- when armed to auto-merge, not adding or changing `hooks` or
  `allowed-tools` in the skill's frontmatter (Decision 4 as the owner
  narrowed it on 2026-11-03: text-only changes auto-merge in any plugin, and
  plugin-runtime-review already holds a skill's own hooks/ or bin/; it does
  not read frontmatter, #135);
- the only ready (non-draft) loop pull request the App has open from this
  repository (Decision 5). Forks and people cannot hold the slot.

Config is read at the base, so a pull request cannot loosen the rules it is
judged by; a file absent there (the pull request that adds it) is read from
the merge result.

Inputs and trust
-----------------
The pull request's JSON and the open pull request list come from the GitHub
API through files the workflow writes; all of it is untrusted data, parsed
with json (and the body with markdown-it-py) only. Output names repo-relative
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
SKILL_RUNTIME_KEYS = ("hooks", "allowed-tools")
KINDS = ("create", "edit", "remove")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: The review record must link a page on one of these hosts: a GitHub run,
#: comment or report, or a claude.ai artifact or session.
REVIEW_PASS_RE = re.compile(
    r"^[ \t]*Adversarial-review:[ \t]*pass[ \t]+"
    r"(https://(?:github\.com|claude\.ai)/\S+)[ \t]*$", re.MULTILINE)
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


def resolve(rev: str) -> str:
    proc = _git("rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"{rev} is not a commit here. The checkout may be shallow "
                         "(CI needs fetch-depth: 0).")
    return proc.stdout.strip()


def merge_result(base: str, head: str) -> str:
    """The tree `main` would have if this pull request merged now. Judging that,
    not head against a merge-base, is what stops a branch built across a
    criss-cross history from choosing a merge-base that hides a deletion."""
    proc = _git("merge-tree", "--write-tree", "--no-messages", base, head)
    tree = proc.stdout.split("\n", 1)[0].strip()
    if proc.returncode == 1:
        raise CheckError("the pull request conflicts with its base branch, so there is "
                         "no merge result to check. Resolve the conflict and re-run.")
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise CheckError(f"git merge-tree {base} {head} failed: {proc.stderr.strip()}")
    return tree


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


def changed_paths(base: str, merged: str) -> List[str]:
    proc = _git("diff", "--no-renames", "--name-only", base, merged)
    if proc.returncode != 0:
        raise CheckError(f"git diff {base} {merged} failed: {proc.stderr.strip()}")
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


def marketplace_entries(rev: str) -> Tuple[Optional[str], Dict[str, dict], Set[str], object]:
    """(marketplace name, {plugin name: entry}, names listed more than once,
    metadata.pluginRoot or None)."""
    data = _json_at(rev, MARKETPLACE)
    if not isinstance(data, dict):
        return None, {}, set(), None
    entries, dupes = {}, set()
    for entry in data.get("plugins") or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            if entry["name"] in entries:
                dupes.add(entry["name"])
            entries[entry["name"]] = entry
    name = data.get("name") if isinstance(data.get("name"), str) else None
    meta = data.get("metadata")
    root = meta.get("pluginRoot") if isinstance(meta, dict) else None
    return name, entries, dupes, root


def frontmatter(text: str) -> dict:
    """The SKILL.md frontmatter, parsed as YAML; {} when absent or unparsable."""
    import yaml  # third-party; imported only when a SKILL.md changed

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def frontmatter_name(text: str) -> Optional[str]:
    value = frontmatter(text).get("name")
    return value.strip() if isinstance(value, str) else None


def present_names(rev: str, verify_against: Optional[str] = None,
                  bad: Optional[List[str]] = None) -> Dict[str, Set[str]]:
    """{marketplace, plugins, skills} present at rev.

    A plugin counts only when its name is listed once and, for a local plugin,
    its `source` is `./plugins/<name>` (the lock's `<bundle>` is that
    directory, and the bootstrap reads `plugins/{bundle}/skills`) and both
    manifests carry the same `name` (Codex and Agent Plugins read it there).
    A `metadata.pluginRoot` other than the repository root moves every local
    source, so no local plugin counts. Anything else is a rename in disguise.
    With verify_against (the base), a SKILL.md whose blob differs from that
    rev's must also carry a frontmatter `name` equal to its directory; one that
    does not is appended to `bad` (the skill would load under another name, or
    none, so it can neither keep a name nor arrive unseen)."""
    name, entries, dupes, plugin_root = marketplace_entries(rev)
    names = {"marketplace": {name} if name else set(), "plugins": set(), "skills": set()}
    moved = plugin_root not in (None, "", ".", "./")
    for plugin, entry in entries.items():
        if plugin in dupes:
            continue
        source = entry.get("source")
        if not isinstance(source, str):
            names["plugins"].add(plugin)  # a remote plugin: its name is all we own
            continue
        root = _local_root(source)
        if moved or root != f"plugins/{plugin}":
            continue
        manifests = [_json_at(rev, f"{root}/{f}") for f in MANIFESTS]
        if not all(isinstance(m, dict) and m.get("name") == plugin for m in manifests):
            continue
        names["plugins"].add(plugin)
        prefix = root + "/skills/"
        for path in files_under(rev, root + "/skills"):
            rest = path[len(prefix):] if path.startswith(prefix) else ""
            skill, _, leaf = rest.partition("/")
            if leaf != "SKILL.md" or not skill:
                continue
            if verify_against is not None:
                if blob_id(rev, path) != blob_id(verify_against, path):
                    if frontmatter_name(show(rev, path) or "") != skill:
                        if bad is not None:
                            bad.append(path)
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
    """(the pass URL or None, whether a block line is present), read only from
    the body's rendered paragraph text: a line inside an HTML comment or a code
    block is not a record anyone would see, so it never counts."""
    from markdown_it import MarkdownIt  # third-party; only when a name is lost

    parts = []
    for token in MarkdownIt("commonmark").parse(body or ""):
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type in ("softbreak", "hardbreak"):
                parts.append("\n")
            elif child.type in ("text", "code_inline"):
                parts.append(child.content)
            # html_inline (a comment inside a paragraph) is hidden: dropped.
        parts.append("\n")
    visible = "".join(parts)
    match = REVIEW_PASS_RE.search(visible)
    return (match.group(1) if match else None), bool(REVIEW_BLOCK_RE.search(visible))


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _q(value: str) -> str:
    return json.dumps(value)


def _basename(key: str, name: str) -> str:
    return name.split("/", 1)[1] if key == "skills" else name


def check_names(base: str, merged: str, body: Optional[str]) -> List[str]:
    """Names at the base tip against names in the merge result."""
    errors = []
    base_ledger = ledger(base)
    head_ledger = ledger(merged)
    if base_ledger is not None and head_ledger is None:
        return [f"{LEDGER} was deleted; it is append-only. Restore it."]
    if base_ledger is not None:
        for key in LEDGER_KEYS:
            for name in base_ledger[key]:
                if name not in head_ledger[key]:
                    errors.append(f"{LEDGER} lost {key} entry {_q(name)}; it is "
                                  "append-only, so a retired name stays listed.")
    before = present_names(base)
    bad: List[str] = []
    after = present_names(merged, verify_against=base, bad=bad)
    for path in bad:
        errors.append(f"{_q(path)} has no parsable frontmatter `name` equal to its "
                      "directory. A skill's name is its directory name; fix the "
                      "frontmatter (or rename both, which is a one-way door).")
    if not after["marketplace"]:
        errors.append(f"{MARKETPLACE} in the merge result has no readable `name`.")
    lost = [(key, name) for key in LEDGER_KEYS for name in sorted(before[key] - after[key])]
    # A name that arrives but has shipped before (by basename, for a skill, so
    # a retired skill re-created in another plugin counts) reuses a retired name:
    # stale locks and records keyed by it would now point at something else.
    recorded_base = base_ledger or {k: [] for k in LEDGER_KEYS}
    reused = [(key, name) for key in LEDGER_KEYS for name in sorted(after[key] - before[key])
              if _basename(key, name) in {_basename(key, n) for n in recorded_base[key]}]
    doors = [("disappear", lost), ("reuse a retired name", reused)]
    doors = [(what, items) for what, items in doors if items]
    if doors:
        url, blocked = review_line(body)
        summary = "; ".join(
            what + ": " + ", ".join(f"{key[:-1] if key != 'marketplace' else key} {_q(n)}"
                                    for key, n in items)
            for what, items in doors)
        if blocked:
            errors.append(f"Shipped names change ({summary}) and the pull request body "
                          "records `Adversarial-review: block`. Address the findings, "
                          "then record a passing review.")
        elif url is None:
            errors.append(
                f"Shipped names change ({summary}): a removal, rename, move between "
                "plugins or reuse of a retired name is a one-way door. Run the "
                "adversarial review in docs/one-way-doors.md and put "
                "`Adversarial-review: pass <url>` (a github.com or claude.ai link) in the "
                "pull request body, outside comments and code blocks, then re-run this job.")
        else:
            print(f"one-way-doors: shipped names change ({summary}); the body records "
                  f"a passing review at {_q(url)}.")
        recorded = head_ledger or {k: [] for k in LEDGER_KEYS}
        for key, name in lost:
            if name not in recorded[key]:
                errors.append(f"{_q(name)} disappears but is not in {LEDGER}'s "
                              f"`{key}` in the merge result. Add it, so the retired name "
                              "stays on record.")
    return errors


def check_loop_pr(base: str, merged: str, pr: dict, open_prs: Optional[list]) -> List[str]:
    config = _json_at(base, LOOP_CONFIG)
    if config is None:
        config = _json_at(merged, LOOP_CONFIG)
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

    changed = changed_paths(base, merged)
    plugins = sorted({p.split("/")[1] for p in changed
                      if re.match(r"^plugins/[^/]+/skills/" + re.escape(skill) + r"/", p)})
    if len(plugins) != 1:
        return errors + [f"An evals-loop pull request changes one skill's directory in one "
                         f"plugin; this one touches {_q(skill)} in {len(plugins)} plugins."]
    plugin = plugins[0]
    root = f"plugins/{plugin}"
    skill_md = f"{root}/skills/{skill}/SKILL.md"
    at_base, at_head = blob_id(base, skill_md), blob_id(merged, skill_md)
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
        old, new = _json_at(base, path), _json_at(merged, path)
        if not isinstance(old, dict) or not isinstance(new, dict) or \
                {k: v for k, v in old.items() if k != "version"} != \
                {k: v for k, v in new.items() if k != "version"}:
            errors.append(f"{_q(path)} may change only its `version` in an evals-loop "
                          "pull request.")

    # Decision 4, as the owner narrowed it on 2026-11-03 (skills-evals ADR
    # 0005, "Text-only skill changes addendum"): a text-only change auto-merges
    # in any plugin; one plugin-runtime-review would hold (a skill's own hooks/
    # or bin/, or a hook naming a path in the skill) is held by that required
    # gate already. What that gate does not see (#135) is the skill's own
    # frontmatter: `hooks` run code and `allowed-tools` skips permission
    # prompts. A pull request armed to auto-merge may not add or change them;
    # a person may still merge one by hand.
    base_fm = frontmatter(show(base, skill_md) or "") if at_base else {}
    head_fm = frontmatter(show(merged, skill_md) or "") if at_head else {}
    changed_keys = [k for k in SKILL_RUNTIME_KEYS
                    if k in head_fm and head_fm.get(k) != base_fm.get(k)]
    if changed_keys and pr.get("auto_merge"):
        errors.append(f"{_q(skill_md)} adds or changes frontmatter {changed_keys} and the "
                      "pull request is armed to auto-merge; ADR 0021 Decision 4 never "
                      "merges a change in what a skill runs or may run automatically. "
                      "Disarm it for a person to review.")

    # A draft is never auto-merged, and Decision 5 lets the draft-skill gate
    # open one while another loop PR is open, so the slot rule skips drafts;
    # `ready_for_review` re-runs this when one is marked ready.
    if open_prs is not None and not pr.get("draft"):
        number = pr.get("number")
        # Only the loop's own ready pull requests hold the slot: a fork or a
        # person can name a branch `retire-skill/x`, and the loop opens drafts
        # on purpose (Decisions 4 and 5), so counting those would let anyone
        # stall the loop and fail the loop's own in-flight pull request.
        others = sorted(
            o.get("number") for o in open_prs
            if isinstance(o, dict) and o.get("number") != number and not o.get("draft")
            and (o.get("user") or {}).get("login") == config["author"]
            and (o.get("user") or {}).get("type") == "Bot"
            and (((o.get("head") or {}).get("repo")) or {}).get("full_name") == base_repo
            and any(((o.get("head") or {}).get("ref") or "").startswith(p)
                    for p in config["lanes"]))
        if others:
            errors.append(f"Another evals-loop pull request is open ({others}); ADR 0021 "
                          "Decision 5 allows one at a time. Close one and re-run.")
    return errors


def run(base: str, head: str, pr: dict, open_prs: Optional[list]) -> List[str]:
    base = resolve(base)
    merged = merge_result(base, resolve(head))
    return check_names(base, merged, pr.get("body")) + check_loop_pr(base, merged, pr, open_prs)


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
