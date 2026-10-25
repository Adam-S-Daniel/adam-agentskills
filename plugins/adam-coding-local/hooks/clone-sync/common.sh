#!/usr/bin/env bash
# common.sh - sourced by the clone-sync hooks (ADR 0017).
#
# launch_clone_sync <harness> <clone-sync.sh args...> starts clone-sync.sh
# DETACHED and returns at once, so a hook finishes in well under a second
# whatever the network does. Its output goes to a log under the cache dir,
# never to the hook's stdout: Codex treats stdout it cannot parse as an error,
# and Claude Code adds a SessionStart hook's stdout to the model's context.
#
# CLONE_SYNC_HOOK_DRY_RUN=<file> records the launch in <file> instead of
# starting anything; the tests use it so they never leave background work.

launch_clone_sync() {
  local harness="$1" here cache log
  shift
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -n "${CLONE_SYNC_HOOK_DRY_RUN:-}" ]]; then
    printf 'harness=%s %s\n' "$harness" "$*" >> "$CLONE_SYNC_HOOK_DRY_RUN"
    return 0
  fi
  cache="${XDG_CACHE_HOME:-$HOME/.cache}/clone-sync"
  mkdir -p "$cache" 2>/dev/null || return 0
  log="$cache/hook.log"
  # Keep the log bounded: one generation of history is enough to debug.
  if [[ -f "$log" ]] && [[ "$(wc -c < "$log")" -gt 1048576 ]]; then
    mv -f "$log" "$log.1" 2>/dev/null
  fi
  printf '%s harness=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$harness" "$*" >> "$log" 2>/dev/null
  # $BASH is the interpreter running this hook, so the child never resolves a
  # bare `bash` (on Windows that is WSL's launcher). setsid, where it exists,
  # moves the child out of the hook's process group, so a harness that kills
  # the group on its timeout does not take the sync with it.
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$BASH" "$here/clone-sync.sh" "$@" >> "$log" 2>&1 < /dev/null &
  else
    nohup "$BASH" "$here/clone-sync.sh" "$@" >> "$log" 2>&1 < /dev/null &
  fi
  return 0
}

# hook_dir_fallback <harness> - where the hook runs when its input names no
# cwd: Claude Code's project dir, else the working directory.
hook_dir_fallback() {
  if [[ "$1" == claude-code && -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    printf '%s\n' "$CLAUDE_PROJECT_DIR"
  else
    printf '%s\n' "$PWD"
  fi
}
