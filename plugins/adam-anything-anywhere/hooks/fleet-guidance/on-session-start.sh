#!/usr/bin/env bash
# on-session-start.sh - install the fleet guidance into Claude Code's user
# memory when a session starts (ADR 0023).
#
# WHY: the fleet's managed guidance (agents-md/base.md in
# Adam-S-Daniel/_agent-guidance) reaches a session through a fleet-memory.sh
# SessionStart hook that each fleet repo carries. A session that starts with no
# fleet repo attached runs no such hook and gets no guidance. This plugin's
# hook closes that gap.
#
# HOW: when the session's own checkout carries .claude/hooks/fleet-guidance.md
# (CLAUDE_PROJECT_DIR), it installs that file, with no network: the repo's own
# hook delivers the same bytes with the same git stamp, so the two agree.
# Otherwise it fetches the CURRENT base.md from GitHub (one commits request, one
# raw download, both bounded) and installs it through the bundled
# fleet-memory.sh, stamped with the commit time of main's HEAD so deliveries
# order correctly against any other. Only the guidance text is fetched; the code
# that writes it is bundled and gated. When the fetch is off or fails, the
# bundled fleet-guidance.md beside it is installed instead and one extra line
# says so.
#
# fleet-memory.sh and fleet-guidance.md next to this file are verbatim copies
# from _agent-guidance, refreshed only by a reviewed pull request. Never edit
# them here.
#
# Knobs: FLEET_GUIDANCE_FETCH=0|false|no|off installs the bundled copy without
# touching the network. FLEET_GUIDANCE_SKIP (any value fleet-memory.sh treats as
# on) removes the block and installs nothing. A nonempty FLEET_GUIDANCE_PAYLOAD
# means another delivery owns the session, so this hook does nothing.
#
# Never reads stdin. Always exits 0: a broken delivery must not break a session.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HERE=""
if [ -z "$HERE" ]; then
  echo "fleet-guidance: DEGRADED — the adam-anything-anywhere plugin hook cannot resolve its own directory"
  exit 0
fi

# Another delivery (for example an eval harness) already owns this session.
if [ -n "${FLEET_GUIDANCE_PAYLOAD:-}" ]; then
  exit 0
fi

# The same rule fleet-memory.sh applies: these values mean off, anything else on.
case "${FLEET_GUIDANCE_SKIP:-}" in
  ""|0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo]|[Oo][Ff][Ff]) ;;
  *)
    env -u FLEET_GUIDANCE_PAYLOAD -u FLEET_GUIDANCE_PAYLOAD_STAMP "$BASH" "$HERE/fleet-memory.sh"
    exit 0
    ;;
esac

# Prefer the session's own checkout. In a fleet repo session the repo's own hook
# delivers this same file with its git stamp; fetching main instead would race
# it with a different version, and in a branch checkout of _agent-guidance would
# replace the branch's guidance with main's. No network.
checkout_payload="${CLAUDE_PROJECT_DIR:-}/.claude/hooks/fleet-guidance.md"
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$checkout_payload" ] \
    && [ -r "$checkout_payload" ] && [ -s "$checkout_payload" ]; then
  env -u FLEET_GUIDANCE_PAYLOAD_STAMP FLEET_GUIDANCE_PAYLOAD="$checkout_payload" \
    "$BASH" "$HERE/fleet-memory.sh"
  exit 0
fi

# Install the bundled copy, say so, and stop.
fall_back() {
  env -u FLEET_GUIDANCE_PAYLOAD -u FLEET_GUIDANCE_PAYLOAD_STAMP "$BASH" "$HERE/fleet-memory.sh"
  printf "fleet-guidance-source: the adam-anything-anywhere plugin's bundled copy (%s), which can be older than _agent-guidance main\n" "$1"
  exit 0
}

# hook_python - print the first interpreter that really runs Python 3.6+.
# Copied from adam-coding-local's hooks/lib/harness.sh. Chosen by RUNNING it,
# not by `command -v`: on Windows, python3 can be the Microsoft Store stub,
# which resolves, prints "Python was not found" and exits 49.
hook_python() {
  local candidate
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 6))' \
        </dev/null >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

case "$(printf '%s' "${FLEET_GUIDANCE_FETCH:-}" | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) fall_back "fetching is off (FLEET_GUIDANCE_FETCH)" ;;
esac
command -v curl >/dev/null 2>&1 || fall_back "curl is not installed"
py="$(hook_python)" || fall_back "no working Python"

# umask 077 in a subshell, so it never reaches the fleet-memory.sh run below.
tmp="$(umask 077 && mktemp -d "${TMPDIR:-/tmp}/fleet-guidance.XXXXXX" 2>/dev/null)" \
  || fall_back "cannot create a temporary directory"
trap 'rm -rf -- "$tmp"' EXIT
trap 'exit 0' HUP INT TERM

# The commit at the HEAD of main, not the last one touching base.md: its
# committer date is never earlier than the merge that delivered the current
# base.md, which a path-filtered history would get wrong for a merged branch.
# No -L, and no token or auth header of any kind.
curl -fsS --proto '=https' --proto-redir '=https' --connect-timeout 3 --max-time 5 \
    --max-filesize 1048576 \
    -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
    -o "$tmp/commit.json" \
    'https://api.github.com/repos/Adam-S-Daniel/_agent-guidance/commits?sha=main&per_page=1' \
  || fall_back "the GitHub API request failed"

# One line out, "<sha> <epoch>". Any failure or exception prints nothing.
# (Python on Windows ends the line with \r\n, and $(...) keeps the \r.)
parsed="$("$py" -c '
import calendar, json, re, sys, time
try:
    with open(sys.argv[1], "rb") as handle:
        data = json.loads(handle.read().decode("utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("not a non-empty list")
    sha = data[0]["sha"]
    date = data[0]["commit"]["committer"]["date"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("bad sha")
    if not isinstance(date, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", date):
        raise ValueError("bad date")
    epoch = calendar.timegm(time.strptime(date, "%Y-%m-%dT%H:%M:%SZ"))
    if epoch <= 0:
        raise ValueError("bad epoch")
    sys.stdout.write("%s %d\n" % (sha, epoch))
except Exception:
    sys.exit(1)
' "$tmp/commit.json" 2>/dev/null)" || parsed=""
parsed="${parsed//$'\r'/}"
sha="${parsed%% *}"
epoch="${parsed#* }"
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$epoch" =~ ^[1-9][0-9]{0,9}$ ]] \
  || fall_back "the GitHub API answer was not a commit"

curl -fsS --proto '=https' --proto-redir '=https' --connect-timeout 3 --max-time 5 \
    --max-filesize 1048576 \
    -o "$tmp/base.md" \
    "https://raw.githubusercontent.com/Adam-S-Daniel/_agent-guidance/$sha/agents-md/base.md" \
  || fall_back "the base.md download failed"

# Nonempty, at most 1 MiB, and starting with the exact header every base.md has
# (compared as bytes, the way fleet-memory.sh's emit_payload does).
header=$'# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**'
size="$(wc -c < "$tmp/base.md" 2>/dev/null | tr -d ' ')"
n="$(LC_ALL=C; printf '%s' "${#header}")"
if [[ ! "$size" =~ ^[0-9]+$ ]] || [ "$size" -eq 0 ] || [ "$size" -gt 1048576 ] \
    || [ "$(head -c "$n" "$tmp/base.md"; printf x)" != "${header}x" ]; then
  fall_back "the downloaded base.md failed validation"
fi

env -u FLEET_GUIDANCE_PAYLOAD_STAMP FLEET_GUIDANCE_PAYLOAD="$tmp/base.md" \
  FLEET_GUIDANCE_PAYLOAD_STAMP="$epoch" "$BASH" "$HERE/fleet-memory.sh"
exit 0
