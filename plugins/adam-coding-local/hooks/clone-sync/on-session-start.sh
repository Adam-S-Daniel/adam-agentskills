#!/usr/bin/env bash
# on-session-start.sh - SessionStart hook: sync this machine's clean clones of
# the repo the session starts in (ADR 0017).
#
# Runs for every harness. The directory is the input's `cwd`; without one,
# Claude Code's CLAUDE_PROJECT_DIR, else the working directory. clone-sync.sh
# resolves that directory's origin itself, detached, so no git runs here. It
# prints NOTHING, and always exits 0.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input="$(cat)"
exec > /dev/null 2>&1

# shellcheck source=../lib/harness.sh
. "$HERE/../lib/harness.sh"
# shellcheck source=common.sh
. "$HERE/common.sh"

harness="$(harness_detect "$input")"
dir=""
if py="$(hook_python)"; then
  target="$(printf '%s' "$input" | "$py" "$HERE/hook_input.py" session)" || target=""
  case "$target" in dir$'\t'*) dir="${target#dir$'\t'}" ;; esac
fi
[[ -n "$dir" ]] || dir="$(hook_dir_fallback "$harness")"
launch_clone_sync "$harness" --repo-of "$dir"
exit 0
