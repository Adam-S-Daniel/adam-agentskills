#!/usr/bin/env bash
# Install the fleet's config-based git hooks for this machine (ADR 0020).
#
#   bash scripts/install-agent-hooks.sh [--system] [--uninstall]
#
# Writes ~/.config/git/agent-hooks.inc, which defines one git hook per event
# that runs `.githooks/agent-checks <event>` when the repo has that file, and
# adds `includeIf "hasconfig:remote.*.url:..."` entries to the global git
# config (or the system one with --system) so the file is read ONLY in repos
# whose remote belongs to the fleet's two owners. A third-party clone never
# runs its own .githooks/agent-checks.
#
# Fails safe on purpose. The retired sync-skills hook was a global hook
# pointing at an absolute script path, and every push on the machine failed
# once the script moved (ADR 0001, ADR 0014). Here nothing points at an
# absolute path: git ignores a missing include file, and a repo without the
# runner is a no-op, so no state this script leaves behind can break a push.
#
# Config-based hooks need Git 2.54+. Older git ignores the hook.* keys, so
# installing early is harmless; the script says so and CI still checks.
#
# Idempotent: re-running rewrites the include file and adds only missing
# entries. --uninstall removes the entries and the file.
set -u

scope=--global
action=install
for arg in "$@"; do
  case "$arg" in
    --system) scope=--system ;;
    --uninstall) action=uninstall ;;
    -h|--help)
      sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "install-agent-hooks: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

events="pre-commit pre-push"
owners="Adam-S-Daniel adam-s-daniel jodidaniel"
url_prefixes="https://github.com/ git@github.com: ssh://git@github.com/"

inc_file="$HOME/.config/git/agent-hooks.inc"
# Git expands the ~ itself when it reads the include, which keeps the entry
# valid on Git for Windows, where $HOME is an MSYS path git cannot open.
# shellcheck disable=SC2088
inc_value='~/.config/git/agent-hooks.inc'

conditions() {
  local owner prefix
  for owner in $owners; do
    for prefix in $url_prefixes; do
      printf 'hasconfig:remote.*.url:%s%s/**\n' "$prefix" "$owner"
    done
  done
}

if [ "$action" = uninstall ]; then
  while IFS= read -r cond; do
    git config "$scope" --unset-all "includeIf.$cond.path" 2>/dev/null || true
  done < <(conditions)
  rm -f "$inc_file"
  echo "install-agent-hooks: removed"
  exit 0
fi

mkdir -p "$(dirname "$inc_file")"
{
  echo "# Written by adam-agentskills scripts/install-agent-hooks.sh (ADR 0020)."
  echo "# Re-run that script to update; edits here are overwritten."
  for event in $events; do
    printf '[hook "agent-checks-%s"]\n' "$event"
    printf '\tevent = %s\n' "$event"
    printf '\tcommand = "f=.githooks/agent-checks; [ -f \\"$f\\" ] || exit 0; exec bash \\"$f\\" %s"\n' "$event"
  done
} > "$inc_file"
echo "install-agent-hooks: wrote $inc_file"

while IFS= read -r cond; do
  existing=$(git config "$scope" --get-all "includeIf.$cond.path" 2>/dev/null) || existing=""
  if ! grep -qxF -- "$inc_value" <<< "$existing"; then
    git config "$scope" --add "includeIf.$cond.path" "$inc_value"
    echo "install-agent-hooks: added includeIf for ${cond#hasconfig:remote.*.url:}"
  fi
done < <(conditions)

version=$(git version | sed -n 's/^git version \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')
read -r major minor <<< "${version:-0 0}"
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 54 ]; }; then
  echo "install-agent-hooks: WARNING: $(git version) ignores config-based hooks (needs 2.54+)." >&2
  echo "install-agent-hooks: nothing runs locally until git is upgraded; CI still runs the checks." >&2
fi
exit 0
