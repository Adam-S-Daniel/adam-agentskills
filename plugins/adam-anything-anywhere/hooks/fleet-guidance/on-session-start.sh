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
# HOW: it asks git for the HEAD commit of main (a commit-only shallow fetch into
# a private temp bare repo), downloads agents-md/base.md at that commit from
# raw.githubusercontent.com (both bounded), and installs it through the bundled
# fleet-memory.sh, stamped with the commit's committer time so deliveries order
# correctly against any other. Only the guidance text is fetched; the code that
# writes it is bundled and gated. When the fetch is off or fails, the bundled
# fleet-guidance.md beside it is installed instead and one extra line says so.
#
# Why git and not the REST API: in claude.ai cloud sessions the egress proxy
# answers api.github.com with HTTP 403 for any repo not attached to the session,
# public ones included, while git smart-HTTP and raw.githubusercontent.com pass.
#
# It never reads anything from the session's project directory. A repo the user
# opens can carry any data file, and this hook writes the GLOBAL user memory, so
# a file found there must not be able to reach it.
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

# Install the bundled copy, say so, and stop.
fall_back() {
  env -u FLEET_GUIDANCE_PAYLOAD -u FLEET_GUIDANCE_PAYLOAD_STAMP "$BASH" "$HERE/fleet-memory.sh"
  printf "fleet-guidance-source: the adam-anything-anywhere plugin's bundled copy (%s), which can be older than _agent-guidance main\n" "$1"
  exit 0
}

case "$(printf '%s' "${FLEET_GUIDANCE_FETCH:-}" | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) fall_back "fetching is off (FLEET_GUIDANCE_FETCH)" ;;
esac
command -v curl >/dev/null 2>&1 || fall_back "curl is not installed"
command -v git >/dev/null 2>&1 || fall_back "git is not installed"

# umask 077 in a subshell, so it never reaches the fleet-memory.sh run below.
tmp="$(umask 077 && mktemp -d "${TMPDIR:-/tmp}/fleet-guidance.XXXXXX" 2>/dev/null)" \
  || fall_back "cannot create a temporary directory"
trap 'rm -rf -- "$tmp"' EXIT
trap 'exit 0' HUP INT TERM

# git without any prompt, capped at 15 s where `timeout` exists (macOS has none;
# the hook's own 30 s limit still caps it there).
run_git() {
  if command -v timeout >/dev/null 2>&1; then
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never timeout 15 git "$@"
  else
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git "$@"
  fi
}

# The commit at the HEAD of main, not the last one touching base.md: its
# committer date is never earlier than the merge that delivered the current
# base.md, which a path-filtered history would get wrong for a merged branch.
# A commit-only fetch (depth 1, no trees, no blobs, no tags) into a bare repo
# only this run can see. --git-dir and never -C, so an inherited GIT_DIR cannot
# redirect it; no credential helper, no hooks. The user's other git config stays
# (a cloud session's proxy authentication may live there). No token of any kind.
bare="$tmp/g"
git init -q --bare --template= "$bare" >/dev/null 2>&1 \
  || fall_back "the commit lookup failed"
run_git --git-dir="$bare" \
    -c credential.helper= -c core.hooksPath=/dev/null \
    -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 \
    fetch -q --no-tags --depth=1 --filter=tree:0 \
    https://github.com/Adam-S-Daniel/_agent-guidance refs/heads/main >/dev/null 2>&1 \
  || fall_back "the commit lookup failed"
commit="$(git --git-dir="$bare" log -1 --format='%H %ct' FETCH_HEAD 2>/dev/null)" \
  || fall_back "the commit lookup failed"
# A Windows git can end the line with \r\n, and $(...) keeps the \r.
commit="${commit//$'\r'/}"
sha="${commit%% *}"
epoch="${commit#* }"
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$epoch" =~ ^[1-9][0-9]{0,9}$ ]] \
  || fall_back "the commit lookup failed"

# -q first, so curl ignores ~/.curlrc: a write-out there would print into the
# session, and a location line would turn redirects back on. No -L, and no token
# or auth header of any kind.
curl -q -fsS --proto '=https' --proto-redir '=https' --connect-timeout 3 --max-time 5 \
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
