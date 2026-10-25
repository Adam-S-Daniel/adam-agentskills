#!/usr/bin/env bash
# launcher.sh - what the `adam-clone-sync` scheduled task runs (ADR 0017).
#
# Register-CloneSyncTask.ps1 copies this file, unchanged, to
# %LOCALAPPDATA%\adam-agentskills\clone-sync-launcher.sh, and the task runs
# that copy in Git Bash. The copy stays small and stable on purpose: at each
# run it resolves the CURRENTLY installed adam-coding-local@adam-agentskills
# from ~/.claude/plugins/installed_plugins.json and runs THAT install's
# hooks/clone-sync/clone-sync.sh --all. So fixes arrive with plugin updates,
# and only released code that passed the plugin-runtime gate (ADR 0016) ever
# runs, never a checkout's working tree.
#
# Then, if `wsl.exe --list --running --quiet` names a running distro, it runs
# the same resolution inside each one against that distro's home, limited to
# its ~/repos (the Windows clones were just synced from this side). It never
# boots WSL: a distro that is not already running is skipped.
#
# It exits 0 in every case, silently when the plugin is not installed, and
# writes one log line per decision under ${XDG_CACHE_HOME:-$HOME/.cache}/
# clone-sync/scheduled.log.
#
#   launcher.sh               this home, then running WSL distros
#   launcher.sh --local-only  this home only (how the WSL side is invoked)
set -u

PLUGIN_KEY="adam-coding-local@adam-agentskills"
LOCAL_ONLY=0
[[ "${1:-}" == --local-only ]] && LOCAL_ONLY=1

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/clone-sync"
mkdir -p "$CACHE_DIR" 2>/dev/null
LOG="$CACHE_DIR/scheduled.log"
log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG" 2>/dev/null
}

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

# install_path - the installPath of the user-scope install of PLUGIN_KEY, or
# nothing. JSON is parsed with json, never matched as text.
install_path() {
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
entries = [e for e in entries if isinstance(e, dict) and isinstance(e.get("installPath"), str)]
chosen = [e for e in entries if e.get("scope") == "user"] or entries
if chosen and "\n" not in chosen[0]["installPath"]:
    print(chosen[0]["installPath"])
PYEOF
}

run_local() {
  local py root script
  if ! py="$(find_python)"; then
    log "skipped: no working Python to read installed_plugins.json"
    return 0
  fi
  root="$(install_path "$py" | tr -d '\r')"
  if [[ -z "$root" ]]; then
    log "skipped: $PLUGIN_KEY is not installed in this home"
    return 0
  fi
  if [[ "$root" == [A-Za-z]:* ]] && command -v cygpath >/dev/null 2>&1; then
    root="$(cygpath -u "$root")"
  fi
  script="$root/hooks/clone-sync/clone-sync.sh"
  if [[ ! -f "$script" ]]; then
    log "skipped: the installed $PLUGIN_KEY has no hooks/clone-sync/clone-sync.sh"
    return 0
  fi
  log "running the installed clone-sync.sh --all"
  "$BASH" "$script" --all >> "$LOG" 2>&1
  log "clone-sync.sh exited $?"
}

run_wsl() {
  local running distro self
  command -v wsl.exe >/dev/null 2>&1 || return 0
  # wsl.exe prints UTF-16LE; dropping the NULs and CRs leaves one name a line.
  running="$(wsl.exe --list --running --quiet 2>/dev/null | tr -d '\000\r')"
  if [[ -z "$running" ]]; then
    log "skipped WSL: no distro is running"
    return 0
  fi
  if command -v cygpath >/dev/null 2>&1; then
    self="$(cygpath -w "$0")"
  else
    self="$0"
  fi
  case "$self" in
    *\'*) log "skipped WSL: the launcher path contains a quote"; return 0 ;;
  esac
  while IFS= read -r distro; do
    [[ -n "$distro" ]] || continue
    case "$distro" in docker-desktop*) continue ;; esac
    log "running in WSL distro $distro"
    # -d names a distro that is ALREADY running, so this never boots one.
    # Inside it, the same launcher resolves that home's install and syncs
    # only that home's ~/repos.
    MSYS_NO_PATHCONV=1 wsl.exe -d "$distro" -e bash -lc \
      "CLONE_SYNC_ROOTS=\"\$HOME/repos/*\" exec bash \"\$(wslpath -u '$self')\" --local-only" \
      < /dev/null >> "$LOG" 2>&1
    log "WSL distro $distro exited $?"
  done <<< "$running"
}

run_local
[[ "$LOCAL_ONLY" == 1 ]] || run_wsl
exit 0
