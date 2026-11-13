#!/usr/bin/env python3
"""plugin_runtime_gate.py — does this pull request change how a plugin runs code?

Run by .github/workflows/plugin-runtime-review.yml (ADR 0016). It decides one
thing, `gated=true|false`, and the workflow's `plugin-runtime-approval` job
waits for the owner's approval on the `plugin-runtime-review` environment when
it is true.

A pull request is GATED when any of these holds:

  (a) hook-file           a changed path (old or new name) is under
                          <plugin-root>/hooks/;
  (b) package-or-settings a changed path is <plugin-root>/settings.json,
                          package.json, or a lockfile Claude Code installs from
                          (bun.lock, bun.lockb, npm-shrinkwrap.json,
                          package-lock.json);
  (c) manifest-runtime-key a manifest (<root>/.claude-plugin/plugin.json or
                          <root>/plugin.json) changed and its `hooks` or
                          `settings` value differs between base and head;
  (d) marketplace-runtime-key .claude-plugin/marketplace.json changed and an
                          entry's `hooks` or `settings` differs, or an added
                          entry carries either;
  (e) hook-referenced     a changed path is, or lies under, a path a hook
                          names after the plugin root, at base or head, in
                          its `command` or `commandWindows` (Codex runs that
                          one on Windows) or an `args` element. The root is
                          spelled ${CLAUDE_PLUGIN_ROOT}, $CLAUDE_PLUGIN_ROOT,
                          ${PLUGIN_ROOT}, $PLUGIN_ROOT, or cmd.exe's
                          %PLUGIN_ROOT% / %CLAUDE_PLUGIN_ROOT%;
  (f) gate-itself         the gate's own workflow, this script or its tests;
  (g) fail-closed         anything this script cannot decide: an API error, a
                          file that does not parse, a diff at GitHub's
                          3000-file cap, or anything unexpected;
  (h) bin-file            a changed path (old or new name) is under
                          <plugin-root>/bin/. Claude Code puts that directory
                          on the session's PATH, so what is in it runs
                          without a model choosing it, like a hook (ADR 0017);
  (i) skill-frontmatter-runtime-key
                          a changed <plugin-root>/skills/<skill>/SKILL.md (old
                          or new name) whose YAML frontmatter `hooks` or
                          `allowed-tools` differs between base and head, or is
                          present on a new skill. Claude Code honors both:
                          skill-scoped hooks run code, `allowed-tools` skips
                          permission prompts. The frontmatter is parsed with
                          PyYAML (never a regex); one that does not parse, or
                          a missing PyYAML, is (g);
  (j) skill-body-command  a changed SKILL.md (anywhere), command file
                          (<plugin-root>/commands/**/*.md, .claude/commands/
                          **/*.md) or file under a path a manifest `skills` or
                          `commands` key names, whose text gains a line Claude
                          Code runs when the skill loads: an exclamation mark
                          then a backtick at line start or after whitespace,
                          or a code fence opened with a backtick fence and an
                          exclamation mark. The rule and its JS whitespace
                          class are ported from _agent-guidance's agent-checks
                          `skill-shell` check;
  (k) skill-body-bash     a body change in such a file whose `allowed-tools`
                          grants Bash (or PowerShell) at base or head;
  (l) the frontmatter keys of (i) are read at every such path, not only
                          <root>/skills/<skill>/SKILL.md: <root>/skills/SKILL.md,
                          commands/**/*.md, and named paths. A manifest's
                          `skills` value and each `commands` entry's
                          `allowedTools` / `allowed-tools` / inline `content`
                          commands are compared as runtime keys too (c, d).

Plugin roots are the union of every plugins/<name>/ the diff touches and every
local `source` in marketplace.json, at base and at head.

Everything read from the pull request is untrusted DATA. It is fetched through
the GitHub API (never checked out), parsed with json only, capped in size, and
never executed. The summary prints repo-relative paths (JSON-quoted, so a path
cannot inject a workflow command) and reason codes, never file contents.

Exit status: 0 whether gated or not; 2 only on a usage error. The workflow
treats a failed or empty result as gated.

Usage:
  python3 scripts/plugin_runtime_gate.py [--repo OWNER/REPO] [--pr N]
                                         [--base SHA] [--head SHA]
  Each flag defaults to an environment variable: REPO, PR_NUMBER, BASE_SHA,
  HEAD_SHA. GH_TOKEN must be set for `gh api`.
"""

import argparse
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

MARKETPLACE = ".claude-plugin/marketplace.json"
MAX_FILE_BYTES = 1024 * 1024
# The pull-request files API lists at most 3000 files. A diff that reaches the
# cap may have been cut short, so reaching it is already "cannot decide".
MAX_CHANGED_FILES = 3000
RUNTIME_KEYS = ("hooks", "settings", "skills")
SKILL_RUNTIME_KEYS = ("hooks", "allowed-tools")
# Files Claude Code loads as a skill or command, relative to a plugin root.
SKILL_MD_RE = re.compile(r"^skills/(?:[^/]+/)?SKILL\.md$")
COMMAND_MD_RE = re.compile(r"^commands/.+\.md$", re.IGNORECASE)
# Project-level locations, at any depth of the repo.
PROJECT_COMMAND_RE = re.compile(r"(?:^|/)\.claude/commands/.+\.md$", re.IGNORECASE)
ROOT_FILES = frozenset({
    "settings.json", "package.json",
    "bun.lock", "bun.lockb", "npm-shrinkwrap.json", "package-lock.json",
})
# Under pull_request_target the BASE branch's workflow and script run, so a
# change to either takes effect only after it merges — which is why changing
# them is itself gated. The action lockfile is listed too: it chooses the
# commit each tag-pinned action (the gate's checkout among them) runs at.
GATE_FILES = frozenset({
    ".github/workflows/actions.lock",
    ".github/workflows/plugin-runtime-review.yml",
    "scripts/plugin_runtime_gate.py",
    "scripts/test_plugin_runtime_gate.py",
})

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Every spelling of the plugin root a hook command can use: POSIX shells see
# ${CLAUDE_PLUGIN_ROOT} or $CLAUDE_PLUGIN_ROOT (Claude Code substitutes the
# first; Codex only exports both names), and Codex's Windows `commandWindows`
# runs in cmd.exe, which spells it %PLUGIN_ROOT%.
_PLUGIN_ROOT_RE = re.compile(
    r"\$(?:\{(?:CLAUDE_)?PLUGIN_ROOT\}|(?:CLAUDE_)?PLUGIN_ROOT(?![A-Za-z0-9_]))"
    r"|%(?:CLAUDE_)?PLUGIN_ROOT%")
# The handler keys whose value is a command line.
_COMMAND_KEYS = ("command", "commandWindows")
# Where a path stops in raw shell text: whitespace or a shell operator.
_PATH_END_RE = re.compile(r"[\s;|&<>()`]")
# Where a path stops being literal: a variable or a glob. What precedes the
# last `/` before it is kept as a directory, so the match stays conservative.
_NON_LITERAL_RE = re.compile(r"[$%*?\[{]")
_ABSENT = object()


class GateError(Exception):
    """A condition this script cannot decide. The message names paths only."""


class GhFetcher:
    """The network side, behind two methods so tests can inject a fake."""

    def __init__(self, repo: str, pr: int):
        self.repo = repo
        self.pr = pr

    def changed_files(self) -> List[dict]:
        proc = subprocess.run(
            ["gh", "api", "--paginate",
             f"repos/{self.repo}/pulls/{self.pr}/files?per_page=100",
             "--jq", ".[] | {filename, status, previous_filename}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            raise GateError(f"listing the pull request's files failed (gh exit {proc.returncode})")
        return _json_values(proc.stdout.decode("utf-8"))

    def read(self, path: str, sha: str) -> Optional[bytes]:
        """The file's bytes at sha, or None when it does not exist there."""
        proc = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw",
             f"repos/{self.repo}/contents/{quote(path, safe='/')}?ref={sha}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            if b"HTTP 404" in proc.stderr:
                return None
            raise GateError(f"reading {json.dumps(path)} failed (gh exit {proc.returncode})")
        return proc.stdout


def _json_values(text: str) -> List[object]:
    """Every JSON value in a stream of them, however `gh --jq` lays them out."""
    decoder = json.JSONDecoder()
    values, index = [], 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return values
        value, index = decoder.raw_decode(text, index)
        values.append(value)


class Snapshot:
    """Reads one side (base or head) of the pull request, checking that what
    exists agrees with the file list — so an unreadable head sha, which would
    make every file look absent, fails closed instead of reading as "no hooks".
    """

    def __init__(self, fetcher, sha: str, side: str, changes: Dict[str, str]):
        self.fetcher = fetcher
        self.sha = sha
        self.side = side
        self.changes = changes  # path -> "present-base-only" / "present-head-only" / "both" / "any"
        self._cache: Dict[str, object] = {}

    def raw(self, path: str) -> Optional[bytes]:
        data = self.fetcher.read(path, self.sha)
        if data is not None and len(data) > MAX_FILE_BYTES:
            raise GateError(f"{json.dumps(path)} at {self.side} is larger than {MAX_FILE_BYTES} bytes")
        expected = self.changes.get(path)
        if expected is not None:
            must_exist = expected == "both" or expected == f"present-{self.side}-only"
            if must_exist and data is None:
                raise GateError(f"{json.dumps(path)} is listed as changed but is missing at {self.side}")
        return data

    def json(self, path: str):
        """Parsed JSON at this side, or _ABSENT."""
        if path not in self._cache:
            data = self.raw(path)
            if data is None:
                self._cache[path] = _ABSENT
            else:
                try:
                    self._cache[path] = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise GateError(f"{json.dumps(path)} at {self.side} is not valid JSON") from None
        return self._cache[path]


def _norm(path: str) -> Optional[str]:
    """A repo-relative POSIX path, "" for the repo root, None if it leaves the repo."""
    path = path.replace("\\", "/")
    if path.startswith("/"):
        return None
    normalized = posixpath.normpath(path) if path else "."
    if normalized == ".":
        return ""
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _join(root: str, rel: str) -> Optional[str]:
    return _norm(posixpath.join(root, rel) if root else rel)


def _under(path: str, prefix: str) -> bool:
    return prefix == "" or path == prefix or path.startswith(prefix + "/")


def _local_roots(marketplace) -> Dict[str, dict]:
    """Every local plugin root a marketplace.json names, mapped to its entry."""
    if marketplace is _ABSENT:
        return {}
    if not isinstance(marketplace, dict) or not isinstance(marketplace.get("plugins", []), list):
        raise GateError(f"{MARKETPLACE} does not have the expected shape")
    metadata = marketplace.get("metadata")
    plugin_root = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    roots = {}
    for entry in marketplace.get("plugins", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
            continue  # a remote source has no root in this repo
        source = entry["source"]
        # Both readings of a relative source, with and without
        # metadata.pluginRoot: an extra root can only gate more, never less.
        for candidate in [source] + ([posixpath.join(plugin_root, source)]
                                     if isinstance(plugin_root, str) else []):
            root = _norm(candidate)
            if root is None:
                raise GateError(f"a marketplace source in {MARKETPLACE} leaves the repo")
            roots[root] = entry
    return roots


def _entries_by_name(marketplace) -> Dict[str, dict]:
    if marketplace is _ABSENT:
        return {}
    return {entry["name"]: entry for entry in marketplace.get("plugins", [])
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)}


def _hook_commands(node) -> Iterable[str]:
    """Every `command` and `commandWindows` string and `args` element
    anywhere in a hook config."""
    if isinstance(node, list):
        for item in node:
            yield from _hook_commands(item)
    elif isinstance(node, dict):
        for key in _COMMAND_KEYS:
            if isinstance(node.get(key), str):
                yield node[key]
        if isinstance(node.get("args"), list):
            yield from (arg for arg in node["args"] if isinstance(arg, str))
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _hook_commands(value)


def _rests_after_plugin_root(text: str) -> Set[str]:
    """What follows the plugin root in one command or argument, read two
    ways — as shell words (quotes removed) and as raw text — and unioned, since
    a path read too short only gates more."""
    rests = set()
    words = [text]
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words += list(lexer)
    except ValueError:
        pass  # unbalanced quotes: the raw reading below still applies
    for index, word in enumerate(words):
        for match in _PLUGIN_ROOT_RE.finditer(word):
            rest = word[match.end():]
            if index == 0:  # the raw text, quotes and all
                rest = rest.lstrip("\"'")
                end = _PATH_END_RE.search(rest)
                rest = rest[:end.start()] if end else rest
                rest = rest.replace('"', "").replace("'", "")
            rests.add(rest)
    out = set()
    for rest in rests:
        rest = rest.replace("\\", "/")
        literal = _NON_LITERAL_RE.search(rest)
        if literal:
            rest = rest[:rest.rfind("/", 0, literal.start()) + 1]
        out.add(rest.lstrip("/"))
    return out


def _hook_paths(config, root: str) -> Set[str]:
    paths = set()
    for text in _hook_commands(config):
        for rest in _rests_after_plugin_root(text):
            path = _join(root, rest)
            if path is not None:
                paths.add(path)
    return paths


def _hook_configs(snap: Snapshot, root: str, entry: Optional[dict]) -> Iterable[object]:
    """Every hook config one plugin root carries at one side: hooks/hooks.json,
    each file a `hooks` value names, and each inline hook object."""
    sources = []
    if entry is not None and "hooks" in entry:
        sources.append(entry["hooks"])
    for manifest in _manifests(root):
        data = snap.json(manifest)
        if isinstance(data, dict) and "hooks" in data:
            sources.append(data["hooks"])
    files = [_join(root, "hooks/hooks.json")]
    for value in sources:
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                files.append(_join(root, item))
            elif isinstance(item, dict):
                yield item
    for path in sorted({f for f in files if f is not None}):
        data = snap.json(path)
        if data is not _ABSENT:
            yield data


def _manifests(root: str) -> Tuple[str, str]:
    return (_join(root, ".claude-plugin/plugin.json"), _join(root, "plugin.json"))


def _commands_runtime(value) -> object:
    """What a manifest's `commands` value can run, per entry: its allowedTools
    and the command lines of inline `content`. _ABSENT when it names none (a
    path or list of paths carries no such field; the files are read as files)."""
    if value is _ABSENT or isinstance(value, (str, list)):
        if isinstance(value, list) and not all(isinstance(v, str) for v in value):
            raise GateError("a `commands` list holds a non-string")
        return _ABSENT
    if not isinstance(value, dict):
        raise GateError("a `commands` value is not a path, a list or an object")
    out = {}
    for name, entry in value.items():
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict):
            raise GateError("a `commands` entry is not a path or an object")
        content = entry.get("content", "")
        if not isinstance(content, str):
            raise GateError("a `commands` entry's content is not a string")
        out[name] = (json.dumps(entry.get("allowedTools", None), sort_keys=True),
                     json.dumps(entry.get("allowed-tools", None), sort_keys=True),
                     tuple(sorted(_command_lines(content).elements())))
    return out or _ABSENT


def _runtime_values(data) -> Dict[str, object]:
    if data is _ABSENT:
        return {key: _ABSENT for key in RUNTIME_KEYS + ("commands-runtime",)}
    if not isinstance(data, dict):
        raise GateError("a manifest or marketplace entry is not a JSON object")
    values = {key: data.get(key, _ABSENT) for key in RUNTIME_KEYS}
    values["commands-runtime"] = _commands_runtime(data.get("commands", _ABSENT))
    return values


# Claude Code's own frontmatter split, ported from its source, anchored at the
# file start.
_JS_WS = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
# JS `\s`, spelled out: Python's `\s` differs (it lacks U+FEFF, adds U+001C-1F, U+0085).
_CLAUDE_CODE_FRONTMATTER = re.compile(
    rf"---[{_JS_WS}]*\n(.*?)---[{_JS_WS}]*\n?", re.DOTALL)


# The line forms Claude Code runs when a skill or command loads, ported from
# _agent-guidance's agent-checks `skill-shell`, with JS `\\s` for whitespace.
_SHELL_INLINE = re.compile(rf"(?:^|[{_JS_WS}])!`")
_SHELL_FENCE = re.compile(rf"^[{_JS_WS}]*```!")
_JS_LINE_BREAK = re.compile("[\n\r\u2028\u2029]")


def _command_lines(text: str) -> Counter:
    """The lines of text that Claude Code would run as shell at load."""
    return Counter(line for line in _JS_LINE_BREAK.split(text)
                   if _SHELL_INLINE.search(line) or _SHELL_FENCE.search(line))


class _Skill:
    """What the gate reads from one SKILL.md or command file."""

    def __init__(self, values, body, commands):
        self.values = values      # {hooks, allowed-tools: value or _ABSENT}
        self.body = body          # the text after the frontmatter
        self.commands = commands  # Counter of load-time shell lines


_NO_SKILL = _Skill({key: _ABSENT for key in SKILL_RUNTIME_KEYS}, "", Counter())


def _grants_bash(value) -> bool:
    """Whether an `allowed-tools` value could grant a shell. Any mention of the
    tool names counts, whatever the value's shape: a miss would be silent."""
    if value is _ABSENT:
        return False
    text = json.dumps(value, default=str).lower()
    return "bash" in text or "powershell" in text


def _read_skill(data: Optional[bytes], path: str) -> _Skill:
    """The runtime keys, body and load-time shell lines of a skill or command
    file (_NO_SKILL when the file is missing). Raises GateError on frontmatter
    that does not parse, so the caller fails closed."""
    if data is None:
        return _NO_SKILL
    try:
        import yaml  # third-party; the runner image ships it
    except ImportError:
        raise GateError("PyYAML is not installed, so SKILL.md frontmatter cannot be read") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GateError(f"{json.dumps(path)} is not valid UTF-8") from None
    commands = _command_lines(text)
    absent = {key: _ABSENT for key in SKILL_RUNTIME_KEYS}
    lines = text.split("\n")
    if not text.startswith("---"):
        return _Skill(absent, text, commands)
    # From here the file may be frontmatter to Claude Code, so the strict
    # reading must be clean or the gate fails closed. Python's rstrip() and
    # JS's `\s` disagree on U+FEFF and other code points, so "---" + U+FEFF is
    # an opening line to Claude Code and not to a strict split.
    if lines[0].rstrip() != "---":
        raise GateError(f"the frontmatter of {json.dumps(path)} has an unusual opening line")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        raise GateError(f"the frontmatter of {json.dumps(path)} has no closing '---' line")
    strict = "\n".join(lines[1:end])
    # Claude Code closes frontmatter at the first '---' anywhere (mid-line,
    # indented, the start of '----'), not only on a line of its own. If the two
    # readings differ, the file's runtime keys are ambiguous: fail closed.
    match = _CLAUDE_CODE_FRONTMATTER.match(text)
    if match is None or match.group(1).strip() != strict.strip():
        raise GateError(f"the frontmatter of {json.dumps(path)} is delimited ambiguously")
    try:
        meta = yaml.safe_load(strict)
    except yaml.YAMLError:
        raise GateError(f"the frontmatter of {json.dumps(path)} does not parse") from None
    body = text[match.end():]
    if meta is None:
        return _Skill(absent, body, commands)
    if not isinstance(meta, dict):
        raise GateError(f"the frontmatter of {json.dumps(path)} is not a mapping")
    return _Skill({key: meta.get(key, _ABSENT) for key in SKILL_RUNTIME_KEYS}, body, commands)


def _named_paths(snap: Snapshot, root: str, entry: Optional[dict], key: str) -> Set[str]:
    """The repo paths a manifest or marketplace entry's `skills` / `commands`
    value names (a string, a list of strings, or `commands`' object form whose
    entries are strings or carry a `source`)."""
    values = []
    if entry is not None and key in entry:
        values.append(entry[key])
    for manifest in _manifests(root):
        data = snap.json(manifest)
        if isinstance(data, dict) and key in data:
            values.append(data[key])
    names = []
    for value in values:
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, list):
            names.extend(value)
        elif isinstance(value, dict) and key == "commands":
            for item in value.values():
                names.append(item.get("source") if isinstance(item, dict) else item)
        else:
            raise GateError(f"a `{key}` value is not a path, a list or an object")
    paths = set()
    for name in names:
        if name is None:
            continue  # an inline command entry has no file
        if not isinstance(name, str):
            raise GateError(f"a `{key}` entry is not a path")
        joined = _join(root, name)
        if joined is None:
            raise GateError(f"a `{key}` path leaves the repo")
        paths.add(joined)
    return paths


def _is_skill_file(path: str, roots: Set[str], named: Set[str]) -> bool:
    """Whether Claude Code may load this path as a skill or command."""
    if posixpath.basename(path) == "SKILL.md" or PROJECT_COMMAND_RE.search(path):
        return True
    if path.lower().endswith(".md") and any(_under(path, n) for n in named):
        return True
    for root in roots:
        if _under(path, root):
            rel = path[len(root) + 1:] if root else path
            if SKILL_MD_RE.match(rel) or COMMAND_MD_RE.match(rel):
                return True
    return False


def evaluate(fetcher, base: str, head: str) -> List[Tuple[str, str]]:
    """The (reason code, path) pairs that gate this pull request; empty when
    nothing does. Raises GateError (or anything else) when it cannot decide."""
    files = fetcher.changed_files()
    if not isinstance(files, list):
        raise GateError("the pull request's file list is not a list")
    if len(files) >= MAX_CHANGED_FILES:
        return [("fail-closed", f"{len(files)} changed files reaches the API's {MAX_CHANGED_FILES}-file cap")]

    changed: Set[str] = set()
    expect: Dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise GateError("an entry in the pull request's file list has no filename")
        name, status, previous = item["filename"], item.get("status"), item.get("previous_filename")
        changed.add(name)
        if status == "added":
            expect[name] = "present-head-only"
        elif status == "removed":
            expect[name] = "present-base-only"
        elif status == "modified":
            expect[name] = "both"
        elif status == "renamed":
            expect[name] = "present-head-only"
        if isinstance(previous, str):
            changed.add(previous)
            if status == "renamed":
                expect.setdefault(previous, "present-base-only")

    base_snap = Snapshot(fetcher, base, "base", expect)
    head_snap = Snapshot(fetcher, head, "head", expect)
    reasons: Set[Tuple[str, str]] = set()

    # (f) first: it needs nothing fetched.
    for path in changed & GATE_FILES:
        reasons.add(("gate-itself", path))

    markets = {"base": base_snap.json(MARKETPLACE), "head": head_snap.json(MARKETPLACE)}
    entry_roots = {side: _local_roots(markets[side]) for side in markets}
    roots = set(entry_roots["base"]) | set(entry_roots["head"])
    for path in changed:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "plugins":
            roots.add("plugins/" + parts[1])

    for path in sorted(changed):
        for root in roots:
            if not _under(path, root):
                continue
            rel = path[len(root) + 1:] if root else path
            if rel.startswith("hooks/"):                      # (a)
                reasons.add(("hook-file", path))
            if rel.startswith("bin/"):                        # (h)
                reasons.add(("bin-file", path))
            if rel in ROOT_FILES:                             # (b)
                reasons.add(("package-or-settings", path))

    for root in sorted(roots):                                # (c)
        for manifest in _manifests(root):
            if manifest in changed and _runtime_values(base_snap.json(manifest)) \
                    != _runtime_values(head_snap.json(manifest)):
                reasons.add(("manifest-runtime-key", manifest))

    entries = {side: _entries_by_name(markets[side]) for side in markets}
    if MARKETPLACE in changed:                                # (d)
        for name, entry in entries["head"].items():
            before = entries["base"].get(name, _ABSENT)
            if before is _ABSENT:
                if any(key in entry for key in RUNTIME_KEYS):
                    reasons.add(("marketplace-runtime-key", MARKETPLACE))
            elif _runtime_values(before) != _runtime_values(entry):
                reasons.add(("marketplace-runtime-key", MARKETPLACE))

    referenced: Set[str] = set()                              # (e)
    for side, snap in (("base", base_snap), ("head", head_snap)):
        for root in sorted(roots):
            for config in _hook_configs(snap, root, entry_roots[side].get(root)):
                referenced |= _hook_paths(config, root)
    for path in changed:
        if any(_under(path, ref) for ref in referenced):
            reasons.add(("hook-referenced", path))

    named: Set[str] = set()                                   # (i), (j), (k), (l)
    for side, snap in (("base", base_snap), ("head", head_snap)):
        for root in sorted(roots):
            for key in ("skills", "commands"):
                named |= _named_paths(snap, root, entry_roots[side].get(root), key)
    for path in sorted(changed):
        if _is_skill_file(path, roots, named):
            # A renamed file's old name is in `changed` too; reading a side
            # where the path is absent gives "no keys", so the pair compares.
            before = _read_skill(base_snap.raw(path), path)
            after = _read_skill(head_snap.raw(path), path)
            if before.values != after.values:
                reasons.add(("skill-frontmatter-runtime-key", path))
            if any(after.commands[line] > before.commands[line] for line in after.commands):
                reasons.add(("skill-body-command", path))
            if before.body != after.body and any(
                    _grants_bash(skill.values["allowed-tools"]) for skill in (before, after)):
                reasons.add(("skill-body-bash", path))

    return sorted(reasons)


def decide(fetcher, base: str, head: str) -> Tuple[bool, List[Tuple[str, str]]]:
    """evaluate(), failing closed on anything it raises."""
    try:
        reasons = evaluate(fetcher, base, head)
    except GateError as exc:
        reasons = [("fail-closed", str(exc))]
    except Exception as exc:  # noqa: BLE001 — anything unexpected gates
        reasons = [("fail-closed", f"unexpected {type(exc).__name__}")]
    return bool(reasons), reasons


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=os.environ.get("REPO"))
    parser.add_argument("--pr", default=os.environ.get("PR_NUMBER"))
    parser.add_argument("--base", default=os.environ.get("BASE_SHA"))
    parser.add_argument("--head", default=os.environ.get("HEAD_SHA"))
    args = parser.parse_args(argv)
    if not args.repo or not REPO_RE.match(args.repo):
        parser.error("--repo / REPO must be OWNER/REPO")
    if not args.pr or not str(args.pr).isdigit() or int(args.pr) < 1:
        parser.error("--pr / PR_NUMBER must be a positive integer")
    for flag in ("base", "head"):
        if not getattr(args, flag) or not SHA_RE.match(getattr(args, flag)):
            parser.error(f"--{flag} must be a 40-character lowercase hex sha")
    args.pr = int(args.pr)
    return args


def main(argv=None, fetcher=None) -> int:
    args = _parse_args(argv)
    if fetcher is None:
        fetcher = GhFetcher(args.repo, args.pr)
    gated, reasons = decide(fetcher, args.base, args.head)
    print(f"gated={'true' if gated else 'false'}")
    for code, detail in reasons:
        # A path comes from the pull request: JSON-quoting it keeps a newline
        # or a leading `::` from being read as a workflow command.
        shown = detail if code == "fail-closed" else json.dumps(detail)
        print(f"  {code}: {shown}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"gated={'true' if gated else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
