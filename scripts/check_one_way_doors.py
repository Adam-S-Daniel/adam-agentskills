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
frontmatter `name` equal to `<skill>` that Claude Code reads the same way (no
`---` inside a line, duplicate key, key-shaped line inside a value, or other
line at column 0 that is not a key). A SKILL.md counts only as a regular
file, not a symlink or gitlink, and not beside a case variant (skill.md)
that overwrites it on a case-insensitive file system. A local plugin counts only while it is
listed once, its `source` is `./plugins/<plugin>` and both manifests name it,
and no `metadata.pluginRoot` moves it: a renamed bundle directory or manifest
`name` is a rename too (round 2 on #134). A name present at the base and absent
from the merge result was removed, renamed or moved between plugins. A name
that arrives but is already in the ledger (a skill by basename, in any
plugin) reuses a retired name. Either fails the check unless the pull request
body's visible paragraph text (not an HTML comment or a code block) has the
line

    Adversarial-review: pass <https://github.com/... or https://claude.ai/...>

and no `Adversarial-review: block` line anywhere but an HTML comment (code,
headings, tables and HTML blocks included). A changed marketplace `renames`
map or any changed .gitattributes (git archive applies its rules) needs the
same line. The link is the recorded review: a
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
- text only, draft or not (a draft marked ready and armed at once could
  merge on its old green result): in SKILL.md's frontmatter only the
  `description` lines change (a new skill: only name, description, license
  and compatibility), with no comment elsewhere in it and nothing Claude Code
  could read differently from this check; in the skill directory only
  SKILL.md, PURPOSE.md or lowercase .md files outside dot-directories, as
  regular files, no two of them one file to a case-insensitive file system
  (Decision 4 as the owner narrowed it on 2026-11-03: text-only changes
  auto-merge in any plugin; plugin-runtime-review gates only a plugin's own
  hooks/ and bin/ and does not read frontmatter, #135). A person who wants
  such a change takes it to a branch outside the loop's prefixes;
- when it changes skills.lock, changing only its own skill's entry and
  pinning a commit in its own history where that skill and every
  .gitattributes match the merge result (the bootstrap installs from the
  lock's ref, not main);
- a create or removal appending nothing to scripts/shipped_names.json but
  its own `<plugin>/<skill>`;
- unless it is a draft, the oldest ready loop pull request the App has open
  from this repository (Decision 5). Forks and people cannot hold the slot,
  and two opened in the same seconds cannot block each other.

Config is read at the base, and CI runs the base branch's copy of this
script, so a pull request cannot loosen the rules it is judged by by editing
them; a file
absent there (the pull request that adds it) is read from the merge result.
A loop pull request may change only the paths its lanes write; a bash-only
CI step refuses anything else before the job runs any of its code (the pip
install, the skill gates), since that code could make this check pass. A
pull request can still edit ci.yml to drop the step, as it can for every
check in that job; the loop's App has no `workflows` permission to push one.

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
#: A block line counts wherever a reader could see it (a list item, a quote,
#: a heading, a table row, bold text, code, <details>), in any case; only HTML
#: comments, as the Markdown parser finds them, hide it. It must
#: start its line, as skills-evals' removal reviewer writes it, so a finding
#: that merely quotes the phrase mid-sentence does not fail a passing review.
REVIEW_BLOCK_RE = re.compile(r"^[ \t>*_+\-0-9.)#|\[\]xX]*Adversarial-review\W*block\b",
                             re.MULTILINE | re.IGNORECASE)
WAIVERS = "scripts/skills_waivers.yml"
LENGTH_WAIVER_CLASS = "length-limit"
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
    # Bytes, decoded without newline translation: a `\r` must reach the
    # ambiguity test as itself (round 13).
    proc = subprocess.run(["git", *_GIT_VERBATIM, "show", f"{rev}:{path}"],
                          capture_output=True)
    if proc.returncode != 0:
        raise CheckError(f"git show {rev}:{path} failed: "
                         f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def blob_id(rev: str, path: str) -> Optional[str]:
    proc = _git("rev-parse", "--verify", "-q", f"{rev}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def files_under(rev: str, root: str) -> List[str]:
    """Every file path under root/ at rev (repo-relative)."""
    proc = _git("ls-tree", "-r", "--name-only", rev, "--", root + "/")
    if proc.returncode != 0:
        raise CheckError(f"git ls-tree {rev} {root} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.splitlines() if p]


def _mode(rev: str, path: str) -> Optional[str]:
    proc = _git("ls-tree", "-z", rev, "--", path)
    if proc.returncode != 0:
        raise CheckError(f"git ls-tree {rev[:12]} {path} failed: {proc.stderr.strip()}")
    entry = proc.stdout.split("\0")[0]
    return entry.split(" ", 1)[0] if entry else None


#: Claude Code skips a plugin SKILL.md larger than this (round 14).
SKILL_MD_MAX_BYTES = 1048576


def _size(rev: str, path: str) -> int:
    proc = _git("cat-file", "-s", f"{rev}:{path}")
    if proc.returncode != 0:
        raise CheckError(f"git cat-file -s {rev[:12]}:{path} failed: {proc.stderr.strip()}")
    return int(proc.stdout.strip())


def _skill_dirs(rev: str, bundle: str) -> Set[str]:
    proc = _git("ls-tree", "-z", "--name-only", f"{rev}:plugins/{bundle}/skills")
    return set(filter(None, proc.stdout.split("\0"))) if proc.returncode == 0 else set()


def _tree(rev: str, path: str) -> Optional[str]:
    proc = _git("rev-parse", "-q", "--verify", f"{rev}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def _lock_matches_merge(base: str, merged: str, head: Optional[str], key: str,
                        removed: bool) -> List[str]:
    """A loop pull request re-pins skills.lock to its own content commit, and
    the bootstrap installs from that ref, not from main. So only its own skill's
    entry may change, that skill must be the same at the ref as in the merge
    result, and the ref must apply the same .gitattributes (which `git archive`
    follows) as the merge result."""
    old, new = _json_at(base, "skills.lock"), _json_at(merged, "skills.lock")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return ["skills.lock is not a JSON object at the base and in the merge result."]
    moving = ("ref", "generated_from", "skills")
    keep = lambda d: {k: v for k, v in d.items() if k not in moving}  # noqa: E731
    if keep(old) != keep(new):
        return ["skills.lock may change only its `ref`, `generated_from` and `skills` in "
                "an evals-loop pull request."]
    ref = new.get("ref")
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{40}", ref):
        return ["skills.lock's `ref` is not a full commit sha."]
    if "generated_from" in new and new["generated_from"] != ref:
        return ["skills.lock's `generated_from` must equal its `ref`."]
    if head is None or _git("merge-base", "--is-ancestor", ref, head).returncode != 0:
        return [f"skills.lock's `ref` {ref[:12]} is not in this pull request's history."]
    old_skills, new_skills = old.get("skills"), new.get("skills")
    if not isinstance(old_skills, dict) or not isinstance(new_skills, dict):
        return ["skills.lock has no `skills` map."]
    errors = []
    others = sorted(k for k in set(old_skills) | set(new_skills)
                    if k != key and old_skills.get(k) != new_skills.get(k))
    if others:
        errors.append(f"skills.lock changes entries other than {_q(key)}: {others}.")
    if removed:
        if key in new_skills:
            errors.append(f"skills.lock still locks {_q(key)}, which this pull request removes.")
    elif key in new_skills or key in old_skills:
        plugin, _, skill = key.partition("/")
        path = f"plugins/{plugin}/skills/{skill}"
        if key not in new_skills or _tree(ref, path) != _tree(merged, path):
            errors.append(f"skills.lock pins {ref[:12]}, whose {_q(path)} differs from the "
                          "merge result's; the lock must pin the content that merges.")
    # The bootstrap installs every locked skill from `ref`, so every other
    # skill, and each bundle's set of skills, must be as at the old ref
    # (round 14: a ref where another skill was deleted).
    old_ref = old.get("ref")
    if not isinstance(old_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", old_ref):
        errors.append("the base's skills.lock `ref` is not a full commit sha.")
    else:
        own_plugin, _, own_skill = key.partition("/")
        for other in sorted(k for k in new_skills if k != key):
            plugin, _, skill = other.partition("/")
            path = f"plugins/{plugin}/skills/{skill}"
            if _tree(old_ref, path) != _tree(ref, path):
                errors.append(f"skills.lock pins {ref[:12]}, where {_q(path)} differs from "
                              "the base's pinned commit.")
        for bundle in sorted({k.partition("/")[0] for k in set(old_skills) | set(new_skills)}):
            drop = {own_skill} if bundle == own_plugin else set()
            if _skill_dirs(old_ref, bundle) - drop != _skill_dirs(ref, bundle) - drop:
                errors.append(f"skills.lock pins {ref[:12]}, where plugins/{bundle}/skills "
                              "holds other skills than the base's pinned commit.")
    attrs = lambda rev: {p: _tree(rev, p) for p in _gitattributes(rev)}  # noqa: E731
    if attrs(ref) != attrs(merged):
        errors.append(f"skills.lock pins {ref[:12]}, whose .gitattributes differ from the "
                      "merge result's, so `git archive` would export something else.")
    return errors


def _path_hazards(rev: str) -> Set[str]:
    """Under plugins/ and .claude-plugin/: each non-ASCII path, and each pair
    of distinct paths or directories that fold to one name."""
    proc = _git("ls-tree", "-r", "-z", "--name-only", "--full-tree", rev)
    if proc.returncode != 0:
        raise CheckError(f"git ls-tree {rev[:12]} failed: {proc.stderr.strip()}")
    paths = [p for p in proc.stdout.split("\0")
             if p.startswith("plugins/") or p.startswith(".claude-plugin/")]
    names = set(paths)
    for path in paths:
        parts = path.split("/")
        names.update("/".join(parts[:i]) for i in range(1, len(parts)))
    hazards = {f"non-ASCII {n}" for n in names if not n.isascii()}
    by_fold: Dict[str, Set[str]] = {}
    for name in names:
        by_fold.setdefault(_fold(name), set()).add(name)
    hazards |= {" = ".join(sorted(group)) for group in by_fold.values() if len(group) > 1}
    # Claude Code names a plugin skill by its folder with every character
    # outside [a-zA-Z0-9_-] turned into `-`, and keeps duplicates, so a folder
    # such as `fan out-triage` takes over `fan-out-triage` (round 17).
    hazards |= {f"skill folder {n}" for n in names
                if re.fullmatch(r"plugins/[^/]+/skills/[^/]+", n)
                and (not SKILL_NAME_RE.match(n.rsplit("/", 1)[1])
                     or _WINDOWS_RESERVED_RE.match(n.rsplit("/", 1)[1]))}
    return hazards


def _gitattributes(rev: str) -> List[str]:
    proc = _git("ls-tree", "-r", "-z", "--name-only", "--full-tree", rev)
    if proc.returncode != 0:
        raise CheckError(f"git ls-tree {rev[:12]} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if posixpath.basename(p) == ".gitattributes"]


def changed_paths(base: str, merged: str) -> List[str]:
    proc = _git("diff", "--no-renames", "--name-only", base, merged)
    if proc.returncode != 0:
        raise CheckError(f"git diff {base} {merged} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.splitlines() if p]


def _strict_pairs(pairs):
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"duplicate keys {dupes}")
    return dict(pairs)


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def _json_at(rev: str, path: str):
    """Parsed as strictly as Claude Code's JSON.parse: no NaN or Infinity
    (round 14), and no duplicate keys (parsers disagree on which one wins)."""
    text = show(rev, path)
    if text is None:
        return None
    try:
        return json.loads(text, parse_constant=_no_constant, object_pairs_hook=_strict_pairs)
    except ValueError as exc:
        raise CheckError(f"{path} at {rev[:12]} is not valid JSON: {exc}") from None


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

def _local_root(source) -> Optional[str]:
    """`./plugins/x` -> `plugins/x`; None for a remote source or anything odd.
    Claude Code accepts a relative source only when it starts with `./`; any
    other string makes the entry unsupported (round 16)."""
    if not isinstance(source, str) or not source.startswith("./"):
        return None
    norm = posixpath.normpath(source)
    if norm.startswith("/") or norm == ".." or norm.startswith("../") or norm == ".":
        return None
    return norm


#: Marketplace entry fields Claude Code types strictly; an entry with any of
#: them mistyped is stubbed as unsupported, so its plugin will not install
#: (round 16).
_ENTRY_TYPES = {"description": str, "version": str, "category": str, "homepage": str,
                "repository": str, "license": str, "keywords": list, "tags": list,
                "strict": bool, "defaultEnabled": bool, "author": dict}


_SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _remote_pin_ok(source: dict) -> bool:
    """A remote source's pin as Claude Code's schema takes it (round 18)."""
    return (("ref" not in source or isinstance(source["ref"], str))
            and ("sha" not in source or (isinstance(source["sha"], str)
                                         and bool(re.fullmatch(r"[a-f0-9]{40}", source["sha"])))))


def _routine_fields_ok(d: dict) -> bool:
    """The fields a routine change may touch, in the shapes Claude Code takes."""
    return (("version" not in d
             or (isinstance(d["version"], str) and bool(_SEMVER_RE.fullmatch(d["version"]))))
            and ("description" not in d
                 or (isinstance(d["description"], str) and d["description"].strip() != ""))
            and ("keywords" not in d or (isinstance(d["keywords"], list) and all(
                isinstance(k, str) and k.strip() for k in d["keywords"]))))


def _config_shape(data, is_marketplace: bool):
    """A marketplace or manifest with the routinely edited fields taken out
    (a version, a description, keywords, a remote plugin's pin), or None when
    one of those has a shape Claude Code would refuse. Two configs with equal
    shapes differ only in routine edits (round 17)."""
    if not isinstance(data, dict):
        return ("unreadable", repr(data))
    routine = ("version", "description", "keywords")
    def strip(d):
        return {k: v for k, v in d.items() if k not in routine}
    if not is_marketplace:
        # Serialized, so `true` and `1` differ as they do to Claude Code.
        return json.dumps(strip(data), sort_keys=True) if _routine_fields_ok(data) else None
    if not _routine_fields_ok(data):
        return None  # a root field Claude Code refuses refuses the marketplace (round 18)
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return json.dumps(data, sort_keys=True)
    shaped = []
    for entry in plugins:
        if not isinstance(entry, dict):
            shaped.append(entry)
            continue
        if not _routine_fields_ok(entry):
            return None
        entry = strip(entry)
        if isinstance(entry.get("source"), dict):
            if not _remote_pin_ok(entry["source"]):
                return None
            entry["source"] = {k: v for k, v in entry["source"].items() if k not in ("ref", "sha")}
        shaped.append(entry)
    return json.dumps({**strip(data), "plugins": shaped}, sort_keys=True)


def _entry_types_ok(entry: dict) -> bool:
    for key, kind in _ENTRY_TYPES.items():
        if key in entry and not isinstance(entry[key], kind):
            return False
    return all(isinstance(v, str) for key in ("keywords", "tags") for v in entry.get(key) or [])


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

    text = text[1:] if text.startswith("\ufeff") else text  # Claude Code strips one
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


#: Control characters (C0 except tab and newline, DEL, C1), the Unicode line
#: and paragraph separators, and the byte-order mark. Every range is
#: deliberate: each is a run of control characters.
_ODD_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(?:[ \t]|$)")
#: How Claude Code finds a SKILL.md's frontmatter: it stops at the first
#: `---` anywhere, even mid-line, where frontmatter() stops at a line that is
#: only `---`. A file the two read differently can hide a key from this check.
_CC_FRONTMATTER_RE = re.compile(r"---\s*\n([\s\S]*?)---\s*\n?")


def frontmatter_is_ambiguous(text: str) -> bool:
    """True when Claude Code and this check could read different frontmatter:
    the two ways of finding its end disagree, or the YAML does not load
    strictly (a duplicate key, which PyYAML resolves to the last value and
    Claude Code's early end can resolve to the first)."""
    import yaml

    text = text[1:] if text.startswith("\ufeff") else text
    if text.startswith("\ufeff"):
        return True  # a second mark: Claude Code then finds no frontmatter
    head = _CC_FRONTMATTER_RE.match(text)
    if head and _ODD_CHAR_RE.search(head.group(0)):
        # PyYAML breaks lines at U+0085, U+2028 and U+2029 and strips other
        # controls where Bun's YAML (Claude Code's) rejects the whole block.
        return True
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return _CC_FRONTMATTER_RE.match(text) is not None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    match = _CC_FRONTMATTER_RE.match(text)
    if end is None or match is None:
        return True
    region = "\n".join(lines[1:end])
    if match.group(1).rstrip("\n") != region.rstrip("\n"):
        return True

    class _Strict(yaml.SafeLoader):
        pass

    def _mapping(loader, node, deep=False):
        keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
        if len(keys) != len(set(map(repr, keys))):
            raise yaml.constructor.ConstructorError(None, None, "duplicate key",
                                                    node.start_mark)
        return loader.construct_mapping(node, deep=deep)

    _Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
    try:
        data = yaml.load(region, Loader=_Strict)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError:
        return True
    # Claude Code's YAML parser is stricter than PyYAML (it rejects a quoted
    # scalar continued at column 0) and then repairs the text line by line, so
    # a line that looks like a top-level key is one to it. Every such line must
    # be a top-level key here too, once each.
    # Any other line at column 0 (a comment, a quoted key) can end a value for
    # one parser and not the other, so it is ambiguous too.
    lines = region.split("\n")
    if any(line[:1].strip() and not _KEY_LINE_RE.match(line) for line in lines):
        return True
    shaped = [m.group(1) for m in (_KEY_LINE_RE.match(line) for line in lines) if m]
    return not isinstance(data, dict) or sorted(shaped) != sorted(map(str, data))


def frontmatter_blocks(text: str) -> Dict[str, List[str]]:
    """{top-level key: its raw lines}, for frontmatter that is not ambiguous
    (so every column-0 line starts a key, once)."""
    lines = (text[1:] if text.startswith("\ufeff") else text).split("\n")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    blocks: Dict[str, List[str]] = {}
    current = None
    for line in lines[1:end]:
        match = _KEY_LINE_RE.match(line)
        if match:
            current = match.group(1)
            blocks[current] = []
        if current is not None:
            blocks[current].append(line)
    return blocks


#: A comment after a value: Claude Code's YAML repair (run only when its
#: first parse fails, which a change elsewhere can cause) quotes a value whole,
#: comment included, so `allowed-tools: Bash # *` would become `*` to it.
_COMMENT_RE = re.compile(r"""(?:^|[\s'"])#""")
_BLOCK_HEADER_RE = re.compile(r"^[>|][+-]?$")
#: Keys a new skill from the loop may have.
LOOP_NEW_SKILL_KEYS = ("name", "description", "license", "compatibility")


def _description_shape_ok(block: List[str]) -> bool:
    """The description in a shape Bun's YAML (Claude Code's) and PyYAML read
    alike: a block header with nothing after it and indented lines, a quoted
    scalar complete on its line with nothing after the closing quote, or a
    one-line plain scalar (round 12: `'x'# y` drops Claude Code's whole
    frontmatter)."""
    import json as _json
    value = block[0].split(":", 1)[1].strip(" \t")
    rest = block[1:]
    if _BLOCK_HEADER_RE.match(value):
        return all(not line.strip() or line.startswith("  ") for line in rest)
    if any(line.strip() for line in rest):
        return False
    if value.startswith('"'):
        try:
            return isinstance(_json.loads(value), str)
        except ValueError:
            return False
    if value.startswith("'"):
        return re.fullmatch(r"'(?:[^']|'')*'", value) is not None
    leading_ok = value[:1] not in "-?:" or (len(value) > 1 and not value[1].isspace())
    return bool(value) and value[0] not in "[]{}&*!|>%@`#," and leading_ok \
        and " #" not in value \
        and ": " not in value


#: What Claude Code runs from a skill body when skill shell execution is on:
#: `!` then a backtick span after any (Unicode) space or at a line start, and
#: a ```! fence anywhere. Matched as Claude Code does, not as a grep would.
#: Any `!` and backtick not inside a code span's opening backtick is refused,
#: closed or not: Claude Code appends the arguments to the body, and a
#: backtick in them closes an open span (round 15).
_SKILL_SHELL_RES = (re.compile(r"(?<!`)!`"), re.compile(r"```!"))
#: Only the placeholders Claude Code substitutes. A made-up `${...}` stays as
#: written, so removing it would hide what it encloses (round 14).
_PLACEHOLDER_RE = re.compile(
    r"\$ARGUMENTS(?:\[\d+\])?|\$\d+|\$\{(?:CLAUDE_SKILL_DIR|CLAUDE_SESSION_ID|CLAUDE_EFFORT"
    r"|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA|CLAUDE_PROJECT_DIR|user_config\.[\w.-]+)\}")
#: Files Claude Code or Codex may load as project memory from a directory
#: they read (round 15), whatever the case on a case-insensitive checkout.
_MEMORY_FILE_NAMES = {"claude.md", "claude.local.md", "agents.md"}
#: Names Windows will not create, whatever the extension (git refuses them).
_WINDOWS_RESERVED_RE = re.compile(r"^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?$",
                                  re.IGNORECASE)


def loop_frontmatter_errors(base_text: Optional[str], head_text: str) -> List[str]:
    """What an evals-loop pull request may do to a SKILL.md's frontmatter:
    rewrite the description and nothing else (a new skill: a few keys only),
    with no comment outside the description that Claude Code's repair step
    could fold into a value. Comparing raw lines, not parsed values, is what
    catches a quoting or comment change PyYAML and Claude Code read
    differently (round 11)."""
    if frontmatter_is_ambiguous(head_text):
        return ["frontmatter Claude Code could read differently (a `---` inside a line, "
                "a duplicate key, or a non-key line at column 0)"]
    try:
        head = frontmatter_blocks(head_text)
    except StopIteration:
        return ["no frontmatter"]
    errors = []
    if "description" not in head or not _description_shape_ok(head["description"]):
        errors.append("a `description` that is not a plain line, one complete quoted "
                      "line, or a `>`/`|` block of indented lines")
    # Claude Code substitutes arguments before it looks, so try the body with
    # each placeholder empty and as a space (round 13: `$ARGUMENTS!` + a span).
    # The text as written is checked too, so nothing a substitution removes
    # can hide a command (round 14).
    # Arguments are escaped for `!` but not for backticks, so a placeholder may
    # also become a backtick, a fence or a closed span (round 16).
    bodies = [head_text] + [_PLACEHOLDER_RE.sub(sub, head_text)
                            for sub in ("", " ", "`", "```", "`x`")]
    if any(r.search(body) for r in _SKILL_SHELL_RES for body in bodies):
        errors.append("a shell command Claude Code would run on load (`!` and a backtick "
                      "span, or a ```! fence)")
    if "arguments" in head:
        # Claude Code fills in `$<name>` for each named argument, which this
        # scan does not model (round 17).
        errors.append("named `arguments` in the frontmatter, whose placeholders this check "
                      "does not model")
    commented = sorted(k for k, block in head.items() if k != "description"
                       and any(_COMMENT_RE.search(line.split(":", 1)[1] if i == 0 else line)
                               for i, line in enumerate(block)))
    if commented:
        errors.append(f"a comment in frontmatter {commented}")
    if base_text is not None:
        try:
            base = frontmatter_blocks(base_text)
        except StopIteration:
            base = {}
        others = sorted(k for k in set(base) | set(head)
                        if k != "description" and base.get(k) != head.get(k))
        if others:
            errors.append(f"frontmatter lines other than `description` changed: {others}")
    else:
        extra = sorted(set(head) - set(LOOP_NEW_SKILL_KEYS))
        if extra:
            errors.append(f"frontmatter keys a new skill from the loop may not have: {extra}")
    return errors


def _fold(name: str) -> str:
    """A path as a case-insensitive file system sees it."""
    import unicodedata
    return "/".join(unicodedata.normalize("NFC", part).rstrip(". ").casefold()
                    for part in name.split("/"))


#: A file an evals-loop pull request may add or change in its skill: SKILL.md,
#: PURPOSE.md, or a lowercase Markdown file in plain (not dot) directories.
_LOOP_SKILL_PATH_RE = re.compile(
    r"^(?:SKILL\.md|PURPOSE\.md|(?:[a-z0-9_-]+/)*[a-z0-9_-][a-z0-9._-]*\.md)$")


def frontmatter_name(text: str) -> Optional[str]:
    value = frontmatter(text).get("name")
    # Exact: Claude Code keeps a quoted name's spaces (round 14).
    return value if isinstance(value, str) else None


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
        if plugin in dupes or not _entry_types_ok(entry):
            continue
        source = entry.get("source")
        if not isinstance(source, str):
            if isinstance(source, dict) and _remote_pin_ok(source):
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
        all_files = files_under(rev, root + "/skills")
        folded: Dict[str, int] = {}
        for path in all_files:
            folded[_fold(path)] = folded.get(_fold(path), 0) + 1
        # Every folded directory prefix too: `skill.md/notes.md` makes a
        # directory that replaces SKILL.md on a case-insensitive checkout.
        dirs = {_fold(path).rsplit("/", i)[0] for path in all_files
                for i in range(1, path.count("/") + 1)}
        # A SKILL.md directly in skills/ makes Claude Code load that one skill
        # and never read the subdirectories: every other skill disappears.
        top = [p for p in all_files if "/" not in p[len(prefix):]
               and _fold(p[len(prefix):]) == "skill.md"]
        if top:
            if verify_against is not None and bad is not None:
                bad.extend(top)
            continue
        for path in all_files:
            rest = path[len(prefix):] if path.startswith(prefix) else ""
            skill, _, leaf = rest.partition("/")
            if leaf != "SKILL.md" or not skill:
                continue
            if _mode(rev, path) not in ("100644", "100755"):
                continue  # a symlink or gitlink: no SKILL.md on a consumer's disk
            if _size(rev, path) > SKILL_MD_MAX_BYTES:
                continue  # Claude Code skips a plugin skill this large
            if verify_against is not None and (folded.get(_fold(path), 0) > 1
                                               or _fold(path) in dirs):
                # Another file with the same name to a case-insensitive file
                # system (skill.md) overwrites this one on checkout there.
                if bad is not None:
                    bad.append(path)
                continue
            if verify_against is not None:
                if blob_id(rev, path) != blob_id(verify_against, path):
                    text = show(rev, path) or ""
                    if frontmatter_name(text) != skill or frontmatter_is_ambiguous(text):
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
    tokens = MarkdownIt("commonmark").parse(body or "")
    for token in tokens:
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
    # A block line counts in code too (a reader sees it), and comments are
    # found by the parser, not a regex an unclosed `<!--` in code could fool.
    shown = [visible] + [t.content for t in tokens if t.type in ("fence", "code_block")
                         or (t.type == "html_block"
                             and not t.content.lstrip().startswith("<!--"))]
    # Tags are markup, not text: `<summary>Adversarial-review: block` starts a
    # visible line.
    shown = [re.sub(r"<[^>\n]*>", "", text) for text in shown]
    blocked = any(REVIEW_BLOCK_RE.search(text) for text in shown)
    return (match.group(1) if match else None), blocked


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _q(value: str) -> str:
    return json.dumps(value)


def _basename(key: str, name: str) -> str:
    """The name as a case-insensitive file system sees it (APFS and NTFS fold
    case and drop trailing dots and spaces), so `Sync-Skills` or
    `sync-skills.` is the retired `sync-skills` on a consumer's disk."""
    base = name.split("/", 1)[1] if key == "skills" else name
    return base.rstrip(". ").lower()


def _source_identity(source):
    """Where a remote plugin lives, without the ref or sha it is pinned to:
    moving a pin is an ordinary release, moving the repository is a rename."""
    if not isinstance(source, dict):
        return source
    return {k: v for k, v in source.items() if k not in ("ref", "sha")}


def _marketplace_key(rev: str, key: str):
    data = _json_at(rev, MARKETPLACE)
    return data.get(key) if isinstance(data, dict) else None


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
                      "directory, or frontmatter Claude Code could read differently (a "
                      "`---` inside a line, a duplicate key, or a key-shaped line inside "
                      "a value). A skill's name is its directory name; fix the "
                      "frontmatter (or rename both, which is a one-way door).")
    if not after["marketplace"]:
        errors.append(f"{MARKETPLACE} in the merge result has no readable `name`.")
    lost = [(key, name) for key in LEDGER_KEYS for name in sorted(before[key] - after[key])]
    # A remote plugin keeps its name but changes every skill under it when its
    # source moves to another repository: that is a rename too.
    _, base_entries, _, _ = marketplace_entries(base)
    _, merged_entries, _, _ = marketplace_entries(merged)
    for plugin in sorted(before["plugins"] & after["plugins"]):
        old_src = base_entries.get(plugin, {}).get("source")
        new_src = merged_entries.get(plugin, {}).get("source")
        if not isinstance(old_src, str) and _source_identity(old_src) != _source_identity(new_src):
            lost.append(("plugins", plugin))
    # A name that arrives but has shipped before (by basename, for a skill, so
    # a retired skill re-created in another plugin counts) reuses a retired name:
    # stale locks and records keyed by it would now point at something else.
    recorded_base = base_ledger or {k: [] for k in LEDGER_KEYS}
    reused = [(key, name) for key in LEDGER_KEYS for name in sorted(after[key] - before[key])
              if _basename(key, name) in {_basename(key, n) for n in recorded_base[key]}]
    doors = [("disappear", lost), ("reuse a retired name", reused)]
    # `git archive`, which builds every lock, applies .gitattributes:
    # export-ignore drops a file, export-subst, ident, text/eol, filter and
    # working-tree-encoding rewrite one, and a macro can set any of them. So
    # any changed .gitattributes is a door, whatever it says.
    changed = changed_paths(base, merged)
    attr_files = [p for p in changed if posixpath.basename(p) == ".gitattributes"]
    if attr_files:
        doors.append(("change what `git archive` exports",
                      [("marketplace", p) for p in attr_files]))
    # Paths a case-insensitive checkout (NTFS, APFS) merges into one: on a
    # consumer's disk one replaces the other, and a SKILL.md or a skills/
    # directory can go. Any new collision, or any new non-ASCII name (whose
    # case folding differs between file systems), is a door.
    hazards = sorted(_path_hazards(merged) - _path_hazards(base))
    if hazards:
        doors.append(("add a path a case-insensitive file system could merge",
                      [("marketplace", h) for h in hazards]))
    # Claude Code validates the marketplace and each manifest against a schema
    # this check does not model (required owner and author names, URL-shaped
    # homepages, ...), and one that fails drops the plugin or every plugin
    # (round 17). So any change but a manifest's `version`, kept a semantic
    # version string, is a door.
    configs = [p for p in changed if p == MARKETPLACE
               or re.fullmatch(r"plugins/[^/]+/(?:\.claude-plugin/)?plugin\.json", p)]
    touched = [("marketplace", path) for path in configs
               if _config_shape(_json_at(base, path), path == MARKETPLACE)
               != _config_shape(_json_at(merged, path), path == MARKETPLACE)
               or _config_shape(_json_at(merged, path), path == MARKETPLACE) is None]
    if touched:
        doors.append(("change the marketplace or a plugin manifest beyond a `version`, "
                      "`description`, `keywords` or remote pin", touched))
    # A `renames` map is followed by Claude Code for good and migrates users'
    # settings (append-only), so adding or changing one is a one-way door.
    if _marketplace_key(base, "renames") != _marketplace_key(merged, "renames"):
        doors.append(("change the marketplace `renames` map", [("marketplace", "renames")]))
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


def _only_drops_length_waiver(base: str, merged: str, skill_md: str) -> bool:
    import yaml  # third-party; only for an improvement that touches the file

    try:
        old = yaml.safe_load(show(base, WAIVERS) or "")
        new = yaml.safe_load(show(merged, WAIVERS) or "")
    except yaml.YAMLError:
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if {k: v for k, v in old.items() if k != "waivers"} != \
            {k: v for k, v in new.items() if k != "waivers"}:
        return False
    old_w, new_w = old.get("waivers") or [], new.get("waivers") or []
    if not isinstance(old_w, list) or not isinstance(new_w, list):
        return False
    target = [w for w in old_w if isinstance(w, dict) and w.get("klass") == LENGTH_WAIVER_CLASS
              and w.get("registry") == "adam-agentskills" and w.get("path") == skill_md]
    return len(target) == 1 and new_w == [w for w in old_w if w is not target[0]]


def check_loop_pr(base: str, merged: str, pr: dict, open_prs: Optional[list],
                  head: Optional[str] = None) -> List[str]:
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
    by_app = ((pr.get("user") or {}).get("login")) == config["author"]
    if lane is None and not by_app:
        return []
    if lane is None:
        # The loop's App on a branch no lane names would escape every limit
        # (round 19), so its pull requests must use a lane.
        return [f"The loop's App {_q(config['author'])} opened this pull request from "
                f"{_q(ref)}, which no lane in {_q(LOOP_CONFIG)} names. Its pull requests "
                "must come from a lane's branch."]
    base_info = pr.get("base") or {}
    default = (base_info.get("repo") or {}).get("default_branch")
    if not isinstance(default, str) or not default or base_info.get("ref") != default:
        # A loop pull request into another branch is judged by that branch's
        # copy of this check, which the App can write (round 19).
        return [f"An evals-loop pull request must target the default branch; this one "
                f"targets {_q(str(base_info.get('ref')))}."]
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
    # (A .gitattributes inside the skill directory is refused below: it can
    # make `git archive`, and so every lock, drop the skill.)
    allowed |= {f"{root}/{m}" for m in MANIFESTS}
    if kind in ("create", "remove"):
        allowed |= {"scripts/plugin_layout.json", LEDGER}
    if kind == "edit" and WAIVERS in changed and _only_drops_length_waiver(base, merged, skill_md):
        # A trim retires the skill's own length-limit waiver (skills-evals'
        # improve_gate.remove_length_waiver), or check_skills.py fails on a
        # stale waiver. Nothing else in the file may change.
        allowed.add(WAIVERS)
    outside = [p for p in changed
               if p not in allowed and (not p.startswith(f"{root}/skills/{skill}/")
                                        or posixpath.basename(p) == ".gitattributes")]
    for path in outside:
        errors.append(f"{_q(path)} is outside ADR 0021 Decision 3's scope for a "
                      f"{_q(lane)} pull request.")
    if LEDGER in changed and LEDGER in allowed:
        # A create or removal may record its own skill; any other append would
        # reserve a name so a later pull request needs a review to use it.
        old_l, new_l = _json_at(base, LEDGER) or {}, _json_at(merged, LEDGER) or {}
        added = {(k, n) for k in LEDGER_KEYS for n in (new_l.get(k) or [])
                 if n not in (old_l.get(k) or [])}
        extra = sorted(added - {("skills", f"{plugin}/{skill}")})
        if extra:
            errors.append(f"{_q(LEDGER)} may gain only {_q(plugin + '/' + skill)} in this "
                          f"pull request; it also adds {extra}.")
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
        elif not (isinstance(new.get("version"), str)
                  and _SEMVER_RE.fullmatch(new["version"])):
            # Claude Code's schema wants a string; anything else unloads the
            # whole plugin (round 16).
            errors.append(f"{_q(path)}'s `version` must be a semantic version string.")

    # Decision 4, as the owner narrowed it on 2026-11-03 (skills-evals ADR
    # 0005, "Text-only skill changes addendum"): a text-only change auto-merges
    # in any plugin. plugin-runtime-review gates a plugin's own hooks/ and
    # bin/ only, so what it does not see is held here: the skill's frontmatter
    # (#135; `hooks` run code and `allowed-tools` skips permission prompts) and
    # the skill's own hooks/ and bin/. A loop pull request may not change
    # them in any state; a person takes such a change to another branch.
    base_fm = frontmatter(show(base, skill_md) or "") if at_base else {}
    head_text = (show(merged, skill_md) or "") if at_head else ""
    head_fm = frontmatter(head_text) if at_head else {}
    changed_keys = [k for k in SKILL_RUNTIME_KEYS
                    if k in head_fm and head_fm.get(k) != base_fm.get(k)]
    # Held in every state: arming a green pull request merges it before a new
    # run of this check registers, and a draft can be marked ready and armed
    # within seconds. A person who wants such a change takes it to a branch
    # outside the loop's prefixes.
    held = True
    hold = ("ADR 0021 Decision 4 never merges a change in what a skill runs or may run "
            "automatically. Take it to a branch outside the loop's prefixes for a "
            "person to review.")
    if held and changed_keys:
        errors.append(f"{_q(skill_md)} adds or changes frontmatter {changed_keys}. {hold}")
    elif held and at_head and at_base != at_head and frontmatter_is_ambiguous(head_text):
        errors.append(f"{_q(skill_md)}'s frontmatter could read differently to Claude "
                      "Code (a `---` inside a line, a duplicate key, or a key-shaped line "
                      "inside a value), so this check cannot tell whether it adds `hooks` "
                      f"or `allowed-tools`. Fix the frontmatter. {hold}")
    if held:
        # Text only: plugin-runtime-review gates a plugin's own hooks/ and bin/,
        # not a skill's scripts, so anything in the skill but a regular
        # Markdown file is code (or a link to code) and is held here.
        for path in changed:
            if not path.startswith(f"{root}/skills/{skill}/"):
                continue
            mode = _mode(merged, path)
            if mode is None:
                continue  # deleted
            rel = path[len(f"{root}/skills/{skill}/"):]
            if mode != "100644" or not _LOOP_SKILL_PATH_RE.match(rel) or (
                    rel != "SKILL.md" and posixpath.basename(rel).casefold() == "skill.md") \
                    or posixpath.basename(rel).casefold() in _MEMORY_FILE_NAMES \
                    or any(_WINDOWS_RESERVED_RE.match(part) for part in rel.split("/")):
                errors.append(f"{_q(path)} is not a plain Markdown file a loop pull request "
                              "may change (SKILL.md, PURPOSE.md, or a lowercase .md outside "
                              f"dot-directories, as a regular file). {hold}")
        in_skill = files_under(merged, f"{root}/skills/{skill}")
        seen: Dict[str, str] = {}
        for path in in_skill:
            other = seen.setdefault(_fold(path), path)
            folded_dirs = {_fold(path).rsplit("/", i)[0] for i in range(1, path.count("/") + 1)}
            clash = next((seen[d] for d in folded_dirs if d in seen and seen[d] != path), None)
            if clash:
                errors.append(f"{_q(clash)} and a directory of {_q(path)} are one name on a "
                              f"case-insensitive file system. {hold}")
            if other != path:
                errors.append(f"{_q(other)} and {_q(path)} are one file on a "
                              f"case-insensitive file system. {hold}")
        if at_head:
            for problem in loop_frontmatter_errors(
                    (show(base, skill_md) or "") if at_base else None, head_text):
                errors.append(f"{_q(skill_md)}: {problem}. {hold}")
    if "skills.lock" in changed:
        errors += _lock_matches_merge(base, merged, head,
                                      f"{plugin}/{skill}", kind == "remove")

    # A draft is never auto-merged, and Decision 5 lets the draft-skill gate
    # open one while another loop PR is open, so the slot rule skips drafts;
    # `ready_for_review` re-runs this when one is marked ready.
    if open_prs is not None and not pr.get("draft"):
        number = pr.get("number")
        # Only the loop's own ready pull requests hold the slot: a fork or a
        # person can name a branch `retire-skill/x`, and the loop opens drafts
        # on purpose (Decisions 4 and 5), so counting those would let anyone
        # stall the loop and fail the loop's own in-flight pull request.
        # Only an OLDER one blocks, so two opened in the same seconds cannot
        # fail each other forever: the older passes, the newer waits.
        others = sorted(
            o.get("number") for o in open_prs
            if isinstance(o, dict) and isinstance(o.get("number"), int)
            and isinstance(number, int) and o["number"] < number and not o.get("draft")
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
    head = resolve(head)
    merged = merge_result(base, head)
    # A create or edit lane's body is model-written (skills-evals pastes its
    # report), so a pass line there opens no door; only the removal lane's
    # reviewer writes one (round 18).
    body = pr.get("body")
    if _loop_kind(base, merged, pr) in ("create", "edit"):
        body = None
    return check_names(base, merged, body) + check_loop_pr(base, merged, pr, open_prs, head)


def _loop_kind(base: str, merged: str, pr: dict) -> Optional[str]:
    config = _json_at(base, LOOP_CONFIG)
    if config is None:
        config = _json_at(merged, LOOP_CONFIG)
    lanes = config.get("lanes") if isinstance(config, dict) else None
    if not isinstance(lanes, dict):
        return None
    ref = ((pr.get("head") or {}).get("ref")) or ""
    lane = next((p for p in lanes if isinstance(p, str) and ref.startswith(p)), None)
    return lanes.get(lane) if lane is not None else None


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
