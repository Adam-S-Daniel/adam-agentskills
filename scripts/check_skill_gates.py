#!/usr/bin/env python3
"""check_skill_gates.py — enforce the registry's skill graduation and touch
gates on a pull request, from what the pull request itself records.

Why this exists
----------------
AGENTS.md ("Skill changes get recorded, and evals gate them") sets four rules
for any change to a skill's content:

- every skill-content change appends an entry to docs/skill-impact.md;
- graduation gate: a new skill ships with a green `with_skill` arm;
- touch gate: a PR that edits an existing SKILL.md runs that skill's eval or
  adds its first fixture, unless the skill is in skills-evals' deliberate
  non-coverage table;
- a new or touched skill adds a PURPOSE.md beside SKILL.md.

Until this script they were prose only, and the impact log shows edits landing
with "Eval: not run" (Adam-S-Daniel/adam-agentskills#66; the retirement side
is skills-evals#70). This script turns the rules into a required check: it
reads the pull request's own diff and its new docs/skill-impact.md entries and
fails when a rule is not met. It also keeps docs/skill-impact.md append-only:
every entry present at the merge-base must still be there, byte for byte.

It does not dispatch an eval run. Evals run through skills-evals' eval
routine on the subscription, not on API credit, and take minutes per arm; a
required check that ran them would be slow, costly and flaky. The gate checks
the recorded result instead: the `- Eval:` line of the pull request's new
impact entry.

What it diffs
--------------
`git merge-base <base> <head>`, never `<base>` itself. In CI `--base` is the
base branch's CURRENT tip (`pull_request.base.sha`), which can be ahead of
where the branch forked; diffing from it would attribute main's own later
skill edits (and their impact entries) to this pull request. _agent-guidance's
scripts/check-guidance-touch.js makes the same choice for the same reason. A
merge-base that cannot be resolved (a shallow checkout, an unknown sha) exits
2 with a named error rather than guessing. Every file is read from the git
object store (`git show <rev>:<path>`), never from the working tree.

Skills are `plugins/<plugin>/skills/<skill>/SKILL.md`. Only SKILL.md counts:
create (absent at the merge-base, present at head), remove (the reverse),
touch (present at both, blob differs). A skill whose basename leaves one
plugin and arrives in another in the same diff is a move: it needs a new
entry, and if its SKILL.md also changed it is gated as a touch in its new
plugin.

How entries are read
---------------------
docs/skill-impact.md is parsed with markdown-it-py, never scanned line by
line: an entry is an h2 heading `<YYYY-MM-DD> — <plugin>/<skill> — <kind>...`
plus everything up to the next h2 (a trailing `---` separator excluded), so
the example heading inside the "Entry format" code fence is a `fence` token
and is never read as an entry. The Eval text is the inline content of the
list item that starts with `Eval:` (a wrapped bullet's continuation lines are
the same inline token). An entry is new when its heading plus body does not
appear as an entry at the merge-base.

The census, and "missing data never blocks"
--------------------------------------------
Whether a touched skill is covered, deliberately skipped or a gap comes from
skills-evals' coverage census, `coverage/latest.json` on its
`persistent/eval-results` branch (`--census` overrides the source; `none`
disables it). When the census cannot be read, or does not name the skill, a
touch whose Eval line carries no evidence is a warning, not a failure: a
missing census must not wedge every skill PR. The create and remove gates
never consult it (a brand-new skill is never in it).

Accepted Eval forms
--------------------
- RAN: a pass count such as `21/21` or a routine run id such as
  `20261026T211234Z-1b7681`, and the line does not start with `none` or
  `not run`.
- FIRST_FIXTURE: a skills-evals pull request link (`skills-evals/pull/N`) or
  the path `evals/<skill>/`.
- EXEMPT: the word `exempt`.
- GREEN (graduation): `with_skill ... X/Y` and `without_skill ... A/B` with
  X > 0 and X/Y >= A/B, plus a run id or a results link.

touch, census `covered`: RAN. `skipped`: RAN or EXEMPT. `gap`: RAN or
FIRST_FIXTURE. create: GREEN, or EXEMPT in a machine-bound plugin
(adam-coding-local, adam-non-coding-local), which passes with a warning to add
a row to skills-evals' evals/non-coverage.yml. remove: a `remove` entry.

The evals loop's own pull requests pass as written: improve PRs
(`eval-improve/<skill>`, skills-evals' scripts/improve_gate.py) write an
`edit` entry with counts like `21/21 -> 21/21` and add a PURPOSE.md stub;
draft PRs (`draft-skill/<skill>`, scripts/draft_new_skill.py) write a `create`
entry with the with_skill/without_skill counts and ship a PURPOSE.md; retire
PRs (`retire-skill/<skill>`, scripts/retire_skill.py) write a `remove` entry.
scripts/test_check_skill_gates.py pins those formats.

Usage:
  python3 scripts/check_skill_gates.py --base <rev> --head <rev> [--census <path|https-url|none>]

Exit 0: every rule met (warnings may print). Exit 1: one or more failures,
all listed. Exit 2: the check could not run (git error, unresolvable
merge-base).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from markdown_it import MarkdownIt

DEFAULT_CENSUS = ("https://raw.githubusercontent.com/Adam-S-Daniel/skills-evals/"
                  "persistent/eval-results/coverage/latest.json")
CENSUS_TIMEOUT_SECONDS = 15
CENSUS_MAX_BYTES = 5 * 1024 * 1024
REGISTRY_NAME = "adam-agentskills"
IMPACT_FILE = "docs/skill-impact.md"
MACHINE_BOUND_PLUGINS = ("adam-coding-local", "adam-non-coding-local")

_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")
_SKILL_PATH = re.compile(r"^plugins/([^/]+)/skills/([^/]+)/(SKILL|PURPOSE)\.md$")
_ENTRY_HEADING = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) — ([^/\s]+)/([^/\s]+) — ([A-Za-z]+)\b")

# Lexical tokens inside one already-parsed Eval text.
_COUNT = re.compile(r"(?<![\d.])(\d+)/(\d+)(?!\d|\.\d)")
_RUN_ID = re.compile(r"\b\d{8}T\d{6}Z(?:-[0-9a-f]{6})?\b")
_NOT_RUN = re.compile(r"^[\s`]*(?:none|not run)\b", re.IGNORECASE)
_EVALS_PR = re.compile(r"skills-evals/pull/\d+")
_EXEMPT = re.compile(r"\bexempt\b", re.IGNORECASE)
_RESULTS_LINK = re.compile(r"https://\S*eval-results\S*")
# `with_skill`, optional backticks, up to three short words (`objective`,
# `scored`), then the count.
_ARM = r"(?<![A-Za-z_]){arm}`?(?:[\s`:]+[A-Za-z]+){{0,3}}[\s`:]+(\d+)/(\d+)(?!\d|\.\d)"
_WITH = re.compile(_ARM.format(arm="with_skill"))
_WITHOUT = re.compile(_ARM.format(arm="without_skill"))


class GateError(Exception):
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
        raise GateError(f"git merge-base {base} {head} failed: {why}. The checkout may "
                        "be shallow (CI needs fetch-depth: 0) or a revision is unknown.")
    return proc.stdout.strip()


def tree_blobs(rev: str) -> Dict[str, str]:
    """{path: blob sha} for every SKILL.md/PURPOSE.md under plugins/ at rev."""
    proc = _git("ls-tree", "-r", rev, "--", "plugins")
    if proc.returncode != 0:
        raise GateError(f"git ls-tree {rev} failed: {proc.stderr.strip()}")
    blobs = {}
    for line in proc.stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and _SKILL_PATH.match(path):
            blobs[path] = parts[2]
    return blobs


def show(rev: str, path: str) -> Optional[str]:
    """File content at rev, or None when the path does not exist there."""
    if _git("cat-file", "-e", f"{rev}:{path}").returncode != 0:
        return None
    proc = _git("show", f"{rev}:{path}")
    if proc.returncode != 0:
        raise GateError(f"git show {rev}:{path} failed: {proc.stderr.strip()}")
    return proc.stdout


def blob_id(rev: str, path: str) -> Optional[str]:
    proc = _git("rev-parse", "--verify", "-q", f"{rev}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else None


# ---------------------------------------------------------------------------
# docs/skill-impact.md
# ---------------------------------------------------------------------------

def parse_entries(src: str) -> List[dict]:
    """Every dated h2 entry, in document order:
    {heading, date, plugin, skill, kind, body, eval}."""
    if not src:
        return []
    lines = src.split("\n")
    tokens = MarkdownIt("commonmark").parse(src)
    h2 = []  # (heading token index, start line, end-of-heading line)
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.tag == "h2" and t.map:
            h2.append((i, t.map[0], t.map[1]))
    hr_lines = [t.map for t in tokens if t.type == "hr" and t.map]
    entries = []
    for n, (i, start, after) in enumerate(h2):
        heading = tokens[i + 1].content.strip()
        m = _ENTRY_HEADING.match(heading)
        if not m:
            continue
        end = h2[n + 1][1] if n + 1 < len(h2) else len(lines)
        # A trailing `---` (an hr token, followed only by blank lines) is the
        # separator between entries, not part of this one.
        inside = [hr for hr in hr_lines if after <= hr[0] < end]
        if inside and all(not ln.strip() for ln in lines[inside[-1][1]:end]):
            end = inside[-1][0]
        body = "\n".join(lines[after:end]).rstrip()
        token_end = h2[n + 1][0] if n + 1 < len(h2) else len(tokens)
        eval_text = None
        depth = 0
        for t in tokens[i + 2:token_end]:
            if t.type == "list_item_open":
                depth += 1
            elif t.type == "list_item_close":
                depth -= 1
            elif (t.type == "inline" and depth > 0 and eval_text is None
                  and t.content.lstrip().startswith("Eval:")):
                eval_text = t.content.strip()[len("Eval:"):].strip()
        date, plugin, skill, kind = m.groups()
        entries.append({"heading": heading, "date": date, "plugin": plugin,
                        "skill": skill, "kind": kind.lower(), "body": body,
                        "eval": eval_text or ""})
    return entries


def identity(entry: dict) -> str:
    return entry["heading"] + "\n" + entry["body"]


def new_entries(head: List[dict], base: List[dict]) -> List[dict]:
    seen = {identity(e) for e in base}
    return [e for e in head if identity(e) not in seen]


def append_only_violations(head: List[dict], base: List[dict]) -> List[str]:
    """Headings of merge-base entries no longer present byte for byte at head
    (multiset membership; order is not enforced)."""
    counts: Dict[str, int] = {}
    for e in head:
        counts[identity(e)] = counts.get(identity(e), 0) + 1
    lost = []
    for e in base:
        key = identity(e)
        if counts.get(key, 0) > 0:
            counts[key] -= 1
        else:
            lost.append(e["heading"])
    return lost


# ---------------------------------------------------------------------------
# Eval evidence
# ---------------------------------------------------------------------------

def is_ran(text: str) -> bool:
    return bool(not _NOT_RUN.match(text) and (_COUNT.search(text) or _RUN_ID.search(text)))


def is_first_fixture(text: str, skill: str) -> bool:
    return bool(_EVALS_PR.search(text) or f"evals/{skill}/" in text)


def is_exempt(text: str) -> bool:
    return bool(_EXEMPT.search(text))


def is_green(text: str) -> bool:
    w, wo = _WITH.search(text), _WITHOUT.search(text)
    if not w or not wo:
        return False
    x, y, a, b = (int(v) for v in (*w.groups(), *wo.groups()))
    if x <= 0 or y <= 0 or b <= 0 or x > y or a > b:
        return False
    if Fraction(x, y) < Fraction(a, b):
        return False
    return bool(_RUN_ID.search(text) or _RESULTS_LINK.search(text))


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------

def load_census(source: str) -> Tuple[Optional[Dict[str, List[dict]]], Optional[str]]:
    """({skill: [rows]} for registry adam-agentskills, None) or (None, why)."""
    try:
        if source.startswith("https://"):
            with urllib.request.urlopen(source, timeout=CENSUS_TIMEOUT_SECONDS) as resp:
                raw = resp.read(CENSUS_MAX_BYTES + 1)
        else:
            with open(source, "rb") as fh:
                raw = fh.read(CENSUS_MAX_BYTES + 1)
        if len(raw) > CENSUS_MAX_BYTES:
            return None, "larger than the size cap"
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # any fetch or parse failure is "unreadable"
        return None, type(exc).__name__
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return None, "not schema 1"
    for reg in doc.get("registries") or []:
        if isinstance(reg, dict) and reg.get("name") == REGISTRY_NAME:
            rows: Dict[str, List[dict]] = {}
            for row in reg.get("skills") or []:
                if isinstance(row, dict) and isinstance(row.get("skill"), str):
                    rows.setdefault(row["skill"], []).append(row)
            return rows, None
    return None, f"no `{REGISTRY_NAME}` registry"


def census_status(census, plugin: str, skill: str) -> Optional[str]:
    if census is None:
        return None
    rows = census.get(skill) or []
    exact = [r for r in rows if r.get("bundle") == plugin] or rows
    status = exact[0].get("status") if exact else None
    return status if isinstance(status, str) else None


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.failures: List[str] = []
        self.warnings: List[str] = []

    def fail(self, who: str, what: str, why: str, fix: str) -> None:
        self.failures.append(f"skill-gates: {who}: {what}. {why}. {fix}")

    def warn(self, who: str, text: str) -> None:
        self.warnings.append(f"{who}: {text}")


def skill_changes(base_blobs: Dict[str, str], head_blobs: Dict[str, str]):
    def skills(blobs):
        out = {}
        for path, sha in blobs.items():
            m = _SKILL_PATH.match(path)
            if m and m.group(3) == "SKILL":
                out[(m.group(1), m.group(2))] = sha
        return out
    before, after = skills(base_blobs), skills(head_blobs)
    created = sorted(k for k in after if k not in before)
    removed = sorted(k for k in before if k not in after)
    touched = sorted(k for k in after if k in before and before[k] != after[k])
    moves = []
    for c in list(created):
        src = next((r for r in removed if r[1] == c[1]), None)
        if src is not None:
            created.remove(c)
            removed.remove(src)
            moves.append((src, c, before[src] != after[c]))
    return created, removed, touched, moves


def _entries_for(entries, plugin, skill, kinds=None, exclude=None):
    return [e for e in entries if e["plugin"] == plugin and e["skill"] == skill
            and (kinds is None or e["kind"] in kinds)
            and (exclude is None or e["kind"] not in exclude)]


def check_remove(rep, added, plugin, skill):
    who = f"{plugin}/{skill}"
    if not _entries_for(added, plugin, skill, kinds={"remove"}):
        rep.fail(who, "SKILL.md was removed with no new `remove` entry in docs/skill-impact.md",
                 "AGENTS.md records every removal there, with its motivation",
                 f"Add `## <date> — {who} — remove` with Motivation, Change, Eval and Outcome lines")


def check_create(rep, added, head_blobs, plugin, skill):
    who = f"{plugin}/{skill}"
    entries = _entries_for(added, plugin, skill, kinds={"create"})
    if not entries:
        rep.fail(who, "a new skill has no new `create` entry in docs/skill-impact.md",
                 "AGENTS.md records every creation there, with its eval result",
                 f"Add `## <date> — {who} — create` with an Eval line carrying the "
                 "with_skill and without_skill counts and the routine run id")
    if f"plugins/{plugin}/skills/{skill}/PURPOSE.md" not in head_blobs:
        rep.fail(who, "a new skill has no PURPOSE.md",
                 "AGENTS.md says a new or touched skill adds a PURPOSE.md beside SKILL.md",
                 "Add PURPOSE.md mapping the skill to the incidents or patterns behind it")
    if not entries:
        return
    if any(is_green(e["eval"]) for e in entries):
        return
    if plugin in MACHINE_BOUND_PLUGINS and any(is_exempt(e["eval"]) for e in entries):
        rep.warn(who, "graduates as exempt; once it is on main, add a row for it to "
                 "skills-evals' evals/non-coverage.yml so the census records why")
        return
    exempt_note = (" (or, for this machine-bound plugin, the word `exempt`)"
                   if plugin in MACHINE_BOUND_PLUGINS else "")
    rep.fail(who, "the `create` entry's Eval line does not show a green with_skill arm",
             "AGENTS.md's graduation gate requires a green `with_skill` arm before a skill graduates",
             "Run the skill's fixtures through the skills-evals eval routine and write "
             "`with_skill objective X/Y, without_skill A/B` (X > 0, X/Y >= A/B) with the run id "
             f"or results link on the Eval line{exempt_note}")


def check_touch(rep, added, head_blobs, plugin, skill, census, census_why):
    who = f"{plugin}/{skill}"
    entries = _entries_for(added, plugin, skill, exclude={"rejected"})
    if not entries:
        rep.fail(who, "SKILL.md changed with no new entry in docs/skill-impact.md",
                 "AGENTS.md says every skill-content change appends an entry there, in the same PR",
                 f"Add `## <date> — {who} — edit` with Motivation, Change, Eval and Outcome lines")
    if f"plugins/{plugin}/skills/{skill}/PURPOSE.md" not in head_blobs:
        rep.fail(who, "SKILL.md changed and the skill has no PURPOSE.md",
                 "AGENTS.md says a touched skill adds a PURPOSE.md beside SKILL.md",
                 "Add PURPOSE.md mapping the skill to the incidents or patterns behind it")
    if not entries:
        return
    texts = [e["eval"] for e in entries]
    ran = any(is_ran(t) for t in texts)
    exempt = any(is_exempt(t) for t in texts)
    first = any(is_first_fixture(t, skill) for t in texts)
    status = census_status(census, plugin, skill)
    run_fix = ("run the skill's eval through the skills-evals eval routine and put its "
               "counts (e.g. 21/21) or run id on the Eval line")
    if status == "covered":
        if not ran:
            rep.fail(who, "the Eval line shows no eval run, and the census lists the skill as covered",
                     "AGENTS.md's touch gate requires a covered skill's eval to run on every SKILL.md edit",
                     run_fix[0].upper() + run_fix[1:])
    elif status == "skipped":
        if not (ran or exempt):
            rep.fail(who, "the Eval line shows neither an eval run nor `exempt`, and the census "
                     "lists the skill as deliberately not covered",
                     "AGENTS.md exempts a skill in the non-coverage table, but the entry must say so",
                     "Write `exempt (DESIGN.md non-coverage table)` on the Eval line, or " + run_fix)
    elif status == "gap":
        if not (ran or first):
            rep.fail(who, "the Eval line shows neither an eval run nor a first fixture, and the "
                     "census lists the skill as having no fixture",
                     "AGENTS.md's touch gate requires either a run or the skill's first fixture",
                     "Link the skills-evals pull request adding `evals/" + skill + "/` on the Eval "
                     "line, or " + run_fix)
    elif not (ran or exempt or first):
        if census_why is not None:
            reason = f"the census could not be read ({census_why})"
        elif status is None:
            reason = "the census does not list this skill"
        else:
            reason = f"the census status `{status}` is not one this check knows"
        rep.warn(who, f"the Eval line shows no eval run, first fixture or exemption, and {reason}, "
                 "so the touch gate could not decide it; missing data never blocks")


def check_move(rep, added, head_blobs, src, dst, changed, census, census_why):
    who = f"{dst[0]}/{dst[1]}"
    entries = (_entries_for(added, *src, exclude={"rejected"})
               + _entries_for(added, *dst, exclude={"rejected"}))
    if not entries:
        rep.fail(who, f"the skill moved from {src[0]} with no new entry in docs/skill-impact.md",
                 "AGENTS.md records renames and moves there",
                 f"Add `## <date> — {who} — rename` with Motivation, Change, Eval and Outcome lines")
    elif changed:
        relabeled = [dict(e, plugin=dst[0], skill=dst[1]) for e in entries]
        check_touch(rep, relabeled, head_blobs, dst[0], dst[1], census, census_why)


def run(base: str, head: str, census_source: str) -> Tuple[int, List[str]]:
    out: List[str] = []
    point = merge_base(base, head)
    base_blobs, head_blobs = tree_blobs(point), tree_blobs(head)
    created, removed, touched, moves = skill_changes(base_blobs, head_blobs)
    impact_changed = blob_id(point, IMPACT_FILE) != blob_id(head, IMPACT_FILE)
    rep = Report()

    base_entries = parse_entries(show(point, IMPACT_FILE) or "")
    head_entries = parse_entries(show(head, IMPACT_FILE) or "") if impact_changed else base_entries
    added = new_entries(head_entries, base_entries)

    if impact_changed:
        for heading in append_only_violations(head_entries, base_entries):
            rep.failures.append(
                f"skill-gates: {IMPACT_FILE}: the entry \"{heading}\" present at the merge-base "
                "is missing or changed at head. The file is append-only, so the history of what "
                "was tried stays intact. Add a correcting entry instead of editing an old one")

    census, census_why = None, None
    if touched or any(changed for _, _, changed in moves):
        if census_source != "none":
            census, census_why = load_census(census_source)
            if census is None:
                rep.warn("census", f"could not read the census at {census_source} ({census_why})")
        else:
            census_why = "--census none"

    for plugin, skill in removed:
        check_remove(rep, added, plugin, skill)
    for plugin, skill in created:
        check_create(rep, added, head_blobs, plugin, skill)
    for plugin, skill in touched:
        check_touch(rep, added, head_blobs, plugin, skill, census, census_why)
    for src, dst, changed in moves:
        check_move(rep, added, head_blobs, src, dst, changed, census, census_why)

    if not (created or removed or touched or moves):
        out.append("skill-gates: no skill created, edited or removed")
    else:
        out.append(f"skill-gates: merge-base {point[:12]}; checked {len(created)} created, "
                   f"{len(touched)} edited, {len(removed)} removed, {len(moves)} moved skill(s) "
                   f"against {len(added)} new impact entr{'y' if len(added) == 1 else 'ies'}")
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for w in rep.warnings:
        if annotate:
            esc = w.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            out.append(f"::warning title=skill-gates::{esc}")
        else:
            out.append(f"skill-gates (warning): {w}")
    out.extend(rep.failures)
    if rep.failures:
        out.append(f"skill-gates: {len(rep.failures)} failure(s)")
    return (1 if rep.failures else 0), out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--census", default=DEFAULT_CENSUS,
                    help="path or https URL of the coverage census, or `none`")
    args = ap.parse_args(argv)
    try:
        code, lines = run(args.base, args.head, args.census)
    except GateError as exc:
        print(f"skill-gates: error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
