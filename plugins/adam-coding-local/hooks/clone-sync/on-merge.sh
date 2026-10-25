#!/usr/bin/env bash
# on-merge.sh - PostToolUse hook: after a successful pull-request merge, sync
# this machine's clean clones of the merged repo (ADR 0017).
#
# Runs for every harness (Claude Code, Codex, anything else): a fast-forward
# of a clean clone is harmless wherever it starts. It reads stdin once, asks
# hook_input.py whether the call was a successful `gh pr merge` or a
# *merge_pull_request tool call, and starts clone-sync.sh detached. It prints
# NOTHING, and always exits 0.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input="$(cat)"
exec > /dev/null 2>&1

# This hook fires on EVERY shell call. Leave before starting python (100 ms or
# more per call in Git Bash) unless the input could describe a merge at all.
case "$input" in
  *merge*) ;;
  *) exit 0 ;;
esac

# shellcheck source=../lib/harness.sh
. "$HERE/../lib/harness.sh"
# shellcheck source=common.sh
. "$HERE/common.sh"

harness="$(harness_detect "$input")"
py="$(hook_python)" || exit 0
target="$(printf '%s' "$input" | "$py" "$HERE/hook_input.py" merge)" || exit 0
case "$target" in
  repo$'\t'*) launch_clone_sync "$harness" --repo "${target#repo$'\t'}" ;;
  dir$'\t'*) launch_clone_sync "$harness" --repo-of "${target#dir$'\t'}" ;;
esac
exit 0
