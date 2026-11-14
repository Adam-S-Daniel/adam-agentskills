#!/bin/bash
# The claude.ai cloud environment setup, kept here so it changes by reviewed
# pull request instead of by re-pasting. The environment's Setup script field
# holds only the short launcher in docs/multi-repo-delivery.md, which runs this
# file from the attached checkout (or from main when this repo is not attached).
#
# It delivers the fleet's pinned skills in a multi-repo session. Hooks resolve
# from <cwd>/.claude and $HOME/.claude only, never from the repos below cwd, so
# the wiring it writes cannot be committed to any of them (ADR 0005).
#
# THE PROJECT DIR IS HARDCODED, and the two obvious ways to derive it are both
# wrong HERE. A setup script runs as root before Claude Code launches, and
# measured in a hosted session: `CLAUDE_PROJECT_DIR` is UNSET — Claude Code sets
# it when it invokes a hook, which this is not — and `HOME` is `/root`, not the
# project dir's parent.
#
# THE WIRING GOES IN USER SCOPE, `/root/.claude/settings.json`. Project threads
# (claude.ai Projects) ignore `<project>/.claude/settings.json` but honor the
# user-scope file, measured 2026-10-31 with a Stop hook in each: only the
# user-scope one fired. The harness's own hooks in `launcher-settings.json`
# kept firing beside it, so the two merge rather than displace each other.
#
# Getting it right the first time matters more than usual, because the setup
# script runs ONCE. Anthropic snapshots the filesystem afterwards and later
# sessions skip the script, so a wrong path is baked into the snapshot and
# persists until the pasted launcher changes or the cache expires (~7 days).
# A merged change to THIS file reaches an environment on that same schedule.
#
# Safe in a single-repo session too: every command looks only in the project
# dir's CHILDREN, and there the project dir IS the repo, which has none, so the
# commands do nothing and the repo's own committed `.claude/settings.json`
# fires as it does today.
project=/home/user
user_claude=/root/.claude

# NOT `set -eu`. A setup script that exits non-zero makes the SESSION FAIL TO
# START, and skills not loading is by far the lesser failure — hence `|| true`
# here and the unconditional `exit 0` at the end.
mkdir -p "$user_claude" || true

# An older copy of this script wired the project scope instead. Remove that
# file, but only if it is ours, so a multi-repo session does not run each hook
# twice.
if grep -qs 'skills-bootstrap.sh' "$project/.claude/settings.json"; then
  rm -f "$project/.claude/settings.json"
fi

# The eval harness (skills-evals' run_eval.py) runs every agent trial under
# Claude Code's sandbox with failIfUnavailable, which needs bubblewrap and
# socat; without them every trial ends `sandbox_unavailable` (eval runner
# routine, 2026-10-20). Needs the environment's default package-manager list.
apt-get update -q >/dev/null 2>&1 && apt-get install -y -q bubblewrap socat >/dev/null 2>&1 \
  || echo "wiring: bubblewrap/socat install failed; sandboxed evals will refuse to run"

cat > "$user_claude/settings.json" <<'JSON'
{
  "skillListingBudgetFraction": 0.02,
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|fork",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'for h in \"${CLAUDE_PROJECT_DIR:-/home/user}\"/*/.claude/hooks/skills-bootstrap.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done; exit 0'",
            "timeout": 90
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'for h in \"${CLAUDE_PROJECT_DIR:-/home/user}\"/*/.claude/hooks/agent-checks-guard.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done; exit 0'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
JSON
echo "wiring: wrote $user_claude/settings.json"

# Config-based git hooks need Git 2.54+ (ADR 0020). Never fatal: without them
# CI still runs the same checks.
if ! git version | awk '{split($3,v,"."); exit !(v[1]>2 || (v[1]==2 && v[2]>=54))}'; then
  apt-get install -y -q libcurl4-openssl-dev >/dev/null 2>&1 || true
  src=$(mktemp -d)
  if git clone -q --depth 1 --branch v2.56.0 https://github.com/git/git "$src/git" \
     && make -C "$src/git" -j"$(nproc)" prefix=/usr/local sysconfdir=/etc \
          NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1 install >/dev/null 2>&1; then
    echo "git: $(/usr/local/bin/git version)"
  else
    echo "git: build failed; local hooks will not run, CI still checks" >&2
  fi
  rm -rf "$src"
fi
for f in "$project"/*/scripts/install-agent-hooks.sh; do
  [ -f "$f" ] && { bash "$f" --system || true; break; }
done

# ---- BEGIN owner block: fleet guidance (_agent-guidance ADR 0043) ----
# The pin is these two variables and nothing else. To bump it, fetch first,
# then take _agent-guidance's current main commit and that file's digest:
#   git -C <checkout> rev-parse origin/main
#   git -C <checkout> show origin/main:.claude/hooks/fleet-memory.sh | sha256sum
# Merge the bump; environments pick it up when their setup snapshot expires.
fleet_pin_commit=0ef732fdbd825e35ad6bd853855fe158b61ada2b
fleet_pin_sha256=cfc01078b9d06990c2e458bade9aebffe10e7e25a97eaec23a59e2d3f28a2241
# The verified installer stays here: it re-reads its own file after it exits
# (to write a delivery receipt), so it must outlive this script's temp dir.
fleet_hook_dir=/opt/fleet-guidance
fleet_guidance_install() (
  fail() { echo "fleet-guidance: setup skipped ($1)"; exit 0; }
  raw=https://raw.githubusercontent.com/Adam-S-Daniel/_agent-guidance
  command -v curl >/dev/null 2>&1 && command -v git >/dev/null 2>&1 \
    && command -v sha256sum >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1 \
    || fail "curl, git, timeout or sha256sum missing"
  case "$user_claude" in
    */.claude) ;;
    *) fail "unexpected Claude directory" ;;
  esac
  umask 077
  mkdir -p "$fleet_hook_dir" && chmod 700 "$fleet_hook_dir" || fail "no installer directory"
  tmp=$(mktemp -d) || fail "no temporary directory"
  dl=$(mktemp "$fleet_hook_dir/.download.XXXXXX") || fail "no installer directory"
  trap 'rm -rf "$tmp"; rm -f "$dl"' EXIT
  hook="$fleet_hook_dir/fleet-memory-$fleet_pin_sha256.sh"
  # The installer: reuse a verified copy, else fetch the pin, verify, then move
  # it into place (same directory, so the move is atomic). Run only verified bytes.
  if [ -f "$hook" ] && [ ! -L "$hook" ] \
      && [ "$(sha256sum < "$hook" | cut -d' ' -f1)" = "$fleet_pin_sha256" ]; then
    :
  else
    curl -q -fsS --proto '=https' --connect-timeout 5 --max-time 20 \
      --max-filesize 4194304 -o "$dl" \
      "$raw/$fleet_pin_commit/.claude/hooks/fleet-memory.sh" || fail "installer download failed"
    [ "$(sha256sum < "$dl" | cut -d' ' -f1)" = "$fleet_pin_sha256" ] \
      || fail "installer digest mismatch; not run"
    mv -f "$dl" "$hook" || fail "installer could not be stored"
  fi
  # The payload: main's HEAD commit (a commit-only fetch; the proxy refuses the
  # REST API for repos not attached to the session), base.md at that commit, and
  # its committer time as the stamp, as the plugin's hook does.
  g=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR -u GIT_INDEX_FILE
     -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES
     -u GIT_SHALLOW_FILE -u GIT_NAMESPACE -u GIT_GRAFT_FILE
     GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_ASKPASS=)
  "${g[@]}" git init -q --bare --template= "$tmp/g" >/dev/null 2>&1 || fail "commit lookup failed"
  "${g[@]}" timeout -k 5 30 git --git-dir="$tmp/g" \
    -c credential.helper= -c core.askPass= -c core.hooksPath=/dev/null \
    -c protocol.allow=never -c protocol.https.allow=always \
    fetch -q --no-tags --depth=1 --filter=tree:0 \
    https://github.com/Adam-S-Daniel/_agent-guidance refs/heads/main >/dev/null 2>&1 \
    || fail "commit lookup failed"
  line=$("${g[@]}" git --git-dir="$tmp/g" log -1 --no-show-signature --format='%H %ct' FETCH_HEAD 2>/dev/null) \
    || fail "commit lookup failed"
  line=${line//$'\r'/}
  head_sha=${line%% *}
  stamp=${line#* }
  [[ "$head_sha" =~ ^[0-9a-f]{40}$ && "$stamp" =~ ^[1-9][0-9]{0,9}$ ]] || fail "commit lookup failed"
  curl -q -fsS --proto '=https' --connect-timeout 5 --max-time 20 \
    --max-filesize 1048576 -o "$tmp/base.md" \
    "$raw/$head_sha/agents-md/base.md" || fail "base.md download failed"
  # The plugin's own check: the exact header every base.md starts with.
  header=$'# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**'
  n="$(LC_ALL=C; printf '%s' "${#header}")"
  [ "$(head -c "$n" "$tmp/base.md"; printf x)" = "${header}x" ] || fail "base.md failed validation"
  # HOME is the home the session runs as, the parent of the .claude dir above.
  env HOME="${user_claude%/.claude}" FLEET_GUIDANCE_OWNER=1 \
    FLEET_GUIDANCE_PAYLOAD="$tmp/base.md" FLEET_GUIDANCE_PAYLOAD_STAMP="$stamp" \
    bash "$hook" || fail "installer exited nonzero"
  echo "fleet-guidance-source: _agent-guidance@$head_sha"
)
fleet_out=$(fleet_guidance_install) || fleet_out+=$'\n'"fleet-guidance: setup skipped (unexpected failure)"
printf '%s\n' "${fleet_out#$'\n'}"
# ---- END owner block ----

# ---- BEGIN setup record ----
# Which launcher and which version of this script ran, so a cached environment
# can be traced. The record goes to $user_claude/cloud-env-setup.json and, where
# the usage sink is configured with proxy signing, to the sink as a `setups`
# record (skills-evals' scripts/ingest_usage_sink.py, validator version 4+).
# The launcher passes the CLOUD_SETUP_* values. Every value is checked against
# a fixed shape before it is written, so nothing needs escaping, and only the
# HTTP status is printed. Never fails setup.
setup_record() (
  hex() { [[ "$1" =~ ^[0-9a-f]{$2}$ ]] && printf '%s' "$1"; }
  rand() { od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }
  version=${CLOUD_SETUP_LAUNCHER_VERSION:-}
  source=${CLOUD_SETUP_SOURCE:-}
  [[ "$version" =~ ^[1-9][0-9]{0,3}$ && "$source" =~ ^(checkout|download)$ ]] \
    || { echo "cloud-env-setup: no launcher values; record skipped"; exit 0; }
  # The installer's own outcome, or `failed` when this script's block gave up.
  status=$(sed -n -e 's/^fleet-guidance: setup skipped.*/failed/p' \
    -e 's/^fleet-guidance: DEGRADED.*/degraded/p' \
    -e 's/^fleet-guidance: \(installed\|current\|skipped\)[ (].*/\1/p' <<<"$fleet_out" | head -n 1)
  guidance_commit=$(sed -n 's/^fleet-guidance-source: _agent-guidance@\([0-9a-f]\{40\}\)$/\1/p' <<<"$fleet_out" | head -n 1)
  id=$(hex "$(rand)" 32) || { echo "cloud-env-setup: no random id; record skipped"; exit 0; }
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  launcher_sha=$(hex "${CLOUD_SETUP_LAUNCHER_SHA256:-}" 64)
  script_sha=$(hex "$(sha256sum < "$0" 2>/dev/null | cut -c1-64)" 64) \
    || { echo "cloud-env-setup: cannot hash this script; record skipped"; exit 0; }
  script_commit=$(hex "${CLOUD_SETUP_COMMIT:-}" 40)
  pin=$(hex "${fleet_pin_commit:-}" 40)
  record=$(printf '{"id":"%s","ts":"%s","launcher_version":%s,"launcher_sha256":"%s","script_source":"%s","script_sha256":"%s","script_commit":"%s","fleet_pin_commit":"%s","fleet_guidance_commit":"%s","fleet_guidance":"%s"}' \
    "$id" "$ts" "$version" "$launcher_sha" "$source" "$script_sha" "$script_commit" "$pin" "$guidance_commit" "$status")
  printf '%s\n' "$record" > "$user_claude/cloud-env-setup.json" 2>/dev/null || true
  short=${launcher_sha:0:12}
  echo "cloud-env-setup: launcher v$version ${short:-(no hash)}, script ${script_sha:0:12} from $source${script_commit:+ @${script_commit:0:12}}"
  bucket=${USAGE_SINK_BUCKET:-} region=${USAGE_SINK_REGION:-}
  if [ "${USAGE_SINK_SIGNING:-}" != proxy ] \
      || ! [[ "$bucket" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ && "$region" =~ ^[a-z]{2}(-[a-z0-9]+){1,4}$ ]]; then
    echo "cloud-env-setup: usage sink not configured; record kept locally"
    exit 0
  fi
  key=$(hex "$(rand)" 32) || { echo "cloud-env-setup: no random key; record kept locally"; exit 0; }
  body=$(printf '{"v":1,"kind":"usage-drop","sent":"%s","guidance":[],"skills":[],"setups":[%s]}' "$ts" "$record")
  code=$(printf '%s' "$body" | curl -q -sS -o /dev/null -w '%{http_code}' --proto '=https' \
    --connect-timeout 5 --max-time 20 -X PUT \
    -H 'If-None-Match: *' -H 'Content-Type: application/json' \
    -H "x-amz-content-sha256: $(printf '%s' "$body" | sha256sum | cut -c1-64)" \
    --data-binary @- "https://$bucket.s3.$region.amazonaws.com/drop/${ts:0:10}/$key.json" 2>/dev/null) \
    || true
  echo "cloud-env-setup: record sent to the usage sink (HTTP ${code:-none})"
)
setup_record || echo "cloud-env-setup: record skipped (unexpected failure)"
# ---- END setup record ----
exit 0
