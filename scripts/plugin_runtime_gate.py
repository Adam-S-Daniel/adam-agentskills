#!/usr/bin/env python3
"""plugin_runtime_gate.py — does this pull request change how a plugin runs code?

Run by .github/workflows/plugin-runtime-review.yml (ADR 0016). It decides one
thing, `gated=true|false`, and the workflow's `plugin-runtime-approval` job
waits for the owner's approval on the `plugin-runtime-review` environment when
it is true.

The gate denies by default. A pull request is GATED when any of these holds:

  (1) plugin-path         a changed path (added, modified, removed, or either
                          name of a rename) lies under a plugin root, unless it
                          is on the inert list below. Codes that say more:
                          hook-file (<root>/hooks), bin-file (<root>/bin, which
                          Claude Code puts on PATH, ADR 0017), package-or-
                          settings (<root>/settings.json, package.json or a
                          lockfile), manifest-runtime-key (a plugin manifest),
                          plugin-layout-path (a file or symlink AT a layout
                          directory's own name). Plugin roots are every local
                          `source` in marketplace.json at base and at head,
                          plus every plugins/<name>/ the diff touches; names
                          match without regard to case.
  (2) marketplace-runtime-key
                          .claude-plugin/marketplace.json, under any spelling
                          or case, changed in anything but its inert keys
                          (top-level description, version, owner, keywords,
                          homepage, repository, license, author, and
                          `metadata` apart from `pluginRoot`); it is missing at
                          head when base had one; or its entries repeat a name.
  (3) hook-referenced     a changed path is, or lies under, a path a hook
                          names after the plugin root, at base or head, in its
                          `command`, `commandWindows` or an `args` element,
                          even when it is otherwise inert. The root is spelled
                          ${CLAUDE_PLUGIN_ROOT}, $CLAUDE_PLUGIN_ROOT,
                          ${PLUGIN_ROOT}, $PLUGIN_ROOT, or cmd.exe's
                          %PLUGIN_ROOT% / %CLAUDE_PLUGIN_ROOT%.
  (4) gate-itself         the gate's own workflow, this script or its tests.
  (5) fail-closed         anything this script cannot decide: an API error, a
                          file that does not parse, a diff at GitHub's
                          3000-file cap, or anything unexpected.
  (6) skill and command files, judged by what they change rather than by path
      (a prose edit does not gate; these gate):
      skill-frontmatter-runtime-key  YAML frontmatter `hooks`, `allowed-tools`
                          or `shell` differs between base and head, or is
                          present on a new file. Claude Code honors all three.
                          A duplicate key or frontmatter that does not parse is
                          (5); so is a missing PyYAML.
      skill-body-command  load-time shell commands differ (added, removed or
                          edited). Claude Code runs a fenced block opened
                          anywhere by three backticks and an exclamation mark,
                          and an inline exclamation mark plus backtick command
                          after whitespace or at a line start, spanning lines;
                          its own two regexes are ported with the JS
                          whitespace class, as in _agent-guidance's
                          agent-checks `skill-shell`.
      skill-body-bash     a body change in a file whose `allowed-tools` grants
                          Bash (or PowerShell) at base or head.
      A skill or command file is a SKILL.md (any case, anywhere), a
      <plugin-root>/commands/**/*.md, a .claude/commands/**/*.md, or a .md under
      a path a manifest or marketplace entry's `skills` or `commands` value
      names (which are runtime values, so naming one gates in (1) and (2)).

The inert list, which (1) skips: README*, LICENSE*, CHANGELOG*, PURPOSE.md, an
image (.png .jpg .jpeg .gif .svg .webp), and a .md file that is not a skill or
command file and has no `hooks`, `allowed-tools` or `shell` frontmatter and no
load-time command at base or head. Nothing under <root>/hooks or <root>/bin is
inert. (check_consistency.py already refuses any symlink under plugins/; the
gate cannot see file modes.)

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
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

MARKETPLACE = ".claude-plugin/marketplace.json"
MAX_FILE_BYTES = 1024 * 1024
# The pull-request files API lists at most 3000 files. A diff that reaches the
# cap may have been cut short, so reaching it is already "cannot decide".
MAX_CHANGED_FILES = 3000
# Marketplace keys that cannot change what runs; everything else gates.
INERT_MARKETPLACE_KEYS = frozenset({
    "description", "version", "owner", "keywords", "homepage", "repository",
    "license", "author"})
# Plugin-root names Claude Code reads; a file or symlink AT one of these names
# (not only a path under it) changes what loads from the directory.
LAYOUT_NAMES = {"hooks": "hook-file", "bin": "bin-file", "commands": "plugin-layout-path",
                "skills": "plugin-layout-path", ".claude-plugin": "plugin-layout-path"}
# What a change cannot hurt, matched on the basename in any case. Never applied
# under hooks/ or bin/. A README, LICENSE or CHANGELOG is inert only as plain
# text (no extension, .md or .txt); `README.js` or `license_check.py` is code.
INERT_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
# Binary images the mention scan never reads. An .svg is text, so it is read.
RASTER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")
INERT_TEXT_EXTENSIONS = ("", ".md", ".txt")
INERT_TEXT_PREFIXES = ("readme", "license", "changelog")
# Plugin-root directories whose contents Claude Code loads as components; any
# change under one gates, whatever the file is.
NEVER_INERT_DIRS = ("agents/", "workflows/", "monitors/")
# Manifest keys that cannot change what runs (mirrors INERT_MARKETPLACE_KEYS).
INERT_MANIFEST_KEYS = frozenset({
    "version", "description", "keywords", "author", "homepage", "repository", "license"})
SKILL_RUNTIME_KEYS = ("hooks", "allowed-tools", "shell")
# Files Claude Code loads as a skill or command, relative to a plugin root.
SKILL_MD_RE = re.compile(r"^skills/(?:[^/]+/)?SKILL\.md$", re.IGNORECASE)
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


GH_ATTEMPTS = 3
GH_TIMEOUT_SECONDS = 60
_sleep = time.sleep  # tests replace it, so none of them waits


class GhFetcher:
    """The network side, behind a few methods so tests can inject a fake."""

    def __init__(self, repo: str, pr: int):
        self.repo = repo
        self.pr = pr

    def _gh(self, args: List[str], what: str, ok_404: bool = False):
        """One `gh` call, retried on a transient failure (a non-zero exit or a
        timeout) with a short backoff, then failing closed. A 404 is an
        answer, not a failure, when the caller asked for it."""
        for attempt in range(GH_ATTEMPTS):
            try:
                proc = subprocess.run(
                    ["gh"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=GH_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc = None
            if proc is not None and proc.returncode == 0:
                return proc
            if proc is not None and ok_404 and b"HTTP 404" in proc.stderr:
                return None
            if attempt + 1 < GH_ATTEMPTS:
                _sleep(attempt + 1)
        code = "timeout" if proc is None else f"gh exit {proc.returncode}"
        raise GateError(f"{what} failed ({code})")

    def changed_files(self) -> List[dict]:
        proc = self._gh(
            ["api", "--paginate", f"repos/{self.repo}/pulls/{self.pr}/files?per_page=100",
             "--jq", ".[] | {filename, status, previous_filename}"],
            "listing the pull request's files")
        return _json_values(proc.stdout.decode("utf-8"))

    def list_files(self, sha: str) -> List[str]:
        """Every file path in the tree at sha."""
        proc = self._gh(
            ["api", f"repos/{self.repo}/git/trees/{sha}?recursive=1",
             "--jq", "{truncated, paths: [.tree[] | select(.type == \"blob\") | .path]}"],
            f"listing the tree at {sha[:7]}")
        values = _json_values(proc.stdout.decode("utf-8"))
        if len(values) != 1 or not isinstance(values[0], dict) or values[0].get("truncated") \
                or not isinstance(values[0].get("paths"), list):
            raise GateError(f"the tree at {sha[:7]} is truncated or unreadable")
        return values[0]["paths"]

    def read(self, path: str, sha: str) -> Optional[bytes]:
        """The file's bytes at sha, or None when it does not exist there."""
        proc = self._gh(
            ["api", "-H", "Accept: application/vnd.github.raw",
             f"repos/{self.repo}/contents/{quote(path, safe='/')}?ref={sha}"],
            f"reading {json.dumps(path)}", ok_404=True)
        return None if proc is None else proc.stdout


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
                    self._cache[path] = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
                except (ValueError, UnicodeDecodeError):
                    raise GateError(f"{json.dumps(path)} at {self.side} is not valid JSON") from None
        return self._cache[path]


def _no_repeated_keys(pairs):
    """json's last-key-wins would hide an earlier value from this gate."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("repeated key")
    return dict(pairs)


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
    # Case-insensitive: macOS and Windows resolve Hooks/ to hooks/.
    path, prefix = path.lower(), prefix.lower()
    return prefix == "" or path == prefix or path.startswith(prefix + "/")


def _inert_name(path: str) -> bool:
    name = posixpath.basename(path).lower()
    stem, ext = posixpath.splitext(name)
    if ext in INERT_IMAGE_EXTENSIONS or name == "purpose.md":
        return True
    return ext in INERT_TEXT_EXTENSIONS and stem.startswith(INERT_TEXT_PREFIXES)


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
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if isinstance(source, dict):
            # A git-subdir or github source may point back into this repo, and
            # the gate cannot tell: its `path` counts as a root either way.
            source = source.get("path")
        if not isinstance(source, str):
            continue  # a remote source with no path has no root in this repo
        remote = isinstance(entry.get("source"), dict)
        # Both readings of a relative source, with and without
        # metadata.pluginRoot: an extra root can only gate more, never less.
        for candidate in [source] + ([posixpath.join(plugin_root, source)]
                                     if isinstance(plugin_root, str) else []):
            root = _norm(candidate)
            if root is None and remote:
                continue
            if root is None:
                raise GateError(f"a marketplace source in {MARKETPLACE} leaves the repo")
            roots[root] = entry
    return roots


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


def _hook_configs(snap: Snapshot, root: str, entry: Optional[dict]) -> Iterable[Tuple[Optional[str], object]]:
    """Every hook config one plugin root carries at one side, as (file path or
    None for an inline object, config): hooks/hooks.json, each file a `hooks`
    value names, and each inline hook object."""
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
                yield None, item
    for path in sorted({f for f in files if f is not None}):
        data = snap.json(path)
        if data is not _ABSENT:
            yield path, data


def _manifests(root: str) -> Tuple[str, str]:
    return (_join(root, ".claude-plugin/plugin.json"), _join(root, "plugin.json"))


def _marketplace_view(marketplace, side: str):
    """The part of a marketplace.json that can change what runs: everything but
    the inert keys and the inert `metadata` keys. Raises GateError on a shape it
    cannot read or on a repeated entry name (a later entry would hide an
    earlier one from a reader that keeps the last)."""
    if not isinstance(marketplace, dict) or not isinstance(marketplace.get("plugins"), list):
        raise GateError(f"{MARKETPLACE} at {side} does not have the expected shape")
    names = [entry["name"] for entry in marketplace["plugins"]
             if isinstance(entry, dict) and isinstance(entry.get("name"), str)]
    if len(names) != len(set(names)):
        raise GateError(f"{MARKETPLACE} at {side} repeats an entry name")
    view = {key: value for key, value in marketplace.items()
            if key not in INERT_MARKETPLACE_KEYS}
    metadata = view.pop("metadata", _ABSENT)
    if isinstance(metadata, dict):
        if "pluginRoot" in metadata:
            view["metadata"] = {"pluginRoot": metadata["pluginRoot"]}
    elif metadata is not _ABSENT:
        view["metadata"] = metadata
    return view


# Claude Code's own frontmatter split, ported from its source, anchored at the
# file start.
_JS_WS = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
# JS `\s`, spelled out: Python's `\s` differs (it lacks U+FEFF, adds U+001C-1F, U+0085).
_CLAUDE_CODE_FRONTMATTER = re.compile(
    rf"---[{_JS_WS}]*\n(.*?)---[{_JS_WS}]*\n?", re.DOTALL)


# What Claude Code (v2.1.323) runs when a skill or command loads: a fenced
# block, opened anywhere (not only at line start) by three backticks and an
# exclamation mark, and an inline `!`...`` after whitespace or at a line start.
# They are its own two regexes, `/```!\s*\n?([\s\S]*?)\n?```/g` and
# `/(?<=^|\s)!`([^`]+)`/gm`, with JS `\s` spelled out. _agent-guidance's
# agent-checks `skill-shell` check flags the same two forms.
_SHELL_FENCE = re.compile(rf"```!(?:[{_JS_WS}]*)\n?(.*?)\n?```", re.DOTALL)
_SHELL_INLINE = re.compile(
    rf"(?:(?<![^\n\r\u2028\u2029])|(?<=[{_JS_WS}]))!`([^`]+)`")


def _command_lines(text: str) -> Counter:
    """The command texts Claude Code would run as shell at load. Compared as a
    whole between base and head, so an edit inside an existing command counts."""
    return Counter(_SHELL_FENCE.findall(text) + _SHELL_INLINE.findall(text))


class _Skill:
    """What the gate reads from one SKILL.md or command file."""

    def __init__(self, values, body, commands, frontmatter="", tools=_ABSENT):
        self.frontmatter = frontmatter  # the frontmatter text, for static mentions
        self.tools = tools        # an agent's `tools` value, which can grant a shell
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


def _strict_yaml_load(yaml, text: str):
    """safe_load that refuses a repeated mapping key (PyYAML keeps the last)."""
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, node, deep=False):
        keys = [repr(loader.construct_object(key, deep=True)) for key, _ in node.value]
        if len(keys) != len(set(keys)):
            raise yaml.YAMLError("repeated key")
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    return yaml.load(text, Loader)  # noqa: S506 — a SafeLoader subclass


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
        meta = _strict_yaml_load(yaml, strict)
    except yaml.YAMLError:
        raise GateError(f"the frontmatter of {json.dumps(path)} does not parse") from None
    body = text[match.end():]
    if meta is None:
        return _Skill(absent, body, commands, strict)
    if not isinstance(meta, dict):
        raise GateError(f"the frontmatter of {json.dumps(path)} is not a mapping")
    return _Skill({key: meta.get(key, _ABSENT) for key in SKILL_RUNTIME_KEYS}, body, commands,
                  strict, meta.get("tools", _ABSENT))


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
    if posixpath.basename(path).lower() == "skill.md" or PROJECT_COMMAND_RE.search(path):
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
    removed: Set[str] = set()  # deleted, or the old name of a rename
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
            removed.add(name)
        elif status == "modified":
            expect[name] = "both"
        elif status == "renamed":
            expect[name] = "present-head-only"
        if isinstance(previous, str):
            changed.add(previous)
            if status == "renamed":
                removed.add(previous)
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
        if len(parts) >= 2 and parts[0].lower() == "plugins":
            roots.add("plugins/" + parts[1])

    named: Set[str] = set()
    for side, snap in (("base", base_snap), ("head", head_snap)):
        for root in sorted(roots):
            for key in ("skills", "commands"):
                named |= _named_paths(snap, root, entry_roots[side].get(root), key)

    def manifest_view(snap, path):
        data = snap.json(path)
        if data is _ABSENT:
            return _ABSENT
        if not isinstance(data, dict):
            raise GateError(f"{json.dumps(path)} is not a JSON object")
        return {k: v for k, v in data.items() if k not in INERT_MANIFEST_KEYS}

    skill_cache: Dict[str, Optional[Tuple[bool, bool]]] = {}

    def skill_of(path, root):
        """(grants a shell, has hooks) for the skill whose directory holds
        `path`, over base and head; None when it is not in a skill directory or
        its SKILL.md exists on neither side."""
        parts = path[len(root) + 1:].split("/") if root else path.split("/")
        if len(parts) < 3 or parts[0].lower() != "skills":
            return None
        skill_dir = "/".join(([root] if root else []) + parts[:2])
        if skill_dir not in skill_cache:
            found = [_read_skill(snap.raw(skill_dir + "/SKILL.md"), skill_dir + "/SKILL.md")
                     for snap in (base_snap, head_snap)
                     if snap.raw(skill_dir + "/SKILL.md") is not None]
            skill_cache[skill_dir] = None if not found else (
                any(_grants_bash(k.values["allowed-tools"]) for k in found),
                any(k.values["hooks"] is not _ABSENT or k.commands for k in found))
        return skill_cache[skill_dir]

    listing_cache: Dict[str, List[str]] = {}

    def files_at(snap):
        if snap.sha not in listing_cache:
            listing_cache[snap.sha] = list(fetcher.list_files(snap.sha))
        return listing_cache[snap.sha]

    def fully_deleted(path, root):
        """Whether `path` is in a skill directory retired whole: the pull
        request's file list shows every changed path under it removed, and the
        head tree has nothing left under it. A pair of answers that cannot both
        be true fails closed."""
        parts = path[len(root) + 1:].split("/") if root else path.split("/")
        if len(parts) < 3 or parts[0].lower() != "skills":
            return False
        prefix = "/".join(([root] if root else []) + parts[:2]).lower() + "/"
        under = {p for p in changed if p.lower().startswith(prefix)}
        left = {f for f in files_at(head_snap) if f.lower().startswith(prefix)}
        if left & removed or (not left and under - removed):
            raise GateError(f"the file list and the head tree disagree about {json.dumps(prefix)}")
        return not left and bool(under)

    def mention_patterns(skill):
        name = re.escape(skill.lower())
        return (re.compile(rf"(?<![a-z0-9._-]){name}/"),
                re.compile(rf"skills/{name}(?![a-z0-9_-])"))

    def normalized(text: str) -> str:
        text = text.lower().replace("\\", "/")
        text = re.sub(r"/+", "/", text)
        while "/./" in text:
            text = text.replace("/./", "/")
        return text

    texts_cache: Dict[str, list] = {}

    def read_listed(snap, name):
        data = snap.raw(name)
        if data is None:
            # The tree lists it, so a 404 is a failure, not an absence.
            raise GateError(f"{json.dumps(name)} is listed at {snap.side} but could not be read")
        return data

    def texts(root):
        """(path, normalized text or None) of every file under `root`, plus the
        marketplace file, at base and at head, but for raster images. JSON is
        read twice, raw and decoded (so \\/ and \\uXXXX spell the same path).
        Text is UTF-8, or UTF-16 after a BOM; a file that is neither is None."""
        if root not in texts_cache:
            out = []
            for snap in (base_snap, head_snap):
                listed = set(files_at(snap))
                names = [n for n in [MARKETPLACE] + sorted(listed)
                         if (n == MARKETPLACE or _under(n, root))
                         and not n.lower().endswith(RASTER_EXTENSIONS)]
                with ThreadPoolExecutor(max_workers=8) as pool:
                    blobs = list(pool.map(snap.raw, names))
                for name, data in zip(names, blobs):
                    if data is None:
                        if name in listed:
                            raise GateError(
                                f"{json.dumps(name)} is listed at {snap.side} but could not be read")
                        continue
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        try:
                            if not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                                raise UnicodeDecodeError("utf-16", b"", 0, 1, "no BOM")
                            text = data.decode("utf-16")
                        except UnicodeDecodeError:
                            out.append((name.lower(), None))
                            continue
                    forms = [text]
                    try:
                        forms.append(json.dumps(json.loads(text), ensure_ascii=False))
                    except ValueError:
                        pass
                    out.append((name.lower(), normalized("\n".join(forms))))
            texts_cache[root] = out
        return texts_cache[root]

    shell_cache: Dict[str, bool] = {}

    def any_skill_grants_a_shell(root):
        if root not in shell_cache:
            found = False
            for snap in (base_snap, head_snap):
                for name in files_at(snap):
                    if _under(name, root) and name.lower().endswith(".md"):
                        md = _read_skill(read_listed(snap, name), name)
                        found |= _grants_bash(md.values["allowed-tools"]) or _grants_bash(md.tools)
            shell_cache[root] = found
        return shell_cache[root]

    def named_elsewhere(root, skill):
        """Whether any text file under the root, outside this skill's own
        scripts/ and tests/, mentions the skill's directory."""
        own = f"{root + '/' if root else ''}skills/{skill}/".lower()
        patterns = mention_patterns(skill)
        return any(not (name.startswith(own) and name[len(own):].split("/")[0] in ("scripts", "tests"))
                   and (text is None or any(pat.search(text) for pat in patterns))
                   for name, text in texts(root))

    referenced: Set[str] = set()                              # (3)
    for side, snap in (("base", base_snap), ("head", head_snap)):
        for root in sorted(roots):
            for config_path, config in _hook_configs(snap, root, entry_roots[side].get(root)):
                referenced |= _hook_paths(config, root)
                if config_path is not None:
                    referenced.add(config_path)
    for path in changed:
        if any(_under(path, ref) for ref in referenced):
            reasons.add(("hook-referenced", path))

    # (1) Deny by default: a changed path under a plugin root gates unless inert.
    for path in sorted(changed):
        for root in roots:
            if not _under(path, root):
                continue
            rel = path[len(root) + 1:] if root else path
            low = rel.lower()
            if fully_deleted(path, root) and not any(_under(path, ref) for ref in referenced):
                continue  # a skill retired whole does not gate on its own
            if low in LAYOUT_NAMES:
                # A file or symlink at the directory's own name (plugins/x/hooks
                # as a link to somewhere else) has no "hooks/" prefix.
                reasons.add((LAYOUT_NAMES[low], path))
            elif low.startswith("hooks/"):
                reasons.add(("hook-file", path))
            elif low.startswith("bin/"):
                reasons.add(("bin-file", path))
            elif low in {name.lower() for name in ROOT_FILES}:
                reasons.add(("package-or-settings", path))
            elif low in (".claude-plugin/plugin.json", "plugin.json"):
                # Only a manifest at its exact path can be confined to inert
                # keys; another spelling still names runtime keys.
                if path not in _manifests(root) or \
                        manifest_view(base_snap, path) != manifest_view(head_snap, path):
                    reasons.add(("manifest-runtime-key", path))
            elif any(low.startswith(d) for d in NEVER_INERT_DIRS):
                reasons.add(("plugin-path", path))
            elif _is_skill_file(path, roots, named):
                pass  # judged by what it changes, in (6)
            else:
                skill = skill_of(path, root)
                shell_skill = bool(skill and skill[0])
                scripts = len(low.split("/")) > 3 and low.split("/")[2] in ("scripts", "tests")
                if skill and scripts and not skill[1] and \
                        not any_skill_grants_a_shell(root) and \
                        not named_elsewhere(root, rel.split("/")[1]):
                    # This accepts the same risk as the skill-body-bash rule
                    # in reverse: the model may run a script a skill ships under
                    # the session's own permissions. No skill in the plugin
                    # grants a shell, the skill has no hooks or load-time
                    # commands, and no text file in the plugin root names its
                    # directory. Only static mentions inside the plugin root are
                    # seen: a path built at run time, or named from outside it
                    # (the repo's own .claude/settings.json), is not. A module
                    # path (`python -m pkg.mod`) and a path split by quoting
                    # (`skills/"s"/x`) are dynamic in the same way. A file that
                    # cannot be decoded blocks the exemption.
                    continue
                if low.endswith(".md"):
                    # Frontmatter and commands are read on every .md, whatever
                    # its name, on both sides.
                    dirty = False
                    for snap in (base_snap, head_snap):
                        md = _read_skill(snap.raw(path), path)
                        dirty |= bool(md.commands) or any(
                            v is not _ABSENT for v in md.values.values())
                    # Under a skill that grants a shell, its prose is what the
                    # model follows with it, so nothing but a plain README,
                    # LICENSE, CHANGELOG or PURPOSE.md is inert there.
                    if dirty or (shell_skill and not _inert_name(path)):
                        reasons.add(("plugin-path", path))
                elif not _inert_name(path):
                    reasons.add(("plugin-path", path))

    # (2) The marketplace, under any spelling or case.
    for path in sorted(changed):
        if path != MARKETPLACE and path.lower() == MARKETPLACE:
            reasons.add(("marketplace-runtime-key", path))
    if MARKETPLACE in changed:
        if markets["base"] is _ABSENT or markets["head"] is _ABSENT:
            reasons.add(("marketplace-runtime-key", MARKETPLACE))
        elif _marketplace_view(markets["base"], "base") != _marketplace_view(markets["head"], "head"):
            reasons.add(("marketplace-runtime-key", MARKETPLACE))
    elif markets["base"] is not _ABSENT and markets["head"] is _ABSENT:
        reasons.add(("marketplace-runtime-key", MARKETPLACE))

    for path in sorted(changed):                              # (6)
        if _is_skill_file(path, roots, named) and not any(
                _under(path, r) and fully_deleted(path, r)
                and not any(_under(path, ref) for ref in referenced) for r in roots):
            # A renamed file's old name is in `changed` too; reading a side
            # where the path is absent gives "no keys", so the pair compares.
            before = _read_skill(base_snap.raw(path), path)
            after = _read_skill(head_snap.raw(path), path)
            if before.values != after.values:
                reasons.add(("skill-frontmatter-runtime-key", path))
            if before.commands != after.commands:
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
