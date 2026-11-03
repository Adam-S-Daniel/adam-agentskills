#!/usr/bin/env python3
"""check_skill_freshness.py — every repo file, workflow and secret name a skill
cites still exists in the repo it names (skills-evals#661).

Why this exists
---------------
Reference-heavy skills (cms-platform's `aws-bootstrap`, `preview-environments`,
`admin-config-render`, `browser-testing`, `code-quality`,
`editorial-label-audit`) fail by going stale, not by teaching a bad procedure,
so skills-evals' DESIGN.md takes them off the eval schedule and names this lint
as their instrument instead. `check_skills.py` checks only references inside a
skill's own directory; this checks what a skill says about other files.

The repos a skill cites change independently of this one, so the lint runs on
a schedule (`.github/workflows/skill-freshness.yml`), not as a pull-request
check, and files one tracking issue when it fails. It runs locally too:

    python3 scripts/check_skill_freshness.py [--registry NAME=PATH]
        [--repo OWNER/NAME=PATH] [--check-secrets] [--output report.md]

What counts as a citation
-------------------------
Every Markdown file in a skill's directory (SKILL.md first) is parsed with a
CommonMark parser, never scanned line by line:

- A GitHub link to a moving branch tip, `https://github.com/<o>/<r>/blob|tree/
  main|master|HEAD/<path>` (or the raw.githubusercontent.com form), anywhere in
  the text or a code block: the path must exist in that repo. A link pinned to
  a commit or tag cannot go stale and is skipped.
- A backticked path right after a repo's name, as in "cms-platform's
  `docs/OPERATIONS.md`" (names from `repos:` in skills_freshness.yml, or an
  `owner/name`): the path must exist in that repo.
- In a registry whose skills describe their own repo (`bare_paths: true`), any
  backticked path (`infrastructure/bootstrap/deploy.sh`) or workflow file name
  (`deploy-preview.yml`): it must exist in that repo or one of its
  `also_resolve_in` places. Only path-shaped spans count — a known file
  extension, no placeholder (`{N}`, `<prefix>`, `$VAR`, `*`), no domain — so
  `example.com` or `pr-{N}/` is never a finding.
- `secrets.NAME` in a backticked span or code block: a secret name. Checked
  only with `--check-secrets`, against each candidate repo's secret list read
  with `gh api`; a list the credential cannot read leaves the name unchecked,
  and the report says so. The scheduled workflow's token cannot list secrets,
  so there it checks files and workflows only.

How a repo is read
------------------
Each cited repo is cloned once, shallow and without blobs (`git clone --depth 1
--filter=blob:none --no-checkout`), and its file list read with `git ls-tree`:
the default branch as GitHub serves it, with no API call. `--repo OWNER/NAME=PATH`
reads a local checkout's HEAD instead. A repo that cannot be cloned may be
private or gone (a 404 does not say which), so its citations are reported as
unverified, not stale — except the repo a registry's own bare paths resolve in,
which must be readable or the run is an error.

Exit codes: 0 every citation checked is fresh; 1 at least one is stale; 2 the
lint could not run (bad config, an unreadable registry repo).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_skills  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = SCRIPT_DIR / "skills_freshness.yml"
DEFAULT_REGISTRIES = SCRIPT_DIR / "skills_registries.yml"
DEFAULT_REMOTE = "https://github.com"

# Extensions that make a backticked span a file path. Deliberately no `.com`,
# `.ai`, `.env` and the like: a domain or a generated, uncommitted file is not
# a citation of a committed one.
FILE_EXTENSIONS = frozenset({
    "md", "yml", "yaml", "sh", "bash", "py", "js", "mjs", "cjs", "ts", "rb",
    "json", "toml", "ps1", "html", "liquid", "css", "scss", "txt", "lock", "erb",
})
WORKFLOW_EXTENSIONS = frozenset({"yml", "yaml"})
BUILD_OUTPUT_DIRS = frozenset({"_site", "node_modules", "test-results", "playwright-report"})
MOVING_REFS = frozenset({"main", "master", "HEAD"})
# Characters that make a span a pattern or placeholder, not a path.
NOT_A_PATH = set(" \t{}<>$*?[]|\"'=,;()…%!^`")
OWNER_REPO = r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+"

# Lexical tokens only (the fleet rule allows a regex for a lexical token).
GITHUB_URL = re.compile(
    r"https://github\.com/(" + OWNER_REPO + r")/(blob|tree)/([^/\s#?)]+)/([^\s#?)\]>`'\"]+)")
RAW_URL = re.compile(
    r"https://raw\.githubusercontent\.com/(" + OWNER_REPO + r")/([^/\s#?)]+)/([^\s#?)\]>`'\"]+)")
SECRET_REF = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
# The text right before a span names a repo: "cms-platform's " or "o/r's ".
REPO_POSSESSIVE = re.compile(r"(?:^|[\s(*_])((?:" + OWNER_REPO + r")|[A-Za-z0-9._-]+)['’]s\s*$")
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
BUILTIN_SECRETS = frozenset({"GITHUB_TOKEN"})


@dataclass(frozen=True)
class Place:
    """A repo, and the directory in it a relative path starts from."""
    slug: str
    prefix: str = ""

    def label(self) -> str:
        return f"{self.slug}:{self.prefix}" if self.prefix else self.slug


@dataclass
class Citation:
    registry: str
    skill: str
    file: str          # relative to the registry root
    line: int
    kind: str          # "path", "file" (a bare name), "workflow" or "secret"
    value: str         # the path, file name or secret name
    places: Tuple[Place, ...]
    explicit: bool     # the citation names its repo itself

    @property
    def skill_id(self) -> str:
        return f"{self.registry}/{self.skill}"


@dataclass
class Result:
    stale: List[Citation] = field(default_factory=list)
    unverified: List[Tuple[Citation, str]] = field(default_factory=list)
    secrets_unchecked: List[Citation] = field(default_factory=list)
    waived: List[Citation] = field(default_factory=list)
    unused_waivers: List[dict] = field(default_factory=list)
    checked: int = 0
    skills: int = 0
    skipped_registries: List[str] = field(default_factory=list)
    secret_notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class Unreadable(Exception):
    pass


# =================================================================================
# Config
# =================================================================================

def parse_place(text: str) -> Place:
    slug, _, prefix = str(text).partition(":")
    if not re.fullmatch(OWNER_REPO, slug):
        raise ValueError(f"not an owner/name repo: {text!r}")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return Place(slug, prefix)


def load_config(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a mapping")
    registries = doc.get("registries") or {}
    if not isinstance(registries, dict) or not registries:
        raise ValueError(f"{path}: needs a non-empty `registries:` mapping")
    parsed = {}
    for name, entry in registries.items():
        if not isinstance(entry, dict) or "repo" not in entry:
            raise ValueError(f"{path}: registry {name!r} needs a `repo:`")
        parsed[name] = {
            "repo": parse_place(entry["repo"]).slug,
            "bare_paths": bool(entry.get("bare_paths", False)),
            "also": [parse_place(p) for p in entry.get("also_resolve_in") or []],
        }
    repos = doc.get("repos") or {}
    if not isinstance(repos, dict):
        raise ValueError(f"{path}: `repos:` must be a mapping")
    for name, slug in repos.items():
        parse_place(slug)
    waivers = doc.get("waivers") or []
    if not isinstance(waivers, list):
        raise ValueError(f"{path}: `waivers:` must be a list")
    for i, w in enumerate(waivers):
        if not isinstance(w, dict) or not all(str(w.get(k) or "").strip()
                                              for k in ("skill", "cite", "reason")):
            raise ValueError(f"{path}: waivers[{i}] needs `skill`, `cite` and `reason`")
    return {"registries": parsed, "repos": {str(k): str(v) for k, v in repos.items()},
            "waivers": waivers}


# =================================================================================
# Extraction
# =================================================================================

def path_shape(raw: str) -> Optional[Tuple[str, str]]:
    """(kind, path) when a backticked span is a repo file path, else None."""
    value = raw.strip().rstrip(".,;:")
    while value.startswith("./"):
        value = value[2:]
    value = LINE_SUFFIX.sub("", value)
    if not value or "://" in value or ":" in value or NOT_A_PATH & set(value):
        return None
    if value.startswith(("/", "~", "../", "-", "@", "#", "\\")) or value.endswith("/"):
        return None
    segments = value.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return None
    # Jekyll's build output is never committed, so it is no citation of a file.
    if segments[0] in BUILD_OUTPUT_DIRS:
        return None
    # A dotted first segment that is not a dotfile is a host: `example.com/x.html`.
    if len(segments) > 1 and "." in segments[0] and not segments[0].startswith("."):
        return None
    last = segments[-1]
    stem, dot, ext = last.rpartition(".")
    if not dot or not stem or ext.lower() not in FILE_EXTENSIONS:
        return None
    if len(segments) == 1:
        return ("workflow" if ext.lower() in WORKFLOW_EXTENSIONS else "file", value)
    return ("path", value)


def _named_repo(before: str, repos: Dict[str, str]) -> Optional[str]:
    m = REPO_POSSESSIVE.search(before)
    if not m:
        return None
    name = m.group(1)
    if "/" in name:
        return name
    return repos.get(name)


def _url_citations(text: str) -> Iterable[Tuple[str, str, str]]:
    """(slug, kind, path) for each GitHub link to a moving branch tip."""
    for m in GITHUB_URL.finditer(text):
        slug, _, ref, path = m.groups()
        if ref in MOVING_REFS:
            yield slug, "path", path.rstrip("/.,;:")
    for m in RAW_URL.finditer(text):
        slug, ref, path = m.groups()
        if ref in MOVING_REFS:
            yield slug, "path", path.rstrip("/.,;:")


def extract(text: str, *, registry: str, skill: str, file: str, host: Place,
            bare: Sequence[Place], repos: Dict[str, str]) -> List[Citation]:
    """Every citation in one Markdown file of a skill."""
    front, body = check_skills.split_frontmatter(text)
    offset = 0 if front is None else text[: len(text) - len(body)].count("\n")
    found: List[Citation] = []
    seen: Set[Tuple[str, str, Tuple[Place, ...]]] = set()

    def add(line: int, kind: str, value: str, places: Sequence[Place], explicit: bool):
        key = (kind, value, tuple(places))
        if key in seen:
            return
        seen.add(key)
        found.append(Citation(registry, skill, file, line, kind, value,
                              tuple(places), explicit))

    for token in check_skills._MARKDOWN.parse(body):
        line = offset + (token.map[0] + 1 if token.map else 1)
        if token.type in ("fence", "code_block"):
            for slug, kind, path in _url_citations(token.content):
                add(line, kind, path, [Place(slug)], True)
            for m in SECRET_REF.finditer(token.content):
                add(line, "secret", m.group(1), [host, *bare], False)
            continue
        if token.type != "inline":
            continue
        before = ""
        for child in _flatten(token.children or ()):
            if child.type == "text":
                for slug, kind, path in _url_citations(child.content):
                    add(line, kind, path, [Place(slug)], True)
                before += child.content
                continue
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                for slug, kind, path in _url_citations(href):
                    add(line, kind, path, [Place(slug)], True)
                before = ""
                continue
            if child.type != "code_inline":
                if child.type in ("softbreak", "hardbreak"):
                    before += " "
                    line += 1
                continue
            content = child.content
            for m in SECRET_REF.finditer(content):
                add(line, "secret", m.group(1), [host, *bare], False)
            for slug, kind, path in _url_citations(content):
                add(line, kind, path, [Place(slug)], True)
            explicit = re.fullmatch(r"(" + OWNER_REPO + r"):(\S+)", content.strip())
            if explicit:
                shape = path_shape(explicit.group(2))
                if shape:
                    add(line, shape[0], shape[1], [Place(explicit.group(1))], True)
            else:
                shape = path_shape(content)
                named = _named_repo(before, repos)
                if shape and named:
                    add(line, shape[0], shape[1], [Place(named)], True)
                elif shape and bare:
                    add(line, shape[0], shape[1], list(bare), False)
            before = ""
    return found


def _flatten(children):
    for child in children:
        yield child
        if child.children:
            yield from _flatten(child.children)


# =================================================================================
# Repos
# =================================================================================

class RepoTrees:
    """File lists of GitHub repos, read through git, cached per run."""

    def __init__(self, overrides: Dict[str, Path], remote: str, cache: Path):
        self.overrides = {k.lower(): v for k, v in overrides.items()}
        self.remote = remote.rstrip("/")
        self.cache = cache
        self._trees: Dict[str, object] = {}

    def tree(self, slug: str) -> Tuple[Set[str], Set[str]]:
        key = slug.lower()
        if key not in self._trees:
            try:
                self._trees[key] = _index(self._list(slug))
            except Unreadable as exc:
                self._trees[key] = exc
        value = self._trees[key]
        if isinstance(value, Unreadable):
            raise value
        return value  # type: ignore[return-value]

    def _list(self, slug: str) -> List[str]:
        local = self.overrides.get(slug.lower())
        if local is not None:
            return _ls_files(local)
        dest = self.cache / slug.replace("/", "__")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false",
                   SSH_ASKPASS="/bin/false")
        try:
            proc = subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none",
                 "--no-checkout", f"{self.remote}/{slug}.git", str(dest)],
                capture_output=True, text=True, timeout=300, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unreadable(f"git clone did not finish ({exc.__class__.__name__})")
        if proc.returncode != 0:
            raise Unreadable("git clone failed: private to this credential, or gone "
                             "(a GitHub 404 does not say which)")
        return _ls_files(dest)


def _ls_files(path: Path) -> List[str]:
    path = Path(path)
    if not path.is_dir():
        raise Unreadable(f"no directory at {path}")
    proc = subprocess.run(["git", "-C", str(path), "ls-tree", "-r", "-z", "--name-only", "HEAD"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return [p for p in proc.stdout.split("\0") if p]
    files = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            files.append(Path(root, n).relative_to(path).as_posix())
    return files


def _index(files: List[str]) -> Tuple[Set[str], Set[str]]:
    dirs: Set[str] = set()
    for f in files:
        parts = f.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return set(files), dirs


def _exists(citation: Citation, place: Place, files: Set[str], dirs: Set[str]) -> bool:
    if citation.kind in ("workflow", "file"):
        # A bare file name says which file, not where: any file of that name
        # under the place counts, a workflow under .github/workflows/ included.
        name = citation.value
        return any(f.startswith(place.prefix) and f.rsplit("/", 1)[-1] == name
                   for f in files)
    target = place.prefix + citation.value
    return target in files or target.rstrip("/") in dirs


def secret_names(slug: str) -> Set[str]:
    """Repo and environment secret names, through `gh api`; Unreadable if not."""
    names: Set[str] = set()
    try:
        proc = subprocess.run(
            ["gh", "api", "--paginate", f"repos/{slug}/actions/secrets",
             "--jq", ".secrets[].name"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unreadable(f"gh did not run ({exc.__class__.__name__})")
    if proc.returncode != 0:
        raise Unreadable("this credential cannot list its secrets")
    names.update(n for n in proc.stdout.split() if n)
    envs = subprocess.run(
        ["gh", "api", "--paginate", f"repos/{slug}/environments", "--jq", ".environments[].name"],
        capture_output=True, text=True, timeout=120)
    if envs.returncode == 0:
        for env_name in envs.stdout.split():
            proc = subprocess.run(
                ["gh", "api", "--paginate", f"repos/{slug}/environments/{env_name}/secrets",
                 "--jq", ".secrets[].name"], capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                names.update(n for n in proc.stdout.split() if n)
    return names


# =================================================================================
# Run
# =================================================================================

def collect(config: dict, registries_config: Path, overrides: Dict[str, str],
            result: Result) -> List[Citation]:
    reg_doc = check_skills.load_yaml_mapping(registries_config, "registries config")
    registries, findings, skips = check_skills.resolve_registries(reg_doc, overrides)
    result.skipped_registries.extend(skips)
    for f in findings:
        result.errors.append(f.message)
    citations: List[Citation] = []
    for registry in registries:
        if not registry.exists:
            continue
        entry = config["registries"].get(registry.name)
        if entry is None:
            result.errors.append(f"registry {registry.name!r} has no entry in "
                                 f"{DEFAULT_CONFIG.name}")
            continue
        host = Place(entry["repo"])
        bare = [host, *entry["also"]] if entry["bare_paths"] else []
        for skill in check_skills.find_skills(registry):
            result.skills += 1
            files = sorted(skill.skill_dir.rglob("*.md"),
                           key=lambda p: (p.name != "SKILL.md", p.as_posix()))
            for md in files:
                if not md.is_file():
                    continue
                text = md.read_text(encoding="utf-8", errors="replace")
                rel = md.relative_to(registry.path).as_posix()
                for c in extract(text, registry=registry.name, skill=skill.basename,
                                 file=rel, host=host, bare=bare, repos=config["repos"]):
                    # A path inside the skill's own directory is check_skills.py's job.
                    if (not c.explicit and c.kind == "path"
                            and (md.parent / c.value).exists()):
                        continue
                    citations.append(c)
    return citations


def check(citations: List[Citation], config: dict, trees: RepoTrees,
          check_secrets: bool, result: Result) -> None:
    hosts = {e["repo"].lower() for e in config["registries"].values()}
    waivers = list(config["waivers"])
    used = set()
    secret_cache: Dict[str, object] = {}
    for c in citations:
        if c.kind == "secret":
            if c.value in BUILTIN_SECRETS:
                continue
            if not check_secrets:
                result.secrets_unchecked.append(c)
                continue
            readable, found = False, False
            for slug in dict.fromkeys(p.slug for p in c.places):
                if slug not in secret_cache:
                    try:
                        secret_cache[slug] = secret_names(slug)
                    except Unreadable as exc:
                        secret_cache[slug] = exc
                        result.secret_notes.append(f"{slug}: {exc}")
                names = secret_cache[slug]
                if isinstance(names, Unreadable):
                    continue
                readable = True
                if c.value in names:
                    found = True
                    break
            if found:
                result.checked += 1
            elif not readable:
                result.secrets_unchecked.append(c)
            else:
                _stale(c, waivers, used, result)
            continue

        reasons, readable, found = [], False, False
        for place in c.places:
            try:
                files, dirs = trees.tree(place.slug)
            except Unreadable as exc:
                if place.slug.lower() in hosts and not c.explicit:
                    result.errors.append(f"cannot read {place.slug}, where bare paths "
                                         f"resolve: {exc}")
                reasons.append(f"{place.slug}: {exc}")
                continue
            readable = True
            if _exists(c, place, files, dirs):
                found = True
                break
        if found:
            result.checked += 1
        elif not readable:
            result.unverified.append((c, "; ".join(dict.fromkeys(reasons))))
        else:
            _stale(c, waivers, used, result)
    result.unused_waivers = [w for i, w in enumerate(waivers) if i not in used]


def _stale(c: Citation, waivers: List[dict], used: Set[int], result: Result) -> None:
    for i, w in enumerate(waivers):
        if w["skill"] == c.skill_id and w["cite"] == c.value:
            used.add(i)
            result.waived.append(c)
            return
    result.stale.append(c)


def render(result: Result, check_secrets: bool) -> str:
    out = ["# Skill freshness", ""]
    out.append(f"Scanned {result.skills} skill(s); {result.checked} citation(s) fresh, "
               f"{len(result.stale)} stale, {len(result.unverified)} unverified, "
               f"{len(result.waived)} waived.")
    out.append("")
    for s in result.skipped_registries:
        out.append(f"- {s}")
    if result.errors:
        out += ["", "## Errors (the lint could not finish)", ""]
        out += [f"- {e}" for e in dict.fromkeys(result.errors)]
    if result.stale:
        out += ["", "## Stale citations", "",
                "Each names a file or workflow the repo it points at no longer has. "
                "Fix the skill, or add a waiver with a reason to "
                "`scripts/skills_freshness.yml` if the citation is right.", ""]
        for c in sorted(result.stale, key=lambda c: (c.skill_id, c.file, c.line)):
            where = ", ".join(p.label() for p in c.places)
            out.append(f"- `{c.registry}:{c.file}:{c.line}` cites {c.kind} `{c.value}`, "
                       f"not found in {where}")
    if result.unverified:
        out += ["", "## Unverified (the repo could not be read)", ""]
        for c, why in result.unverified:
            out.append(f"- `{c.registry}:{c.file}:{c.line}` cites `{c.value}` — {why}")
    out += ["", "## Secret names", ""]
    if not check_secrets:
        out.append(f"Not checked: {len(result.secrets_unchecked)} cited secret name(s). "
                   "Only files and workflows were checked. Run with `--check-secrets` "
                   "and a credential that can list the repos' secrets to check them.")
    else:
        out.append(f"{len(result.secrets_unchecked)} cited secret name(s) left unchecked "
                   "because no repo they may live in let this credential list its secrets.")
        out += [f"- {n}" for n in dict.fromkeys(result.secret_notes)]
    if result.unused_waivers:
        out += ["", "## Waivers that matched nothing", ""]
        out += [f"- `{w['skill']}` `{w['cite']}`" for w in result.unused_waivers]
    return "\n".join(out) + "\n"


def parse_repo_overrides(values: Iterable[str]) -> Dict[str, Path]:
    out = {}
    for v in values or []:
        slug, sep, path = v.partition("=")
        if not sep or not re.fullmatch(OWNER_REPO, slug.strip()) or not path.strip():
            raise SystemExit(f"ERROR: --repo expects OWNER/NAME=PATH, got {v!r}")
        out[slug.strip()] = Path(path.strip()).expanduser()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--registries-config", type=Path, default=DEFAULT_REGISTRIES)
    ap.add_argument("--registry", action="append", default=[], metavar="NAME=PATH",
                    help="where a registry's skills are checked out (as check_skills.py)")
    ap.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME=PATH",
                    help="read this repo's files from a local checkout instead of cloning")
    ap.add_argument("--remote", default=DEFAULT_REMOTE,
                    help="git base URL repos are cloned from (default %(default)s)")
    ap.add_argument("--check-secrets", action="store_true",
                    help="also check cited secret names against each repo's secret list")
    ap.add_argument("--output", type=Path, help="also write the Markdown report here")
    args = ap.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    overrides = check_skills.parse_overrides(args.registry)
    result = Result()
    citations = collect(config, args.registries_config, overrides, result)
    with tempfile.TemporaryDirectory(prefix="skill-freshness-") as cache:
        trees = RepoTrees(parse_repo_overrides(args.repo), args.remote, Path(cache))
        check(citations, config, trees, args.check_secrets, result)
    report = render(result, args.check_secrets)
    sys.stdout.write(report)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    if result.errors:
        return 2
    return 1 if result.stale else 0


if __name__ == "__main__":
    sys.exit(main())
