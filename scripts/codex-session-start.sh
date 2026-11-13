#!/usr/bin/env bash
# codex-session-start.sh - what local Codex's SessionStart hook runs to deliver
# the fleet guidance (_agent-guidance ADR 0043, decision 2).
#
# `setup.sh --owner-machine` copies this file, unchanged, to
# ${XDG_DATA_HOME:-$HOME/.local/share}/fleet-guidance/codex-session-start.sh,
# and _agent-guidance's register-codex-hook.sh points Codex's user-level
# hooks.json at that copy. The copy stays small and stable on purpose: at each
# run it resolves the CURRENTLY installed adam-anything-anywhere@adam-agentskills
# from ~/.claude/plugins/installed_plugins.json and runs THAT install's
# hooks/fleet-guidance/on-session-start.sh, which fetches the guidance and
# writes ~/.codex/AGENTS.md. So fixes arrive with plugin updates and only
# released code that passed the plugin-runtime gate ever runs, and no consumer
# repo has to carry a copy of the hook.
#
# Codex reads a hook's stdout, so this prints nothing of its own: whatever the
# plugin hook prints passes through. It always exits 0, and when no plugin
# install is found it says so in one line on stderr.
set -u

PLUGIN_KEY="adam-anything-anywhere@adam-agentskills"
HOOK_REL="hooks/fleet-guidance/on-session-start.sh"

note() { printf 'fleet-guidance launcher: %s\n' "$*" >&2; }

# A Python that really runs (on Windows, python3 can be the Store stub).
find_python() {
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

# install_paths - the installPath of every install of PLUGIN_KEY, user scope
# first, then newest first (lastUpdated, an ISO time, else the version string).
# JSON is parsed with json, never matched as text.
install_paths() {
  local py="$1" registry="$HOME/.claude/plugins/installed_plugins.json"
  [[ -f "$registry" ]] || return 0
  "$py" - "$registry" "$PLUGIN_KEY" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8-sig") as handle:
        data = json.load(handle)
    entries = data["plugins"][sys.argv[2]]
except Exception:
    sys.exit(0)
if not isinstance(entries, list):
    sys.exit(0)
entries = [e for e in entries if isinstance(e, dict) and isinstance(e.get("installPath"), str)
           and "\n" not in e["installPath"]]
def newest(e):
    return (str(e.get("lastUpdated") or ""), str(e.get("version") or ""))
entries.sort(key=newest, reverse=True)
entries.sort(key=lambda e: e.get("scope") != "user")
for e in entries:
    print(e["installPath"])
PYEOF
}

if ! py="$(find_python)"; then
  note "no working Python to read installed_plugins.json; nothing delivered"
  exit 0
fi

while IFS= read -r root; do
  root="${root%$'\r'}"
  [[ -n "$root" ]] || continue
  if [[ "$root" == [A-Za-z]:* ]] && command -v cygpath >/dev/null 2>&1; then
    root="$(cygpath -u "$root")"
  fi
  script="$root/$HOOK_REL"
  if [[ -f "$script" ]]; then
    # stdin closed: Codex pipes the hook's input here and the plugin hook never reads it.
    FLEET_GUIDANCE_OWNER=1 "$BASH" "$script" </dev/null
    exit 0
  fi
done < <(install_paths "$py")

note "$PLUGIN_KEY is not installed in this home, so nothing was delivered (run: claude plugin install adam-anything-anywhere@adam-agentskills)"
exit 0
