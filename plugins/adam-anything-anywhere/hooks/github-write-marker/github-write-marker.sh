#!/usr/bin/env bash
# github-write-marker.sh — an agent's GitHub write carries the agent marker.
#
# THE CONTRACT
# ------------
# Every write an agent makes on GitHub goes out as Adam's own account. So
# every PR or issue body, issue or PR comment, review body and review reply an
# agent writes ends with the footer
#
#   <sub>🤖 Written by <agent> on behalf of @Adam-S-Daniel</sub>
#
# and an agent never submits an APPROVE review: an approval cannot carry the
# marker and reads as Adam's own sign-off. This PreToolUse hook blocks the
# write that breaks either rule and tells the agent what to append, so the
# agent corrects it and retries. It never rewrites the body itself. See
# docs/decisions/0023-mark-agent-contributions.md and the "Agent-made GitHub
# contributions carry a marker" section of agents-md/base.md.
#
# WHAT IT CHECKS
# --------------
# The marker check is the LEXICAL token `on behalf of @Adam-S-Daniel`, which
# the fleet's AST rule permits for a lexical token. Deciding WHETHER a Bash
# command is a GitHub write is a question of shape, so that part tokenizes
# the command with Python's `shlex` instead of matching a regex over its text.
#
# - MCP tools, matched by a regex on the tool NAME, never by a remembered
#   connector prefix (the claude.ai connector's prefix varies by surface):
#     ^mcp__.*github.*__(add_issue_comment|issue_write|create_pull_request|
#       update_pull_request|pull_request_review_write|
#       add_comment_to_pending_review|add_reply_to_pull_request_comment|
#       update_issue_comment)$
#   `event: APPROVE` (any case) is blocked. A non-empty `body` without the
#   token is blocked. No `body` at all (labels or state only) is allowed.
# - Bash, on `tool_input.command`. Heredoc bodies are cut out, then the rest
#   is split into words and control operators by
#   `shlex.shlex(posix=True, punctuation_chars=True)` with
#   `whitespace_split`. A word is in COMMAND POSITION at the start or after
#   `;`, `&&`, `||`, `|`, `&`, a newline or `(` (so an unquoted `$(...)`
#   counts), past any `NAME=value` prefixes and simple wrappers (`env`,
#   `command`, `exec`, `sudo`, `nohup`, `time`, `xargs`, `!`, `{`, `if`,
#   `then`, `do`, ...) and their dash options. `bash|sh|zsh|dash -c '<cmd>'`,
#   `eval`, a `$(...)` inside a word and a backtick span are checked as
#   commands of their own. A GitHub write is a command-position `gh` (or a
#   path ending in `/gh`) running `issue create|comment|edit`,
#   `pr create|comment|edit|review`, or `api` with `-X`/`--method`
#   POST|PATCH|PUT or a `-f`/`-F body=` field, to a path containing
#   `/issues`, `/pulls` or `/comments`. `gh` text inside an argument
#   (`echo "gh issue comment ..."`, `grep "gh pr create" f`) is not a write.
#   The flags below are read from that command's own words.
#   `gh pr review --approve` (or `-a`) is blocked. Any other write is allowed
#   when the command text holds the token, or when a `--body-file`/`-F
#   <path>` or `body=@<path>` file exists and holds it; a write with no body
#   flag at all (`gh issue edit --add-label x`) is allowed; the rest is
#   blocked. A body-file path is expanded first: `$NAME`/`${NAME}` from
#   `NAME=value` assignments (standalone, `export`ed, or a prefix) earlier in
#   the same command, then from the hook's own environment, then `~`. A path
#   that still holds `$`, cannot be read, or is `-` (stdin) is named in the
#   block message.
#   If `shlex` cannot tokenize the command (an unbalanced quote), the hook
#   falls back to the old regex over the whole text, which errs toward
#   checking.
#
# KNOWN GAPS: a command that holds the token anywhere passes as a whole, so a
# chain of two writes with one marked passes too. Quote context is lost after
# tokenizing, so a single-quoted `'$(gh pr comment ...)'` or backtick span is
# still checked as a write, and a single-quoted `'$VAR'` path is expanded. A
# body-file path is resolved against the hook's `cwd`, not a `cd` earlier in
# the command, and a prefix assignment is applied to its own command's path
# although bash would not. A `gh` reached through anything the wrapper list
# does not name (`ssh host gh ...`, `find -exec gh ...`, a function or alias)
# is not seen.
#
# EXIT CODES (Claude Code PreToolUse): 0 allows the call; 2 blocks it and
# shows stderr to the agent. Anything this hook cannot read is ALLOWED with a
# stderr warning: a broken hook must not wedge every tool call.
#
# The footer names the agent from `AGENT_NAME`: `codex` gives `Codex`, any
# other value or none gives `Claude Code`.
set -uo pipefail

# Read stdin ONCE.
EVENT="$(cat 2>/dev/null)" || EVENT=""

# Fast path: nothing GitHub-shaped in the event, nothing to check. Every Bash
# call reaches this hook when it is registered on `Bash`, so the common case
# never starts an interpreter.
case "$EVENT" in
    *gh*|*github*) ;;
    *) exit 0 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "github-write-marker: DEGRADED — python3 is not on PATH; GitHub writes are not being checked for the agent marker." >&2
    exit 0
fi

# The event travels in the environment because stdin carries the script.
rc=0
GITHUB_WRITE_MARKER_EVENT="$EVENT" python3 - <<'PY' || rc=$?
import json
import os
import re
import shlex
import sys

TOKEN = "on behalf of @Adam-S-Daniel"
AGENT = "Codex" if os.environ.get("AGENT_NAME", "").strip().lower() == "codex" else "Claude Code"
FOOTER = "<sub>🤖 Written by %s on behalf of @Adam-S-Daniel</sub>" % AGENT

MCP_TOOL = re.compile(
    r"^mcp__.*github.*__(add_issue_comment|issue_write|create_pull_request|"
    r"update_pull_request|pull_request_review_write|add_comment_to_pending_review|"
    r"add_reply_to_pull_request_comment|update_issue_comment)$")

# Bash, REGEX FALLBACK: used only when shlex cannot tokenize the command.
# A gh subcommand that writes a body-bearing object.
GH_WRITE = re.compile(r"\bgh\s+(?:issue\s+(?:create|comment|edit)|pr\s+(?:create|comment|edit|review))\b")
GH_PR_REVIEW = re.compile(r"\bgh\s+pr\s+review\b")
APPROVE_FLAG = re.compile(r"(?:^|\s)(?:--approve|-a)(?=\s|=|$|[;&|)])")
# Bash: `gh api` writing to an issues, pulls or comments endpoint.
GH_API = re.compile(r"\bgh\s+api\b")
API_METHOD = re.compile(r"(?:^|\s)(?:-X|--method)(?:\s+|=)?['\"]?(?:POST|PATCH|PUT)\b", re.I)
API_BODY_FIELD = re.compile(r"(?:^|\s)(?:-f|-F|--field|--raw-field)(?:\s+|=)['\"]?body=")
API_PATH = re.compile(r"/(?:issues|pulls|comments)\b")
# Does the command carry a body at all?
BODY_FLAG = re.compile(r"(?:^|\s)(?:--body|--body-file|-b|-F)(?=\s|=)")
# Files that may carry the body.
BODY_FILE = re.compile(r"(?:^|\s)(?:--body-file|-F)(?:\s+|=)(['\"]?)([^\s'\"]+)\1")
API_BODY_FILE = re.compile(r"(?:^|\s)(?:-F|--field)(?:\s+|=)['\"]?body=@([^\s'\"]+)")

# Bash, TOKENIZED: the characters shlex splits out as operators. A newline is
# one too, so it separates commands the way `;` does.
PUNCT = "();<>|&\n"
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VAR_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
# `<<WORD`, `<<-WORD`, `<<'WORD'`, `<<"WORD"`, `<<\WORD` (not `<<<`).
HEREDOC_OP = re.compile(
    r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|()<>'\"]+))")
# Words that run the command after them; their dash options are skipped.
WRAPPERS = {"env", "command", "exec", "sudo", "nohup", "time", "nice", "xargs",
            "!", "{", "if", "then", "else", "elif", "do", "while", "until"}
SHELLS = {"bash", "sh", "zsh", "dash"}
WRITE_METHODS = {"POST", "PATCH", "PUT"}


def allow():
    sys.exit(0)


def block(what):
    sys.stderr.write(
        "github-write-marker: blocked — %s goes out as @Adam-S-Daniel with no "
        "agent marker. End the body with this line, on its own, then retry:\n"
        "%s\n"
        "(ADR 0023: docs/decisions/0023-mark-agent-contributions.md in "
        "Adam-S-Daniel/_agent-guidance.)\n" % (what, FOOTER))
    sys.exit(2)


def block_approve():
    sys.stderr.write(
        "github-write-marker: blocked — an agent never submits an APPROVE review "
        "on Adam's account: an approval cannot carry the agent marker and reads "
        "as his own sign-off. Submit a COMMENT review instead, its body ending "
        "with this line, and leave the approval to Adam:\n%s\n" % FOOTER)
    sys.exit(2)


def block_unreadable(unreadable):
    lines = "".join("  %s — %s\n" % pr for pr in unreadable)
    sys.stderr.write(
        "github-write-marker: blocked — this GitHub write takes its body from a "
        "file the hook could not read, so it cannot see whether the body "
        "carries the agent marker:\n%s"
        "Pass a literal path to a file that exists, or put the marker in the "
        "command text itself, then retry. The body must end with this line, "
        "on its own:\n%s\n"
        "(ADR 0023: docs/decisions/0023-mark-agent-contributions.md in "
        "Adam-S-Daniel/_agent-guidance.)\n" % (lines, FOOTER))
    sys.exit(2)


# ── Bash: deciding what is a GitHub write ──────────────────────────────────
# Each write found is a record: {"approve": bool, "has_body": bool,
# "paths": [body-file paths, expanded]}.

def expand(text, shell_vars):
    """$NAME/${NAME} from inline assignments, then the environment, then ~."""
    text = VAR_REF.sub(
        lambda m: shell_vars.get(m.group(1) or m.group(2), m.group(0)), text)
    return os.path.expanduser(os.path.expandvars(text))


def regex_records(text):
    """The pre-tokenizer check over raw text; it errs toward checking."""
    is_gh_write = bool(GH_WRITE.search(text))
    is_api_write = bool(GH_API.search(text) and API_PATH.search(text)
                        and (API_METHOD.search(text) or API_BODY_FIELD.search(text)))
    if not (is_gh_write or is_api_write):
        return []
    return [{
        "approve": bool(GH_PR_REVIEW.search(text) and APPROVE_FLAG.search(text)),
        "has_body": bool((is_gh_write and BODY_FLAG.search(text))
                         or (is_api_write and API_BODY_FIELD.search(text))),
        "paths": [expand(p, {}) for _, p in BODY_FILE.findall(text)]
                 + [expand(p, {}) for p in API_BODY_FILE.findall(text)],
    }]


def strip_heredocs(text):
    """Drop heredoc bodies (and backslash-newlines) outside quotes, so body
    prose is never tokenized as shell. Returns None when a heredoc has no
    closing delimiter line; the caller then tokenizes the text as it is."""
    out, pending, quote = [], [], None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            out.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                out.append(text[i:i + 2])
            i += 2
            continue
        if quote == '"':
            out.append(c)
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            out.append(c)
            i += 1
            continue
        if text.startswith("<<", i) and not text.startswith("<<<", i):
            m = HEREDOC_OP.match(text, i)
            if m:
                delim = next(g for g in m.group(2, 3, 4) if g is not None)
                pending.append((delim, m.group(1) == "-"))
                out.append(text[i:m.end()])
                i = m.end()
                continue
        if c == "\n" and pending:
            out.append(c)
            j = i + 1
            for delim, strip_tabs in pending:
                while True:
                    k = text.find("\n", j)
                    line = text[j:] if k == -1 else text[j:k]
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        j = n if k == -1 else k + 1
                        break
                    if k == -1:
                        return None
                    j = k + 1
            pending = []
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def tokenize(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=PUNCT)
    lex.whitespace_split = True
    lex.whitespace = " \t\r"
    lex.commenters = ""
    return list(lex)


def is_op(tok):
    return tok != "" and all(ch in PUNCT for ch in tok)


def is_redirect(tok):
    # `>`, `>>`, `2>&1`'s `>&`, `<<`, `&>`; but `<(` and `>(` open a command.
    return (is_op(tok) and (tok[0] in "<>" or tok.startswith("&>"))
            and not tok.endswith("("))


def split_commands(tokens):
    """Words grouped per simple command, redirections and targets removed."""
    cmds, skip = [[]], False
    for t in tokens:
        if skip:
            skip = False
            continue
        if is_op(t):
            if is_redirect(t):
                skip = True
            else:
                cmds.append([])
            continue
        cmds[-1].append(t)
    return [c for c in cmds if c]


def substitutions(word):
    """The text of each `$(...)` inside one word (a quoted one stays one)."""
    found, start = [], 0
    while True:
        i = word.find("$(", start)
        if i == -1:
            return found
        depth, j = 1, i + 2
        while j < len(word) and depth:
            depth += {"(": 1, ")": -1}.get(word[j], 0)
            j += 1
        found.append(word[i + 2:j - 1] if depth == 0 else word[i + 2:])
        start = i + 2


def gh_record(args, shell_vars):
    """A record when `gh <args>` is a GitHub write, else None."""
    if not args:
        return None
    sub, act = args[0], (args[1] if len(args) > 1 else "")
    if (sub == "issue" and act in ("create", "comment", "edit")) or \
            (sub == "pr" and act in ("create", "comment", "edit", "review")):
        rest = args[2:]
        has_body, paths = False, []
        for j, a in enumerate(rest):
            if a in ("--body", "-b") or a.startswith(("--body=", "-b=")):
                has_body = True
            elif a in ("--body-file", "-F"):
                has_body = True
                if j + 1 < len(rest):
                    paths.append(rest[j + 1])
            elif a.startswith(("--body-file=", "-F=")):
                has_body = True
                paths.append(a.split("=", 1)[1])
        approve = (sub == "pr" and act == "review"
                   and any(a in ("--approve", "-a") or a.startswith("--approve=")
                           for a in rest))
    elif sub == "api":
        rest = args[1:]
        method = body_field = False
        paths = []
        for j, a in enumerate(rest):
            nxt = rest[j + 1] if j + 1 < len(rest) else ""
            if a in ("-X", "--method"):
                method = method or nxt.upper() in WRITE_METHODS
            elif a.startswith("--method="):
                method = method or a.split("=", 1)[1].upper() in WRITE_METHODS
            elif a.startswith("-X") and not a.startswith("--"):
                method = method or a[2:].lstrip("=").upper() in WRITE_METHODS
            elif a in ("-f", "-F", "--field", "--raw-field"):
                if nxt.startswith("body="):
                    body_field = True
                    if a in ("-F", "--field") and nxt.startswith("body=@"):
                        paths.append(nxt[len("body=@"):])
            else:
                for flag in ("--field=", "--raw-field=", "-f=", "-F="):
                    if a.startswith(flag + "body="):
                        body_field = True
                        if flag in ("--field=", "-F=") and a.startswith(flag + "body=@"):
                            paths.append(a[len(flag + "body=@"):])
        if not (any(API_PATH.search(a) for a in rest) and (method or body_field)):
            return None
        has_body, approve = body_field, False
    else:
        return None
    return {"approve": approve, "has_body": has_body,
            "paths": [expand(p, shell_vars) for p in paths]}


def analyze(text, shell_vars, depth=0):
    """Every GitHub write in a shell command, as records."""
    if depth > 4:
        return regex_records(text)
    stripped = strip_heredocs(text)
    try:
        tokens = tokenize(text if stripped is None else stripped)
    except ValueError:
        return regex_records(text)
    records = []
    # A backtick span may cross words, so it is cut from the text, not a word.
    if "`" in text:
        for span in text.split("`")[1::2]:
            records += analyze(span, shell_vars, depth + 1)
    for words in split_commands(tokens):
        for w in words:
            for inner in substitutions(w):
                records += analyze(inner, shell_vars, depth + 1)
        i = 0
        while True:
            while i < len(words) and ASSIGN.match(words[i]):
                name, _, value = words[i].partition("=")
                shell_vars[name] = expand(value, shell_vars)
                i += 1
            if i < len(words) and words[i] in WRAPPERS:
                i += 1
                while i < len(words) and words[i].startswith("-"):
                    i += 1
                continue
            break
        if i >= len(words):
            continue
        cmd0, args = words[i], words[i + 1:]
        base = cmd0.rsplit("/", 1)[-1]
        if cmd0 == "export":
            for a in args:
                if ASSIGN.match(a):
                    name, _, value = a.partition("=")
                    shell_vars[name] = expand(value, shell_vars)
        elif base in SHELLS:
            for j, a in enumerate(args):
                if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", a) and j + 1 < len(args):
                    records += analyze(args[j + 1], shell_vars, depth + 1)
                    break
        elif cmd0 == "eval":
            records += analyze(" ".join(args), shell_vars, depth + 1)
        elif base == "gh":
            rec = gh_record(args, shell_vars)
            if rec:
                records.append(rec)
    return records


raw = os.environ.get("GITHUB_WRITE_MARKER_EVENT", "")
try:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("not an object")
except Exception as exc:
    sys.stderr.write("github-write-marker: WARNING — the hook input is not a JSON "
                     "object (%s); allowing the call unchecked.\n" % exc.__class__.__name__)
    allow()

tool = event.get("tool_name")
tool_input = event.get("tool_input")
if not isinstance(tool, str) or not isinstance(tool_input, dict):
    allow()

# ── MCP ────────────────────────────────────────────────────────────────────
m = MCP_TOOL.match(tool)
if m:
    ev = tool_input.get("event")
    if isinstance(ev, str) and ev.strip().upper() == "APPROVE":
        block_approve()
    body = tool_input.get("body")
    if isinstance(body, str) and body.strip() and TOKEN not in body:
        block("this %s" % m.group(1))
    allow()

if tool != "Bash":
    allow()

# ── Bash ───────────────────────────────────────────────────────────────────
cmd = tool_input.get("command")
if not isinstance(cmd, str):
    allow()

try:
    records = analyze(cmd, {})
except Exception:
    records = regex_records(cmd)  # a tokenizer fault fails toward checking
if not records:
    allow()

if any(r["approve"] for r in records):
    block_approve()

if TOKEN in cmd:
    allow()

cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
unreadable = []
for path in [p for r in records for p in r["paths"]]:
    if path == "-":
        unreadable.append((path, "the body comes from stdin, which the hook "
                           "cannot read; put the marker in the command text "
                           "(the heredoc)"))
        continue
    if "$" in path:
        unreadable.append((path, "it holds a shell variable the hook could "
                           "not resolve"))
        continue
    full = path
    if cwd and not os.path.isabs(full):
        full = os.path.join(cwd, full)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            if TOKEN in fh.read():
                allow()
    except Exception as exc:
        unreadable.append((full, "it could not be read (%s)" % exc.__class__.__name__))

if not any(r["has_body"] for r in records):
    allow()

if unreadable:
    block_unreadable(unreadable)
block("this GitHub write")
PY

if [ "$rc" -eq 2 ]; then
    exit 2
fi
if [ "$rc" -ne 0 ]; then
    echo "github-write-marker: WARNING — the check exited $rc without a verdict; allowing the call unchecked." >&2
fi
exit 0
