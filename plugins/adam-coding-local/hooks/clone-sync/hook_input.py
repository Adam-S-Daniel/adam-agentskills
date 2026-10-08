#!/usr/bin/env python3
"""hook_input.py - read a clone-sync hook's stdin JSON (ADR 0017).

Called by on-merge.sh and on-session-start.sh with the hook's stdin JSON on
stdin. Prints at most one line, `<kind>\t<value>`, and nothing at all when the
hook should do nothing:

  merge    `repo\tOWNER/REPO` or `dir\t<cwd>` when the tool call was a
           successful pull-request merge; nothing otherwise.
  session  `dir\t<cwd>` from the input's `cwd`; nothing when it has none.

A merge is either a shell tool call whose command runs `gh pr merge`, or a
tool whose name ends in `merge_pull_request` with `owner` and `repo` in its
input. The repo comes from `--repo`/`-R`, then a pull-request URL, then (as
`dir`) the directory the command ran in, which clone-sync.sh resolves to its
origin in the background.

Success is read leniently, because Claude Code and Codex report a shell
call's outcome differently and neither shape is a stable contract: the call
counts as FAILED only when the response says so plainly (a non-zero exit code
field, an error flag, `success: false`, `merged: false`, `interrupted: true`,
or a string response that starts by naming a non-zero exit code). When unsure
it counts as a success; a fast-forward of a clean clone is harmless.

Parsing only: nothing here runs a command, and nothing it prints is ever
executed by the caller.
"""

import json
import re
import shlex
import sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)/pull/\d+",
    re.IGNORECASE)
EXIT_TEXT_RE = re.compile(
    r"^\s*(?:exit code:?|process exited with code)\s*(-?\d+)", re.IGNORECASE)
OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
EXIT_KEYS = ("exit_code", "exitCode", "returncode", "return_code")
ERROR_KEYS = ("is_error", "isError", "interrupted")


def _slug(value):
    """OWNER/REPO, lowercase, from `o/r`, `HOST/o/r` or a github.com URL."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    match = PR_URL_RE.match(value)
    if match:
        return f"{match.group(1)}/{match.group(2)}".lower()
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE).rstrip("/")
    if value.lower().endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if len(parts) == 3 and parts[0].lower() in ("github.com", "www.github.com"):
        parts = parts[1:]
    candidate = "/".join(parts)
    return candidate.lower() if SLUG_RE.match(candidate) else None


def _failed(response, depth=0):
    """True only when the tool response plainly reports a failure."""
    if depth > 2:
        return False
    if isinstance(response, str):
        match = EXIT_TEXT_RE.match(response)
        return bool(match) and int(match.group(1)) != 0
    if isinstance(response, list):
        return any(_failed(item, depth + 1) for item in response)
    if not isinstance(response, dict):
        return False
    for key in EXIT_KEYS:
        value = response.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return True
    if any(response.get(key) is True for key in ERROR_KEYS):
        return True
    if response.get("success") is False or response.get("merged") is False:
        return True
    return any(_failed(response.get(key), depth + 1)
               for key in ("metadata", "output", "result")
               if isinstance(response.get(key), (dict, str)))


def _words(command):
    """Shell words of a command, operators split out. Never raises."""
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str):
        return []
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def gh_pr_merge(command):
    """For the first `gh ... pr merge` in a command: (True, slug or None).
    (False, None) when the command does not run one."""
    words = _words(command)
    segment = []
    for word in words + [";"]:
        if word not in OPERATORS:
            segment.append(word)
            continue
        found = _merge_in_segment(segment)
        if found[0]:
            return found
        segment = []
    return False, None


def _merge_in_segment(segment):
    for index, word in enumerate(segment):
        name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name not in ("gh", "gh.exe"):
            continue
        rest = segment[index + 1:]
        try:
            pr = rest.index("pr")
        except ValueError:
            continue
        if rest[pr + 1:pr + 2] != ["merge"]:
            continue
        slug = None
        for position, arg in enumerate(rest):
            value = None
            if arg in ("--repo", "-R") and position + 1 < len(rest):
                value = rest[position + 1]
            elif arg.startswith("--repo="):
                value = arg[len("--repo="):]
            elif arg.startswith("-R") and len(arg) > 2:
                value = arg[2:]
            if value is not None and _slug(value):
                return True, _slug(value)
        for arg in rest:
            if PR_URL_RE.match(arg):
                slug = _slug(arg)
                break
        return True, slug
    return False, None


def merge_target(payload):
    """("repo", slug), ("dir", path) or None for one PostToolUse input."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    if _failed(payload.get("tool_response")):
        return None

    if tool.endswith("merge_pull_request"):
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        if isinstance(owner, str) and isinstance(repo, str):
            slug = _slug(f"{owner}/{repo}")
            return ("repo", slug) if slug else None
        return None

    if "command" not in tool_input:
        return None
    is_merge, slug = gh_pr_merge(tool_input.get("command"))
    if not is_merge:
        return None
    if slug:
        return ("repo", slug)
    for key in ("workdir", "cwd"):
        source = tool_input if key == "workdir" else payload
        if isinstance(source.get(key), str) and source[key]:
            return ("dir", source[key])
    return None


def session_target(payload):
    if isinstance(payload, dict) and isinstance(payload.get("cwd"), str) and payload["cwd"]:
        return ("dir", payload["cwd"])
    return None


def main(argv):
    if len(argv) != 2 or argv[1] not in ("merge", "session"):
        return 2
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    target = (merge_target if argv[1] == "merge" else session_target)(payload)
    if target is not None and "\n" not in target[1] and "\t" not in target[1]:
        sys.stdout.write(f"{target[0]}\t{target[1]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
