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
# a private temp bare repo, under a watchdog), downloads agents-md/base.md at
# that commit from raw.githubusercontent.com (bounded by curl's own limits), and
# installs it through the bundled fleet-memory.sh, stamped with the commit's
# committer time so deliveries order correctly against any other. Only the
# guidance text is fetched; the code that writes it is bundled and gated. When
# the fetch is off or fails, the bundled fleet-guidance.md beside it is
# installed instead and one extra line says so.
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
# FLEET_GUIDANCE_FETCH_TIMEOUT=<1-60> is how many seconds the git fetch may run
# before the watchdog stops it and the bundled copy is installed (default 15;
# any other value means 15). Raise it for a slow link, but the plugin's hook
# entry allows 30 s in all and the download can take 5 s of that, so a value
# above 25 can outlast the hook and leave no verdict. The tests set it to 1 and
# stall the fetch to exercise the watchdog.
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

# Signal one process with TERM, and only an integer pid above 1: an empty, zero,
# negative or 1 pid would address a process group or init, not one process. Its
# direct children get the same signal first where pgrep exists: git fetch hands
# the transfer to a `git remote-https` child that outlives it, still waiting on
# the network, when only git is stopped.
stop_pid() {
  [[ "${1:-}" =~ ^[1-9][0-9]{0,9}$ ]] && [ "$1" -gt 1 ] || return 1
  local child
  if command -v pgrep >/dev/null 2>&1; then
    for child in $(pgrep -P "$1" 2>/dev/null); do
      if [[ "$child" =~ ^[1-9][0-9]{0,9}$ ]] && [ "$child" -gt 1 ]; then
        kill -TERM "$child" 2>/dev/null
      fi
    done
  fi
  kill -TERM "$1" 2>/dev/null
}

# Stop whatever this shell has running in the background and wait for it. It
# walks the job table instead of saved pids, so a signal that lands between a
# fork and the line that records its pid still finds the job.
# shellcheck disable=SC2329  # invoked by traps only
stop_jobs() {
  local p
  for p in $(jobs -rp 2>/dev/null); do stop_pid "$p"; done
  { wait; } 2>/dev/null
}

# Runs on every exit, a signal included: stops the fetch and its watcher if they
# are still running, then removes the temp dir.
# shellcheck disable=SC2329  # invoked by the EXIT trap below
cleanup() {
  stop_jobs
  rm -rf -- "$tmp"
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

# The fetch's time limit, in seconds: FLEET_GUIDANCE_FETCH_TIMEOUT when it is an
# integer from 1 to 60 (10# so a leading zero is not octal), else 15.
limit=15
if [[ "${FLEET_GUIDANCE_FETCH_TIMEOUT:-}" =~ ^[0-9]{1,2}$ ]]; then
  n=$((10#$FLEET_GUIDANCE_FETCH_TIMEOUT))
  if [ "$n" -ge 1 ] && [ "$n" -le 60 ]; then limit="$n"; fi
fi

# How every git call here is run. It drops the variables that name a repository
# (an inherited GIT_OBJECT_DIRECTORY would send the fetch's pack into another
# repo; GIT_WORK_TREE, GIT_COMMON_DIR and GIT_SHALLOW_FILE would make it fail),
# and it cannot ask a person for anything: no terminal prompt, and an empty
# GIT_ASKPASS, which ends git's askpass chain, so neither SSH_ASKPASS nor
# core.askPass is consulted (a VS Code terminal or a Linux desktop sets one, and
# a 401 would otherwise pop a dialog). GIT_CONFIG_* stays: a cloud session's
# settings can live there. GIT_ALLOW_PROTOCOL stays too: a user who exports it
# chooses their own protocols, and it takes precedence over the protocol.*
# settings below.
git_env=(env
  -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR -u GIT_INDEX_FILE
  -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES
  -u GIT_SHALLOW_FILE -u GIT_NAMESPACE -u GIT_GRAFT_FILE
  GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_ASKPASS=)

# The commit at the HEAD of main, not the last one touching base.md: its
# committer date is never earlier than the merge that delivered the current
# base.md, which a path-filtered history would get wrong for a merged branch.
# A commit-only fetch (depth 1, no trees, no blobs, no tags) into a bare repo
# only this run can see. --git-dir and never -C, so an inherited GIT_DIR cannot
# redirect it; no credential helper, no hooks, and https only (protocol.allow
# never, https always), so a user's url.<ssh>.insteadOf cannot send it over ssh
# with their key. The hook adds no token. The user's other git config stays (a
# cloud session's proxy authentication may live there), so git still applies
# their own url.<base>.insteadOf between https URLs or http.extraHeader.
bare="$tmp/g"
"${git_env[@]}" git init -q --bare --template= "$bare" >/dev/null 2>&1 \
  || fall_back "the commit lookup failed"

# The fetch runs in the background under a watchdog, on every platform. git has
# no connect timeout (http.lowSpeed* applies only once data flows), macOS has no
# `timeout`, and bash defers a trap while a foreground child runs, so a
# blackholed proxy would hold the hook until git gave up and a SIGTERM would sit
# unhandled. `wait` is a builtin, so the trap above fires at once. The watcher's
# stdio is detached so a sleep it leaves behind can never hold the hook's stdout
# open, and its TERM trap stops that sleep and waits for it.
"${git_env[@]}" git --git-dir="$bare" \
    -c credential.helper= -c core.askPass= -c core.hooksPath=/dev/null \
    -c protocol.allow=never -c protocol.https.allow=always \
    -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 \
    fetch -q --no-tags --depth=1 --filter=tree:0 \
    https://github.com/Adam-S-Daniel/_agent-guidance refs/heads/main >/dev/null 2>&1 &
gpid=$!
(
  trap 'stop_jobs; exit 0' HUP TERM
  sleep "$limit" &
  # Only a sleep that ran its full length stops git: a sleep stopped by the hook
  # (git ended first) must not signal a pid that has been reaped.
  { wait "$!"; } 2>/dev/null && stop_pid "$gpid"
) </dev/null >/dev/null 2>&1 &
wpid=$!
{ wait "$gpid"; } 2>/dev/null
git_status=$?
stop_pid "$wpid"
{ wait "$wpid"; } 2>/dev/null
[ "$git_status" -eq 0 ] || fall_back "the commit lookup failed"

# --no-show-signature: a user's log.showSignature=true would put gpg output
# ahead of the line.
commit="$("${git_env[@]}" git --git-dir="$bare" log -1 --no-show-signature \
    --format='%H %ct' FETCH_HEAD 2>/dev/null)" \
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
